"""A conversation must never see another conversation's words.

The reported bug: "回答问题会串到不同的聊天界面" -- a reply answering one
project's question showed up under a *different* project. This file pins the
**backend** half of that guarantee, through the real entrance (a ``TestClient``
on the real app -> ``POST /api/projects/{id}/chat`` ->
``kairos.skeleton.service.run_chat_reply``, the general lane every ordinary web
and IM chat takes):

* **cross-project isolation (the core case)** -- project A is fed a unique
  sentinel in its chat history *and* in a workspace file it reads (which warms
  the process-wide tool-result cache); project B then asks an unrelated
  question and B's **complete** captured prompt (system + every message, every
  model call) must contain no trace of A's sentinel;
* **per-project history** -- each project replays only its own thread;
* **second line of defence** -- a row whose *own metadata* names another
  project is dropped even though the ``project_id`` column matched it (the
  column is derived from the message's metadata, so this is the independent
  check that catches a mis-persisting writer);
* **the session key is the project** -- the ``messages`` table has no
  ``session_id`` and the web ``/chat`` call sends none; a project *is* a
  conversation, so project scoping is session scoping (a source guard keeps a
  future "add a session column" change from silently de-scoping the read);
* **clean cache every round** -- the general lane, the Coder's own agent loop
  (``coder.chat`` + subagents) and the Coder<->Reviewer loop each start a round
  with an empty tool cache, so one project's ``file_read`` can never answer
  another's;
* **no global memory in the chat prompt** -- the lane reads no global knowledge
  base / project note, so nothing global can bleed into a project turn;
* **best-effort** -- empty / broken history never turns a reply into an error;
* **round trip preserved** -- the same project's next turn still sees the prior
  turn (the regression guard for the isolation fix).

Offline throughout: a scripted client stands in for the model and a real
(throwaway) SQLite ``messages`` table stands in for the DB.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.deps as api_deps
import kairos.skeleton.service as svc
from api.app import app
from api.routes import projects as projects_routes
from kairos.core.message_bus import Message, MessageBus
from kairos.core.persistence import Persistence
from kairos.llm.base import LLMResponse, ToolCall
from kairos.tools.cache import get_cache, set_cache

REPO = Path(__file__).resolve().parents[1]

#: A string no other test, project or fixture would ever produce.
SENTINEL = "ZZ-SENTINEL-PROJECT-A-9F3C1D-UNIQUE"


class _Client:
    """Scripted, offline, tool-capable model stand-in.

    ``script`` is a list of ``(content, tool_calls)`` turns replayed in order;
    once exhausted it answers with ``answer``. Every request is recorded so a
    test can scan the *entire* prompt the model received.
    """

    def __init__(self, script=None, answer="（回答）"):
        self._script = list(script or [])
        self.answer = answer
        self.requests: list = []

    async def complete(self, messages, tools=None):
        self.requests.append(list(messages))
        if self._script:
            content, calls = self._script.pop(0)
            return LLMResponse(content=content, model="fake", tool_calls=calls)
        return LLMResponse(content=self.answer, model="fake")

    @property
    def blob(self) -> str:
        """Every message of every request, concatenated (system included)."""
        parts = []
        for req in self.requests:
            for m in req:
                parts.append(str(getattr(m, "content", "") or ""))
                for tc in (getattr(m, "tool_calls", None) or []):
                    parts.append(str(tc))
        return "\n".join(parts)


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
            else "agent.chat", content=content, msg_type="text",
            metadata={"project_id": project_id}, timestamp=ts))


@pytest.fixture
def two_lanes(tmp_path, monkeypatch):
    """Two projects (distinct workspace roots) on the real app + real DB."""
    ws_a = tmp_path / "wsA"
    ws_b = tmp_path / "wsB"
    ws_a.mkdir()
    ws_b.mkdir()
    db = Persistence(tmp_path / "kairos.db")
    fake = _FakeOrch(db, [_FakeProject("projA", ws_a),
                          _FakeProject("projB", ws_b)])
    monkeypatch.setattr(projects_routes, "_orch", lambda: fake)
    monkeypatch.setattr(api_deps, "orchestrator", fake)
    monkeypatch.setattr(svc, "default_generator", lambda: None)
    client = TestClient(app)  # no lifespan: no MCP, no real data dir
    set_cache(None)           # start from a clean process-wide cache
    yield type("Lanes", (), {"client": client, "db": db, "fake": fake,
                             "wsA": ws_a, "wsB": ws_b})
    set_cache(None)


# ---------------------------------------------------------------------------
# (1) The core case: A's sentinel must not reach B's prompt
# ---------------------------------------------------------------------------

def test_a_projects_sentinel_never_reaches_another_projects_prompt(
        two_lanes, monkeypatch):
    # A: the sentinel lives in history AND in a file A will read.
    two_lanes.wsA.joinpath("sentinel.txt").write_text(
        SENTINEL + " is project A's secret.\n", encoding="utf-8")
    _seed(two_lanes.db, "projA", [
        ("user", "记住这句话：" + SENTINEL, 1_700_000_000.0),
        ("agent", "好的，我记得：" + SENTINEL, 1_700_000_001.0),
    ])
    # B: an unrelated prior turn of its own.
    _seed(two_lanes.db, "projB", [("user", "B 的历史：天气很好", 1_700_000_010.0)])

    # (a) Turn A, forcing a read of the sentinel file -> the process-wide
    #     tool-result cache now holds A's bytes.
    warm = _Client(script=[("", [ToolCall(id="c1", name="file_read",
                                          arguments='{"path": "sentinel.txt"}')])],
                   answer="A done")
    monkeypatch.setattr(svc, "default_tool_client", lambda: warm)
    ra = two_lanes.client.post("/api/projects/projA/chat",
                               json={"message": "读一下 sentinel.txt"})
    assert ra.status_code == 200, ra.text
    assert SENTINEL in warm.blob, "A's own turn did not read the sentinel file"
    assert SENTINEL in repr(get_cache()._data), \
        "the warm step did not populate the tool cache"

    # (b) Turn B: an unrelated question. Capture EVERY message it received.
    cap = _Client(answer="B answer")
    monkeypatch.setattr(svc, "default_tool_client", lambda: cap)
    rb = two_lanes.client.post("/api/projects/projB/chat",
                               json={"message": "今天天气怎么样"})
    assert rb.status_code == 200, rb.text
    assert cap.requests, "B's model client was never called"
    assert SENTINEL not in cap.blob, (
        "project A's sentinel leaked into project B's prompt:\n" + cap.blob)
    assert "B 的历史：天气很好" in cap.blob, "B lost its own history"
    assert SENTINEL not in repr(get_cache()._data), \
        "A's cached read survived into B's turn (stale process-wide cache)"


# ---------------------------------------------------------------------------
# (2) Per-project history: each project replays only its own thread
# ---------------------------------------------------------------------------

def test_each_project_replays_only_its_own_thread(two_lanes):
    _seed(two_lanes.db, "projA", [("user", "A-only-line", 1.0)])
    _seed(two_lanes.db, "projB", [("user", "B-only-line", 2.0)])
    a = svc.load_chat_history("projA", db=two_lanes.db)
    b = svc.load_chat_history("projB", db=two_lanes.db)
    assert [h["content"] for h in a] == ["A-only-line"]
    assert [h["content"] for h in b] == ["B-only-line"]


# ---------------------------------------------------------------------------
# (3) Second line of defence: a row owned by another project is dropped
# ---------------------------------------------------------------------------

def test_a_row_whose_metadata_names_another_project_is_dropped(two_lanes,
                                                              caplog):
    """The SQL scopes by column, but the column is derived from metadata -- a
    message that still names another project must not be replayed here."""
    class _LeakyLoader:
        def load_messages(self, **kwargs):
            # Column says projB, the payload's own metadata says projA.
            return [{"sender": "projA.skeleton", "topic": "agent.chat",
                     "content": "FOREIGN-BODY " + SENTINEL,
                     "metadata": '{"project_id": "projA"}'}]

    with caplog.at_level("WARNING"):
        assert svc.load_chat_history("projB", db=_LeakyLoader()) == []
    assert any("names another project" in r.message for r in caplog.records), \
        "the foreign row was dropped silently -- keep it findable"


def test_a_row_with_no_metadata_is_still_kept(two_lanes):
    """Older writers set no metadata project_id; those rows are not foreign."""
    class _BareLoader:
        def load_messages(self, **kwargs):
            return [{"sender": "user", "topic": "user.chat",
                     "content": "legit old row", "metadata": "{}"}]

    hist = svc.load_chat_history("projB", db=_BareLoader())
    assert [h["content"] for h in hist] == ["legit old row"]


def test_a_row_with_corrupt_metadata_is_not_treated_as_foreign(two_lanes):
    class _BadMetaLoader:
        def load_messages(self, **kwargs):
            return [{"sender": "user", "topic": "user.chat",
                     "content": "row with broken json", "metadata": "{not json"}]

    hist = svc.load_chat_history("projB", db=_BadMetaLoader())
    assert [h["content"] for h in hist] == ["row with broken json"]


# ---------------------------------------------------------------------------
# (4) The session key is the project (the messages table has no session_id)
# ---------------------------------------------------------------------------

def test_the_chat_entrance_sends_no_session_id():
    """A conversation *is* a project, so the read is scoped by project.

    If a future change adds a per-message session/thread id, this guard should
    be updated together with ``load_chat_history`` -- not silently left
    de-scoped.
    """
    src = (REPO / "web" / "src" / "pages" / "Chat.tsx").read_text(
        encoding="utf-8")
    call = src.split("`/projects/${currentProject.id}/chat`", 1)[1][:400]
    assert "session_id" not in call and "sessionId" not in call, (
        "the web chat call now carries a session id -- load_chat_history must "
        "be scoped by it too")


def test_history_read_is_project_scoped_and_foreign_checked():
    src = (REPO / "kairos" / "skeleton" / "service.py").read_text(
        encoding="utf-8")
    assert "project_id=project_id, chat_only=True" in src, \
        "the history read lost its project scope"
    assert "_row_is_foreign" in src, \
        "the independent foreign-row check is gone"


# ---------------------------------------------------------------------------
# (5) A clean cache starts every round (no cross-turn/项目 stale reads)
# ---------------------------------------------------------------------------

def test_the_general_lane_starts_each_turn_with_a_clean_cache(tmp_path):
    from kairos.skeleton.general_tools import run_general_tool_loop
    toxic_key = ("file_read", frozenset({("path", "from-another-project")}))
    set_cache(None)
    get_cache().set(toxic_key, "STALE-FROM-ANOTHER-PROJECT")
    client = _Client(answer="ok")
    asyncio.run(run_general_tool_loop(
        client=client, tools=[], system_prompt="s", message="m"))
    assert get_cache().get(toxic_key) is None, \
        "the general lane did not clear the process-wide tool cache"
    set_cache(None)


def test_every_chat_lane_clears_the_cache_on_its_source_line():
    """Source guards: the three round entry points each call ``clear_round``.

    (The loop_runner clears per Coder<->Reviewer round; ``general_tools`` is the
    chat lane; ``read_tools`` the read-only lane; ``kairos.agents.base`` is
    ``coder.chat`` and every subagent child. Missing any one is how a read in
    project A answers project B.)
    """
    checks = [
        # rel path, module, callable to inspect (None = whole-file check)
        ("kairos/skeleton/general_tools.py", "kairos.skeleton.general_tools",
         "run_general_tool_loop"),
        ("kairos/skeleton/read_tools.py", "kairos.skeleton.read_tools",
         "run_read_tool_loop"),
        ("kairos/loop/loop_runner.py", None, None),
        ("kairos/agents/base.py", "kairos.agents.base",
         "KairosAgent._run_impl"),
    ]
    for rel, module, dotted in checks:
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "clear_round" in src, f"{rel}: no tool-cache clear on this round"
        if module is None:
            continue
        import importlib
        obj = importlib.import_module(module)
        for part in dotted.split("."):
            obj = getattr(obj, part)
        assert "clear_round" in inspect.getsource(obj), \
            f"{rel}:{dotted} does not clear the tool cache"


# ---------------------------------------------------------------------------
# (6) No global memory / KB is folded into a project's chat prompt
# ---------------------------------------------------------------------------

def test_the_chat_lane_reads_no_global_knowledge_or_notes():
    chat_sources = [
        "kairos/skeleton/service.py",
        "kairos/skeleton/general_tools.py",
        "kairos/skeleton/read_tools.py",
        "kairos/task_router.py",
    ]
    joined = "\n".join((REPO / p).read_text(encoding="utf-8")
                       for p in chat_sources)
    for forbidden in ("global_kb", "search_global_insights",
                      "list_project_notes", "find_skills_for",
                      "list_working_fixes", "add_global_insight"):
        assert forbidden not in joined, \
            f"the chat lane now injects global memory ({forbidden})"
    # ``load_messages`` IS used, but only project-scoped, never globally.
    src = (REPO / "kairos/skeleton/service.py").read_text(encoding="utf-8")
    assert "project_id=project_id, chat_only=True" in src


# ---------------------------------------------------------------------------
# (7) Best-effort: empty / broken history never breaks a reply
# ---------------------------------------------------------------------------

def test_empty_and_broken_history_are_best_effort(two_lanes, monkeypatch):
    class _Boom:
        def load_messages(self, **kwargs):
            raise RuntimeError("table vanished")

    assert svc.load_chat_history("projB", db=_Boom()) == []
    assert svc.load_chat_history("", db=two_lanes.db) == []
    assert svc.load_chat_history("projB", db=object()) == []


# ---------------------------------------------------------------------------
# (8) Regression: the same project's next turn still sees the prior turn
# ---------------------------------------------------------------------------

def test_the_same_project_round_trip_still_sees_its_history(two_lanes,
                                                            monkeypatch):
    _seed(two_lanes.db, "projB", [
        ("user", "我叫小红，记住这个名字", 1.0),
        ("agent", "好的，小红。", 2.0),
    ])
    cap = _Client(answer="ok")
    monkeypatch.setattr(svc, "default_tool_client", lambda: cap)
    r = two_lanes.client.post("/api/projects/projB/chat",
                              json={"message": "我还叫什么名字？"})
    assert r.status_code == 200, r.text
    assert "我叫小红" in cap.blob and "好的，小红" in cap.blob, (
        "the isolation fix broke the same-project round trip")
