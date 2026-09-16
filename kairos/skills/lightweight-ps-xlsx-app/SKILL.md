---
name: "lightweight-ps-xlsx-app"
description: "PowerShell-based self-contained applications that combine an embedded TcpListener HTTP server, xlsx files as primary data store (no external libs), and a single-file HTML frontend. Use when building or maintaining any PowerShell + Excel + light-weight browser UI tool for Chinese SMB / attendance / ERM-style workflows. Covers the server loop, JSON config conventions (schedule-{YYYYMM}.json + holidays.json + schedule.default.json), the standard mark legend (✘半天 ▲事假 △病假 ☆婚假 〇旷工 √公休 /出勤 公公出), six concrete PowerShell pitfalls, a browser-less verification recipe (curl + jsdom), and the user's strict UI preference: main page = employee × date data grid; calendar = a small inside-⚙ modal that toggles per-date overrides by clicking — never a text-input schedule form."
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/software-development/lightweight-ps-xlsx-app/SKILL.md"
---
# Lightweight PowerShell + xlsx Apps

A class of small, self-contained business tools (attendance, leave tracking, basic ERM, payroll-adjacent sheets) where:

- **Backend**: A PowerShell script holds an `HttpListener`-free `System.Net.Sockets.TcpListener` HTTP server (avoids Windows URL ACL prompts and works under any port).
- **Data store**: `.xlsx` files on disk. No SQL database, no Python/Node helpers. Read/write via `System.IO.Packaging` (zip inside the .xlsx).
- **Frontend**: A single `index.html` served by the server, vanilla HTML/CSS/JS, no build step.
- **Config**: JSON files alongside the script (`schedule-{YYYYMM}.json`, `schedule.default.json`, `holidays.json`).

Reach for this skill when working on XT考勤系统-style apps, Chinese attendance tools, or any "I want a small browser UI over an Excel file, written entirely in PowerShell so the user only needs PS7" job.

## Architecture (one-line mental model)

```
PS script (Server.ps1) ── TcpListener ── HTTP/1.1 ──┐
   ├── reads/writes *.xlsx in project root                │
   ├── reads/writes config/*.json                        │
   └── serves ui/index.html (single file)  ─────────────┤
                                                       browser
```

Single-threaded listener: each request blocks until answered. Use a single `try { ... } finally { $listener.Stop() }` around the accept loop. Reset connection with `Connection: close` so curl/Playwright always see a clean EOF.

## Standard route pattern

```powershell
function Send-JsonOk    { Send-HttpResponse $Stream 200 'OK' 'application/json; charset=utf-8' $bytes }
function Send-JsonError { ... 500 ... }
function Read-BodyJson  { Read-BodyJson $Req.body }

# inside Handle-HttpRequest's big if/elseif chain:
if ($method -eq 'GET'  -and $path -eq '/api/status')          { Send-JsonOk $Stream (Get-Status); return }
if ($method -eq 'POST' -and $path -eq '/api/upload')          { ...; return }
if ($method -eq 'GET'  -and $path -match '^/api/report/(\d{4})-(\d{1,2})$') { ...; return }
```

Always `return` after handling a route so the fall-through 404 doesn't fire.

## Config file conventions

- **`schedule.default.json`** — fallbacks for any month that doesn't override:
  ```json
  { "shift": { "start": "08:30", "end": "17:30" }, "working_days": [1,2,3,4,5] }
  ```
- **`schedule-{YYYYMM}.json`** — per-month override. New format (preferred):
  ```json
  {
    "year": 2026, "month": 7,
    "shift": { "start": "08:30", "end": "17:30" },
    "working_days": [1,2,3,4,5],
    "override": { "2026-07-04": "W", "2026-07-25": "F" }
  }
  ```
  `override` values: `"W"` work, `"R"` rest, `"F"` festival. Missing keys fall through to defaults + holidays.json.
- **`holidays.json`** — global national-holiday list. Also three arrays:
  ```json
  { "festival": ["2026-10-01", ...], "rest": [], "work": [] }
  ```
- **Monthly attendance settings / person order** — the newer apps in this class carry `attendance-settings.json` + `attendance-settings-YYYYMM.json` and `person-order[-YYYYMM].json` instead of schedule files, each inheriting from the previous month. File set, key shapes and the settings/leave model are in `references/xt-attendance-cheatsheet.md` — read it before inventing a config name.
- **Mark legend (locked)** — see `references/mark-legend-cn.md`. Stats counters must match this, otherwise counts silently lie (this exact bug caught us once).

## PowerShell pitfalls (class-level gotchas)

Six gotchas show up repeatedly. Read `references/powershell-pitfalls.md` for full transcripts.

1. **`"$r.A"` inside a hashtable literal becomes `"$r"` + `.A`** when `$r` is itself a hashtable/PSCustomObject. The output is the entire hashtable's `ToString()` dump. Fix: extract to a temp variable before the hashtable.

2. **`ZipArchive($memStream, Create)` then `Dispose()` does NOT write EOCD** under PowerShell's runtime. `ToArray()` returns truncated bytes. Fix: write to a temp file, `Dispose`, then `ReadAllBytes`.

3. **Chinese `.bat` files emitted via tools need GBK + CRLF.** UTF-8 + LF makes cmd see garbled output and parser treat tokens as command names (causing `"'ul' 不是内部或外部命令"` errors).

4. **`return ,$arr` + `@(f)` at the call site nests the array.** Callers that wrap with `@()` must get a plain `return $list.ToArray()`; with the comma form an empty result comes back as `Count = 1` holding an empty array, so `Count -eq 0` guards silently never fire and JSON shows `[[]]`.

5. **`[pscustomobject]{...}` (missing `@`) parses and returns garbage.** The symptom is far from the edit: every property of the returned object reads `$null` and callers throw "cannot call a method on a null-valued expression". Re-read the `return [pscustomobject]@{` line after any patch.

6. **`"{0:D2}" -f [math]::Floor($x/60)` throws "Format specifier was invalid"** — `Floor` returns `[double]` and `D` only formats integral types. Cast: `[int][math]::Floor(...)`.

## UI design rules for this tool class

The user's UI rules — codified from direct complaints in this class of project:

- **Main page = employee × date attendance grid.** That's the entire purpose: statistics → generated form.
- **Calendar = small tool inside ⚙ settings modal.** Toggle per-date W/R/F by clicking cells. **Never** a text-input schedule form — text forms are "有什么用" (useless).
- **Months picker always shows 12 months**, even months without a source file. Switch via ◀ ▶ + year selector. Empty months show "请先上传源文件" placeholder.
- **Stats cards live at top**, not bottom, but stay compact (one row, max ~9 metrics).
- **Update the marks legend in the front-end AND the switch-counter in the back-end in the SAME commit** when you change a mark meaning. Mismatch = silently-wrong stats for every monthly report until a cache flush.
- **Master-data fields are pickers, never free text.** 部门 / 姓名 / 职级 in any 登记 form come from the current roster → `<select>` (plus a 「其他（手动输入）」 fallback for anything off-roster) or `<datalist>`; picking or typing a known person auto-fills their dept. Hand-typing these fields is the complaint that starts with 「太麻烦」.
- **Parent settings must live-sync into inheriting rows.** Editing 本月默认 re-renders every child row (部门 / 人员) that still inherits **immediately and without saving**, and those rows stay gray-dashed (`data-explicit="0"`) — inherited values are never solidified into the child record. Only rows the user actually edits go solid/black and get written to that month's config.
- **Surgical changes only, and state what you did NOT touch.** This tool is in daily use: when a panel changes, verify and report the exact deltas plus the structural facts that stayed put (the wizard's panes and gates, route list, upload sandbox, output list). A panel looking different reads as "you rewrote my workflow" — answer that with evidence (`goStep(n)` shows exactly pane-n, step bar renders N steps, no uncaught JS errors), not reassurance.

## Workflow (per session)

1. Open the project's `xt_system/Server-XTUI.ps1` (or equivalent). Identify the routes, the Get-XYZ helpers, the report-cache (`$script:ReportCache`).
2. Make the change.
3. Kill the old server (`netstat -ano | grep :<port>` → `Stop-Process -Id`) and relaunch it. **Every `.ps1` edit needs this restart** — the server dot-sources `XT-Build.ps1` at startup. Prove the new code is live by comparing the process `StartTime` against the edited file's mtime; frontend-only edits (`ui/index.html`) need no restart (served from disk) — the user just hard-refreshes (Ctrl+Shift+R).
4. Verify the backend with `curl` first, then the UI with the jsdom harness — both recipes, plus the restart-and-prove commands, are in `references/local-api-ui-verification.md`. Use browser + `vision_analyze` only for a final visual pass.
5. **If the change touches a marked-cell counter**, regenerate one month (`POST /api/process/{Y}-{M}`) so the next `GET /api/report` returns updated numbers.

## Companion skills

- `flask-erp-development` — when the same domain needs SQLAlchemy / Flask instead of xlsx-as-DB.
- `frontend-patching` — surgical edits to the single-file `index.html` frontend.
- `windows-batch-scripts` — for the .bat launcher / closer sidekicks (start/stop scripts).
