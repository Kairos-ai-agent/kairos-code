---
name: "agnes-ai-api"
description: "Work with the Agnes AI unified API (apihub.agnes-ai.com/v1) for LLM, image gen, and async video gen. The user runs TWO production projects on this same endpoint: ImageGen (Cloudflare Worker at C:\\User"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\software-development\\gen-api-integration\\references\\agnes-ai-api\\SKILL.md"
---
# Agnes AI API

Unified multimodal API: chat (LLM) + image gen + async video gen. Base URL `https://apihub.agnes-ai.com/v1`. Auth: `Authorization: Bearer <api_key>` (or `AGNES_API_KEY` env var). Both user projects read the key from their own `data/settings.json`.

## When this skill applies

Triggers (any one):
- User asks to swap, add, or remove an `agnes-*` model in any project.
- Hitting `apihub.agnes-ai.com/v1/...` endpoints.
- Editing `AgnesProvider` / `agnes_provider.py` / `data/settings.json` / frontend `Settings.tsx` on either project.
- Test or runtime errors referencing `UnsupportedParamsError`, `duration ignored`, or video poll 404s.

## API surface

```
GET  /v1/models                  list all models (use to verify before swapping)
POST /v1/chat/completions        OpenAI SDK compatible
POST /v1/images/generations      OpenAI SDK compatible
POST /v1/videos                  FLAT — NOT /v1/videos/generations
GET  /v1/videos/{task_id}        poll (may 404 — see video quirks)
```

## Quick verification script

`scripts/probe_models.py` lists models + smoke-tests chat completion in one shot. Run it BEFORE any bulk rename to confirm the target model exists AND serves real responses (not just listed-but-broken). A model showing up in `/v1/models` only means it's registered; you still need a chat-completions roundtrip to confirm it's live.

## Endpoint quirks (full list: references/endpoint-quirks.md)

The full `references/endpoint-quirks.md` covers the always-on traps. For deeper video-API math and the 5-second-default-bug worked example, see `references/agnes-video-v2.md`.

The most common pitfalls when working on Agnes projects:

1. **Image endpoint rejects `response_format`** — passing `"url"` or `"b64_json"` returns HTTP 400 `UnsupportedParamsError`. Drop the param; server returns URL by default. This is the #1 hit when porting OpenAI image code.
2. **Video endpoint is `POST /v1/videos`** (flat), not `/v1/videos/generations` like OpenAI Sora.
3. **Video `duration` field is silently IGNORED** by the server. Use `num_frames` (must be `8n+1`, ≤441) + `frame_rate` (1-60). At 24fps, `num_frames=121` = 5s.
4. **Video reference image field is `image`**, not `image_url` (OpenAI image API name is ignored).
5. **Video poll endpoint may 404** on `GET /v1/videos/{task_id}`; the provider has a fallback `GET /v1/videos?task_id={id}` pattern.
6. **Video `image` field rejects localhost / private-network URLs** — HTTP 400 with `Image must be a public http(s) URL or base64 image data. Localhost and private network URLs are not supported`. The "serve from a local FastAPI mount at `http://127.0.0.1:8910/uploads/...`" pattern that feels natural will always 400. **Always send a `data:image/<fmt>;base64,...` inline URL** for any local-first pipeline. (Confirmed live 2026-08-08 against `POST /v1/videos`.)
7. **Video `image` field rejects `file://` paths too** — see #6; the same 400 covers both.
8. **Video `num_frames` cap is per-resolution**, not global. Docs say `≤ 441`. Empirically probed (2026-08-08): `1080p/*` tops out at **241 frames regardless of aspect ratio** (the 4xx body returns `data.max_num_frames`). `720p/*` and `480p/*` accept the full 441. Clamp in the provider before the wire request.

## Model-swap workflow

When the user asks to switch an `agnes-*` model (e.g. "把 llm 换成 agnes-2.5-flash"):

1. **Verify target exists + liveness** — run `scripts/probe_models.py` (or curl `/v1/models` with Bearer key from `data/settings.json`). Check at least one chat completion returns 200.
2. **Live-test the target** — single `POST /v1/chat/completions` with `model=target, max_tokens=20`. HTTP 200 + non-empty content + `usage.cached_tokens` is a healthy signal.
3. **Find every occurrence**:
   ```
   grep -rn "<old-model>" <project> --include='*.py' --include='*.json' \
        --include='*.ts' --include='*.tsx' --include='*.toml' --include='*.md'
   ```
   Expect 8-12 hits across:
   - `data/settings.json` (the live config)
   - `kairos_aigc/aigc/settings.py` DEFAULTS dict
   - `kairos_aigc/api/routes/settings.py` — Pydantic model default + `get(...)` fallback
   - `kairos_aigc/aigc/stages/*.py` — N× per-stage fallback strings
   - `tests/test_*.py` — 1-2 hardcoded model names in fixtures
   - `web/src/pages/Settings.tsx` — dropdown array + `initialValues` default
4. **Decide on dropdown options** — `IMAGE_MODELS` and `LLM_MODELS` arrays in `Settings.tsx` follow a dual-pattern (current + previous-gen, e.g. `['agnes-image-2.1-flash', 'agnes-image-2.0-flash']`). When swapping, add the new one FIRST, keep old as fallback. **Don't remove the old unless the user explicitly says to** — they may want to A/B compare or roll back.
5. **Bulk replace in one shot** — for ≥8 sites use `execute_code` with `read_file` + `str.replace` + `write_file`. Avoid the `patch` tool for deep-nested JS/TSX edits (its silent 4-space-indent bug has burned this user 4+ times across imagegen-forge sessions; see memory).
6. **Verify** — re-grep to confirm only expected residuals remain (typically the dropdown array's old-model entry), then run provider tests + live `/v1/chat/completions` smoke.

## Available models (snapshot — re-run `scripts/probe_models.py` for current list)

This list drifts; treat it as illustrative, not authoritative. Confirmed live 2026-08-08:

| Type | Models |
|------|--------|
| LLM  | `agnes-2.0-flash`, `agnes-2.5-flash`, `agnes-2.5-pro`, `agnes-2.5-pro-alpha` |
| Image | `agnes-image-2.0-flash`, `agnes-image-2.1-flash` |
| Video | `agnes-video-v2.0` |

`agnes-1.5-flash` (seen in old frontend dropdowns) is no longer in the API — prune it on sight.

## Pitfalls

- **`response_format` on image gen → HTTP 400**. Drop the param.
- **Video `duration` field is ignored**. Always send `num_frames` + `frame_rate` explicitly. At 24fps: 5s=121, 10s=241, 15s=361, 18s=425 (all `8n+1`, ≤441).
- **Video `image` rejects localhost/private URLs** — always send a base64 data URL (`data:image/<fmt>;base64,...`). See quirks #6/#7. This bites every local-first pipeline that tries to expose a local FastAPI mount and serve `http://127.0.0.1:PORT/uploads/...` — that pattern 400s every time.
- **Video `num_frames` cap is per-resolution** — 1080p tops out at 241 frames (~10s @ 24fps); 720p/480p accept the full 441. Clamp before sending. See quirks #8.
- **Video `frame_rate` should always be emitted (default 24)** — don't make it optional. Without it the server falls back to its own default and the render duration becomes unpredictable. Regression to watch for: refactoring the provider and accidentally dropping the always-emit-frame_rate default.
- **Video rate limit is 6 req/min** (not 1 req/min as older comments implied). Same `429 rate_limit_exceeded` response. Pipe the entire pipeline through a `Semaphore(1)` and fail fast on first 429 with `retry_after_s=60`.
- **Video POST timeout scales with body size — per-call override required.** The `video_create` POST hardcoded `aiohttp.ClientTimeout(total=60)` works for file-path image bodies (~5KB) but silently times out on base64 data-URL bodies (~10MB after a near-8MB upload). When the same `VideoService.generate` is invoked from both pipeline (small body) and manual/upload (large body) paths, only the large-body path fails with `TimeoutError` after exactly 60s — the two paths look identical in code review. **Fix**: thread `request_timeout_s` as a per-call parameter through `VideoService.generate` → `provider.video_create` (default 60, override at large-body callsites with 300s). Don't globally bump the default — that makes small-body paths slower on failure. See `gen-api-integration` skill "Per-call HTTP timeout for variable body sizes" pitfall for the class-level pattern (applies to any provider with variable-size bodies, not just Agnes). Surfaced 2026-08-09 from `ManualVideo.tsx:105 → 500` screenshot on AIGC_agent.
- **Patch tool adds 4 extra leading spaces** when `new_string` lines have deeper indent than `old_string`. For ≥3-occurrence bulk edits in JS/TSX, prefer `execute_code` with read/write_file. Symptom: `node --check` fails "Unexpected token '}'".
- **Don't drop old model from Settings.tsx dropdown** without explicit user OK — keep dual-options so they can compare or roll back. (This is the user's preference; see memory.)
- **Some test failures are pre-existing, not from your swap.** AIGC_agent's `test_storyboard.py` fails with `sqlite3.ProgrammingError: You can only execute one statement at a time` from `persistence.py` (multi-PRAGMA bug, see `gen-api-integration` skill). Confirm `test_agnes_provider.py` (or equivalent provider test) passes separately before blaming the swap.
- **Agnes image response returns URL by default**; if you need b64_json, do NOT pass `response_format` — there's no path to it currently (the param is rejected). Download from URL instead.

## Cross-project context

The user maintains TWO Agnes projects:
- **ImageGen** at `C:\Users\you\D\ImageGen\` — Cloudflare Worker, image-only SaaS (no LLM/video pipeline), admin auth via `ADMIN_API_TOKEN` env. Frontend drops images via Canvas→JPEG@0.85 before D1 ingest to stay under TEXT size limits.
- **AIGC_agent** at `D:\AI_work\AIGC_agent\` — kairos-aigc-factory, full pipeline: script_gen → asset_consolidation → storyboard → image_gen → video_gen. Uses SQLite (`data/aigc.db`), not D1.

When the user says "把 llm 换成 X" without specifying project, default to AIGC_agent (it's the one with the full pipeline + llm model), but ask if ambiguous.

## Related skills

- `frontend-patching` — for surgical edits on the ImageGen single-file SPA
- `multi-file-spa` — for AIGC_agent's `web/` (Vite + React + TypeScript)
- `cloudflare-deployment` — for ImageGen Worker deploys
- `gen-api-integration` — class-level patterns for ANY third-party generation API: probe-then-verify workflow, constraint translation (duration → num_frames), the per-(resolution, ratio) cap-table pattern, and the empirical `references/agnes-video-caps.md`. The class-level skill is where the general "localhost URL rejected, always send base64" lesson lives; this skill is where Agnes-specific field names and quirks live.