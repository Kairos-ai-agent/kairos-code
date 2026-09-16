---
name: "windows-python-venv-troubleshooting"
description: "Diagnose and fix Windows Python venv issues that don't reproduce consistently across shells — MSYS symlink vs NTFS junction, pyvenv.cfg home path, uv link-mode, \"No Python at\" launcher errors. Trigger"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\.archive\\windows-python-venv-troubleshooting\\SKILL.md"
---
# Windows Python venv Troubleshooting

A class of bugs unique to Windows where a Python virtualenv fails to launch from one shell (typically PowerShell, cmd.exe, Windows Terminal) while working from another (MSYS bash, Git bash). Root cause is almost always a symlink/junction layer that one shell follows and another doesn't.

## When to load this skill

- User reports `No Python at '...'` error when launching a venv-based tool (PyInstaller bootstrapper, `python.exe` from venv `Scripts\`, zipapp shebang launcher, etc.)
- A venv works in MSYS bash / Git bash but fails in PowerShell / cmd / Windows Terminal
- pyvenv.cfg `home` field references a path that looks "almost right" — short name without patch version suffix like `cpython-3.11-windows-x86_64-none` instead of `cpython-3.11.15-windows-x86_64-none`
- uv-installed Python under `%APPDATA%\uv\python\cpython-*\` exhibits path-related errors

## Diagnostic procedure (run in order)

1. **Confirm the symptom is shell-dependent, not always-broken**
   ```bash
   # bash / MSYS
   <venv>\Scripts\python.exe --version          # works
   powershell.exe -NoProfile -Command "& '<venv>\Scripts\python.exe' --version"  # fails?
   ```

2. **Inspect pyvenv.cfg** (the smoking gun lives here)
   ```bash
   cat <venv>\pyvenv.cfg
   ```
   Note the `home` line. If it points to a path that is a symlink/junction (not a real directory), that's your bug.

3. **Check if the home path is a symlink vs NTFS junction**
   ```bash
   # From bash: shows symlinks as `lrwxrwxrwx`
   ls -ld "$APPDATA/uv/python/cpython-3.11-windows-x86_64-none"
   # Look for `-> /c/...` arrow → MSYS symlink

   # From PowerShell: shows NTFS junctions as `ReparsePoint` + `LinkType: Junction`
   Get-Item '...\cpython-3.11-windows-x86_64-none' | Format-List *
   ```
   Critical: MSYS symlinks and NTFS junctions look different to each shell. A junction (reparse point) IS visible to native Windows; an MSYS-only symlink is NOT.

4. **Verify python.exe reachable from each shell**
   ```bash
   # bash → resolves MSYS symlink → works
   "C:\Users\u\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe" --version
   # powershell → may not resolve MSYS symlink → fails
   & 'C:\...\cpython-3.11-windows-x86_64-none\python.exe' --version
   ```
   If bash returns the version but PowerShell errors, the home path is an MSYS-only symlink.

5. **Find the real directory**
   ```bash
   uv python list                    # shows installed pythons and their real paths
   # Look for the .15 / .N suffix — that's the real dir, not a symlink alias
   ```

## Common pitfalls

- **`pyvenv.cfg home` recorded the floating name, not the versioned name**. When `uv venv` runs, it captures `cpython-3.11-windows-x86_64-none` (the alias uv creates), not `cpython-3.11.15-windows-x86_64-none` (the actual install). Bash resolves the alias; PowerShell may not. Fix: edit pyvenv.cfg to point `home` at the versioned name.

- **uv's default link mode creates MSYS-style symlinks on Windows**. Newer uv (0.4+) creates NTFS junctions (reparse points) which Windows native tools CAN follow. Older uv or hand-rolled paths may leave MSYS-only symlinks behind. Force copy mode to eliminate ambiguity:
  ```toml
  # %USERPROFILE%\.config\uv\uv.toml
  link-mode = "copy"
  ```

- **The "No Python at" message is from the Python Launcher for Windows** (`PCbuild/launcher.c`, ~line 1500 in CPython 3.11 source), NOT PyInstaller. It fires when:
  - `pyvenv.cfg home` exists as a directory but `home\python.exe` is missing OR
  - `home` doesn't resolve from the calling shell's perspective

- **Just because bash works doesn't mean the bug is "bash only"**. The user's actual workflow (PowerShell 7.6.5 fresh install) is the real production path. Always validate with the user's target shell.

- **`hermes.exe` style zipapps on Windows**: the .exe has a PE header + shebang line (`#!C:\path\to\venv\Scripts\python.exe`). When launched, the PE prefix invokes that python.exe with the embedded script. If THAT python.exe is the Python Launcher and it fails to resolve `home`, you get the "No Python at" message BEFORE the Python script even starts — so searching hermes/python source for the error string will turn up nothing. The error originates from the Windows executable, not from Python code.

## Fix recipes

### Fix 1: Edit pyvenv.cfg (quick, reversible, surgical)

```bash
# Show current pyvenv.cfg
cat <venv>\pyvenv.cfg
# Make a backup
cp <venv>\pyvenv.cfg <venv>\pyvenv.cfg.bak
# Patch: replace the symlink/alias `home` with the real versioned dir
#  e.g. cpython-3.11-windows-x86_64-none  →  cpython-3.11.15-windows-x86_64-none
#  Apply to BOTH `home` and `command` lines (keep `executable` as-is if it already points at the real dir)
```

Verify:
```bash
& '<venv>\Scripts\python.exe' --version          # should now work from PowerShell
& '<venv>\Scripts\<app>.exe' --version           # launcher-prefixed .exe should work too
```

Or use the bundled helper:
```bash
python scripts/fix-pyvenv-home.py <venv_path> --dry-run   # preview diff
python scripts/fix-pyvenv-home.py <venv_path>             # apply + write .bak
```

### Fix 2: Set uv link-mode globally (preventive)

```bash
mkdir -p "$USERPROFILE/.config/uv"
cat > "$USERPROFILE/.config/uv/uv.toml" <<'EOF'
link-mode = "copy"
EOF
```
Next `uv python install` / `uv venv` will COPY instead of symlink/junction. Eliminates the entire class of bugs.

### Fix 3: Recreate the venv (nuclear)

```bash
cd <project>
uv sync                                     # regenerates .venv cleanly
# or
uv venv --python 3.11 .venv                 # explicit version, fresh pyvenv.cfg
```

## Verification checklist

- [ ] From the user's actual target shell (PowerShell, cmd, Windows Terminal), the venv python runs: `python --version`
- [ ] Any zipapp-style .exe (e.g. `hermes.exe`) in `venv\Scripts\` runs: `<app> --version`
- [ ] `pyvenv.cfg` `home` field has the versioned path, not the alias
- [ ] Backup of original pyvenv.cfg exists if fix was surgical

Or run the bundled probe:
```bash
pwsh -NoProfile -File scripts/diagnose-venv.ps1 -VenvPath <venv_path> [-AppExe <name>.exe]
# exit 0 = all green, exit 1 = problems detected (output lists what failed)
```

## Support files

- `references/hermes-pyvenv-case.md` — full investigation trail from the 2026-08-21 Hermes session that originally surfaced this class of bug. Worth reading once to internalize the diagnostic flow and the specific gotchas (zipapp vs PyInstaller disambiguation, MSYS vs NTFS reparse point visibility, where exactly the "No Python at" message originates).
- `scripts/diagnose-venv.ps1` — PowerShell probe script. Run on any suspect venv to confirm the symptom, identify whether the home path is a symlink/junction/real dir, and test reachability from both PowerShell and bash. Returns non-zero exit if problems found.
- `scripts/fix-pyvenv-home.py` — Python helper that parses `uv python list`, finds MSYS-style alias paths (e.g. `cpython-3.11-windows-x86_64-none` without patch), and rewrites `pyvenv.cfg` `home` and `command` lines to the versioned real paths. Supports `--dry-run`. Writes `.bak` automatically.