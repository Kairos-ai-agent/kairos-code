"""The general lane's read-only toolset — real tools, real files, real fence.

``kairos/skeleton`` (the default lane for ordinary chat) had **no tools**, so a
file folded into the prompt as an ``[附件]`` block could not be opened — which is
why ``api/routes/weixin.py`` forced any message carrying media back onto the
Coder. These tests pin the fix (``kairos/skeleton/read_tools.py``):

* the lane really reads a real temp file, through the real tools;
* the toolset it exposes contains **no** write / execute / network capability,
  and the loop refuses such a tool if one is ever offered;
* the project-directory fence and the zip guard still apply;
* the WeChat media bypass is gone (media routes like text) — the lane-level
  proof lives here, the dispatch-level proof in ``test_weixin_lane_routing.py``.

Everything is offline: the model is a deterministic scripted client (no
network), and every file lives under ``tmp_path``.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import pytest

from kairos.approval import ApprovalMode
from kairos.capabilities import (
    Capability,
    requires_approval,
    resolve_capabilities,
    unregister_tool_capabilities,
)
from kairos.llm.base import LLMResponse, ToolCall
from kairos.skeleton.read_tools import (
    MAX_TOOL_TURNS,
    READ_ONLY_CAPABILITIES,
    is_read_only,
    read_only_tools,
    run_read_tool_loop,
    tool_names,
)
from kairos.tools.base import BaseTool, ToolResult

#: The exact read-only surface the general lane is allowed to expose.
EXPECTED_TOOLS = {"file_read", "doc_read", "xlsx_read", "data_analyze",
                  "grep", "find"}

#: Names and/or capabilities that must NEVER appear on the general lane.
FORBIDDEN_NAMES = {
    "file_write", "file_edit_replace", "multi_edit", "checkpoint",
    "terminal", "git", "spawn_subagent", "python_run",
    "webfetch", "websearch", "browser", "computer_use",
}


class _ScriptClient:
    """A deterministic, offline tool-capable client.

    ``complete`` replays a list of ``LLMResponse``s and records each request so
    a test can inspect the messages (tool results are fed back as ``tool``
    messages) and the advertised tool schemas.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list = []
        self.tool_outputs: list = []

    async def complete(self, messages, tools=None):
        self.requests.append({"messages": list(messages), "tools": tools})
        for m in messages:
            if getattr(m, "role", "") == "tool":
                self.tool_outputs.append(m.content)
        if not self._responses:
            # No script left: answer with what the tools returned, so a test can
            # assert the file body actually reached the model.
            return LLMResponse(
                content="ANSWER:\n" + "\n".join(self.tool_outputs),
                model="fake",
            )
        return self._responses.pop(0)


def _tool_call(name: str, args) -> LLMResponse:
    return LLMResponse(content="", model="fake",
                       tool_calls=[ToolCall(id="c1", name=name, arguments=args)])


@pytest.fixture
def ws(tmp_path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# (1) The toolset is read-only by construction
# ---------------------------------------------------------------------------

def test_general_lane_toolset_is_exactly_the_readers(ws):
    tools = read_only_tools(ws)
    assert set(tool_names(tools)) == EXPECTED_TOOLS
    assert len(tools) == len(EXPECTED_TOOLS)  # nothing hidden behind a dup name


def test_no_tool_on_the_general_lane_has_a_write_exec_or_network_capability(ws):
    """The whole point of the lane: readers only."""
    forbidden = {
        Capability.WRITE_FILE, Capability.EXEC_PROCESS,
        Capability.NETWORK, Capability.EXTERNAL_WRITE,
    }
    for tool in read_only_tools(ws):
        assert tool.name not in FORBIDDEN_NAMES, f"{tool.name} must not be here"
        caps = resolve_capabilities(tool.name, obj=tool)
        assert caps is not None, f"{tool.name} declares no capability set"
        assert caps, f"{tool.name} declares an empty capability set"
        assert caps <= READ_ONLY_CAPABILITIES, (
            f"{tool.name} declares {sorted(c.value for c in caps)} — not "
            f"read-only")
        assert not (caps & forbidden), f"{tool.name} carries {caps & forbidden}"
        assert is_read_only(tool) is True


def test_every_visible_schema_is_a_read_only_tool(ws):
    """What the model is *told* it may call is the same read-only set."""
    client = _ScriptClient([LLMResponse(content="done", model="fake")])
    import asyncio
    asyncio.run(run_read_tool_loop(
        client=client, tools=read_only_tools(ws),
        system_prompt="s", message="m"))
    schemas = client.requests[0]["tools"]
    assert {s["name"] for s in schemas} == EXPECTED_TOOLS
    assert not ({s["name"] for s in schemas} & FORBIDDEN_NAMES)


def test_read_only_capability_never_asks_for_approval():
    """A reader must not pop an approval prompt on the conversational lane."""
    assert requires_approval(frozenset({Capability.READ_FILE}),
                             ApprovalMode.SUGGEST)[0] is False
    assert requires_approval(frozenset({Capability.READ_FILE}),
                             ApprovalMode.EDIT)[0] is False


# ---------------------------------------------------------------------------
# (2) The lane really reads a real file, through the real tool
# ---------------------------------------------------------------------------

def test_the_general_lane_reads_a_real_file_through_the_real_tool(ws):
    secret = "KAIROS-GENERAL-LANE-READS-THIS-文件内容"
    (ws / "note.md").write_text(f"# 标题\n\n{secret}\n", encoding="utf-8")

    client = _ScriptClient([
        _tool_call("file_read", {"path": "note.md"}),  # 1st: ask to read it
        # (script exhausted) 2nd: answer from the tool output
    ])
    text = asyncio_run(run_read_tool_loop(
        client=client, tools=read_only_tools(ws),
        system_prompt="open the attachment", message="note.md 里写了什么？"))

    # the model was handed the real bytes...
    assert secret in "\n".join(client.tool_outputs)
    # ...and answered from them
    assert secret in text
    assert len(client.requests) == 2


def test_the_lane_stops_at_the_turn_budget(ws):
    (ws / "a.txt").write_text("x", encoding="utf-8")
    # A client that never stops asking for the same tool.
    responses = [_tool_call("file_read", {"path": "a.txt"})
                 for _ in range(MAX_TOOL_TURNS + 3)]
    client = _ScriptClient(responses)
    asyncio_run(run_read_tool_loop(
        client=client, tools=read_only_tools(ws),
        system_prompt="s", message="m"))
    assert len(client.requests) == MAX_TOOL_TURNS


# ---------------------------------------------------------------------------
# (3) The fence still applies — a path outside the project is refused
# ---------------------------------------------------------------------------

def test_a_path_outside_the_project_is_refused_by_the_fence(ws, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("TOP-SECRET-OUTSIDE", encoding="utf-8")

    client = _ScriptClient([
        _tool_call("file_read", {"path": "../outside.txt"}),
    ])
    text = asyncio_run(run_read_tool_loop(
        client=client, tools=read_only_tools(ws),
        system_prompt="s", message="read ../outside.txt"))

    joined = "\n".join(client.tool_outputs)
    assert "TOP-SECRET-OUTSIDE" not in joined, "the fence leaked an outside file"
    assert "outside" in joined.lower()          # the tool said why, readably
    assert "TOP-SECRET-OUTSIDE" not in text


def test_the_loop_refuses_a_non_read_only_tool_before_it_runs(ws):
    """Belt and braces: even if a writer were offered, the loop refuses it."""
    class _Writer(BaseTool):
        name = "probe_lane_writer"
        capabilities = frozenset({Capability.WRITE_FILE})
        ran = 0

        async def execute(self, **kwargs) -> ToolResult:  # pragma: no cover
            type(self).ran += 1
            return ToolResult(success=True, output="wrote it")

    tools = read_only_tools(ws) + [_Writer(allowed_root=ws)]
    client = _ScriptClient([
        _tool_call("probe_lane_writer", {"path": "new.txt"}),
    ])
    try:
        text = asyncio_run(run_read_tool_loop(
            client=client, tools=tools, system_prompt="s", message="write a file"))
        # not advertised to the model
        assert "probe_lane_writer" not in {
            s["name"] for s in client.requests[0]["tools"]}
        # never executed, and refused in-band
        assert _Writer.ran == 0
        assert "Refused" in text or "Refused" in "\n".join(client.tool_outputs)
        assert not (ws / "new.txt").exists()   # nothing was written
    finally:
        unregister_tool_capabilities("probe_lane_writer")


def test_an_unknown_tool_call_is_refused_without_crashing(ws):
    client = _ScriptClient([_tool_call("delete_everything", {"path": "x"})])
    text = asyncio_run(run_read_tool_loop(
        client=client, tools=read_only_tools(ws),
        system_prompt="s", message="m"))
    assert "Unknown tool" in "\n".join(client.tool_outputs)
    assert "delete_everything" in text or "Unknown" in text


# ---------------------------------------------------------------------------
# (4) The zip guard still applies to the lane's document readers
# ---------------------------------------------------------------------------

def _write_docx(path: Path, paragraphs) -> Path:
    body = "".join(
        "<w:p>" + "".join(f"<w:r><w:t>{xml_escape(p)}</w:t></w:r>" for p in [para])
        + "</w:p>"
        for para in paragraphs
    )
    parts = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types/>',
        "word/document.xml":
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxml'
            'formats.org/wordprocessingml/2006/main"><w:body>'
            f'{body}</w:body></w:document>',
    }
    with zipfile.ZipFile(path, "w") as z:
        for name, content in parts.items():
            z.writestr(name, content)
    return path


def test_doc_read_through_the_lane_still_hits_the_zip_guard(ws, monkeypatch):
    """The lane's doc_read opens Office files through ``guard_zip`` (P0-5)."""
    calls = {"n": 0}
    import kairos.tools.doc_read as doc_mod
    real = doc_mod.guard_zip

    def spy(zf, path):
        calls["n"] += 1
        return real(zf, path)

    monkeypatch.setattr(doc_mod, "guard_zip", spy)
    _write_docx(ws / "spec.docx", ["hello from the document"])

    client = _ScriptClient([_tool_call("doc_read", {"path": "spec.docx"})])
    text = asyncio_run(run_read_tool_loop(
        client=client, tools=read_only_tools(ws),
        system_prompt="s", message="summarise spec.docx"))

    assert calls["n"] == 1, "doc_read skipped the zip-bomb guard on the lane"
    assert "hello from the document" in "\n".join(client.tool_outputs)
    assert "hello from the document" in text


# ---------------------------------------------------------------------------
# (5) The service wires the loop onto the chat lane
# ---------------------------------------------------------------------------

def test_run_chat_reply_uses_the_read_only_loop_when_a_client_is_given(ws):
    from kairos.skeleton.service import run_chat_reply

    secret = "REPLY-FROM-A-REAL-FILE-42"
    (ws / "data.csv").write_text(f"col\n{secret}\n", encoding="utf-8")
    client = _ScriptClient([
        _tool_call("file_read", {"path": "data.csv"}),
    ])

    reply = asyncio_run(run_chat_reply(
        kind="repo", root=ws, message="data.csv 里有什么？", tool_client=client))

    assert secret in reply
    # the reader was advertised, and nothing else
    assert {s["name"] for s in client.requests[0]["tools"]} == EXPECTED_TOOLS


def test_default_tool_client_is_none_without_credentials():
    """No configured provider -> the caller keeps the prompt-only fallback."""
    from kairos.skeleton.service import default_tool_client

    assert default_tool_client() is None


def test_the_lane_read_still_writes_a_redacted_capability_audit(
        ws, tmp_path, monkeypatch):
    """The capability gate audits the lane's reads, with secrets redacted."""
    audit = tmp_path / "cap-audit"
    monkeypatch.setenv("KAIROS_CAPABILITY_AUDIT_DIR", str(audit))
    secret = "sk-" + "A" * 24            # credential-shaped path segment
    (ws / f"{secret}.txt").write_text("hi", encoding="utf-8")

    client = _ScriptClient([_tool_call("file_read", {"path": f"{secret}.txt"})])
    asyncio_run(run_read_tool_loop(
        client=client, tools=read_only_tools(ws),
        system_prompt="s", message="m"))

    lines = (audit / "capabilities.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert lines, "the capability gate did not audit the lane's read"
    blob = "\n".join(lines)
    assert secret not in blob, "a credential-shaped path leaked into the audit"
    assert "sk-<redacted>" in blob
    assert any('"tool": "file_read"' in ln and '"result": "allow"' in ln
               for ln in lines)


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)
