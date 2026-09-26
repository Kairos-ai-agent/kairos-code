"""The one place a tool call is allowed to become an action.

Muse calls its version Sentinel: a separate authority that gates actions and
network egress, which the agent can propose to but never override. Kairos had a
permission policy and an approval ladder, and neither was wired to execution --
``PermissionPolicy`` was constructed in exactly one route and no tool call ever
consulted it. A policy nothing reads is not a policy.

This module is the missing enforcement point, and it is deliberately one
function: every built-in tool and every MCP tool reaches the outside world
through ``Agent._dispatch_tool_with_args``, so that is where the ruling happens.

What it enforces, in order
--------------------------
1. **Explicit user rules win.** ``PermissionPolicy`` DENY and ALLOW rules are
   standing instructions from the person who owns the machine, so they outrank
   everything below -- including the taint rules. That is also the documented
   escape hatch: when the gate denies something a user wants, the denial message
   hands them the exact rule to add.
2. **Credential stores are never the agent's business.** Private keys, cloud
   credentials, ``.netrc``/``.git-credentials`` and this app's own settings file
   are refused outright, read or write, in every mode.
3. **Tainted egress.** Once a run has read something it cannot vouch for -- a web
   page, a third-party MCP server's output -- actions that send data out (network
   tools, ``git push``, or a shell command invoking curl/wget/ssh/nc) are refused.
   This is the link that turns a prompt injection into an exfiltration, and it is
   the reason the gate exists.
4. **Tainted secrets.** In a tainted run, reading a ``.env``/secrets file is
   refused too. The write side stays allowed: adding a variable to ``.env`` is a
   normal development task and shows up in the diff.

What it deliberately does not do
--------------------------------
The approval ladder (SUGGEST / EDIT / FULL_AUTO) stays advisory, because the app
has no channel that can ask the user and wait for an answer -- there is an
``/approval/decide`` endpoint that computes a verdict and nothing that blocks.
Shipping a gate that turned every ASK into a refusal would lock the user out of
their own agent on the first file write. Set ``KAIROS_SENTINEL_STRICT=1`` to make
ASK mean deny once an approval channel exists, and see ``Ruling.strict``.

Failures inside the gate fail open, loudly
------------------------------------------
A bug in the gate must not brick the agent, so an exception inside
``authorize()`` allows the call, logs a warning, and records an ``error`` entry in
the audit trail. Silent fallbacks are how a gate rots: this one leaves a trace.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kairos.approval import ApprovalMode
from kairos.permissions import Decision, PermissionPolicy, PermissionRule
from kairos.taint import TaintTracker, is_network_tool, mcp_server_of

logger = logging.getLogger(__name__)

# Shell verbs that move data off the machine (or pull code in). Matched against a
# command string, so keep them specific: "git push" is egress, "git status" is not.
EGRESS_COMMAND_MARKERS: Tuple[str, ...] = (
    "curl", "wget", "nc ", "ncat", "netcat", "telnet", "ssh ", "scp ", "sftp",
    "rsync", "invoke-webrequest", "invoke-restmethod", "iwr ", "irm ",
    "http.client", "urlopen", "requests.get", "requests.post", "httpx",
    "git push", "git remote add", "docker push", "npm publish", "twine upload",
    "pip install", "pip3 install", "npm install", "npm i ", "yarn add",
    "pnpm add", "cargo install", "go install", "apt-get", "choco install",
    "winget install", "certutil -urlcache", "bitsadmin",
)

# Reading a private key or a cloud credential is never part of a coding task.
CREDENTIAL_PATH_MARKERS: Tuple[str, ...] = (
    ".ssh/id_rsa", ".ssh/id_dsa", ".ssh/id_ecdsa", ".ssh/id_ed25519",
    ".ssh/identity", "id_rsa", "id_ed25519", ".aws/credentials", ".aws/config",
    ".netrc", "_netrc", ".git-credentials", ".docker/config.json",
    ".kube/config", ".npmrc", ".pypirc", "keystore.jks", ".kdbx",
)

# Files that hold secrets but are also edited in the course of normal work:
# reading them in a tainted run is refused, writing them is not.
SECRET_FILE_MARKERS: Tuple[str, ...] = (
    ".env", "secrets.json", "credentials.json", "secret.yaml", "secrets.yaml",
    "settings.json",
)

# Tools whose whole job is to write. Used only for reporting and for the strict
# ladder; the taint rules do not depend on this classification.
WRITE_TOOLS = frozenset({"file_write", "file_edit", "apply_patch", "str_replace"})

# Redactions applied to anything this module stores or prints.
_REDACTIONS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "sk-<redacted>"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"), "sk-ant-<redacted>"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"), "<github-token-redacted>"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{16,}"), "<github-token-redacted>"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<aws-key-redacted>"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}"), "Bearer <redacted>"),
    (re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key)(\s*[=:]\s*)\S+"),
     r"\1\2<redacted>"),
)


def redact(text: str) -> str:
    """Strip credential-shaped substrings. Applied before anything is stored."""
    if not text:
        return text
    out = text
    for pattern, replacement in _REDACTIONS:
        out = pattern.sub(replacement, out)
    return out


def digest(args: Any) -> str:
    """A stable fingerprint of the arguments, so the log need not hold them."""
    try:
        blob = json.dumps(args, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        blob = repr(args)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]


# Argument names worth putting in the audit trail, most specific first.
_RESOURCE_KEYS = (
    "url", "command", "path", "file_path", "pattern", "query", "subcommand",
    "task", "label",
)


def resource_of(tool: str, args: Any) -> str:
    """The most useful single string describing what a call touches."""
    if isinstance(args, str):
        return redact(args[:400])
    if not isinstance(args, dict):
        return ""
    for key in _RESOURCE_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return redact(value.strip()[:400])
    return ""


def is_egress(tool: str, args: Any) -> Tuple[bool, str]:
    """Does this call send data out (or pull code in)?"""
    if is_network_tool(tool):
        return True, f"{tool} reaches the network"
    if tool == "git":
        sub = ""
        if isinstance(args, dict):
            sub = str(args.get("subcommand") or "")
            sub += " " + str(args.get("args") or "")
        if "push" in sub.lower() or "remote add" in sub.lower():
            return True, "git push publishes the repository"
        return False, ""
    if tool == "terminal":
        command = ""
        if isinstance(args, dict):
            command = str(args.get("command") or "")
        lowered = command.lower()
        for marker in EGRESS_COMMAND_MARKERS:
            if marker in lowered:
                return True, f"shell command uses {marker.strip()!r}"
        return False, ""
    if tool.startswith("mcp_") and tool.endswith(("__fetch", "__request", "__post",
                                                  "__send", "__upload", "__publish")):
        return True, f"{tool} looks like an outbound call"
    return False, ""


def _app_key_store_paths() -> Tuple[str, ...]:
    """This app's own credential file, in the shapes a path might arrive in.

    ``settings.json`` on its own is too generic a marker -- a repository is full of
    ``settings.json`` files the agent may legitimately edit. The app's key store is
    identified by its actual location, plus the relative form used in this repo.
    """
    paths = ["data/settings.json"]
    try:
        from kairos.config.settings import settings
        base = getattr(settings, "data_dir", None)
        if base:
            paths.append(str(Path(base) / "data" / "settings.json"))
            paths.append(str(Path(base) / "settings.json"))
    except Exception:  # noqa: BLE001
        pass
    return tuple(p.replace("\\", "/").lower() for p in paths)


def _is_app_key_store(resource: str) -> bool:
    target = (resource or "").replace("\\", "/").lower()
    if not target:
        return False
    return any(target == p or target.endswith("/" + p) for p in _app_key_store_paths())


def _matches_any(text: str, markers: Tuple[str, ...]) -> Optional[str]:
    lowered = (text or "").replace("\\", "/").lower()
    for marker in markers:
        if marker in lowered:
            return marker
    return None


@dataclass(frozen=True)
class Ruling:
    """The gate's verdict on one tool call."""

    tool: str
    resource: str
    decision: str            # "allow" | "deny"
    reason: str
    rule: str
    tainted: bool
    origin: str = "agent"
    strict: bool = False
    at: float = field(default_factory=time.time)

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    @property
    def denied(self) -> bool:
        return self.decision == "deny"

    def message(self) -> str:
        """The text handed back to the model when a call is refused."""
        if self.allowed:
            return ""
        lines = [
            f"Refused by the Kairos gate: {self.reason}",
            f"  tool     : {self.tool}",
        ]
        if self.resource:
            lines.append(f"  target   : {self.resource}")
        if self.tainted:
            lines.append(
                "  because  : this run has already read content it cannot vouch"
                " for, so an action that sends data out could be exfiltrating"
                " whatever a page or a third-party server put in its context."
            )
        api_hint = (
            "  to allow : the user can grant a standing rule: POST"
            ' /api/sentinel/allow with {"tool": "' + self.tool + '", "pattern": "*"}'
        )
        file_hint = (
            " or add it to ~/.kairos/permissions.yaml as"
            ' permissions: {allow: ["' + self.tool + '(*)"}].'
        )
        lines.append(api_hint + file_hint)
        lines.append(
            "  note     : do not try to work around this by another route;"
            " report it to the user instead."
        )
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "at": datetime.fromtimestamp(self.at, timezone.utc).isoformat(),
            "tool": self.tool,
            "resource": self.resource,
            "decision": self.decision,
            "rule": self.rule,
            "reason": self.reason,
            "tainted": self.tainted,
            "origin": self.origin,
        }


def sentinel_dir() -> Path:
    """Where the gate keeps its own state: the audit trail and the allow rules."""
    override = os.environ.get("KAIROS_SENTINEL_AUDIT_DIR")
    if override:
        return Path(override)
    try:
        from kairos.config.settings import settings
        base = Path(getattr(settings, "data_dir", None) or "data")
    except Exception:  # noqa: BLE001
        base = Path(os.environ.get("KAIROS_DATA_DIR", "data"))
    return base / "sentinel"


_ALLOW_FILE = "permissions.yaml"


def allow_file() -> Path:
    """The file the "allow this" endpoint writes, and the gate reads back.

    Kept separate from the user's own ``~/.kairos/permissions.yaml``: the app adds
    standing rules the user asked for, and never rewrites a file it does not own.
    """
    return sentinel_dir() / _ALLOW_FILE


def load_allow_rules(path: Optional[Path] = None) -> List[PermissionRule]:
    """Read the gate's allow file. A malformed file yields no rules, loudly."""
    target = Path(path) if path else allow_file()
    if not target.is_file():
        return []
    try:
        import yaml
        raw = yaml.safe_load(target.read_text(encoding="utf-8", errors="replace")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("sentinel: could not read %s (%s)", target, exc)
        return []
    rules: List[PermissionRule] = []
    perms = (raw or {}).get("permissions") or {}
    for decision_name, items in perms.items():
        try:
            decision = Decision(str(decision_name))
        except ValueError:
            logger.warning("sentinel: unknown decision %r in %s", decision_name, target)
            continue
        for item in items or []:
            rules.append(PermissionRule.from_str(str(item), decision))
    return rules


def write_allow_rule(tool: str, pattern: str = "*",
                     decision: str = "allow") -> Path:
    """Record a standing rule the user granted, so the gate stops asking."""
    import yaml
    tool = (tool or "").strip()
    if not tool:
        raise ValueError("tool name is required")
    entry = f"{tool}({pattern or '*'})"
    target = allow_file()
    raw: Dict[str, Any] = {}
    if target.is_file():
        try:
            raw = yaml.safe_load(target.read_text(encoding="utf-8", errors="replace")) or {}
        except Exception:  # noqa: BLE001
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    perms = raw.setdefault("permissions", {})
    bucket = perms.setdefault(decision, [])
    if entry not in bucket:
        bucket.append(entry)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
                      encoding="utf-8")
    return target


class SentinelAudit:
    """Append-only record of every ruling, one file per day.

    Arguments are fingerprinted, never stored: an audit trail that carries raw
    tool arguments is a new place for credentials to leak.
    """

    def __init__(self, directory: Optional[Path] = None, keep_days: int = 14) -> None:
        self._dir = directory
        self._keep_days = keep_days
        self._lock = threading.Lock()

    @property
    def directory(self) -> Path:
        return self._dir if self._dir is not None else sentinel_dir()

    def _file(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return self.directory / f"audit-{stamp}.jsonl"

    def record(self, ruling: Ruling, *, agent_id: str = "",
               project_id: str = "", args_digest: str = "",
               error: str = "") -> None:
        entry = ruling.to_dict()
        entry["args"] = args_digest
        if agent_id:
            entry["agent_id"] = agent_id
        if project_id:
            entry["project_id"] = project_id
        if error:
            entry["error"] = redact(error[:300])
        try:
            with self._lock:
                path = self._file()
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            # An audit write failure must not stop the agent, but silence here
            # would hide a gate that stops recording.
            logger.warning("sentinel: could not write audit entry (%s)", exc)
            return
        self._prune()

    def _prune(self) -> None:
        try:
            files = sorted(self.directory.glob("audit-*.jsonl"))
            for stale in files[:-self._keep_days]:
                stale.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def read(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Most recent entries first."""
        entries: List[Dict[str, Any]] = []
        try:
            files = sorted(self.directory.glob("audit-*.jsonl"), reverse=True)
        except Exception:  # noqa: BLE001
            return entries
        for path in files:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:  # noqa: BLE001
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if len(entries) >= limit:
                    return entries
        return entries


class Sentinel:
    """The gate. One instance per process; agents read their taint into it."""

    def __init__(self, policy: Optional[PermissionPolicy] = None,
                 mode: ApprovalMode = ApprovalMode.SUGGEST,
                 audit: Optional[SentinelAudit] = None,
                 enabled: Optional[bool] = None,
                 strict: Optional[bool] = None) -> None:
        self.policy = policy or PermissionPolicy()
        self.mode = mode
        self.audit = audit if audit is not None else SentinelAudit()
        self._enabled = enabled
        self._strict = strict

    # -- configuration ----------------------------------------------------

    @property
    def enabled(self) -> bool:
        if self._enabled is not None:
            return self._enabled
        return (os.environ.get("KAIROS_SENTINEL", "on").strip().lower()
                not in ("0", "off", "false", "no"))

    @property
    def strict(self) -> bool:
        """When on, an ASK verdict becomes a refusal (no approver exists yet)."""
        if self._strict is not None:
            return self._strict
        return (os.environ.get("KAIROS_SENTINEL_STRICT", "").strip().lower()
                in ("1", "on", "true", "yes"))

    def reload_policy(self, policy: PermissionPolicy) -> None:
        self.policy = policy

    def reload(self) -> None:
        """Re-read the user's rules and this gate's own allow file.

        Two sources, both read-only from here: ``~/.kairos/permissions.yaml``
        (the app's existing convention, the user's own file) and the gate's
        allow file, which only grows through an explicit "allow this" request.
        Deny beats allow in :meth:`PermissionPolicy.check`, so merging cannot
        weaken a rule the user wrote.
        """
        base = PermissionPolicy()
        try:
            from kairos.permissions import load_policy
            base = load_policy()
        except Exception as exc:  # noqa: BLE001
            logger.warning("sentinel: user permissions not loaded (%s)", exc)
        granted = load_allow_rules()
        if granted:
            base = PermissionPolicy(rules=list(granted) + list(base.rules),
                                    default_decision=base.default_decision)
        self.policy = base

    # -- the ruling -------------------------------------------------------

    def authorize(self, tool: str, args: Any, *, taint: Optional[TaintTracker] = None,
                  origin: str = "agent", agent_id: str = "",
                  project_id: str = "") -> Ruling:
        """Decide whether ``tool`` may run, and record the decision."""
        resource = resource_of(tool, args)
        tainted = bool(taint and taint.tainted)
        try:
            ruling = self._decide(tool, args, resource, tainted, origin)
        except Exception as exc:  # noqa: BLE001
            # Fail open, but leave evidence: a gate that silently stops gating
            # is worse than one that admits it is broken.
            logger.warning("sentinel: gate error on %s (%s) — allowing the call",
                           tool, exc)
            ruling = Ruling(tool=tool, resource=resource, decision="allow",
                            reason=f"gate error, allowed: {exc}",
                            rule="gate-error", tainted=tainted, origin=origin,
                            strict=self.strict)
            self.audit.record(ruling, agent_id=agent_id, project_id=project_id,
                              args_digest=digest(args), error=str(exc))
            return ruling
        self.audit.record(ruling, agent_id=agent_id, project_id=project_id,
                          args_digest=digest(args))
        if ruling.denied:
            logger.warning("sentinel: refused %s (%s) — %s",
                           tool, resource[:120], ruling.reason)
        return ruling

    def _decide(self, tool: str, args: Any, resource: str, tainted: bool,
                origin: str) -> Ruling:
        def make(decision: str, reason: str, rule: str) -> Ruling:
            return Ruling(tool=tool, resource=resource, decision=decision,
                          reason=reason, rule=rule, tainted=tainted,
                          origin=origin, strict=self.strict)

        # 1. The user's standing rules outrank the gate.
        decision, matched = self.policy.check(tool, resource)
        if decision == Decision.DENY:
            where = f"{matched.tool}({matched.pattern})" if matched else "policy"
            return make("deny", f"denied by policy rule {where}", "policy-deny")
        if decision == Decision.ALLOW:
            where = f"{matched.tool}({matched.pattern})" if matched else "policy"
            return make("allow", f"allowed by policy rule {where}", "policy-allow")

        # 2. Credential stores: never, in any mode.
        if _is_app_key_store(resource):
            return make("deny",
                        "this is the app's own key store; the agent must not touch it",
                        "credential-store")
        probe = f"{resource} {_arg_text(args)}"
        credential = _matches_any(probe, CREDENTIAL_PATH_MARKERS)
        if credential:
            return make("deny",
                        f"{credential!r} holds credentials; the agent does not need it",
                        "credential-store")

        # 3. Tainted egress: the injection-to-exfiltration link.
        if tainted:
            egress, why = is_egress(tool, args)
            if egress:
                return make("deny", f"tainted egress — {why}", "tainted-egress")
            secret = _matches_any(probe, SECRET_FILE_MARKERS)
            if secret and tool in ("file_read", "grep", "find", "terminal"):
                return make("deny",
                            f"tainted run reading {secret!r}, which may hold secrets",
                            "tainted-secret-read")

        # 4. No approver exists yet, so the ladder stays advisory unless asked.
        if self.strict:
            ladder, reason = self._ladder(tool, resource)
            if ladder == Decision.ASK:
                return make("deny", f"strict mode: {reason}", "strict-ask")
            return make("allow", reason, f"strict-{ladder.value}")

        return make("allow", "no rule refused this call", "default-allow")

    def _ladder(self, tool: str, resource: str) -> Tuple[Decision, str]:
        from kairos.approval import READ_ONLY_TOOLS, decide
        return decide(self.policy, tool, resource, self.mode)

    # -- provenance -------------------------------------------------------

    def observe_result(self, tool: str, taint: Optional[TaintTracker],
                       tool_obj: Any = None) -> None:
        """Record that this call's output came from outside the trust boundary.

        Called after a successful call: reading a page taints the run, whether or
        not the page turns out to be interesting. ``tool_obj`` lets a server this
        project ships be recognised as trusted.
        """
        if taint is None:
            return
        source = taint.mark_tool(tool, tool_obj)
        if source is not None:
            logger.info("sentinel: run tainted by %s", source.describe())


def _arg_text(args: Any) -> str:
    if isinstance(args, dict):
        return " ".join(str(v) for v in args.values() if isinstance(v, str))
    return str(args or "")


# ---------------------------------------------------------------------------
# Process-wide instance
# ---------------------------------------------------------------------------

_sentinel: Optional[Sentinel] = None
_sentinel_lock = threading.Lock()


def get_sentinel() -> Sentinel:
    global _sentinel
    if _sentinel is None:
        with _sentinel_lock:
            if _sentinel is None:
                _sentinel = Sentinel()
                _sentinel.reload()
    return _sentinel


def reset_sentinel() -> None:
    """Test helper: drop the cached instance."""
    global _sentinel
    with _sentinel_lock:
        _sentinel = None
