---
name: "hermes-auxiliary-vision"
description: "Configure Hermes Agent's auxiliary vision system for image recognition in chat. Covers provider setup, Chinese domestic providers (Alibaba DashScope, Zhipu GLM), troubleshooting, and fallback strategi"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\.archive\\hermes-auxiliary-vision\\SKILL.md"
---
# Hermes Auxiliary Vision Configuration

## The Problem

`vision_analyze` fails when:
- The main model (e.g. `deepseek-v4-flash`, `deepseek-v4-pro`) is **text-only**
- The `auxiliary.vision` auto-detection chain finds no supported backend:
  1. Main model (no vision) ❌
  2. OpenRouter (no key) ❌
  3. Nous Portal ❌
  4. Native Anthropic ❌
  5. Custom endpoint ❌
  6. **→ None → fail** 💥

## Solution: Configure an Explicit Vision Provider

Set `auxiliary.vision` in `config.yaml` to point to a vision-capable OpenAI-compatible endpoint.

### General Commands

```bash
hermes config set auxiliary.vision.provider ""
hermes config set auxiliary.vision.model <model_name>
hermes config set auxiliary.vision.base_url <openai_compatible_endpoint>
hermes config set auxiliary.vision.api_key "<your_api_key>"
```

Note: `provider: ""` (empty string) tells Hermes to use the **custom endpoint** fallback (step 5 in the resolution chain).

---

## Chinese Domestic Providers (Recommended)

### 🅰️ Alibaba Cloud DashScope (通义千问)

Best for: general image description, Chinese + English scenes.

| Field | Value |
|-------|-------|
| **base_url** | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| **model** | `qwen-vl-plus` (good balance) or `qwen-vl-max` (best quality) |
| **api_key** | Get at [dashscope.aliyuncs.com](https://dashscope.aliyuncs.com) → API Keys |

Config:
```bash
hermes config set auxiliary.vision.provider ""
hermes config set auxiliary.vision.model qwen-vl-plus
hermes config set auxiliary.vision.base_url https://dashscope.aliyuncs.com/compatible-mode/v1
# Add key to .env:
echo "DASHSCOPE_API_KEY=sk-xxx" >> ~/.hermes/.env
```

### 🅱️ Zhipu AI GLM (智谱)

Best for: Chinese-language contexts, integrates with existing autoglm-* skills.

| Field | Value |
|-------|-------|
| **base_url** | `https://open.bigmodel.cn/api/paas/v4` |
| **model** | `glm-4.6v` or `glm-4v-plus` |
| **api_key** | Get at [bigmodel.cn](https://bigmodel.cn/usercenter/proj-mgmt/apikeys) |

```bash
hermes config set auxiliary.vision.provider ""
hermes config set auxiliary.vision.model glm-4.6v
hermes config set auxiliary.vision.base_url https://open.bigmodel.cn/api/paas/v4
echo "ZHIPU_API_KEY=your_key_here" >> ~/.hermes/.env
```

### 🅲 Z.AI (api.z.ai)

Uses GLM-5V-Turbo, accessible from China.

| Field | Value |
|-------|-------|
| **base_url** | `https://api.z.ai/v1` |
| **model** | `glm-4v-plus` |

### 🅳 OpenRouter (if accessible)

Fallback when outside China.

```bash
export OPENROUTER_API_KEY=sk-...
# Set model to a vision-capable one like google/gemini-3-flash-preview
```

---

## Verification

Test the configured vision provider directly:

```bash
python3 -c "
import json, base64, urllib.request

api_key = 'your_key'
img_path = 'path/to/test.jpg'
with open(img_path, 'rb') as f:
    img_b64 = base64.b64encode(f.read()).decode()

payload = json.dumps({
    'model': 'qwen-vl-plus',
    'messages': [
        {'role': 'user', 'content': [
            {'type': 'text', 'text': '描述这张图片'},
            {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{img_b64}'}}
        ]}
    ],
    'max_tokens': 500
}).encode()

req = urllib.request.Request(
    'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
    data=payload,
    headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
    method='POST'
)
with urllib.request.urlopen(req, timeout=60) as resp:
    result = json.loads(resp.read())
    print(result['choices'][0]['message']['content'])
"
```

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| `vision_analyze` returns "The vision API rejected the image" | No vision backend configured | Set `auxiliary.vision.*` explicitly |
| `auxiliary_client.py` falls to step 6 (None) | Auto chain exhausted | Check GPU providers have API keys |
| Invalid token error | JWT expired | Refresh token from provider dashboard |
| 401 unauthorized | Wrong API key | Check .env file matches config |
| Connection timeout (China → HF, OpenRouter) | GFW blocking | Use Chinese domestic providers instead |

## Key Files

- `tools/vision_tools.py` — the tool implementation
- `agent/auxiliary_client.py` — the provider resolution logic
- `~/.hermes/config.yaml` — `auxiliary.vision.*` section

## Relevant Source Code

Vision tool resolution chain in `agent/auxiliary_client.py` (lines ~17-23):

```
Resolution for vision tasks (auto mode):
  1. Main provider (if vision-capable)
  2. OpenRouter (OPENROUTER_API_KEY)
  3. Nous Portal
  4. Native Anthropic
  5. Custom endpoint (base_url + api_key)
  6. None
```
