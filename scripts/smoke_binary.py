#!/usr/bin/env python
"""Smoke-test a built ``kairos-code`` binary end to end, headlessly.

Proves the frozen artifact actually works — not merely that PyInstaller exited 0:

1. start it with ``--no-browser`` on a free port,
2. wait for ``GET /api/health`` to answer 200,
3. fetch ``/`` and require HTML back (that is the bundled Web UI, so a build
   without ``web/dist`` fails here),
4. shut it down and report.

    python scripts/smoke_binary.py dist/kairos-code            # or ./dist/kairos-code.exe
    python scripts/smoke_binary.py <binary> --port 9988 --timeout 120
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
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


def main() -> int:
    parser = argparse.ArgumentParser(prog="smoke_binary.py")
    parser.add_argument("binary", help="path to the frozen kairos-code executable")
    parser.add_argument("--port", type=int, default=9988)
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="seconds to wait for the server to answer")
    args = parser.parse_args()

    binary = Path(args.binary).expanduser().resolve()
    if not binary.is_file():
        print(f"FAIL: no such binary: {binary}", file=sys.stderr)
        return 2

    base = f"http://127.0.0.1:{args.port}"
    # A windowed build has no stdout to write to, so never pipe it.
    proc = subprocess.Popen(
        [str(binary), "--no-browser", "--host", "127.0.0.1", "--port", str(args.port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    print(f"[smoke] started pid={proc.pid}  {binary.name}  ({binary.stat().st_size/1048576:.1f} MB)")

    try:
        deadline = time.time() + args.timeout
        health = None
        while time.time() < deadline:
            if proc.poll() is not None:
                print(f"FAIL: the binary exited early with code {proc.returncode}",
                      file=sys.stderr)
                return 1
            code, body = get(f"{base}/api/health")
            if code == 200:
                health = body
                break
            time.sleep(1.0)

        if health is None:
            print(f"FAIL: /api/health never returned 200 within {args.timeout:.0f}s",
                  file=sys.stderr)
            return 1
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

        print("PASS: the frozen binary serves the UI and its API")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
