"""R41 — the task loop runs hidden, and leaves no "运行中" behind.

Two complaints, one code path:

  * "任务 loop 能否隐藏后台运行，不要把 agent 回复页面切换" — the loop must not
    take the chat page. An earlier round stopped the auto-navigation but
    replaced it with a strip above the composer ("运行中，页面会自动刷新…
    当前轮次 1 · 最终评分: 100"), and let the loop's own session title take over
    the chat header.
  * "做完后 agent 回复最底部一直显示运行中" — after the run finished, the
    "运行中" badge stayed up next to the final score.

Root cause of the second one: the frontend inferred ``running`` from whatever
``GET /loop`` last returned, and every terminal topic (``loop.completed``,
``loop.finished``, …) is published from *inside* the still-unwinding loop task —
so the refetch that event triggered read ``running: true``, nothing followed it,
and the badge never came off. A cancelled run (the Stop button) published no
terminal event at all, so ``loop.ended`` is now published from the task-done
callback — the one place that runs after the loop has really finished.

What pins it:
  - the chat page renders no loop running banner, and does not title itself with
    the loop's own session (source);
  - the terminal topics are declared once and clear the flag explicitly (source);
  - a terminal event beats a later refetch reporting ``running: true``
    (behaviour: ``web/src/test/chatWebSocketRunning.test.tsx``);
  - ``_on_loop_done`` really publishes ``loop.ended`` onto the bus, with the
    right status, on approval / cancel / crash (behaviour, below).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_SRC = REPO_ROOT / "web" / "src"
CHAT = WEB_SRC / "pages" / "Chat.tsx"
LOOPCTL = REPO_ROOT / "kairos" / "core" / "orchestrator_parts" / "loopctl.py"


def _read(path: Path) -> str:
    assert path.exists(), f"{path} does not exist"
    return path.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """Source with ``//`` and ``/* */`` comments removed.

    A comment must never be able to satisfy — or break — a check. ``//`` only
    starts a comment when the previous character is not ``:``, so a URL inside a
    string literal does not swallow the rest of the line.
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_block = False
    while i < n:
        if in_block:
            end = text.find("*/", i)
            if end == -1:
                break
            i = end + 2
            in_block = False
            continue
        if text.startswith("/*", i):
            in_block = True
            i += 2
            continue
        if text.startswith("//", i) and (i == 0 or text[i - 1] != ":"):
            nl = text.find("\n", i)
            if nl == -1:
                break
            i = nl
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# 1. The chat page renders no loop running banner
# ---------------------------------------------------------------------------


def test_chat_page_renders_no_loop_running_banner():
    """No background-task strip, and no "运行中，页面会自动刷新…" copy.

    The strip rendered right above the composer whenever ``loopState.running``
    was true — the loop competing with the thread for the same column, on the
    page the user is reading. The loop page is its own tab and the sidebar lists
    one row per session, so nothing about the run belongs here.
    """
    src = _strip_comments(_read(CHAT))
    assert 'data-testid="background-task"' not in src, (
        "Chat.tsx renders the background-task strip again — that is the banner "
        "the loop was supposed to stop occupying the chat page with"
    )
    assert 'data-testid="background-task-view"' not in src, (
        "the strip's 'view' link is back; the run is reached from the sidebar "
        "or the loop page, not from a banner over the thread"
    )
    for key in ("run.running", "run.liveRound", "run.finalScore"):
        assert f"t('{key}')" not in src, (
            f"Chat.tsx renders {key} again — the chat page must not carry the "
            f"loop's running/round/score banner"
        )


def test_chat_header_does_not_take_the_loops_own_session():
    """The header names the session the user opened, never the loop's.

    With the strip gone, the loop's session id in the header was the remaining
    way a background run could look like it owned the page: run a task, and the
    chat header says "会话 <loop session>" instead of the project you are
    actually reading.
    """
    src = _strip_comments(_read(CHAT))
    assert "loopState.session_id.slice" not in src, (
        "the chat header uses the loop's session id as its title again; it "
        "should show the session the user opened (or the project)"
    )


# ---------------------------------------------------------------------------
# 2. An explicit terminal state, declared once
# ---------------------------------------------------------------------------


def _terminal_topics_block(src: str) -> str:
    start = src.find("const LOOP_TERMINAL_TOPICS")
    assert start != -1, (
        "Chat.tsx no longer declares LOOP_TERMINAL_TOPICS — the set of events "
        "that end a run has to live in one place, or 'clear every running flag' "
        "and 'what ends a run' drift apart"
    )
    end = src.find("]);", start)
    assert end != -1, "could not find the end of LOOP_TERMINAL_TOPICS"
    return src[start:end]


def test_chat_page_declares_the_terminal_topics_in_one_place():
    src = _strip_comments(_read(CHAT))
    block = _terminal_topics_block(src)
    # The backend's explicit terminal event, plus every topic the loop runner
    # publishes right before it returns.
    for topic in ("loop.ended", "loop.completed", "loop.finished",
                  "loop.error", "loop.rejected"):
        assert f"'{topic}'" in block, (
            f"{topic} is missing from LOOP_TERMINAL_TOPICS — a run that ends "
            f"that way would leave the '运行中' badge up"
        )
    # loop.approved is the plan decision, not the end of the run: treating it as
    # terminal would clear the badge on a loop that is still working.
    assert "'loop.approved'" not in block, (
        "loop.approved is in LOOP_TERMINAL_TOPICS — it is the plan-approval "
        "decision and the loop carries on after it"
    )


def test_chat_page_clears_running_on_a_terminal_event():
    """The terminal branch sets the flag to false itself.

    This is the assertion that pins the fix: the flag is cleared from the event
    (``running: false``), not left to be inferred from a refetch.
    """
    src = _strip_comments(_read(CHAT))
    assert "running: false" in src, (
        "Chat.tsx has no explicit `running: false` — the '运行中' flag is again "
        "being inferred instead of cleared when a terminal event arrives"
    )
    assert re.search(r"if \(ended\)\s*\{[\s\S]{0,600}?running: false", src), (
        "the `running: false` is not inside the terminal-event branch — every "
        "running flag must be cleared as the terminal event arrives"
    )
    assert "endedSessionsRef" in src, (
        "the set of sessions whose run has ended is gone; without it a later "
        "GET /loop re-reporting `running: true` resurrects the badge"
    )
    assert "has(sessionId)" in src, (
        "nothing consults the closed-session set when folding a GET /loop "
        "response into the loop state"
    )


def test_chat_page_only_seeds_running_from_a_response():
    """A GET /loop response may seed the flag, never override a terminal event."""
    src = _strip_comments(_read(CHAT))
    assert "export function mergeLoopState" in src, (
        "mergeLoopState is gone — the one place that decides how a refetch folds "
        "into the loop state is the guard against a stale 'running: true'"
    )
    assert "setLoopState(r.data)" not in src, (
        "a GET /loop response is applied verbatim again — the loop task is still "
        "unwinding when it publishes its last event, so that response says "
        "running: true and nothing corrects it afterwards"
    )


# ---------------------------------------------------------------------------
# 3. The backend announces the terminal state
# ---------------------------------------------------------------------------


def test_backend_publishes_loop_ended_from_the_task_done_callback():
    src = _read(LOOPCTL)
    assert "def _on_loop_done" in src
    assert "def _announce_loop_ended" in src, (
        "loopctl.py no longer has a publish point for the terminal state"
    )
    done = src[src.index("def _on_loop_done"):
               src.index("def _maybe_reflect")]
    assert "_announce_loop_ended(" in done, (
        "_on_loop_done does not announce the terminal state — that callback is "
        "the only place that runs after the loop has really finished (approved, "
        "crashed, or cancelled with Stop)"
    )
    assert 'topic="loop.ended"' in src, (
        "the backend does not publish loop.ended; the frontend's terminal set "
        "expects it"
    )
    assert '"status": status' in src, (
        "the terminal event carries no status field (stopped / failed / done)"
    )


async def _drive_on_loop_done(kind: str, user_stopped: bool):
    """Run the real ``_on_loop_done`` over a real MessageBus.

    Returns ``(topics_seen, project)``.
    """
    from kairos.core.message_bus import MessageBus
    from kairos.core.orchestrator_parts.loopctl import OrchLoopControlMixin

    class _Db:
        def save_project(self, p):
            pass

        def load_loop_rounds(self, project_id, limit=20):
            return []

    class _Session:
        session_id = "sess-abc123"

        def __init__(self):
            self.user_stopped = user_stopped

    class _Project:
        def __init__(self):
            self.loop_session = _Session()
            self.status = "running"

    project = _Project()
    bus = MessageBus()
    seen: list = []
    bus.add_listener(lambda m: seen.append(m))

    class _Orch(OrchLoopControlMixin):
        def __init__(self):
            self._projects = {"p1": project}
            self._db = _Db()
            self.message_bus = bus
            self._dispatch_tasks = set()

    orch = _Orch()

    async def _body():
        if kind == "crash":
            raise RuntimeError("boom")
        if kind == "finished":
            return "ok"
        await asyncio.sleep(30)  # the 'cancelled' kind: stays pending

    task = asyncio.create_task(_body())
    await asyncio.sleep(0)  # let it start
    if kind == "cancelled":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif kind == "crash":
        with pytest.raises(RuntimeError):
            await task
    else:
        assert await task == "ok"

    assert task.done(), "the callback only fires once the task is done"

    orch._on_loop_done("p1", task)
    # `_announce_loop_ended` schedules the publish as a task on this loop.
    for _ in range(20):
        await asyncio.sleep(0)
    return seen, project


@pytest.mark.parametrize(
    "kind,user_stopped,expected_status",
    [
        ("cancelled", False, "stopped"),
        ("crash", False, "failed"),
        ("finished", False, "done"),
        ("finished", True, "stopped"),
    ],
)
def test_backend_loop_ended_actually_reaches_the_bus(kind, user_stopped,
                                                     expected_status):
    """Behavioural: the terminal event is really published, on every path.

    The publish is scheduled as a task (the callback is a sync frame), so this
    also proves the task is created and the message lands — including for the
    cancelled path, which used to publish nothing at all.
    """
    seen, project = asyncio.run(_drive_on_loop_done(kind, user_stopped))

    ended = [m for m in seen if m.topic == "loop.ended"]
    assert len(ended) == 1, (
        f"expected exactly one loop.ended on the bus, saw "
        f"{[m.topic for m in seen]}"
    )
    msg = ended[0]
    assert msg.metadata.get("project_id") == "p1"
    assert msg.metadata.get("session_id") == "sess-abc123"
    assert msg.metadata.get("status") == expected_status, (
        f"a {kind!r} run should report status {expected_status!r}, got "
        f"{msg.metadata.get('status')!r}"
    )


def test_backend_loop_ended_reaches_a_websocket_client():
    """The event survives the WS wiring: every bus message is forwarded.

    api/routes/websocket.py attaches one listener per client and forwards
    ``msg.to_dict()`` as ``{type: "activity", message: …}``. A new topic needs no
    server change — which is exactly the assumption worth pinning, because a
    topic allow-list here would silently swallow ``loop.ended`` and bring the
    stuck badge back.
    """
    ws = _read(REPO_ROOT / "api" / "routes" / "websocket.py")
    assert '"type": "activity"' in ws, "the WS envelope shape changed"
    assert 'message": msg.to_dict()' in ws, (
        "the WS listener no longer forwards the message verbatim"
    )
    listener = ws[ws.index("async def listener(msg)"):ws.index("listener_token")]
    # The listener's only use of `msg.topic` is the optional agent_update push;
    # nothing filters on it, so loop.ended is forwarded like any other topic.
    assert re.findall(r"msg\.topic[^\n]*", listener) == [
        "msg.topic in AGENT_STATE_TRIGGERS:"
    ], (
        "the WS listener conditions on msg.topic beyond the agent_update push — "
        "a topic allow-list would drop loop.ended before the browser sees it"
    )


def test_frontend_and_backend_agree_on_the_topic_name():
    """The topic the backend publishes is the one the frontend subscribes to."""
    backend = _read(LOOPCTL)
    frontend = _read(CHAT)
    assert 'topic="loop.ended"' in backend
    assert "'loop.ended'" in _terminal_topics_block(_strip_comments(frontend)), (
        "the frontend's terminal set does not list loop.ended — the backend "
        "would publish a terminal event nothing reacts to"
    )
