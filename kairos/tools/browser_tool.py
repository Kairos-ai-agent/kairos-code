"""BrowserTool — the agent drives the project's real browser.

`kairos/browser.py` and the Browser tab shipped in R38.6 §32 with tests and no
caller: the toolset was files / terminal / network only, so "open the page and
see whether it renders" stayed a human-only step. This tool is the caller.

It drives the *same* per-project Chromium context the user sees in the Browser
tab (`kairos.browser.default_manager()`), so a navigation the model makes shows
up there live and its screenshot is a file the user can open.

Two rules worth knowing:

* URLs pass through `kairos.netsec.validate_config_url` -- the lenient guard.
  The loopback and LAN hosts a developer legitimately verifies (their own dev
  server, a staging box) are allowed; the link-local range (`169.254.169.254`,
  cloud metadata) and unresolvable hosts are refused. Webfetch keeps the strict
  guard; a browser is used to look at your own machine, a fetch is not.
* Screenshots land under the browser manager's data dir, never in the project
  workspace, so browsing can never dirty the tree the Reviewer is reading.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from kairos.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# Actions that change where we are or send something somewhere. `kairos.sentinel`
# refuses these in a tainted run (see `_READ_ONLY_SCREEN_ACTIONS` there); keep the
# two lists in agreement.
ACTING_ACTIONS = (
    "open", "navigate", "click", "type", "key", "press",
    "back", "forward", "reload", "evaluate", "viewport",
)
READ_ACTIONS = ("screenshot", "current", "console", "content", "text")
ALL_ACTIONS = ACTING_ACTIONS + READ_ACTIONS + ("close",)

# Default expression for action="content". A text-only model cannot read a PNG,
# so the page's visible text is the action that actually makes this tool useful
# to it.
_TEXT_EXPRESSION = "document.body ? document.body.innerText : ''"


class BrowserTool(BaseTool):
    name = "browser"
    description = (
        "Drive the project's browser. Open a URL, click, type, press keys, run "
        "JS, read the page text or the console, and take a screenshot. Use it "
        "to verify what a UI actually renders instead of guessing from the "
        "source. A screenshot returns a path on disk the user can open; when "
        "you cannot see images, use action='content' to read the page as text."
    )

    def __init__(self, allowed_root: "str | Path" = ".",
                 project_id: str = "", manager: Any = None,
                 data_dir: "Optional[Path]" = None):
        super().__init__(allowed_root=allowed_root)
        self._project_id = project_id or ""
        self._manager = manager
        self._data_dir = Path(data_dir) if data_dir else None

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
                        "enum": list(ALL_ACTIONS),
                        "description": (
                            "navigate/open: go to a URL. screenshot: save a PNG "
                            "and return its path. content: the page's visible "
                            "text. console: recent console messages. current: "
                            "url/title/viewport. evaluate: run JS. click: at x,y "
                            "(get them from a screenshot). type/key: into the "
                            "focused element. viewport/back/forward/reload/close."
                        ),
                    },
                    "url": {"type": "string",
                            "description": "URL for action=navigate/open"},
                    "x": {"type": "integer", "description": "x for click"},
                    "y": {"type": "integer", "description": "y for click"},
                    "text": {"type": "string",
                             "description": "text to type (action=type)"},
                    "key": {"type": "string",
                            "description": "key to press, e.g. Enter (action=key)"},
                    "expression": {"type": "string",
                                   "description": "JS expression (action=evaluate)"},
                    "full_page": {"type": "boolean", "default": False,
                                  "description": "capture the whole page"},
                    "width": {"type": "integer", "description": "viewport width"},
                    "height": {"type": "integer", "description": "viewport height"},
                    "max_chars": {"type": "integer", "default": 8000,
                                  "description": "truncate text output"},
                },
                "required": ["action"],
            },
        }

    def _mgr(self):
        if self._manager is None:
            from kairos.browser import default_manager
            self._manager = default_manager(self._data_dir)
        return self._manager

    def _pid(self) -> str:
        if not self._project_id:
            raise ValueError(
                "this browser tool was built without a project id; the browser "
                "is per project and cannot be addressed"
            )
        return self._project_id

    # -- execution --------------------------------------------------------

    async def execute(self, action: str = "", url: str = "", text: str = "",
                      key: str = "", expression: str = "",
                      x: Optional[int] = None, y: Optional[int] = None,
                      full_page: bool = False, width: Optional[int] = None,
                      height: Optional[int] = None, max_chars: int = 8000,
                      **kwargs) -> ToolResult:
        act = (action or "").strip().lower()
        # Convenience the model reliably reaches for: a bare url means navigate.
        if not act and url:
            act = "navigate"
        if act and act not in ALL_ACTIONS:
            return ToolResult(
                success=False, output="",
                error="unknown action " + repr(act) + "; valid: "
                      + ", ".join(ALL_ACTIONS),
            )
        if not act:
            return ToolResult(success=False, output="",
                              error="action is required")

        if act in ("open", "navigate"):
            if not url:
                return ToolResult(success=False, output="",
                                  error="url is required for action=" + act)
            try:
                from kairos.netsec import validate_config_url
                validate_config_url(url, what="url")
            except ValueError as e:
                return ToolResult(success=False, output="", error=str(e))

        try:
            pid = self._pid()
            mgr = self._mgr()
            return await self._dispatch(act, mgr, pid, url=url, text=text,
                                        key=key, expression=expression, x=x, y=y,
                                        full_page=full_page, width=width,
                                        height=height, max_chars=max_chars)
        except Exception as e:  # noqa: BLE001
            logger.debug("browser tool failed: %s", e, exc_info=True)
            return ToolResult(success=False, output="", error=str(e))

    async def _dispatch(self, act: str, mgr: Any, pid: str, *, url: str,
                        text: str, key: str, expression: str,
                        x: Optional[int], y: Optional[int], full_page: bool,
                        width: Optional[int], height: Optional[int],
                        max_chars: int) -> ToolResult:
        meta: dict = {"action": act, "project_id": pid}

        if act in ("open", "navigate"):
            info = await mgr.navigate(pid, url)
            meta.update(info)
            head = "opened " + str(info.get("url") or url)
            if info.get("error"):
                return ToolResult(
                    success=False, output="", error=str(info["error"]),
                    metadata=meta)
            lines = [
                head,
                "title: " + str(info.get("title") or "(none)"),
                "http status: " + str(info.get("status")),
            ]
            return ToolResult(success=True, output="\n".join(lines),
                              metadata=meta)

        if act == "screenshot":
            path = await mgr.save_screenshot(pid, full_page=full_page)
            size = Path(path).stat().st_size
            meta.update({"path": str(path), "bytes": size,
                         "full_page": bool(full_page)})
            return ToolResult(
                success=True,
                output="screenshot saved: " + str(path)
                       + " (" + str(size) + " bytes, "
                       + ("full page" if full_page else "viewport") + ")",
                metadata=meta)

        if act == "current":
            info = await mgr.current(pid)
            meta.update(info)
            return ToolResult(
                success=True,
                output="url: " + str(info.get("url")) + "\ntitle: "
                       + str(info.get("title")) + "\nviewport: "
                       + json.dumps(info.get("viewport")) + "\nconsole messages: "
                       + str(info.get("console_count")),
                metadata=meta)

        if act == "console":
            msgs = await mgr.get_console(pid, limit=50)
            meta["count"] = len(msgs)
            if not msgs:
                return ToolResult(success=True,
                                  output="console is empty (no messages yet)",
                                  metadata=meta)
            lines = []
            for m in msgs[-25:]:
                if isinstance(m, dict):
                    lines.append(str(m.get("type", "log")) + ": "
                                 + str(m.get("text", ""))[:400])
                else:
                    lines.append(str(m)[:400])
            return ToolResult(success=True,
                              output="console (" + str(len(msgs)) + " recorded):\n"
                                     + "\n".join(lines),
                              metadata=meta)

        if act in ("content", "text"):
            expr = expression or _TEXT_EXPRESSION
            value = await mgr.evaluate(pid, expr)
            body = value if isinstance(value, str) else json.dumps(value,
                                                                  default=str)
            meta["chars"] = len(body)
            if len(body) > max_chars:
                body = body[:max_chars] + "\n… truncated at " + str(max_chars) + " chars"
            return ToolResult(success=True, output=body or "(empty page)", metadata=meta)

        if act == "evaluate":
            if not expression:
                return ToolResult(success=False, output="",
                                  error="expression is required for action=evaluate")
            value = await mgr.evaluate(pid, expression)
            try:
                rendered = json.dumps(value, default=str, ensure_ascii=False)
            except Exception:  # noqa: BLE001
                rendered = repr(value)
            if len(rendered) > max_chars:
                rendered = rendered[:max_chars] + "… (truncated)"
            meta["result_chars"] = len(rendered)
            return ToolResult(success=True, output=rendered, metadata=meta)

        if act == "click":
            if x is None or y is None:
                return ToolResult(
                    success=False, output="",
                    error="click needs x and y; take a screenshot first and read "
                          "the coordinates off it")
            await mgr.click(pid, int(x), int(y))
            meta.update({"x": int(x), "y": int(y)})
            return ToolResult(success=True,
                              output="clicked (" + str(int(x)) + ", " + str(int(y))
                                     + "); take a screenshot to confirm the result",
                              metadata=meta)

        if act == "type":
            if not text:
                return ToolResult(success=False, output="",
                                  error="text is required for action=type")
            await mgr.type_text(pid, text)
            # Deliberately report the length, not the text: whatever was typed
            # may be a credential, and tool output goes into the transcript.
            meta["chars"] = len(text)
            return ToolResult(success=True,
                              output="typed " + str(len(text)) + " characters",
                              metadata=meta)

        if act in ("key", "press"):
            if not key:
                return ToolResult(success=False, output="",
                                  error="key is required for action=key")
            await mgr.press_key(pid, key)
            meta["key"] = key
            return ToolResult(success=True, output="pressed " + key,
                              metadata=meta)

        if act == "viewport":
            if not width or not height:
                return ToolResult(success=False, output="",
                                  error="width and height are required")
            await mgr.set_viewport(pid, int(width), int(height))
            meta.update({"width": int(width), "height": int(height)})
            return ToolResult(success=True,
                              output="viewport set to " + str(int(width)) + "x"
                                     + str(int(height)),
                              metadata=meta)

        if act == "back":
            await mgr.back(pid)
            return ToolResult(success=True, output="went back", metadata=meta)
        if act == "forward":
            await mgr.forward(pid)
            return ToolResult(success=True, output="went forward", metadata=meta)
        if act == "reload":
            await mgr.reload(pid)
            return ToolResult(success=True, output="reloaded", metadata=meta)
        if act == "close":
            closed = await mgr.close_project(pid)
            meta["closed"] = bool(closed)
            return ToolResult(success=True,
                              output="browser closed" if closed
                                     else "there was no browser open for this project",
                              metadata=meta)

        return ToolResult(success=False, output="",
                          error="unhandled action " + repr(act))
