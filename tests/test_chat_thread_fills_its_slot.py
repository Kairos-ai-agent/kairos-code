"""The chat thread must not size itself with 100vh.

ChatThread used to hard-code ``height: calc(100vh - 52px)``. Its slot is
already shortened by the page topbar and the composer, so the box was ~190px
taller than the slot; the slot's ``overflow: hidden`` then clipped the tail of
long replies, and because the inner scrollbar covered the taller box, reaching
the clipped part by scrolling was impossible. The user saw "the first messages
are hidden and the reply is cut off" while the DOM held every character.

Rule: a component nested inside someone else's flex slot never computes 100vh.
It fills with ``height: 100%`` and the parent supplies ``minHeight: 0`` so it
can actually shrink. Page roots (Chat's own root, ChatSidebar) may still use
``100vh - N``: they are children of the shell, not of another flex slot.

These are source-level checks on purpose: jsdom has no layout engine, so a
rendered test could never catch a clipping bug.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THREAD = ROOT / "web/src/components/ChatThread.tsx"
CHAT = ROOT / "web/src/pages/Chat.tsx"


def _code(path: Path) -> str:
    """Source with comments stripped, so a comment mentioning the old
    value cannot satisfy (or break) these checks."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(("//", "/*", "*")):
            continue
        out.append(line)
    return "\n".join(out)


def test_thread_does_not_compute_viewport_height():
    code = _code(THREAD)
    assert "calc(100vh" not in code, (
        "ChatThread computes its own viewport height again; it lives inside a "
        "flex slot that the topbar and composer already shrank, so the box "
        "will overflow and the slot's overflow:hidden will clip content that "
        "no scroll can reach"
    )


def test_thread_fills_its_slot():
    code = _code(THREAD)
    assert "height: '100%'" in code, "ChatThread should fill its parent"
    assert "minHeight: 0" in code, (
        "without minHeight:0 a flex child refuses to shrink below its content"
    )


def test_chat_page_slot_can_shrink():
    code = _code(CHAT)
    assert "flex: 1, minHeight: 0, overflow: 'hidden'" in code, (
        "the thread slot must be allowed to shrink, otherwise the thread "
        "cannot fit and its bottom is clipped"
    )
