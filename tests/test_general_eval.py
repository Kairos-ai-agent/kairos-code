"""Tests for kairos.general_eval (task-level, non-code evaluation).

Two kinds of test live here:

* the **benchmark** -- the real good/bad skeleton fixtures from
  ``tests/fixtures/skeleton/`` are driven through the shipped
  ``PromptWorker`` + ``CitationConsistencyVerifier`` into real persisted run
  records, then the metrics are asserted (passed 1 / failed 1 / undecided 0,
  and the citations verifier's own breakdown). Every number comes from those
  real runs, not from hand-written JSON;
* the **arithmetic + honesty** unit tests -- three-way counting, the
  abstention rate, criterion satisfaction, and the explicit reasons an empty
  directory / a missing directory / a malformed record must state instead of
  silently reporting 0% or 100%.

Nothing here touches the code loop or the user's real data: everything is
written under ``tmp_path``.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from kairos.general_eval import (
    cli_main,
    evaluate_path,
    evaluate_records,
    load_run_records,
    render_text,
)
from kairos.skeleton import (
    CitationConsistencyVerifier,
    DocSetWorkspace,
    PromptWorker,
    SkeletonRun,
    Task,
    Verdict,
    WorkerResult,
    run_task,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "skeleton"
DOCS_FIXTURE = FIXTURES / "docs"
GOOD_REPORT = FIXTURES / "report_cited.md"
BAD_REPORT = FIXTURES / "report_cannot_complete.md"
DOC_NAMES = ("alpha.md", "beta.md", "gamma.md")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_docs(root: Path) -> None:
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    for name in DOC_NAMES:
        shutil.copyfile(DOCS_FIXTURE / name, docs / name)


async def _run_fixture(root: Path, deliverable: str, run_id: str) -> SkeletonRun:
    """Drive one real fixture through the shipped worker + verifier, persist it."""
    workspace = DocSetWorkspace(root)
    run_dir = root / ".kairos" / "skeleton-runs"
    return await run_task(
        PromptWorker(generate=lambda prompt: deliverable),
        workspace,
        Task(instruction="读三份文档产出一页对比报告并自检引用", output_name="report.md"),
        CitationConsistencyVerifier(),
        run_dir=run_dir,
        run_id=run_id,
    )


def _make_run(passed, *, verifier="rubric", requires_human=False, evidence=None):
    return SkeletonRun(
        task=Task(instruction="do a thing", output_name="out.txt"),
        result=WorkerResult(ok=True, output="x"),
        verdict=Verdict(
            passed=passed, verifier=verifier, requires_human=requires_human,
            evidence=list(evidence or []),
        ),
    )


# ===========================================================================
# 1) benchmark: the real good/bad fixtures -> real run records -> metrics
# ===========================================================================


def test_fixture_pair_benchmark(tmp_path):
    """The real fixture pair yields passed 1 / failed 1 / undecided 0."""
    _seed_docs(tmp_path)
    good_text = GOOD_REPORT.read_text(encoding="utf-8")
    bad_text = BAD_REPORT.read_text(encoding="utf-8")

    async def _do():
        good = await _run_fixture(tmp_path, good_text, "goodfile")
        bad = await _run_fixture(tmp_path, bad_text, "badfile")
        return good, bad

    good, bad = asyncio.run(_do())

    # sanity: the *real runs* really did decide as the fixtures demand
    assert good.passed is True and good.verdict.verifier == "citations"
    assert bad.passed is False and bad.verdict.verifier == "citations"

    run_dir = tmp_path / ".kairos" / "skeleton-runs"
    files = sorted(p.name for p in run_dir.glob("skeleton-run-*.json"))
    assert files == ["skeleton-run-badfile.json", "skeleton-run-goodfile.json"]

    report = evaluate_path(tmp_path)
    assert report["source"]["status"] == "ok"
    assert report["source"]["records_loaded"] == 2
    assert report["source"]["records_malformed"] == 0
    assert report["source"]["search_mode"] == "run-dir"

    t = report["totals"]
    assert t["records"] == 2
    assert t["passed"] == 1
    assert t["failed"] == 1
    assert t["undecided"] == 0
    assert t["pass_rate"] == 0.5
    assert t["failed_rate"] == 0.5
    assert t["undecided_rate"] == 0.0
    # abstention is its own, separately-reported number -- and here it is 0
    assert t["abstention_rate"] == 0.0
    assert t["needs_human"] == 0 and t["abstained"] == 0

    c = report["criteria"]
    # 5 evidence rows per run: declared total + 3 resources + declared cited
    assert c["evidence_rows"] == 10
    assert c["satisfied"] == 9
    assert c["unsatisfied"] == 1
    assert c["unknown"] == 0
    assert c["satisfaction_rate"] == pytest.approx(0.9)

    # the citations verifier's own line matches the overall numbers exactly
    assert set(report["by_verifier"]) == {"citations"}
    cit = report["by_verifier"]["citations"]
    assert cit["records"] == 2
    assert cit["passed"] == 1
    assert cit["failed"] == 1
    assert cit["undecided"] == 0
    assert cit["pass_rate"] == 0.5
    assert cit["abstention_rate"] == 0.0
    assert cit["criteria"]["satisfied"] == 9
    assert cit["criteria"]["unsatisfied"] == 1

    # the one unmet criterion is exactly the bad report's "declared cited count"
    bad_criterion = next(
        r for r in cit["criteria"]["by_criterion"]
        if r == "declared cited count equals resources actually cited"
    )
    assert cit["criteria"]["by_criterion"][bad_criterion]["satisfied"] == 1
    assert cit["criteria"]["by_criterion"][bad_criterion]["unsatisfied"] == 1


def test_fixture_benchmark_cli_entrypoints(tmp_path, capsys):
    """Both CLI entry points print the same real metrics (human + --json)."""
    from kairos.cli import EXIT_OK, main as kairos_main

    _seed_docs(tmp_path)

    async def _do():
        await _run_fixture(tmp_path, GOOD_REPORT.read_text(encoding="utf-8"), "g1")
        await _run_fixture(tmp_path, BAD_REPORT.read_text(encoding="utf-8"), "b1")

    asyncio.run(_do())

    # (a) the self-contained module CLI
    assert cli_main([str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["passed"] == 1
    assert payload["totals"]["failed"] == 1
    assert payload["totals"]["abstention_rate"] == 0.0

    # (b) the wired `kairos skeleton eval` command
    code = kairos_main(["skeleton", "eval", str(tmp_path), "--json"])
    assert code == EXIT_OK
    payload2 = json.loads(capsys.readouterr().out)
    assert payload2["totals"] == payload["totals"]
    assert payload2["by_verifier"]["citations"]["records"] == 2


# ===========================================================================
# 2) three-way counting + abstention (arithmetic, over real Verdict objects)
# ===========================================================================


def test_undecided_is_never_counted_as_a_pass():
    records = [
        _make_run(True, verifier="citations"),
        _make_run(False, verifier="citations"),
        _make_run(None, verifier="rubric"),                    # abstained
        _make_run(None, verifier="human", requires_human=True),  # human gate
    ]
    report = evaluate_records(records)
    t = report["totals"]
    assert t["records"] == 4
    assert t["passed"] == 1
    assert t["failed"] == 1
    assert t["undecided"] == 2
    # the pass rate counts *only* passed -- never the two undecided ones
    assert t["pass_rate"] == 0.25
    assert t["undecided_rate"] == 0.5
    assert t["abstention_rate"] == 0.5
    assert t["needs_human"] == 1 and t["abstained"] == 1
    # three-way rates partition the whole
    assert t["passed_rate"] + t["failed_rate"] + t["undecided_rate"] == pytest.approx(1.0)

    assert set(report["by_verifier"]) == {"citations", "rubric", "human"}
    assert report["by_verifier"]["citations"]["pass_rate"] == 0.5
    assert report["by_verifier"]["rubric"]["abstention_rate"] == 1.0
    assert report["by_verifier"]["human"]["needs_human"] == 1


def test_criterion_satisfaction_excludes_unknown_rows():
    records = [
        _make_run(True, verifier="rubric", evidence=[
            {"criterion": "a", "satisfied": True},
            {"criterion": "a", "satisfied": True},
            {"criterion": "b", "satisfied": False},
            {"criterion": "c", "satisfied": None},   # undecided row
        ]),
    ]
    report = evaluate_records(records)
    c = report["criteria"]
    assert c["evidence_rows"] == 4
    assert c["satisfied"] == 2
    assert c["unsatisfied"] == 1
    assert c["unknown"] == 1
    # 2/(2+1) -- the undecided row is counted, then excluded from the rate
    assert c["satisfaction_rate"] == 0.6667
    assert c["by_criterion"]["a"]["satisfaction_rate"] == 1.0
    assert c["by_criterion"]["b"]["satisfaction_rate"] == 0.0
    assert c["by_criterion"]["c"]["satisfaction_rate"] is None
    assert c["by_criterion"]["c"]["unknown"] == 1


# ===========================================================================
# 3) honesty: empty / missing / malformed must state a reason, never 0 or 100
# ===========================================================================


def test_empty_directory_reports_no_records_not_zero(tmp_path):
    report = evaluate_path(tmp_path)
    assert report["source"]["status"] == "no-records"
    assert report["source"]["records_loaded"] == 0
    assert report["totals"]["pass_rate"] is None
    assert report["totals"]["abstention_rate"] is None
    assert report["criteria"]["satisfaction_rate"] is None

    text = render_text(report)
    assert "nothing was measured" in text
    assert "no skeleton-run-*.json found under" in text


def test_existing_run_dir_with_no_records_says_so(tmp_path):
    (tmp_path / ".kairos" / "skeleton-runs").mkdir(parents=True)
    report = evaluate_path(tmp_path)
    assert report["source"]["status"] == "no-records"
    assert report["source"]["search_mode"] == "run-dir"
    assert "holds no run records" in report["source"]["reason"]


def test_missing_path_reports_not_found(tmp_path):
    report = evaluate_path(tmp_path / "does-not-exist")
    assert report["source"]["status"] == "not-found"
    assert "does not exist" in report["source"]["reason"]
    assert cli_main([str(tmp_path / "nope")]) == 2


def test_malformed_records_are_counted_and_skipped(tmp_path):
    _seed_docs(tmp_path)

    async def _do():
        await _run_fixture(tmp_path, GOOD_REPORT.read_text(encoding="utf-8"), "real1")

    asyncio.run(_do())  # a real record

    run_dir = tmp_path / ".kairos" / "skeleton-runs"
    (run_dir / "skeleton-run-empty.json").write_text("{}", encoding="utf-8")
    (run_dir / "skeleton-run-broken.json").write_text("{not json", encoding="utf-8")

    report = evaluate_path(tmp_path)
    assert report["source"]["status"] == "ok"       # one real record survived
    assert report["source"]["records_loaded"] == 1
    assert report["source"]["records_malformed"] == 2
    reasons = {m["path"].rsplit("/", 1)[-1].rsplit("\\", 1)[-1]: m["reason"]
               for m in report["source"]["malformed"]}
    assert "unreadable JSON" in reasons["skeleton-run-broken.json"]
    assert "missing 'verdict'" in reasons["skeleton-run-empty.json"]

    assert report["totals"]["passed"] == 1
    assert report["totals"]["records"] == 1
    text = render_text(report)
    assert "malformed record(s) skipped" in report["source"]["reason"]
    assert "skipped malformed records" in text


def test_all_malformed_reports_all_malformed(tmp_path):
    run_dir = tmp_path / ".kairos" / "skeleton-runs"
    run_dir.mkdir(parents=True)
    (run_dir / "skeleton-run-x.json").write_text("{}", encoding="utf-8")
    (run_dir / "skeleton-run-y.json").write_text("[]", encoding="utf-8")

    report = evaluate_path(tmp_path)
    assert report["source"]["status"] == "all-malformed"
    assert report["source"]["records_loaded"] == 0
    assert report["totals"]["pass_rate"] is None
    assert "none could be parsed" in report["source"]["reason"]
    assert cli_main([str(tmp_path)]) == 2


def test_single_record_file_can_be_evaluated_directly(tmp_path):
    _seed_docs(tmp_path)

    async def _do():
        return await _run_fixture(
            tmp_path, GOOD_REPORT.read_text(encoding="utf-8"), "solo1")

    run = asyncio.run(_do())
    run_file = tmp_path / ".kairos" / "skeleton-runs" / f"skeleton-run-{run.run_id}.json"
    assert run_file.is_file()

    report = evaluate_path(run_file)
    assert report["source"]["search_mode"] == "file"
    assert report["source"]["records_loaded"] == 1
    assert report["totals"]["passed"] == 1
    assert report["totals"]["abstention_rate"] == 0.0


def test_load_run_records_discovers_nested_runs_recursively(tmp_path):
    """A run nested below the root (no conventional run dir) is still found."""
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "skeleton-run-nested.json").write_text(json.dumps({
        "run_id": "nested", "outcome": "passed", "attempts": 1,
        "task": {"instruction": "x", "output_name": "o.txt", "id": "t"},
        "result": {"ok": True, "output": "y"},
        "verdict": {"passed": True, "verifier": "tests", "evidence": [
            {"criterion": "test command exit 0", "satisfied": True}]},
        "history": [],
    }), encoding="utf-8")

    loaded = load_run_records(tmp_path)
    assert loaded["info"]["search_mode"] == "recursive-glob"
    assert loaded["info"]["records_loaded"] == 1

    report = evaluate_path(tmp_path)
    assert report["totals"]["passed"] == 1
    assert report["by_verifier"]["tests"]["records"] == 1
    assert report["by_verifier"]["tests"]["criteria"]["satisfied"] == 1
