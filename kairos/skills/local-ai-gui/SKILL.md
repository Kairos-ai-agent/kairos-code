---
name: "local-ai-gui"
description: "Package local AI models (Flux, SD, LLMs) as standalone GUI apps with Gradio. Covers Windows setup, GPU memory optimization, China mirror config, batch launcher scripts, and smart environment bootstrap"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\software-development\\pyinstaller-desktop-packaging\\references\\local-ai-gui\\SKILL.md"
---
# Local AI Model GUI Packaging

Build standalone desktop GUI apps that load local AI models (diffusion, LLM, etc.) with Gradio UI. Optimized for Windows + low VRAM (6GB) + China GFW.

## Architecture

```
project/
├── launch.bat       # Entry point (finds Python, calls setup.py, then main.py)
├── setup.py         # Smart env bootstrapper (scans drives, mirrors, venv)
├── main.py          # Gradio app + model loading + inference
├── requirements.txt # Dependencies (torch listed separately)
├── models/          # Model files (.safetensors, .gguf, etc.)
├── outputs/         # Generated outputs
└── venv/            # Virtual environment (created by setup.py)
```

## launch.bat — Critical Rules

1. **NO Chinese characters in .bat files** — cmd.exe misinterprets them as commands. All output text must be ASCII-only.
2. **End with `pause`** — prevents flash-close on error. Add `pause` after every error path too.
3. **Find Python robustly** — check `where python`, `where py`, `%LOCALAPPDATA%\Programs\Python\PythonXXX`, `C:\Program Files\PythonXXX`, conda. Don't assume PATH has it.
4. **Call `setup.py` then `main.py`** — don't do complex logic in batch.

```bat
@echo off
title App Name
cd /d "%~dp0"
:: find python ...
%PYTHON_CMD% setup.py
if %errorlevel% neq 0 ( pause & exit /b 1 )
call venv\Scripts\activate.bat
python main.py
pause
```

## setup.py — Smart Environment Bootstrap

### Phase 1: Find Python (fast)
- `subprocess.run("python -c ...")` + `py -3` (fastest)
- Windows registry (`winreg`) for registered installs
- Shallow drive scan: only known paths, NO recursive glob
- **NEVER use `wmic`** — deprecated on newer Windows. Use `ctypes.windll.kernel32.GetLogicalDrives()` + `os.path.isdir()` for drive enumeration.

### Phase 2: Scan packages (targeted)
- Only query the TARGET venv's packages: `pip list --format=json`
- **NEVER query system Python** — venv is isolated, system packages don't transfer
- Don't scan `site-packages` dirs with glob on all drives — too slow

### Phase 3: Create venv + configure mirrors
```python
# pip.ini with China mirror
pip_ini.write_text(
    "[global]\n"
    "index-url = https://pypi.tuna.tsinghua.edu.cn/simple\n"
    "trusted-host = pypi.tuna.tsinghua.edu.cn\n"
)
```

### Phase 4: Install deps
- Check venv packages (not system!) before installing
- PyTorch: use `--index-url https://download.pytorch.org/whl/cu124` for CUDA
- Fallback mirror: aliyun `https://mirrors.aliyun.com/pypi/simple`

### Phase 5: Find model
- Only search known model directories (ComfyUI/models, etc.), not full disk

## main.py — Model Loading

### HuggingFace Mirror (CRITICAL for China GFW)
```python
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
# MUST be set BEFORE importing diffusers/transformers
```

### 6GB VRAM Optimizations
```python
pipe.enable_model_cpu_offload()  # or enable_sequential_cpu_offload()
pipe.enable_attention_slicing("auto")
pipe.enable_vae_slicing()
pipe.enable_vae_tiling()
```

### FLUX.2 Klein — Do NOT Use diffusers FluxPipeline
**FLUX.2 Klein uses Qwen3 text encoder (not T5), custom flow-matching pipeline,
and 7680 context dim.** The diffusers `FluxPipeline` does NOT work with Klein.

Instead, bundle the inference code from `zhangjinyang/flux-restoration`:
```
flux2/
  model.py          # Flux2 Transformer + Klein4BParams
  autoencoder.py    # Custom AutoEncoder
  text_encoder.py   # Qwen3Embedder (layers [9,18,27] -> 7680)
  utils.py          # pack_latents, get_schedule, load_transformer, load_ae
  inference.py      # sample_image (flow-matching loop)
```

Qwen3 model files must use standard names (`model.safetensors`, `config.json`,
`tokenizer.json`) — rename custom-named files before loading.

### FLUX.2 Klein: Base vs Distilled (CRITICAL — garbage output if wrong)

Klein has TWO model variants with completely different inference:

| Variant | Filename hint | Config key | Steps | CFG | Guidance embed |
|---------|--------------|------------|-------|-----|----------------|
| Distilled (fast) | `flux-2-klein-4b` | `flux.2-klein-4b` | 4 | No | Yes |
| Base (slow, needs CFG) | `flux-2-klein-base-4b` | `flux.2-klein-base-4b` | 20-50 | Yes (batch=2) | No |

**How to identify which variant your checkpoint is:**
```python
from safetensors.torch import load_file
sd = load_file("your_checkpoint.safetensors", device="cpu")
has_guidance = any("guidance" in k for k in sd.keys())
print("Distilled" if has_guidance else "Base")
```

**Distilled path** (single forward pass, guidance_scale passed to transformer):
```python
ctx = text_encoder([prompt])  # batch=1
pred = transformer(x=latents, ctx=ctx, guidance=guidance_value)
```

**Base path** (classifier-free guidance, batch=2: uncond + cond):
```python
ctx = text_encoder(["", prompt])  # batch=2: empty string + real prompt
# Run transformer on batch of 2
pred_uncond, pred_cond = pred.chunk(2)
pred = pred_uncond + guidance_scale * (pred_cond - pred_uncond)
```

**If you use distilled inference on a base model → garbage output**
(random noise fragments, not a coherent image). The base model was trained
with CFG and requires it at inference time.

### INT8 Quantized Checkpoint Dequantization

Quantized checkpoints store weights as `._data` + `._scale` pairs:
```python
for key in sd:
    if key.endswith("._data"):
        base = key[:-6]
        data = sd[key].float()
        scale = sd[f"{base}._scale"].float()
        # CRITICAL: scale must be [out, 1] for correct broadcasting
        if scale.dim() == 1:
            scale = scale.unsqueeze(1)  # [out] -> [out, 1]
        dequantized[base] = (data * scale).to(torch.bfloat16)
```

### Multi-strategy loading (diffusers-based models only)
For single safetensors files, try in order:
1. `Pipeline.from_single_file(path, torch_dtype=torch.float16, local_files_only=True)`
2. `Pipeline.from_pretrained(directory_path)`
3. Manual weight loading (bypasses gated repo detection)

**Strategy 1**: Add `local_files_only=True` to prevent downloading configs from a gated repo.

**Strategy 3 (critical for gated models)**: `from_single_file()` auto-detects the model's origin repo and tries to download configs from it. If that repo is gated (e.g. FLUX.2-dev), it fails with 403 even with `local_files_only=True`. Bypass completely:

```python
from safetensors.torch import load_file
from diffusers import FluxPipeline, FluxTransformer2DModel

# 1. Load transformer config from a PUBLIC repo (e.g. FLUX.1-schnell)
transformer = FluxTransformer2DModel.from_pretrained(
    "black-forest-labs/FLUX.1-schnell",  # public, no auth
    subfolder="transformer",
    torch_dtype=torch.float16,
)

# 2. Load local weights into the transformer
state_dict = load_file("path/to/local.safetensors")
transformer.load_state_dict(state_dict, strict=False)
del state_dict

# 3. Build full pipeline with public repo components
pipe = FluxPipeline.from_pretrained(
    "black-forest-labs/FLUX.1-schnell",
    transformer=transformer,
    torch_dtype=torch.float16,
)
```

This loads CLIP, T5, VAE from the public repo, replaces only the transformer with local weights.

### Real-time progress display for long-running inference

**Problem:** `gr.Progress()` only shows a thin bar at the top. For multi-minute ML inference,
users want to see step count, time per step, ETA, and current phase — all updating live.

**Solution A: Thread + gr.Timer (RECOMMENDED for Gradio 6.x)**

Generator functions in Gradio 6.15+ do NOT reliably stream intermediate yields —
the UI buffers them and may only display the final yield. Even combining threading
with `yield` from the main function fails. Use `gr.Timer` for reliable polling:

```python
# Shared mutable state
_gen_status = {"text": "Idle", "done": False, "result": None}

def _run_generation(prompt, w, h, ...):
    """Background thread: does the actual work, updates _gen_status."""
    try:
        def on_progress(phase, step, total, elapsed, stime):
            if phase == "Generating" and total > 0:
                avg = elapsed / step
                remaining = avg * (total - step)
                _gen_status["text"] = (
                    f"  {phase}: {step}/{total}  |  "
                    f"{stime:.1f}s/step  |  "
                    f"Elapsed: {elapsed:.0f}s  |  ETA: {remaining:.0f}s"
                )
            else:
                _gen_status["text"] = f"  {phase}"

        img, text_encoder, step_times = generate_image_full(
            prompt, ..., progress_callback=on_progress)
        _gen_status["result"] = (images, info)
    except Exception as e:
        _gen_status["result"] = ([], f"Error: {e}")
    finally:
        _gen_status["done"] = True

def generate_image(prompt, ..., progress=gr.Progress()):
    """Normal function — launches thread, returns immediately."""
    global _gen_status
    _gen_status = {"text": "  Starting ...", "done": False, "result": None}

    # Launch work in background thread
    t = threading.Thread(target=_run_generation, args=(...), daemon=True)
    t.start()

    # Return immediately — gr.Timer handles the polling
    return [], "  Starting ...", "  Starting ..."


def tick_status():
    """Called by gr.Timer every 0.5s to refresh status + results."""
    if _gen_status["done"]:
        if _gen_status["result"]:
            images, info = _gen_status["result"]
            return gr.update(value=_gen_status["text"]), images, info
        return gr.update(value=_gen_status["text"]), [], _gen_status["text"]
    # Still running — only update the status text, keep gallery/info unchanged
    return gr.update(value=_gen_status["text"]), gr.update(), gr.update()
```

**UI wiring with gr.Timer:**
```python
gen_btn.click(fn=generate_image, inputs=[...],
    outputs=[gallery, info_output, gen_status])

# Timer: polls status every 0.5s, updates UI components
timer = gr.Timer(value=0.5)
timer.tick(fn=tick_status, outputs=[gen_status, gallery, info_output])
```

**Why gr.Timer > yield-based polling:**
- `yield` from a generator function is unreliable in Gradio 6.x (buffered/dropped)
- `yield` from a normal function raises TypeError
- `gr.Timer.tick()` is Gradio 6.x's native periodic-update mechanism
- Timer runs on the Gradio event loop, so component updates are applied immediately
- Thread does heavy GPU work unblocked; Timer reads shared dict safely

**Pitfall: Thread safety** — Python GIL makes dict reads/writes atomic, so a simple
dict shared between threads is safe. Don't use locks for progress text updates.

**Pitfall: If user clicks Generate while one is running**, reset `_gen_status` first
to avoid stale results from the previous run.

**Pitfall: `gr.update()` is still valid in Gradio 6.x** — use it to update one
component without clobbering others (`gr.update(value=new_text)` keeps everything else).

**Solution B: Generator (works in Gradio 4.x–5.x, unreliable in 6.x)**

```python
def generate_image(prompt, ..., progress=gr.Progress()):
    """Generator: yields (gallery, info, gen_status) for real-time UI updates."""
    # ... same callback setup as above ...
    yield all_images, info, f"  Done!"
```

**Pitfall: Early returns in generator must use `yield` + `return` (not bare `return`):**
```python
# WRONG — bare return in generator raises TypeError
def generate():
    if error:
        return [], "error"

# CORRECT — yield first, then return
def generate():
    if error:
        yield [], "error"
        return
```

### UI wiring (same for both approaches)

```python
gen_status = gr.Textbox(value="Idle", label="Generation Progress", lines=2,
    interactive=False, elem_id="gen_status")

gen_btn.click(fn=generate_image, inputs=[...],
    outputs=[gallery, info_output, gen_status])  # gen_status in outputs!
```

CSS for the status box (monospace, dark background):
```python
app.launch(
    ...,
    css="#gen_status textarea { font-family: monospace; font-size: 13px; background: #1a1a2e; color: #0ff; }",
)
```

### Auto-degrade on OOM
If CUDA OOM, automatically retry with smaller resolution/steps before failing.

## Pitfalls

| Issue | Fix |
|-------|-----|
| .bat flash-close with Chinese | Remove all non-ASCII from .bat, use `pause` |
| `wmic` not found | Use `ctypes.windll.kernel32.GetLogicalDrives()` |
| f-string nested quotes | Use `pip list --format=json` instead of inline f-string generation |
| `pip list` shows system packages in venv check | Only query the target venv's pip, not system python's |
| HuggingFace download timeout in China | Set `HF_ENDPOINT=https://hf-mirror.com` before imports |
| Glob scanning all drives is slow | Only check known paths, no `**` recursive on drives |
| PyTorch CPU-only installed | Uninstall + reinstall with `--index-url` for CUDA wheel |
| `from_single_file` needs config files | `local_files_only=True` + fallback to manual loading |
| `from_single_file` 403 on gated repo | Use manual weight loading (Strategy 3 above) |
| Gradio 6.x `show_download_button` removed | Remove from `gr.Image()` — not a valid param anymore |
| Gradio 6.x `show_copy_button` removed | Remove from `gr.Textbox()` — not a valid param in Gradio 6.x |
| Gradio 6.x `theme`/`css` in Blocks | Move to `app.launch(theme=..., css=...)` in Gradio 6.x |
| PyTorch `[out,in] * [out]` broadcast fail | Use `s.unsqueeze(1)` — broadcasting is right-aligned, `[out,in]*[out]` tries dim=1 match |
| `safetensors.load_file()` crashes on INT8 | Use `safe_open` + `get_tensor` — load_file auto-dequantizes incorrectly |
| Gradio progress shows "1 step" for long inference | Inner function needs callback wired through. Pass `progress` as lambda to sub-function (see pattern below) |
| Windows Task Manager GPU 0% but nvidia-smi 100% | Task Manager only tracks DirectX/WDDM load, NOT CUDA compute. Always use `nvidia-smi` for GPU utilization. On hybrid graphics laptops, Task Manager shows Intel GPU as "GPU 0" and NVIDIA as "GPU 1" — CUDA always uses the NVIDIA card regardless |
| FLUX.2 Klein with diffusers FluxPipeline | Klein uses Qwen3 (not T5), custom pipeline. Bundle flux-restoration code instead |
| Qwen3 model.safetensors not found | `AutoModelForCausalLM` expects `model.safetensors` or `pytorch_model.bin`, not custom names |
| Klein output is garbage (random fragments) | Check if base vs distilled mismatch: base model needs CFG (batch=2). Verify with `any("guidance" in k for k in sd.keys())` |
| Klein 4 steps but base model | Base model needs 20-50 steps with CFG. Distilled uses 4 steps with guidance embed |
| `safetensors.load_file()` fails with "页面文件太小" | Zombie Python processes eating RAM. Kill with `cmd.exe //c "taskkill /PID xxx /F"` (bash `kill` doesn't work on Windows PIDs). Check with `tasklist \| grep python \| awk '{print $1,$5}' \| sort -k2 -rn` |
| Gradio 6.x generator yields don't stream | Use `gr.Timer` + shared dict pattern instead (see Solution A above). `yield` from generators is buffered/dropped in 6.15+ |
| `from_pretrained` pre-touch hangs | `from_pretrained()` handles mmap internally — skip manual pre-touch loops. Only pre-touch for `load_state_dict()` models (see `low-vram-inference` skill) |
