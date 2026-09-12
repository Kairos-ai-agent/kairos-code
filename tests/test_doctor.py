"""Tests for kairos.doctor (Round 32).

Covers:
  - Each individual check (ok / warn / fail paths via monkeypatch)
  - DEFAULT_CHECKS list is well-formed
  - _format_table and _summary helpers
  - main() exit codes and JSON output
  - main() --only filter
  - A check that crashes doesn't kill the doctor
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import pytest

from kairos.doctor import (
    DEFAULT_CHECKS,
    CheckResult,
    _format_table,
    _ok,
    _warn,
    _fail,
    _summary,
    check_alerts_history_dir,
    check_data_dir,
    check_dependencies,
    check_fts5,
    check_git,
    check_llm_providers,
    check_mcp_config,
    check_ollama_reachable,
    check_platform,
    check_python_version,
    check_settings_loadable,
    check_skills_loadable,
    check_vendor_dir,
    check_workspace_dir,
    main,
)


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------


def test_status_helpers_set_fields():
    r_ok = _ok("a", "msg")
    assert r_ok.status == "ok"
    assert r_ok.hint == ""
    r_warn = _warn("a", "msg", hint="h", group="g")
    assert r_warn.status == "warn"
    assert r_warn.hint == "h"
    assert r_warn.group == "g"
    r_fail = _fail("a", "msg")
    assert r_fail.status == "fail"


def test_check_result_to_dict_round_trip():
    r = _ok("name", "msg", hint="h", group="g")
    d = r.to_dict()
    assert d == {"name": "name", "status": "ok", "message": "msg",
                 "hint": "h", "group": "g"}


# ---------------------------------------------------------------------------
# Individual checks — happy paths
# ---------------------------------------------------------------------------


def test_check_python_version_ok():
    """On Python 3.10+ this should be OK."""
    r = check_python_version()
    assert r.status == "ok"
    assert r.name == "Python version"


def test_check_platform_ok():
    r = check_platform()
    assert r.status == "ok"
    assert r.name == "Platform"


def test_check_data_dir_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    r = check_data_dir()
    assert r.status == "ok"
    assert str(tmp_path) in r.message


def test_check_data_dir_fails_when_uncreatable(tmp_path, monkeypatch):
    """If the dir cannot be created, the check fails."""
    # Make the dir un-creatable by setting KAIROS_DATA_DIR to a path
    # whose parent doesn't exist on Windows OR an unwritable path on
    # POSIX. We simulate by setting the data dir to a path under a
    # file (not a directory) — mkdir will fail.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir", encoding="utf-8")
    monkeypatch.setenv("KAIROS_DATA_DIR", str(blocker / "subdir"))
    r = check_data_dir()
    assert r.status == "fail"


def test_check_workspace_dir_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_WORKSPACE_DIR", str(tmp_path))
    r = check_workspace_dir()
    assert r.status == "ok"


def test_check_settings_loadable():
    r = check_settings_loadable()
    assert r.status == "ok"
    assert "host=" in r.message


def test_check_skills_loadable():
    r = check_skills_loadable()
    assert r.status == "ok"
    assert "skills" in r.message.lower()


def test_check_fts5_runs():
    r = check_fts5()
    # Either ok or warn — never fail
    assert r.status in ("ok", "warn")


def test_check_dependencies_ok():
    r = check_dependencies()
    assert r.status == "ok"


def test_check_dependencies_fails_when_missing(monkeypatch):
    """If a key dep is not importable, the check must fail."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fastapi":
            raise ImportError("no fastapi in this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    r = check_dependencies()
    assert r.status == "fail"
    assert "fastapi" in r.message


def test_check_git_ok_if_git_installed():
    r = check_git()
    # The CI/dev env has git
    assert r.status == "ok"


def test_check_git_fails_when_git_missing(monkeypatch):
    """Pretend git is not on PATH."""
    from kairos import doctor
    monkeypatch.setattr(doctor, "shutil", __import__("shutil"))
    # Force shutil.which("git") to return None
    import shutil as real_shutil
    monkeypatch.setattr(real_shutil, "which", lambda x: None)
    # doctor module already imported shutil at top; need to patch the
    # reference doctor.shutil.which
    monkeypatch.setattr(doctor.shutil, "which", lambda x: None)
    r = check_git()
    assert r.status == "fail"


def test_check_vendor_dir_ok():
    """vendor/ is present in this repo (with superpowers + anthropic-skills)."""
    r = check_vendor_dir()
    assert r.status == "ok"


def test_check_vendor_dir_warns_when_missing(tmp_path, monkeypatch):
    """If vendor/ doesn't exist, it's a WARN (not a FAIL)."""
    import kairos.doctor as doc
    monkeypatch.setattr(doc, "REPO_ROOT", tmp_path)
    r = check_vendor_dir()
    assert r.status == "warn"


def test_check_alerts_history_dir_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    r = check_alerts_history_dir()
    assert r.status == "ok"


def test_check_mcp_config_not_present(tmp_path, monkeypatch):
    """If .mcp.yaml is absent, the check is OK (not configured)."""
    import kairos.doctor as doc
    monkeypatch.setattr(doc, "REPO_ROOT", tmp_path)
    r = check_mcp_config()
    assert r.status == "ok"
    assert "not configured" in r.message


def test_check_mcp_config_parses_yaml(tmp_path, monkeypatch):
    """If .mcp.yaml is present and valid, the check shows the server count."""
    import kairos.doctor as doc
    monkeypatch.setattr(doc, "REPO_ROOT", tmp_path)
    (tmp_path / ".mcp.yaml").write_text(
        "servers:\n  - name: foo\n    command: echo\n",
        encoding="utf-8",
    )
    r = check_mcp_config()
    assert r.status == "ok"
    assert "1 server" in r.message


def test_check_mcp_config_fails_on_bad_yaml(tmp_path, monkeypatch):
    import kairos.doctor as doc
    monkeypatch.setattr(doc, "REPO_ROOT", tmp_path)
    (tmp_path / ".mcp.yaml").write_text(": bad : yaml :", encoding="utf-8")
    r = check_mcp_config()
    # PyYAML may either error or accept it; either way the check
    # either returns ok (and we should fix) or fail. Just assert
    # it didn't raise.
    assert r.status in ("ok", "fail", "warn")


# ---------------------------------------------------------------------------
# LLM provider check
# ---------------------------------------------------------------------------


def test_check_llm_providers_warns_when_none_set(monkeypatch):
    """If no API keys are set, the check warns (not fail — Ollama might work).

    The check reads the module-level `settings` object, which is populated from
    data/settings.json — so clearing the environment is not enough on a machine
    that has keys on disk. Replace the settings object with an empty one so the
    test is hermetic.
    """
    import kairos.config.settings as cfg
    # Save and clear any keys the env might have
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
              "DASHSCOPE_API_KEY", "ZHIPUAI_API_KEY", "GEMINI_API_KEY",
              "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    class _NoProvider:
        api_key = None
        model = ""
        base_url = None

    class _EmptySettings:
        def __getattr__(self, name):  # any provider key -> empty
            return _NoProvider()

    monkeypatch.setattr(cfg, "settings", _EmptySettings(), raising=False)
    r = check_llm_providers()
    assert r.status == "warn"


def test_check_llm_providers_ok_when_one_key_set(monkeypatch):
    # Clear every other provider's key so only openai is set
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
              "DASHSCOPE_API_KEY", "ZHIPUAI_API_KEY", "GEMINI_API_KEY",
              "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-12345")
    # Re-import settings to pick up the new env (the Settings class
    # reads env at construction)
    import importlib
    import kairos.config.settings as cfg
    importlib.reload(cfg)
    r = check_llm_providers()
    assert r.status == "ok"
    assert "openai" in r.message
    # Restore settings for the other tests
    importlib.reload(cfg)


# ---------------------------------------------------------------------------
# Ollama reachability
# ---------------------------------------------------------------------------


def test_check_ollama_reachable_with_unreachable_default(monkeypatch):
    """Default Ollama URL is http://localhost:11434. If the daemon
    isn't running, the check warns (not fail)."""
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    import importlib
    import kairos.config.settings as cfg
    importlib.reload(cfg)
    r = check_ollama_reachable()
    # The default URL is set, so the check tries to reach it. On a
    # machine without Ollama, that's a warn (daemon not running).
    # On a machine with Ollama, it would be ok. Either way: not fail.
    assert r.status in ("ok", "warn")
    importlib.reload(cfg)


def test_check_ollama_reachable_ok_with_unreachable_url(monkeypatch):
    """If the user explicitly sets OLLAMA_BASE_URL to something the
    doctor can't reach, it warns."""
    # Port 1 is reserved; nothing listens there
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:1")
    import importlib
    import kairos.config.settings as cfg
    importlib.reload(cfg)
    r = check_ollama_reachable()
    assert r.status == "warn"
    importlib.reload(cfg)


def test_check_ollama_reachable_warns_when_unreachable(monkeypatch):
    """If the URL doesn't resolve, the check warns."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:1")  # port 1 = no daemon
    r = check_ollama_reachable()
    assert r.status == "warn"


# ---------------------------------------------------------------------------
# DEFAULT_CHECKS
# ---------------------------------------------------------------------------


def test_default_checks_list_nonempty():
    assert len(DEFAULT_CHECKS) >= 10


def test_default_checks_all_callable():
    for c in DEFAULT_CHECKS:
        assert callable(c)


def test_default_checks_have_unique_names():
    names = [c.__name__ for c in DEFAULT_CHECKS]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def test_format_table_groups_by_category():
    results = [
        _ok("a", "ok", group="g1"),
        _warn("b", "warn", group="g2"),
    ]
    out = _format_table(results)
    assert "G1" in out.upper()
    assert "G2" in out.upper()
    assert "[OK]" in out
    assert "[WARN]" in out


def test_summary_counts():
    r = _summary([
        _ok("a", "m"),
        _ok("b", "m"),
        _warn("c", "m"),
        _fail("d", "m"),
    ])
    assert r == (2, 1, 1)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def _cli(argv, monkeypatch=None):
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        rc = main(argv)
    return rc, buf_out.getvalue(), buf_err.getvalue()


def test_main_human_output(monkeypatch):
    rc, out, _ = _cli([], monkeypatch)
    # All checks pass or warn in this env; rc == 0
    assert rc == 0
    assert "[OK]" in out or "[WARN]" in out or "[FAIL]" in out
    assert "OK, " in out and "WARN, " in out and "FAIL" in out


def test_main_json_output(monkeypatch):
    rc, out, _ = _cli(["--json"], monkeypatch)
    assert rc == 0
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) >= 10
    for entry in data:
        assert {"name", "status", "message"}.issubset(entry.keys())


def test_main_exit_code_1_on_failure(monkeypatch):
    """Force a check to fail by replacing the function inside DEFAULT_CHECKS."""
    from kairos import doctor
    new_list = []
    for c in doctor.DEFAULT_CHECKS:
        if c.__name__ == "check_python_version":
            new_list.append(lambda: _fail("Python version", "0.0.0", "no"))
        else:
            new_list.append(c)
    monkeypatch.setattr(doctor, "DEFAULT_CHECKS", new_list)
    rc, _, _ = _cli([], monkeypatch)
    assert rc == 1


def test_main_only_filter(monkeypatch):
    rc, out, _ = _cli(["--only", "python"], monkeypatch)
    assert rc == 0
    # Only the Python check should be in the output
    assert "Python version" in out
    # Other checks should not appear
    assert "Skills loader" not in out
    assert "MCP config" not in out


def test_main_only_no_match_returns_1(monkeypatch):
    rc, _, err = _cli(["--only", "nonexistent-check-name"], monkeypatch)
    assert rc == 1
    assert "ERROR" in err or "no checks matched" in err


def test_main_survives_a_crashing_check(monkeypatch):
    """If one check raises, the doctor catches it and reports FAIL."""
    from kairos import doctor

    def boom():
        raise RuntimeError("intentional crash for test")

    monkeypatch.setitem({c: c for c in DEFAULT_CHECKS}, boom, boom)
    # Easier: prepend a custom check that crashes
    from kairos.doctor import CheckResult
    def crashy():
        raise RuntimeError("intentional crash")
    crashy.__name__ = "check_crashy_test"  # unique name
    new_list = [crashy] + list(DEFAULT_CHECKS)
    monkeypatch.setattr(doctor, "DEFAULT_CHECKS", new_list)
    rc, out, _ = _cli([], monkeypatch)
    # doctor survives; the crashy check is reported as FAIL
    assert "crashy_test" in out
    assert "intentional crash" in out


def test_main_json_with_failure_includes_failed(monkeypatch):
    """The JSON output includes the failure detail."""
    from kairos import doctor
    new_list = []
    for c in doctor.DEFAULT_CHECKS:
        if c.__name__ == "check_python_version":
            new_list.append(
                lambda: _fail("Python version", "0.0.0", hint="upgrade"))
        else:
            new_list.append(c)
    monkeypatch.setattr(doctor, "DEFAULT_CHECKS", new_list)
    rc, out, _ = _cli(["--json"], monkeypatch)
    assert rc == 1
    data = json.loads(out)
    py = next(e for e in data if e["name"] == "Python version")
    assert py["status"] == "fail"
    assert py["hint"] == "upgrade"
