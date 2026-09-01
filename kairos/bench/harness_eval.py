"""SWE-bench Lite harness baseline (R38.6.4).

10 hand-crafted coding tasks to measure the harness.
See harness_eval_impl.py for the actual task list and runner.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class HarnessTask:
    name: str
    prompt: str
    repo_files: Dict[str, str]
    ground_truth_diff: str
    reward_fn: Optional[Callable[[str, str], float]] = None

    def score(self, predicted_diff: str) -> float:
        if self.reward_fn:
            try:
                return float(self.reward_fn(predicted_diff,
                                              self.ground_truth_diff))
            except Exception:
                pass
        return difflib.SequenceMatcher(
            a=self.ground_truth_diff, b=predicted_diff).ratio()


def _ratio(predicted, expected):
    return difflib.SequenceMatcher(a=expected, b=predicted).ratio()


TASKS: List[HarnessTask] = [
    HarnessTask(
        name="off_by_one",
        prompt=("Fix the off-by-one in the function `last_n(items, n)` "
                 "in src/collection.py. It currently returns at most n-1 items."),
        repo_files={"src/collection.py": "def last_n(items, n):\n    return items[:n-1]\n",
                     "tests/test_collection.py":
                     "from src.collection import last_n\n"
                     "assert last_n([1,2,3,4,5], 3) == [3,4,5]\n"},
        ground_truth_diff=(
            "--- a/src/collection.py\n"
            "+++ b/src/collection.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def last_n(items, n):\n"
            "-    return items[:n-1]\n"
            "+    return items[-n:]\n"),
        reward_fn=_ratio),
    HarnessTask(
        name="add_input_validation",
        prompt=("Add input validation to `divide(a, b)` in src/math_ops.py. "
                 "It should raise ValueError when b is 0."),
        repo_files={"src/math_ops.py": "def divide(a, b):\n    return a / b\n",
                     "tests/test_math_ops.py":
                     "import pytest\nfrom src.math_ops import divide\n"
                     "def test_zero_raises():\n"
                     "    with pytest.raises(ValueError):\n"
                     "        divide(1, 0)\n"},
        ground_truth_diff=(
            "--- a/src/math_ops.py\n"
            "+++ b/src/math_ops.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def divide(a, b):\n"
            "+    if b == 0:\n"
            "+        raise ValueError('b must not be 0')\n"
            "     return a / b\n"),
        reward_fn=_ratio),
    HarnessTask(
        name="convert_print_to_logging",
        prompt=("Replace `print(...)` calls in src/runner.py with "
                 "`logger.info(...)` using the standard logging module."),
        repo_files={"src/runner.py": "def run():\n    print('starting')\n    print('done')\n"},
        ground_truth_diff=(
            "--- a/src/runner.py\n"
            "+++ b/src/runner.py\n"
            "@@ -1,4 +1,6 @@\n"
            "+import logging\n"
            "+logger = logging.getLogger(__name__)\n"
            "+\n"
            " def run():\n"
            "-    print('starting')\n"
            "-    print('done')\n"
            "+    logger.info('starting')\n"
            "+    logger.info('done')\n"),
        reward_fn=_ratio),
    HarnessTask(
        name="extract_magic_number",
        prompt=("Replace the magic number `86400` in src/limits.py with a "
                 "named constant `SECONDS_PER_DAY`."),
        repo_files={"src/limits.py": "def is_one_day(seconds):\n    return seconds >= 86400\n"},
        ground_truth_diff=(
            "--- a/src/limits.py\n"
            "+++ b/src/limits.py\n"
            "@@ -1,2 +1,3 @@\n"
            "+SECONDS_PER_DAY = 86400\n"
            "+\n"
            " def is_one_day(seconds):\n"
            "-    return seconds >= 86400\n"
            "+    return seconds >= SECONDS_PER_DAY\n"),
        reward_fn=_ratio),
    HarnessTask(
        name="add_docstring",
        prompt=("Add a docstring to `parse_int(s)` in src/convert.py."),
        repo_files={"src/convert.py": "def parse_int(s):\n    return int(s)\n"},
        ground_truth_diff=(
            "--- a/src/convert.py\n"
            "+++ b/src/convert.py\n"
            "@@ -1,2 +1,5 @@\n"
            " def parse_int(s):\n"
            "+    \"\"\"Parse ``s`` as an integer.\"\"\"\n"
            "     return int(s)\n"),
        reward_fn=_ratio),
    HarnessTask(
        name="rename_function",
        prompt=("Rename `get_user` to `fetch_user` everywhere in src/auth.py."),
        repo_files={"src/auth.py":
                     "def get_user(id):\n    return {'id': id, 'name': 'u'}\n"
                     "user = get_user(42)\n"},
        ground_truth_diff=(
            "--- a/src/auth.py\n"
            "+++ b/src/auth.py\n"
            "@@ -1,4 +1,4 @@\n"
            "-def get_user(id):\n"
            "+def fetch_user(id):\n"
            "     return {'id': id, 'name': 'u'}\n"
            "-user = get_user(42)\n"
            "+user = fetch_user(42)\n"),
        reward_fn=_ratio),
    HarnessTask(
        name="add_type_hints",
        prompt=("Add Python type hints to `connect(host, port)` in src/net.py."),
        repo_files={"src/net.py":
                     "def connect(host, port):\n    return f'{host}:{port}'\n"},
        ground_truth_diff=(
            "--- a/src/net.py\n"
            "+++ b/src/net.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-def connect(host, port):\n"
            "+def connect(host: str, port: int) -> str:\n"
            "     return f'{host}:{port}'\n"),
        reward_fn=_ratio),
    HarnessTask(
        name="fix_broken_import",
        prompt=("Fix the broken import in src/main.py."),
        repo_files={"src/main.py": "from helpers import utils_helpers\n",
                     "helpers.py": "def utils_helpers(): pass\n"},
        ground_truth_diff=(
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-from helpers import utils_helpers\n"
            "+from helpers import utils_helpers  # fixed path\n"),
        reward_fn=_ratio),
    HarnessTask(
        name="write_unit_test",
        prompt=("Write a unit test for `add(a, b)` in src/calc.py."),
        repo_files={"src/calc.py": "def add(a, b):\n    return a + b\n",
                     "tests/__init__.py": ""},
        ground_truth_diff=(
            "--- /dev/null\n"
            "+++ b/tests/test_calc.py\n"
            "@@ -0,0 +1,4 @@\n"
            "+from src.calc import add\n"
            "+\n"
            "+def test_add():\n"
            "+    assert add(1, 2) == 3\n"),
        reward_fn=_ratio),
    HarnessTask(
        name="implement_function",
        prompt=("Implement `is_palindrome(s)` in src/text.py ignoring case."),
        repo_files={"src/text.py": "def is_palindrome(s):\n    pass\n",
                     "tests/test_text.py":
                     "from src.text import is_palindrome\n"
                     "assert is_palindrome('Racecar')\n"
                     "assert not is_palindrome('Hello')\n"},
        ground_truth_diff=(
            "--- a/src/text.py\n"
            "+++ b/src/text.py\n"
            "@@ -1,2 +1,4 @@\n"
            " def is_palindrome(s):\n"
            "-    pass\n"
            "+    s = s.lower()\n"
            "+    return s == s[::-1]\n"),
        reward_fn=_ratio),
]


@dataclass
class HarnessResult:
    task_name: str
    score: float
    duration_s: float
    predicted_diff: str = ""
    error: str = ""


@dataclass
class HarnessReport:
    results: List[HarnessResult] = field(default_factory=list)
    total_s: float = 0.0

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        passed = sum(1 for r in self.results if r.score >= 0.7)
        return passed / len(self.results)

    @property
    def mean_score(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.score for r in self.results) / len(self.results)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_s": round(self.total_s, 2),
            "mean_score": round(self.mean_score, 3),
            "pass_rate": round(self.pass_rate, 3),
            "tasks": [{"task": r.task_name,
                       "score": round(r.score, 3),
                       "duration_s": round(r.duration_s, 2),
                       "error": r.error}
                      for r in self.results],
        }


async def run_task(task, harness_run):
    work = Path(tempfile.mkdtemp(prefix=f"harness_{task.name}_"))
    try:
        for path, content in task.repo_files.items():
            full = work / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
        started = time.time()
        try:
            if asyncio.iscoroutinefunction(harness_run):
                predicted = await harness_run(task, work)
            else:
                predicted = await asyncio.to_thread(
                    harness_run, task, work)
        except Exception as exc:
            return HarnessResult(task_name=task.name, score=0.0,
                                 duration_s=time.time() - started,
                                 error=f"{type(exc).__name__}: {exc}")
        return HarnessResult(task_name=task.name,
                             score=task.score(predicted),
                             duration_s=time.time() - started,
                             predicted_diff=predicted[:1000])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def print_report(report):
    print(f"\n{'='*60}")
    print(f"  Harness eval: {report.pass_rate:.0%} pass, "
          f"mean {report.mean_score:.2f}, {report.total_s:.1f}s")
    print(f"{'='*60}")
    for r in report.results:
        marker = "PASS" if r.score >= 0.7 else "FAIL"
        err = f"  ({r.error[:50]})" if r.error else ""
        print(f"  [{marker}] {r.task_name:<28} {r.score:.2f} "
              f"({r.duration_s:.1f}s){err}")
    print(f"{'='*60}\n")


def _identity_harness(task, work_dir):
    return ""


async def run_full_eval(tasks=None, harness=None):
    tasks = tasks or TASKS
    harness = harness or _identity_harness
    started = time.time()
    results = []
    for task in tasks:
        r = await run_task(task, harness)
        results.append(r)
    return HarnessReport(results=results, total_s=time.time() - started)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="")
    p.add_argument("--task", default="")
    args = p.parse_args()
    if args.task:
        single = next((t for t in TASKS if t.name == args.task), None)
        if single is None:
            raise SystemExit(f"unknown task: {args.task}")
        TASKS_USE = [single]
    else:
        TASKS_USE = TASKS
    rep = asyncio.run(run_full_eval(TASKS_USE))
    print_report(rep)
    if args.out:
        Path(args.out).write_text(
            json.dumps(rep.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"wrote report to {args.out}")
