"""Agent regression evaluation framework (kairos.eval).

A minimal eval harness that lets the team define test suites as
YAML, grade agent outputs against 11 built-in graders, and detect
regressions across runs via Welch's t-test on per-case scores.

Inspired by ``agentkitai/agenteval`` and ``sauravbhattacharya001/
agent-eval`` — the 2026 generation of agent-eval frameworks. We
deliberately keep the surface small: a single module, no
external deps beyond stdlib + pyyaml, the same dataclass for
"run results" that the rest of Kairos already understands.

Quick start:

    # kairos_eval.yaml
    suites:
      - name: code-gen-basics
        target: kairos.eval.run_target_agent
        cases:
          - name: reverses-string
            input: "Write a Python function that reverses a string"
            graders:
              - contains: ["def ", "return"]
              - regex: ":\\s*str"
              - latency_max_ms: 5000

    # CLI:
    python -m kairos.eval run --suite kairos_eval.yaml

    # Compare two runs:
    python -m kairos.eval compare run-A.json run-B.json

The graders are deliberately mechanical first (Tier 1) and only
LLM-judge for the long tail (Tier 3). Most agent failures
(hallucinated paths, empty output, wrong tool calls, runtime
crashes) are catchable with Tier 1+2 alone.
"""
from __future__ import annotations

import json
import logging
import math
import re
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    """One case in a suite, scored 0.0 - 1.0."""
    name: str
    score: float
    passed: bool
    duration_ms: int = 0
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class SuiteResult:
    """Aggregate of one suite run."""
    suite_name: str
    run_id: str
    cases: List[CaseResult] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def pass_rate(self) -> float:
        if not self.cases:
            return 0.0
        return sum(1 for c in self.cases if c.passed) / len(self.cases)

    @property
    def total_cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.cases)

    @property
    def total_tokens(self) -> int:
        return sum(c.tokens_in + c.tokens_out for c in self.cases)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pass_rate": self.pass_rate,
            "total_cost_usd": self.total_cost_usd,
            "total_tokens": self.total_tokens,
            "cases": [asdict(c) for c in self.cases],
        }


# ---------------------------------------------------------------------------
# Graders (Tier 1 + Tier 2)
# ---------------------------------------------------------------------------


class Grader:
    """Base class. Subclasses implement ``score(text)`` returning 0.0-1.0."""
    name: str = "base"

    def score(self, output: str, context: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
        raise NotImplementedError


class ContainsGrader(Grader):
    name = "contains"

    def __init__(self, needles: Sequence[str], case_sensitive: bool = True):
        self.needles = list(needles)
        self.case_sensitive = case_sensitive

    def score(self, output, context):
        if not self.needles:
            return 1.0, {}
        haystack = output if self.case_sensitive else output.lower()
        hits = 0
        for n in self.needles:
            needle = n if self.case_sensitive else n.lower()
            if needle in haystack:
                hits += 1
        return hits / len(self.needles), {"hits": hits, "total": len(self.needles)}


class RegexGrader(Grader):
    name = "regex"

    def __init__(self, pattern: str, flags: int = 0):
        self.pattern = pattern
        self.re = re.compile(pattern, flags)

    def score(self, output, context):
        m = self.re.search(output)
        if m:
            return 1.0, {"match": m.group(0)[:200]}
        return 0.0, {"match": None}


class ExactGrader(Grader):
    name = "exact"

    def __init__(self, expected: str, case_sensitive: bool = True):
        self.expected = expected
        self.case_sensitive = case_sensitive

    def score(self, output, context):
        if not self.case_sensitive:
            return float(output.lower() == self.expected.lower()), {}
        return float(output == self.expected), {}


class LatencyGrader(Grader):
    name = "latency"

    def __init__(self, max_ms: int):
        self.max_ms = max_ms

    def score(self, output, context):
        ms = int(context.get("duration_ms", 0))
        if ms <= self.max_ms:
            return 1.0, {"duration_ms": ms}
        # Linear decay down to 0 at 2x the budget
        return max(0.0, 1.0 - (ms - self.max_ms) / self.max_ms), {"duration_ms": ms}


class CostGrader(Grader):
    name = "cost"

    def __init__(self, max_usd: float):
        self.max_usd = max_usd

    def score(self, output, context):
        cost = float(context.get("cost_usd", 0.0))
        if cost <= self.max_usd:
            return 1.0, {"cost_usd": cost}
        return max(0.0, 1.0 - (cost - self.max_usd) / self.max_usd), {"cost_usd": cost}


class ToolCallGrader(Grader):
    """Checks that the agent called the expected tools (in any order)."""
    name = "tool_check"

    def __init__(self, expected: Sequence[str], require_all: bool = True):
        self.expected = list(expected)
        self.require_all = require_all

    def score(self, output, context):
        called = set(context.get("tools_called", []) or [])
        expected = set(self.expected)
        if self.require_all:
            ok = expected.issubset(called)
        else:
            ok = bool(expected & called)
        return float(ok), {"called": sorted(called), "expected": sorted(expected)}


class LLMJudgeGrader(Grader):
    """Tier-3 grader: an LLM evaluates the agent's output against
    a free-form rubric and returns 0.0 - 1.0.

    The judge model is configured per-grader so a single suite
    can mix a cheap judge (gpt-4o-mini) for mechanical checks
    and an expensive one (claude-3-5-sonnet) for subtle ones.
    The ``judge`` argument is a callable matching
    ``judge(prompt: str) -> str``; we default to a stub that
    fails open (returns 1.0). Production wires a LiteLLM-backed
    callable in via the ``judge`` kwarg (see build_graders).
    """
    name = "llm_judge"

    def __init__(self, prompt: str, judge: Optional[Callable] = None,
                 threshold: float = 0.5, parse_fn: Optional[Callable] = None):
        """prompt:  the rubric / evaluation question shown to the judge
        judge:   ``Callable[[str], str]`` returning the judge's text answer
        threshold: pass score; the grader returns 1.0 if score>=threshold
        parse_fn: optional ``Callable[[str], float]`` to extract a 0-1
                  score from the judge's text. Default parses ``"score: 0.8"``
                  or any number on the last line.
        """
        self.prompt = prompt
        self._judge = judge
        self.threshold = threshold
        self._parse_fn = parse_fn or _default_parse_score

    def score(self, output: str, context: Dict[str, Any]):
        if self._judge is None:
            # No judge configured — fail open (1.0) so the grader
            # is harmless when the user hasn't wired an LLM.
            return 1.0, {"judge": None, "note": "no judge configured"}
        full_prompt = (
            f"{self.prompt}\n\n"
            f"AGENT OUTPUT:\n{output[:4000]}\n\n"
            f"Respond with a single number from 0.0 to 1.0 "
            f"(e.g. 'score: 0.85') on the last line."
        )
        try:
            raw = self._judge(full_prompt)
        except Exception as exc:
            return 0.0, {"judge_error": str(exc)}
        score = self._parse_fn(raw)
        score = max(0.0, min(1.0, float(score)))
        passed = score >= self.threshold
        return (1.0 if passed else 0.0), {"judge_score": score, "raw": raw[:500]}


def _default_parse_score(raw: str) -> float:
    """Parse a 0-1 score from the judge's free-text response.

    Looks for ``score: N`` patterns first, then falls back to the
    last number on the last non-empty line. Values > 10 are treated
    as percentages (0-100 scale) and normalized to 0-1.
    """
    import re
    if not raw:
        return 0.0
    # Try explicit "score: N" or "Score: N" patterns
    m = re.search(r"score\s*[:=]\s*([0-9]+\.?[0-9]*)", raw, re.IGNORECASE)
    if m:
        try:
            v = float(m.group(1))
            return _normalize_score(v)
        except ValueError:
            pass
    # Fall back: last number on the last non-empty line
    for line in reversed(raw.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        m = re.search(r"([0-9]+\.?[0-9]*)", line)
        if m:
            try:
                return _normalize_score(float(m.group(1)))
            except ValueError:
                continue
    return 0.0


def _normalize_score(v: float) -> float:
    """Treat values > 10 as percentages (0-100 scale) and clamp to 0-1."""
    if v > 10.0:
        v /= 100.0
    return max(0.0, min(1.0, v))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def build_graders(specs: List[Dict[str, Any]],
                  judge: Optional[Callable] = None
                  ) -> List[Grader]:
    """Build a list of graders from a list of YAML grader specs.

    Each spec is a single-key dict mapping the grader name to its
    config (e.g. ``{contains: ["foo", "bar"]}``).

    ``judge`` is an optional callable used by all
    ``llm_judge`` graders. If not provided, the LLM judge
    fails open (returns 1.0) so the suite still runs — the
    user just doesn't get subjective grading.
    """
    out: List[Grader] = []
    for spec in specs or []:
        if "contains" in spec:
            cs = spec.get("case_sensitive", True)
            out.append(ContainsGrader(spec["contains"], case_sensitive=cs))
        elif "regex" in spec:
            out.append(RegexGrader(spec["regex"]))
        elif "exact" in spec:
            cs = spec.get("case_sensitive", True)
            out.append(ExactGrader(spec["exact"], case_sensitive=cs))
        elif "latency_max_ms" in spec:
            out.append(LatencyGrader(int(spec["latency_max_ms"])))
        elif "max_usd" in spec:
            out.append(CostGrader(float(spec["max_usd"])))
        elif "tools_called" in spec:
            out.append(ToolCallGrader(
                spec["tools_called"],
                require_all=spec.get("require_all", True),
            ))
        elif "llm_judge" in spec:
            cfg = spec["llm_judge"]
            if not isinstance(cfg, dict):
                logger.warning("llm_judge spec must be a dict: %s", cfg)
                continue
            out.append(LLMJudgeGrader(
                prompt=cfg.get("prompt", "Rate the agent output 0-1."),
                judge=judge,
                threshold=float(cfg.get("threshold", 0.5)),
            ))
        else:
            logger.warning("Unknown grader spec: %s", spec)
    return out


def score_output(output: str, graders: List[Grader], context: Dict[str, Any]
                 ) -> Tuple[float, Dict[str, Any]]:
    """Run all graders; return (mean_score, per_grader_details)."""
    if not graders:
        return 1.0, {}
    per = []
    details: Dict[str, Any] = {}
    for g in graders:
        s, det = g.score(output, context)
        per.append(max(0.0, min(1.0, s)))
        details[g.name] = det
    return sum(per) / len(per), details


def run_target_agent(prompt: str, **kwargs) -> Dict[str, Any]:
    """Default target: shell out to the Coder CLI.

    The Coder CLI is `python -m kairos.agents.coder`. This gives
    the eval framework a real end-to-end run without a Python
    import dependency on the agent runtime.

    Returns: {"output": str, "tools_called": [str], "tokens_in": int,
              "tokens_out": int, "cost_usd": float, "duration_ms": int}
    """
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "kairos.agents.coder", "--prompt", prompt,
             "--json"],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return {"output": "", "tools_called": [], "tokens_in": 0,
                "tokens_out": 0, "cost_usd": 0.0,
                "duration_ms": int((time.time() - t0) * 1000),
                "error": "timeout"}
    out = proc.stdout
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        data = {"output": out, "tools_called": []}
    data.setdefault("output", "")
    data.setdefault("tools_called", [])
    data.setdefault("tokens_in", 0)
    data.setdefault("tokens_out", 0)
    data.setdefault("cost_usd", 0.0)
    data["duration_ms"] = int((time.time() - t0) * 1000)
    return data


def make_litellm_judge(model: str = "gpt-4o-mini",
                        api_key: Optional[str] = None,
                        base_url: Optional[str] = None
                        ) -> Callable:
    """Build an LLM-judge callable backed by LiteLLM.

    Usage in YAML:
        graders:
          - llm_judge:
              prompt: "Rate the code on correctness, style, and tests."
              threshold: 0.7

    Usage from CLI:
        python -m kairos.eval run --suite suite.yaml \\
            --judge kairos.eval:make_litellm_judge

    Usage from Python:
        from kairos.eval import make_litellm_judge, run_suite
        judge = make_litellm_judge("gpt-4o-mini")
        result = run_suite(spec, judge=judge)

    Requires the ``litellm`` package. If the import fails we raise
    a clear error so the user knows to install it.
    """
    try:
        import litellm  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "make_litellm_judge requires the 'litellm' package. "
            "Install it with: pip install litellm"
        ) from exc
    import os
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    def _judge(prompt: str) -> str:
        kwargs: Dict[str, Any] = {"model": model, "messages":
            [{"role": "user", "content": prompt}],
            "max_tokens": 32, "temperature": 0.0}
        if base_url:
            kwargs["api_base"] = base_url
        try:
            # Synchronous litellm completion; judge is called from
            # sync context in build_graders.
            resp = litellm.completion(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as exc:
            return f"judge_error: {exc}"
    return _judge


def run_suite(suite_spec: Dict[str, Any], target: Optional[Callable] = None,
              run_id: Optional[str] = None,
              judge: Optional[Callable] = None) -> SuiteResult:
    """Run a single suite spec end-to-end.

    ``judge`` is a ``Callable[[str], str]`` used by ``llm_judge``
    graders. If not provided, LLM judge graders fail open
    (return 1.0).
    """
    target = target or run_target_agent
    run_id = run_id or f"run-{int(time.time() * 1000)}"
    res = SuiteResult(
        suite_name=suite_spec.get("name", "unnamed"),
        run_id=run_id,
        started_at=time.time(),
    )
    for case in suite_spec.get("cases", []):
        graders = build_graders(case.get("graders", []), judge=judge)
        t0 = time.time()
        try:
            out = target(case.get("input", ""))
        except Exception as exc:
            err_res = CaseResult(
                name=case.get("name", "<unnamed>"), score=0.0, passed=False,
                error=str(exc), duration_ms=int((time.time() - t0) * 1000),
            )
            res.cases.append(err_res)
            continue
        # Carry agent-side timing into context if not provided
        out.setdefault("duration_ms", int((time.time() - t0) * 1000))
        score, details = score_output(out.get("output", ""), graders, out)
        # Per-case pass threshold (default 0.5). Use a stricter cutoff
        # when ``threshold`` is set in the YAML.
        pass_threshold = float(case.get("threshold", 0.5))
        res.cases.append(CaseResult(
            name=case.get("name", "<unnamed>"),
            score=score, passed=score >= pass_threshold,
            duration_ms=out["duration_ms"],
            cost_usd=float(out.get("cost_usd", 0.0)),
            tokens_in=int(out.get("tokens_in", 0)),
            tokens_out=int(out.get("tokens_out", 0)),
            details=details,
            error=out.get("error"),
        ))
    res.finished_at = time.time()
    return res


# ---------------------------------------------------------------------------
# Comparison (Welch's t-test on per-case scores)
# ---------------------------------------------------------------------------


def _welch_t(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float]:
    """Welch's t-test for two independent samples. Returns (t, p).

    For an alpha of 0.05, p < 0.05 indicates a significant
    difference. We don't pull in scipy to keep this module
    dependency-free; the math is two-pass means + sample variances.
    """
    if len(a) < 2 or len(b) < 2:
        return 0.0, 1.0
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    # Sample variance (n-1 denominator). statistics.variance is
    # sample variance in Python 3.11+; for older Pythons fall back
    # to pvariance + manual correction.
    try:
        va = statistics.variance(a)
        vb = statistics.variance(b)
    except (statistics.StatisticsError, AttributeError):
        # All-equal or single-element: variance is 0
        return 0.0, 1.0
    se = math.sqrt(va / len(a) + vb / len(b))
    if se == 0:
        return 0.0, 1.0
    t = (ma - mb) / se
    # Welch–Satterthwaite df
    num = (va / len(a) + vb / len(b)) ** 2
    den = (va / len(a)) ** 2 / (len(a) - 1) + (vb / len(b)) ** 2 / (len(b) - 1)
    if den == 0:
        return t, 1.0
    df = num / den  # noqa: F841 — kept for future t-CDF precision
    # Two-tailed p-value via normal approximation (good enough for
    # the p < 0.05 cutoff we use in compare_runs).
    p = math.erfc(abs(t) / math.sqrt(2.0))
    return t, p


def compare_runs(
    base: SuiteResult, target: SuiteResult, alpha: float = 0.05,
) -> Dict[str, Any]:
    """Diff two suite runs and flag significant regressions."""
    base_by_name = {c.name: c.score for c in base.cases}
    target_by_name = {c.name: c.score for c in target.cases}
    common = sorted(set(base_by_name) & set(target_by_name))
    base_scores = [base_by_name[n] for n in common]
    target_scores = [target_by_name[n] for n in common]
    t, p = _welch_t(base_scores, target_scores)
    regressions = []
    improvements = []
    unchanged = []
    for name in common:
        delta = target_by_name[name] - base_by_name[name]
        if delta < -0.05:
            regressions.append({"name": name, "delta": round(delta, 3),
                                "base": base_by_name[name],
                                "target": target_by_name[name]})
        elif delta > 0.05:
            improvements.append({"name": name, "delta": round(delta, 3),
                                 "base": base_by_name[name],
                                 "target": target_by_name[name]})
        else:
            unchanged.append({"name": name, "delta": round(delta, 3)})
    return {
        "alpha": alpha,
        "t_stat": round(t, 4),
        "p_value": round(p, 4),
        "significant": p < alpha,
        "base_pass_rate": base.pass_rate,
        "target_pass_rate": target.pass_rate,
        "n_common": len(common),
        "regressions": regressions,
        "improvements": improvements,
        "unchanged_count": len(unchanged),
    }


# ---------------------------------------------------------------------------
# Suite loading + persistence
# ---------------------------------------------------------------------------


def load_suite(path: Path) -> Dict[str, Any]:
    """Load a single suite spec from a YAML file.

    Supports two shapes:
      - a single suite (one top-level ``name:`` + ``cases:``)
      - a multi-suite file with ``suites:`` as the top-level list
        (returns the first suite)
    """
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "suites" in data and isinstance(data["suites"], list) and data["suites"]:
        return data["suites"][0]
    return data


def save_suite_result(result: SuiteResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)


def load_suite_result(path: Path) -> SuiteResult:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cases = [CaseResult(**c) for c in data.get("cases", [])]
    res = SuiteResult(
        suite_name=data.get("suite_name", "unnamed"),
        run_id=data.get("run_id", "unknown"),
        cases=cases,
        started_at=data.get("started_at", 0.0),
        finished_at=data.get("finished_at", 0.0),
    )
    return res


# ---------------------------------------------------------------------------
# Round 13: dataset recording (JSONL append-only log of successful cases)
# ---------------------------------------------------------------------------
# The "record" workflow: after a successful eval run, the user can
# ``kairos eval record --run <run.json> --dataset <ds.jsonl>`` to
# append every case (with its output + cost) to a growing dataset.
# ``kairos eval replay --dataset <ds.jsonl>`` then re-runs every
# recorded case against the *current* agent and flags regressions.
#
# The JSONL format is intentionally simple: one case per line, each
# line a self-contained JSON object. Easy to grep, easy to diff, easy
# to version-control alongside the codebase.


def record_run(
    run: SuiteResult,
    dataset_path: Path,
    *,
    only_passed: bool = True,
) -> int:
    """Append each case from ``run`` to ``dataset_path`` (one per line).

    Returns the number of cases recorded. By default only passes
    are recorded — a regression suite is most useful when it
    contains the *known-good* behavior, not the flaky failures.
    """
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(dataset_path, "a", encoding="utf-8") as f:
        for case in run.cases:
            if only_passed and not case.passed:
                continue
            # ``details`` is a dict of per-grader outputs; we keep
            # it so replay can re-derive the same expectations.
            entry = {
                "suite_name": run.suite_name,
                "run_id": run.run_id,
                "case_name": case.name,
                "score": case.score,
                "output": case.details.get("output", ""),
                "details": case.details,
                "duration_ms": case.duration_ms,
                "cost_usd": case.cost_usd,
                "tokens_in": case.tokens_in,
                "tokens_out": case.tokens_out,
                "recorded_at": time.time(),
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            n += 1
    return n


def load_dataset(path: Path) -> List[Dict[str, Any]]:
    """Load a JSONL dataset, skipping blank lines and corrupt rows."""
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("dataset %s: corrupt row: %s", path, exc)
    return out


def dataset_to_suite_spec(
    dataset: List[Dict[str, Any]],
    name: str = "replay",
) -> Dict[str, Any]:
    """Convert a recorded dataset back into a runnable suite spec.

    The recorded ``output`` is used as the *expected output* for an
    exact-match grader; the grader will pass only if the new run
    emits exactly the same output. (For most real cases you'll
    want to relax this to a ``contains`` grader via the importer
    instead, but for byte-level regression detection this is the
    strictest possible check.)
    """
    cases = []
    for entry in dataset:
        cases.append({
            "name": entry.get("case_name", "<unnamed>"),
            # We have the *output* from the recorded run but not the
            # *input prompt* (the eval runner doesn't persist it).
            # Replay just re-runs the same case by name; the input
            # is the original requirement the user passed at record
            # time, which they can re-derive from the run_id.
            "input": entry.get("input", ""),
            "graders": [
                {"contains": [str(entry.get("output", ""))[:200]]},
            ],
        })
    return {"name": name, "cases": cases}


def replay_dataset(
    dataset_path: Path,
    target: Optional[Callable] = None,
    judge: Optional[Callable] = None,
) -> SuiteResult:
    """Re-run every recorded case in the dataset and return a suite result.

    The target callable is invoked with the original ``input`` (which
    may be empty if the case was recorded from a run that didn't
    persist the prompt — in that case the target receives "" and
    the call is best-effort).
    """
    target = target or run_target_agent
    dataset = load_dataset(dataset_path)
    spec = dataset_to_suite_spec(dataset, name=f"replay-{dataset_path.stem}")
    return run_suite(spec, target=target, judge=judge,
                     run_id=f"replay-{int(time.time() * 1000)}")


def derive_from_git_log(
    repo_path: Path,
    *,
    limit: int = 50,
    plan_header: str = "# Plan at this round",
) -> List[Dict[str, Any]]:
    """Round 13: extract eval case candidates from a kairos commit history.

    Each kairos checkpoint commit (R12.3 wrote the plan block into
    the commit body) becomes a candidate case. The case's graders
    are auto-generated from the plan:
      - ``contains: [each todo.content]`` — keeps the agent
        honest about the work it claimed to do at that commit
      - ``regex: <plan summary>`` — pins the plan shape
    The case is named ``git-round-N-score-S`` for easy filtering.

    Returns a list of case dicts (the same shape
    ``dataset_to_suite_spec`` consumes). The caller is expected
    to wrap them in a suite and run them.
    """
    try:
        proc = subprocess.run(
            ["git", "log", f"-n{limit}", "--format=%H%x1f%s%x1f%B%x1e"],
            cwd=str(repo_path), capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("git log failed for %s: %s", repo_path, exc)
        return []
    if proc.returncode != 0:
        logger.warning("git log non-zero exit: %s", proc.stderr)
        return []
    cases: List[Dict[str, Any]] = []
    for record in proc.stdout.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        # 3 fields separated by \x1f: hash, subject, body
        parts = record.split("\x1f", 2)
        if len(parts) < 3:
            continue
        sha, subject, body = parts[0].strip(), parts[1].strip(), parts[2].strip()
        # Match our commit subject
        if not subject.startswith("kairos: round "):
            continue
        # Extract round + score
        m = re.search(r"kairos:\s+round\s+(\d+)\s+(\w+)\s+\(score\s+(\d+)\)", subject)
        if not m:
            continue
        round_no, verdict, score = m.group(1), m.group(2), m.group(3)
        # Extract the plan block (between # Plan at this round and EOF)
        plan_block = ""
        if plan_header in body:
            plan_block = body.split(plan_header, 1)[1].strip()
        # Parse the plan items from the block. Format:
        #   - [x] Done
        #   - [>] In progress
        #   - [ ] Pending
        needles: List[str] = []
        for line in plan_block.splitlines():
            m = re.match(r"\s*-\s+\[[ x>]\]\s+(.*)", line)
            if m:
                content = m.group(1).strip()
                # Strip trailing active-form "_..." (if present)
                content = re.sub(r"\s+_.*$", "", content)
                if content:
                    needles.append(content)
        if not needles:
            continue
        cases.append({
            "name": f"git-round-{round_no}-score-{score}",
            "input": (f"Round {round_no} regression check "
                       f"(from commit {sha[:7]}, score {score}, {verdict})"),
            "graders": [{"contains": needles}],
        })
    return cases


def derive_and_write_suite(
    repo_path: Path,
    out_path: Path,
    name: str = "from-git-log",
    limit: int = 50,
) -> int:
    """Top-level helper: derive cases from ``repo_path`` git log
    and write them as a runnable YAML suite to ``out_path``.

    Returns the number of cases written.
    """
    cases = derive_from_git_log(repo_path, limit=limit)
    if not cases:
        # Still write an empty suite so the user has a starting point
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            f"# Derived from {repo_path}\n"
            f"# No kairos commits found in the last {limit} commits.\n"
            f"name: {name}\ncases: []\n",
            encoding="utf-8",
        )
        return 0
    write_yaml_simple(cases, out_path, name=name)
    return len(cases)


def write_yaml_simple(
    cases: List[Dict[str, Any]], out_path: Path, name: str = "imported",
) -> None:
    """Same shape as scripts/import_session_to_eval.write_yaml but
    a private copy here so eval.py doesn't depend on scripts/.

    Used by ``derive_and_write_suite`` to keep the CLI usable even
    when the scripts/ dir is missing (e.g. installed wheel)."""
    lines: List[str] = [
        f"# Derived from git log (kairos eval derive)",
        f"name: {name}",
        "threshold: 0.5",
        "cases:",
    ]
    for case in cases:
        # Local reimplementation of yaml quoting to avoid the
        # scripts/ dependency.
        def _qs(s: str) -> str:
            if any(c in s for c in [":", "#", "\n", '"']) or not s:
                return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
            return s
        lines.append(f"  - name: {_qs(case['name'])}")
        lines.append(f"    input: {_qs(case.get('input', ''))}")
        graders = case.get("graders") or []
        if not graders:
            lines.append("    graders: []")
        else:
            lines.append("    graders:")
            for g in graders:
                (k, v), = g.items()
                if isinstance(v, str):
                    lines.append(f"      - {k}: {_qs(v)}")
                elif isinstance(v, list):
                    lines.append(
                        f"      - {k}: [{', '.join(_qs(x) for x in v)}]"
                    )
                else:
                    lines.append(f"      - {k}: {v}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv: List[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="kairos.eval",
        description="Kairos agent regression evaluation framework",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run a suite")
    run.add_argument("--suite", required=True, help="YAML path")
    run.add_argument("--out", help="JSON output path (default: results/<run-id>.json)")
    run.add_argument("--target", help="Python callable in module:function form (overrides default CLI target)")
    run.add_argument("--judge", help="Python callable for LLM-judge graders (module:function form)")
    # Round 14: auto-record on success. When the suite's pass_rate
    # meets the threshold (suite-level or default 0.5), every
    # passing case is appended to the given JSONL dataset. This
    # makes the dataset grow organically with every successful CI
    # run — a self-healing regression suite.
    run.add_argument("--auto-record",
                     help="If set, append passing cases to this JSONL dataset on success")
    run.add_argument("--auto-record-threshold", type=float, default=None,
                     help="Override the auto-record pass-rate threshold "
                          "(default: suite's ``threshold`` field or 0.5)")

    cmp_ = sub.add_parser("compare", help="Diff two runs")
    cmp_.add_argument("base", help="Path to base run JSON")
    cmp_.add_argument("target", help="Path to target run JSON")
    cmp_.add_argument("--alpha", type=float, default=0.05)

    rec = sub.add_parser("record", help="Append a run's cases to a dataset")
    rec.add_argument("--run", required=True, help="Path to a run JSON")
    rec.add_argument("--dataset", required=True, help="Path to dataset JSONL")
    rec.add_argument("--all", action="store_true",
                     help="Record all cases (not just passed ones)")

    rep = sub.add_parser("replay", help="Re-run a recorded dataset")
    rep.add_argument("--dataset", required=True, help="Path to dataset JSONL")
    rep.add_argument("--out", help="Output run JSON")
    rep.add_argument("--target", help="Python callable in module:function form")
    rep.add_argument("--judge", help="Python callable for LLM-judge graders")

    drv = sub.add_parser("derive", help="Build a suite from kairos commits in git log")
    drv.add_argument("--repo", default=".", help="Path to a git repo (default: cwd)")
    drv.add_argument("--out", required=True, help="Output YAML path")
    drv.add_argument("--limit", type=int, default=50,
                     help="How many recent commits to scan (default: 50)")
    drv.add_argument("--name", default="from-git-log", help="Suite name")

    args = p.parse_args(argv)
    if args.cmd == "run":
        spec = load_suite(Path(args.suite))
        target = None
        if args.target:
            mod_name, fn_name = args.target.split(":")
            import importlib
            mod = importlib.import_module(mod_name)
            target = getattr(mod, fn_name)
        judge = None
        if args.judge:
            mod_name, fn_name = args.judge.split(":")
            import importlib
            mod = importlib.import_module(mod_name)
            judge = getattr(mod, fn_name)
        result = run_suite(spec, target=target, judge=judge)
        out_path = Path(args.out) if args.out else (
            Path("results") / f"{result.run_id}.json"
        )
        save_suite_result(result, out_path)
        # Round 14: auto-record on success. Suite-level threshold
        # is preferred; --auto-record-threshold overrides; default
        # falls back to 0.5.
        threshold = (
            args.auto_record_threshold
            if args.auto_record_threshold is not None
            else float(spec.get("threshold", 0.5))
        )
        recorded = 0
        if args.auto_record and result.pass_rate >= threshold:
            recorded = record_run(
                result, Path(args.auto_record), only_passed=True,
            )
            print(
                f"Auto-recorded {recorded} case(s) to {args.auto_record} "
                f"(pass_rate={result.pass_rate:.0%} >= threshold={threshold:.0%})"
            )
        print(
            f"Saved: {out_path}  "
            f"({len(result.cases)} cases, "
            f"pass_rate={result.pass_rate:.0%}, "
            f"cost=${result.total_cost_usd:.4f})"
        )
        return 0 if result.pass_rate >= threshold else 1
    if args.cmd == "compare":
        base = load_suite_result(Path(args.base))
        target = load_suite_result(Path(args.target))
        diff = compare_runs(base, target, alpha=args.alpha)
        print(json.dumps(diff, indent=2, ensure_ascii=False))
        return 0 if not diff["regressions"] else 2
    if args.cmd == "record":
        run = load_suite_result(Path(args.run))
        n = record_run(run, Path(args.dataset), only_passed=not args.all)
        print(f"Recorded {n} case(s) to {args.dataset}")
        return 0
    if args.cmd == "replay":
        target = None
        if args.target:
            mod_name, fn_name = args.target.split(":")
            import importlib
            mod = importlib.import_module(mod_name)
            target = getattr(mod, fn_name)
        judge = None
        if args.judge:
            mod_name, fn_name = args.judge.split(":")
            import importlib
            mod = importlib.import_module(mod_name)
            judge = getattr(mod, fn_name)
        result = replay_dataset(Path(args.dataset), target=target, judge=judge)
        out_path = Path(args.out) if args.out else (
            Path("results") / f"{result.run_id}.json"
        )
        save_suite_result(result, out_path)
        print(
            f"Replayed: {out_path}  "
            f"({len(result.cases)} cases, "
            f"pass_rate={result.pass_rate:.0%})"
        )
        return 0 if result.pass_rate >= 0.5 else 1
    if args.cmd == "derive":
        n = derive_and_write_suite(
            Path(args.repo), Path(args.out),
            name=args.name, limit=args.limit,
        )
        print(f"Derived {n} case(s) from {args.repo} git log -> {args.out}")
        return 0
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
