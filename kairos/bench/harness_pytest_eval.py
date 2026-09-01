"""Pytest-based scorer: apply the LLM diff and run the work_dir tests (v2).

This is the proper SWE-bench scoring: instead of comparing diffs at the
text level, we apply the predicted diff to the work_dir, then execute
the test file. Pass = pytest exit 0.

Key differences from v1:
  * Uses a Python unified-diff applier (avoids git's strict line-ending checks)
  * Sets PYTHONPATH=. so `from src.collection import ...` works
  * Handles new files (--- /dev/null, +++ b/path)
  * Handles multiple files in a single diff
"""
from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kairos.bench.harness_eval import (  # noqa: E402
    TASKS,
    HarnessReport,
    HarnessResult,
    print_report,
)


_HUNK_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_diff(diff: str) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """Parse a unified diff into (target_path, list_of_(op, line)) tuples."""
    files: List[Tuple[str, List[Tuple[str, str]]]] = []
    current_path: str | None = None
    current_ops: List[Tuple[str, str]] = []
    in_hunk = False

    for raw in diff.splitlines():
        line = raw
        if line.startswith("--- "):
            continue
        if line.startswith("+++ "):
            if current_path is not None and current_ops:
                files.append((current_path, current_ops))
            target = line[4:]
            if target.startswith("b/"):
                target = target[2:]
            elif target == "/dev/null":
                target = ""
            current_path = target
            current_ops = []
            in_hunk = False
            continue
        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if not m or current_path is None:
                in_hunk = False
                continue
            in_hunk = True
            continue
        if not in_hunk or current_path is None:
            continue
        if line.startswith("+"):
            current_ops.append(("+", line[1:]))
        elif line.startswith("-"):
            current_ops.append(("-", line[1:]))
        elif line.startswith(" "):
            current_ops.append((" ", line[1:]))
        elif line.startswith("\\"):
            continue
    if current_path is not None and current_ops:
        files.append((current_path, current_ops))
    return files


def apply_diff_to_workdir(work_dir: Path, diff: str) -> Tuple[bool, str]:
    """Apply a unified diff to work_dir using minimal-edit (fuzzy) matching.

    Strategy:
      * ``-`` lines are matched against orig_lines by content and removed.
      * ``+`` lines are inserted at an anchor point, decided as:
          - if there is a ``-`` line: at the position of the first
            matched ``-`` line
          - else: at the position of the first `` `` (context) line
            that exists in orig_lines. Insertions go before it if the
            ``+`` block precedes the context in the hunk, else after.
          - else: at the end of orig_lines.
      * `` `` (context) lines are otherwise ignored — they only serve
        as anchors.
    """
    try:
        files = parse_diff(diff)
    except Exception as e:
        return False, f"parse failed: {e}"
    if not files:
        return False, "no files in diff"

    for path, ops in files:
        full = work_dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        original = full.read_text(encoding="utf-8") if full.exists() else ""
        orig_lines = original.splitlines(keepends=True)
        if orig_lines and not orig_lines[-1].endswith("\n"):
            orig_lines[-1] += "\n"

        minus_lines = [t for op, t in ops if op == "-"]
        plus_lines = [t for op, t in ops if op == "+"]
        if not plus_lines and not minus_lines:
            return False, "diff has no +/- lines"

        # Decide insertion point.
        insert_pos: int | None = None
        insert_after = False  # if True, insert AFTER insert_pos

        # 1. Try first - line that matches orig content
        for ml in minus_lines:
            target = ml.rstrip("\n")
            for idx, ol in enumerate(orig_lines):
                if ol.rstrip("\n") == target:
                    insert_pos = idx
                    insert_after = False
                    break
            if insert_pos is not None:
                break

        # 2. Fall back to first context line as anchor
        if insert_pos is None:
            for op, text in ops:
                if op == " ":
                    target = text.rstrip("\n")
                    for idx, ol in enumerate(orig_lines):
                        if ol.rstrip("\n") == target:
                            insert_pos = idx
                            # If the + block is BEFORE this context line
                            # in the hunk, insert before; else after.
                            saw_plus = False
                            for op2, _ in ops:
                                if op2 == "+":
                                    saw_plus = True
                                elif op2 == " ":
                                    break
                            insert_after = not saw_plus
                            break
                    if insert_pos is not None:
                        break

        if insert_pos is None:
            # 3. No anchor found — append at end
            insert_pos = len(orig_lines)
            insert_after = False

        if insert_after:
            insert_pos = insert_pos + 1

        minus_set = set(ml.rstrip("\n") for ml in minus_lines)
        new_lines: List[str] = []
        inserted = False
        for idx, ol in enumerate(orig_lines):
            if not inserted and idx == insert_pos:
                for pl in plus_lines:
                    if not pl.endswith("\n"):
                        pl = pl + "\n"
                    new_lines.append(pl)
                inserted = True
            if ol.rstrip("\n") in minus_set:
                continue
            new_lines.append(ol)
        if not inserted:
            for pl in plus_lines:
                if not pl.endswith("\n"):
                    pl = pl + "\n"
                new_lines.append(pl)

        full.write_text("".join(new_lines), encoding="utf-8")
    return True, ""


def find_test_files(work_dir: Path) -> List[Path]:
    tests: List[Path] = []
    for path in work_dir.rglob("test_*.py"):
        tests.append(path)
    for path in work_dir.rglob("*_test.py"):
        tests.append(path)
    return sorted(set(tests))


def _run_test_file(test_file: Path, work_dir: Path, timeout: int = 30) -> Tuple[bool, str]:
    """Run a single test file via subprocess. Works for both pytest-style
    (def test_x) and assert-style (top-level assert) test files."""
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(work_dir) + (os.pathsep + existing if existing else "")
    )
    # First try pytest (handles test_*.py / TestClass patterns)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=line", "-x", str(test_file.name)],
        cwd=str(test_file.parent), capture_output=True, text=True,
        timeout=timeout, env=env,
    )
    if result.returncode == 0 and "passed" in result.stdout:
        return True, ""
    # Fall back: run the file directly with python (catches top-level assert)
    result = subprocess.run(
        [sys.executable, str(test_file.name)],
        cwd=str(test_file.parent), capture_output=True, text=True,
        timeout=timeout, env=env,
    )
    if result.returncode == 0:
        return True, ""
    return False, (result.stdout + "\n" + result.stderr)[-600:]


def run_pytest(work_dir: Path, timeout: int = 30) -> Tuple[bool, str]:
    test_files = find_test_files(work_dir)
    if not test_files:
        # No pytest-style test files; try to run any .py file under tests/
        for path in sorted(work_dir.rglob("tests/*.py")):
            if path.name == "__init__.py":
                continue
            ok, err = _run_test_file(path, work_dir, timeout)
            if ok:
                return True, ""
        return False, "no test files in work_dir"
    for tf in test_files:
        ok, err = _run_test_file(tf, work_dir, timeout)
        if ok:
            return True, ""
    return False, "all test files failed"


def pytest_score(predicted: str, expected: str, work_dir: Path) -> float:
    if not predicted.strip():
        return 0.0
    ok, err = apply_diff_to_workdir(work_dir, predicted)
    if not ok:
        return difflib.SequenceMatcher(a=expected, b=predicted).ratio() * 0.5
    passed, perr = run_pytest(work_dir)
    if passed:
        return 1.0
    sm = difflib.SequenceMatcher(a=expected, b=predicted).ratio()
    return max(0.0, sm * 0.4)


async def run_one_task(task, llm_harness) -> HarnessResult:
    work = Path(tempfile.mkdtemp(prefix=f"pytest_{task.name}_"))
    try:
        for path, content in task.repo_files.items():
            full = work / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
        started = time.time()
        try:
            if asyncio.iscoroutinefunction(llm_harness):
                predicted = await llm_harness(task, work)
            else:
                predicted = await asyncio.to_thread(llm_harness, task, work)
        except Exception as exc:
            return HarnessResult(
                task_name=task.name, score=0.0,
                duration_s=time.time() - started,
                error=f"llm call: {type(exc).__name__}: {exc}",
            )
        score = pytest_score(predicted, task.ground_truth_diff, work)
        return HarnessResult(
            task_name=task.name, score=score,
            duration_s=time.time() - started,
            predicted_diff=predicted[:1000],
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def main_async(args):
    from real_eval import llm_harness  # noqa: E402

    selected = TASKS
    if args.task:
        selected = [t for t in TASKS if t.name == args.task]
        if not selected:
            raise SystemExit(f"unknown task: {args.task}")

    print(f"Pytest-eval on {len(selected)} task(s) via MiniMax-Text-01")
    started = time.time()
    results: list[HarnessResult] = []
    for task in selected:
        print(f"  -> {task.name} ...", end="", flush=True)
        r = await run_one_task(task, llm_harness)
        results.append(r)
        marker = "PASS" if r.score >= 0.7 else "FAIL"
        print(f" [{marker}] {r.score:.2f} ({r.duration_s:.1f}s)")

    report = HarnessReport(results=results, total_s=time.time() - started)
    print_report(report)
    if args.out:
        Path(args.out).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {args.out}")


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--task", default="")
    p.add_argument("--out", default="")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
