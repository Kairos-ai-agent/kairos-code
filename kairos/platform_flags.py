"""Keep child processes from popping a console window on Windows.

A *windowed* build (PyInstaller ``--windowed``) has no console of its own. On
Windows, when such a process ``CreateProcess()``-es a **console** program --
``cmd.exe``, git-bash, ``git.exe``, ``taskkill.exe``, ``powershell.exe`` -- the
kernel allocates a **brand-new console window** for that child. Every command
the agent runs therefore flashed (or parked) a black box on screen.

``CREATE_NO_WINDOW`` (``0x08000000``) tells the kernel not to allocate that
console. It is the only knob that works for both ``subprocess`` and
``asyncio`` subprocesses:

* the boolean ``create_no_window=`` Popen argument does not exist (only the
  ``subprocess.CREATE_NO_WINDOW`` constant does), and
* ``asyncio.create_subprocess_*`` accepts ``creationflags`` and nothing else.

It only affects *consoles*: the child's stdin/stdout/stderr handles are
untouched, so a child started with ``stdout=PIPE`` (the MCP stdio protocol)
behaves exactly as before. Do **not** "fix" the flicker by wiring a child's
stdio to ``DEVNULL``/``INHERIT`` -- that breaks the MCP handshake and, with it,
:func:`kairos_code_launcher._reattach_std_streams`.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Dict, Optional

#: Win32 ``CREATE_NO_WINDOW``. ``subprocess`` only defines the constant on
#: Windows, so fall back to the literal everywhere else (it is never used
#: there -- :func:`hidden_kwargs` short-circuits on non-Windows).
CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def is_windows() -> bool:
    """True on Windows. Uses ``sys.platform`` so a monkeypatched ``os.name``
    (which some tests fake to exercise POSIX code paths) does not change it."""
    return sys.platform.startswith("win")


def hidden_kwargs(existing: Optional[Dict] = None) -> Dict:
    """Subprocess kwargs that suppress the child's console window on Windows.

    On non-Windows this returns ``existing`` unchanged (so every call site
    stays cross-platform and POSIX behaviour is byte-identical).

    *existing* is a kwargs dict the call site already built -- commonly one
    carrying ``creationflags=CREATE_NEW_PROCESS_GROUP`` or
    ``start_new_session=True``. Its fields are preserved and any
    ``creationflags`` is **OR-ed** with ``CREATE_NO_WINDOW`` rather than
    overwritten, so the caller's process-group/detached semantics survive. The
    input dict is never mutated.
    """
    merged: Dict = dict(existing) if existing else {}
    if not is_windows():
        return merged
    try:
        current = int(merged.get("creationflags", 0) or 0)
    except (TypeError, ValueError):
        current = 0
    merged["creationflags"] = current | CREATE_NO_WINDOW
    return merged
