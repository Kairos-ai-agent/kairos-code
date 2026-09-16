---
name: "agent-vision-configuration"
description: "Configure your Hermes agent to see and analyze images. Covers auxiliary vision provider setup, model compatibility, and fallback skills (AutoGLM, GLMV) for when the primary model lacks vision."
priority: 0.5
version: "1.0.0"
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\.archive\\agent-vision-configuration\\SKILL.md"
---
# Agent Vision Configuration

Make your Hermes agent able to recognize images ("see" pictures) even when the primary chat model (e.g. DeepSeek V4, text-only LLMs) does not support vision natively.

## How Hermes vision_analyze Works

The `vision_analyze` tool has two modes:

| Mode | Condition | How it works |
|------|-----------|-------------|
| **Native vision** | Current model supports `image_url` input | Image is sent directly to the model's context (e.g. GPT-4o, Claude 3.5 Sonnet, Gemini 2.0) |
| **Auxiliary vision** | Current model is text-only, but a vision provider is configured | Image is sent to the configured auxiliary vision model, which describes it back to the main model |

When `vision.provider: auto` and `vision.model: ''` (the default), the agent tries native vision first. If the primary model doesn't support it, `vision_analyze` fails with an error.

## Models Known to Be Text-Only

These models do NOT support vision:

- **DeepSeek V4-Flash** (all modes)
- **DeepSeek V4-Pro** (all modes)
- DeepSeek V3, DeepSeek-R1
- Pure text models from any provider

## Solution A: Configure an Auxiliary Vision Provider (Recommended)

Edit `config.yaml` in `~/AppData/Local/hermes/`:

```yaml
auxiliary:
  vision:
    provider: <provider-name>
    model: <vision-model-name>
    base_url: <optional-custom-endpoint>
    api_key: <optional-custom-key>  # Leave blank to use provider's default key
    timeout: 120
```

### Provider Quick Reference

| Provider | Vision Model | API Key Needed | Best For |
|----------|-------------|---------------|----------|
| **OpenAI** | `gpt-4o-mini` (cheapest) or `gpt-4o` | `OPENAI_API_KEY` | Best overall quality, cheapest option is `gpt-4o-mini` |
| **ZhipuAI (智谱)** | `glm-4.6v` | `ZHIPU_API_KEY` | Chinese-friendly, domestic access, good image understanding |
| **Z.AI** | `glm-4v-plus` | `ZAI_API_KEY` | Chinese-friendly via z.ai endpoint |
| **Anthropic** | `claude-sonnet-4` or `claude-3.5-sonnet` | `ANTHROPIC_API_KEY` | Strongest detailed descriptions |
| **Gemini** | `gemini-2.0-flash` | `GEMINI_API_KEY` | Fast, cheap, good general vision |

### Example: Configure OpenAI GPT-4o-mini as Vision Provider

```yaml
auxiliary:
  vision:
    provider: openai
    model: gpt-4o-mini
    base_url: https://api.openai.com/v1
    api_key: ""  # Will use OPENAI_API_KEY from .env
    timeout: 120
```

### Example: Configure Zhipu GLM-4V (Domestic, China-friendly)

```yaml
auxiliary:
  vision:
    provider: openai  # GLM uses OpenAI-compatible API
    model: glm-4.6v
    base_url: https://open.bigmodel.cn/api/paas/v4
    api_key: ""  # Will use ZHIPU_API_KEY from .env
    timeout: 120
```

**Setup steps:**
1. Get an API key from [智谱开放平台](https://bigmodel.cn/usercenter/proj-mgmt/apikeys)
2. Add `ZHIPU_API_KEY=<your-key>` to `~/AppData/Local/hermes/.env`
3. Update `config.yaml` with the vision config above
4. Restart Hermes: send `/new` or reconnect the agent

After configuration, `vision_analyze` will work natively — the user can send an image and the agent sees it directly.

### Verification

```bash
# Check the config was applied
grep -A8 "vision:" ~/AppData/Local/hermes/config.yaml
```

Then test by asking the agent to describe an image.

## Solution B: Use AutoGLM Image Recognition Skill (No Config Needed)

The installed `autoglm-image-recognition` skill can recognize images via AutoGLM API. It requires uploading the image first.

**Workflow:**
1. User sends an image in chat
2. Agent uploads the image via `upload-mix.py` to get a public URL
3. Agent calls `image-recognition.py` with the URL to get a description
4. Agent presents the description to the user

**Pros:** No API key needed (uses AutoGLM token service), already installed
**Cons:** Extra upload step, depends on AutoGLM service availability

## Solution C: Use GLMV Caption Skill (Needs ZHIPU_API_KEY)

The installed `glmv-caption` skill uses Zhipu GLM-V models to describe images, videos, and documents.

**Setup:**
```bash
# Add your API key
export ZHIPU_API_KEY="<your-key>"
```

**Usage:**
```bash
cd ~/AppData/Local/hermes/skills/glmv-caption
python scripts/glmv_caption.py --images "<image-url-or-path>"
```

## Comparison

| Solution | Setup Effort | Native Feeling | Domestic Access | Cost |
|----------|-------------|---------------|-----------------|------|
| **A: Vision provider** | Medium (one-time config) | ✅ Seamless | ✅ GLM-4V | API cost |
| **B: AutoGLM recog.** | None | ⚠️ Extra upload step | ✅ Yes | Free (token service) |
| **C: GLMV Caption** | Low (add env var) | ⚠️ Explicit invocation | ✅ Yes | API cost |

## Known Issues & Pitfalls

1. **DeepSeek V4 is text-only**: Do not expect `deepseek-v4-pro` or `deepseek-v4-flash` to support vision. They are strictly text models. The official docs confirm: "DeepSeek V4 is text-only."

2. **vision.provider: auto is not magic**: Setting `auto` only works if the primary chat model itself supports vision. For a text-only primary model, `auto` still fails.

3. **API key scope**: The vision provider's API key is separate from the chat model's API key. Ensure the vision provider has a key with the right model access.

4. **Timeout on large images**: The default `timeout: 120` is usually enough, but very high-res images or slow vision APIs may need more. Increase `auxiliary.vision.timeout` in config.

5. **Multiple vision models on one key**: Some providers (OpenAI, Zhipu) support multiple vision models under the same key. Changing `vision.model` is risk-free — just edit config.yaml and reload.
