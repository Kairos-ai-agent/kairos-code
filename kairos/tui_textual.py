"""Round 18: a Textual-based terminal UI for Kairos.

A focused slice (not a full rewrite) — three panels:
  - Skills browser (lists discovered skills + body preview)
  - Cost dashboard (per-model spend, top 5 most expensive)
  - Recent LLM log (tail of cost.jsonl as a virtual scroller)

Run with: ``python -m kairos.tui`` from a terminal. Falls back
to a graceful "Textual not installed" message on plain
environments; the rest of the CLI still works.

The TUI is intentionally minimal: it does NOT spawn a new
agent loop or interfere with the running server. It's a
read-only dashboard. Heavy work (running the Coder) still
happens via the FastAPI server + Web UI.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Lazy-import textual so the rest of the codebase doesn't
# require it. We catch ImportError at the entrypoint and
# fall back to a plain CLI.
try:
    from textual.app import App, ComposeResult
    from textual.widgets import Static, DataTable, Header, Footer, Input
    from textual.containers import Horizontal, Vertical
    from textual.reactive import reactive
    from textual.binding import Binding
    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False
    App = object  # placeholder for type hints


def _load_cost_log(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read up to 200 entries from the cost JSONL log (newest first)."""
    if db_path is None:
        import os
        data_dir = Path(os.environ.get(
            "KAIROS_DATA_DIR",
            Path(__file__).resolve().parent.parent / "data",
        ))
        db_path = data_dir / "cost.jsonl"
    if not db_path.exists():
        return []
    try:
        text = db_path.read_text(encoding="utf-8", errors="replace")
        lines = [l for l in text.splitlines() if l]
        lines.reverse()
        out: List[Dict[str, Any]] = []
        for line in lines[:200]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
    except OSError:
        return []


def _load_skills() -> List[Any]:
    """Discover all skills via the standard loader."""
    try:
        from kairos.skills import SkillsLoader
        loader = SkillsLoader()
        return loader.discover()
    except Exception as exc:
        logger.debug("skill load failed: %s", exc)
        return []


def _render_cost_table(cost_log: List[Dict[str, Any]]) -> str:
    """Render a compact cost table for the TUI."""
    if not cost_log:
        return "[dim]No cost data yet. Run an eval to populate.[/dim]"
    # Aggregate by model
    by_model: Dict[str, Dict[str, float]] = {}
    for e in cost_log:
        m = e.get("model", "unknown")
        slot = by_model.setdefault(m, {
            "calls": 0, "cost": 0.0, "in_tok": 0, "out_tok": 0,
        })
        slot["calls"] += 1
        slot["cost"] += float(e.get("cost_usd", 0.0))
        slot["in_tok"] += int(e.get("prompt_tokens", 0))
        slot["out_tok"] += int(e.get("completion_tokens", 0))
    rows = sorted(by_model.items(), key=lambda x: -x[1]["cost"])
    lines = ["[bold]Model                      Calls  In-tok  Out-tok  $USD[/bold]"]
    total_cost = 0.0
    total_calls = 0
    for m, d in rows[:10]:
        lines.append(
            f"{m:30s} {int(d['calls']):>5}  "
            f"{int(d['in_tok']):>6}  {int(d['out_tok']):>6}  "
            f"[green]${d['cost']:.6f}[/green]"
        )
        total_cost += d["cost"]
        total_calls += d["calls"]
    lines.append("")
    lines.append(f"[bold]Total:[/bold] {int(total_calls)} calls, "
                 f"${total_cost:.6f} USD")
    return "\n".join(lines)


def _render_skills_table(skills: List[Any], query: str = "") -> str:
    """Render a skills list (filtered by query if given)."""
    if not skills:
        return "[dim]No skills discovered. Check kairos/skills/.[/dim]"
    if query:
        q = query.lower()
        skills = [s for s in skills
                  if q in s.name.lower() or q in (s.body or "").lower()]
    if not skills:
        return f"[dim]No skills match '{query}'.[/dim]"
    lines = [f"[bold]{len(skills)} skills:[/bold]"]
    for s in skills[:20]:
        prio = float(s.priority) if s.priority is not None else 0.5
        body_preview = (s.body or "").replace("\n", " ")[:80]
        lines.append(
            f"  [cyan]{s.name}[/cyan]  [dim](prio {prio:.2f})[/dim]\n"
            f"    [dim]{body_preview}[/dim]"
        )
    return "\n".join(lines)


def _render_recent_log(cost_log: List[Dict[str, Any]]) -> str:
    """Render the recent LLM calls as a table."""
    if not cost_log:
        return "[dim]No calls yet.[/dim]"
    lines = ["[bold]Recent LLM calls (newest first):[/bold]"]
    for e in cost_log[:30]:
        ts = float(e.get("timestamp", 0.0))
        import datetime
        when = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        model = e.get("model", "?")
        cost = float(e.get("cost_usd", 0.0))
        in_tok = int(e.get("prompt_tokens", 0))
        out_tok = int(e.get("completion_tokens", 0))
        ms = int(e.get("duration_ms", 0))
        lines.append(
            f"  [dim]{when}[/dim]  {model:30s}  "
            f"[green]${cost:.5f}[/green]  "
            f"{in_tok}↑ {out_tok}↓  [dim]{ms}ms[/dim]"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The Textual App
# ---------------------------------------------------------------------------


if _HAS_TEXTUAL:

    class KairosTUI(App):
        """Three-pane Textual dashboard for Kairos.

        Layout:
          ┌─ Skills (top-left) ─ Cost (top-right) ─┐
          │                                          │
          ├──────────── Recent log (bottom) ───────┤
          │   [q] quit  [r] refresh  [/] focus search │
          └──────────────────────────────────────────┘
        """

        CSS = """
        Screen {
            layout: vertical;
        }
        #top {
            height: 60%;
        }
        #bottom {
            height: 40%;
            border-top: solid $primary;
        }
        #skills, #cost {
            width: 50%;
            border: round $primary;
            padding: 1 2;
        }
        #log {
            padding: 1 2;
        }
        #search-input {
            height: 3;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("r", "refresh", "Refresh"),
            Binding("slash", "focus_search", "Search"),
        ]

        skill_filter: reactive[str] = reactive("")

        def __init__(self, db_path: Optional[Path] = None):
            super().__init__()
            self._db_path = db_path
            self._refresh_data()

        def _refresh_data(self) -> None:
            self.cost_log = _load_cost_log(self._db_path)
            self.skills = _load_skills()

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Horizontal(id="top"):
                yield Static(id="skills")
                yield Static(id="cost")
            yield Input(placeholder="Filter skills (press / to focus, "
                                    "Enter to clear)", id="search-input")
            yield Static(id="log")
            yield Footer()

        def on_mount(self) -> None:
            self._refresh_panels()

        def on_input_changed(self, event) -> None:
            if event.input.id == "search-input":
                self.skill_filter = event.value
                self._refresh_panels()

        def action_refresh(self) -> None:
            self._refresh_data()
            self._refresh_panels()
            self.notify("Refreshed", timeout=1.5)

        def action_focus_search(self) -> None:
            self.query_one("#search-input").focus()

        def _refresh_panels(self) -> None:
            self.query_one("#skills").update(
                _render_skills_table(self.skills, self.skill_filter)
            )
            self.query_one("#cost").update(
                _render_cost_table(self.cost_log)
            )
            self.query_one("#log").update(
                _render_recent_log(self.cost_log)
            )

else:

    class KairosTUI:  # type: ignore[no-redef]
        """Fallback when textual isn't installed."""
        def __init__(self, *a, **kw):
            raise RuntimeError(
                "Textual is not installed in this environment. "
                "Install it with: pip install textual"
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for ``python -m kairos.tui``."""
    if not _HAS_TEXTUAL:
        print("Textual is not installed. Install with: pip install textual",
              file=sys.stderr)
        return 1
    import argparse
    p = argparse.ArgumentParser(
        prog="kairos.tui",
        description="Kairos terminal UI (Textual-based)",
    )
    p.add_argument("--db", help="Path to cost.jsonl (default: data/cost.jsonl)")
    args = p.parse_args(argv)
    db_path = Path(args.db) if args.db else None
    app = KairosTUI(db_path=db_path)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
