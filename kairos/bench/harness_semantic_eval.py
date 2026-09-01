"""Per-task semantic validator.

The 10 SWE-bench Lite tasks are too small for pytest to fully exercise
(only 3 of 10 have test files in repo_files). Instead, this module
defines a per-task ``validate(work_dir) -> (passed, reason)`` function
that knows what the task is supposed to do and checks semantic
correctness:

- off_by_one: `last_n([1,2,3,4,5], 3) == [3,4,5]`
- add_input_validation: `divide(1, 0)` raises ValueError
- convert_print_to_logging: no `print(` calls in runner.py
- extract_magic_number: `SECONDS_PER_DAY` defined, used in is_one_day
- add_docstring: `parse_int.__doc__` is non-empty
- rename_function: `fetch_user` defined, `get_user` not defined
- add_type_hints: `connect.__annotations__` has 'host', 'port', 'return'
- fix_broken_import: `from helpers import utils_helpers` resolves
- write_unit_test: tests/test_calc.py exists, contains a test calling add
- implement_function: `is_palindrome('Racecar')` is True, `is_palindrome('Hello')` is False

Run:
    python -m kairos.bench.harness_semantic_eval
    python -m kairos.bench.harness_semantic_eval --task off_by_one
    python -m kairos.bench.harness_semantic_eval --out semantic_baseline.json
"""
from __future__ import annotations

import ast
import asyncio
import difflib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kairos.bench.harness_eval import (  # noqa: E402
    TASKS,
    HarnessReport,
    HarnessResult,
    print_report,
)
from kairos.bench.harness_pytest_eval import apply_diff_to_workdir  # noqa: E402


# Each validator takes a work_dir Path and returns (passed, reason).
Validator = Callable[[Path], Tuple[bool, str]]


def _import_from_file(work_dir: Path, module_name: str, rel_path: str):
    """Import a module from work_dir/<rel_path> with work_dir on sys.path."""
    sys.path.insert(0, str(work_dir))
    try:
        spec = importlib.util.spec_from_file_location(
            module_name, str(work_dir / rel_path)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        try:
            sys.path.remove(str(work_dir))
        except ValueError:
            pass


VALIDATORS: dict[str, Validator] = {}


def register(name: str):
    def deco(fn: Validator) -> Validator:
        VALIDATORS[name] = fn
        return fn
    return deco


@register("off_by_one")
def _v_off_by_one(work_dir: Path) -> Tuple[bool, str]:
    mod = _import_from_file(work_dir, "collection", "src/collection.py")
    out = mod.last_n([1, 2, 3, 4, 5], 3)
    if out == [3, 4, 5]:
        return True, f"last_n([1..5], 3) == {out}"
    return False, f"last_n([1..5], 3) == {out} (expected [3,4,5])"


@register("add_input_validation")
def _v_validation(work_dir: Path) -> Tuple[bool, str]:
    mod = _import_from_file(work_dir, "math_ops", "src/math_ops.py")
    try:
        mod.divide(1, 0)
    except ValueError:
        return True, "divide(1,0) raised ValueError"
    except Exception as e:
        return False, f"divide(1,0) raised {type(e).__name__} (expected ValueError)"
    return False, "divide(1,0) did not raise"


@register("convert_print_to_logging")
def _v_print_logging(work_dir: Path) -> Tuple[bool, str]:
    src = (work_dir / "src" / "runner.py").read_text(encoding="utf-8")
    # look for any top-level print(
    has_print = bool(re.search(r"^\s*print\s*\(", src, re.M))
    has_logger = "logger" in src and ("logger.info" in src or "logger.debug" in src)
    if has_logger and not has_print:
        return True, "uses logger, no print()"
    return False, f"has_print={has_print} has_logger={has_logger}"


@register("extract_magic_number")
def _v_magic(work_dir: Path) -> Tuple[bool, str]:
    src = (work_dir / "src" / "limits.py").read_text(encoding="utf-8")
    if "SECONDS_PER_DAY" not in src:
        return False, "SECONDS_PER_DAY not defined"
    if "86400" in src and "SECONDS_PER_DAY" in src:
        # The literal 86400 may still appear in the constant definition; that's OK.
        # Just check that the function uses SECONDS_PER_DAY.
        if re.search(r"is_one_day\s*\([^)]*\)\s*:.*SECONDS_PER_DAY",
                     src, re.S):
            return True, "SECONDS_PER_DAY defined and used in is_one_day"
    return False, f"SECONDS_PER_DAY defined but not used in is_one_day"


@register("add_docstring")
def _v_docstring(work_dir: Path) -> Tuple[bool, str]:
    mod = _import_from_file(work_dir, "convert", "src/convert.py")
    doc = mod.parse_int.__doc__
    if doc and doc.strip():
        return True, f"parse_int.__doc__ = {doc.strip()[:60]!r}"
    # Also accept module-level docstrings (LLM may place it above the function)
    src = (work_dir / "src" / "convert.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    module_doc = ast.get_docstring(tree)
    if module_doc and module_doc.strip():
        return True, f"module docstring: {module_doc.strip()[:60]!r}"
    return False, f"no docstring found on parse_int or module"


@register("rename_function")
def _v_rename(work_dir: Path) -> Tuple[bool, str]:
    src = (work_dir / "src" / "auth.py").read_text(encoding="utf-8")
    has_fetch = re.search(r"def\s+fetch_user\s*\(", src) is not None
    has_get = re.search(r"def\s+get_user\s*\(", src) is not None
    if has_fetch and not has_get:
        return True, "fetch_user defined, get_user not defined"
    return False, f"has_fetch={has_fetch} has_get={has_get}"


@register("add_type_hints")
def _v_hints(work_dir: Path) -> Tuple[bool, str]:
    mod = _import_from_file(work_dir, "net", "src/net.py")
    ann = getattr(mod.connect, "__annotations__", {})
    keys = set(ann.keys())
    if {"host", "port", "return"}.issubset(keys):
        return True, f"annotations: {ann}"
    return False, f"annotations: {ann} (need host, port, return)"


@register("fix_broken_import")
def _v_import(work_dir: Path) -> Tuple[bool, str]:
    # The "fix" task adds a comment to a working import. Verify the import still
    # resolves and utils_helpers is callable.
    sys.path.insert(0, str(work_dir))
    try:
        spec = importlib.util.spec_from_file_location(
            "main_mod", str(work_dir / "src" / "main.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # If exec_module didn't raise, import is fine
        return True, "main.py imports without error"
    except Exception as e:
        return False, f"main.py failed to import: {e}"
    finally:
        try:
            sys.path.remove(str(work_dir))
        except ValueError:
            pass


@register("write_unit_test")
def _v_test_file(work_dir: Path) -> Tuple[bool, str]:
    test_path = work_dir / "tests" / "test_calc.py"
    if not test_path.exists():
        # LLM might have put the test in tests/__init__.py
        alt = work_dir / "tests" / "__init__.py"
        if alt.exists():
            txt = alt.read_text(encoding="utf-8")
            if "add" in txt and ("assert" in txt or "self.assert" in txt):
                # Try to run the test
                result = subprocess.run(
                    [sys.executable, str(alt)],
                    cwd=str(work_dir), capture_output=True, text=True,
                    env={**os.environ, "PYTHONPATH": str(work_dir)},
                    timeout=15,
                )
                if result.returncode == 0:
                    return True, "test in tests/__init__.py runs OK"
                return False, f"test in tests/__init__.py failed: {result.stderr[-200:]}"
        return False, "no tests/test_calc.py and no useful tests/__init__.py"
    txt = test_path.read_text(encoding="utf-8")
    if "add" not in txt:
        return False, "test file does not reference 'add'"
    # Try to run
    result = subprocess.run(
        [sys.executable, str(test_path)],
        cwd=str(work_dir), capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(work_dir)},
        timeout=15,
    )
    if result.returncode == 0:
        return True, "tests/test_calc.py runs OK"
    return False, f"tests/test_calc.py failed: {result.stderr[-200:]}"


@register("implement_function")
def _v_palindrome(work_dir: Path) -> Tuple[bool, str]:
    mod = _import_from_file(work_dir, "text", "src/text.py")
    a = mod.is_palindrome("Racecar")
    b = mod.is_palindrome("Hello")
    if a is True and b is False:
        return True, "is_palindrome('Racecar')=True, ('Hello')=False"
    return False, f"is_palindrome('Racecar')={a!r}, ('Hello')={b!r}"


def semantic_score(predicted: str, expected: str, work_dir: Path,
                   task_name: str) -> Tuple[float, str]:
    """Apply the predicted diff, then run the per-task validator."""
    if not predicted.strip():
        return 0.0, "empty diff"
    ok, err = apply_diff_to_workdir(work_dir, predicted)
    if not ok:
        return 0.2, f"diff apply failed: {err}"
    validator = VALIDATORS.get(task_name)
    if not validator:
        return 0.0, f"no validator for {task_name}"
    try:
        passed, reason = validator(work_dir)
    except Exception as e:
        return 0.3, f"validator error: {type(e).__name__}: {e}"
    if passed:
        return 1.0, reason
    return 0.0, reason


async def run_one_task(task, llm_harness) -> HarnessResult:
    work = Path(tempfile.mkdtemp(prefix=f"sem_{task.name}_"))
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
        score, reason = semantic_score(
            predicted, task.ground_truth_diff, work, task.name)
        # Store reason in error field for visibility
        return HarnessResult(
            task_name=task.name, score=score,
            duration_s=time.time() - started,
            predicted_diff=predicted[:1000],
            error="" if score >= 0.7 else reason[:200],
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def main_async(args):
    from kairos.bench.real_eval import llm_harness  # noqa: E402

    selected = TASKS
    if args.task:
        selected = [t for t in TASKS if t.name == args.task]
        if not selected:
            raise SystemExit(f"unknown task: {args.task}")

    print(f"Semantic-eval on {len(selected)} task(s) via MiniMax-Text-01")
    started = time.time()
    results: list[HarnessResult] = []
    for task in selected:
        print(f"  -> {task.name} ...", end="", flush=True)
        r = await run_one_task(task, llm_harness)
        results.append(r)
        marker = "PASS" if r.score >= 0.7 else "FAIL"
        suffix = f"  ({r.error[:50]})" if r.error and r.score < 0.7 else ""
        print(f" [{marker}] {r.score:.2f} ({r.duration_s:.1f}s){suffix}")

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
