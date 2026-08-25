"""Computer use — desktop automation (screen, mouse, keyboard).

Lets the agent interact with the user's desktop: take a screenshot,
move the mouse, click, type text, press keys. This is the foundation
of Anthropic's "computer use" feature.

Design:

* `ComputerUse` is an abstract base. The `MockComputerUse` is the
  default implementation and is what tests use: every action is
  recorded to a log but nothing actually happens.
* `PlatformComputerUse` is a real implementation that uses
  `ctypes` + the Windows GDI / user32 APIs. It is only enabled
  on Windows; on other platforms the constructor raises
  `ComputerUseError`.
* **Safety**: input actions (click, type, key press) require
  `confirm=True` per call. The agent's plan must opt in to
  each action explicitly. A `dry_run=True` flag is provided
  so the agent can preview what *would* happen.
* All actions are recorded in `ComputerUse.history` as `Action`
  records. The history is JSON-serializable so the trace viewer
  can render the agent's interaction with the desktop.
* No new pip dependencies. We use stdlib (`ctypes`, `base64`,
  `struct`) only.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import struct
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class ComputerUseError(RuntimeError):
    """Raised when a desktop automation action fails."""


class ActionType(str, Enum):
    """The four classes of desktop action."""
    SCREENSHOT = "screenshot"
    MOUSE_MOVE = "mouse_move"
    MOUSE_CLICK = "mouse_click"
    KEY_PRESS = "key_press"
    TYPE_TEXT = "type_text"
    SCROLL = "scroll"


class MouseButton(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


@dataclass
class Action:
    """A single recorded desktop action.

    Attributes:
        id: short unique id
        type: ActionType
        timestamp: unix seconds
        args: action-specific arguments
        result: optional result payload (screenshot path, etc)
        confirmed: whether the user (or the agent) confirmed the action
        dry_run: whether this was a dry run
        error: error message if the action failed
    """
    id: str
    type: ActionType
    timestamp: float
    args: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    confirmed: bool = False
    dry_run: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d


@runtime_checkable
class ComputerUseProtocol(Protocol):
    """Interface every computer-use backend must satisfy."""
    name: str

    def screenshot(self) -> bytes: ...
    def mouse_move(self, x: int, y: int, *, confirm: bool = False,
                    dry_run: bool = False) -> Action: ...
    def mouse_click(self, x: int, y: int, button: MouseButton = MouseButton.LEFT,
                     confirm: bool = False,
                     dry_run: bool = False) -> Action: ...
    def key_press(self, key: str, *, confirm: bool = False,
                   dry_run: bool = False) -> Action: ...
    def type_text(self, text: str, *, confirm: bool = False,
                   dry_run: bool = False) -> Action: ...
    def scroll(self, dx: int, dy: int, *, confirm: bool = False,
                dry_run: bool = False) -> Action: ...


# ---------------------------------------------------------------------------
# MockComputerUse — default backend, used in tests + offline mode
# ---------------------------------------------------------------------------


def _make_tiny_png(width: int = 4, height: int = 4) -> bytes:
    """Create a minimal valid PNG. We use this so `screenshot()`
    returns something the caller can actually base64-decode and
    pass to a multimodal LLM.

    The image is a solid dark gray (RGB 32,32,32). 4x4 is enough
    to be a valid PNG; the real backend would return a full
    desktop screenshot.
    """
    import zlib
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data)
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    # Each scanline: 1 filter byte (0) + width*3 bytes RGB.
    raw = b""
    for _ in range(height):
        raw += b"\x00" + b"\x20\x20\x20" * width
    idat = zlib.compress(raw, 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class MockComputerUse:
    """In-memory computer use backend.

    Every action is recorded in `history` but nothing is sent to
    a real desktop. Screenshots return a tiny valid PNG so callers
    can exercise the multimodal flow end-to-end.
    """

    name = "mock"

    def __init__(self, screenshot_dir: Optional[Path] = None,
                 auto_confirm: bool = False):
        self.history: List[Action] = []
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        if self.screenshot_dir:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.auto_confirm = auto_confirm
        self._screenshot_counter = 0
        self._screen_size = (1920, 1080)  # reported by `screen_size()`

    def screen_size(self) -> tuple:
        return self._screen_size

    def _record(self, action: Action) -> Action:
        self.history.append(action)
        return action

    def screenshot(self) -> bytes:
        data = _make_tiny_png()
        self._screenshot_counter += 1
        a = Action(
            id=uuid.uuid4().hex[:8],
            type=ActionType.SCREENSHOT,
            timestamp=time.time(),
            args={},
            result={"bytes": len(data),
                    "width": 4, "height": 4,
                    "counter": self._screenshot_counter},
        )
        if self.screenshot_dir:
            path = self.screenshot_dir / f"shot_{self._screenshot_counter}.png"
            path.write_bytes(data)
            a.result["path"] = str(path)
        self._record(a)
        return data

    def _check_confirm(self, action: Action, confirm: bool,
                        dry_run: bool) -> None:
        if not (confirm or dry_run or self.auto_confirm):
            raise ComputerUseError(
                f"{action.type.value} requires confirm=True "
                "(or dry_run=True, or auto_confirm on the backend)")
        action.confirmed = confirm or self.auto_confirm
        action.dry_run = dry_run

    def mouse_move(self, x: int, y: int, *, confirm: bool = False,
                    dry_run: bool = False) -> Action:
        a = Action(
            id=uuid.uuid4().hex[:8],
            type=ActionType.MOUSE_MOVE,
            timestamp=time.time(),
            args={"x": int(x), "y": int(y)},
        )
        self._check_confirm(a, confirm, dry_run)
        a.result = {"moved": True}
        return self._record(a)

    def mouse_click(self, x: int, y: int, button: MouseButton = MouseButton.LEFT,
                     confirm: bool = False,
                     dry_run: bool = False) -> Action:
        a = Action(
            id=uuid.uuid4().hex[:8],
            type=ActionType.MOUSE_CLICK,
            timestamp=time.time(),
            args={"x": int(x), "y": int(y),
                  "button": button.value if isinstance(button, MouseButton) else str(button)},
        )
        self._check_confirm(a, confirm, dry_run)
        a.result = {"clicked": True}
        return self._record(a)

    def key_press(self, key: str, *, confirm: bool = False,
                   dry_run: bool = False) -> Action:
        a = Action(
            id=uuid.uuid4().hex[:8],
            type=ActionType.KEY_PRESS,
            timestamp=time.time(),
            args={"key": str(key)},
        )
        self._check_confirm(a, confirm, dry_run)
        a.result = {"pressed": True}
        return self._record(a)

    def type_text(self, text: str, *, confirm: bool = False,
                   dry_run: bool = False) -> Action:
        a = Action(
            id=uuid.uuid4().hex[:8],
            type=ActionType.TYPE_TEXT,
            timestamp=time.time(),
            args={"text": str(text), "length": len(text)},
        )
        self._check_confirm(a, confirm, dry_run)
        a.result = {"typed": True, "chars": len(text)}
        return self._record(a)

    def scroll(self, dx: int, dy: int, *, confirm: bool = False,
                dry_run: bool = False) -> Action:
        a = Action(
            id=uuid.uuid4().hex[:8],
            type=ActionType.SCROLL,
            timestamp=time.time(),
            args={"dx": int(dx), "dy": int(dy)},
        )
        self._check_confirm(a, confirm, dry_run)
        a.result = {"scrolled": True}
        return self._record(a)

    def history_dict(self) -> List[dict]:
        return [a.to_dict() for a in self.history]


# ---------------------------------------------------------------------------
# PlatformComputerUse — real Windows backend
# ---------------------------------------------------------------------------


class PlatformComputerUse:
    """Real Windows desktop automation.

    Uses ctypes + user32 / gdi32 to:
      * screenshot: BitBlt from the desktop window DC into a
        bitmap, then convert to PNG via a stdlib encoder.
      * mouse_move / click: SetCursorPos + mouse_event.
      * key_press / type_text: SendInput with KEYBDINPUT structs.
      * scroll: mouse_event with wheel delta.

    IMPORTANT:
      * The agent's plan must pass `confirm=True` for every input
        action. We enforce this the same way MockComputerUse does.
      * Screenshot capture requires a desktop session. If the
        process is running headless (no visible desktop), the
        `screenshot()` call returns ComputerUseError.
      * Only the ctypes calls are platform-specific; everything
        else (history, confirm, dry_run) is shared with the mock
        so the rest of Kairos doesn't care which backend is in use.
    """

    name = "platform"

    def __init__(self, auto_confirm: bool = False):
        if os.name != "nt":
            raise ComputerUseError(
                "PlatformComputerUse is currently Windows-only")
        import ctypes  # local import to avoid hard dep at module load
        self._ctypes = ctypes
        self.auto_confirm = auto_confirm
        # Lazy-init user32; if the DLL isn't available we fail
        # loudly with a clear error.
        try:
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        except OSError as e:
            raise ComputerUseError(
                f"failed to load user32/gdi32: {e}") from e
        self.history: List[Action] = []
        self._screenshot_counter = 0

    def screen_size(self) -> tuple:
        self._ctypes.windll.user32.GetSystemMetrics.restype = self._ctypes.c_int
        w = self._user32.GetSystemMetrics(0)  # SM_CXSCREEN
        h = self._user32.GetSystemMetrics(1)  # SM_CYSCREEN
        return (w, h)

    def _record(self, a: Action) -> Action:
        self.history.append(a)
        return a

    def _check_confirm(self, a: Action, confirm: bool, dry_run: bool) -> None:
        if not (confirm or dry_run or self.auto_confirm):
            raise ComputerUseError(
                f"{a.type.value} requires confirm=True")
        a.confirmed = confirm or self.auto_confirm
        a.dry_run = dry_run

    def screenshot(self) -> bytes:
        # 1) Get the screen DC.
        user32 = self._user32
        gdi32 = self._gdi32
        hdc_screen = user32.GetDC(0)
        if not hdc_screen:
            raise ComputerUseError("GetDC(NULL) returned NULL")
        png_bytes: Optional[bytes] = None
        w, h = self.screen_size()
        try:
            # 2) Create a compatible DC + bitmap.
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            if not hdc_mem:
                raise ComputerUseError("CreateCompatibleDC failed")
            try:
                hbm = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
                if not hbm:
                    raise ComputerUseError("CreateCompatibleBitmap failed")
                try:
                    # 3) BitBlt the screen into our bitmap.
                    SRCCOPY = 0x00CC0020
                    if not gdi32.BitBlt(hdc_mem, 0, 0, w, h,
                                         hdc_screen, 0, 0, SRCCOPY):
                        raise ComputerUseError("BitBlt failed")
                    # 4) Extract raw bytes (top-down, 32bpp).
                    import ctypes
                    BI_RGB = 0
                    DIB_RGB_COLORS = 0
                    bmi = (ctypes.c_uint8 * 40)()
                    struct.pack_into("<IiiHHIIiiII", bmi, 0,
                                      40, w, -h, 1, 32, BI_RGB,
                                      0, 0, 0, 0, 0)
                    row_size = w * 4
                    buf = (ctypes.c_uint8 * (row_size * h))()
                    got = gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, bmi,
                                            DIB_RGB_COLORS)
                    if not got:
                        raise ComputerUseError("GetDIBits failed")
                    # 5) Convert BGRA -> RGB and encode as PNG.
                    raw = bytes(buf)
                    rgb = bytearray(w * h * 3)
                    for i in range(w * h):
                        b = raw[i * 4 + 0]
                        g = raw[i * 4 + 1]
                        r = raw[i * 4 + 2]
                        rgb[i * 3 + 0] = r
                        rgb[i * 3 + 1] = g
                        rgb[i * 3 + 2] = b
                    png_bytes = _encode_png(w, h, bytes(rgb))
                finally:
                    gdi32.DeleteObject(hbm)
            finally:
                gdi32.DeleteDC(hdc_mem)
        finally:
            user32.ReleaseDC(0, hdc_screen)
        # Record the action after all GDI handles are released.
        self._screenshot_counter += 1
        self._record(Action(
            id=uuid.uuid4().hex[:8],
            type=ActionType.SCREENSHOT,
            timestamp=time.time(),
            args={},
            result={"width": w, "height": h,
                    "bytes": len(png_bytes) if png_bytes else 0,
                    "counter": self._screenshot_counter},
        ))
        return png_bytes  # type: ignore[return-value]

    def mouse_move(self, x: int, y: int, *, confirm: bool = False,
                    dry_run: bool = False) -> Action:
        a = Action(
            id=uuid.uuid4().hex[:8],
            type=ActionType.MOUSE_MOVE,
            timestamp=time.time(),
            args={"x": int(x), "y": int(y)},
        )
        self._check_confirm(a, confirm, dry_run)
        if not dry_run:
            if not self._user32.SetCursorPos(int(x), int(y)):
                raise ComputerUseError(
                    f"SetCursorPos({x},{y}) failed: {self._ctypes.get_last_error()}")
        a.result = {"moved": True, "dry_run": dry_run}
        return self._record(a)

    def mouse_click(self, x: int, y: int, button: MouseButton = MouseButton.LEFT,
                     confirm: bool = False,
                     dry_run: bool = False) -> Action:
        a = Action(
            id=uuid.uuid4().hex[:8],
            type=ActionType.MOUSE_CLICK,
            timestamp=time.time(),
            args={"x": int(x), "y": int(y),
                  "button": button.value if isinstance(button, MouseButton) else str(button)},
        )
        self._check_confirm(a, confirm, dry_run)
        if not dry_run:
            # MOUSEEVENTF_LEFTDOWN  = 0x0002
            # MOUSEEVENTF_LEFTUP    = 0x0004
            # MOUSEEVENTF_RIGHTDOWN = 0x0008
            # MOUSEEVENTF_RIGHTUP   = 0x0010
            # MOUSEEVENTF_MIDDLEDOWN= 0x0020
            # MOUSEEVENTF_MIDDLEUP  = 0x0040
            flags = {
                MouseButton.LEFT: (0x0002, 0x0004),
                MouseButton.RIGHT: (0x0008, 0x0010),
                MouseButton.MIDDLE: (0x0020, 0x0040),
            }[MouseButton(button) if not isinstance(button, MouseButton) else button]
            self._user32.SetCursorPos(int(x), int(y))
            self._user32.mouse_event(flags[0], 0, 0, 0, 0)
            self._user32.mouse_event(flags[1], 0, 0, 0, 0)
        a.result = {"clicked": True, "dry_run": dry_run}
        return self._record(a)

    def key_press(self, key: str, *, confirm: bool = False,
                   dry_run: bool = False) -> Action:
        a = Action(
            id=uuid.uuid4().hex[:8],
            type=ActionType.KEY_PRESS,
            timestamp=time.time(),
            args={"key": str(key)},
        )
        self._check_confirm(a, confirm, dry_run)
        if not dry_run:
            vk = _vkey_from_name(str(key))
            if vk is None:
                raise ComputerUseError(f"unknown key: {key!r}")
            self._send_scan(vk, key_down=True)
            self._send_scan(vk, key_down=False)
        a.result = {"pressed": True, "dry_run": dry_run}
        return self._record(a)

    def type_text(self, text: str, *, confirm: bool = False,
                   dry_run: bool = False) -> Action:
        a = Action(
            id=uuid.uuid4().hex[:8],
            type=ActionType.TYPE_TEXT,
            timestamp=time.time(),
            args={"text": str(text), "length": len(text)},
        )
        self._check_confirm(a, confirm, dry_run)
        if not dry_run:
            for ch in str(text):
                vk = ord(ch.upper())
                # We use the basic VK mapping; non-ASCII chars are
                # dropped (real engines use Unicode SendInput here).
                if vk < 0x20 or vk > 0x7E:
                    continue
                self._send_scan(vk, key_down=True)
                self._send_scan(vk, key_down=False)
        a.result = {"typed": True, "chars": len(text), "dry_run": dry_run}
        return self._record(a)

    def scroll(self, dx: int, dy: int, *, confirm: bool = False,
                dry_run: bool = False) -> Action:
        a = Action(
            id=uuid.uuid4().hex[:8],
            type=ActionType.SCROLL,
            timestamp=time.time(),
            args={"dx": int(dx), "dy": int(dy)},
        )
        self._check_confirm(a, confirm, dry_run)
        if not dry_run:
            # MOUSEEVENTF_WHEEL = 0x0800; high-word of dwData = delta.
            self._user32.mouse_event(0x0800, 0, 0, int(dy) * 120, 0)
        a.result = {"scrolled": True, "dry_run": dry_run}
        return self._record(a)

    def _send_scan(self, vk: int, key_down: bool) -> None:
        """Send a single key event via key_event (legacy; the modern
        SendInput would also work but the struct layout is heavier)."""
        # KEYEVENTF_KEYUP = 0x0002
        flags = 0 if key_down else 0x0002
        self._user32.keybd_event(vk, 0, flags, 0)

    def history_dict(self) -> List[dict]:
        return [a.to_dict() for a in self.history]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """Encode raw RGB bytes as a PNG. Pure stdlib (zlib + struct)."""
    import zlib
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)  # filter: None
        raw.extend(rgb[y * stride:(y + 1) * stride])
    idat = zlib.compress(bytes(raw), 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# Minimal virtual-key table for the most common names. Anything
# not here falls back to ord(letter.upper()).
_VKEY_TABLE = {
    "enter": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pgup": 0x21,
    "pagedown": 0x22, "pgdn": 0x22,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
    "win": 0x5B, "windows": 0x5B, "menu": 0x5D,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}


def _vkey_from_name(name: str) -> Optional[int]:
    if not name:
        return None
    k = name.lower()
    if k in _VKEY_TABLE:
        return _VKEY_TABLE[k]
    # Single ASCII letter?
    if len(k) == 1 and k.isalpha():
        return ord(k.upper())
    if len(k) == 1 and k.isdigit():
        return ord(k)
    return None


def make_default_computer_use(auto_confirm: bool = False) -> Any:
    """Build the default computer-use backend.

    If `KAIROS_COMPUTER_USE_PLATFORM=1` is set AND we're on Windows,
    returns a PlatformComputerUse; otherwise the MockComputerUse.
    Tests can override this with KAIROS_COMPUTER_USE=mock.
    """
    if os.environ.get("KAIROS_COMPUTER_USE") == "mock":
        return MockComputerUse(auto_confirm=auto_confirm)
    if (os.environ.get("KAIROS_COMPUTER_USE_PLATFORM") == "1"
            and os.name == "nt"):
        return PlatformComputerUse(auto_confirm=auto_confirm)
    return MockComputerUse(auto_confirm=auto_confirm)
