"""Build kairos-code.exe with the Kairos favicon icon."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\software_bak\Kairos_code")
LAUNCHER = ROOT / "kairos_code_launcher.py"
DIST = ROOT / "web" / "dist"
EXE_OUT = ROOT / "dist" / "kairos-code.exe"
PYI = ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
ICON = ROOT / "web" / "public" / "branding" / "favicon.ico"

# Move old dist out of the way (Windows file lock safe)
if EXE_OUT.exists():
    backup = ROOT / "dist_old"
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    shutil.move(str(EXE_OUT.parent), str(backup))

add_data = str(DIST) + (";" if os.name == "nt" else ":") + "web/dist"
collect1 = "kairos" + (";kairos/yamls" if os.name == "nt" else ":kairos/yamls")
collect2 = "kairos" + (";kairos/agents/prompts" if os.name == "nt" else ":kairos/agents/prompts")

hidden = [
    "aiosqlite", "uvicorn.lifespan", "uvicorn.lifespan.on",
    "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "openai", "anthropic",
]

import tempfile as _tempfile

_build_tmp = Path(_tempfile.gettempdir())
workpath = _build_tmp / "kairos-pyinst-final"
workpath.mkdir(parents=True, exist_ok=True)
specpath = _build_tmp / "kairos-spec-final"
specpath.mkdir(parents=True, exist_ok=True)

cmd = [str(PYI), "--noconfirm", "--onefile", "--name", "kairos-code",
        "--icon", str(ICON),
        "--windowed",  # GUI app: no black console window
        "--distpath", str(EXE_OUT.parent), "--workpath", str(workpath),
        "--specpath", str(specpath),
        "--add-data", add_data,
        "--collect-data", collect1, "--collect-data", collect2]
cmd += [
    "--collect-submodules", "kairos.agents",
    "--collect-submodules", "kairos.agents.roles",
    "--collect-submodules", "kairos.agents.base",
    "--collect-submodules", "kairos.llm.providers",
    "--collect-submodules", "kairos.llm",
    "--collect-submodules", "kairos.core",
    "--collect-submodules", "kairos.loop",
    "--collect-submodules", "kairos.tools",
    "--collect-submodules", "kairos.llm.providers.openai_provider",
    "--collect-submodules", "kairos.llm.providers.anthropic_provider",
    "--collect-all", "kairos",
    "--hidden-import", "kairos.agents.base",
    "--hidden-import", "kairos.agents.roles.coder",
    "--hidden-import", "kairos.agents.roles.design",
    "--hidden-import", "kairos.agents.roles.docs",
    "--hidden-import", "kairos.agents.roles.perf",
    "--hidden-import", "kairos.agents.roles.refactor",
    "--hidden-import", "kairos.agents.roles.reviewer",
    "--hidden-import", "kairos.agents.roles.security",
    "--hidden-import", "kairos.agents.roles.test",
    "--collect-submodules", "kairos.core",
    "--collect-submodules", "api",
]
for h in hidden:
    cmd += ["--hidden-import", h]
cmd.append(str(LAUNCHER))

print(" ".join(cmd))
r = subprocess.run(cmd, cwd=str(ROOT))
sys.exit(r.returncode)


