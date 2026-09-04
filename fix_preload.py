"""Patch kairos_code_launcher.py pre-load block to log to a file."""
import os
p = r"D:\software_bak\Kairos_code\kairos_code_launcher.py"
t = open(p, encoding="utf-8").read()

old = '''    # R38.6.4 packaging: pre-load all kairos submodules before
    # api.app's eager ``from kairos.agents import ...`` runs.
    import importlib as _il
    for _mod in [
        "kairos.agents.base",
        "kairos.agents.roles.coder",
        "kairos.agents.roles.design",
        "kairos.agents.roles.docs",
        "kairos.agents.roles.perf",
        "kairos.agents.roles.refactor",
        "kairos.agents.roles.reviewer",
        "kairos.agents.roles.security",
        "kairos.agents.roles.test",
        "kairos.llm.base",
        "kairos.llm.providers.openai_provider",
        "kairos.llm.providers.anthropic_provider",
        "kairos.tools",
    ]:
        try:
            _il.import_module(_mod)
        except Exception:
            pass
'''

new = '''    # R38.6.4 packaging: pre-load kairos submodules (with per-module
    # try/except + a log file the user can inspect post-mortem;
    # windowed EXE has no stderr so any failure is silent).
    import importlib as _il
    import traceback as _tb
    _preload_log = os.environ.get("KAIROS_PRELOAD_LOG",
        str(Path.home() / ".kairos-code" / "preload.log"))
    try:
        os.makedirs(os.path.dirname(_preload_log), exist_ok=True)
    except Exception:
        pass
    with open(_preload_log, "w", encoding="utf-8") as _f:
        for _mod in [
            "kairos.agents.base",
            "kairos.agents.roles.coder",
            "kairos.agents.roles.design",
            "kairos.agents.roles.docs",
            "kairos.agents.roles.perf",
            "kairos.agents.roles.refactor",
            "kairos.agents.roles.reviewer",
            "kairos.agents.roles.security",
            "kairos.agents.roles.test",
            "kairos.llm.base",
            "kairos.llm.providers.openai_provider",
            "kairos.llm.providers.anthropic_provider",
            "kairos.tools",
        ]:
            try:
                _il.import_module(_mod)
                _f.write(f"OK   {_mod}\\n")
            except Exception as _exc:
                _f.write(f"FAIL {_mod}: {_exc!r}\\n")
                _f.write(_tb.format_exc())
'''

if "KAIROS_PRELOAD_LOG" not in t:
    t = t.replace(old, new, 1)
    open(p, "w", encoding="utf-8").write(t)
    print("patched")
else:
    print("already patched")
