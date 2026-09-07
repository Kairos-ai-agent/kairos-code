"""Real daemon mode (P0-1 final piece).

The earlier in-process DaemonSupervisor was good for heartbeat
but the user actually wants a SEPARATE PROCESS that survives
browser close. This module provides:

  1. start_daemon() - spawn a background uvicorn worker process
     detached from the current terminal, write its pid to
     .kairos/daemon.pid
  2. stop_daemon() - read pid, terminate
  3. status_daemon() - check pid, read .kairos/daemon.json for
     current state

This is cross-platform: subprocess.Popen with DETACHED_PROCESS
on Windows, double-fork on Linux/macOS.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _state_dir(work_dir=None):
    d = (work_dir or Path.cwd()) / ".kairos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_pid(work_dir=None):
    p = _state_dir(work_dir) / "daemon.pid"
    if not p.exists():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _pid_alive(pid):
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, SystemError):
        return False


def _detect_popen_kwargs():
    if os.name == "nt":
        return {"creationflags": 0x00000008}  # DETACHED_PROCESS
    return {"start_new_session": True}


def start_daemon(work_dir=None, port=8900, host="127.0.0.1"):
    state = _state_dir(work_dir)
    pid = _read_pid(work_dir)
    if _pid_alive(pid):
        info = _read_info(work_dir) or {}
        return {"ok": True, "already_running": True, **info}
    cmd = [sys.executable, "-m", "uvicorn", "api.app:app",
           "--host", host, "--port", str(port),
           "--log-level", "warning"]
    log_path = state / "daemon.out.log"
    err_path = state / "daemon.err.log"
    log_path.write_text("", encoding="utf-8")
    err_path.write_text("", encoding="utf-8")
    log_fp = open(log_path, "a", encoding="utf-8")
    err_fp = open(err_path, "a", encoding="utf-8")
    kwargs = _detect_popen_kwargs()
    # Pass the current env explicitly so the child has PATH /
    # VIRTUAL_ENV / PYTHONPATH (DETACHED_PROCESS isolates the
    # child from the parent's process-group env on Windows).
    full_env = dict(__import__("os").environ)
    full_env["PYTHONPATH"] = str(work_dir or Path.cwd())
    proc = subprocess.Popen(cmd, cwd=str(work_dir or Path.cwd()),
                            stdout=log_fp, stderr=err_fp,
                            stdin=subprocess.DEVNULL,
                            env=full_env, **kwargs)
    new_pid = proc.pid
    (state / "daemon.pid").write_text(str(new_pid), encoding="utf-8")
    info = {"pid": new_pid, "host": host, "port": port,
            "started_at": time.time(), "cmd": cmd}
    (state / "daemon.json").write_text(
        json.dumps(info, indent=2), encoding="utf-8")
    logger.info("Daemon started: pid=%d at %s:%d", new_pid, host, port)
    return {"ok": True, "already_running": False, **info}


def stop_daemon(work_dir=None, timeout_s=10):
    pid = _read_pid(work_dir)
    if not _pid_alive(pid):
        return True
    try:
        if os.name == "nt":
            import psutil
            psutil.Process(pid).terminate()
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as exc:
        logger.warning("daemon stop: %s", exc)
    deadline = time.time() + timeout_s
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.2)
    if _pid_alive(pid):
        try:
            if os.name == "nt":
                import psutil
                psutil.Process(pid).kill()
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    try:
        (_state_dir(work_dir) / "daemon.pid").unlink()
    except Exception:
        pass
    return not _pid_alive(pid)


def status_daemon(work_dir=None):
    pid = _read_pid(work_dir)
    info = _read_info(work_dir)
    alive = _pid_alive(pid)
    out = {"alive": alive, "pid": pid, **(info or {})}
    return out


def _read_info(work_dir=None):
    p = _state_dir(work_dir) / "daemon.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["start", "stop", "status", "restart"])
    p.add_argument("--port", type=int, default=8900)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--work-dir", default=".")
    args = p.parse_args()
    work_dir = Path(args.work_dir).resolve()
    if args.command == "start":
        info = start_daemon(work_dir, port=args.port, host=args.host)
        print(json.dumps(info, indent=2))
    elif args.command == "stop":
        ok = stop_daemon(work_dir)
        print(f'{{"ok": {str(ok).lower()}}}')
    elif args.command == "restart":
        stop_daemon(work_dir)
        info = start_daemon(work_dir, port=args.port, host=args.host)
        print(json.dumps(info, indent=2))
    elif args.command == "status":
        info = status_daemon(work_dir)
        print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
