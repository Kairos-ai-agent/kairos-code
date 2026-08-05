"""Kairos Code - Main entry point."""

from __future__ import annotations

import logging

import uvicorn

from kairos import __version__
from kairos.config.settings import settings

logger = logging.getLogger(__name__)

def main():
    """Start the Kairos Code server."""
    print(f"""
╔══════════════════════════════════════════════╗
║           Kairos Code v{__version__}                 ║
║   Multi-Agent Collaboration Platform         ║
╚══════════════════════════════════════════════╝

Server: http://{settings.host}:{settings.port}
Docs:   http://{settings.host}:{settings.port}/docs
Debug:  {settings.debug}
    """)

    # Provider client cleanup happens in api.app.lifespan — closing them
    # here (via atexit + a hand-rolled event loop) used to be a no-op
    # because the loop was already closed by the time atexit fired.
    uvicorn.run(
        "api.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="info",
    )

if __name__ == "__main__":
    main()