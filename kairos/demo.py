"""``kairos demo`` — see the gate in about a minute, with no API key.

The product's promise is procedural: *the Coder writes, the Reviewer gates, the
ledger records*. A new user cannot see any of that until they have a working
provider — so most people evaluate Kairos by reading about it instead of
watching it. This module fixes that with a run that needs **no key and no
network**:

  1. it writes a tiny but real repo (``relay.py`` + ``test_relay.py`` + git);
  2. it runs the **real** LoopReview engine against a **scripted** model, so the
     Coder's tool calls (file writes, test runs) really execute in a sandbox;
  3. the Reviewer's verdicts are derived by *reading the file* — two bugs, then
     one, then none — so the round-by-round story is genuine, not narrated;
  4. it verifies the final state by running the repo's own tests;
  5. it emits a Gate Report (the same artifact a real run produces).

It runs with ``KAIROS_SKIP_WORKTREES=1`` (one working tree) because a
single-agent walkthrough needs the Reviewer and the final verification to see
the Coder's edits; the role-isolated worktrees are for parallel work
(best-of-N / teams) and stay available outside the demo.

Honesty rules this module follows:
  * the scripted model is stated up front — the demo never pretends to call a
    model, and the cost ledger really is $0 because nothing was billed;
  * the tests that "pass" at the end are executed by the real tool layer;
  * the gate is not bypassed: rejections come from the same calibration and
    normalisation path a live Reviewer goes through.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kairos import cost as cost_mod
from kairos import gate_report as gate_mod
from kairos.llm.base import LLMConfig
from kairos.llm.scripted import (
    ScriptedProvider,
    conversation_text,
    last_role,
    text_response,
    tool_response,
    write_file_call,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_PROJECT_NAME = "demo-relay"
DEMO_REQUIREMENT = (
    "Make relay.py retry a 503 with exponential backoff, honour the Retry-After "
    "header, and never make one retry too many."
)
DEFAULT_TIMEOUT_S = 90

# --------------------------------------------------------------------------
# The mini repo the demo works on. Three deliberate bugs, each of them
# observable by one of the repo's own tests.
# --------------------------------------------------------------------------
RELAY_V1 = '''"""Retry planner for a flaky HTTP client — the math only, no sleeping."""
from __future__ import annotations

MAX_RETRIES = 3
BASE_DELAY = 0.5
RETRY_STATUSES = (429, 500, 503, 504)


def should_retry(status: int) -> bool:
    """True when the HTTP status is worth retrying."""
    return status in RETRY_STATUSES


def plan_delays(max_retries: int = MAX_RETRIES,
                retry_after: float | None = None) -> list[float]:
    """Return the delay to wait before each retry attempt."""
    delays: list[float] = []
    for attempt in range(max_retries + 1):
        delays.append(BASE_DELAY * (2 ** -attempt))
    if retry_after is not None:
        # NOTE: retry_after is read but never applied to the last delay.
        pass
    return delays
'''

RELAY_V2 = '''"""Retry planner for a flaky HTTP client — the math only, no sleeping."""
from __future__ import annotations

MAX_RETRIES = 3
BASE_DELAY = 0.5
RETRY_STATUSES = (429, 500, 503, 504)


def should_retry(status: int) -> bool:
    """True when the HTTP status is worth retrying."""
    return status in RETRY_STATUSES


def plan_delays(max_retries: int = MAX_RETRIES,
                retry_after: float | None = None) -> list[float]:
    """Return the delay to wait before each retry attempt."""
    delays: list[float] = []
    for attempt in range(max_retries):
        delays.append(BASE_DELAY * (2 ** attempt))
    if retry_after is not None:
        # NOTE: retry_after is read but never applied to the last delay.
        pass
    return delays
'''

RELAY_V3 = '''"""Retry planner for a flaky HTTP client — the math only, no sleeping."""
from __future__ import annotations

MAX_RETRIES = 3
BASE_DELAY = 0.5
RETRY_STATUSES = (429, 500, 503, 504)


def should_retry(status: int) -> bool:
    """True when the HTTP status is worth retrying."""
    return status in RETRY_STATUSES


def plan_delays(max_retries: int = MAX_RETRIES,
                retry_after: float | None = None) -> list[float]:
    """Return the delay to wait before each retry attempt."""
    delays: list[float] = []
    for attempt in range(max_retries):
        delays.append(BASE_DELAY * (2 ** attempt))
    if retry_after is not None and delays:
        delays[-1] = max(delays[-1], float(retry_after))
    return delays
'''

TEST_RELAY = '''"""The demo repo's own tests — they fail until relay.py is actually fixed."""
from relay import plan_delays


def test_delays_grow_with_each_attempt():
    delays = plan_delays(3)
    assert delays == sorted(delays), f"delays should grow, got {delays}"
    assert delays[0] < delays[-1], f"delays should grow, got {delays}"


def test_retry_cap_is_respected():
    delays = plan_delays(3)
    assert len(delays) == 3, f"expected exactly 3 attempts, got {len(delays)}"


def test_retry_after_is_honoured():
    delays = plan_delays(3, retry_after=2.5)
    assert delays[-1] >= 2.5, f"Retry-After ignored, got {delays}"


if __name__ == "__main__":
    # Fallback runner so the demo can verify its own work without pytest.
    import sys
    import traceback

    failed = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"PASS {_name}")
            except AssertionError:
                failed += 1
                print(f"FAIL {_name}")
                traceback.print_exc()
    sys.exit(1 if failed else 0)
'''

README_MD = """# relay-demo

A deliberately tiny repo used by `kairos demo` to show the review gate.

* `relay.py` — the retry planner the Coder edits.
* `test_relay.py` — three tests that encode the requirement.

Run the tests yourself:

    python -m pytest -q test_relay.py     # or: python test_relay.py
"""

RELAY_VERSIONS = {1: RELAY_V1, 2: RELAY_V2, 3: RELAY_V3}

DEMO_PLAN = (
    "Plan:\n"
    "1. read relay.py and test_relay.py to see the current contract\n"
    "2. fix the backoff math (the exponent is negated; the retry cap is off by one)\n"
    "3. apply the Retry-After header to the final delay\n"
    "4. run the repo tests after each change\n"
)

# --------------------------------------------------------------------------
# Narration (en/zh — the demo is the first thing a new user sees)
# --------------------------------------------------------------------------
DEMO_STRINGS: Dict[str, Dict[str, str]] = {
    "hdr": {
        "en": "Kairos demo — the review gate, in about a minute, with no API key.",
        "zh": "Kairos 演示 —— 约一分钟看懂审查门禁，不需要任何 API Key。",
    },
    "hdr.plan": {
        "en": "A scripted model plays the Coder and the Reviewer; the loop, the "
              "gate and the ledger are the real thing.",
        "zh": "由脚本扮演 Coder 与 Reviewer；循环、门禁与账本都是真实实现。",
    },
    "step1": {"en": "[1/6] building a tiny repo (relay.py + test_relay.py)",
              "zh": "[1/6] 生成一个小仓库（relay.py + test_relay.py）"},
    "step2": {"en": "[2/6] no API key needed — the real loop meets a scripted model",
              "zh": "[2/6] 不需要 API Key —— 真实循环 + 脚本模型"},
    "step3": {"en": "[3/6] running the loop: Coder → Reviewer → gate",
              "zh": "[3/6] 运行循环：Coder → Reviewer → 门禁"},
    "step4": {"en": "[4/6] verifying the final state with the repo's own tests",
              "zh": "[4/6] 用仓库自带测试验证最终状态"},
    "step5": {"en": "[5/6] generating the Gate Report",
              "zh": "[5/6] 生成门禁报告"},
    "step6": {"en": "[6/6] done in {s}s",
              "zh": "[6/6] 完成，用时 {s}s"},
    "round.line": {
        "en": "      R{n} {mark} {verdict} · score {score} · {bugs}",
        "zh": "      R{n} {mark} {verdict} · 评分 {score} · {bugs}",
    },
    "round.approved": {"en": "approved", "zh": "通过"},
    "round.rejected": {"en": "rejected", "zh": "打回"},
    "bugs.none": {"en": "no bugs", "zh": "无 bug"},
    "bugs.n": {"en": "{n} bug(s): {first}", "zh": "{n} 个问题：{first}"},
    "tests.ok": {"en": "      {summary}", "zh": "      {summary}"},
    "tests.failed": {"en": "      tests FAILED: {summary}", "zh": "      测试未通过：{summary}"},
    "report": {"en": "Report: {path}", "zh": "报告：{path}"},
    "badge": {"en": "Badge:  {badge}", "zh": "徽章：{badge}"},
    "honest": {
        "en": "This run used a scripted model, so it needed no key and made no "
              "network calls — the $0 in the ledger is real. Point Kairos at a "
              "real provider (Settings in the web UI, or data/settings.json) and "
              "the same loop runs your model: same gate, same scores, same ledger.",
        "zh": "本次使用脚本模型，因此不需要 Key、也不联网 —— 账本里的 $0 是真实的。"
              "把 Kairos 指向真实模型（Web 设置或 data/settings.json），同一套循环就会跑你的模型："
              "门禁、评分、账本完全一致。",
    },
    "workspace": {"en": "Workspace kept: {path}", "zh": "已保留工作目录：{path}"},
    "preflight.fail": {"en": "preflight failed: {detail}", "zh": "预检失败：{detail}"},
    "timeout": {"en": "the loop did not finish within {s}s", "zh": "循环在 {s}s 内没有结束"},
    "no.rounds": {
        "en": "the loop produced no rounds — nothing to report",
        "zh": "循环没有产生任何轮次 —— 没有可报告的内容",
    },
}


def t(key: str, lang: str = "en", **params: Any) -> str:
    entry = DEMO_STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(lang) or entry.get("en") or key
    for name, value in params.items():
        text = text.replace("{" + name + "}", str(value))
    return text


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------
@dataclass
class DemoResult:
    ok: bool
    seconds: float
    project_id: str = ""
    workspace: str = ""
    report_path: str = ""
    rounds: int = 0
    rejected_rounds: int = 0
    first_pass: bool = False
    final_score: int = 0
    cost_usd: float = 0.0
    coder_calls: int = 0
    reviewer_calls: int = 0
    tests_ok: bool = False
    tests_summary: str = ""
    checkpoints: int = 0
    cleaned: bool = False
    badge: str = ""
    detail: str = ""
    preflight: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Scripts
# --------------------------------------------------------------------------
def _line_of(path: Path, needle: str) -> Optional[int]:
    """1-based line number of the first line containing ``needle`` (None if absent)."""
    try:
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if needle in line:
                return index
    except OSError:
        return None
    return None


def _round_of(messages: List[Any]) -> int:
    """Current round number, taken as the max mentioned in the conversation.

    Both the Coder prompt ("Round N of the loop...") and the Reviewer prompt
    ("Review the Coder's work for round N") carry it, and rounds only increase,
    so the max is the current one even when the agent keeps prior turns in memory.
    """
    rounds = [int(m) for m in re.findall(r"round\s+(\d+)", conversation_text(messages), re.I)]
    return max(rounds) if rounds else 1


def build_coder_script(repo: Path):
    """A 3-round screenplay: write buggy code, fix the math, honour Retry-After."""
    steps: Dict[int, int] = {}
    answers = {
        1: "Implemented the retry planner in relay.py (exponential backoff).",
        2: "Fixed the delay calculation and the retry count in relay.py.",
        3: "Applied Retry-After to the final delay in relay.py.",
    }

    def script(messages: List[Any], call_no: int, tools: Optional[List[dict]] = None):
        # Plan mode hides tools on the first turn: answer with prose, no edits.
        if not tools:
            return text_response(DEMO_PLAN)
        round_no = _round_of(messages)
        step = steps.get(round_no, 0)
        version = RELAY_VERSIONS.get(round_no, RELAY_V3)

        if step == 0:
            steps[round_no] = 1
            return tool_response(
                [write_file_call("relay.py", version)],
                content=f"Writing relay.py for round {round_no}.",
            )
        steps[round_no] = 2
        return text_response(answers.get(round_no, "Verified relay.py."))

    return script


def build_reviewer_script(repo: Path):
    """A Reviewer that reads relay.py and reports only what is actually broken."""

    def script(messages: List[Any], call_no: int, tools: Optional[List[dict]] = None):
        try:
            text = (repo / "relay.py").read_text(encoding="utf-8")
        except OSError:
            text = ""
        bugs: List[Dict[str, Any]] = []

        if "2 ** -attempt" in text:
            bugs.append({
                "file": "relay.py", "line": _line_of(repo / "relay.py", "2 ** -attempt"),
                "description": "the exponent is negated, so the delay shrinks with every "
                               "attempt instead of growing (test_delays_grow_with_each_attempt fails)",
                "fix": "use BASE_DELAY * (2 ** attempt)",
            })
        if "max_retries + 1" in text:
            bugs.append({
                "file": "relay.py", "line": _line_of(repo / "relay.py", "max_retries + 1"),
                "description": "range(max_retries + 1) performs one retry too many "
                               "(test_retry_cap_is_respected fails)",
                "fix": "use range(max_retries)",
            })
        if "retry_after" in text and "delays[-1] = max(" not in text:
            bugs.append({
                "file": "relay.py",
                "line": _line_of(repo / "relay.py", "if retry_after is not None"),
                "description": "Retry-After is read but never applied to the last delay "
                               "(test_retry_after_is_honoured fails)",
                "fix": "delays[-1] = max(delays[-1], float(retry_after))",
            })

        if bugs:
            summary = f"{len(bugs)} bug(s) in relay.py; the repo's tests fail with the current code."
        else:
            summary = "No bugs found. The retry planner matches the requirement and the tests pass."
        verdict = {"has_bugs": bool(bugs), "bugs": bugs, "summary": summary}
        return text_response(json.dumps(verdict, ensure_ascii=False))

    return script


# --------------------------------------------------------------------------
# Wiring helpers
# --------------------------------------------------------------------------
def _models_config_path() -> Path:
    """The bundled model tiers (no keys needed — the demo replaces the provider)."""
    try:
        from kairos.config.settings import settings
        candidate = Path(settings.data_dir).resolve() / ".." / "kairos" / "config" / "models_config.yaml"
        if candidate.exists():
            return candidate
    except Exception:
        pass
    from kairos import config as pkg_config
    return Path(pkg_config.__file__).parent / "models_config.yaml"


def _scripted_config(model: str) -> LLMConfig:
    """Config for a scripted actor.

    The api_key is a deliberate placeholder: ``KairosAgent._run_impl`` refuses to
    run an agent whose config has no key ("No API key for <model>"), and the demo
    must exercise the real agent loop. Nothing is ever sent anywhere — the
    provider is local and ignores credentials.
    """
    return LLMConfig(provider="scripted", model=model,
                     api_key="scripted-demo-no-key-needed", timeout=30)


def _attach_scripted(agent: Any, provider: ScriptedProvider) -> None:
    """Swap an agent's provider for a scripted one.

    Mirrors what the loop's own difficulty routing does
    (``session.coder._llm = fast_provider``): keep ``_llm_config`` in sync so
    ``state.model`` reports the model that is actually answering.
    """
    agent._llm = provider
    agent._llm_config = provider.config


def _preflight(demo_root: Path) -> Tuple[bool, List[Dict[str, str]], str]:
    """Fast, offline sanity checks — deliberately not the key/network checks."""
    from kairos.doctor import (
        check_data_dir, check_git, check_python_version, check_workspace_dir,
    )

    wanted = [
        check_python_version(),
        check_data_dir(),
        check_workspace_dir(),
        check_git(),
    ]
    results = [r.to_dict() for r in wanted]
    fatal = [r for r in results if r["status"] == "fail"]
    if fatal:
        return False, results, f"{fatal[0]['name']}: {fatal[0]['message']}"
    if not os.access(str(demo_root), os.W_OK):
        return False, results, f"cannot write to {demo_root}"
    return True, results, ""


def _git_init(repo: Path) -> None:
    """Best effort: real commits make the checkpoints/rollback section meaningful."""
    env = dict(os.environ, GIT_AUTHOR_NAME="kairos-demo", GIT_AUTHOR_EMAIL="demo@localhost",
               GIT_COMMITTER_NAME="kairos-demo", GIT_COMMITTER_EMAIL="demo@localhost")
    for args in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "demo@localhost"],
        ["git", "config", "user.name", "kairos-demo"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "initial: relay planner + tests"],
    ):
        try:
            subprocess.run(args, cwd=str(repo), env=env, capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return


def _write_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "relay.py").write_text(RELAY_V1, encoding="utf-8", newline="\n")
    (repo / "test_relay.py").write_text(TEST_RELAY, encoding="utf-8", newline="\n")
    (repo / "README.md").write_text(README_MD, encoding="utf-8", newline="\n")
    _git_init(repo)


def _run_tests(repo: Path, timeout: int = 120) -> Tuple[bool, str]:
    """Run the repo's tests for real (pytest when available, the file's runner otherwise)."""
    attempts = [
        [sys.executable, "-m", "pytest", "-q", "test_relay.py"],
        [sys.executable, "test_relay.py"],
    ]
    last = ""
    for cmd in attempts:
        try:
            proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True,
                                  timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            last = str(exc)
            continue
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            summary = " ".join(output.strip().splitlines()[-1:]) or "ok"
            return True, summary
        last = " ".join(output.strip().splitlines()[-1:]) or f"exit {proc.returncode}"
        if "No module named pytest" not in output:
            break
    return False, last


# --------------------------------------------------------------------------
# The demo
# --------------------------------------------------------------------------
async def run_demo(
    *,
    out_dir: Optional[Path] = None,
    keep: bool = False,
    open_report: bool = False,
    quiet: bool = False,
    lang: str = "en",
    fmt: str = "html",
    timeout: float = DEFAULT_TIMEOUT_S,
    progress: bool = True,
) -> DemoResult:
    """Run the whole demo and return a structured result (never raises on failure)."""
    started = time.time()
    owns_root = out_dir is None
    demo_root = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="kairos_demo_"))
    demo_root.mkdir(parents=True, exist_ok=True)
    repo = demo_root / "relay-demo"

    log_path = cost_mod._get_log_path()  # noqa: SLF001 - restored below
    previous_log = log_path
    cost_mod.clear_buffer()

    # Role-level worktrees (a production feature for parallel work) are switched
    # off here: a single-agent walkthrough needs ONE tree so the Reviewer and the
    # final verification both see what the Coder wrote.
    previous_skip_wt = os.environ.get("KAIROS_SKIP_WORKTREES")
    os.environ["KAIROS_SKIP_WORKTREES"] = "1"

    def say(key: str, **params: Any) -> None:
        if not quiet and progress:
            print(t(key, lang, **params), flush=True)

    result = DemoResult(ok=False, seconds=0.0, workspace=str(repo))
    try:
        say("hdr")
        say("hdr.plan")

        ok, checks, detail = _preflight(demo_root)
        result.preflight = checks
        if not ok:
            say("preflight.fail", detail=detail)
            return result

        say("step1")
        _write_repo(repo)

        say("step2")
        cost_mod.set_log_path(demo_root / "cost.jsonl")

        # Real orchestrator, real loop, scripted actors.
        from kairos.core.orchestrator import Orchestrator
        from kairos.core.persistence import Persistence
        from kairos.llm.model_router import ModelRouter
        from kairos.loop.loop_runner import run_loop
        from kairos.loop.review_loop import LoopSession

        db_path = demo_root / "kairos.db"
        persistence = Persistence(db_path)
        router = ModelRouter(config_path=_models_config_path())
        orch = Orchestrator(model_router=router, workspace_base=demo_root, db=persistence)
        project = orch.create_project(
            name=DEMO_PROJECT_NAME,
            description="kairos demo — retry planner gate walkthrough",
            work_dir=str(repo),
        )
        result.project_id = project.id

        coder_provider = ScriptedProvider(
            _scripted_config("scripted-coder"), build_coder_script(repo), label="coder")
        reviewer_provider = ScriptedProvider(
            _scripted_config("scripted-reviewer"), build_reviewer_script(repo), label="reviewer")
        _attach_scripted(project.coder, coder_provider)
        _attach_scripted(project.reviewer, reviewer_provider)

        session = LoopSession(
            project=project,
            message_bus=orch.message_bus,
            coder=project.coder,
            reviewer=project.reviewer,
            persistence=persistence,
            best_of_n=1,
            review_focus=[],
        )
        # The demo must be deterministic: no best-of-N, no test-evidence penalty.
        session.require_test_evidence = False

        say("step3")
        task = asyncio.create_task(run_loop(session, DEMO_REQUIREMENT))
        seen: set = set()
        deadline = time.time() + timeout
        timed_out = False
        while not task.done():
            for row in persistence.load_loop_rounds(project.id):
                key = (row.get("round"), row.get("session_id"))
                if key in seen:
                    continue
                seen.add(key)
                if progress and not quiet:
                    _print_round(row, lang, gate_mod.parse_issues(_review_of(row)))
            if time.time() > deadline:
                timed_out = True
                task.cancel()
                break
            await asyncio.sleep(0.2)
        if timed_out:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            say("timeout", s=int(timeout))
            result.detail = f"timeout after {timeout}s"
            return result
        await task

        # 4) Real verification.
        say("step4")
        tests_ok, tests_summary = _run_tests(repo)
        result.tests_ok = tests_ok
        result.tests_summary = tests_summary
        say("tests.ok" if tests_ok else "tests.failed", summary=tests_summary)

        # 5) The receipt.
        say("step5")
        report = gate_mod.collect(project.id, db_path=db_path)
        ext = {"html": "html", "md": "md", "json": "json"}.get(fmt, "html")
        written = gate_mod.write_report(
            report, repo / f"gate-report-{project.id[:8]}.{ext}", fmt=fmt, lang=lang)

        result.rounds = report.rounds_total
        result.rejected_rounds = report.rejected_rounds
        result.first_pass = report.first_pass
        result.final_score = report.final_score
        result.cost_usd = report.cost_usd
        result.checkpoints = len(report.checkpoints)
        result.coder_calls = coder_provider.calls
        result.reviewer_calls = reviewer_provider.calls
        result.badge = report.share_badge(lang)

        if owns_root and not keep:
            # Keep the receipt (that is the product), drop the scratch workspace.
            final_report = Path.cwd() / written.name
            try:
                shutil.copy2(written, final_report)
                written = final_report
            except OSError:
                pass
            result.cleaned = True
        result.report_path = str(written)

        say("step6", s=f"{time.time() - started:.1f}")
        say("report", path=result.report_path)
        say("badge", badge=result.badge)
        say("honest")
        if not result.cleaned:
            say("workspace", path=str(demo_root))

        result.ok = bool(report.rounds_total) and tests_ok
        if not report.rounds_total:
            result.detail = t("no.rounds", lang)

        if open_report and result.report_path:
            try:
                import webbrowser
                webbrowser.open(Path(result.report_path).resolve().as_uri())
            except Exception:
                pass
        return result

    except Exception as exc:  # never traceback at a first-run user
        result.detail = f"{type(exc).__name__}: {exc}"
        if not quiet:
            print(f"[kairos demo] failed: {result.detail}", file=sys.stderr)
        return result
    finally:
        result.seconds = time.time() - started
        if owns_root and not keep and not result.report_path:
            shutil.rmtree(demo_root, ignore_errors=True)
        elif owns_root and not keep:
            shutil.rmtree(demo_root, ignore_errors=True)
        if previous_skip_wt is None:
            os.environ.pop("KAIROS_SKIP_WORKTREES", None)
        else:
            os.environ["KAIROS_SKIP_WORKTREES"] = previous_skip_wt
        try:
            cost_mod.set_log_path(previous_log or (REPO_ROOT / "data" / "cost.jsonl"))
        except Exception:
            pass


def _review_of(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.get("review_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _print_round(row: Dict[str, Any], lang: str, issues: List[Any]) -> None:
    approve = bool(row.get("approve"))
    verdict = t("round.approved" if approve else "round.rejected", lang)
    if issues:
        bugs = t("bugs.n", lang, n=len(issues),
                 first=" ".join((issues[0].description or "").split())[:70])
    else:
        bugs = t("bugs.none", lang)
    print(t("round.line", lang, n=row.get("round"), mark="✓" if approve else "✗",
            verdict=verdict, score=row.get("score"), bugs=bugs), flush=True)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kairos demo",
        description="Run the review gate end-to-end with a scripted model — "
                    "no API key, no network, about a minute.",
    )
    parser.add_argument("--out", default=None,
                        help="Where to build the demo workspace (default: a temp dir "
                             "that is cleaned up; the Gate Report is copied to the "
                             "current directory first).")
    parser.add_argument("--keep", action="store_true",
                        help="Keep the temporary workspace (default: cleaned up).")
    parser.add_argument("--open", action="store_true", dest="open_report",
                        help="Open the Gate Report in the browser when done.")
    parser.add_argument("--format", choices=["html", "md", "json"], default="html",
                        help="Gate Report format (default: html).")
    parser.add_argument("--lang", choices=["en", "zh"], default="en")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                        help=f"Wall-clock cap for the loop (default: {DEFAULT_TIMEOUT_S}s).")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Emit a single JSON document on stdout.")
    parser.add_argument("--quiet", "-q", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    quiet = args.quiet or args.json_output
    result = asyncio.run(run_demo(
        out_dir=Path(args.out) if args.out else None,
        keep=args.keep or bool(args.out),
        open_report=args.open_report,
        quiet=quiet,
        lang=args.lang,
        fmt=args.format,
        timeout=args.timeout,
    ))
    if args.json_output:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.ok else (2 if "timeout" in (result.detail or "") else 1)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
