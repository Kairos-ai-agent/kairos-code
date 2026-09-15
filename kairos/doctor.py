"""Round 32: ``kairos doctor`` — a self-diagnostic command.

When something doesn't work, the user wants to know "what's broken
in my install?" in 5 seconds, not "read the source and figure it
out in 5 hours". This module runs a battery of cheap, non-mutating
checks and prints a one-line verdict for each:

  [OK]    Python version >= 3.10
  [OK]    data dir writable (/home/.../data)
  [WARN]  no LLM API key set (set OPENAI_API_KEY or ...)
  [FAIL]  FTS5 not available (skill search will fall back)
  ...

Exit code:
  0  — all checks OK or WARN
  1  — at least one FAIL
  2  — doctor itself crashed (shouldn't happen)

Checks are organized as standalone functions returning
``CheckResult`` so they're easy to test individually. ``main()``
just runs them all and pretty-prints.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# CheckResult — the per-check verdict
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """A single check's outcome."""
    name: str                  # human-readable name (e.g. "Python version")
    status: str                # "ok" | "warn" | "fail"
    message: str               # short detail
    hint: str = ""             # optional fix-it suggestion
    group: str = ""            # category for grouping in the output

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Status helpers ----------------------------------------------------------

def _ok(name: str, msg: str, hint: str = "", group: str = "") -> CheckResult:
    return CheckResult(name, "ok", msg, hint, group)


def _warn(name: str, msg: str, hint: str = "", group: str = "") -> CheckResult:
    return CheckResult(name, "warn", msg, hint, group)


def _fail(name: str, msg: str, hint: str = "", group: str = "") -> CheckResult:
    return CheckResult(name, "fail", msg, hint, group)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_python_version() -> CheckResult:
    """3.10+ is required (Pydantic v2 + match statements)."""
    name = "Python version"
    v = sys.version_info
    msg = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 10):
        return _ok(name, msg, group="runtime")
    if (v.major, v.minor) >= (3, 8):
        return _warn(name, f"{msg} (3.10+ recommended)",
                     hint="Upgrade to Python 3.10 or later.",
                     group="runtime")
    return _fail(name, f"{msg} (3.10+ required)",
                 hint="Install Python 3.10+ before continuing.",
                 group="runtime")


def check_platform() -> CheckResult:
    """Note the platform (Windows / macOS / Linux) — affects sandbox tier."""
    name = "Platform"
    msg = platform.platform(terse=True)
    return _ok(name, msg, group="runtime")


def check_data_dir() -> CheckResult:
    """The data dir must exist and be writable."""
    name = "Data dir"
    from kairos.alerts_dispatcher import _get_history_path
    p = _get_history_path().parent
    if not p.exists():
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _fail(name, f"{p} (cannot create: {exc})",
                         hint="Set KAIROS_DATA_DIR to a writable path.",
                         group="filesystem")
    if not os.access(p, os.W_OK):
        return _fail(name, f"{p} (not writable)",
                     hint=f"chmod +w {p}", group="filesystem")
    return _ok(name, str(p), group="filesystem")


def check_workspace_dir() -> CheckResult:
    """The workspace dir is the Coder's playground."""
    name = "Workspace dir"
    try:
        from kairos.config.settings import settings
        p = Path(settings.workspace_dir)
    except Exception as exc:
        return _fail(name, f"settings load failed: {exc}",
                     hint="Check kairos/config/settings.py for syntax errors.",
                     group="filesystem")
    if not p.exists():
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _warn(name, f"{p} (cannot create: {exc})",
                         hint="The Coder will create it on first run.",
                         group="filesystem")
    if not os.access(p, os.W_OK):
        return _fail(name, f"{p} (not writable)",
                     hint=f"chmod +w {p}", group="filesystem")
    return _ok(name, str(p), group="filesystem")


def check_llm_providers() -> CheckResult:
    """At least one LLM provider should have an API key configured.

    We don't fail if none are set — the user might be running
    offline-only with Ollama, or just exploring. But we WARN so
    the first LLM call doesn't fail mysteriously.
    """
    name = "LLM providers"
    from kairos.config.settings import settings
    configured: List[str] = []
    for prov_name in ("openai", "anthropic", "deepseek", "dashscope",
                       "zhipuai", "gemini", "openrouter"):
        prov = getattr(settings, prov_name, None)
        if prov and prov.api_key:
            configured.append(f"{prov_name}({prov.model})")
    if configured:
        return _ok(name, ", ".join(configured), group="llm")
    # No key set — is Ollama reachable?
    ollama = getattr(settings, "ollama", None)
    if ollama and ollama.base_url:
        # We don't ping here (other check does that); just note the
        # fallback exists.
        return _warn(name, "no API keys set; will use Ollama if reachable",
                     hint="Set OPENAI_API_KEY / ANTHROPIC_API_KEY / etc. "
                          "for cloud providers.",
                     group="llm")
    return _warn(name, "no LLM provider configured",
                 hint="Set at least one of OPENAI_API_KEY, "
                      "ANTHROPIC_API_KEY, OLLAMA_BASE_URL, etc.",
                 group="llm")


def check_ollama_reachable() -> CheckResult:
    """If Ollama is configured, do a quick HEAD to its base URL."""
    name = "Ollama reachable"
    try:
        from kairos.config.settings import settings
        ollama = getattr(settings, "ollama", None)
        if not ollama or not ollama.base_url:
            return _ok(name, "(not configured)", group="llm")
        # Quick HEAD; 5s timeout so doctor stays fast
        req = urllib.request.Request(ollama.base_url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                code = resp.status
        except (urllib.error.URLError, OSError) as exc:
            return _warn(name, f"{ollama.base_url} unreachable: {exc}",
                         hint="Start the Ollama daemon or change "
                              "OLLAMA_BASE_URL.",
                         group="llm")
        if 200 <= code < 500:
            return _ok(name, f"{ollama.base_url} (HTTP {code})",
                       group="llm")
        return _warn(name, f"{ollama.base_url} HTTP {code}",
                     group="llm")
    except Exception as exc:
        return _warn(name, f"check failed: {exc}", group="llm")


def check_skills_loadable() -> CheckResult:
    """Every shipped skill must parse, and the loader must find them.

    R38.11: this used to assert ``n >= 1``, which passed even while the bundled
    set was unreachable from the API layer. It now compares what is on disk with
    what the loader returns, and names the files that fail to parse — the
    failure mode users actually hit.
    """
    name = "Skills loader"
    try:
        from kairos.skills import SkillsLoader, _parse_skill
        bundled = Path(__file__).resolve().parent / "skills"
        files = sorted(bundled.rglob("*.md"))
        n = len(SkillsLoader().discover())
        unparsable = [p for p in files if _parse_skill(p) is None]
    except Exception as exc:
        return _fail(name, f"discover() failed: {exc}",
                     hint="Check that kairos/skills/*.md files are well-formed.",
                     group="skills")
    if n == 0:
        return _fail(name, "no skills discovered",
                     hint="The bundled set ships in kairos/skills/; check the install.",
                     group="skills")
    if unparsable:
        return _warn(
            name, f"{n} skills loaded, {len(unparsable)} shipped file(s) unparsable",
            hint=f"First: {unparsable[0].relative_to(bundled)}", group="skills")
    return _ok(name, f"{n} skills loaded ({len(files)} shipped files)",
               group="skills")


def check_bundled_mcp_servers() -> CheckResult:
    """The offline MCP servers must build their tool tables without a network.

    R38.11: five registry entries are served by this install instead of by
    ``npx``/``uvx``. If one of them cannot even construct its tools, "works out
    of the box" is a claim rather than a fact — so ask it.
    """
    name = "Bundled MCP servers"
    try:
        from kairos.mcp_local_servers import BUNDLED_SERVERS, tools_for
    except Exception as exc:
        return _fail(name, f"cannot import the bundled servers: {exc}", group="mcp")
    broken = []
    tools = 0
    for server in BUNDLED_SERVERS:
        if server == "filesystem":        # served by its own module
            continue
        try:
            tools += len(tools_for(server))
        except Exception as exc:
            broken.append(f"{server}: {type(exc).__name__}: {exc}")
    if broken:
        return _fail(name, f"{len(broken)} offline server(s) broken",
                     hint=broken[0], group="mcp")
    return _ok(name,
               f"{len(BUNDLED_SERVERS)} offline servers, {tools} tools, no download",
               group="mcp")


def check_fts5() -> CheckResult:
    """FTS5 is optional but enables fast skill search."""
    name = "FTS5 full-text search"
    try:
        from kairos.skill_search import fts5_available
        ok = fts5_available()
    except Exception as exc:
        return _fail(name, f"fts5_available() raised: {exc}",
                     group="skills")
    if ok:
        return _ok(name, "available", group="skills")
    return _warn(name, "FTS5 not in this Python build; "
                       "skill search will use the slower Python fallback",
                 hint="Rebuild Python with --enable-loadable-sqlite-extensions "
                      "or use a Python build that includes FTS5.",
                 group="skills")


def check_mcp_config() -> CheckResult:
    """The MCP config file (if present) must be parseable YAML."""
    name = "MCP config"
    p = REPO_ROOT / ".mcp.yaml"
    if not p.exists():
        return _ok(name, "(not configured)", group="mcp")
    try:
        import yaml  # noqa: F401
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        n = len(data.get("servers", []) or [])
        return _ok(name, f"{p.name} ({n} server(s))", group="mcp")
    except ImportError:
        return _warn(name, "PyYAML not installed; cannot parse config",
                     hint="pip install pyyaml", group="mcp")
    except Exception as exc:
        return _fail(name, f"{p.name} parse failed: {exc}",
                     hint="Fix the YAML syntax error.",
                     group="mcp")


def check_vendor_dir() -> CheckResult:
    """vendor/ should exist (offline deps live there)."""
    name = "Vendor dir"
    p = REPO_ROOT / "vendor"
    if not p.exists():
        return _warn(name, "vendor/ not found",
                     hint="Clone the vendored dependencies (see README).",
                     group="filesystem")
    if not p.is_dir():
        return _fail(name, f"vendor/ is not a directory ({p})",
                     group="filesystem")
    # Count .whl files
    whl_count = sum(1 for _ in p.rglob("*.whl"))
    return _ok(name, f"{p} ({whl_count} wheels)", group="filesystem")


def check_alerts_history_dir() -> CheckResult:
    """The alerts.jsonl parent dir should be writable."""
    name = "Alerts history"
    from kairos.alerts_dispatcher import _get_history_path
    p = _get_history_path()
    parent = p.parent
    if not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _fail(name, f"{parent} (cannot create: {exc})",
                         hint="Set KAIROS_DATA_DIR to a writable path.",
                         group="filesystem")
    # Try a touch
    try:
        p.touch(exist_ok=True)
        return _ok(name, f"writable at {p}", group="filesystem")
    except OSError as exc:
        return _fail(name, f"{p} (cannot write: {exc})",
                     group="filesystem")


def check_settings_loadable() -> CheckResult:
    """The global settings singleton must instantiate."""
    name = "Settings"
    try:
        from kairos.config.settings import settings
        # Touch a few fields
        _ = settings.host
        _ = settings.port
        _ = settings.default_provider
        return _ok(name, f"host={settings.host} port={settings.port} "
                         f"default={settings.default_provider}",
                   group="config")
    except Exception as exc:
        return _fail(name, f"settings load failed: {exc}",
                     hint="Check env vars and the Settings class.",
                     group="config")


def check_dependencies() -> CheckResult:
    """A few key deps must be importable."""
    name = "Dependencies"
    missing: List[str] = []
    for mod_name in ("fastapi", "pydantic", "yaml", "click"):
        try:
            __import__(mod_name)
        except ImportError:
            missing.append(mod_name)
    if not missing:
        return _ok(name, "fastapi, pydantic, yaml, click all importable",
                   group="runtime")
    return _fail(name, f"missing: {', '.join(missing)}",
                 hint=f"pip install {' '.join(missing)}",
                 group="runtime")


def check_git() -> CheckResult:
    """Git is needed for checkpoints (R12) and the harness (R29)."""
    name = "Git"
    git_path = shutil.which("git")
    if not git_path:
        return _fail(name, "git not on PATH",
                     hint="Install git (https://git-scm.com).",
                     group="runtime")
    try:
        import subprocess
        out = subprocess.run(["git", "--version"], capture_output=True,
                             text=True, timeout=5)
        if out.returncode == 0:
            return _ok(name, out.stdout.strip(), group="runtime")
        return _fail(name, f"git --version failed: {out.stderr}",
                     group="runtime")
    except Exception as exc:
        return _warn(name, f"git check failed: {exc}", group="runtime")


# ---------------------------------------------------------------------------
# Default check list
# ---------------------------------------------------------------------------


DEFAULT_CHECKS: List[Callable[[], CheckResult]] = [
    check_python_version,
    check_platform,
    check_dependencies,
    check_git,
    check_settings_loadable,
    check_data_dir,
    check_workspace_dir,
    check_alerts_history_dir,
    check_vendor_dir,
    check_llm_providers,
    check_ollama_reachable,
    check_skills_loadable,
    check_fts5,
    check_mcp_config,
    check_bundled_mcp_servers,
]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


_STATUS_SYMBOL = {
    "ok": "[OK]",
    "warn": "[WARN]",
    "fail": "[FAIL]",
}


def _format_table(results: List[CheckResult]) -> str:
    """Pretty-print a table grouped by category."""
    groups: Dict[str, List[CheckResult]] = {}
    for r in results:
        groups.setdefault(r.group or "other", []).append(r)

    out_lines: List[str] = []
    for group, items in groups.items():
        out_lines.append(f"\n  {group.upper()}")
        for r in items:
            symbol = _STATUS_SYMBOL.get(r.status, "[?]")
            line = f"    {symbol:6s}  {r.name}: {r.message}"
            if r.hint:
                line += f"\n            hint: {r.hint}"
            out_lines.append(line)
    return "\n".join(out_lines)


def _summary(results: List[CheckResult]) -> Tuple[int, int, int]:
    n_ok = sum(1 for r in results if r.status == "ok")
    n_warn = sum(1 for r in results if r.status == "warn")
    n_fail = sum(1 for r in results if r.status == "fail")
    return n_ok, n_warn, n_fail


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kairos.doctor",
        description="Run a battery of self-diagnostic checks.",
    )
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON instead of a human-readable table")
    parser.add_argument("--only", action="append", default=[],
                        help="Run only checks whose name matches this substring "
                             "(repeatable)")
    args = parser.parse_args(argv)

    checks = DEFAULT_CHECKS
    if args.only:
        # Filter by check function name containing any --only substring
        wanted = [s.lower() for s in args.only]
        checks = [c for c in checks
                  if any(w in c.__name__.lower() for w in wanted)]
        if not checks:
            print(f"ERROR: no checks matched --only {args.only}",
                  file=sys.stderr)
            return 1

    results: List[CheckResult] = []
    try:
        for c in checks:
            try:
                results.append(c())
            except Exception as exc:
                # A check should never crash the doctor
                results.append(CheckResult(
                    name=c.__name__.replace("check_", ""),
                    status="fail", message=f"check crashed: {exc}",
                    hint="This is a doctor bug — file an issue.",
                ))
    except KeyboardInterrupt:
        print("\ndoctor interrupted", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        print(_format_table(results))
        n_ok, n_warn, n_fail = _summary(results)
        print(f"\n  {n_ok} OK, {n_warn} WARN, {n_fail} FAIL")

    return 0 if all(r.status != "fail" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
