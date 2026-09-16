---
name: "local-torch-inference"
description: "Deploy custom PyTorch AI pipelines on constrained Windows hardware (<=16GB RAM, <=8GB VRAM). Covers safetensors loading, INT8 dequantization, memory management, zombie process cleanup, and GPU perform"
priority: 0.5
version: "1.0.0"
imported-from: "agents"
source-path: "agents/skills/mlops/local-torch-inference/SKILL.md"
---
# Local PyTorch Inference on Constrained Windows Hardware

Deploy custom PyTorch model pipelines (not GGUF/llama.cpp) on Windows machines with limited RAM (<=16GB) and VRAM (<=8GB). Covers model loading, quantization handling, memory optimization, and debugging.

## When to use

- Loading safetensors models (INT8/bf16/fp16) on systems with tight memory
- Debugging "页面文件太小" (os error 1455) or OOM during model load
- Deploying Gradio/Flask web UIs with AI model backends
- Zombie Python process cleanup after crashed sessions
- Choosing between INT8 dequantization approaches
- Running transformer models on low-end GPUs (GTX 16xx, etc.)

## Core pitfalls

### 0. System RAM pressure amplifies EVERYTHING
When system RAM is >90% used, the OS swaps to disk, making ALL operations slower — model loading, CPU↔GPU transfers, tokenization, VAE decode. In one session, transformer-to-GPU transfer went from 41s (normal) to 70s (under pressure). Always check `cat /proc/meminfo | head -3` and kill zombie processes before loading models.

### 1. Zombie Python processes consume memory silently

When a terminal session or background process crashes, child Python processes can become zombies consuming GBs of RAM. This causes subsequent model loads to fail with "页面文件太小" (os error 1455) even when page file size is adequate.

**Diagnosis:**
```bash
# Check all Python processes and their memory
tasklist | grep python | awk '{print $1, $2, $5}' | sort -k3 -rn
```

**Fix (must use cmd.exe, not bash kill):**
```bash
cmd.exe //c "taskkill /PID <pid> /F"
```

**PITFALL:** `kill -9` from MSYS/bash does NOT work for Windows PIDs. Always use `cmd.exe //c "taskkill /PID xxx /F"`.

**Prevention:** Before loading large models, check for and kill zombie processes:
```bash
tasklist | grep python | awk '$5+0 > 500000 {print $2, $5}'
```

### 2. safetensors INT8 dequantization memory peak

Loading INT8 safetensors and dequantizing to bf16 in Python creates a memory peak of ~2-3x the file size because both INT8 data and bf16 result exist simultaneously.

**Example:** 4.4GB INT8 file peaks at ~12GB RSS during dequantization.

**Solutions (in order of preference):**

**A. Pre-dequantize and save as bf16 (recommended for repeated use):**
```python
from safetensors.torch import load_file, save_file
sd = load_file("model_int8.safetensors", device="cpu")
deq = {}
for k in list(sd.keys()):
    if k.endswith("._data"):
        base = k[:-6]
        d = sd.pop(k).to(torch.float32)  # POP to free memory
        s = sd.pop(base + "._scale").to(torch.float32)
        if s.dim() == 1: s = s.unsqueeze(1)
        deq[base] = (d * s).to(torch.bfloat16)
        del d, s
    elif not k.endswith(("._scale", "._input_scale", "._output_scale")):
        deq[k] = sd.pop(k)
save_file(deq, "model_bf16.safetensors")
```
Key: use `sd.pop()` not `sd[k]` to avoid holding both copies.

**B. Use `from_pretrained` with `low_cpu_mem_usage=True`:**
```python
model = AutoModelForCausalLM.from_pretrained(
    path, dtype=torch.bfloat16, device_map="cpu",
    low_cpu_mem_usage=True,
)
```
Uses mmap — fast load (1-2s) but can cause slow GPU transfers.

**C. Increase Windows page file** (band-aid, not fix): Set to system-managed or at least 16GB.

### 3. mmap'd models cause slow GPU transfers

When using `low_cpu_mem_usage=True`, weights are memory-mapped. `.to("cuda")` pages in weights from disk — 8GB file can take 30+ seconds.

**Mitigation:** Pre-dequantize to a normal safetensors file. The bf16 file is still mmapped but smaller transfers are faster. Or load with manual `load_state_dict` after creating model structure with `from_config`.

### 4. bf16 performance on older GPUs

GTX 16xx series have limited bf16 tensor core support. Inference is significantly slower than fp16.

**Rules of thumb for 1660 Ti (6GB VRAM):**
- Single forward pass for 3B transformer: ~3-5 seconds
- With sequential CFG (2x passes): ~6-10 seconds per step
- 20 CFG steps at 512x512: ~3-5 minutes
- Prefer distilled models (no CFG, 4 steps) over base models

**If bf16 is too slow:** Try fp16 — 16xx cards have better fp16 throughput:
```python
model = model.half()  # fp16 instead of bf16
```

## Model loading checklist

Before loading a large model on constrained hardware:

1. **Check free memory:** `cat /proc/meminfo | head -3` (MSYS) or `systeminfo`
2. **Kill zombies:** `tasklist | grep python` — kill anything >500MB that isn't current process
3. **Choose loading strategy:**
   - First load: `from_pretrained` with `low_cpu_mem_usage=True`
   - Repeated use: pre-dequantize to bf16, save, load from bf16 file
4. **Monitor during load:** Watch RSS growth — if it exceeds available RAM, load will fail
5. **For GPU models:** Load transformer first (largest), then VAE, then text encoder on CPU

## VRAM budget for 6GB GPU

| Component | VRAM | Notes |
|-----------|------|-------|
| Transformer (3B bf16) | ~4.1GB | Largest component |
| VAE | ~0.3GB | Stays on GPU |
| Text encoder (4B) | ~4GB | Must shuttle CPU↔GPU |
| **Total peak** | **~8.4GB** | Exceeds 6GB! |

**Strategy:** Keep text encoder on CPU. Move to GPU only for encoding, then immediately back. Never keep both transformer AND text encoder on GPU simultaneously.

## Profiling inference bottlenecks

When a pipeline is slow, add timing prints at each stage to identify the real bottleneck. Example from FLUX.2 Klein on GTX 1660 Ti:

```
Stage                    Time     % of total
──────────────────────────────────────────
Transformer → CPU          3.6s     1%   data transfer
Text encoder → GPU        30.0s     8%   mmap page-in!
Text encode (2 prompts)   10.7s     3%   GPU compute
Transformer → GPU         41.5s    11%   4GB data transfer!
2 CFG steps (4 fwd pass)  119.7s    31%   bf16 compute slow
Unpack latents            22.4s     6%   CPU bottleneck?
VAE decode                  8.8s     2%   normal
──────────────────────────────────────────
Total                    385.3s   100%
```

**Key insight:** The biggest bottleneck is often data transfer (CPU↔GPU), not compute. On 6GB GPUs where models must shuttle, 71.5s (18%) is pure data movement.

**Pre-touching mmap pages:** Stride through every parameter's storage at 2048-element steps (4KB for bf16) to force all mmap pages into physical RAM before GPU transfer. Reduces `.to("cuda")` from ~39s to ~27s (31% faster). Pre-touch itself costs ~6s. Net savings: ~6s per generation. Best combined with pre-dequantized bf16 file (avoids INT8 dequant peak entirely).

```python
# After loading with from_pretrained(..., low_cpu_mem_usage=True):
step = 2048  # 4KB per touch for bf16
for p in model.parameters():
    flat = p.data.view(-1)
    for i in range(0, flat.numel(), step):
        _ = flat[i]  # Force page-in
```

**RAM pressure amplifies everything:** When system RAM is >90% used, the OS starts swapping to disk. This makes EVERY stage slower — not just model loading but also CPU-GPU transfers (which go through RAM), tokenization, and even VAE decode. In one session, transformer-to-GPU transfer went from 41s (normal) to 70s (under RAM pressure). Always check `cat /proc/meminfo | head -3` and kill zombie processes before loading models.

## HuggingFace diffusers format vs custom format

HuggingFace repos (e.g. `black-forest-labs/FLUX.2-klein-4B`) use **diffusers format** with different key naming than custom checkpoints:

| Custom key | Diffusers key |
|---|---|
| `double_blocks.0.ff.net.0.proj.weight` | `transformer_blocks.0.ff.linear_in.weight` |
| `single_blocks.0.attn.to_k.weight` | `single_transformer_blocks.0.attn.to_k.weight` |

**Pitfall:** A custom model class (like `Flux2`) CANNOT load diffusers-format weights directly. You need either:
1. A key mapping conversion script
2. Use diffusers' `FluxTransformer2DModel` instead of custom class
3. Use the custom-format checkpoint (`.safetensors` from the model author, not the HF repo diffusers version)

The root `flux-2-klein-4b.safetensors` in HF repos is often a **1KB pointer file**, not the actual weights. Real weights are under `transformer/diffusion_pytorch_model.safetensors`.

## optimum-quanto INT8 quantization does NOT reduce VRAM for dequantized models

When you dequantize INT8 checkpoint to bf16/fp16 in memory, then quantize back to INT8 with `optimum.quanto`, **VRAM does NOT decrease**. The quanto library stores quantized weights in a format that uses similar memory to the fp16 originals.

```python
# This does NOT save VRAM:
from optimum.quanto import qint8, quantize, freeze
quantize(transformer, weights=qint8)  # transformer was already ~8GB in fp16
freeze(transformer)  # still ~6GB VRAM after quantization
```

**Root cause:** quanto's INT8 stores weight scales alongside the INT8 data, and the original fp16 weights are already expanded in memory before quantization. The quantization happens on the expanded weights, not the original INT8 checkpoint.

**For actual VRAM savings:** Use the original INT8 checkpoint directly (with custom dequantization during forward pass), or use a smaller model.

## fp16 vs bf16 on Turing GPUs (compute 7.5)

GTX 16xx series (Turing, compute capability 7.5) have **fp16 Tensor Core support but NOT bf16 Tensor Core support**. This means:
- **fp16:** Uses Tensor Cores → significantly faster matrix multiplications
- **bf16:** Falls back to CUDA cores → same speed as fp32 for most operations

```python
# Check GPU capability:
print(torch.cuda.get_device_capability(0))  # (7, 5) for 1660 Ti

# Force fp16:
model = model.half()  # fp16
# or
with autocast(device, "fp16"):
    output = model(input)
```

**Practical impact:** On GTX 1660 Ti, fp16 inference can be 1.5-2x faster than bf16 for transformer forward passes. Always prefer fp16 on Turing GPUs.

## Text encoder file format compatibility

When loading text encoders with `from_pretrained`, the safetensors file must be in **standard format** (regular weight keys). INT8-quantized files with `._data` / `._scale` suffixed keys will load but `.to(device)` will fail or produce wrong results.

**Pitfall:** After renaming/reorganizing model files, verify the file is in the expected format:
```python
from safetensors import safe_open
f = safe_open("model.safetensors", framework="pt")
keys = list(f.keys())
has_int8 = any(k.endswith("._data") for k in keys)
print(f"INT8 format: {has_int8}, Keys: {len(keys)}")
```

If the file has `._data` keys, it's INT8 format — use manual dequantization, not `from_pretrained`.

## FLUX.2 Klein 4B — reference config

Model: Klein4BParams(in_channels=128, context_in_dim=7680, hidden_size=3072, num_heads=24, depth=5, depth_single_blocks=20, mlp_ratio=3.0, use_guidance_embed=False)

- **Text encoder:** Qwen3-4B (bf16, 4GB). Layers [9,18,27] → 7680-dim hidden states.
- **Base model needs CFG:** guidance_scale=4.0, default 20 steps (50 for max quality).
- **Distilled model:** 4 steps, no CFG, guidance_scale=1.0. Uses different checkpoint format.
- **Default resolution:** 512x512 on 6GB GPU (max 768x768).

Generation time on GTX 1660 Ti Max-Q (6GB):
- 256x256, 2 steps CFG: ~6-7 min
- 512x512, 20 steps CFG: ~45-60 min (impractical)
- Distilled 4 steps would be ~1-2 min but requires compatible checkpoint

**Default step recommendation:** 20 steps for reasonable quality/speed tradeoff. 50 steps is too slow on 16xx GPUs.

## Windows-specific notes

- `.bat` files must be ASCII-only. UTF-8 Chinese causes garbled output even with `chcp 65001`
- Use `cmd.exe //c` for Windows-native commands from MSYS bash
- `netstat -ano | grep <port>` to check port usage
- Gradio 6.0 moved `theme` from `gr.Blocks()` to `app.launch()`. Using it in Blocks() generates a UserWarning.
- `torch.compile` with `mode="reduce-overhead"` requires dummy forward pass matching actual model input shapes. If shapes don't match, compile fails silently (falls back to eager). Klein4B img_in expects `(B, S, 128)` not `(B, S, 3072)`.
- `tasklist` shows memory in KB — divide by 1024 for MB
- **Gradio progress callback nesting:** `gr.Progress` does NOT propagate through nested function calls — not even when passing the progress object directly. The UI simply won't update. **Fix: use a background thread + shared dict.** The thread inherits the Gradio request context and can call `progress()` successfully:

```python
import threading

def generate(prompt, progress=gr.Progress()):
    _step = {"n": 0, "total": 20}
    _stop = threading.Event()

    def updater():
        while not _stop.is_set():
            progress(_step["n"] / _step["total"],
                     desc=f"Step {_step['n']}/{_step['total']}")
            _stop.wait(0.5)

    t = threading.Thread(target=updater, daemon=True)
    t.start()
    try:
        for step in range(20):
            _step["n"] = step
            # ... long-running work ...
    finally:
        _stop.set()
```

**Reality check (verified in session):** NONE of these approaches reliably show step descriptions in Gradio 6.x's progress bar during long-running sync functions:
- Direct `progress_obj` passed to helper → shows "processing", no desc
- Lambda wrapper → same, no desc
- Background thread + shared dict → shows "processing", no step desc
- `progress(step/total, desc=f"Step {step}/{total}", total=total)` → still just "processing"

**What actually works:** Show step info in the info/status textbox output, not the progress bar. Or accept that the progress bar only shows "processing" until completion. The `[GEN] Step X/Y` terminal logs are the most reliable step tracking.

---

## Absorbed Skills

### Low-VRAM Optimization Techniques
See `references/low-vram-techniques.md` for content absorbed from `low-vram-inference` — including custom low-RAM safetensors reader (page-file-limited systems), custom INT8 absmax quantization, black image diagnostic methodology (step-by-step NaN detection), fp16+fallback pattern for diffusion models at 512x512+, and analysis of production fast implementations (GaiaVideoFactory's mmgp/MAG cache/UniPC approach).
