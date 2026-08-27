"""Round 24: multi-run trend aggregator.

The :mod:`kairos.eval` framework already supports comparing two
runs (``eval compare``). This module extends the idea to **N
runs over time** — a time series of pass_rate, cost, and p95
latency. Useful for:

  - "How is the agent doing this week vs last week?"
  - "Which case has been flaky for the last 3 runs?"
  - "When did cost start climbing?"

The aggregator reads a directory of run JSONs (each produced
by ``eval run``) and emits a trend table. The user can either:

  - Pass an explicit list of run files
  - Point at a directory; we glob all ``run-*.json`` files in
    mtime order (newest last)
  - Set ``window=N`` to take only the last N runs

Output is a structured ``TrendReport`` that's both
machine-readable (JSON) and human-renderable (one line per run).

Quick start:
    from kairos.trend import aggregate_trend_from_dir
    report = aggregate_trend_from_dir("results/", window=20)
    for r in report.runs:
        print(f"{r.timestamp}  {r.suite_name}  "
              f"pass={r.pass_rate:.0%}  cost=${r.cost_usd:.4f}")
"""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RunPoint:
    """One run's worth of trend data."""
    timestamp: str           # ISO-8601 (UTC)
    path: str               # source file
    suite_name: str
    run_id: str
    cases: int
    passed: int
    pass_rate: float
    cost_usd: float
    tokens: int
    avg_duration_ms: float
    p95_duration_ms: float


@dataclass
class TrendReport:
    """Aggregate trend across N runs."""
    suite_name: str
    runs: List[RunPoint] = field(default_factory=list)
    n_total: int = 0
    n_passed: int = 0
    avg_pass_rate: float = 0.0
    avg_cost_usd: float = 0.0
    cost_first_to_last: float = 0.0
    pass_rate_first_to_last: float = 0.0
    window: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["runs"] = [asdict(r) for r in self.runs]
        return d


def _load_run(run_path: Path) -> Optional[RunPoint]:
    """Parse a single run JSON into a RunPoint."""
    try:
        with open(run_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("failed to load run %s: %s", run_path, exc)
        return None
    cases = data.get("cases", []) or []
    if not cases:
        return None
    durations: List[float] = []
    total_cost = 0.0
    total_tokens = 0
    for c in cases:
        try:
            total_cost += float(c.get("cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            pass
        try:
            total_tokens += int(c.get("tokens_in", 0) or 0) + int(
                c.get("tokens_out", 0) or 0
            )
        except (TypeError, ValueError):
            pass
        try:
            d = int(c.get("duration_ms", 0) or 0)
            if d > 0:
                durations.append(d)
        except (TypeError, ValueError):
            pass
    started = float(data.get("started_at", 0.0) or 0.0)
    if started <= 0:
        try:
            started = run_path.stat().st_mtime
        except OSError:
            started = 0.0
    return RunPoint(
        timestamp=datetime.utcfromtimestamp(started).isoformat() + "Z"
                  if started > 0 else "",
        path=str(run_path),
        suite_name=data.get("suite_name", "?"),
        run_id=data.get("run_id", "?"),
        cases=len(cases),
        passed=sum(1 for c in cases if c.get("passed")),
        pass_rate=float(data.get("pass_rate", 0.0) or 0.0),
        cost_usd=total_cost,
        tokens=total_tokens,
        avg_duration_ms=(sum(durations) / len(durations)) if durations else 0.0,
        p95_duration_ms=_p95(durations) if durations else 0.0,
    )


def _p95(values: List[float]) -> float:
    """95th percentile (inclusive). Returns the max for n<20."""
    if not values:
        return 0.0
    if len(values) < 20:
        return max(values)
    return statistics.quantiles(values, n=20)[-1]


def aggregate_trend(
    run_files: List[Path],
    *,
    suite_name: str = "",
    window: int = 0,
) -> TrendReport:
    """Compute a TrendReport over a list of run files (mtime order)."""
    runs: List[RunPoint] = []
    for p in run_files:
        pt = _load_run(Path(p))
        if pt is not None:
            runs.append(pt)
    if window > 0 and len(runs) > window:
        runs = runs[-window:]
    if not runs:
        return TrendReport(suite_name=suite_name, window=window)
    pass_rates = [r.pass_rate for r in runs]
    costs = [r.cost_usd for r in runs]
    report = TrendReport(
        suite_name=runs[0].suite_name if not suite_name else suite_name,
        runs=runs,
        n_total=len(runs),
        n_passed=sum(1 for r in runs if r.pass_rate >= 0.5),
        avg_pass_rate=sum(pass_rates) / len(pass_rates),
        avg_cost_usd=sum(costs) / len(costs),
        cost_first_to_last=(
            (costs[-1] - costs[0]) / costs[0] * 100.0 if costs[0] > 0 else 0.0
        ),
        pass_rate_first_to_last=(
            (pass_rates[-1] - pass_rates[0]) * 100.0
        ),
        window=window,
    )
    return report


def aggregate_trend_from_dir(
    directory: Path, *,
    suite_name: str = "",
    window: int = 0,
    pattern: str = "run-*.json",
) -> TrendReport:
    """Auto-discover run JSONs in a directory (mtime order)."""
    p = Path(directory)
    if not p.exists():
        return TrendReport(suite_name=suite_name, window=window)
    files = sorted(p.glob(pattern), key=lambda x: x.stat().st_mtime)
    return aggregate_trend(files, suite_name=suite_name, window=window)


@dataclass
class CaseTrendPoint:
    """One case's pass/fail history across runs."""
    case_name: str
    pass_rate: float              # 0.0 - 1.0
    n_runs: int
    n_passed: int
    n_failed: int
    flaky: bool                  # True if both pass and fail observed
    history: List[bool] = field(default_factory=list)  # newest last


@dataclass
class CaseTrendReport:
    """Per-case trend across the last N runs."""
    cases: List[CaseTrendPoint] = field(default_factory=list)
    n_runs: int = 0
    window: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


def aggregate_per_case_trend(
    directory: Path, *,
    window: int = 20,
    pattern: str = "run-*.json",
) -> CaseTrendReport:
    """For each case name, return its pass/fail history across
    the last ``window`` runs.

    A case is flagged ``flaky`` if it passed at least once and
    failed at least once in the window — i.e. non-deterministic
    behavior. This is the "which case has been flaky" answer.
    """
    p = Path(directory)
    if not p.exists():
        return CaseTrendReport(window=window)
    files = sorted(p.glob(pattern), key=lambda x: x.stat().st_mtime)
    if window > 0 and len(files) > window:
        files = files[-window:]

    # case_name -> list[bool] (newest last)
    by_case: Dict[str, List[bool]] = {}
    n_runs = 0
    for run_path in files:
        try:
            with open(run_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        cases = data.get("cases", []) or []
        n_runs += 1
        for c in cases:
            name = c.get("name")
            if not name:
                continue
            by_case.setdefault(name, []).append(bool(c.get("passed")))

    out: List[CaseTrendPoint] = []
    for name, history in by_case.items():
        n = len(history)
        n_pass = sum(1 for x in history if x)
        n_fail = n - n_pass
        pr = n_pass / n if n else 0.0
        out.append(CaseTrendPoint(
            case_name=name,
            pass_rate=pr,
            n_runs=n,
            n_passed=n_pass,
            n_failed=n_fail,
            flaky=(n_pass > 0 and n_fail > 0),
            history=history,
        ))
    # Sort: flaky first (most actionable), then by lowest pass_rate
    out.sort(key=lambda p: (not p.flaky, p.pass_rate, p.case_name))
    return CaseTrendReport(cases=out, n_runs=n_runs, window=window)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_row(r: RunPoint) -> str:
    return (
        f"{r.timestamp:20s}  {r.suite_name:24s}  "
        f"pass={r.pass_rate:>5.0%}  ${r.cost_usd:>8.4f}  "
        f"p95={r.p95_duration_ms:>6.0f}ms  {r.path}"
    )


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="kairos.trend")
    p.add_argument("directory", help="Directory of run JSONs")
    p.add_argument("--window", type=int, default=20,
                   help="Take the last N runs (default 20)")
    p.add_argument("--pattern", default="run-*.json",
                   help="Glob pattern (default run-*.json)")
    p.add_argument("--out", help="Write the report JSON to this path")
    args = p.parse_args(argv)
    report = aggregate_trend_from_dir(
        args.directory, window=args.window, pattern=args.pattern,
    )
    print(f"Suite: {report.suite_name}")
    print(f"Runs: {report.n_total}  (avg pass_rate={report.avg_pass_rate:.0%})")
    print(f"Cost: avg=${report.avg_cost_usd:.4f}  "
          f"trend={report.cost_first_to_last:+.1f}%")
    print(f"Pass rate trend: {report.pass_rate_first_to_last:+.1f} pp")
    print("")
    print(f"{'timestamp':20s}  {'suite':24s}  pass    cost       p95     path")
    print("-" * 100)
    for r in report.runs:
        print(_format_row(r))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
