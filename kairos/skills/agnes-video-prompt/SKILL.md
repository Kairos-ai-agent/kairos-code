---
name: "agnes-video-prompt"
description: "Write production-ready prompts for the AIGC_agent project's Agnes agnes-video-v2.0 video model. Use when the user asks for an \"18s video prompt\", \"文生视频\", \"图生视频\", \"negative_prompt\", \"写实视频\", \"动漫视频\", or"
priority: 0.5
version: "1.0.0"
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\software-development\\gen-api-integration\\references\\agnes-video-prompt\\SKILL.md"
---
# Agnes agnes-video-v2.0 video prompt writing

The AIGC_agent project uses **Agnes agnes-video-v2.0** for one-shot video generation (the manual-video flow in `kairos_aigc/api/routes/manual_video.py`). This is **not** MiniMax H3 — different model, different prompt conventions. The two API content fields are:

- `prompt` (str) — positive description
- `negative_prompt` (str, optional) — what to suppress (Agnes supports this natively, unlike many other video models)

The user wants **exhaustive** negative_prompts (~150-250 tokens, all 6 categories). Asking for "详细负面提示词" is the default expectation, not a special request.

## Positive prompt structure (two parts in one string)

### Part A — 7-element schema (one tight paragraph)

Cover all 7 in one short paragraph. Use English even for Chinese-context content (Agnes video models respond better to English prompts):

1. **Subject** — who/what (with age, ethnicity, build, distinguishing marks for characters)
2. **Place** — where (specific location, lighting, weather, time of day)
3. **Action** — what's happening (concrete verbs, not metaphors)
4. **Time/rhythm** — duration + pacing (e.g. "18 seconds, 3 beats of 6s each")
5. **Lens/camera** — focal length, aperture, movement (or locked)
6. **Style/mood** — visual reference (e.g. "photorealistic, indie teen-drama, 35mm film grain")
7. **No** — what NOT to include in positive (e.g. "no text, no music, no voiceover")

### Part B — Beat-by-beat timing (for ≥10s sequences)

For long sequences, break action into labeled beats keyed by timecode:
```
0-6s: BEAT 1 — <one-line action + camera + audio>
6-12s: BEAT 2 — <...>
12-18s: BEAT 3 — <...>
```

For ≤5s, skip Part B. Use "natural descriptors in chronological order" — the model handles short sequences without explicit beat labeling.

## Three generation modes

| Mode | When to use | Image field |
|------|-------------|-------------|
| **t2v** (text-to-video) | No reference image. Default for one-shot. ~58s for 720p 3s. **Faster because no base64 body.** | Omit entirely |
| **i2v** (image-to-video) | Have a starting frame. Base64 data URL inline. ~170s for 4.9MB. | Required (data: URL or HTTP) |
| **keyframe** | Want to control first AND last frame. Two image fields. | First + Last |

For the AIGC_agent manual-video flow, **t2v is the simplest path**. Don't over-engineer with i2v unless the user wants a specific visual anchor.

## Model limits (probed live, source of truth = `DURATION_CAPS` in `kairos_aigc/api/routes/manual_video.py`)

| Resolution | Max num_frames | Effective max duration @ 24fps |
|------------|----------------|----------------------------------|
| 720p (any aspect) | 441 | 18s |
| 1080p (any aspect) | 241 | **10s** |
| 1K 1:1 | 441 | 18s |
| 2K 1:1 | 241 | 10s |

**1080p + 18s fails** because 18s × 24fps = 432 frames > 241 cap. The user will see Agnes return 400 "num_frames exceeds max frames" after 60s+ of retries if the duration_caps gate is bypassed. Always use 720p (or below) for 18s.

## Known failure modes

- **Character identity drift at 18s+ in 2-character scenes** (verified 2026-08-09). Hair length/color/age drifts after ~6-7s into the sequence. Riley's long hair shortened mid-video; Kai's short hair stayed stable. **For 2+ characters with different gender presentations, recommend 10s.** 18s works for single-character or static scenes.
- **Agnes rejects `image: ""`** — pass `data:image/...;base64,...` for inline images, or HTTP(S) URL. Empty string causes 400 "image must be a public http(s) URL or valid base64 image data".
- **T2V with 1×1 PNG fails** — Agnes rejects sub-128×128 images as "too small to animate". Use at least 1024×1024 for i2v.
- **Agnes silent mode drift in long sequences** — model sometimes injects background music or ambient sound into t2v outputs even when prompt says "silent". Negative_prompt `ambient music, sound effects, background music, score, foley` helps but isn't 100%.

## Negative prompt structure (6 categories, ~150-250 tokens)

See `references/negative-prompt-blocks.md` for the full categorized template with example phrasings. Quick overview of the 6 categories (in this order):

1. **Text artifacts** — text, watermark, logo, signature, subtitle, UI, frame, border, vignette, split-screen
2. **Image artifacts** — blur, jitter, flicker, lens flare, banding, compression artifacts
3. **Anatomy** (if humans) — extra fingers, mutated hands, asymmetric face, uncanny valley
4. **Content safety** (project-specific) — weapons, blood, violence, school shooting, sexualization, underage
5. **Character consistency** — haircut, hair length change, character swap, gender swap, age drift
6. **Scene coherence** — random extra characters/animals, off-style rendering, brand logos, dialogue

The user verifies the negative_prompt by *reading it back* — they want it long and explicit, not a one-liner. Default to ~150-250 tokens.

## Worked examples

See `references/example-prompts.md` for two complete examples built for this user:
- **Anime t2v** (18s, 3-beat cheese-trap scene with 米米/大橘 IP)
- **Realistic i2v/t2v** (18s, 3-beat American high-school argument with two 15-year-old students)

Both show the full 7-element + beat + 6-category negative structure.

## See also

- `references/negative-prompt-blocks.md` — categorized pre-built negative prompt blocks (6 categories, each ~20-30 example phrasings)
- `references/example-prompts.md` — two complete worked examples (anime + realistic)
- `kairos_aigc/api/routes/manual_video.py` — backend route, contains `DURATION_CAPS` source of truth
- skill `minimax-h3-prompt` — for a DIFFERENT model (MiniMax H3), do not load this skill when the user is on Agnes
- skill `gen-api-integration` — backend plumbing pitfalls (timeouts, frame caps, local-HTTP image upload trap, persistence + traceback patterns)
