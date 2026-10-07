"""The general skeleton: Worker / Verifier / Workspace.

Two things this file proves:

(A) A **non-code** task -- "read three documents, produce a comparison report,
    self-check the citations" -- runs end to end through the skeleton, driven
    by a *fake* LLM (no network, no real model), with an ``assertion`` /
    ``tool_oracle`` verifier. No git, no pytest as the verification.
(B) The existing Coder / Reviewer are still intact and are *one*
    implementation of the same interfaces (via adapters), not the only shape
    that can exist -- the built-in ``loop_runner`` path still runs unchanged.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import pytest

from kairos.skeleton import (
    AssertionVerifier,
    CitationConsistencyVerifier,
    CoderWorker,
    DocSetWorkspace,
    HumanVerifier,
    PromptWorker,
    ProjectTestsVerifier,
    RepoWorkspace,
    ReviewerVerifier,
    RubricVerifier,
    SkeletonRun,
    Task,
    ToolOracleVerifier,
    Verdict,
    Verifier,
    VerifierRegistry,
    Worker,
    WorkerResult,
    Workspace,
    build_default_registry,
    resume_task,
    run_task,
    verdict_from_review,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

DOC_BODIES = {
    "alpha.md": "方案甲：延迟 30ms，成本 高，成熟度 高。",
    "beta.md": "方案乙：延迟 80ms，成本 低，成熟度 中。",
    "gamma.md": "方案丙：延迟 15ms，成本 中，成熟度 低。",
}


def _doc_workspace(tmp_path: Path) -> DocSetWorkspace:
    docs = tmp_path / "docs"
    docs.mkdir()
    for name, body in DOC_BODIES.items():
        (docs / name).write_text(body, encoding="utf-8")
    return DocSetWorkspace(tmp_path)


class FakeLLM:
    """A deterministic model stand-in. No network, no provider, no API key."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        names = re.findall(r"INPUT: (\S+)", prompt)
        rows = [f"- 方案结论 {i + 1}，依据 [[{n}]]" for i, n in enumerate(names)]
        return "\n".join(
            ["# 三方案对比报告", "", "## 对比", *rows, "",
             f"## 自检引用\nSELF_CHECK: citations={len(names)}/{len(names)} ok"]
        )


def _citation_check(workspace, task, result):
    """Assertion: every input cited + an explicit self-check line present."""
    text = workspace.read_output(task.output_name) or ""
    refs = workspace.resources()
    evidence = [
        {"criterion": f"cites {r}", "satisfied": f"[[{r}]]" in text}
        for r in refs
    ]
    evidence.append({"criterion": "self-check present", "satisfied": "SELF_CHECK" in text})
    evidence.append({"criterion": "at least three inputs read", "satisfied": len(refs) >= 3})
    return all(e["satisfied"] for e in evidence), evidence


# ==========================================================================
# 0) the interfaces exist and are honest about themselves
# ==========================================================================

def test_three_interfaces_are_abstract_contracts():
    assert issubclass(Worker, object) and getattr(Worker, "__abstractmethods__", None)
    assert getattr(Verifier, "__abstractmethods__", None)
    assert getattr(Workspace, "__abstractmethods__", None)
    assert {"run"} == set(Worker.__abstractmethods__)
    assert {"verify"} == set(Verifier.__abstractmethods__)


def test_workspaces_declare_their_capabilities():
    # the code workspace supports git + tests
    assert RepoWorkspace(REPO_ROOT).supports("git")
    assert RepoWorkspace(REPO_ROOT).supports("tests")
    # the document workspace must NOT claim git or tests
    doc_ws = DocSetWorkspace(REPO_ROOT)
    assert doc_ws.kind == "docs"
    assert not doc_ws.supports("git")
    assert not doc_ws.supports("tests")


def test_default_registry_registers_the_five_kinds():
    reg = build_default_registry()
    for name in ("tests", "assertion", "rubric", "human", "tool_oracle"):
        assert name in reg, f"{name} must be registered"
    # lookups are structured, not silent
    assert isinstance(reg.get("rubric"), Verifier)
    with pytest.raises(KeyError):
        reg.get("does-not-exist")


# ==========================================================================
# A) the non-code task
# ==========================================================================

async def test_non_code_task_runs_to_a_passing_verdict(tmp_path):
    """Read three docs -> comparison report -> self-check citations."""
    ws = _doc_workspace(tmp_path)
    assert ws.resources() == ["alpha.md", "beta.md", "gamma.md"]

    llm = FakeLLM()
    worker = PromptWorker(generate=llm)
    task = Task(
        instruction="读三份文档，产出一页对比报告并自检引用。",
        output_name="report.md",
    )
    verifier = AssertionVerifier(check=_citation_check)

    run = await run_task(worker, ws, task, verifier)

    assert run.passed is True
    assert run.result.ok is True
    report = ws.read_output("report.md")
    assert report is not None
    # the report cites every input and carries the self-check line
    for name in DOC_BODIES:
        assert f"[[{name}]]" in report, report
    assert "SELF_CHECK" in report
    # the fake LLM was actually driven from the workspace, not hard-coded
    assert llm.prompts and "INPUT: alpha.md" in llm.prompts[0]
    assert all(e["satisfied"] for e in run.verdict.evidence)
    assert run.verdict.verifier == "assertion"


async def test_non_code_task_uses_no_git_and_no_pytest(tmp_path, monkeypatch):
    """Hard proof the non-code path shells out to nothing."""

    def _boom(*a, **k):
        raise AssertionError("the non-code path must not spawn git/pytest")

    monkeypatch.setattr("subprocess.run", _boom)
    monkeypatch.setattr("subprocess.Popen", _boom)

    ws = _doc_workspace(tmp_path)
    worker = PromptWorker(generate=FakeLLM())
    task = Task(instruction="读三份文档并写报告", output_name="report.md")
    run = await run_task(worker, ws, task, AssertionVerifier(check=_citation_check))

    assert run.passed is True
    # exactly one artifact, written under outputs/; no .git anywhere
    assert ws.outputs() == ["outputs/report.md"]
    assert not (tmp_path / ".git").exists()
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()) == [
        "docs/alpha.md", "docs/beta.md", "docs/gamma.md", "outputs/report.md",
    ]


async def test_tool_oracle_independently_confirms_citations(tmp_path):
    """A second, independent probe re-reads the artifact from disk."""
    ws = _doc_workspace(tmp_path)
    worker = PromptWorker(generate=FakeLLM())
    task = Task(instruction="读三份文档并写报告", output_name="report.md")
    # first, produce the artifact
    await run_task(worker, ws, task, AssertionVerifier(check=_citation_check))

    def oracle(workspace, task, result):
        text = workspace.read_output(task.output_name) or ""
        seen = sorted(set(re.findall(r"\[\[(\w+\.md)\]\]", text)))
        expected = sorted(workspace.resources())
        evidence = [{"criterion": "oracle: every input cited", "satisfied": seen == expected},
                    {"criterion": "oracle: self-check line", "satisfied": "SELF_CHECK" in text}]
        return all(e["satisfied"] for e in evidence), evidence

    verdict = await ToolOracleVerifier(oracle=oracle).verify(ws, task, WorkerResult(ok=True, output=""))
    assert verdict.passed is True
    assert verdict.verifier == "tool_oracle"
    assert all(e["satisfied"] for e in verdict.evidence)


async def test_failing_assertion_yields_a_structured_rejection(tmp_path):
    """A wrong report is rejected with per-criterion evidence -- not an LLM score."""
    ws = _doc_workspace(tmp_path)
    worker = PromptWorker(generate=lambda prompt: "# report with no citations")
    task = Task(instruction="读三份文档并写报告", output_name="report.md")
    run = await run_task(worker, ws, task, AssertionVerifier(check=_citation_check))

    assert run.passed is False
    assert run.verdict.passed is False
    failed = [e for e in run.verdict.evidence if e["satisfied"] is False]
    assert len(failed) >= 3  # the three missing citations
    assert any("cites alpha.md" == e["criterion"] for e in failed)


# ==========================================================================
# 1) verifier kinds behave as advertised
# ==========================================================================

async def test_tests_verifier_declines_on_a_non_code_workspace(tmp_path):
    """Registered, but honest: a doc set has no tests, so it declines (None)."""
    ws = _doc_workspace(tmp_path)
    verdict = await ProjectTestsVerifier().verify(ws, Task(instruction="x"), WorkerResult(ok=True))
    assert verdict.passed is None
    assert verdict.verifier == "tests"
    assert verdict.evidence[0]["satisfied"] is False


async def test_human_verifier_blocks_on_approval(tmp_path):
    ws = _doc_workspace(tmp_path)
    verdict = await HumanVerifier().verify(ws, Task(instruction="x"), WorkerResult(ok=True))
    assert verdict.passed is None
    assert verdict.requires_human is True


async def test_rubric_verifier_scores_criteria_structurally(tmp_path):
    ws = _doc_workspace(tmp_path)
    task = Task(instruction="x", output_name="report.md")
    ws.emit("report.md", "has [[alpha.md]] and SELF_CHECK but no beta")
    rubric = RubricVerifier(
        criteria=[
            ("cites alpha", lambda w, t, r: "[[alpha.md]]" in (w.read_output(t.output_name) or "")),
            ("cites beta", lambda w, t, r: "[[beta.md]]" in (w.read_output(t.output_name) or "")),
            ("self-check", lambda w, t, r: "SELF_CHECK" in (w.read_output(t.output_name) or "")),
        ],
        threshold=0.99,
    )
    verdict = await rubric.verify(ws, task, WorkerResult(ok=True))
    assert verdict.passed is False          # 2/3 < 1.0
    assert verdict.score == pytest.approx(66.7, abs=0.1)
    assert len(verdict.evidence) == 3
    assert [e["satisfied"] for e in verdict.evidence] == [True, False, True]


async def test_expression_assertion_is_supported(tmp_path):
    ws = _doc_workspace(tmp_path)
    task = Task(instruction="x", output_name="report.md")
    ws.emit("report.md", "SELF_CHECK: citations=3/3 ok")
    verdict = await AssertionVerifier(
        check="'SELF_CHECK' in output and len(artifacts) == 1"
    ).verify(ws, task, WorkerResult(ok=True, output="SELF_CHECK: citations=3/3 ok",
                                    artifacts=["outputs/report.md"]))
    assert verdict.passed is True


def test_registry_can_be_swapped_at_runtime():
    reg = VerifierRegistry()
    reg.register(AssertionVerifier(check=lambda w, t, r: True, name="mine"))
    assert reg.names() == ["mine"]
    reg.unregister("mine")
    assert reg.names() == []


# ==========================================================================
# B) Coder / Reviewer as one implementation (regression)
# ==========================================================================

class _StubAgent:
    """Stands in for a KairosAgent: same ``run(task, plan_mode=)`` contract."""

    def __init__(self, response: str, name: str = "stub"):
        self._response = response
        self.name = name
        self.tasks = []

    async def run(self, task, plan_mode: bool = False) -> str:
        self.tasks.append(task)
        return self._response


async def test_coder_worker_wraps_a_coder_like_agent(tmp_path):
    coder = _StubAgent("edited src/app.py and added a test", name="Coder")
    worker = CoderWorker(coder, project_id="proj-1")
    assert isinstance(worker, Worker)

    result = await worker.run(RepoWorkspace(tmp_path), Task(instruction="fix the bug"))
    assert isinstance(result, WorkerResult)
    assert result.ok is True
    assert "edited src/app.py" in result.output
    # it handed the Coder a real AgentTask carrying the project id
    assert coder.tasks and coder.tasks[0].context.get("project_id") == "proj-1"


async def test_reviewer_verifier_maps_review_to_a_structured_verdict(tmp_path):
    ws = RepoWorkspace(tmp_path)
    task = Task(instruction="fix the bug")
    result = WorkerResult(ok=True, output="diff")

    # a bug-only Reviewer verdict (the current default shape) -> reject
    bad = ReviewerVerifier(_StubAgent(json.dumps(
        {"has_bugs": True, "bugs": [{"file": "a.py", "line": 3,
                                     "description": "off-by-one", "fix": "use <="}],
         "summary": "1 bug"})))
    assert isinstance(bad, Verifier)
    verdict = await bad.verify(ws, task, result)
    assert verdict.passed is False
    assert verdict.verifier == "reviewer"
    assert any(e.get("detail", "").startswith("off-by-one") for e in verdict.evidence)

    # no bugs -> pass (score 100 clears the 85 bar)
    ok = ReviewerVerifier(_StubAgent(json.dumps(
        {"has_bugs": False, "bugs": [], "summary": "clean"})))
    verdict2 = await ok.verify(ws, task, result)
    assert verdict2.passed is True


def test_verdict_from_review_is_structured_not_a_bare_score():
    verdict = verdict_from_review(
        {"approve": True, "score": 90, "issues": [], "summary": "ok",
         "tests_evidence": {"ran": True, "command": "pytest -q"}},
    )
    assert isinstance(verdict, Verdict)
    assert verdict.passed is True
    criteria = {e["criterion"] for e in verdict.evidence}
    assert "reviewer approves" in criteria
    assert "tests evidence reported" in criteria


def test_existing_coder_and_reviewer_are_intact():
    """The real roles still exist and are still KairosAgents."""
    from kairos.agents.base import KairosAgent
    from kairos.agents.roles import Coder, Reviewer

    assert issubclass(Coder, KairosAgent)
    assert issubclass(Reviewer, KairosAgent)
    # and each is expressible as a skeleton implementation through its adapter
    assert CoderWorker.__mro__[1] is Worker
    assert ReviewerVerifier.__mro__[1] is Verifier


async def test_legacy_loop_path_still_runs(tmp_path, monkeypatch):
    """Regression: the original Coder->Reviewer loop is unchanged.

    A one-round approve still converges, exactly as ``test_loop_review_feedback``
    pins -- the skeleton did not touch it.
    """
    from kairos.core.message_bus import MessageBus
    from kairos.loop import loop_runner as lr
    from kairos.loop import review_loop as rl

    async def fake_precheck(session, workspace, round_no, bus):
        return "", []

    monkeypatch.setattr(lr, "_run_precheck", fake_precheck)

    class StubProject:
        id = "p1"
        requirements = "x"
        work_dir = str(tmp_path)
        workspace = str(tmp_path)

    coder = _StubAgent("done")
    reviewer = _StubAgent(json.dumps({"has_bugs": False, "bugs": [], "summary": "clean"}))
    session = rl.LoopSession(
        project=StubProject(), message_bus=MessageBus(),
        coder=coder, reviewer=reviewer, persistence=None,
    )
    session.plan_decision = "approve"
    session.best_of_n = 1

    await lr.run_loop(session, "fix the bug")

    assert session.round == 1
    assert session.last_approve is True
    assert session.history and session.history[-1]["review"]["approve"] is True


# ==========================================================================
# C) cross-review fixes: retry policy, safe eval, platform path, honest
#    registry, human gate, run-scoped output, workspace binding, CLI wiring
# ==========================================================================


class _CountingWorker(Worker):
    """A worker that counts how many times it was asked to run."""

    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, workspace, task):
        self.calls += 1
        return WorkerResult(ok=True, output=f"attempt {self.calls}")


class _ScriptedVerifier(Verifier):
    """Returns a scripted sequence of verdicts (repeats the last once exhausted)."""

    name = "scripted"

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)

    async def verify(self, workspace, task, result):
        if len(self.verdicts) > 1:
            return self.verdicts.pop(0)
        return self.verdicts[0]


# ---- (1) retry only on an explicit failure --------------------------------

async def test_abstained_verdict_is_not_retried(tmp_path):
    """`passed=None` means "no verifier could decide" -- do not spin on it."""
    ws = _doc_workspace(tmp_path)
    worker = _CountingWorker()
    verifier = _ScriptedVerifier([Verdict(passed=None, reason="no verifier could decide")])

    run = await run_task(worker, ws, Task(instruction="x"), verifier, max_attempts=5)

    assert worker.calls == 1          # abstention is NOT retried
    assert run.attempts == 1
    assert run.undecided is True
    assert run.outcome == "undecided"


async def test_failed_verdict_retries_up_to_max_attempts(tmp_path):
    ws = _doc_workspace(tmp_path)
    worker = _CountingWorker()
    verifier = _ScriptedVerifier([
        Verdict(passed=False, reason="nope"),
        Verdict(passed=False, reason="still no"),
        Verdict(passed=True, reason="ok"),
    ])

    run = await run_task(worker, ws, Task(instruction="x"), verifier, max_attempts=3)

    assert worker.calls == 3
    assert run.attempts == 3
    assert run.passed is True


async def test_requires_human_stops_immediately(tmp_path):
    ws = _doc_workspace(tmp_path)
    worker = _CountingWorker()
    run = await run_task(worker, ws, Task(instruction="x"), HumanVerifier(), max_attempts=5)

    assert worker.calls == 1
    assert run.blocked_on_human is True
    assert run.outcome == "needs_human"


# ---- (2) string assertions cannot escape the evaluator ---------------------

async def test_string_assertion_rejects_attribute_and_import_escapes(tmp_path):
    """Escape expressions are **rejected**, never executed."""
    ws = _doc_workspace(tmp_path)
    task = Task(instruction="x", output_name="report.md")
    marker = tmp_path / "escaped.txt"
    target = marker.as_posix()

    escapes = [
        "().__class__.__base__.__subclasses__()",
        "(1).__class__",
        f"__import__('pathlib').Path({target!r}).write_text('pwned') or True",
    ]
    for expr in escapes:
        verdict = await AssertionVerifier(check=expr).verify(
            ws, task, WorkerResult(ok=True)
        )
        assert verdict.passed is False, expr
        assert "rejected" in verdict.reason, (expr, verdict.reason)

    assert not marker.exists(), "an escape expression was actually executed"


async def test_string_assertion_still_accepts_simple_conditions(tmp_path):
    ws = _doc_workspace(tmp_path)
    task = Task(instruction="x", output_name="report.md")
    ok = await AssertionVerifier(check="'SELF_CHECK' in output and len(artifacts) >= 1").verify(
        ws, task, WorkerResult(ok=True, output="SELF_CHECK ok", artifacts=["a"]),
    )
    assert ok.passed is True
    bad = await AssertionVerifier(check="'MISSING' in output").verify(
        ws, task, WorkerResult(ok=True, output="SELF_CHECK ok"),
    )
    assert bad.passed is False


async def test_string_assertion_rejects_plain_attribute_access_not_just_dunder(tmp_path):
    """Regression guard: **any** attribute access is rejected, not only dunder.

    ``_ALLOWED_AST_NODES`` omits ``ast.Attribute`` on purpose. ``x.__class__``
    is the famous escape vector, but ``x.some_attr`` is the very same door, so
    the check must NOT be narrowed to dunder-only names (e.g. to make
    ``result.output`` parse). The supported way to reach into the scope objects
    is a *callable* check, which is unrestricted and safe. If someone loosens
    the string evaluator, this test fails.
    """
    ws = _doc_workspace(tmp_path)
    task = Task(instruction="x", output_name="report.md")

    # plain attribute access -> rejected *for being attribute access*
    for expr in ("result.output", "workspace.root", "task.meta"):
        verdict = await AssertionVerifier(check=expr).verify(ws, task, WorkerResult(ok=True))
        assert verdict.passed is False, expr
        assert "rejected" in verdict.reason, (expr, verdict.reason)
        assert "attribute" in verdict.reason.lower(), (expr, verdict.reason)

    # a method call on an attribute is rejected too (at the call check, since
    # the callee is not a whitelisted bare name)
    for expr in ("output.upper()", "(1).__class__"):
        verdict = await AssertionVerifier(check=expr).verify(ws, task, WorkerResult(ok=True))
        assert verdict.passed is False, expr
        assert "rejected" in verdict.reason, (expr, verdict.reason)

    # the sanctioned alternative: a callable may do exactly the same work
    def check(workspace, task, result):
        return (result.output or "").startswith("ok")

    ok = await AssertionVerifier(check=check).verify(
        ws, task, WorkerResult(ok=True, output="ok!"),
    )
    assert ok.passed is True


# ---- (3) cross-platform test-command detection -----------------------------

def test_detector_prefers_each_platforms_venv_layout(tmp_path, monkeypatch):
    from kairos.skeleton.verifiers import _detect_test_command

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    # only a POSIX layout exists -> bin/python, never a hard-coded Scripts path
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    monkeypatch.setattr("kairos.test_command._OS_NAME", "posix")
    cmd = _detect_test_command(tmp_path)
    assert cmd[:3] == [".venv/bin/python", "-m", "pytest"], cmd

    # a Windows layout now also exists -> nt prefers Scripts/python.exe
    (tmp_path / ".venv" / "Scripts").mkdir()
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr("kairos.test_command._OS_NAME", "nt")
    assert _detect_test_command(tmp_path)[:3] == [".venv/Scripts/python.exe", "-m", "pytest"]

    # posix still prefers bin/python when both are present
    monkeypatch.setattr("kairos.test_command._OS_NAME", "posix")
    assert _detect_test_command(tmp_path)[0] == ".venv/bin/python"


def test_detector_falls_back_to_global_pytest_then_none(tmp_path, monkeypatch):
    from kairos.skeleton.verifiers import _detect_test_command

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setattr("kairos.test_command._OS_NAME", "posix")

    # no venv + pytest not on PATH -> None (no bare guess)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert _detect_test_command(tmp_path) is None

    # no venv + pytest on PATH -> the global console script
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/pytest" if name == "pytest" else None
    )
    assert _detect_test_command(tmp_path) == ["pytest", "-q"]

    # nothing to detect at all
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _detect_test_command(empty) is None


# ---- (4) the default registry is honest about which kinds work -------------

def test_default_registry_is_honest_about_readiness():
    reg = build_default_registry()
    describe = reg.describe()

    # `tests` and `citations` can decide on their own; `human` is a gate, not a
    # decider
    assert reg.deciding_names() == ["citations", "tests"]
    assert describe["tests"]["ready"] is True
    assert describe["citations"]["ready"] is True
    assert describe["citations"]["decides"] is True
    assert describe["human"]["ready"] is True
    assert describe["human"]["decides"] is False

    # registered by name but NOT ready until configured
    for name in ("assertion", "rubric", "tool_oracle"):
        assert describe[name]["ready"] is False, name

    # a caller that only wants ready kinds can drop the unconfigured stubs
    assert build_default_registry(include_unconfigured=False).names() == [
        "citations", "human", "tests",
    ]


async def test_default_tool_oracle_abstains_instead_of_faking_a_verdict(tmp_path):
    reg = build_default_registry()
    verdict = await reg.verify(
        "tool_oracle", _doc_workspace(tmp_path),
        Task(instruction="x"), WorkerResult(ok=True),
    )
    assert verdict.passed is None
    assert "no oracle configured" in verdict.reason


async def test_a_configured_tool_oracle_becomes_ready_and_decides(tmp_path):
    ws = _doc_workspace(tmp_path)
    ws.emit("report.md", "has [[alpha.md]]")
    task = Task(instruction="x", output_name="report.md")
    oracle = lambda w, t, r: ("[[alpha.md]]" in (w.read_output(t.output_name) or ""))
    v = ToolOracleVerifier(oracle=oracle)
    assert v.ready is True
    verdict = await v.verify(ws, task, WorkerResult(ok=True))
    assert verdict.passed is True


# ---- (5) the human gate is persistable, observable and resumable -----------

async def test_human_gate_persists_publishes_and_resumes(tmp_path):
    from kairos.core.message_bus import MessageBus

    ws = _doc_workspace(tmp_path)
    worker = PromptWorker(generate=FakeLLM())
    task = Task(instruction="读三份文档并写报告", output_name="report.md")
    run_dir = tmp_path / "runs"
    bus = MessageBus()

    run = await run_task(worker, ws, task, HumanVerifier(), run_dir=run_dir, bus=bus)
    assert run.blocked_on_human and run.outcome == "needs_human"

    files = list(run_dir.glob("skeleton-run-*.json"))
    assert len(files) == 1

    loaded = SkeletonRun.load(files[0])
    assert loaded.blocked_on_human
    assert loaded.run_id == run.run_id

    topics = {m.topic for m in bus.get_history()}
    assert "skeleton.run.verified" in topics
    assert "skeleton.run.completed" in topics

    resumed = await resume_task(loaded, approved=True, reason="looks good",
                                bus=bus, run_dir=run_dir)
    assert resumed.passed is True and resumed.outcome == "passed"
    assert resumed.run_id == run.run_id
    # the same record is overwritten -- no second run file
    assert len(list(run_dir.glob("skeleton-run-*.json"))) == 1
    assert any(m.topic == "skeleton.run.resumed" for m in bus.get_history())


async def test_human_rejection_can_rerun_the_worker(tmp_path):
    from kairos.core.message_bus import MessageBus

    ws = _doc_workspace(tmp_path)
    worker = PromptWorker(generate=FakeLLM())
    task = Task(instruction="读三份文档并写报告", output_name="report.md")
    run = await run_task(worker, ws, task, HumanVerifier(), bus=MessageBus())

    resumed = await resume_task(
        run, approved=False, reason="请补齐引用",
        worker=worker, workspace=ws,
        verifier=AssertionVerifier(check=_citation_check),
    )
    assert resumed.passed is True
    assert "HUMAN FEEDBACK" in resumed.task.instruction


# ---- (6) repo artifacts do not land in the repository root -----------------

def test_repo_workspace_outputs_are_run_scoped(tmp_path):
    ws = RepoWorkspace(tmp_path)
    assert ws.output_dir != tmp_path
    assert ".kairos" in str(ws.output_dir) and "skeleton-runs" in str(ws.output_dir)

    ref = ws.emit("notes.txt", "hello")
    assert ref.startswith(".kairos/skeleton-runs/")
    assert (tmp_path / ref).is_file()
    assert not (tmp_path / "notes.txt").exists()

    ws2 = RepoWorkspace(tmp_path, run_id="fixed123")
    assert ws2.output_dir == tmp_path / ".kairos" / "skeleton-runs" / "fixed123"


# ---- (7) the Coder adapter hands the workspace root to the agent -----------
#
# Three layers are proven here, and they are NOT the same:
#   * the *explicit tool binding* -- the real Coder edits through tools whose
#     root is set at construction (`FileEditTool(allowed_root=X)`), and
#     CoderWorker re-points those tools at the workspace it is handed. This is
#     the default, parallel-safe path: it does not touch the process cwd, so two
#     workers may run concurrently.
#   * the *serial cwd fallback* -- only for a bare agent with nothing to
#     re-point: it sees the workspace as the process cwd during run, and a
#     nested/concurrent entry into that fallback is refused (fail-loud kept).
#   * the *reversibility* of the binding -- the re-point is undone after the
#     run, so a reused instance no longer points at the run's workspace.
# What is still NOT proven (no model / no network): that the full real
# `kairos.agents.roles.Coder` LLM loop edits inside X. See the honest boundary
# note on the tool-level test below.

async def test_coder_worker_sets_process_cwd_for_a_cwd_reading_agent(tmp_path):
    """A cwd-reading agent gets the workspace as the process cwd during run.

    Scope: this proves the *serial cwd fallback* works, i.e. that during the run
    ``os.getcwd()`` is the workspace and is restored after. It does NOT by
    itself prove the real Coder's edits land in X -- that is the tool-level
    binding proven by the tests below.
    """

    class _CwdCoder:
        name = "cwd-coder"

        def __init__(self) -> None:
            self.seen = None

        async def run(self, task, plan_mode=False):
            self.seen = os.getcwd()
            return "ok"

    workspace_x = tmp_path / "workspaceX"
    workspace_x.mkdir()
    coder = _CwdCoder()
    before = os.getcwd()

    result = await CoderWorker(coder).run(
        RepoWorkspace(workspace_x), Task(instruction="do work in X")
    )

    assert result.ok is True
    assert coder.seen is not None
    assert Path(coder.seen).resolve() == workspace_x.resolve()
    # the process cwd is restored afterwards
    assert Path(os.getcwd()).resolve() == Path(before).resolve()


async def test_nested_serial_workspace_binding_is_rejected(tmp_path):
    """Re-entrant cwd binding must raise, not silently clobber the cwd."""
    from kairos.skeleton.adapters import serial_workspace_cwd

    x = tmp_path / "X"
    x.mkdir()
    outer_cwd = os.getcwd()
    with serial_workspace_cwd(str(x)):
        assert Path(os.getcwd()).resolve() == x.resolve()
        with pytest.raises(RuntimeError):
            with serial_workspace_cwd(str(x)):
                pass
        # the rejected inner entry did not change the cwd underneath us
        assert Path(os.getcwd()).resolve() == x.resolve()
    assert Path(os.getcwd()).resolve() == Path(outer_cwd).resolve()


async def test_concurrent_coder_runs_one_binds_the_other_errors(tmp_path):
    """Two overlapping CoderWorker runs: one holds the cwd, the other errors.

    Exactly one worker may hold the process cwd; the second must report a
    failure (``ok=False`` with the re-entrancy reason) rather than quietly
    running in the first worker's directory.
    """
    class _SlowCwdCoder:
        name = "slow-cwd-coder"

        async def run(self, task, plan_mode=False):
            await asyncio.sleep(0)  # yield so the second worker interleaves
            return os.getcwd()

    x = tmp_path / "X"
    y = tmp_path / "Y"
    x.mkdir()
    y.mkdir()

    results = await asyncio.gather(
        CoderWorker(_SlowCwdCoder()).run(RepoWorkspace(x), Task(instruction="a")),
        CoderWorker(_SlowCwdCoder()).run(RepoWorkspace(y), Task(instruction="b")),
    )

    assert sorted(r.ok for r in results) == [False, True]
    loser = next(r for r in results if not r.ok)
    assert "re-entrant" in (loser.error or "")
    # the winner really saw its own workspace, not the other's
    assert Path(next(r for r in results if r.ok).output).resolve() in {x.resolve(), y.resolve()}


def test_real_coder_file_tool_writes_into_the_given_root(tmp_path):
    """The real Coder edits through ``FileEditTool``; its root IS the workspace.

    No LLM: we drive the actual tool the Coder is constructed with
    (``kairos.tools.file_edit.FileEditTool(allowed_root=X)``) and assert the
    bytes land in X. This is the primitive the Coder edits with.
    """
    from kairos.tools.file_edit import FileEditTool

    x = tmp_path / "X"
    x.mkdir()
    tool = FileEditTool(allowed_root=x)

    res = asyncio.run(tool.execute(path="proof.txt", content="written by the coder tool"))

    assert res.success is True, res.error
    assert (x / "proof.txt").read_text(encoding="utf-8") == "written by the coder tool"


async def test_coder_worker_rebinds_real_coder_tools_to_the_workspace(tmp_path):
    """An agent whose *real* edit tool was built for Y ends up editing X.

    The Coder's write tools are bound at construction (orchestrator:
    ``FileEditTool(allowed_root=coder_root)``). ``CoderWorker`` re-points those
    tools at the workspace it is handed, so the actual editing primitive writes
    into X -- never into the tree the tools were built for.

    The re-point is scoped to the run: after ``run`` returns the binding is
    undone (``WorkspaceBinding.restore``), so the tool points back at Y. The
    proof that the *edit* landed in X is therefore taken from the filesystem
    (the file exists in X, not Y), not from the tool's post-run state.

    Honest boundary: the agent body here just calls its own tool once. This
    proves the *binding + real tool + real filesystem* layer end to end; it
    does NOT exercise the full real ``kairos.agents.roles.Coder`` LLM loop
    (that needs a model, which these tests deliberately do not have).
    """
    from kairos.tools.file_edit import FileEditTool

    y = tmp_path / "Y"
    x = tmp_path / "X"
    y.mkdir()
    x.mkdir()

    class _ToolDrivenCoder:
        name = "tool-driven-coder"

        def __init__(self, tools):
            self.tools = tools
            self.root_during_run = None

        async def run(self, task, plan_mode=False):
            # what the tool was pointed at *while running*
            self.root_during_run = Path(self.tools[0]._allowed_root).resolve()
            res = await self.tools[0].execute(path="edited.txt", content="edit in X")
            assert res.success, res.error
            return "edited"

    coder = _ToolDrivenCoder([FileEditTool(allowed_root=y)])
    result = await CoderWorker(coder).run(RepoWorkspace(x), Task(instruction="edit"))

    assert result.ok is True
    # during the run the wrapper had re-pointed the real tool at X...
    assert coder.root_during_run == x.resolve()
    # ...so the file landed in X, not in the tree the tool was built for
    assert (x / "edited.txt").is_file()
    assert not (y / "edited.txt").exists()
    # ...and the re-point did not outlive the run: the tool is back to Y
    assert Path(coder.tools[0]._allowed_root).resolve() == y.resolve()


class _RootTool:
    """A minimal edit tool carrying its own root, like the real ``FileEditTool``.

    It has no dependency on the process cwd -- it writes through its own
    ``_allowed_root`` -- which is exactly the property that makes a worker using
    it safe to run in parallel.
    """

    def __init__(self, root):
        self._allowed_root = root

    def write(self, name: str, content: str) -> str:
        target = Path(self._allowed_root) / name
        target.write_text(content, encoding="utf-8")
        return str(target)


class _ParallelCoder:
    """A tool-driven coder whose edits go through a re-pointable tool root."""

    name = "parallel-coder"

    def __init__(self, tool: _RootTool):
        self.tools = [tool]
        self.cwd_during_run = None

    async def run(self, task, plan_mode=False):
        # nothing here reads the process cwd -- only the tool's own root
        self.cwd_during_run = os.getcwd()
        await asyncio.sleep(0)  # yield so a second worker can interleave
        self.tools[0].write("proof.txt", f"written for {task.id}")
        return str(self.tools[0]._allowed_root)


async def test_two_parallel_coder_workers_each_bind_their_own_workspace(tmp_path):
    """Two CoderWorkers run concurrently, each in its own workspace, no re-entrant error.

    With the explicit tool binding in force the run does **not** touch the
    process cwd, so ``asyncio.gather`` of two workers is allowed. Before this
    change both runs did ``with serial_workspace_cwd(root)`` and the second one
    raised the re-entrancy ``RuntimeError`` (reporting ``ok=False``).
    """
    x = tmp_path / "X"
    y = tmp_path / "Y"
    x.mkdir()
    y.mkdir()
    cwd_before = os.getcwd()

    cx = _ParallelCoder(_RootTool(tmp_path / "unboundX"))
    cy = _ParallelCoder(_RootTool(tmp_path / "unboundY"))

    results = await asyncio.gather(
        CoderWorker(cx).run(RepoWorkspace(x), Task(id="a", instruction="a")),
        CoderWorker(cy).run(RepoWorkspace(y), Task(id="b", instruction="b")),
    )

    # both succeeded -- neither worker surfaced a re-entrancy RuntimeError
    assert [r.ok for r in results] == [True, True]
    assert not any("re-entrant" in (r.error or "") for r in results)

    # each worker saw its own tool root and wrote into its own tree
    assert Path(results[0].output).resolve() == x.resolve()
    assert Path(results[1].output).resolve() == y.resolve()
    assert (x / "proof.txt").read_text(encoding="utf-8") == "written for a"
    assert (y / "proof.txt").read_text(encoding="utf-8") == "written for b"

    # neither worker mutated the process cwd -- that is *why* they can overlap
    assert Path(cx.cwd_during_run).resolve() == Path(cwd_before).resolve()
    assert Path(cy.cwd_during_run).resolve() == Path(cwd_before).resolve()
    assert Path(os.getcwd()).resolve() == Path(cwd_before).resolve()


async def test_bare_agent_without_workdir_or_tools_falls_back_to_serial_cwd(tmp_path):
    """A bare agent (no work_dir, no re-pointable tool) still works via the chdir fallback.

    Nothing can be bound, so the run opts into ``serial_workspace_cwd``: the
    agent sees the workspace as the process cwd during the run. Nesting that
    fallback still raises -- the fail-loud guard is preserved, not removed.
    """
    from kairos.skeleton.adapters import serial_workspace_cwd

    x = tmp_path / "X"
    x.mkdir()

    class _BareAgent:
        # deliberately: no work_dir / cwd / project_dir, and no `tools` attribute
        name = "bare"

        def __init__(self):
            self.seen_cwd = None
            self.nested_error = None

        async def run(self, task, plan_mode=False):
            self.seen_cwd = os.getcwd()
            try:
                with serial_workspace_cwd(os.getcwd()):
                    pass
            except RuntimeError as exc:
                self.nested_error = str(exc)
            return "done"

    cwd_before = os.getcwd()
    agent = _BareAgent()
    result = await CoderWorker(agent).run(RepoWorkspace(x), Task(instruction="x"))

    assert result.ok is True
    # the fallback really changed the cwd while running...
    assert Path(agent.seen_cwd).resolve() == x.resolve()
    # ...and a nested entry into it is still refused, loudly
    assert agent.nested_error and "re-entrant" in agent.nested_error
    # ...and the cwd is restored afterwards
    assert Path(os.getcwd()).resolve() == Path(cwd_before).resolve()


def test_bind_agent_to_workspace_is_reversible(tmp_path):
    """``bind`` reports whether it took, and ``restore`` undoes it exactly."""
    from kairos.skeleton.adapters import bind_agent_to_workspace

    a = tmp_path / "A"
    a.mkdir()
    original = str(tmp_path)

    class _Agent:
        name = "agent"

        def __init__(self):
            self.work_dir = original
            self.tools = [_RootTool(original)]

    agent = _Agent()
    binding = bind_agent_to_workspace(agent, str(a))

    assert binding.bound is True
    assert binding.agent_attrs == ["work_dir"]
    assert binding.tools_rebound == 1
    assert Path(agent.work_dir).resolve() == a.resolve()
    assert Path(agent.tools[0]._allowed_root).resolve() == a.resolve()

    binding.restore()
    # exact restore: back to the pre-bind values, no longer pointing at A
    assert agent.work_dir == original
    assert Path(agent.tools[0]._allowed_root).resolve() == Path(original).resolve()
    assert Path(agent.work_dir).resolve() != a.resolve()
    # idempotent: a second restore is a no-op
    binding.restore()
    assert agent.work_dir == original


def test_bind_reports_false_for_a_bare_agent(tmp_path):
    """A bare agent (nothing to re-point) reports ``bound=False`` -- the chdir signal."""
    from kairos.skeleton.adapters import bind_agent_to_workspace

    class _Bare:
        name = "bare"

    a = tmp_path / "A"
    a.mkdir()
    binding = bind_agent_to_workspace(_Bare(), str(a))

    assert binding.bound is False
    assert binding.agent_attrs == [] and binding.tools_rebound == 0
    binding.restore()  # no-op, must not raise


async def test_coder_worker_restores_binding_after_the_run(tmp_path):
    """Run against A, then the same instance no longer points at A."""
    a = tmp_path / "A"
    a.mkdir()
    original = str(tmp_path)

    class _Agent:
        name = "agent"

        def __init__(self):
            self.work_dir = original
            self.tools = [_RootTool(original)]
            self.root_during_run = None

        async def run(self, task, plan_mode=False):
            self.root_during_run = Path(self.tools[0]._allowed_root).resolve()
            self.tools[0].write("out.txt", "in A")
            return "ok"

    agent = _Agent()
    result = await CoderWorker(agent).run(RepoWorkspace(a), Task(instruction="x"))

    assert result.ok is True
    # bound to A while running -- the write landed there
    assert agent.root_during_run == a.resolve()
    assert (a / "out.txt").is_file()
    # after the run work_dir / tool root are back to their originals
    assert Path(agent.work_dir).resolve() == Path(original).resolve()
    assert Path(agent.tools[0]._allowed_root).resolve() == Path(original).resolve()
    assert Path(agent.work_dir).resolve() != a.resolve()


# ---- (8) the CLI wires the skeleton up (end to end, no network) ------------

def _write_docs(ws_root: Path) -> Path:
    docs = ws_root / "docs"
    docs.mkdir()
    for name, body in DOC_BODIES.items():
        (docs / name).write_text(body, encoding="utf-8")
    return docs


def test_skeleton_cli_runs_a_non_code_task_end_to_end(tmp_path):
    from kairos.cli import EXIT_OK, main

    _write_docs(tmp_path)
    check = ("'[[alpha.md]]' in output and '[[beta.md]]' in output "
             "and '[[gamma.md]]' in output and 'SELF_CHECK' in output")

    code = main([
        "skeleton", "run",
        "--task", "读三份文档，产出一页对比报告并自检引用。",
        "--workspace", str(tmp_path),
        "--verifier", "assertion",
        "--check", check,
        "--out-name", "report.md",
        "--generator", "offline",
        "--json",
    ])

    assert code == EXIT_OK
    report = tmp_path / "outputs" / "report.md"
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    for name in DOC_BODIES:
        assert f"[[{name}]]" in text
    assert "SELF_CHECK" in text
    # the run was persisted under the workspace
    assert list((tmp_path / ".kairos" / "skeleton-runs").glob("skeleton-run-*.json"))
    # nothing leaked into the workspace root: only the inputs and the run/output
    # directories exist, and no run file or artifact sits at the repo root
    assert {p.name for p in tmp_path.iterdir()} == {"docs", "outputs", ".kairos"}
    assert not list(tmp_path.glob("skeleton-run-*.json"))
    assert not list(tmp_path.glob("*.json"))
    assert not (tmp_path / "report.md").exists()


def test_skeleton_cli_repo_run_artifacts_land_under_a_run_subdir(tmp_path):
    """``--kind repo`` writes under ``.kairos/skeleton-runs/<run-id>/``.

    The run id names the subdirectory; the repository root gains nothing but
    ``.kairos`` -- the artifact is not written into the root.
    """
    from kairos.cli import EXIT_OK, main

    _write_docs(tmp_path)
    before = {p.name for p in tmp_path.iterdir()}

    code = main([
        "skeleton", "run",
        "--task", "读文档产报告",
        "--workspace", str(tmp_path),
        "--kind", "repo",
        "--verifier", "assertion",
        "--check", "'SELF_CHECK' in output",
        "--out-name", "report.md",
        "--generator", "offline",
        "--json",
    ])

    assert code == EXIT_OK
    runs_dir = tmp_path / ".kairos" / "skeleton-runs"
    run_subdirs = [p for p in runs_dir.iterdir() if p.is_dir()]
    assert run_subdirs, "no run-id subdirectory was created"
    assert any((d / "report.md").is_file() for d in run_subdirs)
    # the repo root gained nothing new except `.kairos`
    assert {p.name for p in tmp_path.iterdir()} - before == {".kairos"}
    assert not (tmp_path / "report.md").exists()


def test_skeleton_cli_persists_and_resumes_a_human_gate(tmp_path):
    from kairos.cli import EXIT_NEEDS_CONFIRMATION, EXIT_OK, main

    _write_docs(tmp_path)
    code = main([
        "skeleton", "run",
        "--task", "读三份文档并写报告",
        "--workspace", str(tmp_path),
        "--verifier", "human",
        "--out-name", "report.md",
        "--generator", "offline",
        "--json",
    ])
    assert code == EXIT_NEEDS_CONFIRMATION

    files = list((tmp_path / ".kairos" / "skeleton-runs").glob("skeleton-run-*.json"))
    assert len(files) == 1

    code2 = main(["skeleton", "resume", "--run", str(files[0]),
                  "--approve", "--reason", "ok", "--json"])
    assert code2 == EXIT_OK
    loaded = json.loads(files[0].read_text(encoding="utf-8"))
    assert loaded["outcome"] == "passed"


# ---- (9) the workspace's document *contents* really reach the worker -------
#
# The real-model failure this guards against: ``--workspace <dir>`` pointed
# straight at the document folder, ``DocSetWorkspace`` looked in ``<dir>/docs``,
# found nothing, handed the model an empty context, and the model honestly
# answered "no documents were provided -- cannot complete". A passing verdict
# from a weak ``--check`` then blessed it. These tests pin both halves.

def test_docset_workspace_accepts_the_docs_dir_given_directly(tmp_path):
    """``--workspace <docs-dir>`` (no nested ``docs/``) still finds the inputs."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "alpha.md").write_text("延迟 40ms", encoding="utf-8")

    # pointed straight at the folder of documents -- ``<docs>/docs`` is absent
    ws = DocSetWorkspace(docs)
    assert not (docs / "docs").exists()
    assert ws.resources() == ["alpha.md"]
    assert "40ms" in ws.as_prompt_context()


def test_docset_workspace_keeps_the_docs_subdir_layout(tmp_path):
    """The conventional layout (``<root>/docs``) is unchanged by the fallback."""
    ws = _doc_workspace(tmp_path)
    assert ws.resources() == ["alpha.md", "beta.md", "gamma.md"]


def test_docset_resources_exclude_its_own_outputs(tmp_path):
    """A deliverable is not an input: ``outputs/`` never shows up in resources."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "alpha.md").write_text("延迟 40ms", encoding="utf-8")
    ws = DocSetWorkspace(docs)  # root == docs, so outputs/ sits inside the root
    ws.emit("report.md", "hello")
    assert ws.resources() == ["alpha.md"]


def test_docset_resources_exclude_the_run_record_dir(tmp_path):
    """``.kairos`` run records are the harness's own, not workspace inputs."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "alpha.md").write_text("延迟 40ms", encoding="utf-8")
    runs = docs / ".kairos" / "skeleton-runs"
    runs.mkdir(parents=True)
    (runs / "skeleton-run-abc123.json").write_text("{}", encoding="utf-8")

    ws = DocSetWorkspace(docs)  # root == docs, so .kairos sits inside the root
    assert ws.resources() == ["alpha.md"]


async def test_prompt_worker_feeds_workspace_document_bodies(tmp_path):
    """The fake generator captures the prompt: the *bodies* must be in it."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "alpha.md").write_text("# alpha\n- 延迟：约 40ms\n", encoding="utf-8")
    (docs / "beta.md").write_text("# beta\n- 成本：每百万 token 0.3 元\n", encoding="utf-8")
    (docs / "gamma.md").write_text("# gamma\n- 部署：无需服务器\n", encoding="utf-8")
    ws = DocSetWorkspace(tmp_path)

    prompts: list[str] = []

    def gen(prompt: str) -> str:
        prompts.append(prompt)
        return "x"

    await PromptWorker(generate=gen).run(
        ws, Task(instruction="读三份文档产出一页对比报告", output_name="report.md")
    )

    assert prompts, "the generator was never driven"
    prompt = prompts[0]
    # the exact figures from the documents -- not just their names
    assert "40ms" in prompt
    assert "0.3 元" in prompt
    assert "无需服务器" in prompt
    for name in ("alpha.md", "beta.md", "gamma.md"):
        assert f"INPUT: {name}" in prompt


async def test_prompt_worker_reports_an_unreadable_resource(tmp_path):
    """A resource that cannot be read is stated in the prompt, not dropped."""
    ws = _doc_workspace(tmp_path)
    prompts: list[str] = []
    worker = PromptWorker(generate=lambda p: prompts.append(p) or "x")
    await worker.run(
        ws,
        Task(instruction="读文档", output_name="report.md",
             inputs=["alpha.md", "does-not-exist.md"]),
    )
    prompt = prompts[0]
    assert "INPUT: alpha.md" in prompt
    assert "does-not-exist.md" in prompt
    assert "unreadable" in prompt


async def test_prompt_worker_truncates_a_giant_workspace_and_says_so(tmp_path):
    """The context is capped; a cut is named, never silent."""
    docs = tmp_path / "docs"
    docs.mkdir()
    # 'Z'/'Q' appear nowhere in the prompt template, so counting them counts
    # only inlined body characters.
    (docs / "a.md").write_text("Z" * 200, encoding="utf-8")
    (docs / "b.md").write_text("Q" * 200, encoding="utf-8")
    ws = DocSetWorkspace(tmp_path)

    prompts: list[str] = []
    worker = PromptWorker(generate=lambda p: prompts.append(p) or "x",
                          max_context_chars=50)
    await worker.run(ws, Task(instruction="x", output_name="report.md"))

    prompt = prompts[0]
    assert "[TRUNCATED]" in prompt
    assert "50-char cap" in prompt
    assert prompt.count("Z") == 50          # first doc cut exactly at the cap
    assert "INPUT: b.md" not in prompt      # second doc omitted entirely
    assert "b.md" in prompt                 # ... but named in the truncation note
    assert "Q" not in prompt                # no byte of the omitted doc leaks


def test_as_prompt_context_ceiling_bounds_the_inlined_bodies(tmp_path):
    """``max_chars`` bounds the total body characters that get inlined."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "big1.md").write_text("x" * 5000, encoding="utf-8")
    (docs / "big2.md").write_text("y" * 5000, encoding="utf-8")
    ws = DocSetWorkspace(tmp_path)

    ctx = ws.as_prompt_context(max_chars=120)
    assert "[TRUNCATED]" in ctx
    assert "x" * 120 in ctx        # the first body is inlined up to the cap
    assert "x" * 121 not in ctx    # ... and not one character past it
    assert "INPUT: big2.md" not in ctx   # the second body is omitted entirely
    assert "big2.md" in ctx              # ... but named in the truncation note


def test_as_prompt_context_of_an_empty_workspace_is_not_silently_blank(tmp_path):
    """No resources -> an explicit note, so a model is never handed ''."""
    ws = DocSetWorkspace(tmp_path)  # no docs/ and no files at all
    ctx = ws.as_prompt_context()
    assert ctx.strip()
    assert "no readable inputs" in ctx


# ---- (10) the citations verifier: self-report vs the real resource count ---

def _cite_task(name: str = "report.md") -> Task:
    return Task(instruction="读文档并写报告", output_name=name)


async def test_citations_verifier_fails_a_zero_citation_report(tmp_path):
    """The exact real-model deliverable: 'cannot complete' + citations=0/3."""
    ws = _doc_workspace(tmp_path)  # three real resources
    ws.emit("report.md",
            "状态：无法完成。数据缺失。\n\nSELF_CHECK: citations=0/3 ok\n")

    verdict = await CitationConsistencyVerifier().verify(
        ws, _cite_task(), WorkerResult(ok=True, output=""))

    assert verdict.passed is False
    assert verdict.verifier == "citations"
    # the declared total matches reality (3), so the *cited* count is the lie
    total_row = next(e for e in verdict.evidence
                     if e["criterion"] == "declared total equals workspace resource count")
    assert total_row["reported"] == 3 and total_row["actual"] == 3
    # every resource is reported "not cited"
    resource_rows = [e for e in verdict.evidence
                     if str(e["criterion"]).startswith("resource cited:")]
    assert len(resource_rows) == 3
    assert all(e["satisfied"] is False for e in resource_rows)


async def test_citations_verifier_passes_when_every_resource_is_cited(tmp_path):
    ws = _doc_workspace(tmp_path)
    ws.emit("report.md",
            "| 方案 | 延迟 |\n|---|---|\n"
            "| 甲 [[alpha.md]] | 30ms |\n"
            "| 乙 [[beta.md]] | 80ms |\n"
            "| 丙 [[gamma.md]] | 15ms |\n\n"
            "SELF_CHECK: citations=3/3 ok\n")

    verdict = await CitationConsistencyVerifier().verify(
        ws, _cite_task(), WorkerResult(ok=True, output=""))

    assert verdict.passed is True
    assert all(e["satisfied"] for e in verdict.evidence)


async def test_citations_verifier_abstains_without_a_self_report(tmp_path):
    """No self-report -> an honest None, never a silent pass."""
    ws = _doc_workspace(tmp_path)
    ws.emit("report.md", "读了三份文档并写了报告，但没有写自检行。\n")

    verdict = await CitationConsistencyVerifier().verify(
        ws, _cite_task(), WorkerResult(ok=True, output=""))

    assert verdict.passed is None
    assert verdict.verifier == "citations"
    assert "cross-check" in verdict.reason
    assert verdict.evidence[0]["satisfied"] is None


async def test_citations_verifier_flags_a_declared_total_that_disagrees(tmp_path):
    """A denominator that does not match the workspace is caught by itself."""
    ws = _doc_workspace(tmp_path)  # three resources
    ws.emit("report.md", "见 [[alpha.md]] [[beta.md]]\nSELF_CHECK: citations=2/5 ok\n")

    verdict = await CitationConsistencyVerifier().verify(
        ws, _cite_task(), WorkerResult(ok=True, output=""))

    assert verdict.passed is False
    total_row = next(e for e in verdict.evidence
                     if e["criterion"] == "declared total equals workspace resource count")
    assert total_row["satisfied"] is False and total_row["reported"] == 5 \
        and total_row["actual"] == 3


async def test_citations_verifier_is_not_tied_to_any_file_names(tmp_path):
    """Different resources, no alpha/beta/gamma anywhere -- still decides."""
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("north.txt", "south.txt"):
        (docs / name).write_text("内容", encoding="utf-8")
    ws = DocSetWorkspace(tmp_path)
    ws.emit("report.md", "结论见 [[north.txt]] 与 [[south.txt]]\n"
                         "SELF_CHECK: citations=2/2 ok\n")

    verdict = await CitationConsistencyVerifier().verify(
        ws, _cite_task(), WorkerResult(ok=True, output=""))

    assert verdict.passed is True


async def test_citations_verifier_abstains_when_the_workspace_has_no_resources(tmp_path):
    ws = DocSetWorkspace(tmp_path)  # nothing to cross-check against
    ws.emit("report.md", "SELF_CHECK: citations=0/0 ok\n")

    verdict = await CitationConsistencyVerifier().verify(
        ws, _cite_task(), WorkerResult(ok=True, output=""))

    assert verdict.passed is None
    assert "no resources" in verdict.reason


def test_default_registry_includes_the_citations_verifier():
    reg = build_default_registry()
    assert "citations" in reg
    assert reg.get("citations").ready is True
    assert "citations" in reg.deciding_names()


async def test_citations_verifier_is_reachable_through_the_registry(tmp_path):
    ws = _doc_workspace(tmp_path)
    ws.emit("report.md", "数据缺失\nSELF_CHECK: citations=0/3 ok\n")
    verdict = await build_default_registry().verify(
        "citations", ws, _cite_task(), WorkerResult(ok=True))
    assert verdict.passed is False


async def test_run_task_rejects_a_cannot_complete_deliverable_end_to_end(tmp_path):
    """The whole point: worker -> citations verifier, no weak checkbox."""
    ws = _doc_workspace(tmp_path)

    def hopeless_generator(prompt: str) -> str:
        # ignores the (now non-empty) prompt and answers like the real model did
        return "状态：无法完成。本次会话未提供文档内容。\nSELF_CHECK: citations=0/3 ok\n"

    run = await run_task(
        PromptWorker(generate=hopeless_generator), ws, _cite_task(),
        CitationConsistencyVerifier(),
    )
    assert run.passed is False
    assert run.outcome == "failed"
    assert run.verdict.verifier == "citations"


def test_skeleton_cli_citations_verifier_reads_a_docs_folder_given_directly(tmp_path, capsys):
    """``--workspace <docs-dir> --verifier citations`` runs end to end."""
    from kairos.cli import EXIT_OK, main

    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("one.md", "two.md"):
        (docs / name).write_text(f"# {name}\n- 内容\n", encoding="utf-8")

    code = main([
        "skeleton", "run",
        "--task", "读文档产出一页对比报告",
        "--workspace", str(docs),   # straight at the document folder
        "--kind", "docs",
        "--verifier", "citations",
        "--out-name", "report.md",
        "--generator", "offline",
        "--run-dir", str(tmp_path / "runs"),
    ])

    assert code == EXIT_OK
    assert "passed" in capsys.readouterr().out
    report = (docs / "outputs" / "report.md").read_text(encoding="utf-8")
    assert "citations=2/2" in report


def test_skeleton_cli_help_mentions_the_citations_verifier(capsys):
    from kairos.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["skeleton", "run", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "citations" in out
