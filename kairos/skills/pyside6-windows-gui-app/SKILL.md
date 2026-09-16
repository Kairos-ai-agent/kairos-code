---
name: "pyside6-windows-gui-app"
description: "Build a Windows desktop GUI app with PySide6 — transparent frameless windows with click-through masks, system tray, multi-monitor, mouse-drag state machines, and a smart .bat launcher that handles mul"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/.archive/pyside6-windows-gui-app/SKILL.md"
---
# PySide6 Windows GUI App

Class: building a **native Qt desktop application** for Windows with PySide6 — transparent frameless widgets (desktop pets, widgets, HUDs), system tray apps, or any "sits on the desktop alongside other apps" tool. Distinct from PyInstaller-packaged Flask/WebView stacks (different skill).

## When to load this skill

- User wants a "desktop pet", "floating widget", "always-on-top HUD", or any custom Qt window that lives on the desktop
- Multi-monitor support is needed (screen bounds, drag-across-monitors)
- App needs a Windows system tray icon (QSystemTrayIcon)
- App needs click-through on transparent regions (only the widget body catches mouse)
- User reports "闪退" / "garbled text" / "won't start" — usually a launcher or Python-version problem, not a Qt problem
- Building a launcher `.bat` for a Python GUI app on a machine with multiple Python installs

## Core architecture (transparent-widget pattern)

The single most useful pattern in this skill: a transparent frameless window where ONLY the opaque pixels are clickable, and the rest is fully click-through to whatever's behind.

```python
from PySide6.QtCore import Qt
from PySide6.QtGui import QBitmap, QImage
from PySide6.QtWidgets import QWidget

class PetWindow(QWidget):
    def __init__(self, image_path):
        super().__init__()
        # NOTE: do NOT set Qt.WindowTransparentForInput — it conflicts with setMask
        # (see Pitfalls). WA_TranslucentBackground is enough.
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._pixmap = QPixmap(image_path)
        self.resize(self._pixmap.size())
        # Build mask from alpha channel — only opaque pixels catch mouse events
        self._rebuild_mask()

    def _rebuild_mask(self):
        img = self._pixmap.toImage().convertToFormat(QImage.Format_Alpha8)
        self.setMask(QBitmap.fromImage(img))
```

Key insights from this session:
- `setMask(QBitmap.fromImage(alpha_image))` is enough — no `WindowTransparentForInput` flag needed.
- The mask is per-window-position; rebuild it after `resize()` if the widget resizes.
- Multi-monitor: `QGuiApplication.screens()` + union of `availableGeometry()` rects = the full virtual desktop rect.

## Multi-monitor + drag-clamp pattern

Always clamp positions to screen bounds during drag, not after:

```python
def _apply_position(self):
    s = self._screen
    w, h = self.width(), self.height()
    min_x = s.left + w/2; max_x = s.right - w/2
    min_y = s.top + h;    max_y = s.bottom
    self._state.x = max(min_x, min(max_x, self._state.x))
    self._state.y = max(min_y, min(max_y, self._state.y))
    self.move(int(self._state.x - w/2), int(self._state.y - h))
```

The "feet at bottom" convention: store `_state.y` as the bottom-center of the widget. Then `widget_y = state_y - height`. This makes "drop to the floor" and "stand on the screen bottom" trivially correct.

## System tray + QMenu

```python
tray = QSystemTrayIcon()
tray.setIcon(make_icon())  # QIcon from QPixmap
tray.setToolTip("App Name")
menu = QMenu()
menu.addAction("Action 1", handler1)
menu.addSeparator()
menu.addAction("Quit", app.quit)
tray.setContextMenu(menu)
tray.show()
```

**Critical**: do NOT show a blocking `QMessageBox.warning()` on `not QSystemTrayIcon.isSystemTrayAvailable()` — that blocks startup until the user clicks OK, and on a server/headless env it hangs forever. Just log to stderr and continue.

## State machine for behaviors (idle / walk / drag / throw)

For a desktop pet or any animated widget that responds to user input, separate the **state machine** from the **rendering**:

- `behaviors.py` (pure logic): `State` enum, `PetState` dataclass, step functions, physics. No Qt imports. Trivially unit-testable.
- `pet_window.py` (Qt): holds a `PetState`, calls `_tick()` every 33ms from a `QTimer`, advances state, calls `_apply_position()`.

This separation is what lets you write integration tests that run the state machine without spinning up Qt.

For drag → throw physics:
```python
# In mouseMoveEvent: record (t, x, y) into a small ring buffer
# In mouseReleaseEvent: if speed > threshold, compute velocity, enter THROW state
# THROW state: apply gravity, bounce off screen edges with damping, settle when slow
```

## Silent-crash debugging pattern (essential)

A PySide6 app that crashes during startup with no console = invisible to the user. **Always** install a global exception hook that shows a `QMessageBox`:

```python
import sys, traceback
from PySide6.QtWidgets import QMessageBox

def _excepthook(exc_type, exc_value, exc_tb):
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print(text, file=sys.stderr)
    try:
        QMessageBox.critical(None, "App - Error", text)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = _excepthook
```

Also wrap the `PySide6` import itself — that's where version mismatch / missing DLL errors show up:

```python
try:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
except Exception as e:
    msg = f"Failed to load PySide6.\n\n{type(e).__name__}: {e}\n\n" \
          "Possible cause: dependencies do not match this Python version."
    print(msg, file=sys.stderr)
    try:
        from PySide6.QtWidgets import QApplication as _QA, QMessageBox
        _qapp = _QA.instance() or _QA(sys.argv)
        QMessageBox.critical(None, "App", msg)
    except Exception: pass
    sys.exit(1)
```

Without this, "闪退" means "something went wrong, good luck figuring out what".

## THE LAUNCHER .BAT — the part that takes 80% of debugging time

This is the single biggest source of "闪退 + 乱码" reports on Windows. The user has `python` on PATH pointing to *some* Python (often hermes venv's 3.11), but they actually develop against MS Store Python 3.13. pip --target-installed numpy is version-specific (cp311 vs cp313 wheel) and will NOT load cross-version.

**The pattern that works** (English output only, smart Python detection, uses pythonw.exe):

```bat
@echo off
setlocal EnableDelayedExpansion

set "PYEXE="
set "PYMINOR="
set "TMPCFG=%TEMP%\pyver_detect_%RANDOM%.txt"

REM Test candidates in priority order. Inline (NO subroutines — label/return
REM parsing is unreliable across cmd.exe versions).
if not exist "%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe" goto :try_pf
del "%TMPCFG%" 2>nul
"%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe" -c "import sys; print(sys.version_info.major, sys.version_info.minor)" > "%TMPCFG%" 2>nul
for /f "tokens=2" %%V in ('type "%TMPCFG%" 2^>nul') do set "PYMINOR=%%V"
if defined PYMINOR if !PYMINOR! GEQ 10 set "PYEXE=%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe"
if defined PYEXE goto :have_python

:try_pf
REM ... try Program Files paths, then PATH (skipping "hermes" venv) ...

:have_python
if exist "%TMPCFG%" del "%TMPCFG%" > nul 2>&1
if not defined PYEXE ( echo [ERROR] No Python 3.10+ found. & pause & exit /b 1 )

REM Match lib dir to detected Python version
if "!PYMINOR!"=="13" set "PYTHONPATH=%~dp0vendor;%~dp0lib;D:/pylib"
if "!PYMINOR!"=="11" set "PYTHONPATH=%~dp0vendor;%~dp0lib;D:/pylib311"

REM Derive pythonw.exe (windowless Python) — python.exe will BLOCK cmd.exe via start
set "PYWEXE=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYWEXE%" set "PYWEXE=%PYEXE%"   REM fallback

echo Using Python: %PYEXE% (3.%PYMINOR%)
start "" /B "%PYWEXE%" "%~dp0main.py" %*
endlocal
exit /b 0
```

**Hard-won lessons in this launcher:**

1. **English output only.** `chcp 65001` does NOT reliably fix encoding for `echo` in cmd.exe's default rendering. Just don't put Chinese in the bat.

2. **`start ""` with `python.exe` BLOCKS cmd.exe** because python.exe has a console and start inherits it. Use `pythonw.exe` (windowless) for GUI apps — then `start ""` returns instantly.

3. **`for /f` cannot capture MS Store python.exe output directly** (the launcher is a WindowsApps symlink with weird process attributes). Capture to a temp file first:
   ```bat
   "%PYEXE%" -c "..." > "%TMPCFG%" 2>nul
   for /f "tokens=2" %%V in ('type "%TMPCFG%" 2^>nul') do set "PYMINOR=%%V"
   ```

4. **No subroutines + no early `goto :label` from inside sub-blocks.** cmd.exe parses labels inconsistently. Keep the detection inline with `goto :have_python` jumps between sections.

5. **Skip `hermes` in PATH python lookup.** Users often have a hermes-agent venv at `%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe` that's first on PATH. It has different packages. Filter it:
   ```bat
   for /f "delims=" %%P in ('where python.exe 2^>nul') do (
       if not defined PYEXE (
           echo "%%P" | findstr /I "hermes" > nul 2>&1
           if errorlevel 1 ( REM not hermes, use it )
       )
   )
   ```

6. **pip install numpy/PySide6 must go to per-version directories.** MS Store Python's site-packages path is long (triggers Win32 long-path error). Use short paths:
   - Python 3.13: `python3 -m pip install PySide6 Pillow numpy --target D:/pylib`
   - Python 3.11: `python -m pip install PySide6 Pillow numpy --target D:/pylib311`
   - Pillow is mostly abi3-compatible; numpy is the strict version-pinned one.

7. **In your Python `main.py`, select lib dir at runtime by `sys.version_info`:**
   ```python
   _PYLIB_DIR = "D:/pylib311" if sys.version_info[:2] == (3, 11) else "D:/pylib"
   _EXTRA_PATHS = [_PYLIB_DIR, ...]
   for p in _EXTRA_PATHS:
       if p and Path(p).exists() and p not in sys.path:
           sys.path.insert(0, p)
   ```
   **Don't add BOTH pylib paths unconditionally** — the last-inserted ends up at the FRONT of sys.path and Python will try to load the wrong-version numpy first.

## Verification checklist (run after building)

After launching the app via run.bat, verify it's actually alive:

```bash
# 1. App process exists with the tray icon window title
tasklist /v /FO CSV | grep QTrayIconMessageWindow

# 2. Config file got written (your 5-sec save timer proves the event loop is running)
ls -la config.json && stat -c %Y config.json  # mtime should be within last few seconds

# 3. No Python traceback in stderr (if you launched from a console that captures it)
```

## Pitfalls (the bugs that took hours to find)

| Pitfall | Symptom | Fix |
|---|---|---|
| `setWindowFlag(Qt.WindowTransparentForInput, False)` AFTER `show()` | Widget silently disappears | Set the flag once in `__init__`, never again — or just don't use this flag (use `setMask` instead) |
| `PIL.Image.fromarray(arr_int16, "RGBA")` | `TypeError: Cannot handle this data type` | Convert to `uint8` first: `out = arr.astype(np.uint8)` |
| `QFontMetrics.boundingRect(QRectF, flag, text)` | `TypeError: called with wrong argument types` in PySide6 | Use int coords: `boundingRect(x, y, w, h, flag, text)` |
| Multiple `for /f "tokens=2" %%V in ('cmd ...')` inside `if (...)` blocks | PYMINOR ends up empty despite successful detection | Use the temp-file workaround shown above |
| MS Store Python `python -m pip install --target <very-long-path>` | `PermissionError` / long-path error | Install to `D:/pylib` (short path) and add to `PYTHONPATH` |
| Launching `python.exe main.py` from `start ""` in a .bat | cmd.exe never returns — `start` waits for the console child | Use `pythonw.exe` for GUI apps |
| `QMessageBox.warning()` when tray unavailable | Blocks startup until user clicks OK (headless = forever) | Just `print(..., file=sys.stderr)` and continue |

## Bundled resources

- `templates/run.bat` — the smart-launcher template above, ready to drop in
- `templates/main.py` — minimal PySide6 main.py with the exception hook + per-version lib-dir selection
- `references/transparent-widget-pattern.md` — full Qt code for the click-through transparent window + drag + state machine

## Overlay widgets (透明浮窗 / desktop pets / floating tools)

A common subclass of PySide6 desktop apps is the **always-on-top transparent frameless widget** that sits on the desktop like an overlay — desktop pets, floating notes, sticky widgets, Pomodoro overlays, screen watermarks, screenshot tools, recording widgets. The umbrella's `transparent-widget-pattern.md` covers the core `setMask` + click-through pattern; this section catalogs the recurring behavior/tuning recipes.

### When this is the right shape (vs. main-window apps)

If the user asks for any of:
- 桌面宠物 / desktop pet / mascot / floating companion
- 悬浮便签 / sticky note / floating note
- 桌面时钟 / floating clock / 番茄钟 / Pomodoro overlay
- 屏幕水印 / screen watermark / HUD
- 截图工具浮窗 / screenshot widget / quick-capture
- 屏幕录制器浮窗 / screen recorder widget
- 任何"始终在最上层、可拖动、半透明"的桌面 widget

…use the `overlay_window.py` starter below, NOT the `main.py` run.bat pattern.

### Standing toolkit (always reuse)

- `templates/overlay_window.py` — minimal draggable + masked overlay. Copy and customize for any always-on-top semi-transparent widget. Pairs with the `setMask(QBitmap.fromImage(alpha))` pattern from `references/transparent-widget-pattern.md`.
- `templates/overlay_state_machine_pet.py` — full pet with state machine (idle / walk / drag / throw), drag/throw physics, speech bubble, tray icon. Use as the reference impl when the widget has autonomous behaviors.
- `scripts/smoke_test_overlay.py` — drop-in smoke test that spawns an overlay, runs 5s of ticks, checks `isVisible()` and `mask()` is non-empty. Run after building to catch Qt setup bugs early.

### Domain recipes

- `references/overlay-desktop-pet-case-study.md` — concrete worked example (multi-pet manager, persistence, settings dialog, frame animations). The actual 桌面宠物 project's core.
- `references/overlay-image-alpha-pipeline.md` — white-bg → transparent + autocrop + scale, with PIL/numpy pitfalls (`PIL.Image.fromarray(int16) → TypeError`, use `uint8`).
- `references/overlay-multi-monitor-and-physics.md` — screen union via `QGuiApplication.screens()` (NOT `primaryScreen`), throw physics math (200ms sample window for velocity), bounce/friction tuning.

### Common overlays pitfall table

| Pitfall | Symptom | Fix |
|---|---|---|
| `Qt.WindowTransparentForInput` + `setMask()` | Click-through doesn't actually click on opaque pixels | Don't combine them. Use `setMask` alone. |
| `setWindowFlag(...)` AFTER `show()` | Widget silently re-shows / flickers | Set all flags in `__init__`, never modify again at runtime. |
| `Tool` flag | Wants taskbar entry | Use `SplashScreen` instead. |
| Edit resets mask after resize | Click-through stops working | Call `_build_mask()` again after any size change. |
| `int16` to `Image.fromarray(... "RGBA")` | `TypeError: Cannot handle this data type` | Convert to `uint8` first. |
| `QFontMetrics.boundingRect(QRectF, ...)` in PySide6 | `TypeError: called with wrong argument types` | Use int coords: `boundingRect(x, y, w, h, flag, text)`. |
| Frame-number regex without integer sort | `pet_10.png` sorts before `pet_2.png` | Sort by parsed int frame number, not lex. |
| Tray-only app + closing last window | App quits before user can re-show | `app.setQuitOnLastWindowClosed(False)`. |
| `QMessageBox.warning()` when tray unavailable | Blocks startup (headless = forever) | Log to stderr + continue. |

## Absorbed skills

This umbrella subsumes the previously-separate `pyside6-overlay-window` skill (now in `.archive/`). The absorbed content is preserved in the "Overlay widgets" section above plus these files:
- `templates/overlay_window.py`, `templates/overlay_state_machine_pet.py` — starter templates
- `scripts/smoke_test_overlay.py` — verification script
- `references/overlay-*.md` — domain references (multi-monitor, image alpha, pet case study)

If you find yourself loading the archived SKILL.md for anything not already covered by this umbrella, patch this umbrella instead — that's the job-to-be-done.