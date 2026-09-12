"""Gate Report — the shareable artifact of one LoopReview session.

Kairos's pitch is "you don't ship what the agent didn't pass", so the product
needs a *receipt*: what changed, who reviewed it, what it scored, how many
rounds were rejected, what it cost, and whether anything regressed. This module
turns the data Kairos already persists (``loop_rounds``, ``loop_checkpoints``,
the cost ledger, the learned fix/skill tables) into three renderings of the same
report:

    Markdown  — paste into a PR description, a Slack thread or an issue
    HTML      — one self-contained file (no CDN, no images, no JS framework)
                with an in-place **EN / 中文 toggle**, so a mixed-language team
                reads the same artifact
    JSON      — stable keys for CI (``kairos gate report --format json``)

Design notes
------------
* Read-only. The generator opens SQLite with ``mode=ro`` and never writes; the
  fixture writers live in the tests, not here.
* Tolerant. A fresh install has an empty (or missing) database, so every query
  degrades to an empty list and the report renders a "no rounds yet" state
  instead of raising.
* Self-contained i18n. The backend has no i18n runtime (the web UI does), and a
  report is a file that travels outside the app, so the strings live in this
  module and the HTML carries both languages at once.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "kairos.db"


def default_db_path() -> Path:
    """Resolve the SQLite path the same way the app does.

    ``KAIROS_DATA_DIR`` wins (docker / tests), then ``settings.data_dir``
    (the running app), then the repo's own ``data/`` directory. Keeping one
    resolver means the CLI, the API and the tests all read the same DB.
    """
    env = os.environ.get("KAIROS_DATA_DIR")
    if env:
        return Path(env) / "kairos.db"
    try:
        from kairos.config.settings import settings
        return Path(settings.data_dir) / "kairos.db"
    except Exception:
        return DEFAULT_DB

# --------------------------------------------------------------------------
# i18n — the report travels outside the app, so it carries its own strings.
# Adding a language = one more column in STRINGS (the HTML toggle picks it up).
# --------------------------------------------------------------------------
STRINGS: Dict[str, Dict[str, str]] = {
    "title": {"en": "Kairos Gate Report", "zh": "Kairos 门禁报告"},
    "badge.passed": {"en": "Passed", "zh": "已通过"},
    "badge.rejected": {"en": "Not passed", "zh": "未通过"},
    "badge.empty": {"en": "No rounds yet", "zh": "暂无轮次"},
    "badge.running": {"en": "Running", "zh": "进行中"},
    "label.project": {"en": "Project", "zh": "项目"},
    "label.session": {"en": "Session", "zh": "会话"},
    "label.generated": {"en": "Generated", "zh": "生成时间"},
    "label.requirement": {"en": "Requirement", "zh": "需求"},
    "label.rounds": {"en": "Rounds", "zh": "轮次"},
    "label.rejected": {"en": "Rejected rounds", "zh": "被打回轮次"},
    "label.firstpass": {"en": "First-pass", "zh": "一次通过"},
    "label.firstpass.yes": {"en": "yes", "zh": "是"},
    "label.firstpass.no": {"en": "no", "zh": "否"},
    "label.cost": {"en": "Cost", "zh": "花费"},
    "label.tokens": {"en": "Tokens", "zh": "Token"},
    "label.finalScore": {"en": "Final score", "zh": "最终评分"},
    "section.verdict": {"en": "Verdict", "zh": "结论"},
    "section.score": {"en": "Score per round", "zh": "每轮评分"},
    "section.rounds": {"en": "Rounds", "zh": "轮次明细"},
    "section.issues": {"en": "Issues found by the Reviewer", "zh": "审查员发现的问题"},
    "section.cost": {"en": "Cost ledger", "zh": "成本账本"},
    "section.checkpoints": {"en": "Checkpoints (rollback points)", "zh": "检查点（可回滚点）"},
    "section.learned": {"en": "What Kairos learned", "zh": "Kairos 积累的知识"},
    "section.eval": {"en": "Regression check (eval suite)", "zh": "回归检查（评测套件）"},
    "section.reproduce": {"en": "Reproduce", "zh": "复现方式"},
    "th.round": {"en": "Round", "zh": "轮次"},
    "th.score": {"en": "Score", "zh": "评分"},
    "th.verdict": {"en": "Verdict", "zh": "结论"},
    "th.issues": {"en": "Issues", "zh": "问题数"},
    "th.summary": {"en": "Coder summary", "zh": "Coder 摘要"},
    "th.model": {"en": "Model", "zh": "模型"},
    "th.calls": {"en": "Calls", "zh": "调用次数"},
    "th.costUsd": {"en": "Cost (USD)", "zh": "花费（USD）"},
    "th.commit": {"en": "Commit", "zh": "提交"},
    "th.approved": {"en": "Approved", "zh": "是否通过"},
    "th.severity": {"en": "Severity", "zh": "严重度"},
    "th.promptTokens": {"en": "Prompt tokens", "zh": "输入 Token"},
    "th.completionTokens": {"en": "Completion tokens", "zh": "输出 Token"},
    "th.file": {"en": "File", "zh": "文件"},
    "th.problem": {"en": "Problem", "zh": "问题"},
    "th.fix": {"en": "Suggested fix", "zh": "建议修法"},
    "round.approved": {"en": "approved", "zh": "通过"},
    "round.rejected": {"en": "rejected", "zh": "打回"},
    "verdict.none": {
        "en": "No rounds have run yet for this project.",
        "zh": "这个项目还没有跑过任何一轮。",
    },
    "verdict.passedFirst": {
        "en": "Passed on the first round — nothing was shipped before it cleared the gate.",
        "zh": "一轮即通过 —— 未过门禁之前没有交付任何东西。",
    },
    "verdict.passedAfter": {
        "en": "Passed after {n} round(s): the Reviewer sent work back {r} time(s).",
        "zh": "经过 {n} 轮通过：审查员打回过 {r} 次。",
    },
    "verdict.rejected": {
        "en": "Not passed — the last round was sent back. {k} open issue(s).",
        "zh": "尚未通过 —— 最后一轮被打回，仍有 {k} 个问题。",
    },
    "issues.none": {"en": "No open issues in this round.", "zh": "本轮没有未解决问题。"},
    "cost.none": {"en": "No cost recorded for this session.", "zh": "本会话没有记录花费。"},
    "learned.none": {"en": "Nothing captured yet.", "zh": "还没有沉淀。"},
    "learned.fixes": {"en": "working fixes", "zh": "可复用修法"},
    "learned.skills": {"en": "project skills", "zh": "项目技能"},
    "learned.notes": {"en": "project notes", "zh": "项目笔记"},
    "eval.none": {"en": "No eval runs recorded.", "zh": "没有评测记录。"},
    "eval.passRate": {"en": "Pass rate", "zh": "通过率"},
    "eval.trend": {"en": "vs. previous run", "zh": "对比上次"},
    "badge.line": {
        "en": "{mark} · {rounds} round(s) · ${cost} · first-pass {firstpass}",
        "zh": "{mark} · {rounds} 轮 · ${cost} · 一次通过 {firstpass}",
    },
    "badge.share": {
        "en": "Kairos Gate {mark} · {rounds} round(s) · ${cost}",
        "zh": "Kairos 门禁 {mark} · {rounds} 轮 · ${cost}",
    },
    "label.workDir": {"en": "Workspace", "zh": "工作目录"},
    "footer": {
        "en": "Generated by Kairos Code — a self-hosted multi-agent pipeline that enforces review gates.",
        "zh": "由 Kairos Code 生成 —— 自托管、强制审查门禁的多智能体流水线。",
    },
    "empty.hint": {
        "en": "Run a loop (`kairos exec \"<task>\"` or the web UI) and regenerate.",
        "zh": "先跑一次循环（`kairos exec \"<任务>\"` 或 Web UI）再重新生成。",
    },
}


def t(key: str, lang: str = "en", **params: Any) -> str:
    """Look up a report string. Unknown keys return the key itself (never blank)."""
    entry = STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(lang) or entry.get("en") or key
    for name, value in params.items():
        text = text.replace("{" + name + "}", str(value))
    return text


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass
class Issue:
    severity: str = ""
    category: str = ""
    file: str = ""
    line: Optional[int] = None
    description: str = ""
    fix: str = ""

    @property
    def location(self) -> str:
        if self.file and self.line:
            return f"{self.file}:{self.line}"
        return self.file or ""


@dataclass
class RoundView:
    round: int
    session_id: str = ""
    score: int = 0
    approve: bool = False
    summary: str = ""
    coder_summary: str = ""
    created_at: float = 0.0
    issues: List[Issue] = field(default_factory=list)
    verdict_extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Checkpoint:
    round: int
    sha: str = ""
    score: int = 0
    approved: bool = False
    summary: str = ""
    created_at: float = 0.0


@dataclass
class GateReport:
    project_id: str
    project_name: str
    work_dir: str = ""
    status: str = ""
    requirement: str = ""
    session_id: str = ""
    generated_at: float = 0.0
    rounds: List[RoundView] = field(default_factory=list)
    checkpoints: List[Checkpoint] = field(default_factory=list)
    cost_usd: float = 0.0
    cost_calls: int = 0
    cost_by_model: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tokens: int = 0
    learned: Dict[str, int] = field(default_factory=dict)
    eval_latest: Optional[Dict[str, Any]] = None  # from results/*.json via kairos.trend

    # -- derived ---------------------------------------------------------
    @property
    def rounds_total(self) -> int:
        return len(self.rounds)

    @property
    def rejected_rounds(self) -> int:
        return sum(1 for r in self.rounds if not r.approve)

    @property
    def first_pass(self) -> bool:
        return self.rounds_total == 1 and self.rounds[0].approve

    @property
    def passed(self) -> bool:
        return bool(self.rounds) and self.rounds[-1].approve

    @property
    def final_score(self) -> int:
        return self.rounds[-1].score if self.rounds else 0

    @property
    def score_curve(self) -> List[int]:
        return [r.score for r in self.rounds]

    @property
    def open_issues(self) -> List[Issue]:
        return [] if self.passed or not self.rounds else self.rounds[-1].issues

    @property
    def issues_total(self) -> int:
        return sum(len(r.issues) for r in self.rounds)

    def issues_by_severity(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for rnd in self.rounds:
            for issue in rnd.issues:
                key = (issue.severity or "UNKNOWN").upper()
                out[key] = out.get(key, 0) + 1
        return out

    def state(self) -> str:
        if not self.rounds:
            return "empty"
        if self.passed:
            return "passed"
        if (self.status or "").lower() in ("running", "in_progress", "active"):
            return "running"
        return "rejected"

    def badge_line(self, lang: str = "en") -> str:
        mark = {"passed": "✅", "rejected": "❌", "empty": "•", "running": "⏳"}[self.state()]
        first = (
            t("label.firstpass.yes", lang) if self.first_pass
            else t("label.firstpass.no", lang)
        )
        return t(
            "badge.line", lang, mark=mark, rounds=self.rounds_total,
            cost=f"{self.cost_usd:.4f}", firstpass=first,
        )

    def share_badge(self, lang: str = "en") -> str:
        """One line to paste into a PR description, a README or a Slack thread."""
        mark = {"passed": "✅", "rejected": "❌", "empty": "•", "running": "⏳"}[self.state()]
        return t("badge.share", lang, mark=mark, rounds=self.rounds_total,
                 cost=f"{self.cost_usd:.4f}")

    # -- renderings ------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # `location` is a property on Issue, so asdict() drops it — add it back
        # ("file:line" is what CI greps for and what the UI links to).
        for rnd in data.get("rounds", []):
            for issue in rnd.get("issues", []):
                if issue.get("file") and issue.get("line"):
                    issue["location"] = f"{issue['file']}:{issue['line']}"
                else:
                    issue["location"] = issue.get("file") or ""
        data.update({
            "state": self.state(),
            "passed": self.passed,
            "first_pass": self.first_pass,
            "rounds_total": self.rounds_total,
            "rejected_rounds": self.rejected_rounds,
            "final_score": self.final_score,
            "score_curve": self.score_curve,
            "issues_total": self.issues_total,
            "issues_by_severity": self.issues_by_severity(),
            "badge": self.badge_line("en"),
        })
        return data

    def to_markdown(self, lang: str = "en") -> str:
        rows: List[str] = []
        add = rows.append
        mark = {"passed": "✅", "rejected": "❌", "empty": "•", "running": "⏳"}[self.state()]
        add(f"## {mark} {t('title', lang)} — {self.project_name}")
        add("")
        add(f"`{self.share_badge(lang)}`")
        add("")
        add(f"- **{t('label.project', lang)}**: {self.project_name} (`{self.project_id}`)")
        add(f"- **{t('label.session', lang)}**: `{self.session_id or '—'}`")
        if self.work_dir:
            add(f"- **{t('label.workDir', lang)}**: `{self.work_dir}`")
        add(f"- **{t('label.generated', lang)}**: {_fmt_time(self.generated_at)}")
        if self.requirement:
            add(f"- **{t('label.requirement', lang)}**: {self.requirement}")
        add("")
        add(f"### {t('section.verdict', lang)}")
        add("")
        add(self.verdict_text(lang))
        add("")
        add("| " + " | ".join([
            t("label.rounds", lang), t("label.rejected", lang),
            t("label.firstpass", lang), t("label.finalScore", lang),
            t("label.cost", lang), t("label.tokens", lang),
        ]) + " |")
        add("| --- | --- | --- | --- | --- | --- |")
        add("| " + " | ".join([
            str(self.rounds_total), str(self.rejected_rounds),
            t("label.firstpass.yes", lang) if self.first_pass else t("label.firstpass.no", lang),
            str(self.final_score), f"${self.cost_usd:.4f}", str(self.tokens),
        ]) + " |")
        add("")

        if self.rounds:
            add(f"### {t('section.rounds', lang)}")
            add("")
            add("| " + " | ".join([
                t("th.round", lang), t("th.score", lang), t("th.verdict", lang),
                t("th.issues", lang), t("th.summary", lang),
            ]) + " |")
            add("| --- | --- | --- | --- | --- |")
            for rnd in self.rounds:
                summary = _oneline(rnd.coder_summary or rnd.summary, 140)
                add("| " + " | ".join([
                    f"R{rnd.round}", str(rnd.score),
                    t("round.approved", lang) if rnd.approve else t("round.rejected", lang),
                    str(len(rnd.issues)), _md_escape(summary),
                ]) + " |")
            add("")
            add(f"### {t('section.score', lang)}")
            add("")
            add(_sparkline(self.score_curve))
            add("")

        issues = self.open_issues
        if self.rounds:
            add(f"### {t('section.issues', lang)}")
            add("")
            if not issues:
                add(t("issues.none", lang))
            else:
                add("| " + " | ".join([
                    t("th.severity", lang), t("th.file", lang),
                    t("th.problem", lang), t("th.fix", lang),
                ]) + " |")
                add("| --- | --- | --- | --- |")
                for issue in issues:
                    add("| " + " | ".join([
                        _md_escape(issue.severity or "—"),
                        _md_escape(issue.location or "—"),
                        _md_escape(_oneline(issue.description, 200)),
                        _md_escape(_oneline(issue.fix, 200)),
                    ]) + " |")
            add("")

        add(f"### {t('section.cost', lang)}")
        add("")
        if not self.cost_by_model and not self.cost_usd:
            add(t("cost.none", lang))
        else:
            add("| " + " | ".join([
                t("th.model", lang), t("th.calls", lang),
                "Prompt tokens", "Completion tokens", t("th.costUsd", lang),
            ]) + " |")
            add("| --- | --- | --- | --- | --- |")
            for model, slot in sorted(self.cost_by_model.items()):
                add("| " + " | ".join([
                    _md_escape(model), str(slot.get("calls", 0)),
                    str(slot.get("prompt_tokens", 0)), str(slot.get("completion_tokens", 0)),
                    f"${float(slot.get('cost_usd', 0.0)):.6f}",
                ]) + " |")
        add("")

        if self.checkpoints:
            add(f"### {t('section.checkpoints', lang)}")
            add("")
            add("| " + " | ".join([t("th.round", lang), t("th.commit", lang),
                                   t("th.score", lang), t("th.approved", lang)]) + " |")
            add("| --- | --- | --- | --- |")
            for cp in self.checkpoints:
                add("| " + " | ".join([
                    f"R{cp.round}", f"`{cp.sha[:10]}`", str(cp.score),
                    "✅" if cp.approved else "—",
                ]) + " |")
            add("")

        add(f"### {t('section.learned', lang)}")
        add("")
        if not any(self.learned.values()):
            add(t("learned.none", lang))
        else:
            add(" · ".join([
                f"{self.learned.get('fixes', 0)} {t('learned.fixes', lang)}",
                f"{self.learned.get('skills', 0)} {t('learned.skills', lang)}",
                f"{self.learned.get('notes', 0)} {t('learned.notes', lang)}",
            ]))
        add("")

        if self.eval_latest:
            add(f"### {t('section.eval', lang)}")
            add("")
            add(f"- {t('eval.passRate', lang)}: {self.eval_latest.get('pass_rate', 0):.0%} "
                f"({self.eval_latest.get('passed', 0)}/{self.eval_latest.get('cases', 0)})")
            delta = self.eval_latest.get("pass_rate_delta")
            if delta is not None:
                add(f"- {t('eval.trend', lang)}: {delta:+.1%}")
            add("")

        add("---")
        add("")
        add(t("footer", lang))
        return "\n".join(rows) + "\n"

    def verdict_text(self, lang: str = "en") -> str:
        state = self.state()
        if state == "empty":
            return t("verdict.none", lang) + " " + t("empty.hint", lang)
        if state == "passed" and self.first_pass:
            return t("verdict.passedFirst", lang)
        if state == "passed":
            return t("verdict.passedAfter", lang, n=self.rounds_total,
                     r=self.rejected_rounds)
        return t("verdict.rejected", lang, k=len(self.open_issues))

    def to_html(self, lang: str = "en") -> str:
        """One self-contained file: inline CSS, no JS framework, EN/中文 toggle.

        Every translatable string is emitted as ``<span data-en data-zh>`` so the
        toggle is a pure client-side swap of ``textContent``: the file stays a
        single artifact a mixed-language team can both read, and it renders
        offline (attached to a PR or opened from disk).
        """
        state = self.state()
        badge_class = {
            "passed": "ok", "rejected": "bad", "running": "warn", "empty": "idle",
        }[state]
        badge_key = {
            "passed": "badge.passed", "rejected": "badge.rejected",
            "running": "badge.running", "empty": "badge.empty",
        }[state]

        def span2(en_text: str, zh_text: str) -> str:
            initial = en_text if lang == "en" else zh_text
            return (f'<span class="i18n" data-en="{html.escape(en_text)}" '
                    f'data-zh="{html.escape(zh_text)}">{html.escape(initial)}</span>')

        def both(key: str) -> str:
            return span2(t(key, "en"), t(key, "zh"))

        def esc(value: Any) -> str:
            return html.escape("" if value is None else str(value))

        parts: List[str] = []
        parts.append(f"""<!DOCTYPE html>
<html lang="{esc(lang)}" data-lang="{esc(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(t("title", lang))} — {esc(self.project_name)}</title>
<style>
:root {{
  --bg:#ffffff; --fg:#1f2328; --muted:#59636e; --line:#d8dee4; --card:#f6f8fa;
  --ok:#1a7f37; --bad:#cf222e; --warn:#9a6700; --idle:#57606a; --accent:#0969da;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --line:#30363d; --card:#161b22;
           --ok:#3fb950; --bad:#f85149; --warn:#d29922; --idle:#8b949e; --accent:#4493f8; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:32px 20px; background:var(--bg); color:var(--fg);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans",Helvetica,Arial,
  "PingFang SC","Microsoft YaHei",sans-serif; }}
.wrap {{ max-width:960px; margin:0 auto; }}
header {{ display:flex; align-items:flex-start; gap:16px; flex-wrap:wrap; margin-bottom:8px; }}
h1 {{ font-size:22px; margin:0 0 4px; }}
h2 {{ font-size:15px; margin:28px 0 10px; color:var(--muted); font-weight:600;
  text-transform:uppercase; letter-spacing:.04em; }}
.badge {{ display:inline-block; padding:3px 10px; border-radius:999px; font-size:13px;
  font-weight:600; border:1px solid currentColor; }}
.badge.ok {{ color:var(--ok); }} .badge.bad {{ color:var(--bad); }}
.badge.warn {{ color:var(--warn); }} .badge.idle {{ color:var(--idle); }}
.pill {{ display:inline-block; padding:2px 8px; border-radius:6px; background:var(--card);
  border:1px solid var(--line); font-size:12px; color:var(--muted); margin-right:6px; }}
.sub {{ color:var(--muted); font-size:13px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin:16px 0 4px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
.card .k {{ font-size:12px; color:var(--muted); }}
.card .v {{ font-size:20px; font-weight:600; margin-top:2px; }}
table {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; }}
tr:last-child td {{ border-bottom:none; }}
code {{ background:var(--card); border:1px solid var(--line); border-radius:5px;
  padding:1px 5px; font-size:12.5px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
.sev {{ font-size:11px; font-weight:700; letter-spacing:.03em; }}
.sev.BUG,.sev.BLOCKER {{ color:var(--bad); }}
.sev.MAJOR {{ color:var(--warn); }}
.sev.MINOR,.sev.SUGGESTION,.sev.INFO {{ color:var(--idle); }}
.chart {{ display:flex; align-items:flex-end; gap:6px; height:110px; margin:6px 0 2px; }}
.chart .bar {{ flex:0 0 34px; background:var(--accent); border-radius:4px 4px 0 0; position:relative; }}
.chart .bar.rej {{ background:var(--bad); }}
.chart .bar span {{ position:absolute; top:-20px; left:0; right:0; text-align:center;
  font-size:11px; color:var(--muted); }}
.chart-legend {{ color:var(--muted); font-size:12px; }}
footer {{ margin-top:32px; padding-top:14px; border-top:1px solid var(--line);
  color:var(--muted); font-size:12.5px; }}
.lang {{ margin-left:auto; }}
.lang button {{ background:transparent; border:1px solid var(--line); color:var(--muted);
  border-radius:6px; padding:3px 9px; font-size:12px; cursor:pointer; }}
.lang button[aria-pressed="true"] {{ color:var(--fg); border-color:var(--accent); }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1>{both("title")}</h1>
    <div class="sub">{esc(self.project_name)} · <code>{esc(self.project_id)}</code>
      · {esc(self.session_id or "—")} · {esc(_fmt_time(self.generated_at))}</div>
  </div>
  <div class="lang">
    <button id="btn-en" aria-pressed="{'true' if lang == 'en' else 'false'}">EN</button>
    <button id="btn-zh" aria-pressed="{'true' if lang == 'zh' else 'false'}">中文</button>
  </div>
</header>
<div><span class="badge {badge_class}">{both(badge_key)}</span>
  <span class="pill">{span2(self.badge_line("en"), self.badge_line("zh"))}</span></div>""")

        if self.requirement:
            parts.append(f'<p class="sub"><strong>{both("label.requirement")}</strong>: '
                         f'{esc(_oneline(self.requirement, 300))}</p>')

        parts.append(f"<h2>{both('section.verdict')}</h2><p>"
                     f"{span2(self.verdict_text('en'), self.verdict_text('zh'))}</p>")

        parts.append("<div class=\"grid\">")
        for key, value in [
            ("label.rounds", self.rounds_total),
            ("label.rejected", self.rejected_rounds),
            ("label.firstpass", span2(t("label.firstpass.yes", "en"),
                                      t("label.firstpass.yes", "zh")) if self.first_pass
             else span2(t("label.firstpass.no", "en"), t("label.firstpass.no", "zh"))),
            ("label.finalScore", self.final_score),
            ("label.cost", f"${self.cost_usd:.4f}"),
            ("label.tokens", self.tokens),
        ]:
            parts.append(f'<div class="card"><div class="k">{both(key)}</div>'
                         f'<div class="v">{value if key == "label.firstpass" else esc(value)}</div></div>')
        parts.append("</div>")

        if self.rounds:
            parts.append(f"<h2>{both('section.score')}</h2><div class=\"chart\">")
            peak = max(max(self.score_curve), 1)
            for rnd in self.rounds:
                height = max(6, int(rnd.score / peak * 90))
                css = "" if rnd.approve else " rej"
                parts.append(f'<div class="bar{css}" style="height:{height}px">'
                             f'<span>{rnd.score}</span></div>')
            parts.append("</div>")
            parts.append(f'<div class="chart-legend">{both("th.round")} → '
                         f'{esc(" · ".join("R%d" % r.round for r in self.rounds))}</div>')

            parts.append(f"<h2>{both('section.rounds')}</h2><table><thead><tr>"
                         f"<th>{both('th.round')}</th><th>{both('th.score')}</th>"
                         f"<th>{both('th.verdict')}</th><th>{both('th.issues')}</th>"
                         f"<th>{both('th.summary')}</th></tr></thead><tbody>")
            for rnd in self.rounds:
                verdict = span2(t("round.approved", "en") if rnd.approve
                                else t("round.rejected", "en"),
                                t("round.approved", "zh") if rnd.approve
                                else t("round.rejected", "zh"))
                css = "sev MINOR" if rnd.approve else "sev BUG"
                summary = _oneline(rnd.coder_summary or rnd.summary, 200)
                parts.append(
                    f"<tr><td>R{rnd.round}</td><td>{rnd.score}</td>"
                    f'<td><span class="{css}">{verdict}</span></td>'
                    f"<td>{len(rnd.issues)}</td><td>{esc(summary)}</td></tr>")
            parts.append("</tbody></table>")

            parts.append(f"<h2>{both('section.issues')}</h2>")
            if not self.open_issues:
                parts.append(f"<p>{both('issues.none')}</p>")
            else:
                parts.append(f"<table><thead><tr><th>{both('th.severity')}</th>"
                             f"<th>{both('th.file')}</th><th>{both('th.problem')}</th>"
                             f"<th>{both('th.fix')}</th></tr></thead><tbody>")
                for issue in self.open_issues:
                    sev = (issue.severity or "—").upper()
                    parts.append(
                        f'<tr><td><span class="sev {esc(sev)}">{esc(sev)}</span></td>'
                        f"<td><code>{esc(issue.location or '—')}</code></td>"
                        f"<td>{esc(_oneline(issue.description, 400))}</td>"
                        f"<td>{esc(_oneline(issue.fix, 400))}</td></tr>")
                parts.append("</tbody></table>")

        parts.append(f"<h2>{both('section.cost')}</h2>")
        if not self.cost_by_model and not self.cost_usd:
            parts.append(f"<p>{both('cost.none')}</p>")
        else:
            parts.append(f"<table><thead><tr><th>{both('th.model')}</th>"
                         f"<th>{both('th.calls')}</th><th>{both('th.promptTokens')}</th>"
                         f"<th>{both('th.completionTokens')}</th>"
                         f"<th>{both('th.costUsd')}</th></tr></thead><tbody>")
            for model, slot in sorted(self.cost_by_model.items()):
                parts.append(
                    f"<tr><td>{esc(model)}</td><td>{esc(slot.get('calls', 0))}</td>"
                    f"<td>{esc(slot.get('prompt_tokens', 0))}</td>"
                    f"<td>{esc(slot.get('completion_tokens', 0))}</td>"
                    f"<td>${float(slot.get('cost_usd', 0.0)):.6f}</td></tr>")
            parts.append("</tbody></table>")

        if self.checkpoints:
            parts.append(f"<h2>{both('section.checkpoints')}</h2><table><thead><tr>"
                         f"<th>{both('th.round')}</th><th>{both('th.commit')}</th>"
                         f"<th>{both('th.score')}</th><th>{both('th.approved')}</th>"
                         f"</tr></thead><tbody>")
            for cp in self.checkpoints:
                parts.append(
                    f"<tr><td>R{cp.round}</td><td><code>{esc(cp.sha[:10])}</code></td>"
                    f"<td>{cp.score}</td><td>{'✅' if cp.approved else '—'}</td></tr>")
            parts.append("</tbody></table>")

        parts.append(f"<h2>{both('section.learned')}</h2>")
        if not any(self.learned.values()):
            parts.append(f"<p>{both('learned.none')}</p>")
        else:
            items = [
                span2(f"{self.learned.get('fixes', 0)} {t('learned.fixes', 'en')}",
                      f"{self.learned.get('fixes', 0)} {t('learned.fixes', 'zh')}"),
                span2(f"{self.learned.get('skills', 0)} {t('learned.skills', 'en')}",
                      f"{self.learned.get('skills', 0)} {t('learned.skills', 'zh')}"),
                span2(f"{self.learned.get('notes', 0)} {t('learned.notes', 'en')}",
                      f"{self.learned.get('notes', 0)} {t('learned.notes', 'zh')}"),
            ]
            parts.append("<p>" + " · ".join(f'<span class="pill">{i}</span>' for i in items)
                         + "</p>")

        if self.eval_latest:
            run = self.eval_latest
            label = span2(
                f"{t('eval.passRate', 'en')}: {run.get('pass_rate', 0):.0%} "
                f"({run.get('passed', 0)}/{run.get('cases', 0)})",
                f"{t('eval.passRate', 'zh')}: {run.get('pass_rate', 0):.0%} "
                f"({run.get('passed', 0)}/{run.get('cases', 0)})",
            )
            parts.append(f"<h2>{both('section.eval')}</h2><p>{label}</p>")
            if run.get("pass_rate_delta") is not None:
                parts.append("<p>" + span2(
                    f"{t('eval.trend', 'en')}: {run['pass_rate_delta']:+.1%}",
                    f"{t('eval.trend', 'zh')}: {run['pass_rate_delta']:+.1%}",
                ) + "</p>")

        parts.append(f"<h2>{both('section.reproduce')}</h2>"
                     f"<p><code>python -m kairos gate report --project {esc(self.project_id)}"
                     f" --out gate-report.html</code></p>")
        parts.append(f"<footer>{both('footer')}</footer>")
        parts.append("""</div>
<script>
(function () {
  var root = document.documentElement;
  function apply(lang) {
    root.setAttribute('data-lang', lang);
    root.setAttribute('lang', lang);
    highlight(document.getElementById('btn-en'), document.getElementById('btn-zh'), lang);
    document.querySelectorAll('.i18n[data-en][data-zh]').forEach(function (el) {
      el.textContent = el.getAttribute('data-' + lang);
    });
    var t = document.querySelector('title');
    if (t) {
      document.title = document.title.replace(/Kairos Gate Report|Kairos 门禁报告/,
        lang === 'zh' ? 'Kairos 门禁报告' : 'Kairos Gate Report');
    }
  }
  function highlight(en, zh, lang) {
    if (!en || !zh) { return; }
    en.setAttribute('aria-pressed', lang === 'en' ? 'true' : 'false');
    zh.setAttribute('aria-pressed', lang === 'zh' ? 'true' : 'false');
  }
  var en = document.getElementById('btn-en'), zh = document.getElementById('btn-zh');
  if (en) { en.addEventListener('click', function () { apply('en'); }); }
  if (zh) { zh.addEventListener('click', function () { apply('zh'); }); }
})();
</script>
</body>
</html>""")
        return "\n".join(parts)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _fmt_time(ts: float) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _oneline(text: Any, limit: int) -> str:
    if text is None:
        return ""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _md_escape(text: Any) -> str:
    return _oneline(text, 400).replace("|", "\\|")


def _sparkline(values: List[int]) -> str:
    if not values:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    top = max(values) or 1
    line = "".join(blocks[min(len(blocks) - 1, int(v / top * (len(blocks) - 1)))] for v in values)
    return f"`{line}` {values[0]} → {values[-1]}"


def _ro_connect(db_path) -> Optional[sqlite3.Connection]:
    if db_path is None:
        return None
    db_path = Path(db_path)  # tolerate a str from library/CLI callers
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _query(conn: Optional[sqlite3.Connection], sql: str, params: tuple = ()) -> List[dict]:
    if conn is None:
        return []
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []


def parse_issues(review: Dict[str, Any]) -> List[Issue]:
    """Normalise both verdict shapes into Issue rows.

    The simplified reviewer emits ``bugs`` (file/line/description/fix); the older
    rubric shape emits ``issues`` (category/severity/file/line/fix_instruction).
    Both are handled so old sessions still render.
    """
    out: List[Issue] = []
    for raw in (review.get("issues") or []):
        if isinstance(raw, dict):
            out.append(Issue(
                severity=str(raw.get("severity") or ""),
                category=str(raw.get("category") or ""),
                file=str(raw.get("file") or ""),
                line=raw.get("line") if isinstance(raw.get("line"), int) else None,
                description=str(raw.get("description") or ""),
                fix=str(raw.get("fix_instruction") or raw.get("fix") or ""),
            ))
    for raw in (review.get("bugs") or []):
        if isinstance(raw, dict):
            out.append(Issue(
                severity=str(raw.get("severity") or "BUG"),
                category=str(raw.get("category") or "bug"),
                file=str(raw.get("file") or ""),
                line=raw.get("line") if isinstance(raw.get("line"), int) else None,
                description=str(raw.get("description") or ""),
                fix=str(raw.get("fix") or ""),
            ))
    return out


# --------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------
def find_project(value: str, *, db_path: Optional[Path] = None) -> Optional[dict]:
    """Resolve a project by exact id, or by name / id-prefix (case-insensitive)."""
    db_path = db_path or default_db_path()
    conn = _ro_connect(db_path)
    try:
        rows = _query(conn, "SELECT * FROM projects")
    finally:
        if conn is not None:
            conn.close()
    if not rows:
        return None
    needle = (value or "").strip().lower()
    for row in rows:
        if str(row.get("id", "")).lower() == needle:
            return row
    for row in rows:
        if needle and str(row.get("id", "")).lower().startswith(needle):
            return row
    for row in rows:
        if needle and needle in str(row.get("name", "")).lower():
            return row
    return None


def collect(
    project_id: str,
    *,
    db_path: Optional[Path] = None,
    session_id: Optional[str] = None,
    limit: int = 50,
) -> GateReport:
    """Build a GateReport from what the loop persisted. Never raises on empty data."""
    db_path = db_path or default_db_path()
    conn = _ro_connect(db_path)
    try:
        project = None
        for row in _query(conn, "SELECT * FROM projects WHERE id = ?", (project_id,)):
            project = row
        if project is None and conn is not None:
            # the id may be a name/prefix — resolve it like the CLI does
            for row in _query(conn, "SELECT * FROM projects"):
                if (project_id or "").lower() in str(row.get("name", "")).lower():
                    project = row
                    break
        project = project or {}
        # Work with the real id from here on: rounds/checkpoints are keyed by id,
        # so a caller passing a project *name* must still find its data.
        resolved_id = str(project.get("id") or project_id)

        rows = _query(
            conn,
            "SELECT * FROM loop_rounds WHERE project_id = ? "
            "ORDER BY created_at ASC, COALESCE(insert_order, 0) ASC",
            (resolved_id,),
        )
        if session_id:
            rows = [r for r in rows if r.get("session_id") == session_id]
        rows = rows[-limit:]

        checkpoints = _query(
            conn,
            "SELECT * FROM loop_checkpoints WHERE project_id = ? "
            "ORDER BY round ASC",
            (resolved_id,),
        )
        if session_id:
            checkpoints = [c for c in checkpoints if c.get("session_id") == session_id]
    finally:
        if conn is not None:
            conn.close()

    rounds: List[RoundView] = []
    tokens = 0
    for row in rows:
        review: Dict[str, Any] = {}
        raw = row.get("review_json")
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    review = parsed
            except json.JSONDecodeError:
                review = {}
        usage = review.get("usage") if isinstance(review.get("usage"), dict) else {}
        if usage:
            tokens += int(usage.get("total_tokens") or 0)
        rounds.append(RoundView(
            round=int(row.get("round") or 0),
            session_id=str(row.get("session_id") or ""),
            score=int(row.get("score") or 0),
            approve=bool(row.get("approve")),
            summary=str(row.get("review_summary") or ""),
            coder_summary=str(row.get("coder_summary") or ""),
            created_at=float(row.get("created_at") or 0.0),
            issues=parse_issues(review),
            verdict_extra={k: v for k, v in review.items()
                           if k not in ("issues", "bugs", "summary")},
        ))

    if not session_id and rounds:
        session_id = rounds[-1].session_id

    cost_usd, cost_calls, by_model = _cost_snapshot()
    learned = _learned_counts(resolved_id, db_path=db_path)
    requirement = str(project.get("requirements") or project.get("description") or "")
    requirement = _oneline(requirement, 400)

    report = GateReport(
        project_id=resolved_id,
        project_name=str(project.get("name") or project_id),
        work_dir=str(project.get("work_dir") or ""),
        status=str(project.get("status") or ""),
        requirement=requirement,
        session_id=session_id or "",
        generated_at=time.time(),
        rounds=rounds,
        checkpoints=[Checkpoint(
            round=int(row.get("round") or 0),
            sha=str(row.get("sha") or ""),
            score=int(row.get("score") or 0),
            approved=bool(row.get("approved")),
            summary=str(row.get("summary") or ""),
            created_at=float(row.get("created_at") or 0.0),
        ) for row in checkpoints],
        cost_usd=cost_usd,
        cost_calls=cost_calls,
        cost_by_model=by_model,
        tokens=tokens,
        learned=learned,
        eval_latest=_latest_eval(resolved_id, db_path=db_path),
    )
    return report


def _cost_snapshot() -> tuple[float, int, Dict[str, Dict[str, Any]]]:
    """Cost from the in-process ledger plus the on-disk JSONL log."""
    total, calls, by_model = 0.0, 0, {}
    try:  # pragma: no cover - exercised through cost.py itself
        from kairos import cost as cost_mod  # local import: keeps this module light
        summary = cost_mod.cost_summary()
        total += float(summary.get("cost_usd") or 0.0)
        calls += int(summary.get("calls") or 0)
        by_model = dict(summary.get("models") or {})
    except Exception:
        pass

    try:  # pragma: no cover - depends on the repo layout
        from kairos import cost as cost_mod
        log_path = cost_mod._get_log_path()  # noqa: SLF001 - intentional: same module
        if log_path and Path(log_path).exists():
            seen = set()
            for line in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                # The in-process buffer's entries are flushed to the same JSONL, so
                # dedupe on the per-call id (falling back to the natural key) before
                # summing — otherwise every call is counted twice.
                key = entry.get("call_id") or (
                    entry.get("timestamp"), entry.get("model"), entry.get("cost_usd"),
                    entry.get("prompt_tokens"), entry.get("completion_tokens"),
                )
                if key in seen:
                    continue
                seen.add(key)
                model = str(entry.get("model") or "unknown")
                slot = by_model.setdefault(model, {
                    "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                    "cost_usd": 0.0, "avg_duration_ms": 0,
                })
                slot["calls"] = int(slot.get("calls") or 0) + 1
                slot["prompt_tokens"] = int(slot.get("prompt_tokens") or 0) + int(
                    entry.get("prompt_tokens") or 0)
                slot["completion_tokens"] = int(slot.get("completion_tokens") or 0) + int(
                    entry.get("completion_tokens") or 0)
                slot["cost_usd"] = float(slot.get("cost_usd") or 0.0) + float(
                    entry.get("cost_usd") or 0.0)
                total += float(entry.get("cost_usd") or 0.0)
                calls += 1
    except Exception:
        pass
    return round(total, 6), calls, by_model


def _learned_counts(project_id: str, *, db_path: Optional[Path] = None) -> Dict[str, int]:
    conn = _ro_connect(db_path or default_db_path())
    try:
        return {
            "fixes": len(_query(conn, "SELECT 1 FROM working_fixes WHERE project_id = ?",
                                (project_id,))),
            "skills": len(_query(conn, "SELECT 1 FROM project_skills WHERE project_id = ?",
                                 (project_id,))),
            "notes": len(_query(conn, "SELECT 1 FROM project_notes WHERE project_id = ?",
                                (project_id,))),
        }
    finally:
        if conn is not None:
            conn.close()


def _latest_eval(
    project_id: str,
    *,
    db_path: Optional[Path] = None,
    results_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Latest eval run + regression delta.

    The eval harness writes one JSON per run (``results/<run-id>.json``) — there
    is no eval table — so we reuse ``kairos.trend`` to read them. Returns None
    when no runs exist; the report then omits the regression section rather than
    claiming "0 regressions" it never measured.
    """
    candidates: List[Path] = []
    if results_dir:
        candidates.append(Path(results_dir))
    env_dir = os.environ.get("KAIROS_RESULTS_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(REPO_ROOT / "results")

    for directory in candidates:
        if not directory.is_dir():
            continue
        try:
            from kairos.trend import aggregate_trend_from_dir
            trend = aggregate_trend_from_dir(directory, window=20)
        except Exception:
            continue
        runs = list(getattr(trend, "runs", None) or [])
        if not runs:
            continue
        last = runs[-1]
        prev = runs[-2] if len(runs) > 1 else None
        out: Dict[str, Any] = {
            "cases": int(getattr(last, "cases", 0) or 0),
            "passed": int(getattr(last, "passed", 0) or 0),
            "pass_rate": float(getattr(last, "pass_rate", 0.0) or 0.0),
            "suite": str(getattr(last, "suite_name", "") or ""),
            "run_id": str(getattr(last, "run_id", "") or ""),
            "created_at": str(getattr(last, "timestamp", "") or ""),
            "runs_seen": len(runs),
            "cost_usd": float(getattr(last, "cost_usd", 0.0) or 0.0),
            "window": int(getattr(trend, "window", 0) or 0),
        }
        if prev is not None:
            out["pass_rate_delta"] = out["pass_rate"] - float(
                getattr(prev, "pass_rate", 0.0) or 0.0)
        return out
    return None


def write_report(report: GateReport, out: Path, *, fmt: str = "html", lang: str = "en") -> Path:
    """Render and write the report. Extension is honoured when `out` has none."""
    fmt = (fmt or "html").lower()
    if out.suffix == "":
        out = out.with_suffix({"html": ".html", "md": ".md", "json": ".json"}[fmt])
    out.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    elif fmt == "md":
        payload = report.to_markdown(lang)
    else:
        payload = report.to_html(lang)
    out.write_text(payload, encoding="utf-8", newline="\n")
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kairos gate",
        description="Gate Report — the receipt for a LoopReview session.",
    )
    sub = parser.add_subparsers(dest="command")
    report = sub.add_parser("report", help="generate a report for one project")
    report.add_argument("--project", required=True,
                        help="project id, id prefix or name")
    report.add_argument("--out", default="gate-report",
                        help="output path (extension optional, default gate-report.html)")
    report.add_argument("--format", choices=["html", "md", "json"], default="html")
    report.add_argument("--lang", choices=["en", "zh"], default="en")
    report.add_argument("--session", default=None, help="restrict to one loop session")
    report.add_argument("--db", default=None,
                        help="SQLite path (default: the app's data dir)")
    report.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "report":
        build_parser().print_help()
        return 3

    db_path = Path(args.db) if args.db else default_db_path()
    project = find_project(args.project, db_path=db_path)
    if project is None:
        print(f"project not found: {args.project} (db: {db_path})", file=sys.stderr)
        return 3

    report = collect(str(project["id"]), db_path=db_path, session_id=args.session)
    out = write_report(report, Path(args.out), fmt=args.format, lang=args.lang)
    if not args.quiet:
        print(f"{report.badge_line(args.lang)}")
        print(f"{out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
