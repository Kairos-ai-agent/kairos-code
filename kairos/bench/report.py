"""Report formatting for benchmark runs."""
from __future__ import annotations

import json
from typing import Any, Dict

from .runner import BenchmarkResult


def format_summary(result: BenchmarkResult) -> str:
    """Human-readable summary, one line per problem + a header."""
    s = result.summary()
    lines = [
        f"=== {s['agent']}  k={s['k']}  pass@1={s['pass_at_1']:.0%}  "
        f"pass@{s['k']}={s['pass_at_k']:.0%}  "
        f"{s['passed']}/{s['total']} passed  "
        f"{s['duration_s']}s  "
        f"tokens={s['total_prompt_tokens']+s['total_completion_tokens']} ===",
    ]
    for r in result.results:
        marker = "✓" if r.passed else "✗"
        lines.append(
            f"  {marker} {r.problem_id:<28} {r.attempts} attempt(s)  "
            f"{r.duration_s:.2f}s"
        )
        if not r.passed and r.error:
            first_line = r.error.splitlines()[0][:120]
            lines.append(f"      └─ {first_line}")
    return "\n".join(lines)


def to_json(result: BenchmarkResult) -> str:
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False)


def to_dict(result: BenchmarkResult) -> Dict[str, Any]:
    return result.to_dict()
