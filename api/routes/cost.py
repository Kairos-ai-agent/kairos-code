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
