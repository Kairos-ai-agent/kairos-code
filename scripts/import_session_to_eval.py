"""Import a production session log into a fresh eval suite.

The use case: you ran the Coder against a real requirement, the
session produced outputs that you liked, and now you want to
make sure it never regresses on that task. So you run this
script to turn the session into a ``kairos_eval.yaml`` that
asserts the same output quality.

Two modes:

  1. **Replay** (default) — produce a suite whose cases pin the
     original output's content (contains, exact). Future runs
     that emit the same content pass; regressions fail.

  2. **Scaffold** — produce a suite whose cases just have the
     prompt and empty graders, so a human can fill in the
     expectations later. Use this when you want to *track*
     the requirement but haven't yet decided what "good"
     means.

Inspired by AgentLens (the "dataset factory" pattern — every
interesting session becomes a test case).

Usage:
    python -m scripts.import_session_to_eval \\
        --session <session.json> \\
        --out examples/eval_from_session.yaml \\
        --mode replay

The session JSON is the same shape ``kairos.eval.load_suite_result``
accepts: ``{"suite_name", "cases": [{"name", "output", ...}, ...]}``
or a LoopSession dump (with ``history`` and ``coder_result``).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List


def cases_from_session_json(data: Dict[str, Any], mode: str
                           ) -> List[Dict[str, Any]]:
    """Convert a session log into a list of YAML cases."""
    cases: List[Dict[str, Any]] = []
    # Format A: kairos.eval suite_result
    if "cases" in data and isinstance(data["cases"], list):
        for c in data["cases"]:
            cases.append(_case_from_eval(c, mode))
        return cases
    # Format B: LoopSession dump (history + coder_result per round)
    if "history" in data and isinstance(data["history"], list):
        for h in data["history"]:
            cases.append(_case_from_history(h, mode))
        return cases
    # Format C: single requirement + final answer
    if "requirement" in data and "output" in data:
        cases.append(_case_from_one_shot(data, mode))
        return cases
    raise ValueError(
        "Unrecognized session format — expected one of:\n"
        "  - eval suite result: {suite_name, cases: [...]}\n"
        "  - loop session: {history: [{coder, review, ...}]}\n"
        "  - one-shot: {requirement, output}"
    )


def _case_from_eval(c: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """Case from a kairos.eval suite_result row."""
    name = c.get("name", "<unnamed>")
    output = c.get("output", "") or ""
    if mode == "scaffold":
        return {"name": name, "input": c.get("input", ""),
                "graders": []}
    # Replay: pin a few representative substrings + first regex match
    needles = _extract_needles(output, max_n=5)
    graders: List[Dict[str, Any]] = []
    if needles:
        graders.append({"contains": needles})
    # Pin the first code-like regex (function or class def)
    regex = _extract_first_def(output)
    if regex:
        graders.append({"regex": regex})
    return {"name": name,
            "input": c.get("input", ""),
            "graders": graders}


def _case_from_history(h: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """Case from a LoopSession history entry."""
    round_no = h.get("round", 0)
    output = (h.get("coder") or h.get("output") or
              h.get("coder_result") or "").strip()
    # The "input" for replay purposes is the round summary
    input_str = (
        f"Round {round_no} regression check "
        f"(from session {h.get('session_id', 'unknown')})"
    )
    if mode == "scaffold":
        return {"name": f"round-{round_no}",
                "input": input_str, "graders": []}
    needles = _extract_needles(output, max_n=5)
    graders: List[Dict[str, Any]] = []
    if needles:
        graders.append({"contains": needles})
    regex = _extract_first_def(output)
    if regex:
        graders.append({"regex": regex})
    return {"name": f"round-{round_no}", "input": input_str,
            "graders": graders}


def _case_from_one_shot(d: Dict[str, Any], mode: str) -> Dict[str, Any]:
    output = d.get("output", "")
    if mode == "scaffold":
        return {"name": "replay-1", "input": d["requirement"], "graders": []}
    needles = _extract_needles(output, max_n=5)
    graders: List[Dict[str, Any]] = [{"contains": needles}] if needles else []
    regex = _extract_first_def(output)
    if regex:
        graders.append({"regex": regex})
    return {"name": "replay-1", "input": d["requirement"],
            "graders": graders}


def _extract_needles(text: str, max_n: int = 5) -> List[str]:
    """Pull up to max_n representative non-trivial substrings.

    Heuristic: split on whitespace, filter to tokens >= 4 chars,
    dedup, and take the first max_n. We deliberately avoid
    pinning common words like "the", "and", etc. — they're
    noise. For real apps, hand-curate the needles after import.
    """
    STOP = {
        "the", "and", "for", "with", "this", "that", "from",
        "are", "was", "were", "have", "has", "had", "you",
        "your", "but", "not", "any", "all", "can", "will",
    }
    seen: List[str] = []
    for word in text.split():
        w = word.strip(".,;:()[]{}!?'\"`")
        if len(w) < 4:
            continue
        if w.lower() in STOP:
            continue
        if w in seen:
            continue
        seen.append(w)
        if len(seen) >= max_n:
            break
    return seen


def _extract_first_def(text: str) -> str:
    """Return a regex string matching the first function/class def
    in the output, or empty if none found. Used as a structural
    pin (don't change function names; do change implementations)."""
    import re
    m = re.search(r"(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", text)
    if m:
        # Escape the name for safe inclusion in a regex pattern
        return r"\b" + re.escape(m.group(0).split()[1]) + r"\b"
    return ""


def write_yaml(cases: List[Dict[str, Any]], out_path: Path,
               name: str = "imported") -> None:
    """Serialize cases as a minimal hand-rolled YAML (no PyYAML
    dep at write time, so this script is portable)."""
    lines: List[str] = [
        f"# Imported from session log on {Path(__file__).name}",
        f"# Hand-curate the graders below before committing.",
        f"name: {name}",
        f"threshold: 0.5",
        "cases:",
    ]
    for case in cases:
        lines.append(f"  - name: {_yaml_str(case['name'])}")
        lines.append(f"    input: {_yaml_str(case.get('input', ''))}")
        if not case.get("graders"):
            lines.append("    graders: []")
        else:
            lines.append("    graders:")
            for g in case["graders"]:
                lines.append(f"      - {_dump_grader(g)}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _yaml_str(s: str) -> str:
    """Quote a string for inclusion as a YAML scalar."""
    if any(c in s for c in [":", "#", "\n", '"']) or not s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _dump_grader(g: Dict[str, Any]) -> str:
    """Compact YAML form for a single-key grader dict."""
    (k, v), = g.items()
    if isinstance(v, str):
        return f"{k}: {_yaml_str(v)}"
    if isinstance(v, list):
        return f"{k}: [{', '.join(_yaml_str(x) for x in v)}]"
    return f"{k}: {v}"


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        description="Convert a production session log into a kairos.eval suite",
    )
    p.add_argument("--session", required=True,
                    help="Path to a session log (eval result / loop history / one-shot)")
    p.add_argument("--out", required=True,
                    help="Output YAML path")
    p.add_argument("--mode", choices=("replay", "scaffold"),
                    default="replay",
                    help="replay = pin output content; scaffold = empty graders")
    p.add_argument("--name", default="imported",
                    help="Suite name to write into the YAML")
    args = p.parse_args(argv)

    data = json.loads(Path(args.session).read_text(encoding="utf-8"))
    cases = cases_from_session_json(data, args.mode)
    if not cases:
        print("No cases extracted — check the session log format", file=sys.stderr)
        return 1
    write_yaml(cases, Path(args.out), name=args.name)
    print(f"Wrote {len(cases)} case(s) to {args.out} (mode={args.mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
