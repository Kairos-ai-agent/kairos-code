"""Mermaid plan visualization.

Converts the Coder's free-form plan text into a Mermaid flowchart so
the UI can render it as a real architecture diagram instead of a wall
of prose. Also extracts the file paths mentioned into a structured
tree the UI can render as a side panel.

Pure parsing — no LLM calls. We look for explicit patterns the Coder
prompt already teaches it to use ("Step N: ..." with file paths).
"""
from __future__ import annotations

import re
from typing import List, Tuple


# Match "Step N:" / "Step N -" / "N." / "N)" at the start of a line.
_STEP_RE = re.compile(r"(?im)^[\s\-\*]*(?:step\s*)?(\d+)[\.\:\)]\s+(.+?)$")
# Match backtick-wrapped paths: `path/to/file.py`
_PATH_RE = re.compile(r"`([\w\-./\\]+\.[a-zA-Z]{1,4})`")
# Match "create file path", "add path", "edit path" verb phrases.
_FILE_VERB_RE = re.compile(
    r"(?i)\b(create|add|edit|update|modify|write|delete|remove|refactor)"
    r"\s+(?:file\s+)?`?([\w\-./\\]+\.[a-zA-Z]{1,4})`?"
)


def extract_steps(plan_text: str) -> List[Tuple[int, str, List[str]]]:
    """Return [(step_no, description, files_mentioned)] for each step.

    Falls back to a single synthetic step if no explicit numbering is
    found \u2014 the Coder doesn't always follow the format strictly.
    """
    if not plan_text:
        return []
    steps = []
    matches = list(_STEP_RE.finditer(plan_text))
    if not matches:
        # No numbered steps; treat each non-empty paragraph as one step.
        for i, para in enumerate(p for p in plan_text.split("\n\n") if p.strip()):
            steps.append((i + 1, para.strip(), _PATH_RE.findall(para)))
        return steps

    for i, m in enumerate(matches):
        n = int(m.group(1))
        desc = m.group(2).strip()
        # Capture paths from this step's description up to the next step.
        end = matches[i + 1].start() if i + 1 < len(matches) else len(plan_text)
        segment = plan_text[m.start():end]
        files = list(set(_PATH_RE.findall(segment)))
        steps.append((n, desc, files))
    return steps


def plan_to_mermaid(plan_text: str) -> str:
    """Convert plan_text to a Mermaid `flowchart TD` block.

    Each step becomes a node; arrows connect sequential steps. Files
    mentioned in a step appear as a sub-node hanging off it.
    """
    steps = extract_steps(plan_text)
    if not steps:
        return "flowchart TD\n    empty[\"(empty plan)\"]"

    lines = ["flowchart TD"]
    prev_id = None
    for n, desc, files in steps:
        node_id = f"S{n}"
        # Truncate description for readability; full text is in the
        # side panel.
        short = desc.replace('"', "'").replace("\n", " ")[:60]
        if len(desc) > 60:
            short += "..."
        lines.append(f'    {node_id}["{n}. {short}"]')
        if prev_id:
            lines.append(f"    {prev_id} --> {node_id}")
        for f in files[:5]:  # cap at 5 files per step to keep diagram small
            file_id = f"{node_id}_{re.sub(r'[^a-zA-Z0-9]', '_', f)}"
            lines.append(f'    {node_id} --> {file_id}["{f}"]')
        prev_id = node_id
    return "\n".join(lines)


def plan_to_file_tree(plan_text: str) -> str:
    """Build a markdown file tree from paths mentioned in the plan.

    Groups files by directory and renders them as a fenced code block.
    """
    paths = list(set(_PATH_RE.findall(plan_text or "")))
    # Also pick up paths referenced via verb phrases ("create src/foo.py").
    paths += [m.group(2) for m in _FILE_VERB_RE.finditer(plan_text or "")]
    paths = sorted(set(p for p in paths if p))

    if not paths:
        return "(no file paths mentioned)"

    # Group by directory
    tree: dict = {}
    for p in paths:
        parts = p.replace("\\", "/").split("/")
        cur = tree
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = None  # file leaf

    def render(node: dict, prefix: str = "") -> List[str]:
        lines = []
        for k, v in sorted(node.items()):
            if v is None:
                lines.append(f"{prefix}{k}")
            else:
                lines.append(f"{prefix}{k}/")
                lines.extend(render(v, prefix + "  "))
        return lines

    return "\n".join(render(tree))