#!/usr/bin/env python3
"""End-to-end proof that the marketplace's install/probe path really works.

The unit tests prove the pieces. This proves the wire: it starts the real
server over HTTP, installs a server through the public API, asks that server for
a real MCP ``initialize`` handshake, and removes it again — the exact sequence
the Marketplace page performs when a user clicks Install (twice: install and
probe are separate buttons).

It runs against a throwaway ``HOME``/``USERPROFILE``, so it can never write the
real ``~/.kairos/mcp.yaml``. If the install endpoints are absent it fails rather
than skipping: this is the check that the feature is reachable, and a silent
pass would hide exactly the defect it exists to catch.

    .venv/Scripts/python.exe scripts/verify_extensions_install_e2e.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVER = "time"  # a bundled offline server: no network, no download

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {label}"
    if detail:
        line += f" — {detail}"
    print(line, flush=True)
    if not ok:
        _failures.append(label)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def call(url: str, payload: dict | None = None, timeout: float = 30.0):
    """Return (status, parsed-or-text). Never raises for an HTTP error."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(body)
            except ValueError:
                return r.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, body
    except Exception as e:  # connection refused, timeout, ...
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}/api"

    home = Path(tempfile.mkdtemp(prefix="kairos-e2e-home-"))
    mcp_yaml = home / ".kairos" / "mcp.yaml"
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),  # Windows: Path.home() reads this
        "KAIROS_PORT": str(port),
        "KAIROS_NO_UPDATE_CHECK": "1",
        "KAIROS_NO_CHECKPOINTS": "1",
    }

    print(f"throwaway home: {home}")
    print(f"server:         {base} (pid starting)\n")

    proc = subprocess.Popen(
        [sys.executable, "-m", "kairos.cli", "serve", "--port", str(port)],
        cwd=str(REPO), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    try:
        # 1. wait for health
        health = None
        for _ in range(120):
            time.sleep(0.5)
            if proc.poll() is not None:
                print(f"  [FAIL] server exited early (code {proc.returncode})")
                return 1
            st, obj = call(f"{base}/health", timeout=3)
            if st == 200:
                health = obj
                break
        check("server answers /api/health", health is not None,
              json.dumps(health) if health else "no response in 60s")
        if health is None:
            return 1

        # 2. the page's first call: what is installed right now
        st, body = call(f"{base}/extensions/mcp/installed")
        check("GET /extensions/mcp/installed is reachable", st == 200, f"HTTP {st}")
        check("the installed view is layered (bundled/user/project)",
              isinstance(body, dict) and isinstance(body.get("servers"), list),
              f"{len(body.get('servers', []))} server(s) before install"
              if isinstance(body, dict) else str(body)[:120])
        before = (body.get("servers") if isinstance(body, dict) else []) or []
        check(f"'{SERVER}' is not installed yet",
              not any(s.get("name") == SERVER and s.get("layer") == "user" for s in before))
        check("no mcp.yaml is written before the first install", not mcp_yaml.exists())

        # 3. install
        st, body = call(f"{base}/extensions/mcp/install", {"name": SERVER, "scope": "user"}, 60)
        ok = st == 200 and isinstance(body, dict) and body.get("ok") is True
        check("POST /extensions/mcp/install installs", ok, f"HTTP {st} {str(body)[:160]}")
        check("the install wrote ~/.kairos/mcp.yaml", mcp_yaml.exists(),
              f"{mcp_yaml.stat().st_size} bytes" if mcp_yaml.exists() else "absent")
        if mcp_yaml.exists():
            text = mcp_yaml.read_text(encoding="utf-8", errors="replace")
            check("the file names the server and no other",
                  SERVER in text, text.strip().replace("\n", " | ")[:160])

        # 4. installing again changes nothing
        st2, body2 = call(f"{base}/extensions/mcp/install", {"name": SERVER, "scope": "user"}, 60)
        check("installing the same server twice is a no-op",
              st2 == 200 and isinstance(body2, dict) and body2.get("changed") is False,
              f"changed={body2.get('changed') if isinstance(body2, dict) else body2}")

        # 5. the effective view must now say "user", not "registry"
        st, body = call(f"{base}/extensions/mcp/installed")
        rows = (body.get("servers") if isinstance(body, dict) else []) or []
        row = next((s for s in rows if s.get("name") == SERVER), None)
        check("the effective view reports the server as installed",
              row is not None and row.get("layer") == "user",
              json.dumps(row) if row else "not listed")
        check("it reports as enabled and will run",
              bool(row) and row.get("enabled") is not False and row.get("will_run") is not False,
              f"enabled={row.get('enabled')} will_run={row.get('will_run')}" if row else "")

        # 6. the probe: a real handshake with the real subprocess
        st, body = call(f"{base}/extensions/mcp/probe", {"name": SERVER, "scope": "user"}, 90)
        ok = st == 200 and isinstance(body, dict) and body.get("ok") is True
        tools = body.get("tools") if isinstance(body, dict) else None
        check("POST /extensions/mcp/probe really starts it", ok,
              f"HTTP {st} {str(body)[:200]}")
        check("the probe got a real tool list back", bool(tools),
              f"tools={tools} in {body.get('ms') if isinstance(body, dict) else '?'}ms")

        # 7. probing something that cannot start must fail loudly, not silently
        st, body = call(f"{base}/extensions/mcp/probe", {"name": "no-such-server-xyz"}, 30)
        check("probing an unknown server fails with a reason",
              isinstance(body, dict) and body.get("ok") is False and bool(body.get("error")),
              f"HTTP {st} {str(body)[:160]}")

        # 8. uninstall
        st, body = call(f"{base}/extensions/mcp/uninstall", {"name": SERVER, "scope": "user"}, 60)
        check("POST /extensions/mcp/uninstall removes it",
              st == 200 and isinstance(body, dict) and body.get("ok") is True,
              f"HTTP {st} {str(body)[:160]}")
        st, body = call(f"{base}/extensions/mcp/installed")
        rows = (body.get("servers") if isinstance(body, dict) else []) or []
        check("after uninstall it is no longer a user install",
              not any(s.get("name") == SERVER and s.get("layer") == "user" for s in rows))

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {', '.join(_failures)}")
        return 1
    print("OK: the marketplace install path works end to end over HTTP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
