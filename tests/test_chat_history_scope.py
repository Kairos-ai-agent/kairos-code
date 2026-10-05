"""The chat thread must show the loop history with no session selected.

The bug this pins: the loop half of a conversation (each round's Coder summary
and Reviewer verdict) reached the thread ONLY through
``/projects/{id}/sessions/{sid}/rounds``. When the page stopped auto-opening
the newest session — it used to yank the user out of whatever they were
reading, and the loop view has its own bar — ``sid`` is null on load, so that
branch never ran and every round silently disappeared. A real project with
6268 stored rows came back as the handful of bubbles that happened to sit
under a chat topic.

These are source-level pins (the repo's convention for UI wiring, see
tests/test_r37_ui_source.py); the live behaviour is asserted by the route and
by the end-to-end run recorded in the commit message.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAT_TSX = ROOT / "web" / "src" / "pages" / "Chat.tsx"
PROJECTS_PY = ROOT / "api" / "routes" / "projects.py"
TYPES_TS = ROOT / "web" / "src" / "types" / "index.ts"


def test_backend_exposes_project_wide_rounds():
    src = PROJECTS_PY.read_text(encoding="utf-8")
    assert '@router.get("/{project_id}/rounds")' in src, (
        "the chat thread needs the project's rounds without a session"
    )
    assert "load_loop_rounds(project_id" in src, (
        "it should use the existing project-scoped loader"
    )


def test_frontend_loads_rounds_when_no_session_is_selected():
    src = CHAT_TSX.read_text(encoding="utf-8")
    # The session-scoped call stays for the selected-session case…
    assert "/sessions/${sid}/rounds" in src
    # …and the project-wide one covers the no-session case.
    assert "`/projects/${pid}/rounds`" in src, (
        "loadHistory must fetch project rounds when sid is null — otherwise "
        "the loop history is invisible on a fresh page load"
    )
    # It has to be the *null* branch, not another session-scoped call.
    assert "if (sid) {" in src and "} else {" in src


def test_round_ids_are_scoped_to_their_own_session():
    """Two sessions can both have a round 1.

    The synthetic ids used to be ``${sid}-${round}-coder``; with project-wide
    rounds and a null sid that collapses every session's round 1 into one id,
    and mergeHistory (which dedupes by id) silently drops the second copy.
    """
    src = CHAT_TSX.read_text(encoding="utf-8")
    assert "rd.session_id || fallbackScope" in src, (
        "the round id must be built from the row's own session_id"
    )


def test_session_round_carries_session_id():
    """The frontend relies on it, so the type must keep it."""
    types = TYPES_TS.read_text(encoding="utf-8")
    block = types.split("interface SessionRound {")[1].split("}")[0]
    assert "session_id" in block
