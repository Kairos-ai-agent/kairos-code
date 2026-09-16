---
name: "pyinstaller-desktop-packaging"
description: "Build and distribute Windows desktop apps with PyInstaller — Flask/FastAPI backend (uvicorn) + WebView or browser, bundled into a single .exe. Covers onefile vs onedir tradeoffs, the COLLECT wipes-dat"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\software-development\\pyinstaller-desktop-packaging\\SKILL.md"
---
# PyInstaller Desktop App Packaging

Class: building **desktop applications** with PyInstaller — a Python backend (Flask/FastAPI) bundled with a web UI (WebView or browser), distributed as a single `.exe` to non-technical Windows users.

Covers the full lifecycle: spec authoring, asset vendoring, data dir design, build, and post-build data preservation.

---

## When to load this skill

- User wants to convert a Flask/FastAPI backend + WebView frontend into a Windows `.exe`
- Choosing between **onefile** (single `.exe`) and **onedir** (`.exe` + `_internal/` folder) packaging
- "Can you package everything into one file?" / "做成单文件" / "封装到主 exe"
- PyInstaller build wipes a folder containing real data
- Frontend HTML loads CSS/JS from a public CDN and that fails in the target region
- A rebuild needs to preserve user data in the dist folder
- Windowed EXE (no console) launches but exits silently → suspect the uvicorn `isatty()` crash
- Patched a Python file with `patch()`, the build still fails, and `ast.parse()` errors out — start with the patch-tool pitfall section
- **Tkinter / PySide6 app with no HTTP backend** — there is no port to `curl`; verify with a `--selftest` exit-code contract → "GUI-only apps: verify with `--selftest`" and `references/gui-selftest-and-headless-testing.md`
- Rebuild dies at the last step with `PermissionError [WinError 5]` (or at the first step with `[WinError 32]`) on `dist/<name>.exe` → a running instance holds the file lock; the rule applies to ANY onefile exe, not just FastAPI (see `references/fastapi-spa-shadow-and-windows-filelock.md`)
- A frozen GUI/tool app "starts but a feature silently does nothing" → suspect relative-path data files and `.env`/config that no longer resolve after freezing (see the spec-fields and writable-dir sections)

---

## Critical pitfall — PyInstaller COLLECT wipes target folder

**Symptom:** You rebuild with `pyinstaller spec.spec --distpath dist/赛光智能ERP_v2`, and PyInstaller's `COLLECT` step runs `Removing dir dist/赛光智能ERP_v2` first. Any user data (`data/sg_erp.db`, `uploads/`, `backups/`) inside that folder is **silently deleted**.

**Mandatory workflow before every rebuild:**

1. **Back up the data folder** from the dist target:
   ```bash
   cp -r dist/赛光智能ERP_v2/data /tmp/data_backup
   cp -r dist/赛光智能ERP_v2/uploads /tmp/uploads_backup
   cp -r dist/赛光智能ERP_v2/backups /tmp/backups_backup
   ```
2. Run pyinstaller (onefile or onedir, doesn't matter — both wipe).
3. **Restore data + side folders** after build:
   ```bash
   mkdir -p dist/赛光智能ERP_v2/data
   cp /tmp/data_backup/* dist/赛光智能ERP_v2/data/
   cp -r dist/SG_ERP/uploads dist/赛光智能ERP_v2/  # or wherever the templates live
   cp -r dist/SG_ERP/backups dist/赛光智能ERP_v2/
   cp dist/SG_ERP/sg_erp.ico dist/赛光智能ERP_v2/
   ```
4. For onefile mode, also delete the stale `_internal/` folder left over from the previous onedir build.

Or use the bundled helper script (see "Bundled resources" below) which does both halves:
```bash
# Before build
python scripts/backup_dist.py dist/赛光智能ERP_v2

# ... run pyinstaller ...

# After build
python scripts/backup_dist.py dist/赛光智能ERP_v2 --restore-from /tmp/pyinstaller_prebuild_赛光智能ERP_v2_<ts>
```

**Why this happens:** COLLECT is the onedir packing step. It cleans its output dir before writing. If your dist output dir happens to be the user's install dir, their data dies with it.

**Recovery if you forgot:** the previous build's `_internal/` is gone, but data often survives in `dist/<other-name>/data/` if you've ever built with a different spec name. Otherwise, the user is restoring from backups — they will be angry.

---

## Onefile vs onedir decision

| | onedir (default) | onefile |
|--|--|--|
| Output | `app.exe` + `_internal/` folder | single `app.exe` (~60-150MB) |
| Startup | instant | 1-3s self-extract to `%TEMP%\_MEIxxxx` |
| Asset access | `sys._MEIPASS` points at `_internal/` | same, just transparent |
| Distribute | zip the whole `app/` folder | copy one file |

**Pick onefile when:** the user wants "just one exe", the app is internal/limited-distribution, and 1-3s startup is acceptable.
**Pick onedir when:** startup speed matters, debugging is ongoing, or you want hot-swappable assets.

**Spec difference:**
- onedir uses both `EXE(...)` and `COLLECT(exe, a.binaries, a.zipfiles, a.datas, name=...)`
- onefile uses only `EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, ..., name=...)` — no COLLECT

---

## Read-only vs writable split

The `.exe` (and its `_internal/` if onedir) is **read-only at runtime** — PyInstaller unpacks to a temp folder that's not meant for user writes. Any user data MUST live next to the `.exe`.

**Standard layout:**
```
MyApp/
├── MyApp.exe               # read-only at runtime (extracted to temp)
├── app.ico                 # optional, used by Windows shell
├── data/                   # writable: SQLite DB, config
├── uploads/                # writable: user files
└── backups/                # writable: backups
```

**Pattern in code (desktop.py / launcher):**
```python
if getattr(sys, 'frozen', False):
    BUNDLE_DIR = sys._MEIPASS              # read-only, has templates/static
    APP_DIR = os.path.dirname(sys.executable)  # writable, next to exe
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR = BUNDLE_DIR

# config: app.template_folder = os.path.join(BUNDLE_DIR, 'templates')
# db path: sqlite:///{APP_DIR}/data/app.db
```

Any Flask route that writes (DB writes, file uploads, exports) must use APP_DIR paths. Templates, static assets, Python source — BUNDLE_DIR.

**Same rule for `.env` / side config, and `load_dotenv()` does NOT do it for you.** After
freezing, `load_dotenv()` walks up from the caller's file — which lives in the temp
`_MEIPASS` directory — so it never finds the project's `.env`. The exe starts fine and any
feature behind that key silently falls back to a degraded path (invisible with
`console=False`). Load candidates explicitly, in priority order:

```python
candidates = []
if getattr(sys, 'frozen', False):
    candidates.append(Path(sys.executable).parent / '.env')   # next to the exe — document this
candidates.append(Path.cwd() / '.env')
appdata = os.getenv('APPDATA')
if appdata:
    candidates.append(Path(appdata) / 'AppName' / '.env')     # per-user, survives moving the exe
candidates.append(Path(__file__).resolve().parent.parent / '.env')  # dev checkout
for cand in candidates:
    if cand.is_file():
        load_dotenv(cand, override=False); break
```

Read the values **after** this runs (build config objects inside `main()`, not at import time
before the loader), and tell the user in the README where to drop the file — otherwise the
deployed app quietly loses the feature when it is copied to another machine.

---

## Vendor CDN assets for offline / China distribution

**Symptom:** HTML loads `https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/...`. User in China: CDN blocked → no CSS → entire page is unstyled/messy.

**Fix:** download assets to `static/`, change templates to local paths.

```python
import urllib.request, os
UA = 'Mozilla/5.0'
SOURCES = {
    'bootstrap.min.css': [
        'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
        'https://unpkg.com/bootstrap@5.3.3/dist/css/bootstrap.min.css',
        'https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.min.css',
        'https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/css/bootstrap.min.css',  # China mirror
    ],
    'bootstrap-icons.min.css': [
        'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css',
        # ... fallbacks
    ],
}
# Download with fallback chain — agent's host usually has CDN access even when user's browser doesn't
for fname, urls in SOURCES.items():
    for url in urls:
        try:
            urllib.request.urlretrieve(url, f'static/css/{fname}')
            break
        except: continue

# bootstrap-icons references fonts/ via relative URL — keep the same relative layout
urllib.request.urlretrieve(
    'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff2',
    'static/css/fonts/bootstrap-icons.woff2'
)
# bootstrap.bundle.min.js too
urllib.request.urlretrieve(
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js',
    'static/js/bootstrap.bundle.min.js'
)
```

**Template changes:**
```html
<!-- before -->
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>

<!-- after -->
<link href="/static/css/bootstrap.min.css" rel="stylesheet">
<link href="/static/css/bootstrap-icons.min.css" rel="stylesheet">
<script src="/static/js/bootstrap.bundle.min.js"></script>
```

**After build, also patch the bundled `_internal/templates/*.html`** — PyInstaller's COLLECT step copies templates into `_internal/`, but the patches you've made to source `templates/` don't propagate automatically. Same for `static/` (it DOES propagate via the `datas` spec, but verify with a curl test).

---

## Spec file naming for non-default output paths

If the user has a custom exe name like `赛光智能ERP_v2` instead of the spec default, the build will go to `dist/<spec_name>/`. Two-step pattern:

```bash
cp sg_erp.spec sg_erp.spec.bak
sed -i "s/name='赛光智能ERP',/name='赛光智能ERP_v2',/g" sg_erp.spec
pyinstaller sg_erp.spec --distpath dist --workpath build --clean -y
# ... restore and clean up
mv sg_erp.spec.bak sg_erp.spec
rm -rf build
```

The pyinstaller output for onefile mode is `dist/<name>.exe` (single file, no subfolder) — move it into the user's expected install folder next to the data dirs:
```bash
mv dist/赛光智能ERP_v2.exe dist/赛光智能ERP_v2/赛光智能ERP_v2.exe
```

---

## Spec template (onefile, copy and edit)

```python
# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onefile spec — single .exe, all assets bundled."""
import os

block_cipher = None
base_dir = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    ['desktop.py'],
    pathex=[base_dir],
    binaries=[],
    datas=[
        ('templates', 'templates'),  # bundled read-only
        ('static', 'static'),
        ('app.ico', '.'),
    ],
    hiddenimports=[...],  # list all imports used by code that PyInstaller can't auto-detect
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas'],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='YourAppName',
    debug=False,
    console=True,   # see "Console and runtime UX" section below
    disable_windowed_traceback=False,  # see below
    icon='app.ico',
)
```

**Spec fields that silently break the shipped app (check every rebuild):**

- **`datas=[]` — the classic.** If the code loads `config/*.json`, `templates/`, or any data file
  by a path relative to `__file__`, an empty `datas` builds perfectly and **crashes at launch**
  with `FileNotFoundError: <_MEIPASS>\modules\..\config\x.json`. `os.path.dirname(__file__)/..`
  resolves correctly under `_MEIPASS` — the only missing piece is the `datas` entry. Symptoms
  look like "the exe is broken" but the source runs fine.
- **Keep exactly ONE spec file as the source of truth.** A drawer of stale sibling specs
  (`App.spec`, `App_v2.spec`, `App_v3.spec`) guarantees that sooner or later the build runs the
  one whose `datas`/`hiddenimports` were stripped during debugging. Delete the dead ones, and
  make `build.py` call the spec instead of hand-writing PyInstaller flags — a typed flag list
  is where `"--icon", None`-style breakage lives.
- **`hiddenimports=[]`** mostly works (PyInstaller auto-detects top-level imports) but fails for
  dynamically imported modules; list your own package's modules explicitly.
- **Never bundle `.env` or credentials** — see "Read-only vs writable split" for where the frozen
  app must look for them instead.

## Console and runtime UX

Two flags control what the user sees when they double-click the exe:

| Flag | `True` (default for GUI) | `False` |
|--|--|--|
| `console` | A black `cmd` window opens behind/with the GUI. Flask logs, prints, tracebacks all visible there. | No console window. App is pure GUI. Flask logs/prints go to NUL. **Process is fully invisible** from a UX perspective — Task Manager shows just `YourApp.exe`. |
| `disable_windowed_traceback` | On unhandled exception, a Windows message box pops up with the traceback. User sees the crash. | Silent crash — process just exits. Safer for production (no scary traceback popup) but debugging requires you to set `console=True` first. |

**Recommended combo for shipped desktop apps:**

```python
console=False,                  # no black window, no visible cmd
disable_windowed_traceback=False,  # still show traceback popup if it crashes (better than silent exit)
```

**During development / debugging:**

```python
console=True,                   # see Flask request logs in real time
disable_windowed_traceback=False,
```

**Why `disable_windowed_traceback=False` is safer even in production:** with `console=False` and a real bug, the app just exits silently and the user thinks "the program is broken, weird". A traceback popup at least tells them something went wrong. Switch to `True` only if you've thoroughly QA'd and want a perfectly clean exit-on-crash UX.

**Note on Task Manager / `tasklist`:** Flask runs as a thread inside the exe (started in `desktop.py`'s `start_flask`), so the user only ever sees ONE process. Even with port 5000 open and serving requests, there's no separate `python.exe` or `flask.exe` to hide. Verify after build:

```bash
tasklist | grep -i "YourAppName"   # exactly one row
netstat -ano | grep ":5000"        # the same PID owns the port
```

## GUI-only apps (no HTTP backend): verify with `--selftest`

A GUI-only app (Tkinter, PySide6) has no endpoint to probe, and `console=False` means a
startup crash prints nothing anywhere. Verify it the same way you would verify a service —
with an exit code:

1. Add a headless `--selftest` branch to the entry point that loads bundled data and runs the
   core pipeline, returns 0/1, and **also writes the report to a file** (windowed EXEs lose
   stdout): `App.exe --selftest; echo "EXIT=$?"; cat selftest_result.txt`.
2. Run it from `build.py` right after packaging and fail the build if it does not print
   `RESULT: PASS` (delete the stale result file first).
3. Confirm the window itself starts — exit code alone does not prove the GUI opens:
   `powershell -NoProfile -Command "Get-Process AppName | Select-Object Id,Responding,MainWindowTitle"`
   → `Responding=True` + non-empty title.
4. Close the instance you launched before any further build (it locks `dist/App.exe`).

Full recipes — including a headless Tk driver that verifies UI/layout changes by driving the
real handlers and asserting on the produced artifact — are in
`references/gui-selftest-and-headless-testing.md`.

---

## Verification checklist after build

```bash
# 1. Static assets served?
for url in /static/css/*.css /static/js/*.js /static/img/*.png; do
  curl -s -o /dev/null -w "%{http_code} %{size_download}B $url\n" http://127.0.0.1:5000$url
done

# 2. No CDN refs in served HTML?
curl -s http://127.0.0.1:5000/login | grep -E "cdn|jsdelivr|unpkg" && echo "BAD" || echo "GOOD"

# 3. App responds to login flow?
curl -s -c /tmp/c.txt -X POST http://127.0.0.1:5000/login -d "username=admin&password=admin123"

# 4. DB still has data?
sqlite3 data/app.db "SELECT count(*) FROM <main_table>;"

# 5. Single process visible (Flask is a thread, not separate process)
tasklist | grep -i "YourAppName"          # exactly one row
netstat -ano | grep ":5000"               # the same PID owns the port
```

All five should pass. If 1-2 fail, you forgot to update the bundled `_internal/` (templates/static) — they don't auto-sync from source.

---

## Common `hiddenimports` for Flask + WebView stacks

```python
hiddenimports=[
    # pywebview EdgeChromium
    'clr', 'webview.platforms', 'webview.platforms.edgechromium',
    # Flask ecosystem
    'flask', 'flask_cors', 'flask_login', 'flask_sqlalchemy', 'flask_wtf',
    'werkzeug', 'sqlalchemy', 'sqlalchemy.sql.default_comparator',
    'jinja2', 'markupsafe', 'dateutil',
    # Reports/exports
    'fpdf', 'fpdf.fonts',
    'openpyxl', 'openpyxl.styles', 'openpyxl.cell', 'openpyxl.workbook',
    # Windows integration
    'win32com.client',
]
```

---

## Windowed mode + uvicorn isatty crash (FastAPI apps)

**Symptom:** FastAPI / Starlette app packaged with PyInstaller `--windowed` (or `--noconsole`) blows up at startup with:

```
AttributeError: 'NoneType' object has no attribute 'isatty'
  File "uvicorn\logging.py", line 42, in __init__
ValueError: Unable to configure formatter 'default'
  File "uvicorn\config.py", line 391, in configure_logging
  File "logging\config.py", line 823, in dictConfig
```

The launcher exits silently — no window, no log, no message.

**Why:** `runw.exe` (the PyInstaller windowed bootloader) gives the embedded Python interpreter **no real stdin/stdout/stderr handles** — `sys.stdout` / `sys.stderr` are literally `None`. uvicorn's default `LOGGING_CONFIG["formatters"]["default"]` is `uvicorn.logging.DefaultFormatter`, whose `__init__` does:

```python
self.use_colors = sys.stdout.isatty()  # AttributeError when stdout is None
```

`logging.dictConfig` catches the AttributeError, then raises `ValueError: Unable to configure formatter 'default'`, which kills `uvicorn.Config(...)` before the server can start.

**Why `--windowed` and not `--console`:** `--console` works around it by giving the process a real console (so `sys.stdout` is a real file), but it pops a black `cmd` window behind your GUI — useless for desktop apps. `--windowed` is the right choice; the launcher just has to compensate.

**Fix — pass a custom `log_config` to `uvicorn.Config`** that forces `use_colors=False` (skipping the isatty probe) and routes handlers to a safe stream:

```python
import sys, os
from pathlib import Path

def _build_log_config(log_dir: Path) -> dict:
    # If stderr exists (run-from-console dev), use it. Otherwise fall back
    # to a file under the user log dir so the EXE never crashes on no-console.
    if sys.stderr is not None:
        stream = "ext://sys.stderr"
    else:
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = str(log_dir / "uvicorn.log")
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(message)s",
                "use_colors": False,   # <-- critical, skips isatty()
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(levelprefix)s %(client_addr)s - '
                       '"%(request_line)s" %(status_code)s',
                "use_colors": False,
            },
        },
        "handlers": {
            "default": {"formatter": "default", "class": "logging.StreamHandler", "stream": stream},
            "access":  {"formatter": "access",  "class": "logging.StreamHandler", "stream": stream},
        },
        "loggers": {
            "uvicorn":        {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error":  {"level": "INFO"},
            "uvicorn.access": {"handlers": ["access"],  "level": "INFO", "propagate": False},
        },
    }

# In your launcher's main():
config = uvicorn.Config(
    app, host=args.host, port=port,
    log_level="info", access_log=False,
    log_config=_build_log_config(log_dir),   # <-- the fix
)
```

**Also fix your launcher's `print()` calls.** `print()` raises `ValueError: I/O operation on closed file` when `sys.stdout` is None. Wrap them in a helper that tries stdout, falls back to stderr, and finally swallows:

```python
def _emit(line: str) -> None:
    try:
        print(line); return
    except Exception:
        pass
    try:
        if sys.stderr is not None:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
    except Exception:
        pass
```

**How to reproduce in a dev env** (so you catch this BEFORE shipping):

```python
# Run in a subprocess with stdio detached
import subprocess, os, sys
src = '''
import sys
sys.stdout = None   # simulate runw.exe
sys.stderr = None
sys.stdin  = None
import uvicorn
from api.app import app
try:
    c = uvicorn.Config(app, host="127.0.0.1", port=8900)
    with open("windowed_test.log", "w") as f:
        f.write("Config OK\\n")
except Exception as e:
    import traceback
    with open("windowed_test.log", "w") as f:
        traceback.print_exc(file=f)
'''
subprocess.run([sys.executable, "-c", src],
               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL, check=False)
```

If `windowed_test.log` contains `Config OK`, your log_config is safe. If it contains an `AttributeError` traceback, the bug is live.

**Related symptom:** a few other libraries also probe `sys.stdout.isatty()` on import — `rich`, `colorama`, `click` in some modes. If you ship those in a windowed EXE, expect them to either silently disable colors (best case) or crash (worst case). Test with `sys.stdout = sys.stderr = None` once before shipping.

---

## Patch-tool indentation pitfall (Python)

Three ways the `patch` tool can produce a Python file that `ast.parse()` rejects — and a quick check that catches all of them before you ship.

### A) Over-indented body (most common)

**Symptom:** You use `patch(mode='replace')` on a Python file (e.g. `app.py`) to insert a new function. After the patch, `python -c "import ast; ast.parse(...)"` reports `IndentationError: unexpected indent` on lines you just added. Every line in your new content is exactly **4 spaces too far right**.

**Why this happens:** The Hermes `patch` tool's `new_string` carries over the indentation of the line that matched `old_string`'s last line. If you paste a fresh `def foo():` block (at 4-space top-level indent), but the patch tool anchors it next to a line that was at 12 spaces deep inside some other function, every line of your new block gets +4 spaces for each level of nesting the anchor lived in. The lint warning at the bottom of the patch result tells you — don't ignore it.

**Reproduction (sg-erp case):** I patched `app.py` to insert a new `@app.route` + `def api_*():` block. The anchor was at 4-space indent (module level inside `create_app`). My `new_string` was already correctly indented at 4 spaces for `def`. But every **body line** of the new block — already at 8 spaces — came out at **12 spaces** because the patch tool added one level of nesting. Result: `IndentationError`.

**Fix — three patterns:**

1. **Quickest:** after every patch on Python code, run `python -c "import ast; ast.parse(open('app.py').read())"` (or equivalent). If it fails, dedent the patched region by 4 spaces:

   ```python
   # Find the patched region (lines start_at..end_at in the diff)
   with open('app.py', 'r', encoding='utf-8') as f:
       lines = f.readlines()
   for j in range(start_at, end_at):
       if lines[j].startswith('            '):  # 12 -> 8
           lines[j] = lines[j][4:]
       elif lines[j].startswith('                '):  # 16 -> 12
           lines[j] = lines[j][4:]
       with open('app.py', 'w', encoding='utf-8') as f:
           f.writelines(lines)
   ```

   Dedent by however many levels the patch tool over-indented (usually 1 = 4 spaces).

2. **Defensive (if `old_string` was clean):** paste the new block **at the same exact indent** as the line below where it'll go, so the patch tool's "anchor" lands on a line at the right depth. e.g. if you're inserting after a 4-space `@app.route`, make sure your `new_string`'s first line is also at 4 spaces.

3. **For multi-line replacements where you can't get the anchor right:** rewrite the entire target function/file via `write_file` instead of `patch`. Slower but no indentation drift.

**Why this matters for desktop packaging work specifically:** every packaging change tends to involve patching `app.py` (new endpoints, route additions, model field changes). You'll hit this every rebuild cycle if you don't check syntax after each patch.

### B) `module.__getattr__` doesn't rescue class-statement base expressions

**Symptom:** You added a `__getattr__` to break a circular import — `class Coder(KairosAgent):` references `KairosAgent`, but you lazy-loaded it via:

```python
def __getattr__(name):
    if name in ['KairosAgent']:
        import kairos.agents.base as _m
        return getattr(_m, name)
```

Now `import kairos.agents.roles.coder` raises `NameError: name 'KairosAgent' is not defined`.

**Why:** module-level `__getattr__` only fires for **attribute access** on the module (`module.foo`). It does **not** fire for **name lookups inside a class statement**. When Python evaluates `class Coder(KairosAgent):`, it looks up `KairosAgent` in the module's globals dict — not via attribute access. The `__getattr__` shim never gets a chance to run.

The same trap fires for any name referenced in a class body: base classes, decorators that are bare names, metaclass= arguments.

**Fix:** if a class-statement needs a name, the name **must be a top-level binding** in the module. Either:

- Restore the direct import: `from kairos.agents.base import KairosAgent` (simplest — works unless there's a real circular dependency, in which case the fix is to break the cycle in the dependency, not to defer the name lookup).
- Or move the lazy machinery to **import time** via a metaclass / `__init_subclass__` / dynamic `type(name, bases, dict)` call — but those are much heavier than just fixing the import.

**Why this matters for PyInstaller work:** the lazy `__getattr__` pattern gets used during packaging to dodge PyInstaller's eager-import analyzer. If the lazy-loaded name is then referenced in a class statement, the analyzer's lazy-by-default vs. eager-on-class-statement asymmetry will bite you — and it only shows up at module import time inside the EXE, never in a normal dev run (where the imports happen in dependency order).

### C) Literal `\n` in `new_string` makes it through as escaped text

**Symptom:** Your `patch(mode='replace')` lands cleanly (no lint warning), but `python -c "import ast; ast.parse(...)"` errors with `SyntaxError: unexpected character after line continuation character` somewhere in the patched region. The patched source contains literal two-character sequences `\` + `n` instead of newlines.

**Why:** if you write a multi-line `old_string` or `new_string` as a Python string literal that itself contains `\n` escapes, those escapes **do get interpreted as newlines** — but only at the **outer Python layer** that produces them. Inside the patch tool's transport, if the JSON envelope gets any backslash-escaped form, you may end up writing the literal characters `\` and `n` into the file instead of a newline.

**Fix patterns that keep you safe:**

- Pass the entire patch payload through `json.dumps(...)` first to see what gets sent — every `\n` should appear as a literal newline character, not as the two characters `\` `n`.
- After the patch, run the syntax check. If it fails with "line continuation", search for backslash-newline in the patched region and replace with literal newlines:

  ```python
  src = open(p, encoding='utf-8').read()
  # Where the patch landed, replace accidental "\n" with real newlines
  fixed = src.replace('\\\n', '\n')  # backslash-newline -> newline
  open(p, 'w', encoding='utf-8').write(fixed)
  ```

- For long multi-element list literals (a common case where this bites — e.g. constructing a long CLI argv list), prefer **breaking the list across multiple lines** in `new_string` and using actual newlines rather than embedding `\n` inside a single-line string. The patch tool handles multi-line `new_string` reliably; the issue is specifically with embedded escape sequences.

### Universal safety net

After **every** `patch()` on a Python file, run this before rebuilding:

```bash
python -c "import ast; ast.parse(open('PATH/TO/file.py', encoding='utf-8').read())" \
  && echo OK || echo FAIL
```

Three seconds, catches all three pitfalls above.

For the full project (catches syntax errors hiding in unvisited modules too):

```python
import os, ast
for dp, _, fn in os.walk('.'):
    if '__pycache__' in dp: continue
    for f in fn:
        if f.endswith('.py'):
            p = os.path.join(dp, f)
            try: ast.parse(open(p, encoding='utf-8').read())
            except SyntaxError as e:
                print(f'{p}:{e.lineno}  {e.msg}')
```

---

## Absorbed GUI-build sub-skills (consolidated umbrella)

This is the umbrella for **building & distributing Windows desktop Python apps** to non-technical users, and covers the shared concern of the smart `.bat` launcher that handles multi-Python-version environments. Two sibling GUI-build approaches were consolidated here; their full bodies live under `references/`:

- **PySide6 / Qt native GUI** — transparent frameless windows, click-through masks, system tray, multi-monitor, mouse-drag state machines, and the smart `.bat` launcher for multi-Python (hermes venv 3.11 + MS Store 3.13). → `references/pyside6-windows-gui-app/`
- **Local AI model GUI (Gradio)** — package Flux / SD / LLM as a standalone Gradio desktop app; Windows setup, low-VRAM (6GB) optimization, China mirror config, batch launcher, smart environment bootstrap. → `references/local-ai-gui/`

## Bundled resources

- **`scripts/backup_dist.py`** — pre-build backup / post-build restore helper for the COLLECT-wipes-target pitfall. Run `python scripts/backup_dist.py dist/MyApp` before each `pyinstaller` invocation, then `python scripts/backup_dist.py dist/MyApp --restore-from /tmp/pyinstaller_prebuild_MyApp_<ts>` after.
- **`references/sg-erp-case-study.md`** — concrete worked example of this whole pattern against a real Flask + pywebview ERP app (赛光智能ERP_v2). Includes the actual spec file, the CDN-vendoring fix for China, and the data-wipe recovery procedure.
- **`references/gui-selftest-and-headless-testing.md`** — verifying a GUI-only app (Tkinter/PySide6, no HTTP backend): the `--selftest` exit-code contract wired into `build.py`, the headless Tk driver that tests UI changes without a human, the three-layer grep for removed form fields, and the `Get-Process` liveness check.