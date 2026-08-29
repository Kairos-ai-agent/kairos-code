"""Per-project Playwright browser manager (R38.6 §32).

Each project gets its own persistent browser context so cookies
and login state survive between page visits. The browser is
headless Chromium launched via Playwright's Python SDK.

Why per-project (not global)?
  - Different projects may want different proxy / user-agent
  - Login state in one project shouldn't leak to another
  - "Project A" navigates to localhost:3000, "Project B" to a
    public site — separate contexts prevent cookie mixing.

Lifecycle
---------
  1. ``BrowserManager`` is a singleton owned by the API
     process. It is created lazily on first navigate call.
  2. ``get_context(project_id)`` returns (or creates) the
     Playwright ``BrowserContext`` for that project. Cookies
     are stored in ``<data_dir>/browsers/<project_id>/profile``
     so the user can close the app and reopen without losing
     login state.
  3. ``navigate(project_id, url)`` opens a new tab (or reuses
     the existing one) and returns the new page metadata.
  4. ``screenshot(project_id)`` returns a PNG of the current
     page viewport.
  5. ``click(project_id, x, y)`` clicks at viewport-relative
     coordinates.
  6. ``type_text(project_id, text)`` types into the focused
     element.
  7. ``close_project(project_id)`` tears down the project's
     context (called on project delete or when the user
     explicitly closes the browser tab).

Resource limits
---------------
  - Idle context timeout: 30 minutes (then auto-closed to
    avoid leaking memory when the user is done).
  - Max concurrent projects: 8 (configurable). Beyond this,
    the oldest idle context is closed.
  - Viewport: 1280x800 by default. Can be overridden per
    project via ``set_viewport``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Lazy import — playwright is heavy and may not be installed in
# all environments. We import inside the methods so import-time
# failures are isolated to the actual call site.
_playwright = None


def _get_playwright():
    global _playwright
    if _playwright is None:
        from playwright.async_api import async_playwright
        _playwright = async_playwright
    return _playwright


def _find_chromium_executable() -> Optional[str]:
    """Locate an installed Chromium binary on the local machine.

    Playwright's default ``chromium_headless_shell`` ships with a
    specific build number that may not match the local SDK
    version, so a fresh ``playwright install`` can fail. We fall
    back to any pre-existing ``chromium-*`` directory under
    ``%LOCALAPPDATA%\\ms-playwright`` and use that binary
    directly via ``executable_path``.
    """
    candidates_root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if not candidates_root.exists():
        return None
    # Prefer the full chrome (not headless_shell) — full chrome
    # supports more features if we ever switch to non-headless.
    for entry in sorted(candidates_root.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        if entry.name.startswith("chromium-") and not entry.name.startswith("chromium_headless_shell-"):
            for sub in ("chrome-win64/chrome.exe", "chrome-win/chrome.exe"):
                exe = entry / sub
                if exe.exists():
                    return str(exe)
    return None


# Default viewport — matches a typical laptop so screenshots
# look familiar to the user.
DEFAULT_VIEWPORT = {"width": 1280, "height": 800}

# Idle timeout: if a project hasn't been touched for this long,
# its context is closed automatically. 30 minutes is the sweet
# spot — long enough that a paused user can come back, short
# enough that we don't keep 10 idle Chromium processes alive.
IDLE_TIMEOUT_S = 30 * 60

# Max concurrent projects. Chromium is ~100MB per context, so
# 8 is ~800MB which is acceptable on a developer machine.
MAX_PROJECTS = 8


@dataclass
class ProjectBrowser:
    """State for one project's browser session."""
    project_id: str
    context: Any  # playwright BrowserContext
    page: Any     # playwright Page (the currently-active tab)
    last_used: float = field(default_factory=time.time)
    viewport: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_VIEWPORT))
    console: list = field(default_factory=list)  # last N console msgs


class BrowserManager:
    """Singleton owning Playwright + per-project contexts."""

    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir)
        self._profiles_dir = self._data_dir / "browsers"
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        self._projects: Dict[str, ProjectBrowser] = {}
        self._playwright_ctx = None  # async_playwright() context
        self._playwright = None      # the Playwright instance
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the Playwright server. Idempotent."""
        if self._playwright is not None:
            return
        pw = _get_playwright()
        self._playwright_ctx = pw()
        self._playwright = await self._playwright_ctx.start()

    async def stop(self) -> None:
        """Stop the Playwright server and close all contexts."""
        for proj in list(self._projects.values()):
            try:
                await proj.context.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("context close failed for %s: %s",
                             proj.project_id, exc)
        self._projects.clear()
        if self._playwright_ctx is not None:
            try:
                await self._playwright_ctx.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("playwright stop failed: %s", exc)
        self._playwright_ctx = None
        self._playwright = None

    async def _evict_idle(self) -> None:
        """Close projects that exceed MAX_PROJECTS or IDLE_TIMEOUT_S."""
        now = time.time()
        # Auto-close on idle
        for pid in list(self._projects.keys()):
            proj = self._projects[pid]
            if now - proj.last_used > IDLE_TIMEOUT_S:
                logger.info("auto-closing idle browser for %s "
                            "(idle %ds)", pid, int(now - proj.last_used))
                try:
                    await proj.context.close()
                except Exception:  # noqa: BLE001
                    pass
                del self._projects[pid]
        # Cap concurrent
        while len(self._projects) >= MAX_PROJECTS:
            oldest = min(self._projects.values(),
                         key=lambda p: p.last_used)
            logger.info("evicting oldest browser %s to stay under cap",
                        oldest.project_id)
            try:
                await oldest.context.close()
            except Exception:  # noqa: BLE001
                pass
            del self._projects[oldest.project_id]

    async def get_or_create(self, project_id: str) -> ProjectBrowser:
        """Get (or lazily create) the browser for a project."""
        async with self._lock:
            await self.start()
            await self._evict_idle()
            if project_id in self._projects:
                self._projects[project_id].last_used = time.time()
                return self._projects[project_id]
            profile_dir = self._profiles_dir / project_id
            profile_dir.mkdir(parents=True, exist_ok=True)
            # Persistent context so cookies / localStorage survive
            launch_kwargs = dict(
                user_data_dir=str(profile_dir),
                headless=True,
                viewport=DEFAULT_VIEWPORT,
                args=[
                    "--no-sandbox",  # required when running as root
                    "--disable-dev-shm-usage",  # avoid /dev/shm issues
                ],
            )
            # If the locally-installed Playwright SDK doesn't
            # bundle the matching Chromium build, fall back to
            # any pre-installed Chromium binary on disk.
            exe = _find_chromium_executable()
            if exe:
                launch_kwargs["executable_path"] = exe
            ctx = await self._playwright.chromium.launch_persistent_context(
                **launch_kwargs,
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            proj = ProjectBrowser(
                project_id=project_id,
                context=ctx,
                page=page,
            )
            # Capture console messages for the UI
            def _on_console(msg):
                proj.console.append({
                    "type": msg.type,
                    "text": msg.text,
                    "ts": time.time(),
                })
                # Keep last 200 msgs
                if len(proj.console) > 200:
                    proj.console[:] = proj.console[-200:]
            page.on("console", _on_console)
            self._projects[project_id] = proj
            return proj

    async def close_project(self, project_id: str) -> bool:
        async with self._lock:
            proj = self._projects.pop(project_id, None)
            if proj is None:
                return False
            try:
                await proj.context.close()
            except Exception:  # noqa: BLE001
                pass
            return True

    async def navigate(self, project_id: str, url: str) -> Dict[str, Any]:
        proj = await self.get_or_create(project_id)
        try:
            response = await proj.page.goto(url, wait_until="domcontentloaded",
                                            timeout=15000)
            title = await proj.page.title()
            return {
                "url": proj.page.url,
                "title": title,
                "status": response.status if response else None,
                "ok": response.ok if response else False,
            }
        except Exception as exc:  # noqa: BLE001
            # Don't lose the context — return the error so the UI
            # can show it but the browser stays alive for retry.
            return {
                "url": proj.page.url,
                "title": "",
                "status": None,
                "ok": False,
                "error": str(exc),
            }

    async def screenshot(self, project_id: str,
                         full_page: bool = False) -> bytes:
        proj = await self.get_or_create(project_id)
        return await proj.page.screenshot(full_page=full_page,
                                          type="png")

    async def current(self, project_id: str) -> Dict[str, Any]:
        proj = await self.get_or_create(project_id)
        return {
            "url": proj.page.url,
            "title": await proj.page.title(),
            "viewport": proj.viewport,
            "console_count": len(proj.console),
        }

    async def click(self, project_id: str, x: int, y: int) -> None:
        proj = await self.get_or_create(project_id)
        # Translate viewport-relative coords (what the user sees
        # in the screenshot) to viewport coords (what Playwright
        # expects). They are the same in this default config.
        await proj.page.mouse.click(x, y)

    async def type_text(self, project_id: str, text: str) -> None:
        proj = await self.get_or_create(project_id)
        await proj.page.keyboard.type(text, delay=20)

    async def press_key(self, project_id: str, key: str) -> None:
        proj = await self.get_or_create(project_id)
        await proj.page.keyboard.press(key)

    async def back(self, project_id: str) -> None:
        proj = await self.get_or_create(project_id)
        await proj.page.go_back()

    async def forward(self, project_id: str) -> None:
        proj = await self.get_or_create(project_id)
        await proj.page.go_forward()

    async def reload(self, project_id: str) -> None:
        proj = await self.get_or_create(project_id)
        await proj.page.reload()

    async def get_console(self, project_id: str,
                          limit: int = 50) -> list:
        proj = await self.get_or_create(project_id)
        return proj.console[-limit:]

    async def set_viewport(self, project_id: str, width: int,
                           height: int) -> None:
        proj = await self.get_or_create(project_id)
        await proj.page.set_viewport_size({"width": width,
                                            "height": height})
        proj.viewport = {"width": width, "height": height}

    async def evaluate(self, project_id: str, expression: str) -> Any:
        """Run an arbitrary JS expression on the page. Used by
        the agent to extract DOM state when reasoning about the
        page (e.g. ``document.querySelectorAll('a').length``)."""
        proj = await self.get_or_create(project_id)
        return await proj.page.evaluate(expression)
