"""Kairos Code - Main entry point."""

from __future__ import annotations

import logging
import os

import uvicorn

from kairos import __version__
from kairos.config.settings import settings
from kairos.perf import recommended_workers

logger = logging.getLogger(__name__)


def _resolve_workers() -> int:
    """Pick the worker count from settings, falling back to a
    sensible default based on CPU count.

    - ``workers=0`` (default) → ``min(8, 2 * cpu + 1)``
    - ``workers>=1``            → that exact value
    - ``debug=True``            → forced to 1 (uvicorn's
      ``--reload`` requires a single process)

    Looks up the settings at call time (not at import time) so
    tests can monkeypatch ``kairos.config.settings.settings``.
    """
    from kairos.config.settings import settings as _settings
    if _settings.debug:
        return 1
    if _settings.workers and _settings.workers > 0:
        return _settings.workers
    return recommended_workers()


def _resolve_loop() -> str:
    """Pick the asyncio loop implementation.

    - ``"uvloop"``  → force uvloop (raises on Windows / if missing)
    - ``"asyncio"`` → force the stdlib loop
    - ``"auto"``    → uvloop on POSIX when available, else asyncio
    """
    from kairos.config.settings import settings as _settings
    if _settings.loop and _settings.loop != "auto":
        return _settings.loop
    if os.name == "nt":
        return "asyncio"
    try:
        import uvloop  # noqa: F401
        return "uvloop"
    except ImportError:
        return "asyncio"


def main():
    """Start the Kairos Code server."""
    workers = _resolve_workers()
    loop = _resolve_loop()
    print(f"""
╔══════════════════════════════════════════════╗
║           Kairos Code v{__version__}                 ║
║   Multi-Agent Collaboration Platform         ║
╚══════════════════════════════════════════════╝

Server:  http://{settings.host}:{settings.port}
Docs:    http://{settings.host}:{settings.port}/docs
Debug:   {settings.debug}
Workers: {workers}  ({loop} loop)
    """)

    uvicorn.run(
        "api.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        workers=workers if not settings.debug else None,
        loop=loop if not settings.debug else "asyncio",
        log_level="info",
    )


if __name__ == "__main__":
    main()