---
name: "short-drama-pipeline"
description: "Local short-drama / AIGC content generation pipeline (D:\\AI_work\\AIGC_agent). Use when the user mentions \"AIGC_agent\", \"D:/AI_work/AIGC_agent\", \"kairos_aigc\", \"分集/资产/分镜\", \"剧本/日志\" tab, \"script_gen / sp"
priority: 0.5
version: "1.0.0"
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\creative\\short-drama-pipeline\\SKILL.md"
---
# 短剧 AIGC 流水线（AIGC_agent）

本地 FastAPI + TS 前端的全流程 AIGC 短剧生成工具，跑 agnes 系列模型。**不是用户当前主推方向**（主推 = 海外 Upwork freelance），但用户仍在用、随时会回来调，所以这个 skill 要保持可用。

---

## 项目根

- 主入口：`D:\AI_work\AIGC_agent`
- 同一份工程也可从 `E:\D_bak\AI_work\AIGC_agent` 访问（目录联接，指向同一条路径）

不要假设路径，每次用工具前确认还在。

## 启动 / 停止

- Windows 后台：`start_silent.bat`
- Linux 等价：`start_silent.sh`
- 停止：`stop_silent.bat`
- backend 端口：`http://127.0.0.1:8910`，前端 `http://127.0.0.1:3010`
- 健康探活用 `scripts/health-check.sh`
- **改完后端代码必须 stop → start 才生效** —— uvicorn 没开 reload，改完文件后 8910 上跑的还是旧代码。交接时把"需要重启"明确说出来，别让用户以为改动没生效。

---

## 六阶段流水线（核心架构）

`kairos_aigc/aigc/pipeline.py` 串行 orchestrator，每个阶段是一个 module：

| # | 文件 | 输入 | 输出 |
|---|------|------|------|
| ① | stages/script_gen.py | idea_prompt | script 全文 + 元数据 |
| ② | stages/split_eps.py | script | 多集（episode 数组） |
| ③ | stages/asset_consolidation.py | 多集 | 角色/场景/道具 清单 |
| ④ | stages/asset_gen.py | 清单 | image（每资产一张） |
| ⑤ | stages/storyboard.py | 多集 + 资产 | 每镜 prompt（用 image 生成 image-to-image prompt 链） |
| ⑥ | stages/video_gen.py | storyboard + image | 视频（每镜一段） |

前端 4 个 tab ↔ 阶段对应：

- **剧本/日志** ← ① + 实时日志流（warning/info）
- **分集(N)** ← ②
- **资产(N)** ← ③ + ④
- **分镜/视频(N)** ← ⑤ + ⑥

## 当前模型（2026-09-15 升级到 2.5 世代）

来源：`data/settings.json`

- `llm_model`: agnes-2.5-flash
- `image_model`: **agnes-image-2.5-flash**
- `video_model`: **agnes-video-2.5-flash**
- `image_size`: `2K`（**档位制**，不是像素）+ `image_ratio`: `16:9`
- `video_size` / `default_video_resolution`: `720P`（flash 只出 720P）

Agnes API 端点：`https://apihub.agnes-ai.com/v1/`
账户实测可用（`GET /v1/models`）：`agnes-3.0-flash / agnes-2.5-flash / agnes-2.0-flash /
agnes-image-2.5-flash / agnes-image-2.1-flash / agnes-video-2.5-flash / agnes-video-2.5 /
agnes-2.5-pro-alpha`。**`agnes-video-v2.0` 已不在模型列表里** — 老代码里的 v2.0 wire 格式全部作废。

⚠️ 2.5 的接入契约和 v2.0 完全不同（`seconds` / `size` 档位 / `mode` / `/agnesapi` 轮询）。
改模型或加模型前**必读** `references/agnes-25-migration.md`，并先跑
`scripts/probe_agnes_video.py` 实测，不要从 docs 外推（agnes 历史踩过坑：`1.5-flash` 还写在
代码里但 API 已下线）。

### 替换模型 / 换 API 契约的标准流程

1. **先探针**：`GET /v1/models` 确认模型在；跑 `scripts/probe_agnes_video.py` 真发一次图片 +
   视频请求。探完就停手 —— 免费档连续 4-5 次就能把配额和渲染队列打满，之后全是 429/503。
2. **对齐契约**：逐条过 `references/agnes-25-migration.md`：哪些字段消失、哪个字段改了语义、
   轮询端点是否变。文档 ≠ 实际行为，写代码前用探针确认。
3. **改 provider + 所有调用方**：`llm/agnes_provider.py` → `media/video.py` →
   `stages/{asset_gen,video_gen,storyboard}.py` → `api/routes/*` → `pipeline.py` → 前端下拉。
4. **grep 测试树改手搓 mock**：`grep -rn "旧字段名" tests/`。fake provider / `fake_generate`
   都带显式 kwargs，provider 签名一变就 `TypeError: unexpected keyword argument`；旧断言
   （`num_frames` / `1080p` / `negative_prompt` …）必须逐条改写成新契约 —— **不是加几条新测试了事**。
5. **验证**：`pytest tests` → `web/` 下 `npx tsc -b` + `npx vite build`（验完删掉 `web/dist`
   与 `web/tsconfig.tsbuildinfo`）→ 在 **spare port** 上跑一次真实 E2E（配方见
   `references/agnes-25-migration.md` 第 5 节；**不要 kill 用户正在跑的 8910/3010**）。

---

## ⚠️ 已知风险 & 待定方案

### Risk: script_gen JSON 降级 warning

日志样例：

  11:38:30  stage.warning  LLM didn't return JSON; using raw text as script.

来源：`stages/script_gen.py` — idea 阶段 LLM 返回非 JSON，pipeline 不抛错，降级把 raw text 当作 script 继续往下跑。
后果：下游 ②③④⑤ 拿到非结构化文本可能彻底分错。

### 三个修复方案（用户 2026-08-08 列出，未拍板）

- **#1** 改 SCRIPT_FROM_IDEA prompt 让 LLM 严格 JSON（修源头）
- **#2** 自动降级重试一次再 fallback（兜底）
- **#3** LLM SDK 强制 `response_format={type: "json_object"}`（预防）

下次用户回来问 warning，**不要自作主张选一个** — 把三个方案再说一遍让用户拍板。memory 用户偏好明确："我还没选择，你就自己做了？"

### Risk（2026-08-09 缓解）：手搓视频 5xx 错误无 traceback

旧 `_generate()` unhandled exception 走 FastAPI 兜底返回裸 `{"detail": "Internal Server Error"}`，
uvicorn 启动在 minimized window 吞 stderr → user 看不到 traceback。修复见
`references/manual-video-ui.md` 的 "Persistent error log 模式" 节。
**2026-08-09** 新增 `try/except Exception` 显式 `raise HTTPException(500, detail=repr(e))`，
traceback 写 `data/logs/manual_video_errors.log`。

---

## 与 storyboard-gen skill 的关系（关键！）

`storyboard-gen` skill 已装（C:\Users\leohu\AppData\Local\hermes\skills\storyboard-gen\，v6），但 **AIGC_agent 项目里的 prompt 不引用它**。

现状：
- storyboard-gen skill 是给 **Hermes Agent** 用的 — 用户说"写个分镜剧本"，我按 v6 格式输出
- AIGC_agent 是独立后台工具 — 跑自己的 prompt_templates.py，跟 skill 加载无关

用户曾讨论三条整合路径（未拍板）：
- #A 把 SCRIPT_FROM_IDEA 改成"输出 v6 SEG 表格"（改 1 文件）
- #B 加适配层：第一次 LLM 生成自由文本，第二次 LLM 转 v6 结构
- #C 不动 script_gen，到 storyboard 阶段才套 v6（storyboard.py 也要改 prompt）

下次用户提"统一分镜格式"再问要不要落地方案。

---

## API 路由（kairos_aigc/api/routes/）

- `manual_video.py` — `/api/manual-video/{generate,upload,presets,history}` 手动触发端点（i2v/t2v 双模式、history 列表；2.5 起分辨率固定 720P、时长 4–12s 由后端夹紧，旧的 DURATION_CAPS 已作废）。详见 `references/manual-video-ui.md`。
- `settings.py` — 模型切换、prompt 调整；2026-08-09 新增 `image_concurrency` / `image_interval_s` 限流字段
- `projects.py` — 项目 CRUD

API 详细路径看 `references/api-endpoints.md`。

---

## Prompt 模板位置

所有 prompt 硬编码在 `kairos_aigc/aigc/llm/prompt_templates.py`：

- `SCRIPT_FROM_IDEA` — 阶段 ①
- 阶段 ②③④⑤⑥ 各有独立常量

修改 prompt 后必须跑 `tests/` 验证。完整 prompt 见 `references/prompts.md`。

---

## 测试

`tests/` 下 pytest：`test_storyboard.py`、`test_agnes_provider.py` 等。

跑测试前确认 `data/settings.json` 里 `api_key` 没失效（Agnes key 偶尔会 rotate）。

---

## ⚠️ 关键陷阱（再次强调）

1. **不要假设模型还在** — 加新模型前 curl probe `/v1/models`
2. **不要假设 backend 还在跑** — 操作前先 `curl http://127.0.0.1:8910/` 探活，没活就用 `start_silent.bat` 启
3. **不要自作主张选修复方案** — 给 3 选项让用户拍板
4. **不要给自由发挥空间给 LLM** — script_gen 阶段已经踩过非 JSON 降级的坑，prompt 要约束
5. **不要绑死 video_gen 的输出视频 prompt 格式** — 用户偏 storyboard 优先（项目里有 video_gen 阶段但 prompt 风格要可控）
6. **不要让图片并发 4+**（2026-08-09 新增）— Agnes image API 在 burst 下会 429，默认 `image_concurrency=1` + `image_interval_s=2.0` 已经够用；高 API tier 改 settings，**不要**改代码默认
7. **不要让 scene/prop reference 图出现人**（2026-08-09 新增）— start-frame 合成时会从 scene + chars + props 一起贴图；scene/prop 里有人 = 双重贴图（ghost figure）。
   ⚠️ 2026-09-15 更正：**图片 2.5 完全不支持 `negative_prompt`**（上游 400 `negative_prompt is not supported by text image queue`，且 `openai` SDK 连这个 kwarg 都不接受 → TypeError）。禁止人物/换族的约束现在**写进 prompt 正文**（`asset_gen` 的 `character_exclusions` / `scene_prop_exclusions`），**不要**再加回 `negative_prompt` 参数（加了就是 TypeError / 400），也**不要**删掉 prompt 里那两段排除清单。
8. **不要让 video_gen 跳过 preflight**（2026-08-09 新增）— `run_video_gen` 现在第一步就是 `run_asset_gen`，缺资产时 abort 而不是 10×500。**不要**为了"快一点"绕过这一步
9. **不要给 storyboard 表加 prev/next continuity 字段**（2026-08-09 新增）— continuity 在 video_gen 阶段从 `persistence.list_storyboards()` 实时按 `scene_name` 分组计算，零 schema 迁移。后续要加新维度也是同样模式，**不要**回退到 schema 字段方案
10. **不要用 patch / 手拼多行块做缩进敏感的多行替换**（强烈）— patch tool 会在跨函数/嵌套 dict 编辑时自动 +4 或 dedent，造成连续 10+ 次 IndentationError 雪崩；手写多行锚点也常因缩进差 2 格、或把实际单行的语句当成两行而 `count == 0`，白跑一轮。**做法**：① 整段改就在 `execute_code` 里对全文 `str.replace`，每次替换前 `assert raw.count(old) == 1`；② 只改几行时用**单行锚点**——按行定位（`[i for i,l in enumerate(lines) if substr in l]`）再替换那一行，不要拼多行块；③ 改完立刻 `python -c "import 模块"` 或 `ast.parse()` 验一次
11. **不要为了验证改动去 kill 用户正在跑的服务**— `taskkill /F /PID <8910 的 PID>` 会弹用户确认，无人响应即判定「未同意」并阻塞整个流程（且不许重试）。**做法**：8910/3010 原样留着，另起一个实例验证新代码（`uvicorn kairos_aigc.api.app:app --host 127.0.0.1 --port 8912`，background），验完只 kill 自己起的那个后台进程，再把"需要 stop → start 才生效"告诉用户。

---

## 相关 skill / 引用

- `storyboard-gen` — v6 分镜剧本格式（本项目**未集成**）
- `creative/ai-storyboard-artist` — AI 分镜创作工作流（不同 skill，注意区分）
- `storyboard-preview-tool` — 浏览器端分镜动画预览工具

详细架构 / prompt / API / 手动视频 UI 见 `references/`：
- `references/agnes-25-migration.md` — **2.5 世代接入契约**（视频 seconds/档位/mode//agnesapi 轮询；图片档位+ratio/extra_body；不支持参数清单）
- `references/pipeline-architecture.md` — 数据流 + 6 阶段 + preflight gate + continuity live-compute
- `references/api-endpoints.md` — 全部路由 + 新增 history 端点
- `references/prompts.md` — 模板位置 + 各阶段 prompt 常量
- `references/manual-video-ui.md` — 手动视频页面（i2v/t2v、压缩、history、antd 5.x AntApp 模式）