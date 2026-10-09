"""The ordinary chat lane must SEE the conversation that came before it.

The reported bug: a user asks something, gets an answer, then sends a follow-up
("重新分析…") -- and the agent answers like a blank agent that never heard the
first turn. Root cause: ``kairos.skeleton.service.run_chat_reply`` (the general
lane every ordinary ``/chat`` takes) took **only the single new message**; no
history was read and none reached either model seam. The frontend re-hydrates
the thread from the ``messages`` table, so the *user* sees the conversation --
the model did not.

This file pins the fix, and is written so it fails on the old code:

* **round trip** -- two prior turns in the DB appear in the *next* turn's
  captured prompt (red before the fix: nothing was injected);
* **caps** -- only the newest ``CHAT_HISTORY_MAX_MESSAGES`` turns, and only
  what fits ``CHAT_HISTORY_MAX_CHARS``, are replayed;
* **robust** -- no history / one oversized turn never breaks the reply;
* **isolation** -- another project's history can never leak into this one;
* **source guard** -- the chat lane still references the history source, so a
  future "cleanup" that deletes the line is caught here;
* **unchanged when empty** -- with no history the prompt is byte-for-byte what
  it was before history existed, on *both* seams;
* **contract** -- the IM bridges call ``run_chat_reply`` compatibly.

Everything is offline: a deterministic capturing client stands in for the
model, a real (throwaway) SQLite ``messages`` table stands in for the DB.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.deps as api_deps
import kairos.skeleton.service as svc
from api.app import app
from api.routes import projects as projects_routes
from kairos.core.message_bus import Message, MessageBus
from kairos.core.persistence import Persistence
from kairos.llm.base import LLMResponse

REPO = Path(__file__).resolve().parents[1]

#: The exact prompt the fallback seam built BEFORE history existed. Pinned
#: literally (not via the module constant) so a template edit is caught.
_EXPECTED_EMPTY_CHAT_PROMPT = (
    "You are answering the user's message conversationally, inside their "
    "project workspace.\n\n"
    "Use the workspace context below when it is relevant to the message. If "
    "it is not relevant, just answer the question directly and briefly.\n\n"
    "WORKSPACE CONTEXT:\n{context}\n\n"
    "USER MESSAGE:\n{message}\n\n"
    "Write your reply now."
)


class _CapturingClient:
    """Deterministic model stand-in that records every prompt it is handed."""

    def __init__(self, answer: str = "（回答）"):
        self.answer = answer
        self.requests: list = []

    async def complete(self, messages, tools=None):
        self.requests.append(list(messages))
        return LLMResponse(content=self.answer, model="fake")

    @property
    def last_system(self) -> str:
        for m in reversed(self.requests[-1]):
            if getattr(m, "role", "") == "system":
                return getattr(m, "content", "") or ""
        return ""

    @property
    def last_blob(self) -> str:
        return "\n".join(getattr(m, "content", "") or ""
                         for m in self.requests[-1])


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


def _seed(db: Persistence, project_id: str, rows) -> None:
    """rows: iterable of (role, content, timestamp). role in {user, agent}."""
    for role, content, ts in rows:
        db.save_message(Message(
            sender="user" if role == "user" else f"{project_id}.skeleton",
            receiver=f"{project_id}.coder", topic="user.chat" if role == "user"
            else "agent.chat",
            content=content, msg_type="text",
            metadata={"project_id": project_id}, timestamp=ts))


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """A TestClient on the real app whose general lane reads a real DB."""
    ws = tmp_path / "ws"
    ws.mkdir()
    db = Persistence(tmp_path / "kairos.db")
    fake = _FakeOrch(db, [_FakeProject("p1", ws), _FakeProject("p2", ws)])
    monkeypatch.setattr(projects_routes, "_orch", lambda: fake)
    monkeypatch.setattr(api_deps, "orchestrator", fake)

    cap = _CapturingClient()
    # The lane resolves both seams at call time; pin them offline.
    monkeypatch.setattr(svc, "default_tool_client", lambda: cap)
    monkeypatch.setattr(svc, "default_generator", lambda: None)

    client = TestClient(app)  # no lifespan: no MCP, no real data dir
    return type("Lane", (), {"client": client, "db": db, "cap": cap,
                             "fake": fake, "ws": ws})


# ---------------------------------------------------------------------------
# (1) round trip -- the core case (RED on the old code)
# ---------------------------------------------------------------------------

def test_second_turn_sees_the_first_turn(lane):
    _seed(lane.db, "p1", [
        ("user", "我叫小明，记住这个名字", 1_700_000_000.0),
        ("agent", "好的，小明，我记住了。", 1_700_000_001.0),
    ])
    r = lane.client.post("/api/projects/p1/chat",
                         json={"message": "重新分析一下我的名字"})
    assert r.status_code == 200, r.text
    assert r.json()["route"] == "skeleton"

    blob = lane.cap.last_blob
    assert "我叫小明" in blob, "turn 1 (user) missing from the turn-2 prompt"
    assert "好的，小明" in blob, "turn 1 (agent) missing from the turn-2 prompt"
    assert "## Recent conversation" in lane.cap.last_system
    # ...and the current message is not replayed twice.
    assert blob.count("重新分析一下我的名字") == 1


def test_history_is_labelled_as_background_not_a_new_instruction(lane):
    _seed(lane.db, "p1", [("user", "先分析 A", 1.0), ("agent", "A 的结论", 2.0)])
    lane.client.post("/api/projects/p1/chat", json={"message": "再分析 B"})
    system = lane.cap.last_system
    assert "## Recent conversation" in system
    assert "NOT a new instruction" in system


# ---------------------------------------------------------------------------
# (2) caps -- count and characters, newest first
# ---------------------------------------------------------------------------

def test_only_the_newest_n_turns_are_replayed(lane):
    n = svc.CHAT_HISTORY_MAX_MESSAGES
    _seed(lane.db, "p1", [("user", f"msg-{i:02d}", 1000.0 + i)
                          for i in range(50)])
    lane.client.post("/api/projects/p1/chat", json={"message": "继续"})
    blob = lane.cap.last_blob

    newest = f"msg-{49:02d}"
    assert newest in blob, "the newest prior turn must be replayed"
    # only the newest ``n`` are kept -> the (50-n-1)-th is the oldest dropped
    dropped = f"msg-{50 - n - 1:02d}"
    assert dropped not in blob, "an older turn survived the count cap"
    injected = re.findall(r"msg-\d\d", blob)
    assert len(injected) == n, f"expected {n} turns, got {len(injected)}"


def test_the_character_budget_is_respected(lane):
    big = "长" * 3000
    _seed(lane.db, "p1", [
        ("user", big + "-oldest", 1.0),
        ("agent", big + "-middle", 2.0),
        ("user", big + "-newest", 3.0),
    ])
    hist = svc.load_chat_history("p1", db=lane.db)
    total = sum(len(h["content"]) for h in hist)
    assert total <= svc.CHAT_HISTORY_MAX_CHARS, total
    assert hist, "something must survive the budget"
    # oldest-first, and it is the recent tail that is kept
    assert hist[-1]["content"].endswith("-newest")


def test_a_single_oversized_message_is_clipped_not_dropped(lane):
    _seed(lane.db, "p1", [
        ("user", "超" * (svc.CHAT_HISTORY_MAX_CHARS + 500), 1.0)])
    hist = svc.load_chat_history("p1", db=lane.db)
    assert hist, "a lone oversized turn must still be replayed (clipped)"
    assert len(hist[0]["content"]) <= svc.CHAT_HISTORY_MAX_CHARS


def test_load_chat_history_is_oldest_first_and_project_scoped(lane):
    _seed(lane.db, "p1", [("user", "one", 1.0), ("agent", "two", 2.0)])
    _seed(lane.db, "p2", [("user", "other-project", 3.0)])
    hist = svc.load_chat_history("p1", db=lane.db)
    assert [h["role"] for h in hist] == ["user", "assistant"]
    assert [h["content"] for h in hist] == ["one", "two"]


# ---------------------------------------------------------------------------
# (3) robust -- emptiness and an oversized turn never break the reply
# ---------------------------------------------------------------------------

def test_no_history_still_answers(lane):
    r = lane.client.post("/api/projects/p1/chat", json={"message": "你好"})
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "（回答）"
    assert "## Recent conversation" not in lane.cap.last_system


def test_one_oversized_turn_does_not_break_the_reply(lane):
    _seed(lane.db, "p1", [("user", "超" * (svc.CHAT_HISTORY_MAX_CHARS + 500),
                           1.0)])
    r = lane.client.post("/api/projects/p1/chat", json={"message": "继续"})
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "（回答）"
    assert len(lane.cap.last_blob) < 80_000  # not the whole wall dumped in


def test_a_database_without_the_loader_degrades_to_no_history(monkeypatch, tmp_path):
    class _NoLoader:
        pass

    monkeypatch.setattr(svc, "_default_history_db", lambda: _NoLoader())
    assert svc.load_chat_history("p1") == []
    # an explicit empty project id is a no-op too
    assert svc.load_chat_history("") == []


def test_a_raising_loader_is_logged_and_swallowed(lane, caplog):
    class _Boom:
        def load_messages(self, **kwargs):
            raise RuntimeError("table vanished")

    with caplog.at_level("WARNING"):
        assert svc.load_chat_history("p1", db=_Boom()) == []
    assert any("chat history read failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# (4) isolation -- another project's history must never leak
# ---------------------------------------------------------------------------

def test_another_projects_history_never_leaks_in(lane):
    secret = "P2-ONLY-SECRET-DO-NOT-LEAK"
    _seed(lane.db, "p1", [("user", "p1 的旧消息", 1.0)])
    _seed(lane.db, "p2", [("user", secret, 2.0), ("agent", secret + "!", 3.0)])

    lane.cap.requests.clear()
    r = lane.client.post("/api/projects/p1/chat", json={"message": "继续"})
    assert r.status_code == 200, r.text
    assert "p1 的旧消息" in lane.cap.last_blob
    assert secret not in lane.cap.last_blob, "a foreign project's chat leaked in"


# ---------------------------------------------------------------------------
# (5) source guard -- the chat lane is wired to the history source
# ---------------------------------------------------------------------------

def test_chat_lane_reads_and_injects_history_source():
    src = (REPO / "kairos" / "skeleton" / "service.py").read_text(
        encoding="utf-8")
    assert "def load_chat_history" in src
    assert "load_messages" in src, "the lane no longer reads the messages table"
    assert "chat_only=True" in src, "history read must be chat-scoped"
    assert "## Recent conversation" in src, "the history block marker is gone"
    # both seams consume the rendered block
    assert src.count("history_block") >= 4


def test_the_web_entrance_passes_the_project_to_the_lane():
    src = (REPO / "api" / "routes" / "projects.py").read_text(encoding="utf-8")
    call = re.search(r"run_chat_reply\(\s*kind=decision\.workspace_kind.*?"
                     r"project_id=project_id", src, re.S)
    assert call, "the general-lane call moved; update this guard"
    assert "project_id=project_id" in call.group(0), (
        "the web chat entrance no longer hands the lane a project id, so it "
        "cannot read history")


def test_im_bridges_call_the_lane_compatibly():
    """The IM/WeCom/Weixin bridges may pass ``project_id`` but must keep the
    call contract: the historical kwargs are all still present and every new
    one is optional."""
    params = inspect.signature(svc.run_chat_reply).parameters
    for old in ("kind", "root", "message", "generate", "max_context_chars",
                "tool_client"):
        assert old in params, f"run_chat_reply lost the {old!r} parameter"
    for new in ("project_id", "history"):
        assert new in params
        assert params[new].default is None, f"{new} must stay optional"

    for path in ("api/routes/im.py", "api/routes/wecom.py",
                 "api/routes/weixin.py"):
        src = (REPO / path).read_text(encoding="utf-8")
        call = re.search(r"run_chat_reply\(\s*kind=decision\.workspace_kind"
                         r".*?project_id=project\.id\)", src, re.S)
        assert call, (
            f"{path}: the general-lane call is no longer threading project_id "
            f"(or moved)")
        text = call.group(0)
        assert "kind=" in text and "root=" in text and "message=" in text


# ---------------------------------------------------------------------------
# (6) unchanged when empty -- byte-for-byte the pre-history prompt
# ---------------------------------------------------------------------------

def test_empty_history_prompt_is_byte_identical_to_before(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "default_tool_client", lambda: None)  # force fallback
    ws = tmp_path / "ws"
    ws.mkdir()
    prompts: list = []

    async def run():
        return await svc.run_chat_reply(
            kind="repo", root=ws, message="你好",
            generate=lambda prompt: prompts.append(prompt) or "x")

    import asyncio
    asyncio.run(run())

    context = svc.build_workspace("repo", ws).as_prompt_context(
        max_chars=None, query="你好")
    expected = _EXPECTED_EMPTY_CHAT_PROMPT.format(context=context,
                                                  message="你好")
    assert prompts and prompts[0] == expected, \
        "the no-history fallback prompt changed"
    assert "## Recent conversation" not in prompts[0]


def test_no_history_tool_seam_prompt_changed_only_by_the_block(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    import asyncio

    base = _CapturingClient()
    asyncio.run(svc.run_chat_reply(kind="repo", root=ws, message="你好",
                                   tool_client=base))
    with_hist = _CapturingClient()
    asyncio.run(svc.run_chat_reply(
        kind="repo", root=ws, message="你好", tool_client=with_hist,
        history=[{"role": "user", "content": "之前说过的话"}]))
    # empty history -> the block is absent and the body is untouched
    assert "## Recent conversation" not in base.last_system
    assert base.last_system.startswith(
        "You are answering the user's message conversationally")
    # with history -> the block is prepended, the original body is intact
    assert with_hist.last_system.endswith(base.last_system)
    assert with_hist.last_system.startswith("## Recent conversation")
    assert "之前说过的话" in with_hist.last_system
