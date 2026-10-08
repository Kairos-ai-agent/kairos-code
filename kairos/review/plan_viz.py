"""Plan visualization helpers for ``GET /api/projects/{id}/plan/visualization``.

Two jobs, both pure and offline:

  * turn a *structured* plan (``kairos.loop.plan.Plan`` — a list of
    ``TodoItem{status, content, activeForm}``) into a Mermaid ``flowchart``
    with one node per todo, sequential edges, and a ``classDef`` per status
    (``pending`` / ``in_progress`` / ``completed``) so the UI can colour them;
  * when there is no structured plan but the Coder's free-form ``plan_text``
    exists, render **the plan's own lines** (top-level numbered / bulleted
    items) as nodes. This is explicitly a *rendering of the plan text the
    Coder wrote*, **not** a model-generated graph — the header line of the
    returned block carries a ``%%`` comment saying so.

Escaping is done by :func:`escape_mermaid_label`. Plan text routinely holds
Chinese, quotes, newlines, ``-->``, parentheses and ``#`` — any of which, if
spliced raw into a ``id["label"]`` node, either breaks the diagram or (via a
stray ``"]``) injects extra Mermaid syntax. Every label therefore goes
through one function that:

  * normalises CRLF/CR to LF, then turns newlines into ``<br/>``;
  * escapes ``&`` → ``&amp;``, ``<`` → ``&lt;``, ``>`` → ``&gt;``;
  * escapes ``#`` → ``#35;`` and ``"`` → ``#quot;`` (Mermaid's entity syntax)
    so the label's own quote can never close the node early and ``#`` can
    never start an entity.

Node ids are generated (``n0``, ``n1``, ...) rather than derived from the
label, so hostile content can never reach the id position either.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional

# Status → (classDef fill/stroke/colour). Kept in one place so a new status
# fails loudly in tests rather than silently rendering an uncoloured node.
_STATUS_STYLE = {
    "pending": "fill:#fffbe6,stroke:#faad14,color:#874d00",
    "in_progress": "fill:#e6f7ff,stroke:#1677ff,color:#003a8c",
    "completed": "fill:#f6ffed,stroke:#52c41a,color:#135200",
}

# Top-level numbered / bulleted plan lines: "1. x", "1) x", "Step 3: x",
# "- x", "* x", "+ x". Used by the free-form-text fallback only.
_ITEM_RE = re.compile(
    r"^\s*(?:step\s*\d+\s*[:.)-]\s*|[-*+]\s+|\d+\s*[.)\]:]\s+)(.+?)\s*$",
    re.IGNORECASE,
)


def escape_mermaid_label(text: Any) -> str:
    """Return *text* safe to splice inside a Mermaid ``id["..."]`` label.

    Never returns a raw newline, a raw ``<``/``>``, a bare ``#`` or a bare
    ``"`` — see the module docstring for the exact transformations.
    """
    s = "" if text is None else str(text)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;").replace(">", "&gt;")
    s = s.replace("#", "#35;")
    s = s.replace('"', "#quot;")
    # Newlines last: <br/> is inserted after the < / > sweep so it stays a
    # real Mermaid line break instead of being escaped to &lt;br/&gt;.
    s = s.replace("\n", "<br/>")
    return s


def _todo_fields(todo: Any) -> tuple[str, str]:
    """(status, content) from a ``TodoItem`` or its dict form."""
    if isinstance(todo, dict):
        status = str(todo.get("status", "pending") or "pending")
        content = todo.get("content", "")
    else:
        status = str(getattr(todo, "status", "pending") or "pending")
        content = getattr(todo, "content", "")
    if status not in _STATUS_STYLE:
        status = "pending"
    return status, "" if content is None else str(content)


def todos_to_mermaid(todos: Iterable[Any]) -> str:
    """Mermaid flowchart for a structured todo list (status-coloured).

    Returns ``""`` for an empty list so the caller can fall through to the
    text path (and then to 404).
    """
    items = list(todos or [])
    if not items:
        return ""
    lines: List[str] = ["flowchart TD"]
    by_status: dict[str, List[str]] = {}
    prev: Optional[str] = None
    for i, todo in enumerate(items):
        status, content = _todo_fields(todo)
        nid = f"n{i}"
        label = escape_mermaid_label(content or f"todo {i + 1}")
        lines.append(f'    {nid}["{label}"]')
        if prev is not None:
            lines.append(f"    {prev} --> {nid}")
        prev = nid
        by_status.setdefault(status, []).append(nid)
    for status, style in _STATUS_STYLE.items():
        lines.append(f"    classDef {status} {style}")
    for status, ids in by_status.items():
        lines.append(f"    class {','.join(ids)} {status}")
    return "\n".join(lines)


def _plan_text_items(text: str) -> List[str]:
    """Top-level items of a free-form plan, in order.

    Prefers numbered / bulleted lines; if the plan has none (plain prose),
    falls back to the non-empty lines. Blank lines and the header are
    dropped.
    """
    items: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _ITEM_RE.match(line)
        items.append((m.group(1) if m else line).strip())
    return items


def plan_text_to_mermaid(text: str) -> str:
    """Mermaid flowchart of **the plan text's own lines** (fallback path).

    This is a presentation of the Coder's plan prose — one node per
    top-level numbered/bulleted item — not a graph an LLM generated. The
    block header says so via a ``%%`` comment. Returns ``""`` when there is
    no usable text.
    """
    items = _plan_text_items(text)
    if not items:
        return ""
    lines: List[str] = [
        "flowchart TD",
        "    %% rendering of the plan text (Coder-authored), not a generated graph",
    ]
    prev: Optional[str] = None
    for i, item in enumerate(items):
        nid = f"n{i}"
        label = escape_mermaid_label(item)
        lines.append(f'    {nid}["{label}"]')
        if prev is not None:
            lines.append(f"    {prev} --> {nid}")
        prev = nid
    return "\n".join(lines)


def entries_to_tree_text(
    entries: Iterable[Any], *, max_entries: int = 200, indent: str = "  "
) -> str:
    """Render workbench walk entries (``FileEntry``) as an indented text tree.

    ``entries`` are ``{path, name, is_dir}`` records from
    ``api.routes.workbench._walk`` — ordered depth-first, so indentation by
    path depth reconstructs the tree. The response is **capped** at
    ``max_entries`` with an explicit ``(+N more)`` footer: a large repo can
    never produce an unbounded response body.
    """
    rows = list(entries or [])
    if not rows:
        return "(empty)"
    shown = rows[: max(0, int(max_entries))]
    lines: List[str] = []
    for e in shown:
        path = getattr(e, "path", None)
        if path is None and isinstance(e, dict):
            path = e.get("path", "")
        name = getattr(e, "name", None)
        if name is None and isinstance(e, dict):
            name = e.get("name", "")
        is_dir = getattr(e, "is_dir", None)
        if is_dir is None and isinstance(e, dict):
            is_dir = e.get("is_dir", False)
        depth = str(path or "").count("/")
        lines.append(f"{indent * depth}{name}{'/' if is_dir else ''}")
    remaining = len(rows) - len(shown)
    if remaining > 0:
        lines.append(f"... (+{remaining} more entries, truncated)")
    return "\n".join(lines)
