---
name: "llm-browser-gateway"
description: "浏览器前端直连 LLM API 的端到端方案。当用户说\"前端调大模型\"、\"浏览器接 LLM\"、\"CORS 错误 Failed to fetch\"、\"前端用 LLM 解析文本\"、\"前端接入 OpenAI/Anthropic/MiniMax\"等时使用。 覆盖：(a) 跨平台 API 风格自动适配（OpenAI Chat Completions vs Anthropic Messages vs MiniMax Anthropic-compatible）；(b) 通用 CORS proxy 模板（FastAPI 10 行）；(c) 浏览器 fetch 写法模板；(d) 调试 / 错误诊断（完整 URL + 状态码 + 响应 body）。 不覆盖：后端 server-side 调用（用 OpenAI / Anthropic Python SDK 即可，无 CORS 问题）。不覆盖：流式响应 / function calling / tools schema（这些是更高阶用法，按需扩展）。"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/.archive/llm-browser-gateway/SKILL.md"
---
# 浏览器 LLM Gateway · 前端直连 LLM 完整方案

## 触发场景
- 前端 HTML/JS 需要直接调用 LLM API（OpenAI / Anthropic / MiniMax / 自建兼容 endpoint）
- 出现 `Failed to fetch` 错误（90% 是浏览器 CORS 拦截）
- 用户问"为什么 curl 能调通但浏览器不行"
- 需要在多个 LLM 提供商间无缝切换（一个 demo 同时支持 OpenAI / Anthropic / MiniMax）
- 想给前端加 LLM 推理能力（如"用 LLM 解析 v6 分镜剧本"、"用 LLM 生成摘要"）

## 核心问题
**两个独立问题叠在一起**：
1. **浏览器 CORS 拦截** —— LLM 服务（特别是 MiniMax / Anthropic 官方）通常没为浏览器开放 CORS header。
2. **LLM API 风格不统一** —— OpenAI / Anthropic / MiniMax / Azure Foundry 用不同请求格式。

`fetch()` 跨域调用 LLM 时，浏览器先发 OPTIONS preflight 请求。如果服务端没返回正确的 `Access-Control-Allow-*` 头部，浏览器直接拒绝，请求根本没机会发出去。

## 方案选型

| 场景 | 方案 |
|------|------|
| 一次性 demo / 个人工具 / 本地开发 | **本地 FastAPI proxy**（10 行，推荐） |
| 生产环境 / 多用户 / 部署上线 | Cloudflare Worker 反向代理 |
| 完全自托管 LLM（Ollama / vLLM / LocalAI） | 启动时加 `--cors-allow-origins '*'` |
| 已有后端服务 | 在后端加个新路由转发（Express / Flask / FastAPI 都行） |

## API 风格对比（必须区分）

| Provider | 路径 | Auth Header | Request body 关键字段 | Response 取文本字段 |
|----------|------|-------------|----------------------|---------------------|
| OpenAI | `${base}/v1/chat/completions` | `Authorization: Bearer <key>` | `{messages:[{role, content}]}` | `choices[0].message.content` |
| Anthropic 官方 | `${base}/v1/messages` | `x-api-key: <key>` + `anthropic-version: 2023-06-01` | `{messages:[{role, content}], max_tokens}` | `content[0].text` |
| **MiniMax (china)** | `${base}/anthropic/v1/messages` | `Authorization: Bearer <key>` 或 `x-api-key` | 同 Anthropic | 同 Anthropic |
| Azure Foundry (anthropic 风格) | `${base}/anthropic/v1/messages?api-version=...` | `Authorization: Bearer <key>` | 同 Anthropic | 同 Anthropic |
| 自建 OpenAI 兼容（OpenRouter / Agnes） | `${base}/v1/chat/completions` | `Authorization: Bearer <key>` | 同 OpenAI | 同 OpenAI |

**自动检测规则**：base_url 含 `/anthropic` 字符串或域名是 `anthropic.com` → Anthropic 风格；否则 → OpenAI 风格。

## 浏览器端 fetch 模板（推荐直接复制）

```javascript
async function callLLM({ system, user, cfg }) {
  let baseUrl = cfg.base_url.trim().replace(/\/+$/, '');
  const isAnthropic = /\/anthropic/i.test(baseUrl) || /anthropic\.com/i.test(baseUrl);

  // 补全路径
  let url;
  if (isAnthropic) {
    if (!/\/v\d+/.test(baseUrl)) baseUrl += '/v1';
    url = `${baseUrl}/messages`;
  } else {
    if (!/\/v\d+$/.test(baseUrl)) baseUrl += '/v1';
    url = `${baseUrl}/chat/completions`;
  }

  // 构造请求
  const headers = { 'Content-Type': 'application/json' };
  let body;
  if (isAnthropic) {
    headers['x-api-key'] = cfg.api_key;
    headers['anthropic-version'] = '2023-06-01';
    body = JSON.stringify({
      model: cfg.model,
      max_tokens: 4096,
      // Anthropic 没有 system role，拼到第一条 user 消息前面
      messages: [{ role: 'user', content: system + '\n\n' + user }]
    });
  } else {
    headers['Authorization'] = `Bearer ${cfg.api_key}`;
    body = JSON.stringify({
      model: cfg.model,
      messages: [
        { role: 'system', content: system },
        { role: 'user', content: user }
      ],
      temperature: 0.1
    });
  }

  let resp;
  try {
    resp = await fetch(url, { method: 'POST', headers, body });
  } catch (netErr) {
    // 99% 是 CORS 拦截
    throw new Error(`网络/CORS 失败：${netErr.message}\nURL: ${url}\n→ 起本地 proxy 或用支持浏览器 CORS 的 endpoint。`);
  }

  if (!resp.ok) {
    const errBody = await resp.text().catch(() => '');
    throw new Error(`HTTP ${resp.status}\nURL: ${url}\n风格: ${isAnthropic ? 'Anthropic Messages' : 'OpenAI Chat Completions'}\n响应: ${errBody.slice(0, 400)}`);
  }

  const data = await resp.json();
  return isAnthropic
    ? data.content?.map(c => c.text || '').join('') || ''
    : data.choices?.[0]?.message?.content || '';
}
```

## FastAPI CORS Proxy 模板（10 行核心）

完整模板在 `templates/fastapi_cors_proxy.py`。核心结构：

```python
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
import httpx

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                    allow_methods=["*"], allow_headers=["*"])

UPSTREAM = "https://api.minimaxi.com/anthropic/v1/messages"  # 改成实际 endpoint

@app.api_route("/anthropic/v1/messages", methods=["POST", "OPTIONS"])
async def proxy(request: Request):
    if request.method == "OPTIONS":
        return Response(status_code=204)
    body = await request.body()
    fwd = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(UPSTREAM, content=body, headers=fwd)
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"),
                    headers={"Access-Control-Allow-Origin": "*"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
```

启动：`pip install fastapi uvicorn httpx` → `python proxy.py`。浏览器 base_url 改成 `http://localhost:8765/anthropic`。

## Pitfalls（按踩坑频率排序）

1. **curl 通 ≠ 浏览器通** —— curl 不做 OPTIONS preflight，浏览器强制 CORS 检测。`Failed to fetch` 永远是 CORS / 网络层错，**不是 API 路径错**。先怀疑 CORS，最后才怀疑路径。
2. **MiniMax 不是 OpenAI 风格** —— 用 `/v1/chat/completions` 必 404。必须用 `/anthropic/v1/messages`（Anthropic Messages API 兼容）。
3. **Anthropic 没有 system role** —— 必须把 system prompt 拼到 user 第一条消息前面（或用官方 SDK 的 system 字段）。硬塞 `{role: "system"}` 会被拒。
4. **Authorization vs x-api-key** —— Anthropic 官方用 `x-api-key`，但 MiniMax / Azure Foundry 用 `Authorization: Bearer`。如果 401 错误，尝试切换 header。
5. **自动补 /v1 要分风格** —— OpenAI 路径必须是 `/v1/chat/completions`（v 后接 chat），Anthropic 路径是 `/v1/messages`（v 后接 messages）。自动补后还要再判断一次。
6. **错误信息要详细** —— 不要只 catch 网络错误后报"出错了"。至少显示完整 URL、HTTP 状态码、响应 body 前 400 字符。三者能定位 90% 的问题。
7. **demo 默认 base_url 要选对** —— 用户每次打开 demo 都希望默认就能用。MiniMax 用户默认 `http://localhost:8765/anthropic`，OpenAI 用户默认 `https://api.openai.com/v1`。记下当前活跃 demo 用的 provider。
8. **流式响应另算** —— SSE (Server-Sent Events) 跟普通 JSON 不一样，需要 `ReadableStream` 解析。本模板只覆盖非流式。流式要扩展。
9. **Anthropic 强制 max_tokens** —— 不传 `max_tokens` 会被拒。OpenAI 不强制。

## 用户配置 UI 推荐设计

- Base URL 输入框 + 提示语（"MiniMax 用 /anthropic 路径"）
- API Key 密码框
- Model 输入框
- "测试连接" 按钮（不发业务请求，只发 `max_tokens: 5` 的小请求验证 endpoint 通 + 风格对）
- 错误显示区（红框，monospace 字体，显示 URL + 状态码 + body）

存到 `localStorage`，base_url 跟 model 都缓存，下次打开不用重填。

## 调试清单

按顺序排查：

1. **curl 验证 endpoint 真存在** —— `curl -X POST <url> -H "..." -d '{...}'`。返回 401 = endpoint 真实；返回 404 = 路径错。
2. **curl 验证 CORS 头** —— 加 `-H "Origin: https://example.com" -i` 看响应是否有 `Access-Control-Allow-Origin`。
3. **curl 验证认证** —— 用真 key 试一次，确认能拿到 200。
4. **浏览器测试** —— 起本地 proxy，浏览器调 `http://localhost:PORT`。如果 proxy 通而云端 URL 不通，100% 是 CORS。
5. **F12 Network 面板** —— 看 preflight OPTIONS 请求的响应。如果 OPTIONS 返回 200 但没 CORS 头，浏览器拒绝。
6. **F12 Console** —— `Failed to fetch` 几乎都是 CORS；`HTTP 404` 是路径错；`HTTP 401` 是 key 错；`HTTP 400` 是请求体格式错。

## References / Templates / Scripts

### Core files (this skill's own catalog)

- `references/api-style-comparison.md` —— 各家 LLM API 风格的完整对比表（headers、request body、response、错误码、token 限制）
- `references/cors-proxy-deploy.md` —— 各种部署方案（FastAPI / Cloudflare Worker / Node / Nginx / Express）
- `references/demo-api-style-detection.md` —— absorbed from `browser-llm-demo`: minimal API-style detection rule (`/\/anthropic/i.test(baseUrl)`) + cheat sheet
- `references/demo-progress-checkpoints.md` —— absorbed from `browser-llm-demo`: `_step()` helper + status div + checkpoint table for debugging "stuck on initializing"
- `templates/fastapi_cors_proxy.py` —— 完整可跑的 FastAPI proxy 模板
- `templates/browser_fetch_module.js` —— 完整浏览器端 fetch 模块（含测试连接 + 错误诊断 + localStorage 缓存）
- `scripts/test_llm_connection.sh` —— curl 多 endpoint 批量测试脚本（验证 endpoint 是否真实存在）
- `scripts/demo-local-proxy.py` —— absorbed from `browser-llm-demo`: alternate FastAPI proxy implementation (kept for back-compat, prefer `templates/fastapi_cors_proxy.py` for new projects)

## Building single-HTML demos that call cloud LLMs (absorbed from `browser-llm-demo`)

When the deliverable is a single HTML file (e.g. `storyboard-previs.html`, image viewer, chat widget), apply this 4-step pattern on top of the connection layer above:

### Step 1: Auto-detect API style from URL (subpattern of cross-vendor adapter)

```javascript
const isAnthropic = /\/anthropic/i.test(baseUrl) || /anthropic\.com/i.test(baseUrl);
const url = isAnthropic
  ? `${baseUrl.replace(/\/+$/, '')}/v1/messages`
  : `${baseUrl.replace(/\/+$/, '')}/v1/chat/completions`;
```

Use the headers/body routing from the cross-vendor table above — don't duplicate it.

### Step 2: Local FastAPI CORS proxy on `localhost:8765`

Most cloud LLM APIs don't expose `Access-Control-Allow-Origin` for browsers. Run `templates/fastapi_cors_proxy.py` (or the absorbed `scripts/demo-local-proxy.py`) on port 8765 and point the demo's `base_url` at `http://localhost:8765`. User experience: start proxy → open HTML → no CORS errors.

### Step 3: Local `vendor/` folder, NOT `<script src="https://unpkg.com/...">`

`importmap` + ES module + public CDN fails silently in three scenarios: headless browsers (Browserbase etc.), China networks (unpkg timeouts), offline machines. Reliable pattern:

```
project/
├── index.html
├── vendor/three.min.js   # downloaded once via curl, ~655KB
├── proxy.py
└── index.html
```

Reference with `<script src="./vendor/three.min.js"></script>` — UMD, exposes `window.THREE`, no module system needed. **Don't promisely use ES modules on `file://`.**

### Step 4: Progress checkpoints at every step

Use the `_step()` helper + `<div id="status">` from `references/demo-progress-checkpoints.md`. Without progress text, "stuck on initializing" is the #1 user complaint, and you can't tell which step hung.

### Demo-specific pitfalls (load-bearing)

1. **Backticks inside JS template literals break syntax silently.** A `SYSTEM_PROMPT` literal that contains literal triple-backticks (e.g. `don't wrap in \`\`\`json` fences`) terminates the template early — browser silently skips the entire `<script>` block, no console error, just frozen UI. **Always** describe in prose ("triple-backtick + json + triple-backtick"), OR escape the backticks as `\`\`\`` inside the template literal. **Detection before shipping**: extract the inline `<script>` content to a `.js` file and run `node -c file.js`. Node is strict about syntax; browser is silent.

2. **Headless browser screenshots ≠ real-browser validation.** `browserbase` and most cloud browser tools have inconsistent ES-module / importmap / `file://` CORS handling. A demo that fails in headless may work fine in Chrome on the user's laptop, and vice versa. Don't conclude a bug from headless alone — combine with `node -c` for syntax + ask the user to test in their actual browser.

3. **Don't conflate LLM API styles by URL.** Common mistakes observed in 2026-08:
   - `/v1/chat/completions` for MiniMax → 404 (MiniMax only does Anthropic-compatible)
   - `/anthropic/v1/chat/completions` → 404 (mixing Anthropic path with OpenAI resource)
   - **Only `/anthropic/v1/messages`** works for MiniMax.
   - Hermes's `~/.hermes/.env` shows `MINIMAX_CN_BASE_URL=https://api.minimaxi.com/v1` — the SDK internally rewrites this to `/anthropic/v1/messages`. Don't copy the bare URL into a browser config; copy the SDK's path convention.

4. **"等待加载" / "Waiting for input" as initial status reads as a bug.** User feedback (2026-08-14): a page that opens to a static "等待加载" looks broken even when it isn't. Either auto-trigger on load (load sample + auto-play), OR show actionable instructions ("点「示例」体验 / 「LLM 解析」处理你的分镜").

### Demo delivery checklist

- [ ] `node -c` on extracted inline script content → syntax OK
- [ ] Test "test connection" button reports HTTP status (not `Failed to fetch`)
- [ ] Error messages show: URL, HTTP status, response body snippet, detected API style
- [ ] LLM call surfaces a clear error if JSON parse fails (position + context window)
- [ ] **No `unpkg.com` / `cdn.jsdelivr.net` references in shipped HTML** — vendor them locally
- [ ] Auto-loads sample on page open (or shows clear instructions)
- [ ] Progress status updates visible at each step
- [ ] CORS proxy starts cleanly: `curl http://localhost:8765/` returns the proxy's status JSON

### Recommended file layout for a single-HTML demo

```
project/
├── index.html              # main app (single file)
├── proxy.py                # CORS proxy on 8765 (templates/fastapi_cors_proxy.py)
└── vendor/
    └── three.min.js        # ~655 KB, downloaded once via curl
```

## Absorbed skills

This umbrella subsumes the previously-separate `browser-llm-demo` skill (now in `.archive/` + its content snapshotted in `software-development/browser-llm-demo-archive/`). The absorbed skill documented the 4-step pattern for building single-HTML demos with LLM calls; that pattern now lives in the "Building single-HTML demos that call cloud LLMs" section above. All absorbed references are kept under the `demo-` prefix to avoid clashing with the umbrella's own catalog.

If you find yourself loading the archived demo-skill for anything not already covered here, patch this umbrella instead — that's the job-to-be-done.