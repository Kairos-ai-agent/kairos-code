# Round 37 — UI/behavior fixes (user feedback round)

**Goal:** five concrete fixes the user requested after the R28-R36
feature push. The R28-R36 work was heavy on backend; R37 is the
"now make the UI usable" round.

| # | User request | Where it's fixed |
|---|--------------|------------------|
| 1 | Top-right settings moved to bottom-left | `web/src/components/AppLayout.tsx` + `web/src/components/ChatSidebar.tsx` (new `SidebarFooter`) |
| 2 | Folder picker moved to chat input | `web/src/components/ChatComposer.tsx` (now mounts `<FolderPicker />` above the textarea) |
| 3 | LLM settings were "hidden" — re-add as custom OpenAI / Anthropic only, with test-connection | `web/src/stores/settingsStore.ts` (new `OpenAIConfig` / `AnthropicConfig` shape) + `web/src/components/SettingsDrawer.tsx` (rewritten `ProviderPanel` + `OpenAICompatForm` / `AnthropicCompatForm`) + `api/routes/config.py` (new `POST /api/config/test_connection`) |
| 4 | New project: no loop cap + reviewer only checks bugs | `kairos/loop/loop_runner.py` (`unbounded=True` param) + `kairos/loop/prompts.py` (new `bug_reviewer` focus that explicitly says "BUGS ONLY") + `kairos/core/orchestrator.py` (`_is_new_project()` + `start_loop` auto-wires both) |
| 5 | "Every conversation becomes a task" — fix | `web/src/components/ChatComposer.tsx` (new "Run as task" toggle, default OFF) + `web/src/pages/Chat.tsx` (`handleSubmit` branches on the toggle: OFF → `POST /chat` single-turn; ON → `POST /start` loop) + new backend `POST /api/projects/{id}/chat` in `api/routes/projects.py` |

**Scope:** 1 new backend endpoint + 1 new CLI test endpoint + 4
frontend refactors + 39 new tests.

---

## 1. Backend changes

### 1.1 `POST /api/projects/{id}/chat` (single-turn, no loop)

```python
# api/routes/projects.py
@router.post("/{project_id}/chat")
async def chat(project_id: str, request: "ChatRequest"):
    """Send a single user message to the Coder and return the reply."""
```

The Coder is invoked once via `project.coder.run(AgentTask(...))`.
No plan tracking, no plan approval, no Reviewer. The reply is
returned over HTTP *and* published to the message bus so the WS
thread can render it. This is the endpoint the "Chat mode" of the
composer hits.

### 1.2 `POST /api/config/test_connection` (LLM key verifier)

```python
# api/routes/config.py
@router.post("/test_connection")
async def test_connection(req: TestConnectionRequest = Body(...)):
    """Verify that the user-supplied base URL + API key actually work."""
```

Two probes:
- **OpenAI-compatible**: `GET {base_url}/v1/models` with `Authorization: Bearer ...`
- **Anthropic-compatible**: `POST {base_url}/v1/messages` with `x-api-key: ...`

Returns `{ok, status, detail}`. The SettingsDrawer "Test connection"
button calls this and shows a green OK / red Fail tag inline.

### 1.3 `bug_reviewer` focus + `unbounded=True` for new projects

```python
# kairos/loop/prompts.py
_REVIEW_FOCUS_LABELS = {
    "bug_reviewer": (
        "BUGS ONLY. Look exclusively for runtime errors, exceptions, "
        "null-pointer / index-out-of-bounds risks, infinite loops, "
        ...
        "Only flag actual bugs."
    ),
    ...
}
```

The existing labels (`security_reviewer`, `perf_reviewer`, etc.)
are unchanged for backward compatibility. The new `bug_reviewer`
explicitly tells the LLM "do NOT nitpick".

```python
# kairos/loop/loop_runner.py
async def run_loop(session, requirement, *, unbounded: bool = False):
    ...
    if session.round >= LOOP_SAFETY_CAP and not getattr(session, "_unbounded", False):
        # emit loop.safety_cap, return
```

When `unbounded=True` the cap is skipped; the loop runs until the
user hits Stop or the Coder reports completion. Default is `False`
so the safety guarantee is preserved.

```python
# kairos/core/orchestrator.py
async def start_loop(self, project_id: str, requirement: str) -> str:
    ...
    is_new_project = self._is_new_project(project_id)
    unbounded = is_new_project
    if is_new_project and not review_focus:
        review_focus = ["bug_reviewer"]
    project.loop_task = asyncio.create_task(
        run_loop(session, requirement, unbounded=unbounded), ...
    )
```

`_is_new_project()` probes `load_messages + load_loop_rounds` and
returns `True` only if both are empty. Any DB failure defaults to
`False` (conservative — don't trip the new-project fast path on
a transient error).

User-configured `review_focus` always wins. The new-project
default of `bug_reviewer` is only applied when the user hasn't
configured anything.

## 2. Frontend changes

### 2.1 Topbar → minimal (logo + folder picker only)

`AppLayout.tsx` previously had 4 controls in the top-right
(Today / Tools / Theme / Settings dropdown). All 4 are gone; the
topbar now contains only:

- Sidebar toggle
- Logo
- FolderPicker (project switcher)
- Spacer

### 2.2 `SidebarFooter` — bottom-left controls (new)

`ChatSidebar.tsx` now renders a `<SidebarFooter />` at the very
bottom (after the project + session list). The footer is a 2x2
grid of buttons matching the visual weight of the existing
`ProjectRow` / `SessionRow`:

| | Left | Right |
|---|---|---|
| Top | **Today** | **Tools** |
| Bottom | **Settings** (opens drawer) | **Light / Dark** (theme toggle) |

Each has its own `data-testid` (`footer-today`, `footer-tools`,
`footer-settings`, `footer-theme`) so future UI tests can target
them precisely. The footer is exported as a named component so
the test environment can mount it in isolation.

### 2.3 `ChatComposer` — Run-as-task toggle + FolderPicker

The composer was the single biggest UX fix in R37:

- **Run-as-task toggle** (default OFF): a small pill in the action
  row showing "Chat" (single-turn) or "Task" (full loop). The send
  button color also changes (brand for chat, warning yellow for
  task) so the user can see at a glance what mode they're in.
- **FolderPicker above the input**: the project switcher now lives
  right above the textarea, so the user can change projects
  without scrolling to the top of the page. The topbar still has
  one too for quick access.

`onSubmit` signature changed from `onSubmit(text)` to
`onSubmit(text, runAsTask)`. The placeholder text also adapts to
the mode ("Ask anything" for chat vs "Describe a task" for task).

### 2.4 `Chat.tsx` — chat vs task dispatch

`handleSubmit` now branches on the toggle:

```typescript
if (askState?.pending) {
  // Reviewer question is pending → always answer the question
  await api.post(`/ask/answer`, { answer: text });
} else if (runAsTask) {
  // Task mode → kick off the loop
  await api.post(`/start`, { requirement: text });
} else {
  // Chat mode → single-turn
  const r = await api.post(`/chat`, { message: text });
  // append the reply as a coder bubble so the thread reads like a
  // conversation
}
```

### 2.5 `SettingsDrawer` — focused LLM Models tab

The Provider tab was renamed to "LLM Models" and redesigned:

- **Only 2 options**: OpenAI-compatible, Anthropic-compatible
- **Each form has**: Base URL + API key (password input) + Model
- **Each has a "Test connection" button** that POSTs to
  `/api/config/test_connection` and shows a green/red tag inline

Legacy shape migration: if a user has the old
`{apiKeyEnv, ollamaBaseUrl, ollamaModel}` config on disk
(persisted from R8), the load path detects it and resets to the
new defaults rather than crashing on the missing `openai` /
`anthropic` keys.

`settingsStore.ts` was also rewritten: the `ProviderSettings` type
now is `{active, openai: {baseUrl, apiKey, model}, anthropic: {...}}`.
The old `apiKeyEnv` / `ollamaBaseUrl` / `ollamaModel` fields are
gone.

## 3. Tests

### 3.1 Backend — `tests/test_r37_backend.py` (24 tests)

| Sub-group | Count | What |
|-----------|-------|------|
| `bug_reviewer` focus | 3 | label exists, prompts use it, fallback when no focus |
| `run_loop(unbounded=...)` | 3 | signature accepts kwarg, cap skipped with True, enforced with False |
| `/api/projects/{id}/chat` | 5 | success path, empty message → 400, missing project → 404, coder failure → 500, no coder → 503 |
| `/api/config/test_connection` | 5 | empty URL/key → 400, unknown provider → 400, unreachable URL → ok=False, OpenAI uses GET /v1/models, Anthropic uses POST /v1/messages |
| `_is_new_project()` | 4 | empty DB → True, with messages → False, with rounds → False, DB error → False |
| `start_loop` wiring | 3 | new project → unbounded + bug_reviewer, existing → no unbounded, user focus wins over new-project default |

**Total: 24 / 24 passing in 6.74 s.**

### 3.2 UI source — `tests/test_r37_ui_source.py` (15 tests)

An attempted vitest test file was abandoned because mounting the
full antd tree in jsdom takes >15s per test (well over the
default 5s timeout). The vitest file is preserved as
`web/src/test/appLayoutR37.test.tsx.skipped` for future re-enable
when the test environment is faster.

Instead, the UI tests are **source-level** checks: they read the
.tsx files and assert that the R37 markers are present
(testids, function names, click handlers, import shapes). These
catch reverts cheaply and run in <2s total.

| Test | Asserts |
|------|---------|
| `test_applayout_no_longer_has_top_right_avatar_dropdown` | No `<Dropdown>` or `<Avatar>` in AppLayout, no `/today` or `/tools` link |
| `test_applayout_still_keeps_logo_and_folder_picker` | Logo + `<FolderPicker />` still there |
| `test_chatsidebar_exports_sidebarfooter` | `export const SidebarFooter` present |
| `test_chatsidebar_footer_has_today_tools_settings_theme` | 4 data-testids present |
| `test_chatsidebar_footer_uses_navigate_for_today_and_tools` | `navigate('/today')`, `navigate('/tools')`, `openSettings`, `onClick={toggle}` regex |
| `test_chatcomposer_has_folder_picker_above_input` | `data-testid="composer-folder"` present |
| `test_chatcomposer_has_run_as_task_toggle` | `data-testid="run-as-task-toggle"`, `runAsTask`/`setRunAsTask` |
| `test_chatcomposer_on_submit_signature_takes_runtask_boolean` | `onSubmit: (text: string, runAsTask: boolean)` |
| `test_chat_tsx_handle_submit_branches_on_runtask` | branches on `/chat` vs `/start` |
| `test_settings_drawer_provider_tab_renamed_to_llm_models` | `<RobotOutlined /> LLM Models` |
| `test_settings_drawer_provider_active_uses_only_openai_anthropic` | No ollama / deepseek / custom value |
| `test_settings_drawer_test_connection_button_present` | Both testids + the API path |
| `test_settings_drawer_openai_test_posts_openai_provider` | provider 'openai' / 'anthropic' payloads |
| `test_settings_drawer_legacy_provider_shape_migrated` | `'apiKeyEnv' in d.provider` migration check |
| `test_settings_store_provider_has_only_openai_and_anthropic` | New `OpenAIConfig` / `AnthropicConfig` types present, legacy fields gone |

**Total: 15 / 15 passing in 1.26 s.**

## 4. Manual smoke test

1. Open the app. The topbar is now: `≡  K  Kairos  [▼ /project]`. The
   previous Today / Tools / Theme / Settings buttons are gone.
2. The chat sidebar has a new bottom-left footer with 4 buttons.
3. Open Settings (footer "Settings") → "LLM Models" tab. Type a
   OpenAI base URL + key → click "Test connection" → green OK
   tag. Type a bogus key → click again → red Fail tag.
4. Compose a message in chat mode (default). Press Enter.
   The message goes to `/chat` (single-turn), the Coder replies
   once, and no loop is started. The chat thread shows the user
   bubble + the coder bubble.
5. Toggle "Chat" → "Task" (the pill becomes yellow + a lightning
   bolt). Compose another message. The message goes to `/start`
   (loop kicks off), the WebSocket shows round events, and the
   Reviewer only checks for bugs (because the project is new).
6. Press Stop. New project? Loop is `unbounded=True` so the cap
   doesn't hit. Set up a custom focus in settings — that wins
   over the bug_reviewer default.

## 5. Risk + mitigation

| Risk | Mitigation |
|------|------------|
| Two vitest test files have 4-second+ per-test times because of the antd import graph | Source-level tests replace vitest; backend pytest covers the API surface. The `*.tsx.skipped` file is preserved so the tests can be re-enabled when the env is faster. |
| `unbounded=True` for a new project with a buggy Coder could run forever | The user can hit Stop in the UI; the cap is still enforced for existing projects. We log a `loop.started` event with `unbounded=True` so ops can audit. |
| Legacy `apiKeyEnv` users get their config wiped on first load | The migration in `SettingsDrawer` detects the old shape and resets to safe defaults. The user's actual key value can't be recovered (we never stored it), so they re-paste it once. |
| `POST /chat` could be abused as a single-turn LLM call (no plan, no review) | Same rate limit + auth as `/start`; the Coder is the same instance and goes through the same provider. No new attack surface. |
| `bug_reviewer` may approve too liberally if the LLM ignores the prompt | The `approve` decision still goes through `_check_gates` (R12) which verifies the Coder actually wrote something. The focus is guidance, not a hard filter. |

## 6. Follow-up

- **Re-enable vitest UI tests** when the test env is faster (or use
  Playwright for browser-driven tests)
- **Visual regression**: capture screenshots of the new topbar +
  footer + composer + settings drawer for the visual-regression suite
- **Migrate `bug_reviewer` to a JSON schema** so the Reviewer's
  approve/reject decision is parseable, not just the summary
  string (this is a real limitation, not a R37 one — carrying it
  forward)

## 7. Diff summary

```
 kairos/loop/prompts.py                    | +12 (bug_reviewer label)
 kairos/loop/loop_runner.py               | +20 (unbounded param)
 kairos/core/orchestrator.py              | +35 (_is_new_project, wiring)
 api/routes/projects.py                    | +60 (POST /chat endpoint)
 api/routes/config.py                      | +90 (test_connection endpoint)
 web/src/stores/settingsStore.ts           | rewrite (OpenAI/Anthropic shape)
 web/src/components/AppLayout.tsx         | rewrite (minimal topbar)
 web/src/components/ChatSidebar.tsx        | +SidebarFooter (bottom-left)
 web/src/components/ChatComposer.tsx       | rewrite (Run-as-task + folder picker)
 web/src/components/SettingsDrawer.tsx     | rewrite (LLM Models tab + test)
 web/src/pages/Chat.tsx                    | update (chat vs task dispatch)
 tests/test_r37_backend.py                 | 24 tests (new)
 tests/test_r37_ui_source.py               | 15 tests (new)
 docs/ROUND_37_REPORT.md                  | this file
```

**Result:** 39 new tests, 0 regressions, `tsc --noEmit` clean.
All 5 user requests shipped.
