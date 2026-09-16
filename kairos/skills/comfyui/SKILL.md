---
name: "comfyui"
description: "Generate images, video, and audio with ComfyUI — install, launch, manage nodes/models, run workflows with parameter injection. Uses the official comfy-cli for lifecycle and direct REST/WebSocket API f"
priority: 0.5
version: "5.1.0"
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\creative\\comfyui\\SKILL.md"
---
# ComfyUI

Generate images, video, audio, and 3D content through ComfyUI using the
official `comfy-cli` for setup/lifecycle and direct REST/WebSocket API
for workflow execution.

## What's in this skill

**Reference docs (`references/`):**

- `official-cli.md` — every `comfy ...` command, with flags
- `rest-api.md` — REST + WebSocket endpoints (local + cloud), payload schemas
- `workflow-format.md` — API-format JSON, common node types, param mapping
- `flux-klein-face-bias.md` — why Flux.2 Klein 4B/9B defaults to Western
  faces (training data bias, NOT a LoRA), plus prompt/LoRA/alternative-model
  fixes. Load this when user asks about Flux Klein and Asian/Chinese faces.
- `template-integrity.md` — converting `comfyui-workflow-templates` from
  editor format to API format: Reroute bypass, dotted dynamic-input keys
  (`values.a`, `resize_type.width`), Cloud quirks (302 redirect, 1 concurrent
  free-tier job, 1080p VRAM ceiling), Discord-compatible ffmpeg stitch.
  Authored by [@purzbeats](https://github.com/purzbeats). Load this whenever
  you're starting from an official template.
- `flux-klein-lora-guide.md` — finding, evaluating, downloading, and setting
  up prompt-based auto-loading of LoRAs for FLUX.2 Klein 4B. Covers: HF mirror
  for domestic access (国内源), file size checking, 4B vs 9B compatibility,
  verified LoRA list with sizes, 150MB size constraint, and 4 approaches to
  auto-load LoRAs by style keyword in ComfyUI. Load this when the user asks
  about Klein LoRAs, anime style LoRAs, auto-loading LoRAs from prompt
  keywords, or LoRA compatibility.
- `standalone-flux-generation.md` — patterns for standalone Flux/SD image
  generators (non-ComfyUI): FLUX.2 Klein 4B architecture constants, INT8
  dequantization, weight name conversion, Gradio lazy loading, VRAM-aware
  UI, OOM fallback chains, memory optimization order, base pipeline caching,
  Windows .bat launcher patterns. Load when building or optimizing a
  direct-diffusers Flux pipeline outside ComfyUI (e.g. KairosVideo project).
- `distribution-packages.md` — comparison of pre-configured ComfyUI integration
  packages (landon2022 all-in-one, 秋叶绘世启动器, YanWenKun portable, EZi Desktop,
  official Desktop/Portable, comfy-cli). Load when user asks which ComfyUI to install
  or wants a ready-to-use distribution.
- `gaia-video-factory-troubleshooting.md` — debugging GaiaVideoFactory /
  Wan2GP issues: reference prompts failing to write (GenerateRefPrompts,
  Invalid JSON after repair, fix by upgrading text model from qwen-plus to
  qwen-max), and missing model weight files (ltx-2.3-22b_audio_vae.safetensors,
  ltx-2.3-22b_vocoder.safetensors — download from hf-mirror).

**Scripts (`scripts/`):**

| Script | Purpose |
|--------|---------|
| `_common.py` | Shared HTTP, cloud routing, node catalogs (don't run directly) |
| `hardware_check.py` | Probe GPU/VRAM/disk → recommend local vs Comfy Cloud |
| `comfyui_setup.sh` | Hardware check + comfy-cli + ComfyUI install + launch + verify |
| `extract_schema.py` | Read a workflow → list controllable params + model deps |
| `check_deps.py` | Check workflow against running server → list missing nodes/models |
| `auto_fix_deps.py` | Run check_deps then `comfy node install` / `comfy model download` |
| `run_workflow.py` | Inject params, submit, monitor, download outputs (HTTP or WS) |
| `run_batch.py` | Submit a workflow N times with sweeps, parallel up to your tier |
| `ws_monitor.py` | Real-time WebSocket viewer for executing jobs (live progress) |
| `health_check.py` | Verification checklist runner — comfy-cli + server + models + smoke test |
| `fetch_logs.py` | Pull traceback / status messages for a given prompt_id |

**Example workflows (`workflows/`):** SD 1.5, SDXL, Flux Dev, SDXL img2img,
SDXL inpaint, ESRGAN upscale, AnimateDiff video, Wan T2V. See
`workflows/README.md`.

## When to Use

- User asks to generate images with Stable Diffusion, SDXL, Flux, SD3, etc.
- User wants to run a specific ComfyUI workflow file
- User wants to chain generative steps (txt2img → upscale → face restore)
- User needs ControlNet, inpainting, img2img, or other advanced pipelines
- User asks to manage ComfyUI queue, check models, or install custom nodes
- User wants video/audio/3D generation via AnimateDiff, Hunyuan, Wan, AudioCraft, etc.
- User asks "which ComfyUI to install", "best integration package", or wants a
  pre-configured distribution — load `references/distribution-packages.md`
- User has a standalone Flux/SD .safetensors checkpoint and wants to build a
  Gradio app without ComfyUI — load `references/standalone-flux-generation.md`
- User asks about FLUX.2 Klein architecture (Qwen3 text encoder, 7680 context
  dim, flow-matching) — load `references/standalone-flux-generation.md`

## Architecture: Two Layers

```
┌─────────────────────────────────────────────────────┐
│ Layer 1: comfy-cli (official lifecycle tool)        │
│   Setup, server lifecycle, custom nodes, models     │
│   → comfy install / launch / stop / node / model    │
└─────────────────────────┬───────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────┐
│ Layer 2: REST/WebSocket API + skill scripts         │
│   Workflow execution, param injection, monitoring   │
│   POST /api/prompt, GET /api/view, WS /ws           │
│   → run_workflow.py, run_batch.py, ws_monitor.py    │
└─────────────────────────────────────────────────────┘
```

**Why two layers?** The official CLI is excellent for installation and server
management but has minimal workflow execution support. The REST/WS API fills
that gap — the scripts handle param injection, execution monitoring, and
output download that the CLI doesn't do.

## Quick Start

### Detect environment

```bash
# What's available?
command -v comfy >/dev/null 2>&1 && echo "comfy-cli: installed"
curl -s http://127.0.0.1:8188/system_stats 2>/dev/null && echo "server: running"

# Can this machine run ComfyUI locally? (GPU/VRAM/disk check)
python3 scripts/hardware_check.py
```

If nothing is installed, see **Setup & Onboarding** below — but always run the
hardware check first.

### One-line health check

```bash
python3 scripts/health_check.py
# → JSON: comfy_cli on PATH? server reachable? at least one checkpoint? smoke-test passes?
```

## Core Workflow

### Step 1: Get a workflow JSON in API format

Workflows must be in API format (each node has `class_type`). They come from:

- ComfyUI web UI → **Workflow → Export (API)** (newer UI) or
  the legacy "Save (API Format)" button (older UI)
- This skill's `workflows/` directory (ready-to-run examples)
- Community downloads (civitai, Reddit, Discord) — usually editor format,
  must be loaded into ComfyUI then re-exported

Editor format (top-level `nodes` and `links` arrays) is **not directly
executable**. The scripts detect this and tell you to re-export.

### Step 2: See what's controllable

```bash
python3 scripts/extract_schema.py workflow_api.json --summary-only
# → {"parameter_count": 12, "has_negative_prompt": true, "has_seed": true, ...}

python3 scripts/extract_schema.py workflow_api.json
# → full schema with parameters, model deps, embedding refs
```

### Step 3: Run with parameters

```bash
# Local (defaults to http://127.0.0.1:8188)
python3 scripts/run_workflow.py \
  --workflow workflow_api.json \
  --args '{"prompt": "a beautiful sunset over mountains", "seed": -1, "steps": 30}' \
  --output-dir ./outputs

# Cloud (export API key once; uses correct /api routing automatically)
export COMFY_CLOUD_API_KEY="comfyui-..."
python3 scripts/run_workflow.py \
  --workflow workflow_api.json \
  --args '{"prompt": "..."}' \
  --host https://cloud.comfy.org \
  --output-dir ./outputs

# Real-time progress via WebSocket (requires `pip install websocket-client`)
python3 scripts/run_workflow.py \
  --workflow flux_dev.json \
  --args '{"prompt": "..."}' \
  --ws

# img2img / inpaint: pass --input-image to upload + reference automatically
python3 scripts/run_workflow.py \
  --workflow sdxl_img2img.json \
  --input-image image=./photo.png \
  --args '{"prompt": "make it watercolor", "denoise": 0.6}'

# Batch / sweep: 8 random seeds, parallel up to cloud tier limit
python3 scripts/run_batch.py \
  --workflow sdxl.json \
  --args '{"prompt": "abstract"}' \
  --count 8 --randomize-seed --parallel 3 \
  --output-dir ./outputs/batch
```

`-1` for `seed` (or omitting it with `--randomize-seed`) generates a fresh
random seed per run.

### Step 4: Present results

The scripts emit JSON to stdout describing every output file:

```json
{
  "status": "success",
  "prompt_id": "abc-123",
  "outputs": [
    {"file": "./outputs/sdxl_00001_.png", "node_id": "9",
     "type": "image", "filename": "sdxl_00001_.png"}
  ]
}
```

## Decision Tree

| User says | Tool | Command |
|-----------|------|---------|
| **Lifecycle (use comfy-cli)** | | |
| "install ComfyUI" | comfy-cli | `bash scripts/comfyui_setup.sh` |
| "start ComfyUI" | comfy-cli | `comfy launch --background` |
| "stop ComfyUI" | comfy-cli | `comfy stop` |
| "install X node" | comfy-cli | `comfy node install <name>` |
| "download X model" | comfy-cli | `comfy model download --url <url> --relative-path models/checkpoints` |
| "list installed models" | comfy-cli | `comfy model list` |
| "list installed nodes" | comfy-cli | `comfy node show installed` |
| **Execution (use scripts)** | | |
| "is everything ready?" | script | `health_check.py` (optionally with `--workflow X --smoke-test`) |
| "what can I change in this workflow?" | script | `extract_schema.py W.json` |
| "check if W's deps are met" | script | `check_deps.py W.json` |
| "fix missing deps" | script | `auto_fix_deps.py W.json` |
| "generate an image" | script | `run_workflow.py --workflow W --args '{...}'` |
| "use this image" (img2img) | script | `run_workflow.py --input-image image=./x.png ...` |
| "8 variations with random seeds" | script | `run_batch.py --count 8 --randomize-seed ...` |
| "show me live progress" | script | `ws_monitor.py --prompt-id <id>` |
| "fetch the error from job X" | script | `fetch_logs.py <prompt_id>` |
| **Direct REST** | | |
| "what's in the queue?" | REST | `curl http://HOST:8188/queue` (local) or `--host https://cloud.comfy.org` |
| "cancel that" | REST | `curl -X POST http://HOST:8188/interrupt` |
| "free GPU memory" | REST | `curl -X POST http://HOST:8188/free` |

## Setup & Onboarding

When a user asks to set up ComfyUI, **the FIRST thing to do is ask whether
they want Comfy Cloud (hosted, zero install, API key) or Local (install
ComfyUI on their machine)**. Don't start running install commands or hardware
checks until they've answered.

**Official docs:** https://docs.comfy.org/installation
**CLI docs:** https://docs.comfy.org/comfy-cli/getting-started
**Cloud docs:** https://docs.comfy.org/get_started/cloud
**Cloud API:** https://docs.comfy.org/development/cloud/overview

### Step 0: Ask Local vs Cloud (ALWAYS FIRST)

Suggested script:

> "Do you want to run ComfyUI locally on your machine, or use Comfy Cloud?

Routing:

- **Cloud** → skip to **Path A**.
- **Local** → run hardware check first, then pick a path from Paths B–E based on the verdict.
- **Unsure** → run the hardware check and let the verdict decide.

### Step 1: Verify Hardware (ONLY if user chose local)

```bash
python3 scripts/hardware_check.py --json
```

| Verdict    | Meaning                                                       | Action |
|------------|---------------------------------------------------------------|--------|
| `ok`       | >=8 GB VRAM (discrete) OR >=32 GB unified (Apple Silicon)       | Local install |
| `marginal` | SD1.5 works; SDXL tight; Flux/video unlikely                  | Local OK for light workflows, else Cloud |
| `cloud`    | No usable GPU, <6 GB VRAM, <16 GB Apple unified               | Switch to Cloud |

For the fully automated path (hardware check → install → launch → verify):

```bash
bash scripts/comfyui_setup.sh
```

### Path A: Comfy Cloud (No Local Install)

For users without a capable GPU or who want zero setup.

**Docs:** https://docs.comfy.org/get_started/cloud

1. Sign up at https://comfy.org/cloud
2. Generate an API key at https://platform.comfy.org/login
3. Set the key:
   ```bash
   export COMFY_CLOUD_API_KEY="comfyui-xxxxxxxxxxxx"
   ```
4. Run workflows:
   ```bash
   python3 scripts/run_workflow.py \
     --workflow workflows/flux_dev_txt2img.json \
     --args '{"prompt": "..."}' \
     --host https://cloud.comfy.org \
     --output-dir ./outputs
   ```

### Path B: ComfyUI Desktop (Windows / macOS)

One-click installer for non-technical users.

**Docs:** https://docs.comfy.org/installation/desktop

### Path C: ComfyUI Portable (Windows Only)

**Docs:** https://docs.comfy.org/installation/comfyui_portable_windows

Download from https://github.com/comfyanonymous/ComfyUI/releases

### Path D: comfy-cli (All Platforms — Recommended for Agents)

**Docs:** https://docs.comfy.org/comfy-cli/getting-started

```bash
# Install comfy-cli
pipx install comfy-cli

# Install ComfyUI
comfy --skip-prompt install --nvidia              # NVIDIA (CUDA)
comfy --skip-prompt install --m-series            # Apple Silicon (MPS)

# Launch
comfy launch --background
```

### Path E: Manual Install (Advanced)

```bash
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI
pip install -r requirements.txt
python main.py
```

### Post-Install: Verify

```bash
python3 scripts/health_check.py
python3 scripts/check_deps.py my_workflow.json
python3 scripts/run_workflow.py \
  --workflow workflows/sd15_txt2img.json \
  --args '{"prompt": "test", "steps": 4}' \
  --output-dir ./test-outputs
```

## Image Upload (img2img / Inpainting)

```bash
python3 scripts/run_workflow.py \
  --workflow workflows/sdxl_img2img.json \
  --input-image image=./photo.png \
  --args '{"prompt": "make it cyberpunk", "denoise": 0.6}'
```

## Cloud Specifics

- **Base URL:** `https://cloud.comfy.org`
- **Auth:** `X-API-Key` header (or `?token=KEY` for WebSocket)
- **Concurrent jobs:** Free/Standard: 1, Creator: 3, Pro: 5

## Queue & System Management

```bash
curl -s http://127.0.0.1:8188/queue
curl -X POST http://127.0.0.1:8188/interrupt
curl -X POST http://127.0.0.1:8188/free -d '{"unload_models": true, "free_memory": true}'
```

## Pitfalls

0. **FLUX.2 Klein ≠ FLUX.1** — Klein uses Qwen3 text encoder (not T5), custom
   flow-matching pipeline (not diffusers FluxPipeline), and 7680 context dim.
   Do NOT use `FluxPipeline.from_pretrained()` for Klein models. See
   `references/standalone-flux-generation.md` for the correct architecture.
1. **API format required** — every script expects API-format workflow JSON.
2. **Server must be running** — all execution requires a live server.
3. **Model names are exact** — case-sensitive, includes file extension.
4. **Missing custom nodes** — "class_type not found" means a required node isn't installed.
5. **Cloud free-tier API limits** — `/api/prompt` returns 403 on free accounts.
6. **Timeout for video/audio workflows** — auto-detected; override with `--timeout 1800`.
7. **Workflow JSON is arbitrary code** — inspect workflows from untrusted sources.
10. **GaiaVideoFactory/Wan2GP** — not a ComfyUI tool, but uses the same model ecosystem. If debugging video generation failures in GaiaVideoFactory, load `references/gaia-video-factory-troubleshooting.md`. Key issues: reference prompts failing to write (upgrade text model from qwen-plus to qwen-max), missing model weight files (audio_vae, vocoder), duplicate shot records across versions, and frame file vs text description mismatches in the database.\n\n## Verification Checklist

## Verification Checklist

- [ ] `hardware_check.py` verdict is `ok` OR user chose Comfy Cloud
- [ ] `comfy --version` works
- [ ] `curl http://HOST:PORT/system_stats` returns JSON
- [ ] `comfy model list` shows at least one checkpoint
- [ ] Workflow JSON is in API format
- [ ] Test run with a small workflow completes; outputs land in `--output-dir`
