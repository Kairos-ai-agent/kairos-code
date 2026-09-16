---
name: "flask-desktop-packager"
description: "Package Flask+SQLite web apps as native Windows desktop applications using pywebview + PyInstaller. Use when the user wants to convert a Flask web app into a desktop app, build an .exe, or deploy with"
priority: 0.5
imported-from: "agents"
source-path: "C:\\Users\\you\\.agents\\skills\\software-development\\flask-desktop-packager\\SKILL.md"
---
# Flask Desktop Packager

Package a Flask web application as a native Windows desktop app using **pywebview** (native webview, no browser dependency) + **PyInstaller** (standalone .exe).

## Quick Start

### 1. Install dependencies
```bash
pip install pywebview pyinstaller
```

### 2. Create `desktop.py`
```python
import os, sys, time, threading, socket, webview

if getattr(sys, 'frozen', False):
    BUNDLE_DIR = sys._MEIPASS       # read-only: templates, static, code
    APP_DIR = os.path.dirname(sys.executable)  # writable: data, uploads, backups
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR = BUNDLE_DIR

os.chdir(APP_DIR)
sys.path.insert(0, BUNDLE_DIR)

PORT = 5000

def ensure_dirs():
    for d in ['data', 'uploads', 'backups']:
        os.makedirs(os.path.join(APP_DIR, d), exist_ok=True)

def is_port_open(port, timeout=0.5):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except (ConnectionRefusedError, OSError):
        return False

def wait_for_server(port, max_wait=30):
    """Wait for Flask to be fully ready (port open + HTTP response OK)."""
    import urllib.request
    for _ in range(int(max_wait / 0.5)):
        if is_port_open(port, timeout=0.3):
            try:
                req = urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2)
                if req.status < 500:
                    return True
            except Exception:
                pass
        time.sleep(0.5)
    return False

def start_flask():
    import config as cfg
    # Override DB path to writable location
    cfg.Config.SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(APP_DIR, "data", "app.db")}'
    first_run = not os.path.exists(os.path.join(APP_DIR, "data", "app.db"))

    from app import create_app
    app = create_app()

    # In frozen mode, templates/static are in BUNDLE_DIR
    if BUNDLE_DIR != APP_DIR:
        app.template_folder = os.path.join(BUNDLE_DIR, 'templates')
        app.static_folder = os.path.join(BUNDLE_DIR, 'static')

    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    # First-run: auto-initialize database if missing
    if first_run:
        print('First run, initializing database...')
        with app.app_context():
            from models import db
            db.create_all()
        try:
            from init_db import init_db
            init_db()
        except Exception as e:
            print(f'Init warning: {e}')

    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)

def main():
    ensure_dirs()
    if not is_port_open(PORT):
        threading.Thread(target=start_flask, daemon=True).start()
        if not wait_for_server(PORT, max_wait=30):
            print('Startup timeout'); sys.exit(1)

    window = webview.create_window(
        title='My App', url=f'http://127.0.0.1:{PORT}',
        width=1400, height=900, min_size=(1024, 600),
        resizable=True, text_select=True,
    )

    # Set window icon via Windows API (create_window has NO icon param)
    def set_icon():
        import ctypes
        user32 = ctypes.windll.user32
        ico = os.path.join(APP_DIR, 'app.ico')
        if not os.path.exists(ico):
            ico = os.path.join(BUNDLE_DIR, 'app.ico')
        if os.path.exists(ico):
            try:
                hicon_s = user32.LoadImageW(0, ico, 1, 16, 16, 0x10)
                hicon_b = user32.LoadImageW(0, ico, 1, 32, 32, 0x10)
                if not hicon_s or not hicon_b:
                    return
                # pywebview 6.x: window.native is WinForms BrowserForm
                hwnd = window.native.Handle.ToInt32()
                user32.SetClassLongPtrW(hwnd, -34, hicon_s)
                user32.SetClassLongPtrW(hwnd, -14, hicon_b)
                user32.SendMessageW(hwnd, 0x0080, 0, hicon_s)
                user32.SendMessageW(hwnd, 0x0080, 1, hicon_b)
                user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001|0x0002|0x0040)
            except Exception: pass

    def set_icon_delayed():
        """Wait for window to be fully created, then set icon."""
        for _ in range(15):
            time.sleep(0.5)
            try:
                # pywebview 6.x WinForms: use native.Handle.ToInt32()
                hwnd = window.native.Handle.ToInt32()
                if hwnd:
                    set_icon()
                    return
            except Exception: pass

    webview.start(set_icon_delayed, debug=False)

if __name__ == '__main__':
    main()
```

### 3. Create `app.spec` (PyInstaller)
```python
import os
block_cipher = None
base_dir = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    ['desktop.py'], pathex=[base_dir], binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
        ('app.ico', '.'),      # bundle icon
    ],
    hiddenimports=[
        'clr', 'webview.platforms', 'webview.platforms.winforms',
        'flask', 'flask_cors', 'flask_login', 'flask_sqlalchemy',
        'sqlalchemy', 'jinja2', 'markupsafe',
    ],
    excludes=['tkinter', 'matplotlib', 'numpy'],
    cipher=block_cipher, noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
    name='MyApp', console=True, icon='app.ico',   # <-- icon embedded in exe
    strip=False, upx=True, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, name='MyApp')
```

### 4. Build
```bash
pyinstaller app.spec --distpath dist --workpath build --clean -y
```

### 5. Post-build: restore runtime directories
```bash
mkdir -p dist/MyApp/data dist/MyApp/uploads dist/MyApp/backups
cp data/app.db dist/MyApp/data/        # copy existing DB
cp app.ico dist/MyApp/                 # copy icon to exe dir
```

## Pitfalls

### `create_window()` has NO `icon` parameter
pywebview's `create_window()` does **not** accept `icon=`. Passing it raises `TypeError: got an unexpected keyword argument 'icon'`. Use ctypes `SendMessageW` with `WM_SETICON` (0x0080) after window creation instead (see code above). The exe's embedded icon (via PyInstaller `icon=`) handles taskbar/shortcut display automatically.

**Pitfall — delayed icon set**: `window.native.Handle` may not be available immediately after `create_window()`. If you set the icon in the `webview.start()` callback directly, the handle might raise an exception. Use a delayed approach — poll for the handle in a loop with `time.sleep(0.3)` before calling `set_icon()`. Also load separate 16×16 (small) and 32×32 (big) icons, and call `SetWindowPos` after setting icons to force a taskbar refresh.

**Pitfall — old `native_handle` API removed in pywebview 6.x**: pywebview 6.x uses WinForms internally. `window.native_handle` raises `AttributeError`. Use `window.native.Handle.ToInt32()` instead. Verify with a quick test:
```python
import webview, time, threading
def check(win):
    time.sleep(2)
    print(type(win.native))  # should be BrowserForm
    hwnd = win.native.Handle.ToInt32()
    print(f'hwnd: {hwnd}')
    win.destroy()
w = webview.create_window('test', html='<h1>T</h1>')
threading.Thread(target=check, args=(w,), daemon=True).start()
webview.start()
```

### Config path must be overridden BEFORE `create_app()`
The Flask Config class evaluates `SQLALCHEMY_DATABASE_URI` at class definition time. You must override the class attribute directly:
```python
cfg.Config.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
```
Not `cfg.BASE_DIR = ...` (that won't affect the already-evaluated URI).

### `sys._MEIPASS` points to `_internal/` on modern PyInstaller
In PyInstaller >=6 with `--onedir`, `sys._MEIPASS` resolves to `<dist>/_internal/`, NOT `<dist>/`. The exe itself lives one level up. So:
- `BUNDLE_DIR = sys._MEIPASS` → `<dist>/_internal/` (templates, static, code)
- `APP_DIR = os.path.dirname(sys.executable)` → `<dist>/` (data, uploads, backups)

### Windows bat file encoding
`.bat` files with UTF-8 Chinese characters show garbled output in cmd.exe (which defaults to CP936/GBK). Add `chcp 65001 >nul 2>&1` as the **first** line, or use English-only text in bat files.

### Hiding the console window
When running a Flask+pywebview desktop app, a console window appears alongside the GUI.

**PRIMARY SOLUTION: `pythonw.exe` via .bat launcher** (most reliable, works everywhere):
```bat
@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
start "" pythonw desktop.py
```
`pythonw.exe` runs Python without creating a console window at all. The `start ""` detaches the process so the .bat file exits immediately. `daemon=True` on the Flask thread ensures it stops when the pywebview window closes.

For VBS double-click launcher:
```vbs
Set WshShell = CreateObject("WScript.Shell")
strPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strPath
WshShell.Run "pythonw.exe desktop.py", 0, False
```

**PyInstaller `console=False`** — In the `.spec` file, set `console=False` on the EXE to suppress the console in the packaged build:
```python
exe = EXE(pyz, ..., console=False, ...)
```

**Pitfalls — approaches that DO NOT work:**
- `ShowWindow(hwnd, 0)` with SW_HIDE only **minimizes** the console, does NOT hide it. The window remains visible in the taskbar.
- `FreeConsole()` from kernel32 causes Python to **crash** because `print()` and stdout become invalid after detaching from the console. Even redirecting stdout to devnull before FreeConsole() is unreliable.
- Any in-process ctypes approach is fragile. Always prefer `pythonw.exe` at the launcher level.

### VBS launcher for production
Create `启动ERP.vbs` (or `start.vbs`) at the dist root so users can double-click to launch without any terminal:
- Uses `pythonw.exe` (no console)
- Window style `0` = hidden
- `daemon=True` on Flask thread ensures cleanup when window closes
- Keep a `python desktop.py` option for debugging

### pywebview does NOT support browser file downloads
pywebview's Edge WebView2 does not handle file downloads like a real browser. ALL of these approaches FAIL silently or crash:
- `fetch()` + `blob` + `<a download>` click — blob download does nothing
- `window.location.href = '/endpoint.pdf'` — PDF renders as raw bytes or blank page
- `<a href="/file.pdf" target="_blank">` — new window opens but no download
- `Content-Disposition: attachment` header — ignored by pywebview

**WORKAROUND: server-side save + `os.startfile()`**

Generate the file (PDF, Excel, etc.) server-side in Flask, save to a known directory (e.g. `backups/`), then open it with the OS default handler:

```python
import os, subprocess
from flask import jsonify

@app.route('/api/export/<int:id>')
def export(id):
    # 1. Generate file
    buf = generate_pdf(id)  # or generate_excel(id)
    # 2. Save to disk
    path = os.path.join('backups', f'export_{id}.pdf')
    with open(path, 'wb') as f:
        f.write(buf.getvalue())
    # 3. Open with default system viewer
    try:
        os.startfile(os.path.abspath(path))  # Windows
    except Exception:
        subprocess.Popen(['cmd', '/c', 'start', '', os.path.abspath(path)], shell=True)
    # 4. Return JSON (not the file)
    return jsonify({'success': True, 'path': path})
```

Frontend calls this via `fetch()`, gets JSON back, shows a toast. The PDF/Excel opens in the system's default viewer (Adobe, WPS, etc.).

**Pitfall**: Do NOT return the file as `application/octet-stream` or `application/pdf` — pywebview will try to render it inline, not download it. Always return JSON and handle the file opening server-side.

### Health check: verify HTTP response, not just port
A port-open check (`socket.create_connection`) only confirms the TCP port is listening — it does NOT confirm Flask is ready to serve requests. The webview may connect before Flask finishes initializing (importing models, creating tables, etc.), causing `ERR_CONNECTION_REFUSED`.

Use `wait_for_server()` which checks both port + HTTP response:

```python
def wait_for_server(port, max_wait=30):
    import urllib.request
    for _ in range(int(max_wait / 0.5)):
        if is_port_open(port, timeout=0.3):
            try:
                req = urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2)
                if req.status < 500:
                    return True
            except Exception:
                pass
        time.sleep(0.5)
    return False
```

**Pitfall**: The default Flask route `/` must return HTTP 200. If your app redirects `/` to a login page, check that the redirect returns 302 (which is < 500, so it passes). If you have no route at `/`, add a health check endpoint or use an existing lightweight route.

### Chinese PDF generation
For Chinese content, **fpdf2** is the most reliable option (pure Python, no external deps):
- `html2pdf.js` (html2canvas + jsPDF): fails badly on Chinese — renders only partial content, missing tables/text
- `xhtml2pdf`: requires `cairo` native library which is hard to install on Windows
- `fpdf2`: works with `C:\\Windows\\Fonts\\simsun.ttc` (宋体) and `simhei.ttf` (黑体), no external deps

```python
from fpdf import FPDF
pdf = FPDF()
pdf.add_font('song', '', r'C:\\Windows\\Fonts\\simsun.ttc')
pdf.add_font('hei', '', r'C:\\Windows\\Fonts\\simhei.ttf')
pdf.add_page()
pdf.set_font('hei', size=18)
pdf.cell(0, 12, '采  购  合  同', align='C', new_x='LMARGIN', new_y='NEXT')
# ... build PDF programmatically
pdf.output(buf)
```

#### fpdf2 Chinese text layout pitfalls

**Measure text width before rendering.** Chinese fonts are much wider than Latin fonts. SimSun size 10 ≈ 3.5mm per character. Always verify:
```python
pdf.set_font('song', size=10)
w = pdf.get_string_width(text)  # e.g. 195.8mm
# A4 usable width = 210 - left_margin - right_margin (e.g. 190mm)
# If w > usable_width, reduce font size or shorten text
```

**Do NOT use `set_x()` + `multi_cell()` together.** This combination causes broken line wrapping for Chinese text — each word/segment gets split onto separate lines. Instead use `multi_cell()` with explicit width and no `set_x()`:
```python
# BAD — causes broken wrapping:
pdf.set_x(15)
pdf.multi_cell(pw - 15, 5.5, text)

# GOOD — use explicit width parameter:
pdf.multi_cell(w=pw, h=5, text=text, border=0)
```

**Table column widths must fit A4.** A4 = 210mm. With 10mm margins each side, usable width = 190mm. Sum of all column widths must be ≤ 190mm:
```python
col_w = [12, 38, 28, 14, 20, 24, 26, 20]  # sum = 182mm ≤ 190mm ✓
```

**Image layer ordering**: fpdf2 renders elements in draw order — later elements appear on top. To place a stamp/seal image behind text and borders, draw the image FIRST, then draw borders and text on top. See `references/contract-pdf-patterns.md` for the full pattern with code example.

### Print orientation in pywebview (WebView2): @page CSS in iframes does NOT work

`window.print()` in pywebview's WebView2 uses Edge's system print dialog. **However, `@page { size: landscape; }` CSS placed in an iframe is IGNORED by WebView2's print dialog.** The @page rule must be in the **MAIN document's `<head>`** for the print dialog to recognize orientation.

**DO NOT** use an iframe approach for printing:
```javascript
// ❌ WRONG — @page in iframe is ignored by WebView2
const iframe = document.createElement('iframe');
iframe.contentDocument.write(printHtml);  // @page in here → ignored
iframe.contentWindow.print();
```

**Correct pattern — main document replacement:**

```javascript
var _savedBody = null;
var _savedPageStyle = null;

function showPrintContent(dl, orient) {
  // 1. Save current page content
  if (_savedBody === null) {
    _savedBody = document.body.innerHTML;
  }
  // 2. Add @page style to MAIN document's <head>
  var printStyle = document.createElement('style');
  printStyle.id = '__print_style';
  printStyle.textContent = '@page { margin: 0; size: ' + orient + '; }' +
    '@media print { body { padding: 10px 15px; } }';
  document.head.appendChild(printStyle);
  _savedPageStyle = printStyle;
  // 3. Replace body with print content
  document.body.innerHTML = '<div>... print content ...</div>';
  // 4. Print from MAIN window (not iframe.contentWindow)
  setTimeout(function() { window.print(); }, 100);
}

function closePrint() {
  // 5. Restore original content
  if (_savedBody !== null) {
    document.body.innerHTML = _savedBody;
    _savedBody = null;
  }
  if (_savedPageStyle) {
    _savedPageStyle.remove();
    _savedPageStyle = null;
  }
}
```

**Key points:**
- `@page` MUST be in `document.head` of the main window, NOT in an iframe's document
- Call `window.print()` on the main window, not `iframe.contentWindow.print()`
- The `size` keyword alone works: `size: landscape` (without specifying paper size `A4 landscape`) is more reliable
- Save and restore `document.body.innerHTML` so the ERP UI is preserved
- A 100ms `setTimeout` before `window.print()` gives the DOM time to render
- The main-window approach also works with the WebView2 print dialog's orientation control — the dialog pre-selects the orientation matching @page

**Pitfall — `document.body.innerHTML` destroys JS event listeners**: After restoring `document.body.innerHTML`, all JavaScript event listeners, React/Vue state, and data bindings are lost. The page effectively reloads. If the user's workflow involves unsaved state (forms, modal inputs), warn them or auto-save before printing. For simple CRUD ERP pages, the reload is acceptable because the next user action (clicking a button) triggers a fresh page load via the SPA router.

**Pitfall — `cssText.includes()` string matching can fail**: When detecting iframes by `style.cssText`, property order and formatting may differ between browsers. In WebView2, `element.style.cssText = 'position:fixed;z-index:9999;...'` returns properties in the same order they were set. Use `indexOf` (IE-compatible) instead of `includes` if supporting older WebView2 builds.

### pywebview 6.x uses WinForms on Windows (NOT EdgeChromium directly)
pywebview 6.x wraps the webview inside a **WinForms `BrowserForm`**. This means:
- `window.native_handle` **does NOT exist** (AttributeError). The attribute was removed.
- The correct way to get the HWND is: `window.native.Handle.ToInt32()` — `window.native` is the WinForms `BrowserForm` instance, and `.Handle` is the WinForms `IntPtr` handle.
- Ensure `webview.platforms.winforms` is in hiddenimports (not `edgechromium`).

**Full icon-setting pattern for pywebview 6.x:**
```python
def set_icon():
    import ctypes
    user32 = ctypes.windll.user32
    icon_path = os.path.join(APP_DIR, 'app.ico')
    if not os.path.exists(icon_path):
        icon_path = os.path.join(BUNDLE_DIR, 'app.ico')
    if not os.path.exists(icon_path):
        return
    try:
        hicon_s = user32.LoadImageW(0, icon_path, 1, 16, 16, 0x10)
        hicon_b = user32.LoadImageW(0, icon_path, 1, 32, 32, 0x10)
        if not hicon_s or not hicon_b:
            return
        # pywebview 6.x: native is WinForms BrowserForm
        hwnd = window.native.Handle.ToInt32()
        user32.SetClassLongPtrW(hwnd, -34, hicon_s)  # GCLP_HICONSM
        user32.SetClassLongPtrW(hwnd, -14, hicon_b)  # GCLP_HICON
        user32.SendMessageW(hwnd, 0x0080, 0, hicon_s)
        user32.SendMessageW(hwnd, 0x0080, 1, hicon_b)
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001|0x0002|0x0040)
    except Exception:
        pass
```

**Verified**: `window.native` returns `<class 'webview.platforms.winforms.BrowserView.BrowserForm'>`. Calling `.Handle.ToInt32()` returns the valid HWND. `LoadImageW` + `SetClassLongPtrW` + `SendMessage WM_SETICON` successfully sets both title bar and taskbar icons.

## Directory Structure (post-build)

See `references/contract-pdf-patterns.md` for a complete contract PDF generation example (fpdf2 route + frontend + measurements).
```
dist/MyApp/
├── MyApp.exe           ← double-click to run
├── app.ico             ← window icon (also copied here)
├── _internal/          ← PyInstaller bundle (read-only)
│   ├── templates/      ← Flask templates
│   ├── static/         ← Flask static files
│   └── app.ico         ← bundled icon
├── data/               ← SQLite DB (writable)
├── uploads/            ← user uploads (writable)
└── backups/            ← export files (writable)
```

## Icon Generation from PNG

### Auto-crop approach (numpy — fast and reliable)
```python
from PIL import Image
import numpy as np

img = Image.open('logo.png').convert('RGBA')
arr = np.array(img)
# Find non-white pixels (RGB < 250 or alpha > 5)
mask = (arr[:,:,3] > 5) & ((arr[:,:,0] < 250) | (arr[:,:,1] < 250) | (arr[:,:,2] < 250))
rows = np.any(mask, axis=1)
cols = np.any(mask, axis=0)
rmin, rmax = np.where(rows)[0][[0, -1]]
cmin, cmax = np.where(cols)[0][[0, -1]]
cropped = img.crop((cmin, rmin, cmax+1, rmax+1))

# Pad to square, logo fills ~95% of icon
def make_icon(src, size):
    bg = Image.new('RGBA', (size, size), (255, 255, 255, 255))
    ratio = min((size * 0.95) / src.width, (size * 0.95) / src.height)
    nw, nh = int(src.width * ratio), int(src.height * ratio)
    resized = src.resize((nw, nh), Image.LANCZOS)
    bg.paste(resized, ((size - nw) // 2, (size - nh) // 2), resized)
    return bg

sizes = [16, 32, 48, 64, 128, 256]
icons = [make_icon(cropped, s) for s in sizes]
icons[-1].save('app.ico', format='ICO', sizes=[(s, s) for s in sizes])
```

**Pitfall**: Source logos often have 40-60% white padding. Without auto-crop, the logo appears tiny in the icon (e.g., 149×132 content in a 309×272 canvas = only 23% fill). Always auto-crop before creating ICO.

**Pitfall — Windows caches old icons**: After replacing an `.ico` file, Windows may still show the old icon in taskbar/title bar. Clear the icon cache:
```bat
taskkill /f /im explorer.exe
del "%LOCALAPPDATA%\IconCache.db"
del "%LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache*"
del "%LOCALAPPDATA%\Microsoft\Windows\Explorer\thumbcache*"
start explorer.exe
```
Also re-create any `.lnk` shortcuts (they embed the icon path at creation time).

**Pitfall — single-size ICO fails LoadImageW**: An ICO file with only 256×256 will cause `LoadImageW` to return 0 (failure) when requesting 16×16 or 32×32. Windows needs multiple sizes embedded. Always save ICO with explicit sizes:
```python
img.save('app.ico', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
# Verify: LoadImageW should return non-zero
h = user32.LoadImageW(0, 'app.ico', 1, 32, 32, 0x10)
assert h != 0, "ICO missing 32x32 size"
```
