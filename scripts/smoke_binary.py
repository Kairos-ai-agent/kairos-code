#!/usr/bin/env python
"""Smoke-test a built ``kairos-code`` binary end to end, headlessly.

Proves the artifact actually works — not merely that PyInstaller exited 0:

1. start it with ``--no-browser`` on a free port,
2. wait for ``GET /api/health`` to answer 200,
3. fetch ``/`` and require HTML back (that is the bundled Web UI, so a build
   without ``web/dist`` fails here),
4. shut it down, and print the child's own output if anything went wrong.

    python scripts/smoke_binary.py dist/kairos-code            # ./dist/kairos-code.exe
    python scripts/smoke_binary.py --installed /tmp/v/bin/kairos

``--installed`` targets a console script from a real `pip install` (it takes the
``serve`` subcommand) instead of the frozen launcher. That mode is what catches
an undeclared dependency: the first release dry run shipped binaries whose server
died at import time because `python-multipart` was missing from the core set,
and only a clean install reveals that — a developer venv has it anyway.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception:  # noqa: BLE001 - not up yet
        return 0, ""


def terminate_tree(proc: subprocess.Popen) -> None:
    """Stop the server *and* everything it started.

    A frozen build is a PyInstaller bootloader: the process we spawn immediately forks
    the real server, so terminating that pid alone leaves an invisible, port-holding
    orphan behind — and on Windows it also keeps a lock on the executable, which makes
    the next build fail with ``[WinError 5]``. ``taskkill /T`` ends the whole tree; on
    POSIX the child gets its own session (see the Popen call) so signalling its process
    group cannot reach this process.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, check=False)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def check_bundled_mcp(binary: Path, frozen: bool = True) -> tuple[bool, str]:
    """A bundled server is only shipped if it can actually answer.

    The content check below counts what is inside the binary, and it passed on a
    build where the ``mcp`` package had never been frozen in: five servers were
    listed, none could start (ModuleNotFoundError: No module named 'mcp'), every
    start burned a request timeout, and the whole MCP layer — including the HTTP
    transport — was unreachable in the packaged app. Counting is not running.

    Only a frozen build is asked to answer. ``--installed`` exercises a source
    install built from core dependencies, where ``mcp`` is an optional extra and
    its absence is correct rather than a defect — and ``--mcp-serve`` is a flag
    only the frozen launcher has.
    """
    if not frozen:
        return True, ("bundled MCP servers not probed: this is a source install, "
                      "and mcp is an optional extra")
    initialize = (
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        b'{"protocolVersion":"2024-11-05","capabilities":{},'
        b'"clientInfo":{"name":"smoke","version":"0"}}}\n'
    )
    tried, failures = [], []
    for name in ("time", "filesystem"):
        args = [str(binary), "--mcp-serve", name]
        if name == "filesystem":
            args += ["--root", str(binary.parent)]
        try:
            proc = subprocess.run(args, input=initialize, capture_output=True,
                                  timeout=90)
        except subprocess.TimeoutExpired:
            failures.append(f"{name}: no answer within 90s")
            tried.append(name)
            continue
        tried.append(name)
        if b'"serverInfo"' not in proc.stdout:
            # Print enough of stderr to actually diagnose it. Taking only the
            # last line gave "Failed to execute script ... unhandled exception",
            # which names neither the exception nor the file — the traceback was
            # three lines above and thrown away. A smoke test that hides the
            # reason is barely better than one that does not run.
            err = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            tail = " | ".join(line.strip() for line in err[-6:])
            failures.append(f"{name}: {tail or 'no output'}")
    if failures:
        return False, "bundled MCP server(s) did not answer: " + "; ".join(failures)
    return True, f"bundled MCP servers answer initialize ({', '.join(tried)})"


def main() -> int:
    parser = argparse.ArgumentParser(prog="smoke_binary.py")
    parser.add_argument("binary", help="path to the frozen kairos-code executable, "
                                       "or the installed console script with --installed")
    parser.add_argument("--installed", action="store_true",
                        help="treat the path as an installed console script (`kairos serve ...`)")
    parser.add_argument("--port", type=int, default=9988)
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="seconds to wait for the server to answer")
    args = parser.parse_args()

    binary = Path(args.binary).expanduser().resolve()
    if not binary.is_file():
        print(f"FAIL: no such binary: {binary}", file=sys.stderr)
        return 2

    base = f"http://127.0.0.1:{args.port}"
    # Never discard the child's output: a frozen app that dies on startup is
    # useless without its traceback, and that is exactly how the first CI run
    # failed — three platforms, no diagnostics. A redirected handle also works
    # for a windowed (GUI-subsystem) build, which has no console of its own.
    log_path = Path(tempfile.gettempdir()) / f"kairos-smoke-{os.getpid()}.log"
    log = log_path.open("wb")
    # Frozen launcher: its own flags, no subcommands.
    argv = [str(binary), "--no-browser", "--host", "127.0.0.1", "--port", str(args.port)]
    if args.installed:
        # Installed console script: `kairos serve --host ... --port ...`.
        argv = [str(binary), "serve", "--host", "127.0.0.1", "--port", str(args.port)]
    # POSIX: its own session, so a process-group kill cannot take this process (or, in
    # CI, the whole job) down with it. Windows: a new process group, which taskkill /T
    # can then end as one tree.
    popen_kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                    if os.name == "nt" else {"start_new_session": True})
    proc = subprocess.Popen(
        argv,
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        **popen_kwargs,
    )
    print(f"[smoke] started pid={proc.pid}  {binary.name}  ({binary.stat().st_size/1048576:.1f} MB)")

    def report(reason: str) -> int:
        print(f"FAIL: {reason}", file=sys.stderr)
        log.flush()
        text = log_path.read_text(encoding="utf-8", errors="replace")
        if text.strip():
            print(f"--- {binary.name} output (last 40 lines) ---", file=sys.stderr)
            for line in text.splitlines()[-40:]:
                print(f"    {line}", file=sys.stderr)
        else:
            print("--- the binary produced no output at all ---", file=sys.stderr)
        return 1

    try:
        deadline = time.time() + args.timeout
        health = None
        while time.time() < deadline:
            if proc.poll() is not None:
                return report(f"the binary exited early with code {proc.returncode}")
            code, body = get(f"{base}/api/health")
            if code == 200:
                health = body
                break
            time.sleep(1.0)

        if health is None:
            return report(f"/api/health never returned 200 within {args.timeout:.0f}s")
        try:
            print(f"[smoke] /api/health 200  {json.dumps(json.loads(health))[:120]}")
        except Exception:  # noqa: BLE001
            print(f"[smoke] /api/health 200  {health[:120]}")

        code, page = get(f"{base}/", timeout=10.0)
        if code != 200 or "<div id=\"root\"" not in page:
            print(f"FAIL: GET / returned {code} and did not look like the SPA "
                  f"(the bundle is probably missing web/dist)", file=sys.stderr)
            return 1
        print(f"[smoke] GET / 200  ({len(page)} bytes of the bundled UI)")

        code, _ = get(f"{base}/assets/definitely-not-here.js")
        print(f"[smoke] missing asset -> {code} (404 expected)")

        # The shipped content must be in the binary, not only in the wheel. A
        # build that serves the UI but carries no skills and no offline MCP
        # servers passes every other check here — which is exactly how the
        # packaged app ended up "installing 36 skills" and loading none.
        code, body = get(f"{base}/api/extensions/capabilities")
        if code != 200:
            return report(f"/api/extensions/capabilities -> {code}")
        caps = json.loads(body)
        skills = (caps.get("skills") or {}).get("count", 0)
        mcp = len((caps.get("mcp") or {}).get("configured") or [])
        plugins = (caps.get("plugins") or {}).get("bundledCount", 0)
        print(f"[smoke] shipped content: {skills} skills, {mcp} MCP server(s), "
              f"{plugins} bundled plugin(s)")
        if skills < 10:
            return report(
                f"only {skills} skills inside the binary — kairos/skills is not packaged")
        if mcp < 5:
            return report(
                f"only {mcp} MCP servers configured — the bundled plugin is not packaged")

        # Counting what is inside is not the same as running it. Ask two of the
        # bundled servers to answer an ``initialize`` over stdio — this is the
        # check that was missing when 0.1.5 shipped with the mcp package absent.
        ok, detail = check_bundled_mcp(binary, frozen=not args.installed)
        print(f"[smoke] {detail}")
        if not ok:
            return report(detail)

        print("PASS: the frozen binary serves the UI and its API")
        return 0
    finally:
        terminate_tree(proc)


if __name__ == "__main__":
    raise SystemExit(main())
