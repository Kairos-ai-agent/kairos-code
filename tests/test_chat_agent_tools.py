"""The general chat lane as a real agent: tools, artifacts, download.

Pins the change that turned ordinary web/IM chat into an agent which can READ,
WRITE / EDIT, RUN a command and FETCH a page (``kairos/skeleton/general_tools``)
-- and that reports the files a turn actually produced as **artifacts** the
frontend can hand to the user (``POST /{id}/chat`` -> ``artifacts``;
``GET /{id}/artifacts/download``).

The cases mirror the gap this closes:

* the chat lane's toolset now contains write / edit / terminal / webfetch
  (it used to be read-only) -- a *source/construction* guard so it cannot be
  quietly reverted;
* an ``EXTERNAL_WRITE`` tool is still refused in-band (browser / computer_use /
  subagents stay off this lane);
* a turn that writes a ``.md`` reports it (path / name / size / mime) and the
  file really landed on disk; a turn that writes nothing reports ``[]``;
* the published reply carries the same array on ``metadata.artifacts``;
* the artifact scan honours the ignore list and finds nested files;
* the download endpoint serves the bytes, marks text/markdown inline, and
  refuses traversal, absolute paths and a symlink escape.

Everything is offline: a deterministic scripted client stands in for the model
and never opens a socket; every file lives under ``tmp_path``.
"""
from __future__ import annotations

import asyncio
import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api.deps as api_deps
import kairos.skeleton.service as svc
from api.app import app
from api.routes import projects as projects_routes
from kairos.capabilities import (
    Capability,
    resolve_capabilities,
    unregister_tool_capabilities,
)
from kairos.core.message_bus import Message, MessageBus
from kairos.core.persistence import Persistence
from kairos.file_snapshot import snapshot_tree_files
from kairos.llm.base import LLMConfig, LLMMessage, LLMResponse, ToolCall
from kairos.llm.providers.openai_provider import OpenAIProvider
from kairos.skeleton.general_tools import (
    GENERAL_CHAT_EMPTY_RECOVERIES,
    GENERAL_CHAT_EMPTY_ANSWER_NUDGE,
    GENERAL_CHAT_MAX_TURNS,
    GENERAL_CHAT_PROMISE_RECOVERIES,
    GENERAL_CHAT_PROMISE_NUDGE,
    GENERAL_TOOL_CAPABILITIES,
    collect_artifacts,
    general_tools,
    is_allowed,
    run_general_tool_loop,
    tool_names,
)
from kairos.tools.base import BaseTool, ToolResult

REPO = Path(__file__).resolve().parents[1]

#: The readers the lane always had, the writers/exec/net it gained.
READERS = {"file_read", "doc_read", "xlsx_read", "data_analyze", "grep", "find"}
WRITERS = {"file_write", "file_edit_replace", "multi_edit"}
EXEC = {"terminal"}
NET = {"webfetch"}
#: Capabilities/tools that must NEVER appear on the general chat lane.
FORBIDDEN_TOOLS = {"browser", "computer_use", "spawn_subagent", "git",
                   "python_run"}


def _run(coro):
    return asyncio.run(coro)


class _WriteClient:
    """A deterministic, offline tool-capable client.

    ``calls`` is a script of ``(tool_name, args)`` tuples (or ``None`` for a
    plain answer) replayed in order; once exhausted it answers with ``answer``.
    Records every request so a test can inspect the advertised schemas.
    """

    def __init__(self, *calls, answer: str = "已完成"):
        self._calls = list(calls)
        self.answer = answer
        self.requests: list = []
        self._served = 0

    async def complete(self, messages, tools=None):
        self.requests.append({"messages": list(messages), "tools": tools})
        if self._served < len(self._calls):
            call = self._calls[self._served]
            self._served += 1
            if call is None:
                return LLMResponse(content=self.answer, model="fake")
            name, args = call
            return LLMResponse(content="", model="fake",
                               tool_calls=[ToolCall(id="c1", name=name,
                                                    arguments=args)])
        return LLMResponse(content=self.answer, model="fake")


def _write(path: str, content: str):
    return ("file_write", {"path": path, "content": content})


# ---------------------------------------------------------------------------
# App-level harness (a real TestClient, a throwaway SQLite DB, a fake bus)
# ---------------------------------------------------------------------------


class _FakeProject:
    def __init__(self, pid: str, root: Path):
        self.id = pid
        self.work_dir = str(root)
        self.workspace = root
        self.loop_task = None
        self.loop_session = None
        self.coder = None
        self.metadata = {}


class _FakeOrch:
    def __init__(self, db, projects):
        self._db = db
        self.message_bus = MessageBus()
        self._projects = {p.id: p for p in projects}

    def get_project(self, pid):
        return self._projects.get(pid)


@pytest.fixture
def lane(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    db = Persistence(tmp_path / "kairos.db")
    fake = _FakeOrch(db, [_FakeProject("p1", ws)])
    monkeypatch.setattr(projects_routes, "_orch", lambda: fake)
    monkeypatch.setattr(api_deps, "orchestrator", fake)
    # Force the general lane onto the tool client; never the prompt-only seam.
    monkeypatch.setattr(svc, "default_generator", lambda: None)
    client = TestClient(app)          # no lifespan: no MCP, no real data dir
    return type("Lane", (), {"client": client, "db": db, "fake": fake,
                             "ws": ws})


# ---------------------------------------------------------------------------
# (1) The toolset is a real agent's, and nothing past it
# ---------------------------------------------------------------------------

def test_general_lane_toolset_has_read_write_run_and_fetch(tmp_path):
    names = set(tool_names(general_tools(tmp_path)))
    assert READERS <= names, "a reader went missing"
    assert WRITERS <= names, "the lane cannot write -- it is not an agent"
    assert EXEC <= names, "the lane cannot run a command"
    assert NET <= names, "the lane cannot fetch a page"
    assert not (names & FORBIDDEN_TOOLS), names & FORBIDDEN_TOOLS


def test_every_general_tool_is_inside_the_lane_allowlist(tmp_path):
    for tool in general_tools(tmp_path):
        caps = resolve_capabilities(tool.name, obj=tool)
        assert caps is not None and caps, f"{tool.name} declares no capability"
        assert caps <= GENERAL_TOOL_CAPABILITIES, (tool.name, caps)
        assert Capability.EXTERNAL_WRITE not in caps
        assert is_allowed(tool) is True


def test_the_loop_refuses_an_external_write_tool_before_it_runs(tmp_path):
    """Belt and braces: even offered a browser-shaped tool, the loop refuses."""
    class _ExternalWriter(BaseTool):
        name = "probe_external_writer"
        capabilities = frozenset({Capability.EXTERNAL_WRITE})
        ran = 0

        async def execute(self, **kwargs) -> ToolResult:  # pragma: no cover
            type(self).ran += 1
            return ToolResult(success=True, output="did it")

    tools = general_tools(tmp_path) + [_ExternalWriter(allowed_root=tmp_path)]
    client = _WriteClient(("probe_external_writer", {}), answer="ok")
    try:
        text = _run(run_general_tool_loop(
            client=client, tools=tools, system_prompt="s", message="m"))
        assert "probe_external_writer" not in {
            s["name"] for s in client.requests[0]["tools"]}   # not advertised
        assert _ExternalWriter.ran == 0                        # never executed
        assert "Refused" in text or text == "ok"
        assert any("Refused" in str(r["messages"]) or "Refused" in
                   str(m) for r in client.requests for m in r["messages"]) \
            or "Refused" in text
    finally:
        unregister_tool_capabilities("probe_external_writer")


def test_the_turn_budget_is_raised_but_bounded():
    assert 6 < GENERAL_CHAT_MAX_TURNS <= 30


# ---------------------------------------------------------------------------
# (2) A turn that writes a file reports it as an artifact (RED before the fix)
# ---------------------------------------------------------------------------

def test_a_chat_turn_that_writes_a_file_reports_it_as_an_artifact(
        lane, monkeypatch):
    body = "# 结论\n\n这是本轮产出。\n"
    monkeypatch.setattr(svc, "default_tool_client",
                        lambda: _WriteClient(_write("result.md", body)))

    r = lane.client.post("/api/projects/p1/chat",
                         json={"message": "整理一下材料，产出结论"})
    assert r.status_code == 200, r.text
    arts = r.json()["artifacts"]
    assert isinstance(arts, list) and len(arts) == 1, arts
    entry = arts[0]
    # the frozen four-field shape
    assert set(entry) == {"path", "name", "size", "mime"}
    assert entry["path"] == "result.md"
    assert entry["name"] == "result.md"
    # size is the real on-disk byte count (text write may expand \n -> \r\n)
    assert entry["size"] == (lane.ws / "result.md").stat().st_size
    assert entry["size"] > 0
    assert entry["mime"] == "text/markdown"
    # ...and the file really landed in the workspace
    assert (lane.ws / "result.md").read_text(encoding="utf-8") == body


def test_a_chat_turn_that_writes_nothing_reports_an_empty_list(
        lane, monkeypatch):
    monkeypatch.setattr(svc, "default_tool_client",
                        lambda: _WriteClient(answer="你好呀"))
    r = lane.client.post("/api/projects/p1/chat", json={"message": "聊聊天吧"})
    assert r.status_code == 200, r.text
    assert r.json()["artifacts"] == []


def test_the_reply_message_carries_the_artifacts_in_metadata(lane, monkeypatch):
    """Refresh safety: the reply bubble itself keeps the produced files."""
    monkeypatch.setattr(svc, "default_tool_client",
                        lambda: _WriteClient(_write("note.md", "hi\n")))
    lane.client.post("/api/projects/p1/chat", json={"message": "整理一下"})

    msgs = _run(lane.fake.message_bus.recent(50))
    replies = [m for m in msgs if getattr(m, "topic", "") == "agent.chat"]
    assert replies, "the general lane published no agent.chat reply"
    meta = getattr(replies[-1], "metadata", {}) or {}
    assert [a["path"] for a in meta.get("artifacts", [])] == ["note.md"]


def test_run_chat_reply_fills_the_optional_artifacts_out(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    arts: list = []
    out = _run(svc.run_chat_reply(
        kind="repo", root=ws, message="整理一下", tool_client=_WriteClient(
            _write("direct.md", "x")), artifacts_out=arts))
    assert out == "已完成"
    assert [a["path"] for a in arts] == ["direct.md"]
    assert (ws / "direct.md").exists()


def test_run_chat_reply_without_artifacts_out_still_returns_text(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    out = _run(svc.run_chat_reply(
        kind="repo", root=ws, message="整理一下",
        tool_client=_WriteClient(_write("x.md", "y"))))
    assert out == "已完成"
    assert (ws / "x.md").exists()


# ---------------------------------------------------------------------------
# (3) The artifact scan: ignore list honoured, nested files found
# ---------------------------------------------------------------------------

def test_artifacts_honour_the_ignore_list_and_find_nested_files(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    before = snapshot_tree_files([root])

    (root / "sub").mkdir()
    (root / "sub" / "deep.txt").write_text("kept", encoding="utf-8")
    for ignored in ("node_modules", ".venv", "__pycache__", "attachments",
                    "runs"):
        (root / ignored).mkdir()
        (root / ignored / "noise.txt").write_text("x", encoding="utf-8")
    (root / "dropped.tmp").write_text("x", encoding="utf-8")

    after = snapshot_tree_files([root])
    paths = {a["path"] for a in collect_artifacts(root, before, after)}
    assert paths == {"sub/deep.txt"}, paths


# ---------------------------------------------------------------------------
# (4) Download endpoint: bytes out, escapes refused
# ---------------------------------------------------------------------------

_DOWNLOAD = "/api/projects/p1/artifacts/download"


def _write_file(lane, rel: str, data: bytes = b"hello\n") -> Path:
    p = lane.ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_download_serves_the_file_bytes_and_previews_text(lane):
    _write_file(lane, "out/report.md", b"# title\nbody\n")
    r = lane.client.get(_DOWNLOAD, params={"path": "out/report.md"})
    assert r.status_code == 200, r.text
    assert r.content == b"# title\nbody\n"
    disposition = r.headers.get("content-disposition", "")
    assert "inline" in disposition and "report.md" in disposition
    assert r.headers.get("content-type", "").startswith("text/markdown")


def test_download_rejects_parent_traversal(lane, tmp_path):
    (tmp_path / "outside.txt").write_text("TOP-SECRET", encoding="utf-8")
    r = lane.client.get(_DOWNLOAD, params={"path": "../outside.txt"})
    assert r.status_code == 400, r.text
    assert b"TOP-SECRET" not in r.content


def test_download_rejects_an_absolute_path(lane, tmp_path):
    outside = tmp_path / "abs.txt"
    outside.write_text("TOP-SECRET", encoding="utf-8")
    r = lane.client.get(_DOWNLOAD, params={"path": str(outside)})
    assert r.status_code == 400, r.text
    assert b"TOP-SECRET" not in r.content


def test_download_rejects_a_symlink_escape(lane, tmp_path):
    """A symlink inside the project that points outside must not be served."""
    outside = tmp_path / "link-secret.txt"
    outside.write_text("TOP-SECRET", encoding="utf-8")
    link = lane.ws / "escape.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this host cannot create a symlink: {exc}")
    r = lane.client.get(_DOWNLOAD, params={"path": "escape.txt"})
    assert r.status_code == 400, r.text
    assert b"TOP-SECRET" not in r.content


def test_download_404s_for_a_missing_file(lane):
    r = lane.client.get(_DOWNLOAD, params={"path": "nope.txt"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# (5) Source / contract guards
# ---------------------------------------------------------------------------

def test_chat_lane_wires_the_general_toolset_not_the_readers():
    src = (REPO / "kairos" / "skeleton" / "service.py").read_text(
        encoding="utf-8")
    assert "run_general_tool_loop" in src, "the chat lane lost its tool loop"
    assert "general_tools(" in src, "the chat lane stopped building the agent tools"
    assert "read_only_tools(" not in src, (
        "the chat lane fell back to the read-only toolset -- the exact "
        "regression this guard exists to catch")
    gt = (REPO / "kairos" / "skeleton" / "general_tools.py").read_text(
        encoding="utf-8")
    for cls in ("FileEditTool", "FileEditReplaceTool", "MultiEditTool",
                "TerminalTool", "WebFetchTool"):
        assert cls in gt, f"{cls} is no longer wired onto the chat lane"


def test_run_chat_reply_keeps_its_contract_and_adds_optional_artifacts():
    params = inspect.signature(svc.run_chat_reply).parameters
    for old in ("kind", "root", "message", "generate", "max_context_chars",
                "tool_client"):
        assert old in params, f"run_chat_reply lost the {old!r} parameter"
    for new in ("project_id", "history", "artifacts_out"):
        assert new in params, f"run_chat_reply lost the {new!r} parameter"
        assert params[new].default is None, f"{new} must stay optional"


# ---------------------------------------------------------------------------
# (6) The answer channel: reasoning is NEVER the reply (the defect this fixes)
# ---------------------------------------------------------------------------
#
# The user's report, verbatim: they asked the general lane for an md document
# and got back the model's *thinking* ("Installed. Now, 'AI培训' PPT — what
# content? ... Should I ask for clarification? ... Let me build a Python
# script ...") with an empty ``artifacts`` array and no file on disk. Root
# cause: the provider promoted the hidden-reasoning channel into ``content``
# whenever the content channel came back empty, and the lane's loop returned
# that ``content`` verbatim. These tests pin the fix at both layers.

#: A stand-in for what a reasoning model writes on its hidden channel.
MONOLOGUE = (
    "Installed. Now, 'AI培训' PPT — what content? The user wants a general "
    "analysis of the current large-model landscape as an md document. Should I "
    "ask for clarification? Let me build a Python script generating the pptx "
    "... 名字越来越清晰了。再精确定位几个关键信息。"
)


class _FakeMessage:
    """The OpenAI SDK message shape the provider reads."""

    def __init__(self, content="", reasoning_content=None, tool_calls=None,
                 reasoning=None):
        self.content = content
        self.reasoning_content = reasoning_content
        self.reasoning = reasoning
        self.tool_calls = tool_calls


class _FakeProviderCompletions:
    def __init__(self, message):
        self._message = message

    async def create(self, **kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=self._message,
                                     finish_reason="stop")],
            model="stub", usage=None)


class _FakeProviderClient:
    def __init__(self, message):
        self.chat = SimpleNamespace(
            completions=_FakeProviderCompletions(message))


def _real_provider(message) -> OpenAIProvider:
    """A REAL OpenAIProvider with only its SDK client faked out."""
    provider = OpenAIProvider(LLMConfig(
        provider="openai", model="deepseek-flash",
        api_key="sk-test-not-a-real-key"))
    provider._client = _FakeProviderClient(message)
    return provider


def test_reasoning_monologue_is_never_promoted_into_the_answer():
    """content="" + a reasoning monologue => content stays "" (not the monologue)."""
    provider = _real_provider(_FakeMessage(content="", reasoning_content=MONOLOGUE))

    resp = _run(provider.complete(
        [LLMMessage(role="user", content="分析目前的大模型情况，给我md文档")]))

    assert resp.content == "", (
        "an empty content channel must stay empty — the reasoning is not the reply")
    assert MONOLOGUE not in resp.content
    # ...but the event is decidable: the marker and the tail are kept.
    assert resp.reply_from_reasoning is True
    assert resp.reasoning_chars == len(MONOLOGUE)
    assert resp.reasoning_tail  # the thinking line still has a source


def test_content_is_the_answer_even_when_reasoning_is_present():
    """content non-empty + reasoning non-empty => the answer is the content."""
    answer = "当前大模型格局：第一梯队……"
    provider = _real_provider(_FakeMessage(content=answer,
                                           reasoning_content=MONOLOGUE))

    resp = _run(provider.complete([LLMMessage(role="user", content="q")]))

    assert resp.content == answer
    assert MONOLOGUE not in resp.content
    assert resp.reply_from_reasoning is False


def test_whitespace_only_content_does_not_become_the_monologue():
    provider = _real_provider(_FakeMessage(content="   \n ",
                                           reasoning_content=MONOLOGUE))
    resp = _run(provider.complete([LLMMessage(role="user", content="q")]))
    assert resp.content.strip() == ""
    assert resp.reply_from_reasoning is True


def test_the_chat_lane_never_hands_back_the_reasoning_monologue(tmp_path):
    """End-to-end: the reported request must not return the thinking as the reply."""
    ws = tmp_path / "ws"
    ws.mkdir()
    provider = _real_provider(_FakeMessage(content="", reasoning_content=MONOLOGUE))
    arts: list = []

    reply = _run(svc.run_chat_reply(
        kind="repo", root=ws, message="分析目前的大模型情况，给我md文档",
        tool_client=provider, artifacts_out=arts))

    assert MONOLOGUE not in (reply or ""), (
        "the lane handed the model's private monologue back as the answer")
    assert (reply or "").strip() == ""
    assert arts == []


def test_streamed_reasoning_only_is_not_emitted_as_the_reply():
    """The streaming path has the same rule: reasoning is not a text delta."""
    import json as _json

    class _StreamDelta:
        def __init__(self, content=None, reasoning=None, finish=None):
            self.content = content
            self.reasoning_content = reasoning
            self.reasoning = None
            self.tool_calls = None
            self.finish_reason = finish

    class _StreamCompletions:
        async def create(self, **kwargs):
            async def _gen():
                yield SimpleNamespace(choices=[SimpleNamespace(
                    delta=_StreamDelta(reasoning="思考"), finish_reason=None)],
                    usage=None)
                yield SimpleNamespace(choices=[SimpleNamespace(
                    delta=_StreamDelta(reasoning=MONOLOGUE),
                    finish_reason="stop")], usage=None)
            return _gen()

    class _StreamClient:
        chat = SimpleNamespace(completions=_StreamCompletions())

    provider = OpenAIProvider(LLMConfig(
        provider="openai", model="deepseek-flash", api_key="sk-test"))
    provider._client = _StreamClient()

    async def collect():
        return [c async for c in provider.stream([LLMMessage(role="user", content="q")])]

    raw = _run(collect())
    text = "".join(c for c in raw
                   if not (isinstance(c, str) and c.startswith("{")))
    assert MONOLOGUE not in text, "streamed reasoning leaked into the reply"
    assert text == ""
    meta = [_json.loads(c) for c in raw
            if isinstance(c, str) and c.startswith("{")
            and _json.loads(c).get("type") == "stream_meta"]
    assert meta and meta[-1]["reply_from_reasoning"] is True


# ---------------------------------------------------------------------------
# (7) The general lane actually produces the file (nudge rounds)
# ---------------------------------------------------------------------------


class _ScriptedClient:
    """A model client played back from a script of responses."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests: list = []

    async def complete(self, messages, tools=None):
        self.requests.append({"messages": list(messages), "tools": tools})
        if len(self.requests) <= len(self._responses):
            return self._responses[len(self.requests) - 1]
        return LLMResponse(content="（脚本已耗尽）", model="fake")


def test_a_reasoning_only_turn_recovers_and_writes_the_file(tmp_path):
    """empty content + reasoning flag => the lane asks again and the file lands."""
    ws = tmp_path / "ws"
    ws.mkdir()
    body = "# 大模型现状\n\n正文……\n"
    client = _ScriptedClient(
        LLMResponse(content="", model="deepseek-flash",
                    reply_from_reasoning=True, reasoning_chars=len(MONOLOGUE),
                    reasoning_tail=MONOLOGUE[-40:]),
        LLMResponse(content="", model="deepseek-flash",
                    tool_calls=[ToolCall(id="c1", name="file_write",
                                         arguments={"path": "report.md",
                                                    "content": body})]),
        LLMResponse(content="已写入 report.md", model="deepseek-flash"),
    )
    arts: list = []

    reply = _run(svc.run_chat_reply(
        kind="repo", root=ws, message="分析大模型，产出 md 文档",
        tool_client=client, artifacts_out=arts))

    assert (ws / "report.md").read_text(encoding="utf-8") == body
    assert [a["path"] for a in arts] == ["report.md"]
    assert reply == "已写入 report.md"
    # The recovery is real: the second request carried the corrective nudge.
    assert any(GENERAL_CHAT_EMPTY_ANSWER_NUDGE in (m.content or "")
               for m in client.requests[1]["messages"])


def test_a_promise_only_reply_is_held_to_the_work(tmp_path):
    """"我会为你生成…" with no tool call => nudged once, then the file lands."""
    ws = tmp_path / "ws"
    ws.mkdir()
    client = _ScriptedClient(
        LLMResponse(content="好的，我会为你生成一份 md 文档。", model="deepseek-flash"),
        LLMResponse(content="", model="deepseek-flash",
                    tool_calls=[ToolCall(id="c1", name="file_write",
                                         arguments={"path": "out.md",
                                                    "content": "正文\n"})]),
        LLMResponse(content="已写入 out.md", model="deepseek-flash"),
    )
    arts: list = []

    reply = _run(svc.run_chat_reply(
        kind="repo", root=ws, message="给我写一份md文档",
        tool_client=client, artifacts_out=arts))

    assert (ws / "out.md").exists()
    assert [a["path"] for a in arts] == ["out.md"]
    assert reply == "已写入 out.md"
    assert any(GENERAL_CHAT_PROMISE_NUDGE in (m.content or "")
               for m in client.requests[1]["messages"])


def test_the_empty_reply_recovery_is_bounded(tmp_path):
    """A model that never answers cannot spin: exactly one recovery, then stop."""
    class _Stuck:
        def __init__(self):
            self.n = 0

        async def complete(self, messages, tools=None):
            self.n += 1
            return LLMResponse(content="", model="deepseek-flash",
                               reply_from_reasoning=True)

    stuck = _Stuck()
    text = _run(run_general_tool_loop(
        client=stuck, tools=general_tools(tmp_path),
        system_prompt="s", message="给我文件"))

    assert text == ""
    assert stuck.n == 1 + GENERAL_CHAT_EMPTY_RECOVERIES


def test_chitchat_produces_no_file_and_no_artifact(tmp_path):
    """The original intent survives: a plain greeting writes nothing, no nudge."""
    ws = tmp_path / "ws"
    ws.mkdir()
    client = _ScriptedClient(LLMResponse(content="你好呀，有什么可以帮你的吗？",
                                         model="deepseek-flash"))
    arts: list = []

    reply = _run(svc.run_chat_reply(
        kind="repo", root=ws, message="随便聊聊天",
        tool_client=client, artifacts_out=arts))

    assert reply == "你好呀，有什么可以帮你的吗？"
    assert arts == []
    assert list(ws.rglob("*")) == []
    assert len(client.requests) == 1, "a plain greeting must not spend a nudge"


# ---------------------------------------------------------------------------
# (8) Prompt + source guards (the fix must not be quietly reverted)
# ---------------------------------------------------------------------------


def test_tool_chat_prompt_requires_writing_the_deliverable():
    """The prompt must say: a file/document request => write it and hand it back."""
    prompt = svc._TOOL_CHAT_PROMPT.lower()
    required = ("file_write", "workspace", "document", "artifact", "plan",
                "greeting", "write nothing")
    for kw in required:
        assert kw in prompt, f"the deliverable rule is missing {kw!r}"
    assert "{context}" in svc._TOOL_CHAT_PROMPT, "the context slot was lost"
    # Chitchat intent kept: a greeting / artifact-free question writes nothing.
    assert "needs no file" in prompt


def test_provider_never_promotes_reasoning_into_the_answer():
    src = (REPO / "kairos" / "llm" / "providers" / "openai_provider.py").read_text(
        encoding="utf-8")
    assert "content_text = reasoning_text" not in src, (
        "the empty-content => reasoning-as-answer substitution is back")
    assert "yield reasoning_accum" not in src, (
        "the streamed reasoning is being surfaced as the reply again")
    assert "NOT using the reasoning channel as the reply" in src
    assert "reply_from_reasoning = True" in src


def test_general_loop_keeps_a_named_empty_reply_recovery():
    src = (REPO / "kairos" / "skeleton" / "general_tools.py").read_text(
        encoding="utf-8")
    assert "GENERAL_CHAT_EMPTY_ANSWER_NUDGE" in src
    assert "GENERAL_CHAT_PROMISE_NUDGE" in src
    assert GENERAL_CHAT_EMPTY_RECOVERIES >= 1
    assert GENERAL_CHAT_PROMISE_RECOVERIES >= 1
