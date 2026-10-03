"""Intake — turn a task document of *any* shape into a task record.

The main agent (Claude Code, Codex, a human, CI) writes the task however it
likes. **Nothing in this module may require a format from the caller.** No
frontmatter, no headings, no keywords: if we demanded any of that, every
main-agent upgrade, every different author, every hand-written task would be a
chance for the whole pipeline to stop working. The importer bends; the pipe
does not.

What we accept and what we promise
----------------------------------
Input:  arbitrary text (Markdown, plain prose, a numbered list, Chinese,
        English, a pasted chat log) plus the repo it lands in.
Output: :class:`IntakeResult` — a *deterministic, validated* record we own:
        task units, the assumptions we filled in, the questions we could not
        answer, and a risk level.

The caller's document is only ever **material**. Every field we need comes
from one of three places, in this order:

  1. the document (if it happens to say so),
  2. the repository itself — ``AGENTS.md``, ``README``, the tree, recent
     commits, uncommitted changes (:class:`RepoFacts`),
  3. a conservative default, recorded as an **assumption** so it is visible
     rather than silent.

Things we refuse to guess: scope, permissions, acceptance. Those are either
stated, derived from the repo, or turned into a question. Guessing scope wrong
means damaging someone else's files; guessing acceptance wrong means half an
hour of work the reviewer throws away.

Provenance is checked mechanically
----------------------------------
Every unit carries ``source_quote``, and a quote that is not literally present
in the document is discarded (with a note) rather than trusted. That check is
what keeps a summarising model from inventing a task we were never asked to
do — and it is a substring test, not a judgement call.

Degradation, never failure
--------------------------
No model, a broken model, or an unparseable answer all fall back to
:func:`heuristic_units`, which splits the document structurally. A heuristic
result is honest about itself (``extracted_by="heuristic"``, low confidence,
plus a question), so a scheduled run still makes progress instead of dying.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import string
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# How much of the document we show the model. A task book longer than this is
# probably a whole project plan; head+tail keeps the intent and the acceptance
# criteria, which is where the signal is.
_MAX_SOURCE_CHARS = 24000
_MAX_UNITS = 12
_MAX_GOAL_CHARS = 1200
_MAX_QUOTE_CHARS = 400

# Destructive / privileged things. Matching one does NOT block the task — a
# document may perfectly well say "never touch .env" — but it is recorded as a
# caution, pushes the risk up, and asks for confirmation.
_CAUTION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)\b(rm\s+-rf|rd\s+/s|del\s+/[sq]|mkfs|diskpart|format\s+[a-z]:)\b",
     "destructive file/system command"),
    (r"(?i)(api[ _-]?key|secret|password|passwd|access[ _-]?token|credential"
     r"|\.env\b|id_rsa|private[ _-]?key)", "credentials"),
    (r"(?i)(c:\\windows|/etc/|/usr/|~/\.[a-z]+/|\bsudo\b|\breg\s+(add|delete)\b"
     r"|注册表|提权)", "system paths or privilege escalation"),
    (r"(?i)(push\s+(-f|--force)|reset\s+--hard|drop\s+table|truncate\s+table"
     r"|shutdown|reboot|删库|清空数据库)", "irreversible action"),
)

# Lines that read like something we could run to verify the work.
_ACCEPTANCE_HINT = re.compile(
    r"(?i)(pytest|npm\s+(run\s+)?test|yarn\s+test|pnpm\s+test|make\s+\w+|"
    r"cargo\s+(test|build|clippy)|go\s+test|dotnet\s+test|mvn\s+\w+|"
    r"gradle\s+\w+|ruff|eslint|tsc\b|mypy|black\s+--check|"
    r"python\s+-m\s+\w+|bash\s+\w+\.sh|\./\w+\.sh)"
)

# Paths and globs that appear as bare tokens, e.g. src/auth/**, backend/app.py
_PATH_HINT = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)+[\w.*-]+|[\w-]+\.(?:py|ts|tsx|js|jsx|go|rs|java"
    r"|kt|cs|rb|php|c|cc|cpp|h|hpp|sql|md|json|ya?ml|toml|ini|cfg))(?![\\\w-])"
)

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")

# Phrases that mark a non-goal in either language, so "what not to do" is
# captured wherever the author put it.
_OUT_OF_SCOPE_HINT = re.compile(
    r"(?i)(out\s+of\s+scope|non-?goal|do\s+not\s+(touch|change|modify)|"
    r"don'?t\s+(touch|change|modify)|不要(动|改|碰|删)|别(动|改|碰|删)|"
    r"不涉及|不在范围|不包含|不含|非目标|排除)"
)

_DANGER_CONTEXT = 300


# ---------------------------------------------------------------------------
# The record we own
# ---------------------------------------------------------------------------


class TaskUnit(BaseModel):
    """One piece of work, small enough to finish and review on its own."""

    unit_id: str = ""
    title: str = ""
    goal: str = ""
    scope_paths: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    source_quote: str = ""
    confidence: float = 0.5


class IntakeResult(BaseModel):
    """What we understood. Deterministic, persisted, and shown back to the
    caller so a misunderstanding is caught before the work, not after."""

    schema_version: int = SCHEMA_VERSION
    task_id: str = ""
    source_file: str = ""
    source_sha256: str = ""
    extracted_by: str = "heuristic"        # "model" | "heuristic"
    units: list[TaskUnit] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    risk: str = "low"                      # low | medium | high
    needs_confirmation: bool = False
    blocked: bool = False                  # nothing actionable could be formed
    notes: str = ""

    @property
    def actionable(self) -> bool:
        return bool(self.units) and not self.blocked


# ---------------------------------------------------------------------------
# Grounding in the repository
# ---------------------------------------------------------------------------


@dataclass
class RepoFacts:
    """The part of the picture the document usually leaves out.

    A task book says *what*, rarely *where* or *how we verify things here*.
    The repository knows both, and reading it ourselves is what lets us accept
    a document in any shape at all.
    """

    root: Path
    tree: list[str] = field(default_factory=list)
    agents_md: str = ""
    readme: str = ""
    recent_commits: list[str] = field(default_factory=list)
    dirty: list[str] = field(default_factory=list)
    acceptance_hints: list[str] = field(default_factory=list)

    @classmethod
    def gather(cls, root: str | Path, *, depth_files: int = 60) -> RepoFacts:
        """Read the repo. Never raises: a missing git or a huge tree is a
        thinner picture, not a failure."""
        base = Path(root).expanduser()
        facts = cls(root=base)
        try:
            facts.agents_md = _read_capped(base / "AGENTS.md", 2000)
            if not facts.agents_md:
                facts.agents_md = _read_capped(base / "CLAUDE.md", 2000)
            facts.readme = _read_capped(base / "README.md", 1200)
            facts.tree = _list_tree(base, limit=depth_files)
            facts.recent_commits = _git(base, ["log", "--oneline", "-n", "12"])
            facts.dirty = _git(base, ["status", "--porcelain"])[:20]
            facts.acceptance_hints = _detect_acceptance(facts)
        except Exception as exc:  # noqa: BLE001 - grounding is best effort
            logger.debug("repo grounding partial for %s: %s", base, exc)
        return facts

    def as_prompt_block(self) -> str:
        """A compact digest for the extraction prompt."""
        parts: list[str] = [f"repo root: {self.root}"]
        if self.tree:
            parts.append("top-level files:\n  " + "\n  ".join(self.tree[:40]))
        if self.agents_md:
            parts.append("AGENTS.md (project rules):\n" + self.agents_md)
        if self.readme:
            parts.append("README (excerpt):\n" + self.readme)
        if self.recent_commits:
            parts.append("recent commits:\n  " + "\n  ".join(
                self.recent_commits[:10]))
        if self.dirty:
            parts.append("uncommitted files:\n  " + "\n  ".join(self.dirty))
        if self.acceptance_hints:
            parts.append("commands this repo uses to verify work:\n  "
                         + "\n  ".join(self.acceptance_hints))
        return "\n\n".join(parts)


def _read_capped(path: Path, limit: int) -> str:
    try:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _list_tree(root: Path, limit: int = 60) -> list[str]:
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
            "build", ".mypy_cache", ".ruff_cache", ".pytest_cache", ".kairos"}
    out: list[str] = []
    try:
        for entry in sorted(root.iterdir()):
            if entry.name in skip:
                continue
            out.append(entry.name + ("/" if entry.is_dir() else ""))
            if len(out) >= limit:
                break
    except OSError:
        pass
    return out


def _git(root: Path, args: list[str]) -> list[str]:
    """Run one git command. A non-repo answers with an empty list, which is
    a legitimate answer, not an error."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True,
            timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _detect_acceptance(facts: RepoFacts) -> list[str]:
    """Guess how this repo verifies work, from what it already declares."""
    hints: list[str] = []
    text = "\n".join([facts.agents_md, facts.readme])
    for line in text.splitlines():
        stripped = line.strip(" -*`\t")
        if stripped and _ACCEPTANCE_HINT.search(stripped) and len(stripped) < 160:
            hints.append(stripped)
    for name in ("pyproject.toml", "package.json", "Makefile"):
        snippet = _read_capped(facts.root / name, 4000)
        for line in snippet.splitlines():
            if _ACCEPTANCE_HINT.search(line):
                hints.append(f"{name}: {line.strip()[:120]}")
    seen: set[str] = set()
    unique: list[str] = []
    for hint in hints:
        if hint not in seen:
            seen.add(hint)
            unique.append(hint)
    return unique[:8]


# ---------------------------------------------------------------------------
# Deterministic scans
# ---------------------------------------------------------------------------


def scan_cautions(text: str) -> list[str]:
    """Flag destructive or privileged subject matter.

    Deliberately *not* a veto: a document is allowed to mention credentials
    (usually to forbid them). The flag travels with the result so a policy can
    decide, instead of a regex silently refusing work.
    """
    found: list[str] = []
    for pattern, label in _CAUTION_PATTERNS:
        for match in re.finditer(pattern, text):
            start = max(0, match.start() - _DANGER_CONTEXT // 2)
            excerpt = text[start:start + _DANGER_CONTEXT].replace("\n", " ")
            found.append(f"{label}: …{excerpt.strip()}…")
            break  # one example per category is enough to be actionable
    return found


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split on Markdown headings; a document without headings becomes one
    paragraph-safe section per blank-line block. Returns (title, body)."""
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_title = ""
    current: list[str] = []
    saw_heading = False
    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            saw_heading = True
            if current_title or any(s.strip() for s in current):
                sections.append((current_title, current))
            current_title = heading.group(2).strip()
            current = []
        else:
            current.append(line)
    if current_title or any(s.strip() for s in current):
        sections.append((current_title, current))

    if not saw_heading:
        blocks = re.split(r"\n\s*\n", text)
        sections = [("", [b]) for b in blocks if b.strip()]

    out: list[tuple[str, str]] = []
    for title, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if not body and not title:
            continue
        out.append((title, body))
    return out


def heuristic_units(text: str, *, limit: int = _MAX_UNITS) -> list[TaskUnit]:
    """Structural extraction — no model involved.

    Used when there is no model, when the model fails, and as the floor the
    caller can always fall back to. It is intentionally dumb and predictable:
    headings become units, commands become acceptance, paths become scope.
    """
    units: list[TaskUnit] = []
    for title, body in _split_sections(text):
        if not body.strip() and not title.strip():
            continue
        units.append(_unit_from_text(title, body, index=len(units)))
        if len(units) >= limit:
            break
    if not units and text.strip():
        units.append(_unit_from_text("", text, index=0))
    return units


def _unit_from_text(title: str, body: str, *, index: int) -> TaskUnit:
    lines = body.splitlines()
    acceptance = [ln.strip(" -*`") for ln in lines
                  if _ACCEPTANCE_HINT.search(ln) and len(ln) < 200]
    paths: list[str] = []
    for match in _PATH_HINT.finditer(body):
        token = match.group(1)
        if token not in paths:
            paths.append(token)
    out_of_scope = [ln.strip(" -*") for ln in lines
                    if _OUT_OF_SCOPE_HINT.search(ln) and len(ln) < 200]
    goal = body.strip() or title.strip()
    goal = goal[:_MAX_GOAL_CHARS]
    deliverables: list[str] = []
    for line in lines:
        lowered = line.lower()
        if any(word in lowered for word in
               ("deliverable", "交付", "产出", "produce")):
            deliverables.append(line.strip(" -*")[:200])
    return TaskUnit(
        unit_id=f"u{index + 1}",
        title=(title or goal.splitlines()[0][:80]).strip(),
        goal=goal,
        scope_paths=paths[:20],
        deliverables=deliverables[:10],
        acceptance=acceptance[:10],
        out_of_scope=out_of_scope[:10],
        source_quote=body.strip()[:_MAX_QUOTE_CHARS],
        confidence=0.35,
    )


# ---------------------------------------------------------------------------
# Model-backed extraction
# ---------------------------------------------------------------------------


_PROMPT = """You are the intake step for an autonomous software worker. Somebody \
else's agent wrote the task document below in whatever shape it liked. Your job \
is to say what we are being asked to do — not to rewrite it, not to improve it, \
and above all not to invent work.

Rules:
- Copy wording from the document. Every unit needs a `source_quote` that appears \
verbatim in the document, because we check it.
- One unit per independently reviewable piece of work. If the document splits a \
project into modules, that is one unit per module. If it describes one change, \
that is one unit.
- Leave a field empty when the document does not say. Do not fill gaps with \
plausible-sounding specifics. Use `questions` for anything you genuinely need \
and `assumptions` for defaults you would take.
- Commands that verify the work go in `acceptance`. If the document names none \
and the repo names none, leave it empty — we will run the repo's own checks.
- Reply with JSON only. No prose, no code fences.

JSON shape:
{"units":[{"title":str,"goal":str,"scope_paths":[str],"deliverables":[str],\
"acceptance":[str],"out_of_scope":[str],"depends_on":[str],"source_quote":str,\
"confidence":0.0-1.0}],"assumptions":[str],"questions":[str],"risk":"low|medium|high"}

--- repository facts ---
$facts

--- task document (verbatim) ---
$source
--- end of task document ---
"""


def _extract_json(text: str) -> dict | None:
    """Pull a JSON object out of a model answer, tolerating fences and chat."""
    if not text:
        return None
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        loaded = json.loads(cleaned)
        return loaded if isinstance(loaded, dict) else None
    except ValueError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            loaded = json.loads(cleaned[start:end + 1])
            return loaded if isinstance(loaded, dict) else None
        except ValueError:
            return None
    return None


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def verify_quote(quote: str, source: str) -> bool:
    """A quote we can check is a claim; one we cannot is a guess.

    Whitespace-insensitive, because reformatting a quote is not the same as
    inventing one."""
    if not quote or not quote.strip():
        return False
    return _normalise(quote) in _normalise(source)


def units_from_payload(payload: dict, source: str) -> tuple[list[TaskUnit], list[str]]:
    """Turn a model payload into units, discarding anything we cannot verify.

    Returns ``(units, notes)`` where the notes explain what was dropped — a
    dropped unit is information about the model, not something to hide.
    """
    notes: list[str] = []
    units: list[TaskUnit] = []
    raw_units = payload.get("units")
    if not isinstance(raw_units, list):
        return [], ["model returned no units list"]
    for index, raw in enumerate(raw_units[:_MAX_UNITS]):
        if not isinstance(raw, dict):
            continue
        quote = str(raw.get("source_quote") or "")[:_MAX_QUOTE_CHARS]
        if not verify_quote(quote, source):
            if quote:
                notes.append(f"dropped an unverifiable quote for unit {index + 1}")
            quote = ""
        confidence = raw.get("confidence", 0.5)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = min(1.0, max(0.0, confidence))
        if not quote:
            # No provenance means we cannot show where this came from. Keep it
            # if it has a goal, but stop pretending we are sure.
            confidence = min(confidence, 0.4)
        goal = str(raw.get("goal") or "").strip()[:_MAX_GOAL_CHARS]
        title = str(raw.get("title") or "").strip()[:120]
        if not goal and not title:
            continue
        units.append(TaskUnit(
            unit_id=f"u{index + 1}",
            title=title or goal.splitlines()[0][:80],
            goal=goal or title,
            scope_paths=_string_list(raw.get("scope_paths"), 20),
            deliverables=_string_list(raw.get("deliverables"), 10),
            acceptance=_string_list(raw.get("acceptance"), 10),
            out_of_scope=_string_list(raw.get("out_of_scope"), 10),
            depends_on=_string_list(raw.get("depends_on"), 10),
            source_quote=quote,
            confidence=confidence,
        ))
    return units, notes


def _string_list(value: Any, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[:limit]:
        text = str(item).strip()[:300]
        if text:
            out.append(text)
    return out


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------


def task_id_from(source_file: str, text: str) -> str:
    """Prefer the filename the caller chose (``T001-auth.md`` → ``T001``);
    otherwise derive a stable id from the content, so the same document
    always lands on the same task."""
    name = Path(source_file).stem if source_file else ""
    match = re.match(r"^\s*(T?\d{1,4}[A-Za-z]?)\b", name)
    if match:
        return match.group(1)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")[:32]
    digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}" if slug else f"task-{digest}"


class Intake:
    """Accept a task document of any shape and produce our own record of it.

    ``llm`` may be either an object with ``async complete(messages)`` (what the
    agents use) or an async callable taking a prompt and returning text. Pass
    nothing and the intake runs structurally — useful for CI, for tests, and
    for the case where no key is configured.
    """

    def __init__(self, llm: Any = None, *, timeout: float = 120.0,
                 cache_dir: Path | None = None) -> None:
        self._llm = llm
        self._timeout = timeout
        self._cache_dir = cache_dir

    # -- public ----------------------------------------------------------

    async def accept(self, text: str, *, source_file: str = "",
                     facts: RepoFacts | None = None,
                     task_id: str = "", refresh: bool = False,
                     prior_notes: list[str] | None = None) -> IntakeResult:
        """Read the document and return our record. Never raises for input
        reasons: bad input produces a blocked result with a question."""
        raw = text or ""
        canonical = raw.replace("\r\n", "\n")
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        resolved_id = task_id or task_id_from(source_file, canonical)

        if not refresh:
            cached = self._load_cache(digest)
            if cached is not None:
                cached.task_id = cached.task_id or resolved_id
                cached.source_file = cached.source_file or source_file
                return cached

        result = IntakeResult(
            task_id=resolved_id,
            source_file=source_file,
            source_sha256=digest,
        )

        if not canonical.strip():
            result.blocked = True
            result.risk = "low"
            result.questions = [
                "任务文档是空的 —— 我没有任何可执行的内容。"
                "请把任务写进文档，或者告诉我这次要做的是什么。",
            ]
            result.notes = "empty document"
            self._save_cache(digest, result)
            return result

        result.cautions = scan_cautions(canonical)

        payload = await self._ask_model(canonical, facts, prior_notes)
        if payload is not None:
            units, notes = units_from_payload(payload, canonical)
            if units:
                result.units = units
                result.extracted_by = "model"
                result.assumptions = _string_list(
                    payload.get("assumptions"), 12)
                result.questions = _string_list(payload.get("questions"), 12)
                result.risk = _clamp_risk(payload.get("risk"))
                if notes:
                    result.notes = "; ".join(notes)
            else:
                result.notes = ("model extraction produced nothing usable: "
                                + ("; ".join(notes) or "no units"))

        if not result.units:
            # Degrade, do not fail. Say plainly which path produced this.
            result.units = heuristic_units(canonical)
            result.extracted_by = "heuristic"
            result.questions.append(
                "没有模型可用（或模型没能给出可用结果），我按文档结构做了机械切分，"
                "请确认这样理解对不对。"
            )
            result.assumptions.append(
                "按标题/段落把文档切成任务单元；每个单元的验收标准从文中出现的"
                "命令推断，可能不准。"
            )

        if result.extracted_by == "heuristic":
            result.risk = _risk_from_heuristics(result)

        self._finalise(result, facts)
        self._save_cache(digest, result)
        return result

    # -- internals -------------------------------------------------------

    async def _ask_model(self, source: str, facts: RepoFacts | None,
                         prior_notes: list[str] | None) -> dict | None:
        if self._llm is None:
            return None
        body = source
        if len(body) > _MAX_SOURCE_CHARS:
            half = _MAX_SOURCE_CHARS // 2
            body = (body[:half] + "\n\n… [truncated] …\n\n" + body[-half:])
        fact_block = facts.as_prompt_block() if facts else "(not available)"
        if prior_notes:
            fact_block += ("\n\nnotes carried over from earlier tasks in this "
                          "repo:\n  " + "\n  ".join(prior_notes[:10]))
        prompt = string.Template(_PROMPT).safe_substitute(
            facts=fact_block, source=body)
        try:
            raw = await self._complete(prompt)
        except Exception as exc:  # noqa: BLE001 - fall back, never fail
            logger.warning("intake model call failed (%s); using structure", exc)
            return None
        payload = _extract_json(raw)
        if payload is None:
            logger.warning("intake model returned unparseable output")
        return payload

    async def _complete(self, prompt: str) -> str:
        import asyncio

        if callable(self._llm) and not hasattr(self._llm, "complete"):
            return await asyncio.wait_for(self._llm(prompt), self._timeout) or ""

        from kairos.llm.base import LLMMessage

        async def _run() -> str:
            response = await self._llm.complete(
                [LLMMessage(role="user", content=prompt)])
            return getattr(response, "content", "") or ""

        return await asyncio.wait_for(_run(), self._timeout)

    def _finalise(self, result: IntakeResult, facts: RepoFacts | None) -> None:
        """Record the defaults we took, and decide whether a human (or the
        main agent) has to confirm before we act."""
        no_acceptance = [u for u in result.units if not u.acceptance]
        if no_acceptance:
            hint = ""
            if facts and facts.acceptance_hints:
                hint = "（仓库里看到：" + "; ".join(facts.acceptance_hints[:3]) + "）"
            result.assumptions.append(
                f"{len(no_acceptance)} 个单元没写验收标准，我会自己跑仓库的检查并"
                f"如实报告跑了什么{hint}。"
            )
        scoped = [u for u in result.units if u.scope_paths]
        if len(scoped) < len(result.units):
            result.assumptions.append(
                "部分单元没声明改动范围；默认在整个仓库内工作，但只在交付分支上"
                "改动，供你审核 diff。"
            )
        if not any(u.out_of_scope for u in result.units):
            result.assumptions.append(
                "文档没写「不要做什么」，我按「只改与目标直接相关的文件」执行。"
            )

        if result.cautions:
            result.questions.append(
                "文档涉及破坏性或特权操作，请明确授权范围："
                + "；".join(c.split(":")[0] for c in result.cautions)
            )

        weak = [u for u in result.units if u.confidence < 0.5]
        if weak and result.extracted_by == "model":
            result.assumptions.append(
                f"{len(weak)} 个单元依据不足（文档里找不到确切出处），动手前值得"
                f"确认一下。"
            )

        if not result.units:
            result.blocked = True
            if not result.questions:
                result.questions.append("我无法从文档里提取出任何可执行的任务，请补充说明。")

        result.needs_confirmation = bool(
            result.blocked
            or result.risk == "high"
            or result.cautions
        )

        if result.extracted_by == "heuristic" and result.units:
            result.assumptions.append(
                "机械切分的结果：每个单元的边界就是文档里的标题/段落边界。"
            )

    def _load_cache(self, digest: str) -> IntakeResult | None:
        if self._cache_dir is None:
            return None
        path = self._cache_dir / f"{digest}.json"
        try:
            if path.exists():
                return IntakeResult.model_validate_json(
                    path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("intake cache unreadable (%s)", exc)
        return None

    def _save_cache(self, digest: str, result: IntakeResult) -> None:
        if self._cache_dir is None:
            return
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._cache_dir / f"{digest}.json"
            path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.debug("intake cache unwritable (%s)", exc)


def _clamp_risk(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in ("low", "medium", "high") else "medium"


def _risk_from_heuristics(result: IntakeResult) -> str:
    """Structural extraction is always less certain; say so in the risk, so a
    policy that gates on risk gates on the right thing."""
    if len(result.units) > 3:
        return "medium"
    if any(len(u.scope_paths) > 8 for u in result.units):
        return "medium"
    return "medium"


# ---------------------------------------------------------------------------
# Rendering: what the caller sees
# ---------------------------------------------------------------------------


def render_understanding(result: IntakeResult) -> str:
    """The "here is what I understood" block.

    This is the cheapest alignment tool in the pipeline: put it at the top of
    the result and a misunderstanding shows up before the work is reviewed,
    instead of after."""
    lines = [f"## 我这样理解的（{result.task_id}）", ""]
    lines.append(f"- 依据：`{result.source_file or '(未命名文档)'}` "
                 f"sha256 `{result.source_sha256[:12]}`")
    lines.append(f"- 提取方式：{'模型' if result.extracted_by == 'model' else '按文档结构机械切分'}"
                 f" · 风险：{result.risk}"
                 + (" · **需要确认**" if result.needs_confirmation else ""))
    lines.append(f"- 任务单元：{len(result.units)} 个")
    lines.append("")
    for unit in result.units:
        lines.append(f"### {unit.unit_id} {unit.title}")
        lines.append(f"**目标**：{unit.goal.splitlines()[0][:300]}")
        if unit.scope_paths:
            lines.append(f"**范围**：{', '.join(unit.scope_paths[:10])}")
        if unit.deliverables:
            lines.append(f"**交付**：{'; '.join(unit.deliverables[:5])}")
        if unit.acceptance:
            lines.append(f"**验收**：{'; '.join(unit.acceptance[:5])}")
        if unit.out_of_scope:
            lines.append(f"**不做**：{'; '.join(unit.out_of_scope[:5])}")
        if unit.depends_on:
            lines.append(f"**依赖**：{', '.join(unit.depends_on)}")
        if unit.source_quote:
            lines.append(f"**原文依据**：> {unit.source_quote[:200]}")
        lines.append(f"**把握**：{unit.confidence:.2f}")
        lines.append("")
    if result.assumptions:
        lines.append("### 我替你做的默认")
        lines.extend(f"- {item}" for item in result.assumptions)
        lines.append("")
    if result.cautions:
        lines.append("### ⚠️ 注意")
        lines.extend(f"- {item}" for item in result.cautions)
        lines.append("")
    return "\n".join(lines)


def render_questions(result: IntakeResult) -> str:
    """The questions file, for the main agent to answer in prose. We do not
    ask it to fill in a form — we read its answer the same way we read the
    task document."""
    if not result.questions:
        return ""
    lines = [f"# 需要你确认的问题（{result.task_id}）", "",
             "请用你自己的话回答任意一条，写在这个文件下面就行 —— "
             "不需要任何格式，我会自己读。", ""]
    for index, question in enumerate(result.questions, 1):
        lines.append(f"{index}. {question}")
    lines.append("")
    return "\n".join(lines)
