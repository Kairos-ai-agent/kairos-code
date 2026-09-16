---
name: "gen-api-integration"
description: "Use when integrating any third-party generation API (image, video, audio, TTS, music). Covers probing actual vs documented limits, multi-layer parameter plumbing, async task polling, constraint transl"
priority: 0.5
version: "1.0.1"
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\software-development\\gen-api-integration\\SKILL.md"
---
# Integrating third-party generation APIs

Most "AI generation" SaaS providers (Agnes, Runway, Pika, Suno, ElevenLabs, etc.) share a common shape:
async task creation → polling → result URL → download. The official docs describe the happy path but **hide the real limits behind server-side normalization and undocumented caps**. This skill captures the patterns that survive contact with the actual server.

## When to use

- Wiring a new image / / video / / audio / / TTS / / music gen provider into a multi-stage pipeline
- A video / image / audio call is "silently wrong" — output doesn't match what the docs promise (wrong dimensions, wrong duration, wrong format)
- Adding a new parameter the docs describe but the integration doesn't yet pass through
- The provider returns 4xx errors and you need to figure out the real ceiling
- The user reports an absurdly long 500 (e.g. 180s of wasted retries) on what looks like a "valid" parameter combination — most often this means a per-(resolution, ratio) cap on the upstream is being hit and the request is doing a full retry loop before the user sees the error. See "Cap-table = 'API surface', not 'internal constant'" pitfall below.
- **The image model keeps rendering a generic Western face / mid-shot / natural lighting regardless of what the script or character brief specifies** — character asset generation pipelines in particular need the three-layer defense (structured extraction + exact-match rule + style-preset guard), see Core Pattern #7 below. Hits this when the brief mentions Asian / non-Western characters, age-specific features, or any visual attribute the image model silently overrides.

## Core patterns

### 1. Probe actual server limits empirically — don't trust the docs blindly

Docs advertise a global `num_frames ≤ 441` rule. Empirically, the server caps 1080p at 241 regardless of aspect ratio. **Discover these by sending deliberately-over-limit requests and parsing the 4xx error body.**

```python
# Try 441 frames; if 4xx, the error body's `max_num_frames` field is the real cap.
body = {"width": 1920, "height": 1088, "num_frames": 441, ...}
async with sess.post(url, json=body, ...) as r:
    if r.status >= 400:
        err = json.loads(await r.text())
        cap = err["data"]["max_num_frames"]   # 241, not 441
```

Build a per-(resolution, ratio) cap table from probe results; clamp in the provider layer before the wire request. See `references/parameter-plumbing-checklist.md` for where this sits in the stack.

### 2. Translate user-facing input to wire format with explicit constraints

User says "5 seconds". Server takes `num_frames` + `frame_rate`. Constraints: `8n+1`, `≤ 441`. Don't send `duration` — most providers (Agnes included) silently ignore it.

```python
def duration_to_num_frames(duration_s: int, frame_rate: int = 24) -> int:
    target = int(round(duration_s * frame_rate))
    target = min(target, 441)
    n = max(0, (target - 1 + 7) // 8)   # ceil(target-1)/8
    return 8 * n + 1                     # smallest 8n+1 ≥ target
```

5s @ 24fps → 121 frames. 18s @ 24fps → 433 (not 441 — 432 → 433 because target=432, not 441).

### 3. Server normalization: send the exact dimension, not what you "want"

Sending `1920x1080` to Agnes for 1080p/16:9 → server normalizes to `1920x1088` and reports `metadata.size_mapping.adjusted: true`. Sending `1920x1088` directly skips the round-trip. Maintain a preset table keyed by (resolution, ratio) that stores the **server-normalized** dimensions:

```python
SIZE_PRESETS = {
    ("1080p", "16:9"): (1920, 1088),  # not 1920x1080
    ("1080p", "9:16"): (1088, 1920),
    ("1080p", "1:1"):  (1080, 1080),
    ...
}
```

### 4. Async task API: poll → status → download from `metadata.url`

Most providers return `{id, status: "queued", seconds, size, metadata: {url, ...}}` on creation. The actual download URL is nested under `metadata.url`, not `video_url` or `url`. Walk a few common shapes — future shape changes shouldn't silently drop the URL:

```python
video_url = (
    final.get("video_url")
    or final.get("url")
    or (final.get("metadata") or {}).get("url")      # most common
    or (final.get("data") or [{}])[0].get("url")
    or ((final.get("data") or [{}])[0].get("metadata") or {}).get("url")
    or (final.get("videos") or [{}])[0].get("url")
)
```

### 5. Reference image upload: ALWAYS send base64 for local-first pipelines

Image-to-video / image-to-image endpoints reject `file://` paths AND `localhost` / private-network URLs (e.g. `http://127.0.0.1:8910/uploads/...`) with HTTP 400 ("must be a public http(s) URL or base64"). The local-HTTP-server pattern that feels natural — "compose the start frame, save it under `/uploads`, expose via FastAPI's `StaticFiles` mount, send that URL" — **always 400s**. The server treats `127.0.0.1`, `localhost`, `192.168.x.x`, `10.x.x.x` etc. as not-public.

**For any local dev / self-hosted pipeline, ALWAYS send a `data:image/<fmt>;base64,...` inline URL.** Read the bytes from disk, base64-encode, prefix with the right MIME, drop it into the request body. There's no size limit documented for this path; 5-10 MB works fine.

If you genuinely need a public URL (e.g. for a CDN-fronted production deploy), use a third-party object store / image host. The local-HTTP path is a trap — don't recommend it.

### 5a. Text-to-video mode (omit image entirely)

Many video endpoints that document themselves as "image-to-video" also accept a text-only request with no reference image. The video model uses the prompt alone to generate both the visual style AND the motion — useful for storyboarding, concept shots, scenes where you don't have a reference, or rapid prototyping without committing to a reference frame yet. Wire it as a separate mode rather than an absent field:

- **Frontend** — segmented control / toggle between i2v and t2v; in t2v mode, the upload UI is disabled and `image_url` is omitted from the POST payload.
- **Wire** — `image` field omitted entirely (not `image: null`, not `image: ""`) — most providers reject empty-string image with a 400. Probe: send a no-image request and check for HTTP 200.
- **Backend** — `Optional[str] = None` default on the request DTO so omitting the field on the wire side naturally maps to Python `None`, which the provider layer interprets as "no image".
- **Speed** — t2v is typically **much faster** than i2v because the body is tiny (no base64 6-10MB blob) and there's no image-to-features extraction step. For Agnes agnes-video-v2.0: i2v ~168s for a 5s clip, t2v ~58s for the same config (~3x faster). Budget accordingly when offering both modes.

Detection heuristic: if the API documentation only describes "image-to-video" but the request schema accepts `image: optional` (some providers expose this as a boolean toggle in the docs, some hide it), it's almost certainly supported. Test with a small request before assuming it's not.

Pattern from `kairos_aigc/api/routes/manual_video.py` `GenerateRequest.mode` field + `web/src/pages/ManualVideo.tsx` `mode: 'i2v' | 't2v'` segmented control since 2026-08-09 (AIGC_agent). Surfaced when the user asked for text-to-video support on the manual page; the backend already supported it (`image_url: Optional[str] = None`), only the frontend + DTO field needed changes.

### 6. Rate-limit the entire pipeline, not just one call

Hard rate limit (Agnes: 6 req/min for video, not the 1 req/min implied by older comments) means the *whole* pipeline must serialize + queue. Wrap the provider with a `Semaphore` and fail fast on the FIRST 429 with a `retry_after_s` hint. Long-lived batch pipelines can retry through 429; user-initiated button-clicks should NOT make the user wait 3 × 70s for a guaranteed failure.

### 6a. Per-call HTTP timeout for variable body sizes (the bloat-body trap)

The same provider function is often called from multiple code paths with **radically different request body sizes** — file-path references (~5 KB body) vs base64 inline uploads (~10 MB body). A single hardcoded `aiohttp.ClientTimeout(total=60)` works for the small-body path and silently times out on the large-body path. **Symptom**: pipeline paths work, manual/upload paths fail with `TimeoutError` after exactly 60 seconds. The two code paths look identical in code review; the failure shows up only on real user traffic where the body happens to be large.

Required pattern: **thread a `request_timeout_s` parameter through the wrapper → service → provider stack** with the hardcoded `60` as default (back-compat), and let the caller override per-path:

```python
# provider layer (default 60, override per call)
async def video_create(self, *, ..., request_timeout_s: int = 60):
    timeout = aiohttp.ClientTimeout(total=request_timeout_s)

# service layer (forwards)
async def generate(self, *, ..., request_timeout_s: int = 60):
    task = await self.provider.video_create(..., request_timeout_s=request_timeout_s)

# caller (manual path only)
await video_service.generate(..., request_timeout_s=300)  # 5min for ~10MB body
```

Heuristic for picking the timeout: `request_timeout_s ≥ (body_size_MB × 30)` as a rough lower bound on a slow link. A 10MB base64 body can take 30-60s just to upload, so 300s gives headroom for slow networks without making small-body paths waste time on retries. Don't globally bump the default — keep it 60s for the common case and override only at the callsites that send large bodies.

Detection: when two code paths use the same service but only one fails with `TimeoutError` after exactly N seconds (N = the hardcoded timeout), it's this bug. Don't paper over it by bumping the default — that makes small-body paths slower on failure. Fix: per-call parameter.

Pattern from `kairos_aigc/aigc/media/video.py` `VideoService.generate(request_timeout_s=...)` + `kairos_aigc/aigc/llm/agnes_provider.py` `video_create(request_timeout_s=...)` + `kairos_aigc/api/routes/manual_video.py` `_generate()` since 2026-08-09 (AIGC_agent) — surfaced from a `500 Internal Server Error` screenshot on `ManualVideo.tsx:105`. The 60s default was fine for pipeline (file-path image, ~5KB body); manual's base64 data URL bloated the body to ~10MB and consistently exceeded 60s on the user's link.

### 7. Three-layer defense for image prompt fidelity

Image models default to a generic Western face / mid-shot / natural lighting when the prompt doesn't lock down specifics. Fix by stacking three constraints — every layer is independently necessary because each one is bypassed differently by different image models.

**Critical extension for scene/prop references: SCENE and PROP assets MUST be character-free.** The three-layer defense above targets character assets (multi-view consistency, ethnicity lock, etc.). For scene and prop assets the danger is the opposite — the image model is happy to populate an "empty kitchen" reference with a chef standing at the counter, or render a "block of cheese" with a mouse holding it. This is **catastrophic** when the video stage composes the start frame by pasting the scene thumbnail + the character reference together: the character appears TWICE (once from the dedicated reference, once baked into the scene image). The downstream video model then sees two characters and either crashes (`invalid_request: too many subjects`) or composites a ghost figure. Fix by stacking a fourth constraint layer specifically for scene/prop negatives: **explicitly forbid every human-related term in the scene/prop negative_prompt** — people, person, human, character, figure, body, hand, arm, leg, face, head, hair, foot, feet, walking person, standing person, person in the background, ghost figure, second character, crowd, group of people, mannequin, statue of a person, anthropomorphic object, talking object. Also explicitly forbid "person holding [prop]" / "hand holding prop" / "person interacting with object" — these are the patterns the model reaches for when a prop needs visual context. Verify with a one-test inspection of a sample scene asset: count the number of human-shaped figures in the rendered image; if > 0, the negative_prompt isn't strong enough. Pattern from `kairos_aigc/aigc/stages/asset_gen.py` `_gen_one(negative_prompt=...)` for scene/prop types since 2026-08-09 (AIGC_agent) — surfaced when a scene reference of "厨房" (kitchen) included a chef, and the start-frame composited the chef ON TOP of the character reference, producing a "ghost figure" bug that took hours to diagnose. The character-side three-layer defense is necessary but not sufficient for scene/prop assets; you need the character-forbidden negative layer too.

1. **Extraction template** (LLM call 1) — require STRUCTURED fields in the LLM's output. For characters: ethnicity + age + facial features + hair + body build + skin tone + distinguishing marks, one English paragraph ≥120 chars. Provide GOOD/BAD examples so the LLM learns the right default (Chinese-name characters → East Asian; vague "a tired man" 10-char descriptions are explicitly bad). For scenes/props: mood + lighting + key props. Reject vague outputs in the prompt itself, not at the image-model side.

2. **Image prompt** (LLM call 2 / final assembly) — explicit "MUST match exactly" + "do NOT substitute a different ethnicity's features" + "all views (front / side / back) must show the SAME face — no inconsistencies" + "no extra accessories beyond what the description states" rules. Don't trust the image model to be consistent across multiple views of the same character — without explicit constraint it drifts.

3. **Style preset** (last guard rail) — every style preset must carry an explicit ethnicity / facial-feature clause. Without this, even a good extraction + good prompt can slip through: some image models weight preset style vocabulary higher than the description, so a "写实" preset that says "natural skin and fabric textures" with NO ethnicity cue will default to a generic Western face. Add the clause as a sentence at the end of every preset's description (not a separate field), and unit-test that `resolve_style()` returns text containing ethnicity/facial/skin keywords for ALL preset labels AND for the fallback path (`resolve_style(unknown_label)` must still include the cue).

Verification: each layer needs its own unit test. A test that only checks the LLM output without checking the prompt content is a refactor magnet — anyone editing the extraction template later won't know they broke the schema. Three tests minimum: extraction prompt contains all 6 schema keywords, image prompt contains "MUST match", every preset passes an ethnicity-cue assertion. Pattern from AIGC_agent `kairos_aigc/aigc/stages/asset_gen.py` + `kairos_aigc/aigc/llm/prompt_templates.py` + `kairos_aigc/aigc/llm/style_presets.py` since 2026-08-09.

## Multi-layer parameter plumbing checklist

Adding a new parameter (e.g. `duration_s`) to a generation API requires plumbing it through EVERY layer:

- [ ] `settings.py` DEFAULTS + UI-list constants (`VIDEO_DURATIONS_S = [3, 5, 10, 18]`)
- [ ] `data/settings.json` (sync the new defaults)
- [ ] `api/routes/settings.py` Pydantic DTO + initialValues defaults
- [ ] `web/src/pages/Settings.tsx` Select/Input form item
- [ ] `provider.video_create(*, new_param, ...)` — accepts and forwards to wire
- [ ] `media/video.py` `VideoService.generate(*, new_param, ...)` — passthrough
- [ ] `stages/video_gen.py` — read from project settings, pass to service
- [ ] `pipeline.py` / `orchestrator.py` — pass settings into the stage
- [ ] `prompt_templates.py` — if it's agent-driven (per-shot), prompt LLM to output it
- [ ] `stages/storyboard.py` — sanitize + persist per-shot overrides
- [ ] `persistence.py` schema migration (add column + `_migrate_add_columns`)
- [ ] Tests: provider wire format + sanitize edge cases + integration end-to-end

**Missing any one layer = silent fall-through to server default** (which is what caused the original 5s lock bug).

## Pitfalls

- **Trusting the docs' "global max"** — server caps are often per-resolution/per-ratio and lower than the docs claim. Probe.
- **The local-HTTP image upload trap** — providers reject `http://127.0.0.1:PORT/...` URLs as "not public". Many local-first pipelines try this pattern (compose start frame → save to disk → expose via FastAPI `StaticFiles` → send URL) and always 400. Default to base64 inline; don't waste time debugging the local HTTP path.
- **Field name drift** — providers name fields inconsistently across image (OpenAI-style `prompt`) and video (Agnes-style `image`, not `image_url`). Always check the request schema against actual accepted field names; don't infer from sister APIs.
- **Circular imports via shared constants** — `api/deps.py` is a popular place to put `ROOT` and other singletons, but it also constructs the global `Orchestrator` which transitively imports the pipeline. Computing `ROOT = Path(__file__).resolve().parents[N]` locally avoids pulling in the whole stack.
- **MiniMax is Anthropic Messages API, not OpenAI** — `https://api.minimaxi.com/anthropic/v1/messages` is the real endpoint. Calling `/v1/chat/completions` returns `404 page not found` with NO diagnostic. The auth header is `x-api-key`, NOT `Authorization: Bearer`. Hermes `config.yaml` misleadingly writes `base_url: https://api.minimaxi.com/v1` but internally `anthropic_adapter.py` rewrites to the Anthropic path. **Any new integration that assumes "it's an OpenAI-compatible endpoint" will silently 404.** See `references/minimax-anthropic-api.md` for the full 5-point integration checklist + curl-verified path table.
- **Patch tool indentation bug** — Hermes's `patch` tool silently indents new content 4 spaces deeper than `old_string` when the lines have mixed indentation (Python dict literals and indentation-aware refactors are the usual victims). Hit it 3+ times in one session on 2026-08-09 (AIGC_agent) — every patch where the new block touched multiple indentation levels produced `IndentationError` despite correct-looking diffs. **Three workarounds, in order of preference:**
  1. **Use `scripts/dedent_after_patch.py`** (best for files >200 lines): a Python script that re-reads the file, locates the over-indented block by content marker, strips 4 leading spaces from any line that starts with 8 spaces in that range, and writes back. The function is small enough to inline-import inside `execute_code` rather than shelling out. The full script lives at `scripts/dedent_after_patch.py` and the signature is `dedent_block(path, start_marker, end_marker, levels=1)`. Pattern from 2026-08-09 session:
     ```python
     lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
     for i in range(start_idx, end_idx):
         if lines[i].startswith("        ") and not lines[i].startswith("         "):
             lines[i] = lines[i][4:]   # 8 spaces → 4
     open(path, "w", encoding="utf-8").write("".join(lines))
     ```
  2. **`write_file` rewrite the whole file** — fine for small files, expensive and error-prone for files >300 lines (you risk typos in unrelated sections).
  3. **Run `python -m py_compile <file>` after every patch on .py files** — catches the bug but doesn't fix it; only useful as a tripwire, not a repair.
- **Sanitize agent output before persisting** — if the parameter is LLM-driven, the model can hallucinate values outside the allowed set (`duration_s: 7` when only [3, 5, 10, 18] are valid). Coerce out-of-range values to `None` so the downstream layer falls back to the project default, instead of crashing the whole pipeline.
- **Don't keep "fallback" options the user didn't ask for** — when replacing X→Y at the user's request, don't keep X as a back-compat option unless explicitly asked. The user has a strong "我只要 X 就只给 X" preference (see memory).
- **Agent-driven parameters should NOT surface as UI controls** — when the pipeline is fully automated (agent decides each shot's duration/aspect/etc. in code), don't expose the same knob in the settings UI just because the parameter exists. The user said it best: "agent是自动分镜" — they want the agent to choose, not to be offered another form field. The UI gets a *project-wide default* for when the agent doesn't fill in a value, but per-shot values belong in the schema + LLM prompt, not in a React form.
- **"User says it failed" ≠ it actually failed** — when troubleshooting a user-reported generation failure with no visible server logs, **check the artifacts FIRST**: does the response file exist on disk? did the server log show POST 200 OK? does the generated mp4 exist in `data/uploads/results/`? On two separate occasions (2026-08-08, AIGC_agent) the user's frontend WebSocket was still pointing at a stale/killed backend process, so the request silently 200'd on the new backend but the browser never refreshed. The server's view: "200 OK, file written". The user's view: "失败". The fix is a browser hard-refresh, not a code fix — confirm before changing code.
- **LLM fenced-JSON with broken content: structural markers don't prove a parseable payload** — LLMs frequently wrap JSON in ```` ```json ... ``` ```` fences but with broken content inside (Chinese-quote nesting like `"订单显示"配送中""`, unmatched braces, trailing commas). Your best-effort `_extract_json` regex matches the `{...}` region but `json.loads` fails on the contents. **Two consequences for fallback logic**: (1) raw text contains your structural markers (e.g. episode numbers) so a naive "markers-found ≥ expected → safe to use raw text" check passes; (2) but the raw text the user sees is the **broken JSON envelope**, not the actual prose content they wanted. Always add a **shape gate** in addition to structural markers: if `raw_text.startswith("```")`, the LLM was attempting JSON — even if markers are present inside, refuse to fall back, raise a `RuntimeError`, and force a retry. Pattern lives in `kairos_aigc/aigc/stages/script_gen.py` `run_script_gen` since 2026-08-09 (AIGC_agent).
- **Dual storage drift** — when the same logical event has TWO write sites (e.g. `update_X_paths()` updates a column AND `save_X()` writes a separate table row), one often gets forgotten in the implementation. The symptom is silent: backend reports success, but the frontend shows a placeholder / incomplete view because one half of the write never happened. Always grep for ALL writes that mention the changed entity after editing one site. Better: collapse to a single `save_X_complete()` that does both writes atomically. Symptom in AIGC_agent (2026-08-09): `videos` table stayed empty across 15 done storyboards because only `storyboards.video_path` was being updated — frontend `<video>` element never rendered and users saw the start-frame AntImage ("拼凑的图片") for months. `save_video()` function existed but had zero callers in the entire codebase; the bug only surfaced when a user explicitly complained about the wrong rendering. Unit-test that ALL writes happen together, not just that the obvious one did.
- **Agent-driven structured outputs: split HARD vs SOFT fields before defaulting** — when an LLM is told to emit N structured fields per item (e.g. 11 v6 cinematography fields per storyboard shot, or 5 scene tags per chapter), **don't blanket-NULL missing fields or blanket-default them all**. Classify each field by what downstream can synthesize from:
  - **HARD fields** (camera focal/height/distance/movement, composition, intent) — downstream can always pick a safe mid-range default (50mm, 胸口高度, 固定机位, 主体居中). Never NULL: a mid-shot is always a valid render choice, and NULL would either crash the pipeline or produce a worse fallback than the default.
  - **SOFT narrative fields** (baseline/acting beats/sound design/cross-shot handoff) — downstream can NOT fabricate these without misrepresenting the story. Keep them NULL when missing; downstream layers should treat NULL as "skip this field, don't try to invent". `passes_to_next` is a half-case: derive it from `baseline` so the next shot has a continuity anchor; on the last shot use a literal sentinel like `"（末镜，无下一镜）"` so downstream can detect end-of-sequence.
  - The function pattern is `_fill_v6_defaults(s, *, is_last) -> dict`: re-sanitize each value, if None → safe default for hard fields, leave None for soft fields. Applied at the persistence layer (BEFORE save), not in the LLM prompt, because (a) it works regardless of which model is calling, (b) re-sanitizes any hallucinated out-of-set values the prompt sanitizer missed, (c) it's deterministic and testable. Pattern from `kairos_aigc/aigc/stages/storyboard.py` `_fill_v6_defaults` since 2026-08-09 (AIGC_agent).
- **Two-table sync trap: write ALL tables, not just the "primary" one** — if the frontend reads from a separate lookup/API table (e.g. `videos` joined by `storyboard_id`) while the backend also writes a redundant denormalized field on the parent row (e.g. `storyboards.video_path`), you MUST update both. Writing only the parent field looks correct in code review (the data is there) but the frontend's `videos.find()` returns nothing → `<video>` element never renders → user sees only the placeholder image ("拼凑的图片"). Bug pattern from 2026-08-09 (AIGC_agent): 15 done storyboards, 0 `videos` rows, frontend showed start-frame AntImage for every shot for weeks. **Detection heuristic**: any time you have (a) a `videos.find(storyboard_id=X)` or similar join in the frontend, AND (b) a `_process_*` success branch that only calls `update_*_paths()` — the `update_*_paths()` call is incomplete. Add a parallel `save_video({...})` (or equivalent lookup-table insert) in the same success branch. Backfill existing data with a one-shot migration script before deploying the fix.
- **Cap-table = "API surface", not "internal constant" — expose it in the presets endpoint AND gate the request at entry** — when you probe a per-(resolution, ratio) cap (e.g. Agnes 1080p caps at 241 frames, ~10s @ 24fps), that knowledge MUST surface in three places or the user gets burned by 3-retry-182s-before-500 latencies:
  1. **Internal constant** — e.g. `DURATION_CAPS = {"16:9": {"720p": 18, "1080p": 10}, ...}` in the route module. Used for the entry gate.
  2. **`/presets` endpoint** — same data surfaced as a JSON field (`{"duration_caps": {...}}`). The frontend `useEffect` reads this and **filters the duration dropdown** so the user can never even pick a combination known to fail (1080p+18s → "18s" option hidden). Don't hardcode the cap in the frontend — let the backend own the source of truth.
  3. **Route entry gate** — before any network I/O / provider call, check `req.duration_s <= cap_for(resolution, ratio)`. If not, `raise HTTPException(400, detail=f"duration_s={...} 超过 {aspect}@{resolution} 的上限 {cap}s（Agnes 服务端会拒绝）。请降到 {cap}s 或更低。")`. **Critical**: gate at the entry, not after the provider retry loop. Without the entry gate, `1080p + 18s = 432 frames > 241 cap` → server returns 400 → VideoService retries 3× (each ~60s aiohttp timeout) → 182s of wasted user time → finally 500. With the gate, the user gets 400 in <50ms with an actionable message.
  - Test it: assert (a) `GET /presets` includes `duration_caps` with the right cap for each (aspect, resolution) pair, (b) `POST /generate` with `duration_s > cap` returns 400 with the cap in the detail, (c) `POST /generate` with `duration_s == cap` succeeds (boundary), (d) `POST /generate` with `duration_s < cap` succeeds.
  - Pattern from `kairos_aigc/api/routes/manual_video.py` `DURATION_CAPS` + presets endpoint + `_generate()` entry gate + `web/src/pages/ManualVideo.tsx` `allowedDurations` filter since 2026-08-09 (AIGC_agent). Surfaced from `ManualVideo.tsx:174 → 500 in 182s` screenshot.
- **Split a multi-responsibility LLM prompt into separate calls** — when one prompt must emit both cinematography fields AND an asset manifest AND scene narration AND dialogue, the per-shot token load explodes (each item carries 11 v6 fields + a full manifest block). Symptom: output JSON fails to fit in `max_tokens` or the model degenerates (truncates the manifest, drops fields). The fix is **two calls**: (1) cinematography/narration pass — output the cinematic items only; (2) asset-extraction pass over the cinema output — small prompt that takes the named entities and emits the asset manifest. Pros: each call's prompt is shorter and more focused, higher per-field completion rate. Cons: extra latency (~1s) and one more LLM call to budget. Implementation rules: (a) the second call's prompt must explicitly require **exact-name reuse** from the first call's output ("names MUST match the storyboards' char_names/scene_name/prop_names verbatim — Stage 2.5 joins on string equality"), or downstream consolidation silently breaks; (b) keep the second call's failure non-fatal — storyboards already saved, just log warning and use empty assets; (c) write a test asserting both `provider.chat.call_count == 2` AND the second call's prompt contains the extraction template's signature phrase (catches accidental re-merging of the manifest into the main pass). Pattern from `kairos_aigc/aigc/stages/storyboard.py` `EXTRACT_ASSETS_FROM_STORYBOARDS` since 2026-08-09 (AIGC_agent).
- **Defensive dict access for subprocess results** — any route that calls a subprocess service (`VideoService.generate`, `LLMProvider.chat`, an upstream API wrapper, an external tool call) gets back a result that may be `None`, may be missing the expected keys, or may have unknown status values. The biggest unhandled-failure source in async FastAPI apps is `result["status"]` — when the result is `None`, `{}`, or any dict missing `status`, this raises `KeyError`, falls through to FastAPI's bare 500 handler with `detail="Internal Server Error"`, and the user gets no actionable information. **Required pattern**: (1) `result_status = (result or {}).get("status")` — never index `result["status"]` directly; (2) handle every documented status (`done` / `failed` / `rate_limited` / `cancelled` / `unknown`) explicitly, even if the current implementation only ever returns `done`; (3) `cancelled` is a **normal control-flow path**, NOT a failure — return 200 with `{"status": "cancelled"}` so the frontend can show a neutral toast; (4) unknown status → 500 with `detail` containing BOTH the status value AND the full result dict (so debugging doesn't require digging through stderr); (5) include `result.get("error")` in the detail if present. Test each branch with `monkeypatch` setting the service to return each variant: `{"status": "failed", "error": "..."}`, `{"status": "cancelled"}`, `{"status": "rate_limited", "retry_after_s": 60}`, `None`, `{}`, `{"error": "no status key"}`, and a real `raise RuntimeError(...)`. Pattern from `kairos_aigc/api/routes/manual_video.py` `_generate()` since 2026-08-09 (AIGC_agent) — the user's "generate 500" screenshot was this exact bug; happy-path tests never hit any of these branches.
- **Asset preflight gate: ensure all reference assets are complete BEFORE the video stage starts, not lazily per-shot** — naive pipelines call `_ensure_refs_for(shot)` from inside the video loop, generating any missing asset JUST before that specific shot is rendered. The trap is twofold: (a) the first 2-3 shots render against a half-generated asset set (later shots may add new assets the first ones never saw → inconsistent style/palette/ethnicity across the assembled video); (b) a top-up failure mid-loop only surfaces on the Nth shot, after N-1 shots have already spent ~140s each on upstream calls — the user waits minutes to discover "shot 7's asset failed, video stage failed". Fix: at the **top of the video stage**, run a full asset pass (the same loop the asset stage already has) as a preflight gate, then assert every canonical asset has its variant image on disk + `status='done'` before entering the per-shot loop. If any asset is incomplete, abort the entire video stage with a single `stage.failed` event listing the missing assets — the user can then run the asset stage again (or check upstream rate limits / model errors). The preflight is cheap (~one DB query) compared to the cost of a half-rendered video. Pattern from `kairos_aigc/aigc/stages/video_gen.py` `run_video_gen()` preflight + `_all_variants_present()` helper + `kairos_aigc/aigc/stages/asset_gen.py` reused as the preflight runner since 2026-08-09 (AIGC_agent) — surfaced from user reports of "storyboard 5 looks totally different from storyboard 1" + "video stage hangs for 10 minutes then fails halfway".
- **Cross-shot continuity via live-computed prompt context (not stored schema fields)** — multi-shot video projects cut together cleanly only if every shot knows about its neighbors (wardrobe, lighting, character identity, scene state). Naive approach: store `prev_shot_summary` / `next_shot_summary` columns on the storyboard row, populated when the storyboard stage writes the row. Better approach: **compute continuity LIVE inside the video stage** from `persistence.list_storyboards(pid)` — group by `scene_name` so prev/next refers to neighbours in the SAME scene (not across scenes with totally different lighting), then prepend `Continuity: Shot X of Y in this scene; previous shot: <summary>; next shot: <summary>` to every video prompt. Why live-compute over stored fields: (a) no schema migration if you add the feature later; (b) if the storyboard stage is rerun (e.g. user regenerates one episode), the stored continuity would be stale until the next full storyboard pass — live-compute is always fresh; (c) the summary string itself is just `scene + composition + action` joined and truncated to ~140 chars, so the cost is one DB query + a few string concats per shot. The `~140 chars` cap matters: with 5-8 shots per scene, padding every shot's prompt with a full prev/next block can balloon the prompt by ~30%, hitting upstream `max_tokens` or causing subtle token-limit truncations. Pattern from `kairos_aigc/aigc/stages/video_gen.py` `_compose_video_prompt_with_context()` + `_summarize_for_continuity()` since 2026-08-09 (AIGC_agent) — surfaced after the user reported "every shot looks like a stand-alone clip" and cuts broke wardrobe/lighting. The live-compute approach is also defensible against the storyboard stage being a black box — you don't need to instrument it to make the video stage correct.
- **Persist tracebacks to disk in daemon-mode FastAPI services** — services launched via `start_silent.bat /min`, `nohup ... &`, or any background daemon send stderr to a window the user never sees. When an unhandled exception happens, the traceback vanishes — leaving the user with a bare `Internal Server Error` 500 and no way to diagnose. Required pattern at every route's outer handler:
  ```python
  try:
      return await _generate(req, request)
  except HTTPException:
      raise
  except Exception as e:
      import traceback
      try:
          tb_path = ROOT / "data" / "logs" / f"{module_name}_errors.log"
          tb_path.parent.mkdir(parents=True, exist_ok=True)
          with open(tb_path, "a", encoding="utf-8") as f:
              f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} "
                      f"{module_name} unhandled: {type(e).__name__}: {e}\n")
              f.write(traceback.format_exc())
      except Exception:
          pass  # never let logging failure mask the original error
      logger.exception(f"{module_name} failed: %s", e)
      raise
  ```
  Critical detail: `tb_path.parent.mkdir(parents=True, exist_ok=True)` BEFORE `open()` — without mkdir the write silently fails and the traceback is still lost. Also wrap the file write in its own try/except so a disk-full / permission-denied never masks the original exception. Test that the log file appears after a fault injection, with the traceback text inside. Pattern from `kairos_aigc/api/routes/manual_video.py` `generate()` handler since 2026-08-09 (AIGC_agent) — surfaced from the same "500 with no detail" screenshot, because the user's `start_silent.bat /min` startup swallowed all stderr.
- **Multi-endpoint diagnostic + reproduction ladder: when "X is broken, Y works" on endpoints that share code, the bug is in the INPUT dimension, not the code path** — combine two related lessons. **Diagnostic side**: when sibling endpoints share code and one fails while the other succeeds, the trap is to assume the failing endpoint has a unique bug. Usually the code is identical and the bug is in an input dimension the two callers pass differently. List every input the shared code takes, then for each check whether the failing caller passes a value in a regime the success caller doesn't. Common input-dimension mismatches: body size (file-path vs base64 data URL), value scale (10 vs 10000 elements), shape (path vs URL), locale (English vs Chinese), optional vs required fields. The fix is almost never "rewrite the failing path" — it's "parameterize the shared code so both callers pass a value appropriate to their input regime". **Reproduction-ladder side**: when a happy-path in-process test passes but the user reports breakage, the bug lives in a layer your test didn't exercise. The four layers, in order from cheapest to most realistic: (1) **in-process function call** — bypasses HTTP, catches pure-logic bugs. (2) **FastAPI `TestClient`** — runs through FastAPI's middleware in-process, catches routing / validation bugs. (3) **live HTTP** — `urllib.request` or `curl` against the actual server, catches process-level bugs (stdout buffering, real aiohttp sockets, daemonization). (4) **vite proxy / nginx / browser bundle** — exactly what the browser does, catches intercepting layers. **When the test reproduces at one layer but not the next, the bug is in the layer you just crossed.** Don't stop at layer 1 because "it works locally" — the user's failure often lives in layers 2-4, including in places the in-process test doesn't see. Joint pattern from AIGC_agent 2026-08-09: user reported `api/manual-video/generate → 500`; layers 1+2 both succeeded, layer 3 (live HTTP) revealed a 60s `TimeoutError` on the 4.9MB base64 body that layers 1+2 missed entirely because they never sent a body that large. Diagnostic test that proves you've found the dimension: hit the failing endpoint with a synthetically-small body (mimicking the working endpoint's input regime) → succeeds; same endpoint with synthetically-large body → fails at exactly the same `TimeoutError` signature. If failure scales with input size, you've found the dimension.
  - **Layer-2 gotcha: `starlette TestClient` re-raises unhandled exceptions as Python exceptions instead of returning HTTP 500.** TestClient's `raise_server_exceptions=True` (default) propagates unhandled exceptions out of `client.post()` — your test fails with a `RuntimeError` traceback, not with `r.status_code == 500`. This means: (a) you CAN'T use TestClient to assert "this route returns 500 with detail X" for unhandled-exception paths — the test will see the exception itself; (b) the persistent-traceback handler is therefore not exercisable via TestClient alone. Workaround for testing the unhandled-error path: invoke the inner function (`asyncio.run(mv_module._generate(req, request=None))`) directly, asserting both that the exception propagates AND that the outer handler's persistent log gets written by simulating the handler's `try/except` block in the test. For the 200 / 400 / explicit-HTTPException paths TestClient works fine. Gotcha surfaced 2026-08-09 (AIGC_agent) while testing the traceback-persistence handler — required a workaround that pattern from `tests/test_manual_video.py` `test_generate_unhandled_exception_persists_traceback` since then.
  - **Layer-4 red herring: vite proxy (or any simple HTTP reverse proxy) is usually NOT the bottleneck** even when a long-running backend endpoint is being proxied. `http-proxy` (which vite uses) defaults to `timeout: 0` (no socket timeout) and `proxyTimeout: 0` (no request timeout); a slow backend just queues on the proxy's connection pool with no extra delay. Vite-specific gotchas: `changeOrigin: true` (without it, `Host:` header mismatch can confuse some upstreams), and vite's `server.proxy` config does NOT support a `timeout` field directly — there's no built-in way to bound the proxy, so don't waste time adding one. Before assuming the proxy is the problem, verify by hitting the backend directly (port 8910, no vite involved) with the same payload — if it succeeds at the same latency, the proxy is transparent. Pattern from AIGC_agent 2026-08-09: spent a full diagnostic round chasing a "vite proxy timeout" hypothesis that turned out to be wrong; the real bug was 1080p+18s num_frames cap, visible at the backend layer. Always verify with a direct-backend probe before suspecting an intermediate layer.
- **Client-side Canvas-compress before base64 upload (ImageGen pattern)** — for any frontend that uploads an image, base64-encodes it, and sends the data URL to a backend which then forwards the data URL inline to an upstream API (e.g. image-to-video endpoints that reject `localhost` URLs), the source image size is the bottleneck end-to-end. A 4-8MB source image inflates to 6-10MB base64; that's borderline the upstream's aiohttp timeout, may exceed the upload-size hard limit, and bloats every intermediate body (proxy, backend, upstream POST). **Default to compressing client-side** before any encoding, regardless of the source size — the cost is one or two Canvas round-trips, the savings are ~10x on typical user uploads. The compress function pattern:
  ```ts
  async function compressImage(file: File): Promise<File> {
    // Pass through tiny files without burning CPU.
    if (file.size <= TARGET_BYTES / 2) return file;
    // Stage 1: downscale longest dim to MAX_DIM (e.g. 1280px) preserving aspect.
    // Stage 2: encode as JPEG @ 0.85; if still over TARGET (1MB), re-encode at 60% scale.
    return new File([blob], file.name.replace(/\.[^.]+$/, ".jpg"), { type: "image/jpeg" });
  }
  ```
  Send a tiny "compressed" status note back to the user (`参考图已上传（已自动压缩 4949KB → 500KB）`) so they understand the quality change. Mirror the `references/agnes-video-v2.md` and the 1280px / JPEG 0.85 / 1MB target as the AIGC_agent default. The pattern is fully generalizable — any frontend that ships images as base64 to a backend that then forwards them should compress first. Pattern from `web/src/pages/ManualVideo.tsx` `compressImage()` since 2026-08-09 (AIGC_agent) — surfaced because the user explicitly asked "默认稍微压缩下" after seeing a large body cause repeated 500s; the fix is one function and ~30 lines of code, and it eliminates the class of bug entirely.

## Verification

After any parameter-plumbing change:
1. `python -m py_compile <file>` on every touched .py
2. Probe the actual API end-to-end with the new param: HTTP 200 + expected `seconds`/`size` echoed in the create body
3. Hit the 4xx case (over-limit request) → confirm the error body has the field you need to clamp on
4. Run the unit tests; assert the wire body shape matches what you intended (mock aiohttp session and capture the JSON)

## Per-provider reference banks (consolidated umbrella)

This is now the umbrella for **integrating third-party generation APIs** (image / video / audio / TTS / music). The provider-specific prompt & integration knowledge below was consolidated here from narrow siblings — each full body is preserved under `references/<name>/`:

- **Agnes AI unified API** — LLM + image + async video at `apihub.agnes-ai.com/v1`; agnes-* model strings spread across the ImageGen & AIGC_agent projects (settings.json + Python DEFAULTS + Pydantic + stage fallbacks + frontend dropdowns); quirks: image `response_format` rejected, video `duration` ignored, async-poll-404 pattern. → `references/agnes-ai-api/`
- **Agnes agnes-video-v2.0 prompt writing** — `prompt` + `negative_prompt` t2v/i2v conventions for the AIGC_agent manual-video flow. → `references/agnes-video-prompt/`
- **MiniMax H3 (海螺 H3) video prompt writing** — instruction-following + shot-breakdown + asset-annotation style, distinct from Seedance/Grok. → `references/minimax-h3-prompt/`
- **Xiaomi MiMo V2.5 TTS** — chat-completions endpoint (NOT `/v1/audio/speech`), `api-key` header (NOT Bearer), the 3 models (standard / voice-design / voice-clone), voice list & pitfalls. → `references/xiaomi-mimo-tts/`

## References

- `references/agnes-video-v2.md` — Agnes Video V2.0 specific notes
- `references/agnes-video-caps.md` — Empirical num_frames cap table per (resolution, ratio) probed live 2026-08-08. The 1080p cap (241, ~10s) is lower than the docs' global 441 — server rejects with `data.max_num_frames`. Re-probe with `scripts/probe_video_caps.py` before relying on these in production.
- `references/minimax-anthropic-api.md` — MiniMax LLM is Anthropic Messages API compatible (`/anthropic/v1/messages` + `x-api-key`), NOT OpenAI Chat Completions. Calling `/v1/chat/completions` returns 404 with NO diagnostic. Required 5-point checklist for integration; trap surfaced 2026-08-14 when defaulting to OpenAI-style fetch in a browser-side storyboard previz tool. Includes curl-verified path table.
- `references/parameter-plumbing-checklist.md` — expanded version of the checklist above with the exact line locations for a Kairos-AIGC-style pipeline
- `scripts/dedent_after_patch.py` — repair utility for the "patch tool over-indented my edit" bug (5+ occurrences in one session on 2026-08-09). Strips N levels of 4-space indent from a marker-delimited range. Inline-import the `dedent_block(path, start_marker, end_marker, levels=1)` function rather than shelling out when working inside `execute_code`.
- `templates/cap_table_expose.py` — 3-layer discipline template for exposing per-(resolution, ratio) cap tables: (1) internal Python constant, (2) `/presets` endpoint field, (3) entry gate that raises HTTPException(400) with the cap in the `detail`, plus the frontend `useEffect` that filters the dropdown. Required when the cap is non-obvious to users (e.g. 1080p caps at 10s not 18s).