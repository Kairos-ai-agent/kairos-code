---
name: "gaia-media-api"
description: "Gaia Video Factory API integration patterns — image/video generation, async polling, model quirks, and reference image handling."
priority: 0.5
imported-from: "agents"
source-path: "agents/skills/media-api/gaia-media-api/SKILL.md"
---
# Gaia Media API Integration

Integration patterns for the Gaia Video Factory API (`https://api.gaiavideofactory.com/v1`). Covers image generation, video generation (Seedance 2.0), and async job lifecycle.

## Base API

Base URL: `https://api.gaiavideofactory.com/v1`
API key required for most operations.

### Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/images/generations` | POST | Sync image generation |
| `/images/generations/async` | POST | Async image generation (returns jobId) |
| `/images/generations/async/:jobId` | GET | Poll async image job status |
| `/v3/contents/generations/tasks` | POST | Submit video generation job |
| `/v3/contents/generations/tasks/:jobId` | GET | Poll video generation job |

## Image Generation

### Sync vs Async

- **Sync mode**: POST to `/images/generations`, response includes image directly (data URL)
- **Async mode (client-poll)**: POST to `/images/generations/async`, response returns `jobId`, client polls `GET /images/generations/async/:jobId` until completed
- **Async mode (server-poll)**: Server submits async job and polls internally, returns final image to client

### Reference Images

- **Single reference**: `payload.image = dataUrlString` (singular field)
- **Multiple references**: `payload.images = [dataUrl1, dataUrl2, ...]` (plural field!)
- ⚠️ **Critical quirk**: For multi-image references with `rebeccaber` route, use field name `images` (plural), NOT `image` (singular with array value). The singular `image` field with array value is silently ignored.
- Reference images should be data URLs (`data:image/...;base64,...`), compressed before submission to reduce payload size
- The `gpt-image2-2k-2` model supports multi-image reference

### Payload Structure

```javascript
{
  model: "gpt-image-2",
  prompt: "description text",
  size: "1280x720",
  n: 1,
  quality: "auto",
  image: "data:..."               // single ref, OR
  images: ["data:...", "data:..."] // multiple refs
}
```

## Video Generation (Seedance 2.0)

### Multipart Form Data

Video generation uses **multipart form data** (NOT JSON). Use `input_reference[]` for reference images:

```
model=seedance-2.0-fast
prompt=...
seconds=6           ← MUST be String type!
style=...
input_reference[]=@/path/to/image1.png
input_reference[]=@/path/to/image2.png
```

### Model Notes

| Model | Status | Notes |
|---|---|---|
| `seedance-2.0-fast` | ✅ Stable | Text-to-video and image-to-video |
| `seedance-2.0-pro` | ✅ Available | Higher quality, slower |
| `grok-imagine-video-1.5-preview` | ✅ Available | Needs reference image |
| `doubao-seedance-2-0-fast-260128` | ❌ 404 | Internal API URL broken |
| `doubao-seedance-2-0-260128` | ❌ 404 | Same upstream 404 issue |

### Seconds Parameter

`seconds` MUST be a **String**, not Number. The API rejects integer values.

### Reference Images

Reference images for video go in `input_reference[]` fields with `role: "reference_image"` in Content-Disposition. At least one reference image required for `grok-imagine-video-1.5-preview`.

## Async Job Polling

Both image and video async jobs follow the same pattern:

1. Submit job → get `jobId`
2. Poll every 2-5s with retries (120-300 attempts)
3. Check status: `"running"` → continue, `"failed"` → abort, `"completed"` → extract result
4. Extract images using recursive search for `b64_json`, `url`, `data` fields

### Server-side Polling

- Start with 2s delay, increase to 5s after first attempt
- 240 attempts = ~20 minute timeout
- On completion, save image to `outputs/image/` directory

### Download URL

If result has `result_url`, strip `/v1` from `config.baseUrl` before constructing download:
```
// Bad:  https://api.gaiavideofactory.com/v1/uploads/...
// Good: https://api.gaiavideofactory.com/uploads/...
```

## Known Pitfalls

1. **Multi-image reference field name**: Use `images` (plural), not `image` (singular with array). The `rebeccaber` route only reads `images`.
2. **doubao-seedance-* models**: All give Gaia internal 404 (POST to `/v3/contents/generations/tasks`). Backend bug, not client-fixable. Use `seedance-2.0-fast`.
3. **seconds MUST be String**: `String(6)` not `6`.
4. **TLS handshake timeouts**: `seedance-2.0-fast` can have transient TLS errors with `uptoken.cc`. Retry.
5. **Model name mapping**: Config `gpt-image-2` → submitted as `gpt-image2-2k-2` (auto-mapped by Gaia).
