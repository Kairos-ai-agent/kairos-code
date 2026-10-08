"""The product entry: ``POST /api/projects/{id}/start`` now routes.

Two things must both be true, and the whole point of this file is to hold them
at once:

* a request that declares (or clearly *is*) a non-code task runs on the
  domain-neutral skeleton, and its structured ``Verdict`` is written to a run
  record and readable back;
* **an ordinary code request takes the Coder <-> Reviewer loop exactly as
  before** -- the skeleton is not on its path. This is the reverse proof: the
  test asserts the orchestrator's ``start_loop`` was called (and the skeleton
  was not), so a future refactor that quietly reroutes code work fails here.

The skeleton route is a **background task** (like the loop), so the request
returns immediately with a run id and ``status="running"``; the verdict is read
back from ``GET /{id}/skeleton`` once the run finishes. ``test_non_code_...``
proves the non-blocking property with a slow fake generator, and
``test_skeleton_run_can_be_stopped_...`` proves the Stop path reaches a terminal
state.

Everything uses a fake generator: no network, no real model.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.deps as _api_deps
from api.app import app
from api.routes import projects as _projects_routes
from kairos.core.orchestrator_parts.loopctl import OrchLoopControlMixin


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class FakeProject:
    def __init__(self, root: Path) -> None:
        self.id = "p1"
        self.work_dir = str(root)
        self.workspace = root
        self.loop_task = None
        self.loop_session = None
        self.metadata: dict = {}
        self.coder = None
        self.runtime = type("R", (), {"coder_mode": "default", "coder_policy": None})()


class FakeOrchestrator(OrchLoopControlMixin):
    """Enough of the Orchestrator: get_project, a bus, and a counted start_loop.

    It inherits the *real* ``stop_loop`` / ``_on_loop_done`` (via
    ``OrchLoopControlMixin``), so ``POST /{id}/stop`` exercised here runs the
    product's loop-stop code, not a stand-in: a cancelled loop task fires the
    same done-callback that publishes the terminal ``loop.ended``.
    """

    def __init__(self, project: FakeProject) -> None:
        from kairos.core.message_bus import MessageBus

        self.project = project
        self.message_bus = MessageBus()
        self.start_loop_calls: list = []
        self.stop_loop_calls: list = []
        self._projects = {project.id: project}
        self._db = _FakeDb()
        self._dispatch_tasks: set = set()

    def get_project(self, project_id: str):
        return self.project if project_id == self.project.id else None

    async def start_loop(self, project_id: str, requirement: str) -> str:
        self.start_loop_calls.append((project_id, requirement))
        return "sess-1"

    def stop_loop(self, project_id: str) -> bool:
        # Record the dispatch, then run the *real* stop_loop unchanged.
        self.stop_loop_calls.append(project_id)
        return OrchLoopControlMixin.stop_loop(self, project_id)


class _FakeDb:
    """The bits ``_on_loop_done`` touches, plus the chat message sink."""

    def __init__(self) -> None:
        self.saved: list = []

    def save_project(self, project) -> None:
        pass

    def save_message(self, msg) -> None:
        # The chat route persists the user's own message straight to the DB
        # (topic ``user.chat``); keep them so a test can assert it happened.
        self.saved.append(msg)

    def load_loop_rounds(self, project_id, limit: int = 20):
        return []


class FakeCoder:
    """A minimal Coder for the chat lane.

    ``chat()`` records the call, publishes ``agent.chat`` on the bus (exactly
    what the real Coder does in ``kairos/agents/agent_parts/chat.py``), and
    returns a marked reply -- so a test can prove the Coder lane was taken and
    that its body + event are unchanged.
    """

    def __init__(self, bus) -> None:
        self.bus = bus
        self.agent_id = "p1.coder"
        self.chat_calls: list = []

    async def chat(self, text, *, voice_mode: bool = False) -> str:
        self.chat_calls.append({"text": text, "voice_mode": voice_mode})
        reply = f"[coder] {text}"
        from kairos.core.message_bus import Message

        await self.bus.publish(Message(
            sender=self.agent_id, topic="agent.chat", content=reply,
            msg_type="text"))
        return reply


class _CancelledLoopTask:
    """Minimal stand-in for an asyncio loop task a Stop cancels.

    ``cancel()`` marks it done and fires the done-callback the way a real
    cancelled task does — mirroring ``start_loop``'s
    ``add_done_callback(lambda t: self._on_loop_done(project_id, t))``
    (``kairos/core/orchestrator.py:1002``) — so the loop reaches the same
    terminal state through the same code.
    """

    def __init__(self) -> None:
        self._callbacks: list = []
        self._done = False
        self._cancelled = False

    def add_done_callback(self, cb) -> None:
        self._callbacks.append(cb)

    def done(self) -> bool:
        return self._done

    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> bool:
        self._cancelled = True
        self._done = True
        for cb in self._callbacks:
            cb(self)
        return True

    def exception(self):
        return None


def _arm_running_loop(fake: FakeOrchestrator, project_id: str = "p1") -> _CancelledLoopTask:
    """Give the project a running loop the way ``start_loop`` would.

    A ``LoopSession`` plus an in-flight task whose done-callback is the real
    ``_on_loop_done`` — so cancelling it publishes ``loop.ended`` exactly as a
    real run does.
    """
    from kairos.loop.review_loop import LoopSession

    project = fake.project
    project.loop_session = LoopSession(
        project=project, message_bus=fake.message_bus,
        coder=object(), reviewer=object(),
    )
    task = _CancelledLoopTask()
    task.add_done_callback(lambda t: fake._on_loop_done(project_id, t))
    project.loop_task = task
    return task


def _fake_generate(prompt: str) -> str:
    """Deterministic model stand-in: cite every input, then self-check."""
    names = re.findall(r"INPUT: (\S+)", prompt or "")
    rows = [f"- 结论 {i + 1}，依据 [[{name}]]" for i, name in enumerate(names)]
    return "\n".join([
        "# 对比报告", "", "## 对比", *rows, "",
        "## 自检引用",
        f"SELF_CHECK: citations={len(names)}/{len(names)} ok",
    ])


def _fake_chat_generate(prompt: str) -> str:
    """A chat-style deterministic generator (no network, no model).

    Distinct from ``_fake_generate`` so a test can tell the general lane's own
    answer from a Coder reply.
    """
    return "（通用车道）这是一次对话式回答。"


def _slow_generate(progress: dict, delay: float):
    """An async generator that is still sleeping when the HTTP call returns."""
    async def _gen(prompt: str) -> str:
        progress["started"] = True
        await asyncio.sleep(delay)
        progress["finished"] = True
        return _fake_generate(prompt)
    return _gen


@pytest.fixture
def fake_gen(monkeypatch):
    """Inject the fake generator at the service seam (no model, no network)."""
    monkeypatch.setattr("kairos.skeleton.service.default_generator", lambda: _fake_generate)


@pytest.fixture
def docs_root(tmp_path) -> Path:
    root = tmp_path / "ws"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "alpha.md").write_text("方案甲：延迟 30ms", encoding="utf-8")
    (root / "docs" / "beta.md").write_text("方案乙：延迟 80ms", encoding="utf-8")
    (root / "docs" / "gamma.md").write_text("方案丙：延迟 15ms", encoding="utf-8")
    return root


@pytest.fixture
def code_root(tmp_path) -> Path:
    root = tmp_path / "code"
    (root / "pkg").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (root / "pkg" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    return root


@pytest.fixture
def make_client(fake_gen):
    """Build a TestClient whose project root is ``root``; restores globals."""
    real_deps = _api_deps.orchestrator
    real_routes = _projects_routes._orch
    clients: list = []

    def _build(root: Path):
        fake = FakeOrchestrator(FakeProject(root))
        _api_deps.orchestrator = fake
        _projects_routes._orch = lambda: fake
        c = TestClient(app)
        b = c.__enter__()
        clients.append((c, b, fake))
        return c, fake

    try:
        yield _build
    finally:
        for c, _b, _fake in clients:
            try:
                c.__exit__(None, None, None)
            except Exception:
                pass
        _api_deps.orchestrator = real_deps
        _projects_routes._orch = real_routes


# ---------------------------------------------------------------------------
# helpers: wait for the background run to reach its terminal state
# ---------------------------------------------------------------------------

def _wait_terminal(client: TestClient, project_id: str = "p1", timeout: float = 8.0) -> dict:
    """Poll ``GET /{id}/skeleton`` until the run leaves ``running``."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/projects/{project_id}/skeleton").json()
        if last.get("status") not in (None, "running"):
            return last
        time.sleep(0.02)
    raise AssertionError(f"skeleton run never reached a terminal state: {last}")


def _wait_topic(bus, topic: str, timeout: float = 8.0):
    """Wait for a topic to appear on the bus; return the last message on it."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        hits = [m for m in bus.get_history(limit=200) if m.topic == topic]
        if hits:
            return hits[-1]
        time.sleep(0.02)
    raise AssertionError(f"topic {topic!r} never appeared on the bus ({last})")


# ---------------------------------------------------------------------------
# (1) explicit non-code task -> skeleton, returns at once, verdict readable
# ---------------------------------------------------------------------------

def test_explicit_docs_task_routes_to_the_skeleton(make_client, docs_root):
    client, fake = make_client(docs_root)

    r = client.post("/api/projects/p1/start",
                    json={"requirement": "读三份文档，产出一页对比报告并自检引用。",
                          "kind": "docs"})
    assert r.status_code == 200, r.text
    body = r.json()

    # the response *says* which path it took, and hands back a run id at once
    assert body["route"] == "skeleton"
    assert body["workspace_kind"] == "docs"
    assert body["route_source"] == "explicit"
    assert body["status"] == "running"
    assert body["run_id"] and body["session_id"] == body["run_id"]

    # the loop was NOT used
    assert fake.start_loop_calls == []

    # ...and once the background run finishes, the structured Verdict is there
    state = _wait_terminal(client)
    assert state["status"] == "done"
    assert state["outcome"] == "passed"
    verdict = state["verdict"]
    assert verdict["passed"] is True
    assert verdict["verifier"] == "citations"
    assert isinstance(verdict["evidence"], list) and verdict["evidence"]
    assert all(isinstance(e, dict) and "satisfied" in e for e in verdict["evidence"])

    # the deliverables exist on disk
    assert (docs_root / "outputs" / "report.md").is_file()
    report = (docs_root / "outputs" / "report.md").read_text(encoding="utf-8")
    for name in ("alpha.md", "beta.md", "gamma.md"):
        assert f"[[{name}]]" in report


def test_routed_run_is_written_to_a_run_record(make_client, docs_root):
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "读文档写对比报告", "kind": "docs"})
    assert r.status_code == 200, r.text
    body = r.json()

    state = _wait_terminal(client)
    run_file = Path(state["run_file"])
    assert run_file.is_file(), state["run_file"]
    assert run_file.parent == docs_root / ".kairos" / "skeleton-runs"
    # the id handed back up front is the id the record is saved under
    assert run_file.name == f"skeleton-run-{body['run_id']}.json"

    record = json.loads(run_file.read_text(encoding="utf-8"))
    # the SAME structured verdict is in the persisted record
    assert record["outcome"] == "passed"
    assert record["verdict"] == state["verdict"]
    assert record["run_id"] == body["run_id"]


def test_routed_run_is_published_to_the_existing_bus(make_client, docs_root):
    """The existing observation path (project bus -> DB) sees the run."""
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "读文档写对比报告", "kind": "docs"})
    assert r.status_code == 200, r.text

    _wait_terminal(client)
    topics = {m.topic for m in fake.message_bus.get_history(limit=100)}
    assert "skeleton.run.verified" in topics
    assert "skeleton.run.completed" in topics


def test_workspace_kind_alias_also_routes(make_client, docs_root):
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "写报告", "workspace_kind": "docs"})
    assert r.status_code == 200, r.text
    assert r.json()["route"] == "skeleton"
    assert fake.start_loop_calls == []


def test_explicit_docs_beats_a_code_workspace(make_client, code_root):
    """The caller's explicit signal wins even over a code-looking root."""
    client, fake = make_client(code_root)
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "读文档写报告", "kind": "docs"})
    assert r.status_code == 200, r.text
    assert r.json()["route"] == "skeleton"
    assert fake.start_loop_calls == []


def test_heuristic_routes_a_docs_workspace_with_no_signal(make_client, docs_root):
    """No explicit kind, but the workspace is clearly a document set."""
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/p1/start", json={"requirement": "读文档写报告"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route"] == "skeleton"
    assert body["route_source"] == "heuristic"
    assert fake.start_loop_calls == []


# ---------------------------------------------------------------------------
# (2) THE REVERSE PROOF: an ordinary code task still goes to the loop
# ---------------------------------------------------------------------------

def test_ordinary_code_task_still_takes_the_loop(make_client, code_root):
    client, fake = make_client(code_root)

    r = client.post("/api/projects/p1/start",
                    json={"requirement": "fix the auth bug"})
    assert r.status_code == 200, r.text
    body = r.json()

    # the loop ran, with the requirement, and returned a session id...
    assert fake.start_loop_calls == [("p1", "fix the auth bug")]
    assert body["session_id"] == "sess-1"
    # ...and the skeleton did not touch this request
    assert body.get("route") != "skeleton"
    assert "verdict" not in body


def test_no_signal_on_an_undecidable_workspace_takes_the_general_lane(make_client, tmp_path):
    """An empty workspace is undecided -> the general lane (new default)."""
    empty = tmp_path / "empty"
    empty.mkdir()
    client, fake = make_client(empty)

    r = client.post("/api/projects/p1/start", json={"requirement": "do something"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route"] == "skeleton"
    assert body["route_source"] == "default"
    # the loop was NOT used for an undecided, non-coding task
    assert fake.start_loop_calls == []


def test_explicit_repo_kind_on_a_docs_workspace_takes_the_loop(make_client, docs_root):
    """An explicit code signal keeps the loop even for a docs-looking root."""
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "refactor the module", "kind": "repo"})
    assert r.status_code == 200, r.text
    assert r.json()["session_id"] == "sess-1"
    assert fake.start_loop_calls == [("p1", "refactor the module")]


def test_already_running_loop_is_still_a_409(make_client, code_root):
    client, fake = make_client(code_root)

    class _Done:
        def done(self):
            return False

    fake.project.loop_task = _Done()
    r = client.post("/api/projects/p1/start", json={"requirement": "x"})
    assert r.status_code == 409
    assert fake.start_loop_calls == []


# ---------------------------------------------------------------------------
# (3) THE NON-BLOCKING PROOF: the request returns before the worker finishes
# ---------------------------------------------------------------------------

def test_non_code_request_returns_before_the_worker_finishes(make_client, docs_root, monkeypatch):
    """A slow worker must not block the HTTP request.

    Proves three things at once: (a) the response came back while the worker
    was *still sleeping* -- so it cannot have awaited it; (b) it already carried
    a run id and ``running``; (c) the run then finishes and its Verdict becomes
    readable from the state endpoint and the record.
    """
    progress: dict = {"started": False, "finished": False}
    monkeypatch.setattr("kairos.skeleton.service.default_generator",
                        lambda: _slow_generate(progress, delay=0.5))
    client, fake = make_client(docs_root)

    t0 = time.time()
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "慢慢读文档写报告", "kind": "docs"})
    elapsed = time.time() - t0
    assert r.status_code == 200, r.text
    body = r.json()

    # (a) the worker had NOT finished when the request returned
    assert progress["finished"] is False
    assert elapsed < 0.5, f"request blocked for {elapsed:.3f}s (worker sleeps 0.5s)"
    # (b) the response is a running handle, not a result
    assert body["status"] == "running"
    assert body["route"] == "skeleton"
    assert body["run_id"]
    assert "verdict" not in body and "outcome" not in body

    # (c) the run completes on its own and the verdict is readable
    state = _wait_terminal(client)
    assert progress["finished"] is True
    assert state["status"] == "done"
    assert state["run_id"] == body["run_id"]
    assert state["verdict"]["passed"] is True
    assert state["verdict"]["verifier"] == "citations"
    assert Path(state["run_file"]).is_file()
    assert fake.start_loop_calls == []


# ---------------------------------------------------------------------------
# (4) THE STOP PATH: stopping reaches a terminal state (not stuck "running")
# ---------------------------------------------------------------------------

def test_skeleton_run_can_be_stopped_with_a_terminal_state(make_client, docs_root, monkeypatch):
    """Stop cancels the task and stamps a terminal state + ``skeleton.ended``."""
    progress: dict = {"started": False, "finished": False}
    monkeypatch.setattr("kairos.skeleton.service.default_generator",
                        lambda: _slow_generate(progress, delay=5.0))
    client, fake = make_client(docs_root)

    r = client.post("/api/projects/p1/start",
                    json={"requirement": "很慢的任务", "kind": "docs"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running"

    # it is really running (the worker got to its sleep)...
    deadline = time.time() + 2.0
    while not progress["started"] and time.time() < deadline:
        time.sleep(0.01)
    assert progress["started"] is True

    stop = client.post("/api/projects/p1/skeleton/stop")
    assert stop.status_code == 200, stop.text
    assert stop.json()["status"] == "stopping"

    state = _wait_terminal(client)
    # ...and after stopping it is NO LONGER running
    assert state["status"] == "stopped"
    assert state["running"] is False
    assert state["stop_requested"] is True
    # the slow worker never produced its deliverable
    assert progress["finished"] is False

    # the terminal event fired, so a UI watching the bus leaves 运行中
    ended = _wait_topic(fake.message_bus, "skeleton.ended")
    assert ended.metadata["status"] == "stopped"
    assert ended.metadata["project_id"] == "p1"


def test_stop_with_nothing_running_is_reported(make_client, docs_root):
    client, _fake = make_client(docs_root)
    r = client.post("/api/projects/p1/skeleton/stop")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "no_skeleton_running"


# ---------------------------------------------------------------------------
# (4b) THE MERGED STOP: the generic /stop dispatches to whoever is running
# ---------------------------------------------------------------------------

def test_generic_stop_with_only_the_loop_is_byte_identical(make_client, code_root):
    """Only the loop is live -> the generic /stop is today's route, word for word.

    It must call the same ``stop_loop`` with the same argument, return the exact
    same two-key body, and reach the same terminal ``loop.ended`` -- and it must
    not drag the skeleton path in.
    """
    client, fake = make_client(code_root)
    _arm_running_loop(fake)

    r = client.post("/api/projects/p1/stop")
    assert r.status_code == 200, r.text
    body = r.json()
    # today's exact body: same keys, same order, no extra "stopped" field
    assert body == {"status": "stopping", "project_id": "p1"}
    assert list(body.keys()) == ["status", "project_id"]

    # the loop path was the only one taken
    assert fake.stop_loop_calls == ["p1"]
    # the same terminal event the loop has always published
    ended = _wait_topic(fake.message_bus, "loop.ended")
    assert ended.metadata["status"] == "stopped"
    assert ended.metadata["project_id"] == "p1"
    # the skeleton side did nothing at all
    assert getattr(fake.project, "skeleton_state", None) is None
    topics = {m.topic for m in fake.message_bus.get_history(limit=100)}
    assert "skeleton.ended" not in topics


def test_generic_stop_stops_a_lone_skeleton_run(make_client, docs_root, monkeypatch):
    """Only the skeleton is live -> the generic /stop reaches its terminal state.

    (The old generic /stop could not touch the skeleton at all; this is the
    behaviour the merge adds.)
    """
    progress: dict = {"started": False, "finished": False}
    monkeypatch.setattr("kairos.skeleton.service.default_generator",
                        lambda: _slow_generate(progress, delay=5.0))
    client, fake = make_client(docs_root)

    start = client.post("/api/projects/p1/start",
                        json={"requirement": "很慢的任务", "kind": "docs"})
    assert start.status_code == 200, start.text
    assert start.json()["status"] == "running"

    deadline = time.time() + 2.0
    while not progress["started"] and time.time() < deadline:
        time.sleep(0.01)
    assert progress["started"] is True

    # the GENERIC stop, not the skeleton alias
    stop = client.post("/api/projects/p1/stop")
    assert stop.status_code == 200, stop.text
    assert stop.json()["status"] == "stopping"
    assert stop.json()["stopped"] == ["skeleton"]
    # the loop side was consulted but had nothing to stop
    assert fake.stop_loop_calls == ["p1"]

    state = _wait_terminal(client)
    assert state["status"] == "stopped"
    assert state["running"] is False
    assert progress["finished"] is False
    ended = _wait_topic(fake.message_bus, "skeleton.ended")
    assert ended.metadata["status"] == "stopped"
    assert ended.metadata["project_id"] == "p1"


def test_generic_stop_stops_both_when_both_run(make_client, docs_root, monkeypatch):
    """Both live -> both stop, and the body names both."""
    progress: dict = {"started": False, "finished": False}
    monkeypatch.setattr("kairos.skeleton.service.default_generator",
                        lambda: _slow_generate(progress, delay=5.0))
    client, fake = make_client(docs_root)

    start = client.post("/api/projects/p1/start",
                        json={"requirement": "很慢的任务", "kind": "docs"})
    assert start.json()["status"] == "running"
    _arm_running_loop(fake)
    deadline = time.time() + 2.0
    while not progress["started"] and time.time() < deadline:
        time.sleep(0.01)
    assert progress["started"] is True

    stop = client.post("/api/projects/p1/stop")
    assert stop.status_code == 200, stop.text
    assert stop.json()["status"] == "stopping"
    assert stop.json()["stopped"] == ["loop", "skeleton"]

    # skeleton terminal state + event
    state = _wait_terminal(client)
    assert state["status"] == "stopped" and state["running"] is False
    skel_ended = _wait_topic(fake.message_bus, "skeleton.ended")
    assert skel_ended.metadata["status"] == "stopped"
    # loop terminal state + event
    assert fake.project.status == "stopped"
    loop_ended = _wait_topic(fake.message_bus, "loop.ended")
    assert loop_ended.metadata["status"] == "stopped"


def test_generic_stop_with_nothing_running_matches_today(make_client, code_root):
    """Neither live -> the body is exactly today's (no invented error shape)."""
    client, fake = make_client(code_root)

    r = client.post("/api/projects/p1/stop")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"status": "no_loop_running", "project_id": "p1"}
    assert list(body.keys()) == ["status", "project_id"]
    # both sides were consulted
    assert fake.stop_loop_calls == ["p1"]


def test_skeleton_stop_alias_still_stops(make_client, docs_root, monkeypatch):
    """The alias ``POST /{id}/skeleton/stop`` is kept and still works."""
    progress: dict = {"started": False, "finished": False}
    monkeypatch.setattr("kairos.skeleton.service.default_generator",
                        lambda: _slow_generate(progress, delay=5.0))
    client, fake = make_client(docs_root)

    start = client.post("/api/projects/p1/start",
                        json={"requirement": "很慢的任务", "kind": "docs"})
    assert start.json()["status"] == "running"
    deadline = time.time() + 2.0
    while not progress["started"] and time.time() < deadline:
        time.sleep(0.01)

    stop = client.post("/api/projects/p1/skeleton/stop")
    assert stop.status_code == 200, stop.text
    # the alias keeps its own (unchanged) body shape
    assert stop.json() == {"status": "stopping", "project_id": "p1"}

    state = _wait_terminal(client)
    assert state["status"] == "stopped" and state["running"] is False
    ended = _wait_topic(fake.message_bus, "skeleton.ended")
    assert ended.metadata["status"] == "stopped"


# ---------------------------------------------------------------------------
# (5) honest failure when the route needs a model and there is none
# ---------------------------------------------------------------------------

def test_skeleton_route_without_a_model_is_a_503(make_client, docs_root, monkeypatch):
    monkeypatch.setattr("kairos.skeleton.service.default_generator", lambda: None)
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "写报告", "kind": "docs"})
    assert r.status_code == 503, r.text
    assert "skeleton route failed" in r.json()["detail"]
    assert fake.start_loop_calls == []


def test_unknown_project_is_still_a_404(make_client, docs_root):
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/nope/start", json={"requirement": "x", "kind": "docs"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# (6) the finished run survives a lost in-process state (restart / re-hydrate)
# ---------------------------------------------------------------------------

def test_finished_run_is_readable_after_live_state_is_lost(make_client, docs_root):
    """A run that finished read back as ``none`` once the process forgot it.

    The live state (``project.skeleton_state``) lives only in the process that
    started the run; a backend restart -- or a project re-hydrated from the DB --
    drops it, and the panel then said 未运行 even though the record was on disk.
    ``GET /skeleton`` must fall back to that record.
    """
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "读三份文档写对比报告", "kind": "docs"})
    assert r.status_code == 200, r.text
    state = _wait_terminal(client)
    assert state["status"] == "done" and state["passed"] is True
    run_id = state["run_id"]

    # Simulate the restart: the in-process state is gone.
    fake.project.skeleton_state = None

    again = client.get("/api/projects/p1/skeleton").json()
    assert again["status"] == "done", again
    assert again["running"] is False
    assert again["run_id"] == run_id
    assert again["session_id"] == run_id
    assert again["project_id"] == "p1"
    assert again["outcome"] == "passed"
    assert again["passed"] is True
    assert again["workspace_kind"] == "docs"
    assert again["verdict"]["verifier"] == "citations"
    assert again["verdict"]["evidence"]           # the per-criterion rows survive
    assert again["artifacts"] == ["outputs/report.md"]
    assert again["run_file"].endswith(f"skeleton-run-{run_id}.json")
    assert Path(again["run_file"]).is_file()


def test_live_state_is_preferred_over_the_record(make_client, docs_root):
    """While the run is here, the endpoint reports the live state, not the file."""
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "读文档写报告", "kind": "docs"})
    assert r.status_code == 200, r.text
    live_id = r.json()["run_id"]
    # A stale record from some earlier run, newer on disk than the live state.
    run_dir = docs_root / ".kairos" / "skeleton-runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    stale = run_dir / "skeleton-run-deadbeef.json"
    stale.write_text(json.dumps({
        "run_id": "deadbeef", "outcome": "failed", "attempts": 1,
        "task": {"instruction": "stale"}, "result": {}, "verdict": {},
        "history": [],
    }, ensure_ascii=False), encoding="utf-8")
    got = client.get("/api/projects/p1/skeleton").json()
    # the live run (whatever its word) is what the panel already sees
    assert got["run_id"] == live_id


def test_fallback_read_does_not_touch_the_run_directory(make_client, docs_root):
    """The fallback is strictly read-only: no file is created or rewritten."""
    client, fake = make_client(docs_root)
    client.post("/api/projects/p1/start",
                json={"requirement": "读文档写报告", "kind": "docs"})
    _wait_terminal(client)
    fake.project.skeleton_state = None

    run_dir = docs_root / ".kairos" / "skeleton-runs"

    def snapshot():
        return {str(p.relative_to(docs_root)): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in docs_root.rglob("*") if p.is_file()}

    before = snapshot()
    for _ in range(3):
        r = client.get("/api/projects/p1/skeleton")
        assert r.status_code == 200 and r.json()["status"] == "done"
    after = snapshot()
    # not one file created, deleted, or rewritten anywhere under the workspace
    assert after == before
    assert run_dir.is_dir()


def test_project_status_is_terminal_after_the_run(make_client, docs_root, monkeypatch):
    """The project row's "运行中" marker follows the run into its terminal state.

    The row renders 运行中 straight off ``project.status`` (which the loop sets);
    if the skeleton never clears it, the row stays on 运行中 while the panel says
    已停止/完成 -- one screen, two answers. It must not survive the run.
    """
    progress: dict = {"started": False, "finished": False}
    monkeypatch.setattr("kairos.skeleton.service.default_generator",
                        lambda: _slow_generate(progress, delay=0.4))
    client, fake = make_client(docs_root)

    r = client.post("/api/projects/p1/start",
                    json={"requirement": "慢任务", "kind": "docs"})
    assert r.status_code == 200, r.text
    # while live, the row agrees with the panel
    assert fake.project.status == "running"

    state = _wait_terminal(client)
    assert state["status"] == "done"
    # ...and the marker is gone the moment the run is over
    assert fake.project.status != "running"
    assert fake.project.status in ("done", "failed", "stopped")


def test_skeleton_llm_call_is_recorded_in_the_cost_ledger(monkeypatch):
    """A real-model skeleton call lands in the shared cost ledger (kairos.cost).

    The loop's Gate Report reads that ledger for a run's spend; the skeleton
    path never wrote to it, so the cost panel read 0 calls / $0 after a real
    run. The call is recorded with the provider's real token counts.
    """
    import kairos.cost as cost_mod
    from kairos.skeleton.service import _record_provider_call

    seen: list = []
    monkeypatch.setattr(cost_mod, "record_entry", lambda **kw: seen.append(kw))

    _record_provider_call(model="deepseek-chat", provider="deepseek",
                          usage={"prompt_tokens": 120, "completion_tokens": 40},
                          duration_ms=850)

    assert len(seen) == 1
    entry = seen[0]
    assert entry["model"] == "deepseek-chat"
    assert entry["provider"] == "deepseek"
    assert entry["prompt_tokens"] == 120
    assert entry["completion_tokens"] == 40
    # no price table on this path: an honest 0.0, never a fabricated figure
    assert entry["cost_usd"] == 0.0


def test_skeleton_call_without_usage_is_still_counted(monkeypatch):
    """A provider that reports no usage still counts as one call (tokens 0)."""
    import kairos.cost as cost_mod
    from kairos.skeleton.service import _record_provider_call

    seen: list = []
    monkeypatch.setattr(cost_mod, "record_entry", lambda **kw: seen.append(kw))

    _record_provider_call(model="m", provider="p", usage={}, duration_ms=5)

    assert len(seen) == 1
    assert seen[0]["prompt_tokens"] == 0
    assert seen[0]["completion_tokens"] == 0


def test_corrupt_newest_record_does_not_hide_the_run(tmp_path):
    """A half-written newer record is skipped, not surfaced as "none"."""
    import os
    from kairos.skeleton.runner import load_persisted_skeleton_state

    run_dir = tmp_path / ".kairos" / "skeleton-runs"
    run_dir.mkdir(parents=True)
    good = run_dir / "skeleton-run-good.json"
    good.write_text(json.dumps({
        "run_id": "good", "outcome": "passed", "attempts": 1,
        "task": {"instruction": "读文档"}, "result": {"artifacts": ["outputs/report.md"]},
        "verdict": {"passed": True, "verifier": "citations",
                    "evidence": [{"criterion": "c", "satisfied": True}]},
        "history": [],
    }, ensure_ascii=False), encoding="utf-8")
    bad = run_dir / "skeleton-run-bad.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    # make the corrupt record the newest one
    newer = good.stat().st_mtime + 60
    os.utime(bad, (newer, newer))

    state = load_persisted_skeleton_state(tmp_path, project_id="p1")
    assert state is not None
    assert state["run_id"] == "good"
    assert state["status"] == "done"


# ---------------------------------------------------------------------------
# (7) THE CHAT ROUTING: ``POST /{id}/chat`` now picks a lane, and the Coder
#     lane is byte-for-byte today's chat
# ---------------------------------------------------------------------------

def test_chat_plain_greeting_takes_the_general_lane(make_client, tmp_path, monkeypatch):
    """(chat 1) A plain greeting on an undecided workspace -> general lane."""
    monkeypatch.setattr("kairos.skeleton.service.default_generator",
                        lambda: _fake_chat_generate)
    empty = tmp_path / "empty"
    empty.mkdir()
    client, fake = make_client(empty)
    # a Coder IS wired, but must not be touched on the general lane
    coder = FakeCoder(fake.message_bus)
    fake.project.coder = coder

    r = client.post("/api/projects/p1/chat", json={"message": "你好，今天天气怎么样？"})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["route"] == "skeleton"
    assert body["route_source"] == "default"
    assert body["route_reason"]
    assert body["mode"] == "chat"
    assert body["reply"] == _fake_chat_generate("")
    assert body["message"] == "你好，今天天气怎么样？"
    # no verifier on the conversational general lane -> undecided, not failed
    assert body["verdict"]["passed"] is None
    # the Coder was NOT used
    assert coder.chat_calls == []
    # the reply still travelled the same chat event (agent.chat)...
    topics = [m.topic for m in fake.message_bus.get_history(limit=100)]
    assert "agent.chat" in topics
    # ...and the user's message was persisted on the same topic as the loop lane
    assert any(getattr(m, "topic", "") == "user.chat" for m in fake._db.saved)


def test_chat_general_lane_works_without_a_coder(make_client, tmp_path, monkeypatch):
    """The general lane does not need a Coder at all (unlike today's chat)."""
    monkeypatch.setattr("kairos.skeleton.service.default_generator",
                        lambda: _fake_chat_generate)
    empty = tmp_path / "empty"
    empty.mkdir()
    client, fake = make_client(empty)
    assert fake.project.coder is None

    r = client.post("/api/projects/p1/chat", json={"message": "帮我起个名字"})
    assert r.status_code == 200, r.text
    assert r.json()["route"] == "skeleton"


def test_chat_docs_workspace_question_takes_the_general_lane(make_client, docs_root, monkeypatch):
    """(chat 4) A question in a document workspace -> general lane."""
    monkeypatch.setattr("kairos.skeleton.service.default_generator",
                        lambda: _fake_chat_generate)
    client, fake = make_client(docs_root)
    fake.project.coder = FakeCoder(fake.message_bus)

    r = client.post("/api/projects/p1/chat", json={"message": "帮我总结这几份文档"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route"] == "skeleton"
    assert body["route_source"] == "heuristic"
    assert fake.project.coder.chat_calls == []


def test_chat_code_repo_question_takes_the_coder_lane(make_client, code_root):
    """(chat 3) A repo workspace + a plain question -> today's Coder path."""
    client, fake = make_client(code_root)
    coder = FakeCoder(fake.message_bus)
    fake.project.coder = coder

    r = client.post("/api/projects/p1/chat", json={"message": "这个项目是做什么的？"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert coder.chat_calls == [{"text": "这个项目是做什么的？", "voice_mode": False}]
    assert body["reply"] == "[coder] 这个项目是做什么的？"


def test_chat_coding_intent_takes_the_coder_lane(make_client, tmp_path):
    """(chat 2) A coding-intent message -> the Coder lane, even with no repo."""
    empty = tmp_path / "empty"
    empty.mkdir()
    client, fake = make_client(empty)
    coder = FakeCoder(fake.message_bus)
    fake.project.coder = coder

    r = client.post("/api/projects/p1/chat", json={"message": "修复 src/auth 里的登录 bug"})
    assert r.status_code == 200, r.text
    assert coder.chat_calls and coder.chat_calls[0]["text"] == "修复 src/auth 里的登录 bug"


def test_chat_plan_mode_takes_the_coder_lane(make_client, tmp_path):
    """A plan-mode (long) turn -> the Coder lane."""
    empty = tmp_path / "empty"
    empty.mkdir()
    client, fake = make_client(empty)
    coder = FakeCoder(fake.message_bus)
    fake.project.coder = coder

    r = client.post("/api/projects/p1/chat",
                    json={"message": "帮我做这个", "require_plan": True})
    assert r.status_code == 200, r.text
    assert coder.chat_calls


def test_chat_coder_lane_body_and_event_are_identical_to_today(make_client, tmp_path):
    """(chat 7) The reversed proof: the Coder lane is today's chat, word for word."""
    empty = tmp_path / "empty"
    empty.mkdir()
    client, fake = make_client(empty)
    coder = FakeCoder(fake.message_bus)
    fake.project.coder = coder

    r = client.post("/api/projects/p1/chat",
                    json={"message": "fix the auth bug", "voice_mode": True})
    assert r.status_code == 200, r.text
    body = r.json()

    # exactly today's four fields, in order -- no route/verdict fields leak in
    assert list(body.keys()) == ["project_id", "reply", "mode", "message"]
    assert body == {"project_id": "p1", "reply": "[coder] fix the auth bug",
                    "mode": "chat", "message": "fix the auth bug"}
    # coder.chat() got the text + voice_mode, exactly as before
    assert coder.chat_calls == [{"text": "fix the auth bug", "voice_mode": True}]
    # the reply travelled on the same event the Coder has always published
    hits = [m for m in fake.message_bus.get_history(limit=100)
            if m.topic == "agent.chat"]
    assert hits and hits[-1].sender == "p1.coder"
    assert hits[-1].content == "[coder] fix the auth bug"
    # the user message was persisted the same way (user.chat)
    saved = [m for m in fake._db.saved if getattr(m, "topic", "") == "user.chat"]
    assert saved and saved[-1].content == "fix the auth bug"


def test_chat_general_lane_without_a_model_is_a_503(make_client, tmp_path, monkeypatch):
    """Honest failure: the general lane needs a model; none -> 503, not a blank."""
    monkeypatch.setattr("kairos.skeleton.service.default_generator", lambda: None)
    empty = tmp_path / "empty"
    empty.mkdir()
    client, fake = make_client(empty)

    r = client.post("/api/projects/p1/chat", json={"message": "你好"})
    assert r.status_code == 503, r.text
    assert "general lane" in r.json()["detail"]

