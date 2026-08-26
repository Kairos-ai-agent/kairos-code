"""Canonical benchmark problem sets.

Three flavors, all small enough to ship in-tree:

  - ``HumanEvalProblem``     — function signature + docstring + tests
  - ``MBPPProblem``          — short problem statement + assert tests
  - ``CustomUnitTestProblem``— repo-relative file + test command

Each problem is self-contained: ``.prompt()`` returns the input we
send to the agent, ``.check(candidate)`` returns ``(passed, message)``.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple


@dataclass
class BenchmarkProblem:
    """Base class for a single benchmark problem.

    Subclasses should set ``prompt``, ``entry_point``, and override
    ``check``.
    """
    id: str
    name: str
    prompt: str
    entry_point: str  # the function/class name we expect the agent to define
    reference_solution: str = ""  # ground-truth code (used for unit tests / diffs)
    source: str = "custom"        # "humaneval" | "mbpp" | "custom"
    timeout_s: float = 10.0
    metadata: dict = field(default_factory=dict)

    def check(self, candidate: str) -> Tuple[bool, str]:
        """Run the candidate solution in a sandboxed subprocess.

        Default implementation: write the candidate to a temp file,
        import it, and call the entry_point with the reference tests.
        Subclasses may override.
        """
        from .sandbox_exec import run_candidate
        return run_candidate(self, candidate)


# ---------------------------------------------------------------------------
# HumanEval-style
# ---------------------------------------------------------------------------


@dataclass
class HumanEvalProblem(BenchmarkProblem):
    """A HumanEval-style problem.

    ``tests`` is a list of single-line assert statements (without
    imports) that the reference solution is expected to satisfy.
    """
    tests: List[str] = field(default_factory=list)
    source: str = "humaneval"

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt,
            "entry_point": self.entry_point,
            "tests": list(self.tests),
            "source": self.source,
            "timeout_s": self.timeout_s,
        }
        return d


# ---------------------------------------------------------------------------
# MBPP-style
# ---------------------------------------------------------------------------


@dataclass
class MBPPProblem(BenchmarkProblem):
    """A Mostly Basic Python Problems entry.

    MBPP gives a short problem statement, a reference solution, and
    a few assert tests. The prompt to the agent is the statement
    (without the reference).
    """
    tests: List[str] = field(default_factory=list)
    source: str = "mbpp"

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt,
            "entry_point": self.entry_point,
            "tests": list(self.tests),
            "reference_solution": self.reference_solution,
            "source": self.source,
            "timeout_s": self.timeout_s,
        }
        return d


# ---------------------------------------------------------------------------
# Repo-relative unit-test
# ---------------------------------------------------------------------------


@dataclass
class CustomUnitTestProblem(BenchmarkProblem):
    """A problem defined relative to a project on disk.

    The runner checks out a worktree (or just uses the project root),
    writes the candidate into the right path, and runs the
    configured test command.
    """
    repo_root: str = ""
    target_file: str = ""           # where the candidate is written
    test_command: List[str] = field(default_factory=list)  # argv list
    source: str = "custom"

    def check(self, candidate: str) -> Tuple[bool, str]:
        if not self.repo_root or not self.target_file:
            return False, "CustomUnitTestProblem needs repo_root + target_file"
        target = Path(self.repo_root) / self.target_file
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if target.exists():
            backup = target.read_text(encoding="utf-8")
        try:
            target.write_text(candidate, encoding="utf-8")
            proc = subprocess.run(
                self.test_command,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
            ok = proc.returncode == 0
            msg = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
            return ok, msg[-2000:]
        except subprocess.TimeoutExpired:
            return False, "test command timed out"
        except Exception as exc:  # noqa: BLE001
            return False, f"error: {exc}"
        finally:
            if backup is not None:
                target.write_text(backup, encoding="utf-8")


# ---------------------------------------------------------------------------
# Built-in problem sets (small, no network required)
# ---------------------------------------------------------------------------


def _h(*args) -> HumanEvalProblem:
    """Shorthand constructor."""
    id_, name, prompt, entry, tests, ref = args
    return HumanEvalProblem(
        id=id_, name=name, prompt=prompt, entry_point=entry,
        tests=tests, reference_solution=ref,
    )


def _m(*args) -> MBPPProblem:
    id_, name, prompt, entry, tests, ref = args
    return MBPPProblem(
        id=id_, name=name, prompt=prompt, entry_point=entry,
        tests=tests, reference_solution=ref,
    )


HUMANEVAL_MINI: List[BenchmarkProblem] = [
    _h(
        "he-001-has-close-elements",
        "has_close_elements",
        'from typing import List\n\n'
        'def has_close_elements(numbers: List[float], threshold: float) -> bool:\n'
        '    """ Given a list of numbers, return True if any two numbers are\n'
        '    closer than the threshold. """\n',
        "has_close_elements",
        [
            "assert has_close_elements([1.0, 2.0, 3.0], 0.5) == False",
            "assert has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3) == True",
            "assert has_close_elements([], 0.5) == False",
            "assert has_close_elements([1.0], 0.5) == False",
        ],
        "from typing import List\n\n"
        "def has_close_elements(numbers: List[float], threshold: float) -> bool:\n"
        "    for idx, a in enumerate(numbers):\n"
        "        for b in numbers[idx + 1:]:\n"
        "            if abs(a - b) < threshold:\n"
        "                return True\n"
        "    return False\n",
    ),
    _h(
        "he-002-separate-paren-groups",
        "separate_paren_groups",
        'def separate_paren_groups(paren_string: str) -> list:\n'
        '    """ Input is a string of multiple for loop iterations over a\n'
        '    list. Return a list of all the parentheses groups, balanced. """\n',
        "separate_paren_groups",
        [
            "assert separate_paren_groups(\"()()\") == [\"()\", \"()\"]",
            "assert separate_paren_groups(\"()(())\") == [\"()\", \"(())\"]",
            "assert separate_paren_groups(\"(()())\") == [\"(()())\"]",
        ],
        "def separate_paren_groups(paren_string: str) -> list:\n"
        "    result = []\n"
        "    depth = 0\n"
        "    current = []\n"
        "    for ch in paren_string:\n"
        "        if ch == \"(\":\n"
        "            depth += 1\n"
        "            current.append(ch)\n"
        "        elif ch == \")\":\n"
        "            depth -= 1\n"
        "            current.append(ch)\n"
        "            if depth == 0:\n"
        "                result.append(\"\".join(current))\n"
        "                current = []\n"
        "    return result\n",
    ),
    _h(
        "he-003-truncate-number",
        "truncate_number",
        'def truncate_number(number: float) -> float:\n'
        '    """ Given a positive floating point number, return its decimal part. ""\"\n',
        "truncate_number",
        [
            "assert truncate_number(3.5) == 0.5",
            "assert truncate_number(1.25) == 0.25",
            "assert truncate_number(0.0) == 0.0",
            "assert truncate_number(100.0) == 0.0",
        ],
        "def truncate_number(number: float) -> float:\n"
        "    return number - int(number)\n",
    ),
    _h(
        "he-004-below-zero",
        "below_zero",
        'from typing import List\n\n'
        'def below_zero(operations: List[int]) -> bool:\n'
        '    """ You\'re given a list of operations. Return True if the\n'
        '    running sum ever goes strictly below zero. """\n',
        "below_zero",
        [
            "assert below_zero([1, 2, 3]) == False",
            "assert below_zero([1, -2, 2]) == True",
            "assert below_zero([-1, -2, 3]) == True",
            "assert below_zero([]) == False",
        ],
        "from typing import List\n"
        "def below_zero(operations: List[int]) -> bool:\n"
        "    total = 0\n"
        "    for op in operations:\n"
        "        total += op\n"
        "        if total < 0:\n"
        "            return True\n"
        "    return False\n",
    ),
    _h(
        "he-005-mean-absolute-deviation",
        "mean_absolute_deviation",
        'from typing import List\n\n'
        'def mean_absolute_deviation(numbers: List[float]) -> float:\n'
        '    """ Mean absolute deviation of a list of numbers. """\n',
        "mean_absolute_deviation",
        [
            "assert abs(mean_absolute_deviation([1.0, 2.0]) - 0.5) < 1e-6",
            "assert abs(mean_absolute_deviation([1.0, 2.0, 3.0, 4.0]) - 1.0) < 1e-6",
            "assert abs(mean_absolute_deviation([5.0]) - 0.0) < 1e-6",
        ],
        "from typing import List\n"
        "def mean_absolute_deviation(numbers: List[float]) -> float:\n"
        "    if not numbers:\n"
        "        return 0.0\n"
        "    mean = sum(numbers) / len(numbers)\n"
        "    return sum(abs(n - mean) for n in numbers) / len(numbers)\n",
    ),
]


MBPP_MINI: List[BenchmarkProblem] = [
    _m(
        "mbpp-001-sum-digits",
        "sum_digits",
        'Write a Python function named `sum_digits(n)` that sums the digits of a non-negative integer.',
        "sum_digits",
        ["assert sum_digits(123) == 6", "assert sum_digits(0) == 0", "assert sum_digits(1000) == 1"],
        "def sum_digits(n):\n    return sum(int(d) for d in str(n))\n",
    ),
    _m(
        "mbpp-002-is-prime",
        "is_prime",
        'Write a Python function named `is_prime(n)` that returns True if a positive integer is prime, False otherwise.',
        "is_prime",
        [
            "assert is_prime(2) == True",
            "assert is_prime(7) == True",
            "assert is_prime(9) == False",
            "assert is_prime(1) == False",
        ],
        "def is_prime(n):\n    if n < 2: return False\n    for i in range(2, int(n**0.5) + 1):\n        if n % i == 0: return False\n    return True\n",
    ),
    _m(
        "mbpp-003-reverse-string",
        "reverse_string",
        'Write a Python function named `reverse_string(s)` that reverses a string.',
        "reverse_string",
        [
            "assert reverse_string('hello') == 'olleh'",
            "assert reverse_string('') == ''",
            "assert reverse_string('a') == 'a'",
        ],
        "def reverse_string(s):\n    return s[::-1]\n",
    ),
    _m(
        "mbpp-004-factorial",
        "factorial",
        'Write a Python function named `factorial(n)` that computes the factorial of a non-negative integer.',
        "factorial",
        [
            "assert factorial(0) == 1",
            "assert factorial(1) == 1",
            "assert factorial(5) == 120",
            "assert factorial(7) == 5040",
        ],
        "def factorial(n):\n    r = 1\n    for i in range(2, n + 1): r *= i\n    return r\n",
    ),
    _m(
        "mbpp-005-count-vowels",
        "count_vowels",
        'Write a Python function named `count_vowels(s)` that counts the vowels in a string (a, e, i, o, u, case-insensitive).',
        "count_vowels",
        [
            "assert count_vowels('hello') == 2",
            "assert count_vowels('xyz') == 0",
            "assert count_vowels('AEIOU') == 5",
            "assert count_vowels('') == 0",
        ],
        "def count_vowels(s):\n    return sum(1 for c in s.lower() if c in 'aeiou')\n",
    ),
]


def humaneval_mini() -> List[BenchmarkProblem]:
    """Return a copy of the in-tree HumanEval subset."""
    return list(HUMANEVAL_MINI)


def mbpp_mini() -> List[BenchmarkProblem]:
    return list(MBPP_MINI)


def default_problem_set() -> List[BenchmarkProblem]:
    """Combined default set: HumanEval-mini + MBPP-mini (10 problems)."""
    return humaneval_mini() + mbpp_mini()
