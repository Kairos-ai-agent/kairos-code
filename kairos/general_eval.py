"""Task-level (general, non-code) evaluation over skeleton run records.

Why this exists
---------------
``kairos/eval.py`` grades **code generation**: a YAML suite of cases, each
scored 0..1 by mechanical graders, compared run-to-run with a t-test. That is
the right shape for "did the Coder's diff do the thing". It cannot say anything
about a *general* skeleton task ("read three docs -> produce a report"),
because that outcome is not a model score -- it is a structured
:class:`~kairos.skeleton.contracts.Verdict` whose ``passed`` is ``True``,
``False`` or ``None`` and whose decision is a list of per-criterion booleans in
``evidence``.

This module is the general-task counterpart. It reads the run records the
skeleton driver already persists (``<work_dir>/.kairos/skeleton-runs/
skeleton-run-*.json``) and computes verifiable, non-fabricated metrics:

* **three-way outcome** -- ``passed`` / ``failed`` / ``undecided`` counts and
  rates, where a ``passed is None`` abstention is **never** counted as a pass;
* **abstention rate** -- the ``passed is None`` share, reported on its own
  line and never folded into the pass rate;
* **criterion-level satisfaction** -- how many ``evidence`` rows were
  satisfied across every verdict, overall and per criterion name, with the
  undecided (``satisfied is None``) rows counted and excluded from the rate
  rather than silently dropped;
* **per-verifier breakdown** -- the same numbers grouped by
  ``verdict.verifier`` (``tests`` / ``assertion`` / ``citations`` / ``rubric``
  / ``human`` / ``tool_oracle`` / ``reviewer`` / ...).

Honesty rules (the whole point)
-------------------------------
* every number is derived from real run records read off disk; nothing is
  synthesised and there is no default 0% or 100%;
* an empty directory, a directory with no run records, and records that cannot
  be parsed each produce an explicit ``status`` + ``reason`` and ``None``
  rates -- a real "nothing was measured", not a silent zero;
* a malformed record is skipped, **counted**, and reported with its path and
  the reason, so a partial read is never mistaken for a clean one.

No network, no model, no new dependency: this is arithmetic over files the
driver already wrote.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kairos.skeleton.driver import SkeletonRun

logger = logging.getLogger(__name__)

#: File-name pattern the skeleton driver persists its runs under.
RUN_GLOB = "skeleton-run-*.json"

#: Conventional run directory under a workspace root.
_RUN_DIR_PARTS = (".kairos", "skeleton-runs")

#: Directories never walked when falling back to a recursive search -- the
#: same "don't crawl into vendored trees" rule the rest of the tooling uses.
_IGNORED_DIRS = frozenset({
    ".venv", "venv", "__pycache__", ".git", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
})

#: The verifier kinds the gap report names; used only to *order* the
#: per-verifier report. A verifier name outside this list is still reported,
#: never dropped.
KNOWN_VERIFIERS: Tuple[str, ...] = (
    "tests", "assertion", "citations", "rubric", "human", "tool_oracle",
    "reviewer",
)

#: Bucket name for a verdict with an empty ``verifier`` string.
NULL_VERIFIER = "<none>"


# ---------------------------------------------------------------------------
# Discovery + loading
# ---------------------------------------------------------------------------


def _iter_run_files(root: Path) -> Tuple[List[Path], str, Path]:
    """Discover run-record files under ``root``.

    Returns ``(files, search_mode, search_path)``. Prefers the conventional
    ``<root>/.kairos/skeleton-runs/`` directory; only when that does not exist
    does it fall back to a recursive ``skeleton-run-*.json`` walk that skips
    vendored/cache directories.
    """
    run_dir = root
    for part in _RUN_DIR_PARTS:
        run_dir = run_dir / part
    if run_dir.is_dir():
        files = sorted(p for p in run_dir.glob(RUN_GLOB) if p.is_file())
        return files, "run-dir", run_dir
    files: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]
        for name in filenames:
            if name.startswith("skeleton-run-") and name.endswith(".json"):
                files.append(Path(dirpath) / name)
    return sorted(files), "recursive-glob", root


def _record_invalid_reason(raw: Any) -> Optional[str]:
    """Why ``raw`` is not a usable skeleton run record, or ``None`` if it is.

    A record must be a JSON object carrying a ``verdict`` object with a
    ``passed`` key and a ``task`` object. This is deliberately stricter than
    :meth:`SkeletonRun.from_dict`, which happily fills defaults for ``{}`` --
    an empty object is not a run, and counting it as an ``undecided`` run
    would inflate the abstention rate with a non-run.
    """
    if not isinstance(raw, dict):
        return "not a JSON object"
    verdict = raw.get("verdict")
    if not isinstance(verdict, dict):
        return "missing 'verdict' object (not a skeleton run record?)"
    if "passed" not in verdict:
        return "verdict has no 'passed' key"
    if not isinstance(raw.get("task"), dict):
        return "missing 'task' object"
    return None


def load_run_records(path: Any) -> Dict[str, Any]:
    """Load every run record under ``path`` (a file or a directory).

    Returns ``{"info": {...}, "records": [SkeletonRun, ...]}``. ``info`` always
    carries a ``status`` and a human ``reason``; malformed files are listed in
    ``info["malformed"]`` and counted in ``info["records_malformed"]`` rather
    than silently dropped.
    """
    raw_path = Path(str(path)).expanduser()
    info: Dict[str, Any] = {
        "input": str(raw_path),
        "search_mode": "",
        "search_path": "",
        "files_found": 0,
        "records_loaded": 0,
        "records_malformed": 0,
        "malformed": [],
        "status": "ok",
        "reason": "",
    }
    if not raw_path.exists():
        info["status"] = "not-found"
        info["reason"] = f"path does not exist: {raw_path}"
        return {"info": info, "records": []}

    if raw_path.is_file():
        files = [raw_path]
        info["search_mode"] = "file"
        info["search_path"] = str(raw_path)
    else:
        files, mode, search_path = _iter_run_files(raw_path)
        info["search_mode"] = mode
        info["search_path"] = str(search_path)
    info["files_found"] = len(files)

    records: List[SkeletonRun] = []
    for path_obj in files:
        try:
            raw = json.loads(Path(path_obj).read_text(encoding="utf-8"))
        except Exception as exc:  # bad JSON, unreadable file, ...
            info["malformed"].append(
                {"path": str(path_obj), "reason": f"unreadable JSON: {exc}"}
            )
            continue
        reason = _record_invalid_reason(raw)
        if reason is not None:
            info["malformed"].append({"path": str(path_obj), "reason": reason})
            continue
        try:
            records.append(SkeletonRun.from_dict(raw))
        except Exception as exc:  # a shape from_dict still chokes on
            info["malformed"].append(
                {"path": str(path_obj), "reason": f"cannot build run: {exc}"}
            )

    info["records_loaded"] = len(records)
    info["records_malformed"] = len(info["malformed"])
    for item in info["malformed"]:
        # A skipped record is a *warning*, never a silent drop: the caller gets
        # the count in ``records_malformed`` and the detail in ``malformed``.
        logger.warning("general_eval: skipped malformed run record %s: %s",
                       item["path"], item["reason"])

    if info["files_found"] == 0:
        info["status"] = "no-records"
        if info["search_mode"] == "run-dir":
            info["reason"] = (
                f"no {RUN_GLOB} in {info['search_path']} "
                "(the run directory exists but holds no run records)"
            )
        else:
            info["reason"] = f"no {RUN_GLOB} found under {info['search_path']}"
    elif not records:
        info["status"] = "all-malformed"
        info["reason"] = (
            f"{info['files_found']} record file(s) found but none could be "
            "parsed (see 'malformed')"
        )
    else:
        info["status"] = "ok"
        info["reason"] = (
            f"{info['records_loaded']} record(s) loaded"
            + (f", {info['records_malformed']} malformed record(s) skipped"
               if info["records_malformed"] else "")
        )
    return {"info": info, "records": records}


# ---------------------------------------------------------------------------
# Metric arithmetic
# ---------------------------------------------------------------------------


def _rate(num: int, den: int) -> Optional[float]:
    """``num/den`` rounded, or ``None`` when there is nothing to divide by.

    ``None`` (not ``0.0``) is the honest answer for an empty denominator: a
    rate over zero records does not exist.
    """
    if not den:
        return None
    return round(num / den, 4)


def _sat_bucket(value: Any) -> str:
    """Classify an evidence ``satisfied`` value: satisfied / unsatisfied / unknown."""
    if value is True:
        return "satisfied"
    if value is False:
        return "unsatisfied"
    return "unknown"


def _empty_criteria() -> Dict[str, Any]:
    return {
        "evidence_rows": 0,
        "satisfied": 0,
        "unsatisfied": 0,
        "unknown": 0,
        "satisfaction_rate": None,
        "by_criterion": {},
    }


def _accumulate_criteria(stats: Dict[str, Any], verdicts: List[Any]) -> Dict[str, Any]:
    """Fold every verdict's ``evidence`` rows into ``stats`` (in place)."""
    for verdict in verdicts:
        for row in (verdict.evidence or []):
            if not isinstance(row, dict):
                continue
            name = str(row.get("criterion") or "<unnamed>")
            bucket = _sat_bucket(row.get("satisfied"))
            stats["evidence_rows"] += 1
            stats[bucket] += 1
            entry = stats["by_criterion"].setdefault(
                name,
                {"satisfied": 0, "unsatisfied": 0, "unknown": 0,
                 "total": 0, "satisfaction_rate": None},
            )
            entry[bucket] += 1
            entry["total"] += 1
    # The satisfaction rate is over *decided* criteria only; the undecided
    # rows are reported (``unknown``) but excluded from the denominator.
    stats["satisfaction_rate"] = _rate(
        stats["satisfied"], stats["satisfied"] + stats["unsatisfied"]
    )
    for entry in stats["by_criterion"].values():
        entry["satisfaction_rate"] = _rate(
            entry["satisfied"], entry["satisfied"] + entry["unsatisfied"]
        )
    return stats


def _new_group() -> Dict[str, Any]:
    return {
        "records": 0, "passed": 0, "failed": 0, "undecided": 0, "needs_human": 0,
    }


def _new_verifier_group() -> Dict[str, Any]:
    return {**_new_group(), "criteria": _empty_criteria()}


def _bump(group: Dict[str, Any], verdict: Any) -> None:
    group["records"] += 1
    if verdict.passed is True:
        group["passed"] += 1
    elif verdict.passed is False:
        group["failed"] += 1
    else:
        group["undecided"] += 1
        if getattr(verdict, "requires_human", False):
            group["needs_human"] += 1


def evaluate_records(records: List[SkeletonRun]) -> Dict[str, Any]:
    """Compute the three-way, abstention, criterion and per-verifier metrics.

    ``undecided`` is ``verdict.passed is None`` in every reading of it, and it
    is never added to ``passed``. ``needs_human`` (a ``requires_human`` gate)
    is a subset of ``undecided``; ``abstained`` is ``undecided`` minus the
    human-gated ones.
    """
    totals = _new_group()
    by_verifier: Dict[str, Dict[str, Any]] = {}
    verdicts: List[Any] = []

    for run in records:
        verdict = run.verdict
        verdicts.append(verdict)
        _bump(totals, verdict)
        name = verdict.verifier or NULL_VERIFIER
        group = by_verifier.setdefault(name, _new_verifier_group())
        _bump(group, verdict)

    n = totals["records"]
    totals["abstained"] = totals["undecided"] - totals["needs_human"]
    totals["passed_rate"] = _rate(totals["passed"], n)
    totals["failed_rate"] = _rate(totals["failed"], n)
    totals["undecided_rate"] = _rate(totals["undecided"], n)
    # 弃权率: the passed=None share, deliberately its own field so nobody
    # mistakes it for (or adds it into) the pass rate.
    totals["abstention_rate"] = totals["undecided_rate"]
    totals["pass_rate"] = _rate(totals["passed"], n)  # explicit passed-only

    for group in by_verifier.values():
        m = group["records"]
        group["abstained"] = group["undecided"] - group["needs_human"]
        group["pass_rate"] = _rate(group["passed"], m)
        group["abstention_rate"] = _rate(group["undecided"], m)

    # Criterion stats: overall, and per verifier (each over that verifier's
    # own verdicts).
    for name, group in by_verifier.items():
        own = [v for v in verdicts if (v.verifier or NULL_VERIFIER) == name]
        _accumulate_criteria(group["criteria"], own)
    criteria = _accumulate_criteria(_empty_criteria(), verdicts)

    return {"totals": totals, "criteria": criteria, "by_verifier": by_verifier}


def evaluate_path(path: Any) -> Dict[str, Any]:
    """Load records under ``path`` and return the full metrics report."""
    loaded = load_run_records(path)
    report: Dict[str, Any] = {
        "tool": "kairos.general_eval",
        "schema": 1,
        "source": loaded["info"],
    }
    report.update(evaluate_records(loaded["records"]))
    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _pct(rate: Optional[float]) -> str:
    """A rate as a percentage, or ``n/a`` when it does not exist."""
    return "n/a" if rate is None else f"{rate * 100:.1f}%"


def _ordered_verifiers(by_verifier: Dict[str, Any]) -> List[str]:
    known = [n for n in KNOWN_VERIFIERS if n in by_verifier]
    rest = sorted(n for n in by_verifier if n not in KNOWN_VERIFIERS)
    return known + rest


def render_text(report: Dict[str, Any]) -> str:
    """A human-readable report; states the reason whenever nothing was measured."""
    src = report["source"]
    lines = ["=== kairos general-task evaluation ==="]
    lines.append(f"input       : {src['input']}")
    lines.append(f"search      : {src['search_path']}  ({src['search_mode']})")
    lines.append(
        f"records     : {src['records_loaded']} loaded, "
        f"{src['records_malformed']} malformed (of {src['files_found']} file(s))"
    )
    lines.append(f"status      : {src['status']} -- {src['reason']}")

    if src["status"] != "ok":
        lines.append("")
        lines.append(
            "nothing was measured: no pass rate, no abstention rate and no "
            "criterion satisfaction are reported (a 0% or 100% here would be "
            "fabricated)."
        )
        for item in src["malformed"]:
            lines.append(f"  malformed: {item['path']}")
            lines.append(f"             reason: {item['reason']}")
        return "\n".join(lines)

    t = report["totals"]
    lines.append("")
    lines.append("-- three-way outcome (undecided is never counted as a pass) --")
    lines.append(f"  passed      : {t['passed']}/{t['records']}  ({_pct(t['passed_rate'])})")
    lines.append(f"  failed      : {t['failed']}/{t['records']}  ({_pct(t['failed_rate'])})")
    lines.append(f"  undecided   : {t['undecided']}/{t['records']}  ({_pct(t['undecided_rate'])})")
    lines.append(f"  pass rate   : {_pct(t['pass_rate'])}  (passed only)")

    lines.append("")
    lines.append("-- abstention (弃权率: passed=None share, listed separately) --")
    lines.append(
        f"  abstention  : {t['undecided']}/{t['records']}  "
        f"({_pct(t['abstention_rate'])})   "
        f"[needs_human {t['needs_human']}, abstained {t['abstained']}]"
    )

    c = report["criteria"]
    decided = c["satisfied"] + c["unsatisfied"]
    lines.append("")
    lines.append("-- criteria (criterion-level) --")
    lines.append(
        f"  evidence    : {c['evidence_rows']} row(s); satisfied {c['satisfied']}, "
        f"unsatisfied {c['unsatisfied']}, unknown {c['unknown']}"
    )
    lines.append(
        f"  satisfaction: {_pct(c['satisfaction_rate'])} of decided criteria "
        f"({c['satisfied']}/{decided}); {c['unknown']} undecided row(s) "
        "excluded from the rate"
    )

    lines.append("")
    lines.append("-- by verifier --")
    for name in _ordered_verifiers(report["by_verifier"]):
        grp = report["by_verifier"][name]
        lines.append(
            f"  {name:<12} records {grp['records']}  passed {grp['passed']}  "
            f"failed {grp['failed']}  undecided {grp['undecided']}  "
            f"(pass {_pct(grp['pass_rate'])}, abstain {_pct(grp['abstention_rate'])})"
        )
        gc = grp["criteria"]
        lines.append(
            f"  {'':<12} criteria {gc['satisfied']} satisfied / "
            f"{gc['unsatisfied']} unsatisfied / {gc['unknown']} unknown "
            f"-> {_pct(gc['satisfaction_rate'])}"
        )

    if src["records_malformed"]:
        lines.append("")
        lines.append("-- skipped malformed records --")
        for item in src["malformed"]:
            lines.append(f"  {item['path']}: {item['reason']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cli_main(argv: Optional[List[str]] = None) -> int:
    """Entry point for ``python -m kairos.general_eval`` / ``kairos skeleton eval``.

    Exit codes: ``0`` = metrics computed over at least one valid record;
    ``2`` = nothing was measurable (empty dir / no records / all malformed /
    path not found) -- the reason is printed either way.
    """
    parser = argparse.ArgumentParser(
        prog="kairos.general_eval",
        description="Evaluate general (non-code) skeleton tasks from their "
                    "persisted run records: three-way pass rate, abstention "
                    "rate and per-criterion satisfaction. undecided is never "
                    "counted as a pass.",
    )
    parser.add_argument(
        "path",
        help="A workspace dir (searched for .kairos/skeleton-runs/) or a "
             "single run-record JSON file.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Emit the full metrics report as JSON.",
    )
    args = parser.parse_args(argv)

    report = evaluate_path(args.path)
    if args.json_output:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render_text(report) + "\n")
    sys.stdout.flush()
    return 0 if report["source"]["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(cli_main(sys.argv[1:]))
