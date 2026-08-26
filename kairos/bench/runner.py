"""Benchmark runner.

Drives an agent over a set of problems and collects pass@k metrics.

The agent must expose ``generate(prompt: str) -> str`` (sync) or
``agenerate(prompt: str) -> Awaitable[str]`` (async). Both work.

Pass@k is computed for the requested k; if the runner is asked
for k>1, it samples N=k candidates per problem and counts how many
of those N passed — a problem is "passed" if any of the N is
correct. The ``run_one`` method returns a single attempt's result
so the caller can do their own metric.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .problems import BenchmarkProblem
from .sandbox_exec import run_candidate

logger = logging.getLogger(__name__)


@dataclass
class ProblemResult:
    problem_id: str
    name: str
    passed: bool
    attempts: int
    details: List[Tuple[bool, str]] = field(default_factory=list)
    duration_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "problem_id": self.problem_id,
            "name": self.name,
            "passed": self.passed,
            "attempts": self.attempts,
            "duration_s": round(self.duration_s, 3),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "error": self.error,
            "details": [
                {"passed": ok, "message": msg[:200]} for ok, msg in self.details
            ],
        }


@dataclass
class BenchmarkResult:
    started_at: float
    finished_at: float
    k: int
    agent_label: str
    results: List[ProblemResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def pass_at_1(self) -> float:
        """Pass@1 = fraction of problems where the first attempt passed."""
        if not self.results:
            return 0.0
        # For k=1, this is just the count of passed.
        if self.k == 1:
            return self.passed / self.total
        # For k>1, pass@1 still uses only the first attempt of each.
        first_attempt_passed = sum(
            1 for r in self.results if r.details and r.details[0][0]
        )
        return first_attempt_passed / self.total if self.total else 0.0

    @property
    def pass_at_k(self) -> float:
        """Pass@k = fraction of problems with at least one passing attempt."""
        if not self.total:
            return 0.0
        return self.passed / self.total

    def summary(self) -> Dict[str, Any]:
        return {
            "agent": self.agent_label,
            "k": self.k,
            "total": self.total,
            "passed": self.passed,
            "pass_at_1": round(self.pass_at_1, 4),
            "pass_at_k": round(self.pass_at_k, 4),
            "duration_s": round(self.finished_at - self.started_at, 2),
            "total_prompt_tokens": sum(r.prompt_tokens for r in self.results),
            "total_completion_tokens": sum(r.completion_tokens for r in self.results),
        }

    def to_dict(self) -> dict:
        return {
            **self.summary(),
            "results": [r.to_dict() for r in self.results],
        }


class BenchmarkRunner:
    """Drives an agent over benchmark problems."""

    def __init__(self, agent: Any, *, label: str = "agent",
                 max_concurrency: int = 1):
        self.agent = agent
        self.label = label
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def _generate(self, prompt: str) -> str:
        if hasattr(self.agent, "agenerate"):
            return await self.agent.agenerate(prompt)
        gen = self.agent.generate(prompt)
        if hasattr(gen, "__await__"):
            return await gen
        return gen

    def _track_tokens(self, prompt: str, completion: str) -> Tuple[int, int]:
        """Best-effort token counts.

        If the agent exposes ``count_tokens(text)`` we use it.
        Otherwise we estimate (chars / 4) which is good enough for
        per-problem accounting.
        """
        c = getattr(self.agent, "count_tokens", None)
        if callable(c):
            try:
                return int(c(prompt)), int(c(completion))
            except Exception:
                pass
        return max(1, len(prompt) // 4), max(1, len(completion) // 4)

    async def _run_one_attempt(self, problem: BenchmarkProblem) -> Tuple[bool, str, int, int]:
        prompt = problem.prompt
        try:
            async with self.semaphore:
                candidate = await self._generate(prompt)
        except Exception as exc:  # noqa: BLE001
            return False, f"agent error: {exc}", 0, 0
        p_tokens, c_tokens = self._track_tokens(prompt, candidate)
        try:
            ok, msg = run_candidate(problem, candidate)
        except Exception as exc:  # noqa: BLE001
            return False, f"sandbox error: {exc}", p_tokens, c_tokens
        return ok, msg, p_tokens, c_tokens

    async def _run_problem(self, problem: BenchmarkProblem, k: int) -> ProblemResult:
        start = time.perf_counter()
        attempts: List[Tuple[bool, str]] = []
        total_p = 0
        total_c = 0
        last_error = ""
        for i in range(k):
            ok, msg, p_tok, c_tok = await self._run_one_attempt(problem)
            attempts.append((ok, msg))
            total_p += p_tok
            total_c += c_tok
            if not ok and not last_error:
                last_error = msg
        passed = any(ok for ok, _ in attempts)
        return ProblemResult(
            problem_id=problem.id,
            name=problem.name,
            passed=passed,
            attempts=k,
            details=attempts,
            duration_s=time.perf_counter() - start,
            prompt_tokens=total_p,
            completion_tokens=total_c,
            error="" if passed else last_error,
        )

    async def run_async(
        self,
        problems: Sequence[BenchmarkProblem],
        k: int = 1,
    ) -> BenchmarkResult:
        if k < 1:
            raise ValueError("k must be >= 1")
        started = time.time()
        results: List[ProblemResult] = []
        # Sequential by default to keep token accounting deterministic.
        # Concurrency is still available via the semaphore if desired.
        for problem in problems:
            r = await self._run_problem(problem, k)
            results.append(r)
            logger.info("benchmark %s: %s %s", self.label, problem.id,
                        "PASS" if r.passed else "FAIL")
        return BenchmarkResult(
            started_at=started,
            finished_at=time.time(),
            k=k,
            agent_label=self.label,
            results=results,
        )

    def run(
        self,
        problems: Sequence[BenchmarkProblem],
        k: int = 1,
    ) -> BenchmarkResult:
        """Sync wrapper around :meth:`run_async`."""
        return asyncio.run(self.run_async(problems, k))
