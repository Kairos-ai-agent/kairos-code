"""Slot heights: the shell owns the viewport, everything else fills it.

Why this file exists
--------------------
Two rounds of the same bug. The shell column is::

    <Layout minHeight="100vh">
      <Header height=52 />
      <Layout>
        <Sider><ChatSidebar/></Sider>
        <Content overflow="hidden">
          <UpdateBanner/>
          <Outlet/>          <- the page

Round 1: ``ChatThread`` hard-coded ``height: calc(100vh - 52px)`` while living
inside a slot that the page topbar and composer had already shortened. The box
was ~190px taller than the slot, the slot's ``overflow: hidden`` clipped the
tail of long replies, and the inner scrollbar covered the taller box — so the
clipped part could not be reached by scrolling either. The DOM held every
character; the user saw "the first messages are hidden and the reply is cut off".

Round 2: the fix stopped at ``ChatThread``, because page roots (Chat,
ChatSidebar) were assumed to be children of the shell rather than of a flex
slot. They are not safe either: ``<UpdateBanner />`` lives in that same column,
so *any* ``100vh - <constant>`` is wrong whenever the banner is up. The DOM is
correct and scrollable; the bottom is simply outside the clipped box.

The rule, now structural
------------------------
1. Exactly one element computes viewport units: the shell's ``<Content>``, and
   it derives the offset from ``LAYOUT.topbarHeight`` so the two cannot drift.
2. That element lays out as a flex column, so the banner takes its own height
   and the page gets the remainder through ``flex: 1; minHeight: 0``.
3. Nothing else in ``web/src`` computes 100vh. Components and pages fill with
   ``height: '100%'`` plus ``minHeight: 0`` so a flex parent can shrink them.

These are source-level checks on purpose: jsdom has no layout engine, so a
rendered test could never catch a clipping bug.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "web/src"
THREAD = SRC / "components/ChatThread.tsx"
CHAT = SRC / "pages/Chat.tsx"
SHELL = SRC / "components/AppLayout.tsx"

# Page roots that used to compute their own viewport height. They are children
# of the shell column, which also holds the update banner.
PAGE_ROOTS = [
    CHAT,
    SRC / "components/ChatSidebar.tsx",
    SRC / "pages/Loop.tsx",
]


def _code(path: Path) -> str:
    """Source with comments stripped, so a comment mentioning the old value
    cannot satisfy (or break) these checks.

    A line-based stripper is not enough: comment *continuation* lines carry no
    marker of their own (this file's own first version missed a ``{/* ... */}``
    block in Chat.tsx for exactly that reason). Scan character-wise instead.

    ``//`` only starts a comment when the previous character is not ``:``, so
    ``https://`` inside a string literal does not eat the rest of the line.
    """
    text = path.read_text(encoding="utf-8")
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


def test_page_roots_do_not_compute_viewport_height():
    """Round 2 of the bug: a page root inside the shell column is no safer
    than a nested component — the shell also hosts the update banner."""
    for path in PAGE_ROOTS:
        assert "calc(100vh" not in _code(path), (
            f"{path.name} computes its own viewport height again. The shell "
            f"column holds the update banner as well, so `100vh - <constant>` "
            f"is taller than the real slot and the shell's overflow:hidden "
            f"clips the bottom away. Use `height: '100%', minHeight: 0`."
        )


def test_shell_owns_the_viewport_height():
    code = _code(SHELL)
    assert "calc(100vh - ${LAYOUT.topbarHeight}px)" in code, (
        "the shell's Content must derive its height from the same constant "
        "the Header uses; a literal would drift the moment the topbar changes"
    )
    assert "flex: 1, minHeight: 0" in code, (
        "the Content must lay out as a flex column and hand the page the "
        "remainder, otherwise the banner's height is unaccounted for"
    )


def test_only_the_shell_uses_viewport_units():
    """A global sweep, so a new page cannot reintroduce the bug."""
    offenders = []
    for path in sorted(SRC.rglob("*.ts")) + sorted(SRC.rglob("*.tsx")):
        if path == SHELL or "/test/" in path.as_posix() or ".test." in path.name:
            continue
        if "calc(100vh" in _code(path):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert not offenders, (
        "these files compute viewport height outside the shell: "
        f"{offenders}. Fill with `height: '100%', minHeight: 0` instead."
    )
