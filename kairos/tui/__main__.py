"""TUI screenshot / smoke-test helper.

Boots the TUI app in headless mode (using Textual's
``Pilot``), exercises a few interactions, and exports an SVG
screenshot. Useful for:

  - Sanity-checking the TUI before shipping a release
  - Producing visual artifacts for the docs
  - Catching CSS / layout regressions

Usage::

    python -m kairos.tui.screenshot --out kairos-tui.svg
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from kairos.tui import BackendClient, TuiController, build_textual_app


class StaticBackend:
    """In-memory backend used for the screenshot test.

    Returns canned data so the TUI mounts and renders a
    realistic-looking screen without needing a live FastAPI
    server.
    """

    def __init__(self) -> None:
        self.coder_mode = "sandbox"
        self.sessions = [
            {"id": "s1", "round": 3, "started_at": "2026-08-26T10:00:00Z"},
            {"id": "s2", "round": 1, "started_at": "2026-08-25T15:30:00Z"},
            {"id": "s3", "round": 5, "started_at": "2026-08-24T09:00:00Z"},
        ]

    async def list_projects(self):
        return [{"id": "p1", "name": "kairos-demo"}]

    async def list_sessions(self, project_id):
        return list(self.sessions)

    async def get_session_rounds(self, project_id, session_id):
        return []

    async def post_message(self, project_id, text):
        return {"status": "ok", "response": f"[stub] echo: {text}"}

    async def post_answer(self, project_id, ask_id, text):
        return {"status": "ok", "response": f"[stub] answer to {ask_id}: {text}"}

    async def start_loop(self, project_id, requirement):
        return {"status": "started", "session_id": "s1"}

    async def stop_loop(self, project_id):
        return {"status": "stopped"}

    async def set_coder_mode(self, project_id, mode):
        self.coder_mode = mode
        return {"mode": mode}

    async def get_coder_mode(self, project_id):
        return {"mode": self.coder_mode}


async def _capture_screenshot(out_path: Path,
                              backend: StaticBackend,
                              project_id: str = "p1",
                              interactions: int = 3) -> str:
    """Boot the app, type a few messages, export an SVG, return its path."""
    controller = TuiController(backend=backend, project_id=project_id)  # type: ignore[arg-type]
    app_cls = build_textual_app(controller)
    app = app_cls(controller)

    # Pre-populate some turns so the screenshot looks lived-in.
    controller.state.project_name = "kairos-demo"
    controller.state.add_turn("user", "Hi Kairos, set up a small benchmark please.")
    controller.state.add_turn("coder",
        "Sure — I'll create a HumanEval-style problem set with 5 functions "
        "and run the loop in sandbox mode so writes are isolated.")
    controller.state.add_turn("tool", "Created kairos/bench/problems.py (5 problems).",
        tool="file_write")
    controller.state.add_turn("system", "/help → 10 slash commands available")

    async with app.run_test(size=(120, 40)) as pilot:
        # Wait for mount
        await pilot.pause()
        # Type a few more messages
        for i in range(interactions):
            composer = app.query_one("#composer")
            composer.value = f"Continue with test {i + 1}"
            await pilot.press("enter")
            await pilot.pause()

        # Export the screenshot
        svg = app.export_screenshot(title="Kairos TUI")
        out_path.write_text(svg, encoding="utf-8")
        return str(out_path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="TUI screenshot helper")
    parser.add_argument("--out", default="kairos-tui.svg",
                        help="output SVG path")
    parser.add_argument("--project", default="p1")
    parser.add_argument("--interactions", type=int, default=3)
    args = parser.parse_args(argv)

    out = Path(args.out)
    backend = StaticBackend()
    written = asyncio.run(_capture_screenshot(
        out, backend,
        project_id=args.project,
        interactions=args.interactions,
    ))
    print(f"wrote {written} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
