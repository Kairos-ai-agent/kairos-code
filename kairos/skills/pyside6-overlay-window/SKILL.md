---
name: "pyside6-overlay-window"
description: "Build Windows desktop overlay widgets with PySide6 — transparent frameless always-on-top windows with alpha masks, click-through on transparent regions, drag/throw physics, multi-monitor support, syst"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/.archive/pyside6-overlay-window/SKILL.md"
---
# PySide6 Overlay Window

Class: building **Windows desktop overlay widgets** with PySide6 — transparent, frameless, always-on-top, with click-through on transparent regions, drag/throw physics, and optional autonomous behaviors.

Covers the technique core that all these widgets share: a pet, a sticky note, a floating clock, a Pomodoro timer, a HUD, a screen watermark — they all need the same "transparent frameless always-on-top window with alpha mask" foundation.

---

## When to load this skill

Trigger when the user asks for:
- 桌面宠物 / desktop pet / mascot / desktop companion
- 悬浮便签 / sticky note / floating note
- 桌面时钟 / floating clock / 番茄钟 / Pomodoro overlay
- 屏幕水印 / screen watermark / HUD / overlay HUD
- 截图工具浮窗 / screenshot widget / quick-capture
- 屏幕录制器浮窗 / screen recorder widget
- 任何"始终在最上层、可拖动、半透明"的桌面 widget

Don't load this for: full-screen GUI apps (regular `QMainWindow` is enough), web-based desktop apps (use `pyinstaller-desktop-packaging`), tray-only utilities without visible windows.

---

## Core technique: transparent window with mask

**The right way:**

```python
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QBitmap, QImage, QPainter
from PySide6.QtWidgets import QWidget

class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        # CRITICAL: do NOT use Qt.WindowTransparentForInput — it defeats setMask
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self._pix = QPixmap("pet.png")
        self.resize(self._pix.size())
        self._build_mask()

    def _build_mask(self):
        # Use alpha channel — only opaque pixels are clickable
        img = self._pix.toImage().convertToFormat(QImage.Format_Alpha8)
        self.setMask(QBitmap.fromImage(img))

    def paintEvent(self, ev):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self._pix)
```

**Critical pitfalls:**

1. **`Qt.WindowTransparentForInput` defeats `setMask()`** — the flag tells Qt "ignore ALL mouse events on this widget", which is stronger than what mask does. Don't combine them. Use mask alone for click-through on transparent pixels.

2. **`setWindowFlag(...)` AFTER `show()` silently hides the widget.** Qt will re-show it, but the user sees a flicker. Always set all flags in `__init__` and never modify them later. If you need to toggle something at runtime, do it before the first `show()`.

3. **`setMask(QBitmap.fromImage(alpha_channel))` is the right pattern.** Don't use `QPixmap.mask()` — it's deprecated for click-through. Get the alpha channel from `toImage().convertToFormat(QImage.Format_Alpha8)`.

4. **`Tool` flag** removes the entry from the taskbar (good for pets). Use `SplashScreen` instead if you want it visible in the taskbar.

5. **Mask scaling:** if the window size changes (zoom, scale), call `_build_mask()` again with the new size. Scaled pixmap → new alpha image → new mask.

---

## Drag + throw physics

For widgets that get dragged and (optionally) thrown on release:

```python
import time, math
from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent

class DraggableOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self._press_pos = None
        self._press_global = None
        self._drag_samples = []  # (t, x, y) — sampled for velocity

    def mousePressEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.LeftButton:
            self._press_pos = ev.position().toPoint()
            self._press_global = ev.globalPosition().toPoint()
            self._drag_samples = [(time.time(),
                                   ev.globalPosition().x(),
                                   ev.globalPosition().y())]

    def mouseMoveEvent(self, ev: QMouseEvent):
        if self._press_pos is not None:
            delta = ev.globalPosition().toPoint() - self._press_global
            self.move(self.pos() + delta)
            self._drag_samples.append((time.time(),
                                       ev.globalPosition().x(),
                                       ev.globalPosition().y()))
            # Keep only the most recent 200ms for velocity estimation
            self._drag_samples = [(t, x, y) for (t, x, y) in self._drag_samples
                                  if time.time() - t < 0.2]

    def mouseReleaseEvent(self, ev: QMouseEvent):
        if self._press_pos is not None:
            self._drag_samples.append((time.time(),
                                       ev.globalPosition().x(),
                                       ev.globalPosition().y()))
            t0, x0, y0 = self._drag_samples[0]
            t1, x1, y1 = self._drag_samples[-1]
            dt = max(0.001, t1 - t0)
            vx = (x1 - x0) / dt
            vy = (y1 - y0) / dt
            speed = math.hypot(vx, vy)
            if speed > 200:  # threshold — slow drags don't throw
                self._throw(vx, vy)
            self._press_pos = None

    def _throw(self, vx, vy):
        # Hand off to a physics tick (gravity, bounce, friction)
        self.vx, self.vy = vx, vy
        # ... start a QTimer that applies gravity per tick
```

The 200ms sample window is the sweet spot: enough to filter jitter, short enough to feel responsive.

---

## Multi-monitor screen box

```python
from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import QRect

def get_screen_box():
    """Union of all monitors' available geometry."""
    virt = QRect()
    for s in QGuiApplication.screens():
        virt = virt.united(s.availableGeometry())
    return virt.left(), virt.top(), virt.right(), virt.bottom()
```

**Pitfall:** `primaryScreen()` only gives ONE monitor. For pets/widgets that can move across monitors, always union `screens()`. Recompute occasionally (every few seconds in your tick) — monitor arrangement changes when the user docks/un-docks.

---

## State machine for autonomous behaviors

Pet/widget behaviors (idle → walk → sleep → drag → throw → ...) are best modeled as an enum + tick:

```python
from enum import Enum
import random

class State(Enum):
    IDLE = "idle"
    WALK = "walk"
    SLEEP = "sleep"

class PetOverlay(DraggableOverlay):
    def __init__(self):
        super().__init__()
        self._state = State.IDLE
        self._state_t = 0.0
        self._next_action_t = random.uniform(4, 10)

        from PySide6.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)  # ~30 FPS

    def _tick(self):
        dt = 0.033
        self._state_t += dt
        if self._state == State.IDLE and self._state_t > self._next_action_t:
            self._enter_walk()

    def _enter_walk(self):
        self._state = State.WALK
        self._state_t = 0.0
        # ... pick a target, start moving
```

State transitions in dedicated `_enter_X()` methods keeps the tick readable. Add `on_state_change` signal so the UI can react (e.g., show Z-bubble when entering SLEEP).

---

## System tray integration

```python
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PySide6.QtGui import QIcon

tray = QSystemTrayIcon()
tray.setIcon(QIcon("icon.png"))
tray.setToolTip("My Overlay")

menu = QMenu()
menu.addAction("Show All", lambda: [w.show() for w in overlays])
menu.addAction("Hide All", lambda: [w.hide() for w in overlays])
menu.addSeparator()
menu.addAction("Quit", QApplication.instance().quit)
tray.setContextMenu(menu)
tray.show()
```

**Pitfall:** `app.setQuitOnLastWindowClosed(False)` — otherwise closing all overlay windows will quit the app before user can re-show via tray.

**Auto-show tray notification on first launch** with `tray.showMessage("Title", "Right-click for menu", QSystemTrayIcon.Information, 1500)`.

---

## Image alpha processing: white-bg → transparent

For pets/widgets where the user uploads PNG/JPG with a white background:

```python
import numpy as np
from PIL import Image, ImageFilter

def remove_white_bg(img: Image.Image, threshold=235, feather=2) -> Image.Image:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    arr = np.array(img, dtype=np.int16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    whiteness = np.minimum(np.minimum(r, g), b)  # min channel = whiteness

    # whiteness >= threshold → alpha 0 (white)
    # whiteness <= (255 - (255-threshold)) → alpha 255 (clearly not white)
    white_band = max(1, 255 - threshold)
    alpha = np.clip((threshold - whiteness) * (255 / white_band), 0, 255).astype(np.uint8)

    if feather > 0:
        # Gaussian blur on alpha → smooths the edge so it doesn't look jagged
        alpha_img = Image.fromarray(alpha, mode="L").filter(
            ImageFilter.GaussianBlur(radius=feather)
        )
        alpha = np.array(alpha_img, dtype=np.uint8)

    out = np.array(img, dtype=np.uint8)  # MUST be uint8, not int16
    out[..., 3] = alpha
    return Image.fromarray(out, mode="RGBA")
```

Then `img.getbbox()` gives the tight crop, add a few px padding, scale to standard size.

**Pitfall:** `Image.fromarray(arr, mode="RGBA")` does NOT accept `int16`. Convert back to `uint8` first (the original int16 was only for the subtraction math).

---

## Frame sequence detection

For sprite animations where users upload `pet_001.png`, `pet_002.png`, ... in the same folder:

```python
import re
from pathlib import Path

_FRAME_NUMBER_RE = re.compile(r"^(.+?)_(\d+)(?:_([a-zA-Z]+))?$")

def detect_frame_sequence(path: str) -> list[str]:
    p = Path(path)
    parent = p.parent
    suffix = p.suffix
    stem = p.stem
    m = _FRAME_NUMBER_RE.match(stem)
    if not m:
        return []
    prefix = m.group(1)
    siblings = []
    for f in parent.iterdir():
        if f.suffix.lower() != suffix.lower():
            continue
        sm = _FRAME_NUMBER_RE.match(f.stem)
        if sm and sm.group(1) == prefix:
            try:
                siblings.append((int(sm.group(2)), str(f)))
            except ValueError:
                pass
    if len(siblings) < 2:
        return []
    siblings.sort()
    return [s[1] for s in siblings]
```

Sort by integer frame number, not lex (`pet_10.png` would otherwise sort before `pet_2.png`).

---

## Speech bubble widget

Separate small widget positioned above the pet. Use `Qt.Tool` + `Qt.WindowStaysOnTopHint` + `Qt.WindowTransparentForInput` so clicks pass through to the pet:

```python
class Bubble(QWidget):
    def __init__(self, text):
        super().__init__()
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        # ... paint rounded rect + triangle pointing down
```

**Pitfall:** `QFontMetrics.boundingRect(QRectF, ...)` does NOT exist in PySide6. Use the int-coord overload: `fm.boundingRect(0, 0, max_w, 1000, Qt.TextWordWrap, text)`.

---

## Persistence

```python
import json
from pathlib import Path

class AppConfig:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self._load()

    def _load(self):
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return self._default()

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def _default(self):
        return {"version": 1, "pets": [], "global_behaviors": {}}
```

Save every 5s in a `QTimer`, plus once on quit. Don't save on every property change — that's IO thrash.

---

## Autostart on Windows (registry)

```python
import winreg

REG = r"Software\Microsoft\Windows\CurrentVersion\Run"

def set_autostart(enable: bool, app_name="MyOverlay"):
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG)
    if enable:
        # Use pythonw (no console) for GUI apps
        exe = sys.executable.replace("python.exe", "pythonw.exe")
        if not Path(exe).exists():
            exe = sys.executable
        winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ,
                          f'"{exe}" "{script_path}" --minimized')
    else:
        try: winreg.DeleteValue(key, app_name)
        except FileNotFoundError: pass
    winreg.CloseKey(key)
```

`HKCU\...\Run` is user-level (no admin needed). Use `pythonw` to avoid the cmd window.

---

## Python launcher script (run.bat) pitfalls

When the project has a `run.bat` for the user to double-click, **don't just call `where pythonw`** — the user's PATH may put a hermes venv Python 3.11 first, but your deps are compiled for 3.13. Pick explicitly:

```bat
@echo off
setlocal EnableDelayedExpansion
set "PYEXE="

REM Priority 1: MS Store Python 3.13
set "CAND=%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe"
if exist "!CAND!" set "PYEXE=!CAND!"

REM Priority 2: System Python 3.13/3.12/3.11 at common paths
if "!PYEXE!"=="" (
    for %%V in (313 312 311) do (
        for %%P in ("C:\Python%%V\python.exe" "C:\Program Files\Python%%V\python.exe") do (
            if exist %%P set "PYEXE=%%~P" & goto :found
        )
    )
)

REM Priority 3: py launcher
if "!PYEXE!"=="" (
    py -3.13 -c "import sys; print(sys.executable)" >nul 2>&1 && (
        for /f "delims=" %%V in ('py -3.13 -c "import sys; print(sys.executable)"') do set "PYEXE=%%V"
    )
)

:found
start "" "!PYEXE!" "%~dp0main.py" %*
```

**Also:** if you `pip install --target D:/pylib`, set `PYTHONPATH=D:/pylib` in the bat to find the deps (this avoids the Windows long-path error from installing into MS Store Python's site-packages).

---

## Bundled resources

- **`templates/overlay_window.py`** — minimal draggable + masked overlay. Copy and customize for any "always-on-top semi-transparent" widget.
- **`templates/state_machine_pet.py`** — full pet with state machine, drag/throw, speech bubble, tray. The `桌面宠物` project's core.
- **`references/desktop-pet-case-study.md`** — concrete worked example from the actual project (multi-pet manager, persistence, settings dialog, frame animations).
- **`references/image-alpha-pipeline.md`** — white-bg→transparent + autocrop + scale, with PIL/numpy pitfalls.
- **`references/multi-monitor-and-physics.md`** — screen union, throw physics math, bounce/friction tuning.
- **`scripts/smoke_test_overlay.py`** — drop-in smoke test that spawns an overlay, runs 5s of ticks, checks `isVisible()` and `mask()` is non-empty. Run after building to catch Qt setup bugs early.