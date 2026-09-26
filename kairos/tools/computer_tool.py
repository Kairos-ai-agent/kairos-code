"""ComputerTool — the agent drives the desktop.

`kairos/computer_use.py` shipped with tests and no caller, while the bundled
`computer-use` skill told every model "You have a `computer_use` tool that
drives the user's desktop". The skill was wrong. This is the fix.

The backend is chosen the way the module already chose it: `KAIROS_COMPUTER_USE=1`
or `KAIROS_COMPUTER_USE_PLATFORM=1` on Windows gives the real `SendInput`
backend; otherwise `MockComputerUse`, which records actions and touches nothing.
Every result names the backend that ran, because a mock that silently reports
"clicked" is worse than no tool at all.

Safety notes, both of which the bundled skill used to get backwards:

* The real backend moves the user's actual cursor and types into whatever window
  has focus. It is **not** a background driver. `kairos.sentinel` therefore treats
  every acting action as egress in a tainted run, and the audit trail records the
  action without the typed text.
* Actions are issued with `confirm=True`. The gate has already ruled by the time
  a call reaches here, and a second, weaker confirmation inside the tool would be
  enforcement in a second place -- the exact drift the gate exists to prevent.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import time
from pathlib import Path
from typing import Any, Optional

from kairos.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

ACTIONS = ("capture", "click", "move", "type", "key", "scroll",
           "history", "screen_size")

# MouseButton names we accept from the model.
_BUTTONS = ("left", "right", "middle")


class ComputerTool(BaseTool):
    name = "computer_use"
    description = (
        "Drive the desktop: capture the screen, move or click the mouse, type "
        "text, press a key, scroll. Use it only when a task genuinely needs a "
        "GUI that has no CLI or API. On a machine without a real backend it "
        "runs against a mock that records actions without touching anything -- "
        "the result always says which backend ran, so never claim an action "
        "happened on screen without checking that."
    )

    def __init__(self, allowed_root: "str | Path" = ".", backend: Any = None,
                 out_dir: "Optional[Path]" = None):
        super().__init__(allowed_root=allowed_root)
        self._backend = backend
        self._out_dir = Path(out_dir) if out_dir else None

    # -- plumbing ---------------------------------------------------------

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(ACTIONS),
                        "description": (
                            "capture: screenshot the screen and save a PNG "
                            "(returns its path). click/move: at x,y. type: text. "
                            "key: one key name, e.g. enter. scroll: dx,dy. "
                            "history: what this tool has done. screen_size."
                        ),
                    },
                    "x": {"type": "integer", "description": "x for click/move"},
                    "y": {"type": "integer", "description": "y for click/move"},
                    "text": {"type": "string", "description": "text to type"},
                    "key": {"type": "string",
                            "description": "key name, e.g. enter (action=key)"},
                    "button": {"type": "string", "enum": list(_BUTTONS),
                               "description": "mouse button (default left)"},
                    "dx": {"type": "integer", "description": "horizontal scroll"},
                    "dy": {"type": "integer", "description": "vertical scroll"},
                    "dry_run": {"type": "boolean", "default": False,
                                "description": "validate without acting"},
                },
                "required": ["action"],
            },
        }

    def _be(self) -> Any:
        if self._backend is None:
            from kairos.computer_use import make_default_computer_use
            self._backend = make_default_computer_use()
        return self._backend

    def _backend_name(self) -> str:
        return type(self._be()).__name__

    def _is_mock(self) -> bool:
        return "Mock" in self._backend_name()

    def _dir(self) -> Path:
        if self._out_dir is None:
            from kairos.config.settings import settings
            self._out_dir = Path(settings.data_dir) / "computer_use"
        self._out_dir.mkdir(parents=True, exist_ok=True)
        return self._out_dir

    # -- execution --------------------------------------------------------

    async def execute(self, action: str = "", x: Optional[int] = None,
                      y: Optional[int] = None, text: str = "", key: str = "",
                      button: str = "left", dx: int = 0, dy: int = 0,
                      dry_run: bool = False, **kwargs) -> ToolResult:
        act = (action or "").strip().lower()
        if act not in ACTIONS:
            return ToolResult(
                success=False, output="",
                error="unknown action " + repr(act) + "; valid: "
                      + ", ".join(ACTIONS),
            )
        try:
            return await self._dispatch(act, x=x, y=y, text=text, key=key,
                                        button=button, dx=dx, dy=dy,
                                        dry_run=dry_run)
        except Exception as e:  # noqa: BLE001
            logger.debug("computer_use tool failed: %s", e, exc_info=True)
            return ToolResult(success=False, output="", error=str(e))

    async def _dispatch(self, act: str, *, x: Optional[int], y: Optional[int],
                        text: str, key: str, button: str, dx: int, dy: int,
                        dry_run: bool) -> ToolResult:
        be = self._be()
        name = type(be).__name__
        mock = self._is_mock()
        meta: dict = {"action": act, "backend": name, "mock": mock,
                      "platform": platform.system()}

        def _note() -> str:
            if mock:
                return ("\nbackend: " + name + " (MOCK — nothing on screen "
                        "changed; set KAIROS_COMPUTER_USE_PLATFORM=1 on Windows "
                        "for the real backend)")
            return "\nbackend: " + name

        if act == "screen_size":
            size = await asyncio.to_thread(be.screen_size)
            meta["size"] = list(size)
            return ToolResult(success=True,
                              output="screen: " + str(size[0]) + "x" + str(size[1])
                                     + _note(),
                              metadata=meta)

        if act == "capture":
            data = await asyncio.to_thread(be.screenshot)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = self._dir() / ("screen-" + stamp + "-"
                                  + str(int(time.time() * 1000) % 1000).zfill(3) + ".png")
            path.write_bytes(data)
            meta.update({"path": str(path), "bytes": len(data)})
            return ToolResult(
                success=True,
                output="screen saved: " + str(path) + " (" + str(len(data))
                       + " bytes). A text-only model cannot read the image: "
                         "describe what you need, or use a browser/web tool for "
                         "the same UI." + _note(),
                metadata=meta,
            )

        if act in ("click", "move"):
            if x is None or y is None:
                return ToolResult(success=False, output="",
                                  error=act + " needs x and y (capture first, "
                                              "read the coordinates off the image)")
            xi, yi = int(x), int(y)
            meta.update({"x": xi, "y": yi})
            if act == "move":
                await asyncio.to_thread(be.mouse_move, xi, yi,
                                        confirm=True, dry_run=dry_run)
                meta["dry_run"] = bool(dry_run)
                return ToolResult(success=True,
                                  output="moved the pointer to (" + str(xi)
                                         + ", " + str(yi) + ")"
                                         + (" [dry run]" if dry_run else "")
                                         + _note(), metadata=meta)
            btn = (button or "left").strip().lower()
            if btn not in _BUTTONS:
                return ToolResult(success=False, output="",
                                  error="button must be one of " + ", ".join(_BUTTONS))
            from kairos.computer_use import MouseButton
            enum_btn = getattr(MouseButton, btn.upper(), MouseButton.LEFT)
            meta["button"] = btn
            await asyncio.to_thread(be.mouse_click, xi, yi, enum_btn,
                                    confirm=True, dry_run=dry_run)
            meta["dry_run"] = bool(dry_run)
            return ToolResult(success=True,
                              output="clicked " + btn + " at (" + str(xi) + ", "
                                     + str(yi) + ")"
                                     + (" [dry run]" if dry_run else "")
                                     + "; capture again to confirm the result"
                                     + _note(), metadata=meta)

        if act == "type":
            if not text:
                return ToolResult(success=False, output="",
                                  error="text is required for action=type")
            await asyncio.to_thread(be.type_text, text, confirm=True,
                                    dry_run=dry_run)
            # The length, never the text: whatever is typed may be a credential
            # and tool output ends up in the transcript and the audit trail.
            meta["chars"] = len(text)
            meta["dry_run"] = bool(dry_run)
            return ToolResult(success=True,
                              output="typed " + str(len(text)) + " characters"
                                     + (" [dry run]" if dry_run else "") + _note(),
                              metadata=meta)

        if act == "key":
            if not key:
                return ToolResult(success=False, output="",
                                  error="key is required for action=key")
            await asyncio.to_thread(be.key_press, key, confirm=True,
                                    dry_run=dry_run)
            meta["key"] = key
            meta["dry_run"] = bool(dry_run)
            return ToolResult(success=True,
                              output="pressed " + key
                                     + (" [dry run]" if dry_run else "") + _note(),
                              metadata=meta)

        if act == "scroll":
            if not dx and not dy:
                return ToolResult(success=False, output="",
                                  error="scroll needs dx and/or dy")
            await asyncio.to_thread(be.scroll, int(dx), int(dy), confirm=True,
                                    dry_run=dry_run)
            meta.update({"dx": int(dx), "dy": int(dy), "dry_run": bool(dry_run)})
            return ToolResult(success=True,
                              output="scrolled by (" + str(int(dx)) + ", "
                                     + str(int(dy)) + ")"
                                     + (" [dry run]" if dry_run else "") + _note(),
                              metadata=meta)

        if act == "history":
            hist = be.history_dict() if hasattr(be, "history_dict") else []
            meta["count"] = len(hist)
            if not hist:
                return ToolResult(success=True,
                                  output="no desktop actions in this session" + _note(),
                                  metadata=meta)
            lines = []
            for item in hist[-20:]:
                lines.append(str(item)[:300])
            return ToolResult(success=True,
                              output="last " + str(min(20, len(hist))) + " of "
                                     + str(len(hist)) + " actions:\n"
                                     + "\n".join(lines) + _note(),
                              metadata=meta)

        return ToolResult(success=False, output="",
                          error="unhandled action " + repr(act))
