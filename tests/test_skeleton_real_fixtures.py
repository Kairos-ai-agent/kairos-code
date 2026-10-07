"""Fixtures from a *real* end-to-end model run -- the non-code skeleton task.

Provenance (read this before trusting the fixtures)
---------------------------------------------------
These fixtures are **not** hand-written mock text. They are the verbatim
artifacts a real model (DeepSeek, via the configured provider) produced when
the ``kairos skeleton run --kind docs --verifier citations`` path was driven
end to end against three tiny source documents (alpha.md / beta.md / gamma.md)
in a scratch workspace outside the repo. Nothing here is synthesised; the
figures asserted below (40ms, 0.3 元, ...) are the ones the model actually
wrote, copied out of the recorded run JSON.

Two real deliverables came out of that run, and they are the two halves of the
defect this file pins:

* ``report_cited.md`` -- the **good** artifact. The model read all three docs
  and wrote a comparison table whose every row cites a source with
  ``[[alpha.md]]`` / ``[[beta.md]]`` / ``[[gamma.md]]``, closing with
  ``SELF_CHECK: citations=3/3 ok``. It was recorded as verdict
  ``passed=true`` with verifier ``citations`` (run id ``97b5abe9``).
  Taken from the run's ``outputs/report.md`` (byte-identical to the same run's
  ``result.output``).

* ``report_cannot_complete.md`` -- the **bad** artifact. The model answered
  "**状态：无法完成**" + a table of "数据缺失", then declared
  ``SELF_CHECK: citations=0/3 ok`` -- i.e. it *admits* it read nothing, yet the
  run was still recorded as ``outcome=passed`` by the weak one-line
  ``--check`` used at the time (run id ``38c1d48c``). Extracted from that run
  file's ``result.output`` field, verbatim.

A third run (``3f16893a``) is deliberately **not** copied here: it failed with
an HTTP ``401 Authentication Fails ... Your api key: ****lder is invalid`` --
that is where the invalid-key failure was first noticed, but its recorded error
string carries an API-key fragment and a request id, so no part of it belongs
in a fixture. It is only mentioned so the chronology is honest: the two runs
above are from the *same* provider/flow, before and after the key was fixed.

Why these fixtures exist
------------------------
The point of the pair is to stop a regression: a deliverable that says
"cannot complete / data missing" while self-reporting ``citations=0/N`` must
**not** come back as passing. The tests below re-read both real artifacts from
disk and run the shipped ``citations`` verifier over them, so a future refactor
that turns the bad one back into a pass fails here loudly.

Everything is read from these files (no inline copies of the model text), and
the fixtures carry no credential- or machine-identifying strings: see the
secret/absolute-path scan recorded in the task that added them.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kairos.skeleton import (
    CitationConsistencyVerifier,
    DocSetWorkspace,
    PromptWorker,
    Task,
    WorkerResult,
    run_task,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "skeleton"
DOCS_FIXTURE = FIXTURES / "docs"
GOOD_REPORT = FIXTURES / "report_cited.md"
BAD_REPORT = FIXTURES / "report_cannot_complete.md"

DOC_NAMES = ("alpha.md", "beta.md", "gamma.md")


def _read(path: Path) -> str:
    assert path.is_file(), f"fixture missing: {path}"
    return path.read_text(encoding="utf-8")


def _workspace_with_docs(tmp_path: Path) -> DocSetWorkspace:
    """Copy the three real source docs into a temp workspace (no repo writes)."""
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in DOC_NAMES:
        shutil.copyfile(DOCS_FIXTURE / name, docs / name)
    return DocSetWorkspace(tmp_path)


def _task() -> Task:
    return Task(instruction="读三份文档产出一页对比报告并自检引用", output_name="report.md")


# ==========================================================================
# 0) the fixtures really are the real model output (not invented)
# ==========================================================================

def test_source_docs_are_the_real_inputs():
    alpha = _read(DOCS_FIXTURE / "alpha.md")
    beta = _read(DOCS_FIXTURE / "beta.md")
    gamma = _read(DOCS_FIXTURE / "gamma.md")
    # the exact figures the model had to reconcile -- proves these are the real
    # inputs, not placeholders
    assert "40ms" in alpha and "1.2 元" in alpha
    assert "120ms" in beta and "0.3 元" in beta
    assert "75ms" in gamma and "0.6 元" in gamma


def test_good_report_is_the_real_model_artifact():
    text = _read(GOOD_REPORT)
    # real figures copied out of the model's own output...
    assert "约 40ms" in text
    assert "每百万 token 0.3 元" in text
    # ...one citation per source, and the self-check the prompt asked for
    for name in DOC_NAMES:
        assert f"[[{name}]]" in text
    assert "SELF_CHECK: citations=3/3 ok" in text


def test_bad_report_is_the_real_cannot_complete_artifact():
    text = _read(BAD_REPORT)
    # the model's own admission, from the run's ``result.output`` field
    assert "无法完成" in text
    assert "数据缺失" in text
    assert "SELF_CHECK: citations=0/3 ok" in text
    # it named the files (so the naive substring check saw them) but cited none
    for name in DOC_NAMES:
        assert name in text


# ==========================================================================
# 1) the shipped citations verifier over the real artifacts
# ==========================================================================

async def test_citations_verifier_rejects_the_real_cannot_complete_report(tmp_path):
    """The bad deliverable must FAIL -- this is the defect being pinned.

    The model's own table names alpha.md / beta.md / gamma.md, so a naive
    substring check is fooled. The verifier instead cross-checks the declared
    ``citations=0/3`` against the three resources the workspace really has, and
    the "declared cited count" row shows ``0`` reported vs ``3`` actually
    present -- the exact per-condition conclusion, not a vibe.
    """
    ws = _workspace_with_docs(tmp_path)
    ws.emit("report.md", _read(BAD_REPORT))

    verdict = await CitationConsistencyVerifier().verify(
        ws, _task(), WorkerResult(ok=True, output=""))

    assert verdict.passed is False
    assert verdict.verifier == "citations"
    assert verdict.reason.startswith("self-reported citations=0/3")
    assert "3 actually cited" in verdict.reason

    # the denominator is honest (the workspace really has 3 resources), so the
    # lie is isolated to the *cited* count
    total_row = next(e for e in verdict.evidence
                     if e["criterion"] == "declared total equals workspace resource count")
    assert total_row["satisfied"] is True
    assert total_row["reported"] == 3 and total_row["actual"] == 3

    cited_row = next(e for e in verdict.evidence
                     if e["criterion"] == "declared cited count equals resources actually cited")
    assert cited_row["satisfied"] is False
    assert cited_row["reported"] == 0 and cited_row["actual"] == 3

    # every resource row is "cited" only in the substring sense; they are all
    # satisfied here precisely because the verifier does NOT rely on them alone
    resource_rows = [e for e in verdict.evidence
                     if str(e["criterion"]).startswith("resource cited:")]
    assert len(resource_rows) == 3
    assert all(e["satisfied"] is True for e in resource_rows)


async def test_citations_verifier_passes_the_real_cited_report(tmp_path):
    ws = _workspace_with_docs(tmp_path)
    ws.emit("report.md", _read(GOOD_REPORT))

    verdict = await CitationConsistencyVerifier().verify(
        ws, _task(), WorkerResult(ok=True, output=""))

    assert verdict.passed is True
    assert verdict.verifier == "citations"
    assert verdict.score == pytest.approx(100.0)
    assert all(e["satisfied"] is True for e in verdict.evidence)


async def test_citations_verifier_abstains_when_the_self_report_line_is_gone(tmp_path):
    """No self-report to cross-check -> honest ``None``, never a silent pass.

    Existing tests cover the abstain branch with inline text; this one runs it
    over the *real* fixture with the ``SELF_CHECK`` line stripped, so the
    fixture-driven path is pinned too.
    """
    ws = _workspace_with_docs(tmp_path)
    stripped = "\n".join(ln for ln in _read(GOOD_REPORT).splitlines()
                         if "SELF_CHECK" not in ln)
    assert "citations=" not in stripped  # the line really is gone
    ws.emit("report.md", stripped)

    verdict = await CitationConsistencyVerifier().verify(
        ws, _task(), WorkerResult(ok=True, output=""))

    assert verdict.passed is None
    assert verdict.verifier == "citations"
    assert "cross-check" in verdict.reason


# ==========================================================================
# 2) end to end: a worker that emits the bad artifact cannot pass
# ==========================================================================

async def test_worker_emitting_the_real_cannot_complete_report_cannot_pass(tmp_path):
    """Worker -> citations verifier, with the deliverable read from the fixture.

    This is the whole point in one test: reproduce the real run -- a worker
    whose output is the model's own "cannot complete" text -- and assert the
    run does not come back ``passed``.
    """
    bad_text = _read(BAD_REPORT)
    ws = _workspace_with_docs(tmp_path)

    run = await run_task(
        PromptWorker(generate=lambda prompt: bad_text),
        ws, _task(), CitationConsistencyVerifier(),
    )

    assert run.passed is False
    assert run.outcome == "failed"
    assert run.verdict.verifier == "citations"
    # the recorded artifact is exactly the fixture text
    assert ws.read_output("report.md") == bad_text
