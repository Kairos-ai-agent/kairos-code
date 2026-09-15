"""kairos_code launcher: bundled into single-EXE by PyInstaller.

Responsibilities:
  1. Locate the extracted bundle (MEIPASS at runtime, normal dir when
     running from source).
  2. Resolve the data / log directories (use a per-user app dir, not
     the install location, so the EXE doesn't need write access to
     its own folder).
  3. Start the FastAPI app via uvicorn (programmatically, on a free
     port; default 8900).
  4. Open the default browser at the served URL.
  5. Block on Ctrl+C / SIGTERM for clean shutdown.

PyInstaller bundles this file as the EXE entrypoint, plus the
``kairos`` and ``api`` Python packages, the built frontend ``web/dist``
(under ``--add-data``), and the vendored offline wheels (``vendor/``).
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _bundle_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def _user_app_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "kairos-code"
    return Path.home() / ".kairos-code"


def _pick_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def _open_browser_when_ready(url: str, timeout: float = 30.0) -> None:
    """Open the default browser once the HTTP server is actually serving.

    uvicorn's ``Server.run()`` blocks the main thread, so the browser must
    be opened from a daemon thread. We poll ``/api/health`` until it returns
    200 (the app is truly up) instead of waiting on a ``ready`` Event that
    was never set. The old code waited 15s on the Event, gave up, and the
    browser never opened — so a double-click ran the server fine but produced
    no visible window (\"no response\").

    Writes a small log under the user app dir so the behaviour is inspectable
    post-mortem even though a windowed EXE has no console.
    """
    import urllib.request
    _log = os.environ.get("KAIROS_BROWSER_LOG",
                          str(Path.home() / ".kairos-code" / "browser.log"))
    try:
        with open(_log, "w", encoding="utf-8") as _f:
            _f.write("browser thread started\n")
    except Exception:
        pass
    deadline = time.time() + timeout
    reached = False
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                    url.rstrip("/") + "/api/health", timeout=1.0) as _r:
                if _r.status == 200:
                    reached = True
                    break
        except Exception:
            time.sleep(0.5)
    try:
        with open(_log, "a", encoding="utf-8") as _f:
            _f.write(f"server ready={reached}, opening {url}\n")
    except Exception:
        pass
    try:
        webbrowser.open(url)
    except Exception:
        try:
            os.startfile(url)  # Windows fallback, works inside GUI apps
        except Exception:
            pass


def _serve_bundled_mcp(argv: list) -> int:
    """Become one of the bundled MCP servers, on stdio, in this executable.

    The app launches its bundled servers as ``kairos-code --mcp-serve <name>``
    (see ``kairos.mcp_local_servers.bundled_mcp_command``): a frozen build has no
    ``python -m``, so the executable itself has to be able to turn into the
    server. Without this branch the "server" is a second copy of the whole app,
    which never answers ``initialize`` — and every configured server then costs
    a full request timeout before the UI even comes up.
    """
    import asyncio

    name = argv[argv.index("--mcp-serve") + 1]
    root = Path(argv[argv.index("--root") + 1]) if "--root" in argv else Path.cwd()

    if name == "filesystem":
        from kairos.mcp_filesystem_server import _serve_async as _serve_filesystem
        asyncio.run(_serve_filesystem(root))
    else:
        from kairos.mcp_local_servers import _serve_async as _serve_local
        asyncio.run(_serve_local(name, root))
    return 0


def main() -> int:
    # Must come before argparse: this is a different program (a stdio MCP server)
    # wearing the same executable.
    if "--mcp-serve" in sys.argv:
        return _serve_bundled_mcp(sys.argv)

    parser = argparse.ArgumentParser(prog="kairos-code")
    parser.add_argument("--port", type=int, default=9527)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    bundle = _bundle_root()
    user_dir = _user_app_dir()
    user_dir.mkdir(parents=True, exist_ok=True)
    data_dir = user_dir / "data"
    log_dir = user_dir / "logs"
    data_dir.mkdir(exist_ok=True)
    log_dir.mkdir(exist_ok=True)

    frontend_dist = bundle / "web" / "dist"
    if not frontend_dist.is_dir():
        frontend_dist = None

    os.environ["KAIROS_DATA_DIR"] = str(data_dir)
    os.environ.setdefault("KAIROS_LOG_DIR", str(log_dir))
    os.environ["KAIROS_FRONTEND_DIST"] = str(frontend_dist) if frontend_dist else ""

    port = _pick_port(args.port)
    url = f"http://{args.host}:{port}"

    # R38.6.4 packaging: under windowed EXE sys.stdout can be None, so a
    # plain print() raises ValueError. Use a tiny helper that routes to
    # stderr when stdout is unavailable.
    def _emit(line: str) -> None:
        try:
            print(line)
            return
        except Exception:
            pass
        try:
            if sys.stderr is not None:
                sys.stderr.write(line + "\n")
                sys.stderr.flush()
        except Exception:
            pass

    _emit("==================================================")
    _emit(" Kairos Code")
    _emit(f"  data:  {data_dir}")
    _emit(f"  logs:  {log_dir}")
    _emit(f"  web:   http://{args.host}:{port}")
    _emit("==================================================")
    _emit("  Press Ctrl+C to stop.\n")

    if not args.no_browser:
        threading.Thread(target=_open_browser_when_ready,
                         args=(url, 30.0), daemon=True).start()

    # R38.6.4 packaging: pre-load kairos submodules (with per-module
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
                _f.write(f"OK   {_mod}\n")
            except Exception as _exc:
                _f.write(f"FAIL {_mod}: {_exc!r}\n")
                _f.write(_tb.format_exc())

    import uvicorn  # noqa: E402
    from api.app import app  # noqa: E402

    # R38.6.4 packaging: disable uvicorn's auto-open-browser
    # feature. The default fallback tries the stdlib ``webbrowser``
    # module and, if that fails (e.g. headless / no DISPLAY),
    # falls back to ``playwright``. We don't ship playwright inside
    # the EXE (it would add ~200 MB of headless browser), so the
    # fallback prints ``Browser manager failed to start: No module
    # named 'playwright'`` at startup. The API is unaffected
    # (uvicorn still serves requests on the chosen port); this is
    # just a startup warning. The launcher opens the browser
    # itself when ``--no-browser`` is NOT set, so the uvicorn
    # auto-opener is redundant anyway.
    os.environ.setdefault("UVICORN_NO_BROWSER_OPEN", "1")

    # R38.6.4 packaging: uvicorn's default log_config points the
    # "default" formatter at uvicorn.logging.DefaultFormatter, whose
    # __init__ does ``sys.stdout.isatty()`` to decide colour. Under
    # a windowed EXE (runw.exe) sys.stdout is None, so the formatter
    # blows up with ``AttributeError: 'NoneType' object has no
    # attribute 'isatty'`` and uvicorn then aborts with
    # ``ValueError: Unable to configure formatter 'default'``.
    #
    # Fix: ship our own log_config that:
    #   1. forces DefaultFormatter(use_colors=False) so the isatty
    #      probe is never reached;
    #   2. routes the "default" + "access" handlers at sys.stderr if
    #      it exists, otherwise at a no-op file under the user log
    #      dir, so the EXE never crashes even with no console.
    def _build_log_config() -> dict:
        if sys.stderr is not None:
            stream = "ext://sys.stderr"
        else:
            # windowed EXE: write uvicorn chatter to a log file the
            # user can grep if they need to (default never happens
            # in this branch because we point it at /devnull-style).
            fallback = log_dir / "uvicorn.log"
            try:
                fallback.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            stream = str(fallback)
        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": "uvicorn.logging.DefaultFormatter",
                    "fmt": "%(levelprefix)s %(message)s",
                    "use_colors": False,
                },
                "access": {
                    "()": "uvicorn.logging.AccessFormatter",
                    "fmt": '%(levelprefix)s %(client_addr)s - '
                           '"%(request_line)s" %(status_code)s',
                    "use_colors": False,
                },
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": stream,
                },
                "access": {
                    "formatter": "access",
                    "class": "logging.StreamHandler",
                    "stream": stream,
                },
            },
            "loggers": {
                "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"level": "INFO"},
                "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
            },
        }

    config = uvicorn.Config(
        app, host=args.host, port=port,
        log_level="info", access_log=False,
        log_config=_build_log_config(),
    )
    server = uvicorn.Server(config)
    try:
        server.run()
    finally:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
