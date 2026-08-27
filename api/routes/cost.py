"""Cost dashboard API.

Returns aggregated cost data from the ``kairos.cost`` module
(ring buffer + on-disk JSONL log). Used by the CostDashboard
UI panel (Round 16).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter()


def _read_disk_log(path: Path, limit: int = 1000) -> List[Dict[str, Any]]:
    """Tail a JSONL cost log, returning at most ``limit`` entries.

    Newest first (we read tail of file). If the log is empty /
    missing, returns an empty list — never raises.
    """
    if not path.exists():
        return []
    try:
        # Read the whole file (small; cap at ~10MB to be safe)
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = [l for l in text.splitlines() if l]
        # Newest first
        lines.reverse()
        out: List[Dict[str, Any]] = []
        for line in lines[:limit]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
    except OSError as exc:
        logger.debug("cost log read failed: %s", exc)
        return []


@router.get("/summary")
async def summary(
    include_disk: bool = Query(True,
                               description="Also read from data/cost.jsonl on disk"),
    limit: int = Query(1000, ge=1, le=10_000),
) -> Dict[str, Any]:
    """Return a one-shot cost summary suitable for a dashboard.

    Combines the in-memory ring buffer (process-local) with
    the on-disk JSONL log (process-spanning) so a fresh server
    restart doesn't show "0 calls" until the next LLM call.
    """
    from kairos.cost import cost_summary, get_buffer, cost_by_model

    # In-memory (process-local)
    in_mem = cost_summary()
    in_mem_by_model = in_mem.get("models", {}) if isinstance(in_mem, dict) else {}
    # Disk (process-spanning) — merged into a unified view
    disk_entries: List[Dict[str, Any]] = []
    if include_disk:
        from kairos.cost import _get_log_path
        try:
            disk_entries = _read_disk_log(_get_log_path(), limit=limit)
        except Exception as exc:
            logger.debug("disk cost log read failed (non-fatal): %s", exc)
    # Total
    total_cost = float(in_mem.get("cost_usd", 0.0)) + sum(
        float(e.get("cost_usd", 0.0)) for e in disk_entries
    )
    total_calls = int(in_mem.get("calls", 0)) + len(disk_entries)
    # Unified by_model (merge in-memory + disk)
    by_model: Dict[str, Dict[str, Any]] = {}
    for model, data in in_mem_by_model.items():
        by_model[model] = {
            "calls": int(data.get("calls", 0)),
            "prompt_tokens": int(data.get("prompt_tokens", 0)),
            "completion_tokens": int(data.get("completion_tokens", 0)),
            "cost_usd": float(data.get("cost_usd", 0.0)),
            "avg_duration_ms": int(data.get("avg_duration_ms", 0)),
        }
    for e in disk_entries:
        m = e.get("model", "unknown")
        slot = by_model.setdefault(m, {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "cost_usd": 0.0, "avg_duration_ms": 0,
        })
        slot["calls"] += 1
        slot["prompt_tokens"] += int(e.get("prompt_tokens", 0))
        slot["completion_tokens"] += int(e.get("completion_tokens", 0))
        slot["cost_usd"] += float(e.get("cost_usd", 0.0))
        # Naive avg: total_duration / total_calls (we don't have
        # per-call duration summed, so use the entry's duration as
        # a proxy — accurate enough for a dashboard).
        if e.get("duration_ms"):
            slot["avg_duration_ms"] = (
                (slot["avg_duration_ms"] * (slot["calls"] - 1) + int(e["duration_ms"]))
                // slot["calls"]
            )
    return {
        "calls": total_calls,
        "cost_usd": total_cost,
        "in_memory": in_mem,
        "by_model": by_model,
    }


@router.get("/recent")
async def recent(
    limit: int = Query(50, ge=1, le=500),
) -> List[Dict[str, Any]]:
    """Return the most recent cost entries (newest first).

    Reads from the in-memory ring buffer; for full history
    use ``/summary?include_disk=true`` or read the JSONL log
    directly.
    """
    from kairos.cost import get_buffer
    entries = get_buffer()
    # Convert dataclass to dict for the wire
    out: List[Dict[str, Any]] = []
    for e in reversed(entries[-limit:]):
        out.append({
            "timestamp": e.timestamp,
            "model": e.model,
            "provider": e.provider,
            "prompt_tokens": e.prompt_tokens,
            "completion_tokens": e.completion_tokens,
            "cost_usd": e.cost_usd,
            "duration_ms": e.duration_ms,
        })
    return out


@router.get("/by_model")
async def by_model() -> Dict[str, Dict[str, Any]]:
    """Per-model cost aggregation.

    Merges in-memory + disk so the dashboard has full data
    even after a server restart.
    """
    from kairos.cost import cost_by_model, _get_log_path

    by_model = dict(cost_by_model())  # in-memory baseline
    try:
        for e in _read_disk_log(_get_log_path(), limit=10_000):
            m = e.get("model", "unknown")
            slot = by_model.setdefault(m, {
                "calls": 0, "prompt_tokens": 0,
                "completion_tokens": 0, "cost_usd": 0.0,
                "avg_duration_ms": 0,
            })
            slot["calls"] += 1
            slot["prompt_tokens"] += int(e.get("prompt_tokens", 0))
            slot["completion_tokens"] += int(e.get("completion_tokens", 0))
            slot["cost_usd"] += float(e.get("cost_usd", 0.0))
            if e.get("duration_ms"):
                slot["avg_duration_ms"] = (
                    (slot["avg_duration_ms"] * (slot["calls"] - 1)
                     + int(e["duration_ms"]))
                    // slot["calls"]
                )
    except Exception as exc:
        logger.debug("disk by_model merge failed: %s", exc)
    return by_model


# ---------------------------------------------------------------------------
# Round 19: datasets (eval recording) listing
# ---------------------------------------------------------------------------


@router.get("/datasets")
async def list_datasets(directory: str = "") -> List[Dict[str, Any]]:
    """List JSONL datasets under the given directory (default: data/datasets/).

    For each ``*.jsonl`` file, return a small summary (count,
    size, mtime) so the UI can show "you have 3 datasets" without
    loading every record.
    """
    from kairos.cost import _get_log_path
    base = Path(directory) if directory else _get_log_path().parent / "datasets"
    if not base.exists():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(base.glob("*.jsonl")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            count = sum(1 for line in text.splitlines() if line)
            stat = p.stat()
            out.append({
                "name": p.name,
                "path": str(p),
                "size_bytes": stat.st_size,
                "count": count,
                "mtime": stat.st_mtime,
            })
        except OSError:
            continue
    return out


@router.post("/datasets/record")
async def record_dataset(
    run_path: str,
    dataset_path: str,
    only_passed: bool = True,
) -> Dict[str, Any]:
    """Append cases from a run JSON to a dataset JSONL file."""
    from kairos.eval import load_suite_result, record_run
    run = load_suite_result(Path(run_path))
    n = record_run(run, Path(dataset_path), only_passed=only_passed)
    return {"recorded": n, "dataset": dataset_path}


@router.post("/datasets/replay")
async def replay_dataset_endpoint(
    dataset_path: str,
    out_path: str = "",
) -> Dict[str, Any]:
    """Replay a recorded dataset against the default target."""
    from kairos.eval import replay_dataset, save_suite_result
    result = replay_dataset(Path(dataset_path))
    final_out = (
        Path(out_path) if out_path
        else _get_log_path().parent / "replay" / f"{result.run_id}.json"
    )
    save_suite_result(result, final_out)
    return {
        "run_id": result.run_id,
        "pass_rate": result.pass_rate,
        "total_cost_usd": result.total_cost_usd,
        "out_path": str(final_out),
    }


@router.post("/datasets/derive")
async def derive_from_git_endpoint(
    repo_path: str = ".",
    out_path: str = "",
    limit: int = 50,
) -> Dict[str, Any]:
    """Derive an eval suite from a git repo's kairos commit history."""
    from kairos.eval import derive_and_write_suite
    out = (
        Path(out_path) if out_path
        else _get_log_path().parent / "derived.yaml"
    )
    n = derive_and_write_suite(Path(repo_path), out, name="api-derived",
                                limit=limit)
    return {"cases": n, "out_path": str(out)}


# ---------------------------------------------------------------------------
# Round 35: cost-of-goods-sold (COGS) value metrics
# ---------------------------------------------------------------------------


def _count_dataset_outcomes(datasets_dir: Path) -> Dict[str, int]:
    """Count pass/fail/total across all datasets/*.jsonl.

    Each line in a dataset JSONL is a case record. The pass/fail
    is the ``passed`` boolean (R13 record_run format). Returns
    aggregate totals.
    """
    total = 0
    passed = 0
    failed = 0
    if not datasets_dir.exists():
        return {"total": 0, "passed": 0, "failed": 0}
    for p in sorted(datasets_dir.glob("*.jsonl")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1
                if rec.get("passed"):
                    passed += 1
                else:
                    failed += 1
        except OSError:
            continue
    return {"total": total, "passed": passed, "failed": failed}


@router.get("/value")
async def cost_value_metrics() -> Dict[str, Any]:
    """COGS (cost of goods sold) value metrics.

    Combines:
      - ``data/cost.jsonl`` (every LLM call's cost)        — R14
      - ``data/datasets/*.jsonl`` (eval pass/fail records)  — R13/R19
      - ``data/alerts.jsonl`` (fired alerts)                — R28

    Returns derived metrics:
      - ``cost_per_case``     : total_cost / total_cases
      - ``cost_per_passing``  : total_cost / passing_cases
      - ``cost_per_alert``    : total_cost / alert_count
      - ``efficiency``        : passing / total (0-1)
      - ``approval_yield``    : 1 - critical_alert_ratio (proxy for "fraction
                                 of work that didn't need human intervention")

    If a denominator is zero, the corresponding metric is
    ``None`` (not 0 or NaN) so the UI can render "N/A" cleanly.
    """
    from kairos.cost import _get_log_path
    from kairos.alerts_dispatcher import _get_history_path

    # Total cost (disk-only — the in-memory ring buffer is process-local
    # and the dashboard already exposes it via /summary)
    cost_path = _get_log_path()
    total_cost = 0.0
    n_calls = 0
    if cost_path.exists():
        for line in cost_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line:
                continue
            try:
                rec = json.loads(line)
                total_cost += float(rec.get("cost_usd", 0.0))
                n_calls += 1
            except (json.JSONDecodeError, ValueError, TypeError):
                continue

    # Dataset outcomes
    datasets_dir = cost_path.parent / "datasets"
    outcomes = _count_dataset_outcomes(datasets_dir)
    total_cases = outcomes["total"]
    passing = outcomes["passed"]
    failed = outcomes["failed"]

    # Alert counts
    alert_path = _get_history_path()
    n_alerts = 0
    n_critical = 0
    if alert_path.exists():
        for line in alert_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line:
                continue
            try:
                rec = json.loads(line)
                n_alerts += 1
                if rec.get("severity") == "critical":
                    n_critical += 1
            except json.JSONDecodeError:
                continue

    def _ratio(num: float, den: float) -> Optional[float]:
        return round(num / den, 6) if den > 0 else None

    return {
        "total_cost_usd": round(total_cost, 6),
        "n_llm_calls": n_calls,
        "dataset": {
            "total_cases": total_cases,
            "passed": passing,
            "failed": failed,
        },
        "alerts": {
            "total": n_alerts,
            "critical": n_critical,
        },
        "metrics": {
            "cost_per_case": _ratio(total_cost, total_cases),
            "cost_per_passing": _ratio(total_cost, passing),
            "cost_per_alert": _ratio(total_cost, n_alerts),
            "efficiency": _ratio(passing, total_cases),
            "approval_yield": (
                round(1.0 - (n_critical / n_alerts), 6) if n_alerts > 0 else None
            ),
        },
    }
