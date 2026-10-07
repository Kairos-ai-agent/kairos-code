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

import json
import re
from pathlib import Path

import pytest

from kairos.skeleton import (
    AssertionVerifier,
    CoderWorker,
    DocSetWorkspace,
    HumanVerifier,
    PromptWorker,
    ProjectTestsVerifier,
    RepoWorkspace,
    ReviewerVerifier,
    RubricVerifier,
    Task,
    ToolOracleVerifier,
    Verdict,
    Verifier,
    VerifierRegistry,
    Worker,
    WorkerResult,
    Workspace,
    build_default_registry,
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
        check="'SELF_CHECK' in workspace.read_output(task.output_name)"
    ).verify(ws, task, WorkerResult(ok=True))
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
