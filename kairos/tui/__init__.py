"""Terminal UI for Kairos (TUI adapter).

A Textual-based client that talks to the same FastAPI backend the
web UI uses. Lets users who prefer a terminal still benefit from
Kairos without losing any features.

Layout (top to bottom):

  ┌──────────────────────────────────────────────────────────┐
  │ Kairos  project=<name>  mode=<mode>  round=<n>  /help    │  1 line header
  ├────────────────────────┬─────────────────────────────────┤
  │ Sessions (left)        │ Thread (right, scrollable)      │
  │ - session 1            │ user: ...                       │
  │ - session 2            │ coder: ...                      │
  │ - session 3 (active)   │ reviewer: ...                   │
  │                        │ tool: ...                       │
  ├────────────────────────┴─────────────────────────────────┤
  │ > input here (multiline, Enter to send, Alt-Enter nl)   │  1 line composer
  └──────────────────────────────────────────────────────────┘

Key bindings:

  - Enter         — send the current composer line
  - Alt+Enter     — insert a newline in the composer
  - Ctrl+C        — quit
  - Ctrl+L        — clear the screen
  - /<command>    — slash commands (see kairos.commands)

Run with::

    python -m kairos.tui --backend http://127.0.0.1:8000 --project p1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from kairos.commands import (
    CommandContext,
    CommandParser,
    CommandResult,
    parse_command,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backend client (httpx-based; talks to the FastAPI app)
# ---------------------------------------------------------------------------


class BackendClient:
    """Thin async client for the Kairos HTTP API.

    Used by the TUI to list projects, list sessions, post messages,
    and read the WebSocket stream. The interface is intentionally
    small so the TUI stays decoupled from the orchestrator's
    in-process state.
    """

    def __init__(self, base_url: str, *, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_projects(self) -> List[Dict[str, Any]]:
        r = await self._client.get("/api/projects")
        r.raise_for_status()
        return list(r.json().get("projects") or [])

    async def list_sessions(self, project_id: str) -> List[Dict[str, Any]]:
        r = await self._client.get(f"/api/projects/{project_id}/sessions")
        r.raise_for_status()
        return list(r.json().get("sessions") or [])

    async def get_session_rounds(
        self, project_id: str, session_id: str,
    ) -> List[Dict[str, Any]]:
        r = await self._client.get(
            f"/api/projects/{project_id}/sessions/{session_id}/rounds"
        )
        r.raise_for_status()
        return list(r.json().get("rounds") or [])

    async def post_message(self, project_id: str, text: str) -> Dict[str, Any]:
        r = await self._client.post(
            f"/api/projects/{project_id}/ask",
            json={"question": text},
        )
        r.raise_for_status()
        return dict(r.json())

    async def post_answer(self, project_id: str, ask_id: str, text: str) -> Dict[str, Any]:
        r = await self._client.post(
            f"/api/projects/{project_id}/ask/answer",
            json={"ask_id": ask_id, "answer": text},
        )
        r.raise_for_status()
        return dict(r.json())

    async def start_loop(self, project_id: str, requirement: str) -> Dict[str, Any]:
        r = await self._client.post(
            f"/api/projects/{project_id}/start",
            json={"requirement": requirement},
        )
        r.raise_for_status()
        return dict(r.json())

    async def stop_loop(self, project_id: str) -> Dict[str, Any]:
        r = await self._client.post(f"/api/projects/{project_id}/stop")
        r.raise_for_status()
        return dict(r.json())

    async def set_coder_mode(self, project_id: str, mode: str) -> Dict[str, Any]:
        r = await self._client.post(
            f"/api/projects/{project_id}/coder_mode",
            json={"mode": mode},
        )
        r.raise_for_status()
        return dict(r.json())

    async def get_coder_mode(self, project_id: str) -> Dict[str, Any]:
        r = await self._client.get(
            f"/api/projects/{project_id}/coder_mode",
        )
        r.raise_for_status()
        return dict(r.json())


# ---------------------------------------------------------------------------
# Pure-logic model (no Textual dependency) so we can unit-test it
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    role: str
    content: str
    tool: Optional[str] = None

    def render(self) -> str:
        prefix = {
            "user": "[bold cyan]you[/]",
            "coder": "[bold green]coder[/]",
            "reviewer": "[bold magenta]reviewer[/]",
            "tool": "[dim]tool[/]",
            "system": "[yellow]system[/]",
        }.get(self.role, self.role)
        body = self.content
        if self.tool:
            body = f"({self.tool}) {body}"
        return f"{prefix}: {body}"


@dataclass
class TuiState:
    """In-memory state of the TUI session.

    Held outside the Textual widgets so it can be exercised in
    unit tests without booting the event loop.
    """
    project_id: str = ""
    project_name: str = ""
    coder_mode: str = "default"
    session_id: str = ""
    round: int = 0
    turns: List[Turn] = field(default_factory=list)
    pending_ask: Optional[Dict[str, Any]] = None
    running: bool = False

    def add_turn(self, role: str, content: str, tool: Optional[str] = None) -> None:
        self.turns.append(Turn(role=role, content=content, tool=tool))

    def header(self) -> str:
        return (
            f"Kairos  project={self.project_name or '-'}  "
            f"mode={self.coder_mode}  round={self.round}  "
            f"running={'yes' if self.running else 'no'}"
        )

    def transcript(self) -> str:
        return "\n".join(t.render() for t in self.turns)


# We avoid importing dataclass at the top of the public file because
# this subpackage may be imported by tools that don't need it; but
# tests do. Inline the import below to keep the import surface small.
class TuiController:
    """Pure-logic controller for the TUI.

    Holds a :class:`TuiState` and dispatches user input to either a
    slash command (locally) or the backend (via the
    :class:`BackendClient`). The Textual app subclasses this and
    wires UI events to its methods.
    """

    def __init__(self, backend: BackendClient, project_id: str,
                 *, command_parser: Optional[CommandParser] = None) -> None:
        self.backend = backend
        self.state = TuiState(project_id=project_id)
        self.parser = command_parser or CommandParser()

    async def refresh(self) -> None:
        """Pull the latest state from the backend."""
        try:
            sessions = await self.backend.list_sessions(self.state.project_id)
            if sessions and not self.state.session_id:
                self.state.session_id = sessions[0].get("id", "")
                self.state.round = int(sessions[0].get("round", 0))
            # Use the typed method when available, fall back to raw httpx.
            if hasattr(self.backend, "get_coder_mode"):
                mode_info = await self.backend.get_coder_mode(self.state.project_id)
            else:
                # Backwards-compat with old test fakes that only expose _client.
                client = getattr(self.backend, "_client", None)
                if client is None:
                    return
                r = await client.get(
                    f"/api/projects/{self.state.project_id}/coder_mode"
                )
                r.raise_for_status()
                mode_info = r.json()
            self.state.coder_mode = mode_info.get("mode", "default")
        except Exception as exc:  # noqa: BLE001
            self.state.add_turn("system", f"refresh error: {exc}")

    async def submit(self, text: str) -> None:
        """Handle a user-submitted message.

        Slash commands are intercepted and run locally (no LLM call).
        Other text is sent to the backend. If the backend returns a
        pending ask (clarification), it's recorded in the state so
        the next /answer call can answer it.
        """
        text = (text or "").strip()
        if not text:
            return

        # Slash command?
        if parse_command(text):
            ctx = CommandContext(
                project_id=self.state.project_id,
                work_dir="",
                orchestrator=None,
                coder_agent=None,
                reviewer_agent=None,
                metadata={"coder_mode": self.state.coder_mode},
            )
            res = await self.parser.handle(text, ctx)
            await self._apply_command_result(text, res)
            return

        # Otherwise, post to the backend.
        self.state.add_turn("user", text)
        self.state.running = True
        try:
            resp = await self.backend.post_message(self.state.project_id, text)
        except Exception as exc:  # noqa: BLE001
            self.state.add_turn("system", f"network error: {exc}")
            self.state.running = False
            return
        self.state.running = False

        if resp.get("status") == "pending":
            self.state.pending_ask = resp
            self.state.add_turn("system", "(agent asked for clarification)")
        else:
            self.state.add_turn("coder", resp.get("response", "(no response)"))
            self.state.pending_ask = None

    async def _apply_command_result(self, raw: str, res: Optional[CommandResult]) -> None:
        if res is None:
            self.state.add_turn("user", raw)
            return
        if res.error:
            self.state.add_turn("system", f"error: {res.error}")
            return
        if res.system_output:
            self.state.add_turn("system", res.system_output)
        if res.replace_message:
            # Re-submit the replaced message as if the user typed it.
            await self.submit(res.replace_message)
            return
        if res.append_to_message:
            await self.submit(f"{raw} {res.append_to_message}")
            return
        if not res.system_output:
            self.state.add_turn("system", f"(command: {raw})")

    async def answer_pending(self, text: str) -> None:
        """Reply to a pending ask from the backend."""
        if not self.state.pending_ask:
            self.state.add_turn("system", "no pending ask to answer")
            return
        ask_id = self.state.pending_ask.get("ask_id", "")
        try:
            resp = await self.backend.post_answer(
                self.state.project_id, ask_id, text,
            )
        except Exception as exc:  # noqa: BLE001
            self.state.add_turn("system", f"answer error: {exc}")
            return
        self.state.add_turn("user", text)
        self.state.add_turn("coder", resp.get("response", "(no response)"))
        self.state.pending_ask = None


# ---------------------------------------------------------------------------
# Textual app (only imported when running)
# ---------------------------------------------------------------------------


def build_textual_app(controller: TuiController):
    """Construct the Textual App *class*. Imported lazily so the
    rest of the package stays importable in headless test envs.
    """
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal
    from textual.widgets import Footer, Header, Input, RichLog, Static

    class KairosTuiApp(App):
        CSS = """
        Screen { layout: vertical; }
        #header { dock: top; height: 1; background: $accent; color: $text; padding: 0 1; }
        #body { height: 1fr; }
        #sidebar { width: 28; border-right: solid $primary; padding: 1; }
        #thread { padding: 1; }
        #composer { dock: bottom; height: 3; border-top: solid $primary; padding: 0 1; }
        """

        BINDINGS = [
            Binding("ctrl+c", "quit", "Quit", show=True),
            Binding("ctrl+l", "clear", "Clear", show=True),
        ]

        def __init__(self, controller: TuiController) -> None:
            super().__init__()
            self.controller = controller

        def compose(self) -> ComposeResult:
            yield Static(self.controller.state.header(), id="header")
            with Horizontal(id="body"):
                yield Static("Kairos TUI\n\n/projects: /help\n", id="sidebar")
                yield RichLog(id="thread", highlight=True, markup=True, wrap=True)
            yield Input(placeholder="Type a message or /command…  (Enter to send)",
                        id="composer")
            yield Footer()

        async def on_mount(self) -> None:
            await self.controller.refresh()
            self._refresh_header()
            self._refresh_thread()

        def _refresh_header(self) -> None:
            self.query_one("#header", Static).update(
                self.controller.state.header()
            )

        def _refresh_thread(self) -> None:
            log = self.query_one("#thread", RichLog)
            log.clear()
            for line in self.controller.state.transcript().splitlines():
                log.write(line)

        async def on_input_submitted(self, event) -> None:
            inp = event.input
            text = inp.value
            inp.value = ""
            await self.controller.submit(text)
            self._refresh_header()
            self._refresh_thread()

        def action_clear(self) -> None:
            self.controller.state.turns.clear()
            self._refresh_thread()

    return KairosTuiApp


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Kairos terminal UI")
    parser.add_argument("--backend", default="http://127.0.0.1:8000",
                        help="Kairos backend base URL")
    parser.add_argument("--project", required=True, help="Project ID to chat in")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))

    async def _run() -> int:
        backend = BackendClient(args.backend)
        controller = TuiController(backend, args.project)
        app_cls = build_textual_app(controller)
        app = app_cls()
        try:
            await app.run_async()
        finally:
            await backend.aclose()
        return 0

    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
