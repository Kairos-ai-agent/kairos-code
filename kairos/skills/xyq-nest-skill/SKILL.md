---
name: "xyq-nest-skill"
description: "通过小云雀的 AI 能力进行综合创作，支持生成和编辑图片/视频。覆盖场景包括：生成（文生图、文生视频、图生视频、做动画、画一个xxx、来段xxx）、编辑修改（把xxx换成yyy、去掉xxx、加上xxx、改成xxx、调整xxx、局部修改、改镜头）、风格转换（风格迁移、转绘、换风格）、视频续写延长、复刻视频/TVC/宣传片、短剧/短漫剧生成、音乐MV生成、产品广告/展示片制作、分镜/故事板设计、教育视"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/openclaw-imports/xyq-nest-skill/SKILL.md"
---
# 小云雀会话（生视频）

通过 小云雀的API 创建会话、发送消息（生图、生视频、编辑视频等）、上传图片/视频文件，并查询会话消息进展。

小云雀是一个 AI 综合创作平台，同时为人类创作者和 Agent 设计。Agent 通过 Skill 入口理解任务、调用模型并自动编排工作流。

**平台核心能力：**
- **生成**：文生图、文生视频、图生视频、视频续写
- **编辑**：局部修改、元素替换、镜头调整、风格迁移
- **复杂创作**：一句话生成完整短剧（剧本→分镜→成片）、复刻已有视频风格做 TVC/宣传片、用音乐生成 MV、产品展示片制作

用户的所有创作和编辑需求都通过发送自然语言消息来完成，Agent 会自主编排工作流。复杂任务（短剧、MV）耗时较长，需耐心轮询。

## 功能

1. **创建会话 / 发消息** - 创建新会话或向已有会话发送一条消息（如「创作一个视频」）
2. **查询会话进展** - 根据 `thread_id` 、 `run_id`、`after_seq` 增量拉取该会话的消息列表，用于轮询创作过程的消息和最终产物结果
3. **上传文件** - 支持上传`单张图片`或`单个视频文件`到小云雀资产库，得到文件对应的 `asset_id`（编辑已有视频/图片时需要先上传）
4. **下载结果** - 将会话中生成的图片/视频批量下载到本地，支持指定输出目录和文件名前缀。


## 前置要求

```bash
export XYQ_ACCESS_KEY="your-access-key"
```

可选：`XYQ_OPENAPI_BASE` 或 `XYQ_BASE_URL`，默认 `https://xyq.jianying.com`。

无需安装额外依赖，仅使用 Python 标准库。

## 使用方法

### 1. 创建会话 / 发送消息

```bash
# 创建新会话并发送「生一个动漫视频」
python3 {baseDir}/scripts/submit_run.py --message "生一个动漫视频"

# 向已有会话发送消息
python3 {baseDir}/scripts/submit_run.py --message "再生成一个故事视频" --thread-id THREAD_ID
```

### 2. 查询会话进展

```bash
# 查询会话消息列表
python3 {baseDir}/scripts/get_thread.py --thread-id THREAD_ID --run-id RUN_ID --after-seq SEQUENCE
```

> `run_id` 由 `submit_run` 返回，用于指定查询某次具体运行的结果。

### 3. 上传文件

- 当用户提供了参考的文件地址时，先进行文件上传，仅支持图片、视频。
- 单次指令执行仅支持单个文件，多个文件可并行调用，单个文件大小必须在200MB以下。

```bash
# 上传图片
python3 {baseDir}/scripts/upload_file.py /path/to/image.png

# 上传视频
python3 {baseDir}/scripts/upload_file.py /path/to/video.mp4
```

### 4. 下载结果

任务完成后，可以将会话中的所有产物批量下载到本地。

```bash
# 指定 URL 列表，指定输出目录，指定文件名前缀（如 artifact_01.png, artifact_02.png ...）进行下载
python3 {baseDir}/scripts/download_results.py --urls URL1 URL2 URL3 --output-dir ./xyq_output --prefix "artifact"
```

## OpenAI 兼容代理

小云雀可以启动 OpenAI 兼容代理服务器，让任何 OpenAI 格式客户端调用。

### 启动代理

```bash
python3 {baseDir}/scripts/xyq_openai_proxy.py --port 8800
```

代理自动读取上级目录的 `.env` 文件获取 `XYQ_ACCESS_KEY`。

### API 端点

| 端点 | 说明 |
|------|------|
| `POST /v1/chat/completions` | OpenAI 格式调用 |
| `GET /v1/models` | OpenAI 格式模型列表 |
| `GET /api/models` | xyq 完整模板/模型列表（30+模板+底层模型） |
| `GET /health` | 健康检查 |

### 模型选择

xyq 后端 Agent 会自动选择模型。要强制使用指定底层模型，在消息前加 `[使用模型: model_id]`：

```python
# 代理自动处理：选中 seedance_2.0_fast 时，消息变为
"[使用模型: seedance_2.0_fast] 生成一个动漫视频"
```

已知底层模型：
- `seedance_2.0_fast` — Seedance 2.0 Fast 基础版
- `seedance2.0_fast_vision` — Seedance 2.0 Fast Vision（VIP，图生视频版）

详见 [references/openai-proxy.md](references/openai-proxy.md) 和 [references/xyq-api-models.md](references/xyq-api-models.md)

## 典型工作流

理解这些工作流，才能正确组合上面的脚本完成用户需求。

### 场景 1：用户要求生成图片或视频（最常见）

```
1. submit_run.py --message "用户的描述"  →  拿到 thread_id、run_id 和 web_thread_link
2. **立即**将 `web_thread_link` 展示给用户（如"任务已提交，可在此查看：{web_thread_link}"）
3. 每隔 `10` 秒钟调用 get_thread.py --thread-id THREAD_ID --run-id RUN_ID --after-seq SEQUENCE 进行轮询
4. 检查 messages：
  - 当任务还在创作中：
    - 将过程创作信息展示给用户，继续轮询
  - 当任务完成（run 结束）：
    - 如果涉及意图确认/流程中断（如"请回答以下问题"）：
      → 向用户展示问题，等待用户回复
      → 使用 `thread_id` 重新提交任务（保持同一会话，产生新的 run_id）
      → 回到步骤 2 继续轮询（可能多轮，直到不再意图确认）
    - 如果 content 中包含产物 URL：
      → 信息展示 → 下载产物 → 结果展示
5. 自动下载：download_results.py --urls URL1 URL2 URL3 --output-dir 输出目录 --prefix 有意义的前缀
6. 向用户展示：过程中的创作信息，以及下载后的本地文件列表
```

### 场景 2：用户提供图片/视频要求编辑修改（如"参考这个视频做一个新的"）

```
1. upload_file.py /path/to/video.mp4  →  拿到 asset_id
2. submit_run.py --message "参考这个视频做一个新的" --asset-ids asset_id  →  拿到 thread_id、run_id、web_thread_link
3. 后续同场景 1 的步骤 2-6
```

用户给了文件路径 + 编辑指令 = 先上传文件，再把编辑指令和 所有asset_id 一起发送。

### 场景 3：用户提供参考图/视频要求生成新内容

```
1. upload_file.py /path/to/ref1.png  →  拿到 asset_id1
2. upload_file.py /path/to/ref2.mp4  →  拿到 asset_id2
3. 直到所有文件上传完成，拿到所有 asset_id
4. submit_run.py --message "根据参考图、视频生成xxx" --asset-ids asset_id1 asset_id2, ...  →  拿到 thread_id、run_id、web_thread_link
5. 后续同场景 1 的步骤 2-6
```

### 场景 4：在已有会话中追加新需求

```
1. submit_run.py --message "新的描述" --thread-id THREAD_ID  →  拿到 thread_id、run_id、web_thread_link
2. 后续同场景 1 的步骤 2-6
```

### 轮询策略

- **间隔**：每 10 秒查询一次
- **增量拉取**：首次用 --after-seq 0，后续根据messages消息列表长度，计算新的 seq 值
- **完成判断**：当创作任务完成且messages的content中包含产物结果 URL（图片/视频地址）
- **超时**：连续轮询 `48 小时`仍无结果，告知用户"生成时间较长，可稍后查看"，不再继续轮询
- **错误重试**：单次查询失败可重试 1 次，连续 3 次失败则停止并告知用户

## 输出格式

**submit_run** 返回：
```json
{
  "thread_id": "90f05e0c-...",
  "run_id": "abc123-...",
  "web_thread_link": "https://xyq.jianying.com/..."
}
```

**get_thread** 返回：
```json
{
  "messages": [
    {"id": "1", "role": "user", "content": "生一个动漫视频"},
    {"id": "2", "role": "assistant", "content": [
      {"type": "{type}", "subtype": "{sub_type}", "data": {...}}
    ]},
    {"id": "3", "role": "assistant", "content": [
      {"type": "{type}", "subtype": "{sub_type}", "data": {..., "url": "{url}"....}}
    ]}
  ]
}
```

**upload_file** 返回：
```json
{"asset_id": "{asset_id}"}
```

**download_results** 返回：
```json
{
  "output_dir": "./xyq_output",
  "downloaded": ["./xyq_output/01.png", "..."],
  "total": 10
}
```

## 向用户展示内容

- 任务提交后：立即将 `web_thread_link` 展示给用户，方便用户直接打开浏览器查看任务页面
- 任务在创作中：
  - 展示过程中的创作信息等，继续轮询
- 任务完成（run 结束）：
  - 若涉及意图确认/流程中断（如"请回答以下问题"）→ 展示问题 → 等待用户回复 → 使用同一 `thread_id` 重新提交任务 → 继续轮询（可能多轮）
  - 若 content 中包含产物 URL：
  - 结果地址：来自 `get_thread` 返回的 `messages` 中，任务创作完成会包含产物 URL，将产物链接、下载的本地文件等信息告知用户。

## 核心原则：用户侧不做创作，只做传话

你（用户侧 Agent）的职责是**搬运工**，不是创作者。后端有专门的 Agent 负责理解需求、拆解分镜、编排工作流、选模型、写 prompt。你要做的只有三件事：

1. **上传**：如果用户给了本地文件 → `upload_file.py` 拿到 asset_id
2. **提交任务**：把用户的原始描述 + asset_id 原封不动发给 `submit_run.py`
3. **传话**：根据 `get_thread.py` 返回的消息列表，展示过程中的意图询问、创作信息等
4. **取件**：`get_thread.py` 轮询结果 → 检查结果 → 下载产物 → 结果展示给用户

**绝对不要做的事：**
- 不要替用户扩写、润色、翻译 prompt（用户说"帮我推演分镜"，就直接传"帮我推演分镜"，不要自己先写个分镜表再逐条发）
- 不要自行编排镜头描述、剧情推演、风格分析
- 不要在消息中添加自己编的 prompt（如"超写实风格，电影级光影，8K分辨率"之类的描述词）

后端 Agent 对模型能力、参数配置、prompt 工程远比用户侧更专业。用户侧越俎代庖只会降低生成质量，换个弱模型更是灾难。

**正确示例：**
```
用户说：「根据多张参考图，做个科普故事视频」
用户给了参考图：/path/to/ref1.png, /path/to/ref2.png, /path/to/ref3.png

→ upload_file.py /path/to/ref1.png →  拿到 asset_id1
→ upload_file.py /path/to/ref2.png →  拿到 asset_id2
→ upload_file.py /path/to/ref3.png →  拿到 asset_id3
→ submit_run.py --message "根据参考图、视频生成xxx" --asset-ids asset_id1 asset_id2, asset_id3  →  拿到 web_thread_link，立即展示给用户
→ 轮询 ─┬─ 意图确认 → 用户确认 → 使用 thread_id 重新提交 → 继续轮询
        └─ 无意图确认 → 信息展示 → 下载产物 → 结果展示
```

**错误示例：**
```
❌ 用户侧自己先写了个九宫格分镜表（对峙、交锋、危机...）
❌ 然后把自己编的描述发给后端
❌ 或者拆成9次 submit_run 分别发送
```

## OpenAI 兼容代理

代理服务器将小云雀模板系统包装为 OpenAI /v1/chat/completions 格式。

**核心设计：纯转接，不走 agent**
- 选模板 → 查 xyq 模板列表匹配 template_id + tool_key
- 提交 → 带 template_id + tool_key 调 submit_run
- 返回 → 直接给 web 链接，用户去网页看结果
- 不轮询、不解析产物、不自动下载

**启动代理：**
```bash
cd {baseDir}/scripts
# 需要 .env 文件包含 XYQ_ACCESS_KEY=xxx
python3 xyq_openai_proxy.py --port 8800
```

**关键端点：**
| 端点 | 说明 |
|------|------|
| GET /api/models | xyq 完整模板列表（30个） |
| GET /v1/models | OpenAI 格式模型列表 |
| POST /v1/chat/completions | 提交任务，返回 web 链接 |
| GET /health | 健康检查 |

**提交流程：**
1. 从 /api/models 拉取模板列表（缓存5分钟）
2. 用户选模板 → 匹配 template_id + tool_key
3. POST submit_run 带 template_id + tool_key
4. 返回 web_thread_link 给用户

**模型选择机制：**
- 模板列表来自 xyq API `/api/biz/v1/template/list`（30个模板）
- 选模板时传 template_id + tool_key，xyq 后端按模板流程执行
- 不要在消息里写模型名（后端会忽略，自行选择 VIP 版本）
- 模板匹配逻辑：先精确匹配 tool_key/title，再模糊匹配

**Windows 注意：**
- .bat 文件必须用 ASCII 编码，中文会乱码
- 代理启动前先杀旧进程（端口占用检查）
- 代理脚本自动加载上级目录的 .env 文件

## 注意事项

- 鉴权方式为请求头 `Authorization: Bearer <XYQ_ACCESS_KEY>`
- 创建会话时 `message` 是用户的指令要求，不能为空
- 查询会话时可用 --after-seq 做增量拉取，便于轮询新消息（含 assistant 回复与生图/生视频结果）
- 上传文件仅支持图片（image/*）和视频（video/*）类型，其他类型会被拒绝，文件大小须在 200MB 以下
- 生成过程中将过程中的创作信息展示给用户；任务完成后给出**产物结果（图片/视频）URL链接**和下载的**本地文件列表**。
- submit_run API 支持扩展参数：`template_id`（模板ID）、`tool_key`（工具标识）、`video_model`（视频模型名）
- 模型选择由 xyq 后端 Agent 控制，客户端无法强制指定——后端会根据任务类型自动选最优模型（通常是 VIP 版）

## 常见陷阱

### 陷阱 5：xyq 后端会覆盖模型选择

即使在消息中指定模型名（如 `[使用模型: seedance_2.0_fast]`），xyq 后端 Agent 会根据任务类型自动选择 VIP 版本。这是因为后端 Agent 有自己的 `general_agent_settings` 配置，用户侧无法覆盖。

**解决方法：** 通过 submit_run 的 `video_model` 参数传递模型名，而不是在消息中写。但即使如此，后端仍可能覆盖。这是 xyq 平台的设计限制。

### 陷阱 6：xyq 没有模型列表 API

xyq 的模型选择（Seedance 2.0 Fast / Fast VIP / VIP / 基础版）是网页前端硬编码的，没有单独的 API 端点返回模型列表。模板列表 API（`/api/biz/v1/template/list`）只返回 30 个模板，不包含底层视频模型。

**已知模型（来自网页端）：**
- `seedance_2.0_fast_vip` — Seedance 2.0 Fast VIP（极速推理，会员专属）
- `seedance_2.0_vip` — Seedance 2.0 VIP（全模态，会员专属）
- `seedance_2.0_fast` — Seedance 2.0 Fast（高性价比）
- `seedance_2.0` — Seedance 2.0（全能王者）

### 陷阱 7：Windows bat 文件编码

Windows cmd 不支持 UTF-8 中文，bat 文件中的中文会乱码导致闪退。解决方法：bat 文件只用英文字符。
- 视频下载 URL 有时效性（token 过期后 401），需要在新 run 完成后尽快下载。过期后可从 `get_thread` 的 entry_list → artifact 中重新提取最新 URL。
- 生成中的进度信号：当后端 `get_thread` 返回的消息列表中出现 `pippit_asset_id`（即使 data 为空字符串），说明后端已把任务提交给视频引擎（Seedance 等），正在排队/渲染中。这个 ID 是比单纯轮询更早的「已提交」信号。
- 模型自动选择：后端 Agent 在 `general_agent_settings` 中设置 `video_model`，实际调用时在 `sandbox_generate_video` 的 `request_data` 中才是最终使用的模型。两者可能不同，以 `sandbox_generate_video` 为准。
- **Windows .bat 文件必须使用纯 ASCII 字符**。中文字符即使 `chcp 65001` 也会导致乱码，错误提示如 `'悊鏈嶅姟鍣?' 不是内部或外部命令`。

## 常见陷阱

### 陷阱 1：后端只写方案不生成视频

后端 Agent 加载 brand-film / marketing-video 等技能后，可能会：
1. ✅ 读取参考文件
2. ✅ 生成完整的创意方案（Brand Film Spec）
3. ✅ 写出格式化 prompt（详细的镜头、色彩、灯光说明）
4. ❌ **但不会执行实际视频生成**

**判断方法：** 轮询时出现 `sandbox_write` 写入 spec 文件 + `"创意方案已保存"` + 展示 prompt 文本 → 但**无 `sandbox_generate_video` 调用** → 说明只停在策划阶段。

**解决方法：** 用同一 thread_id 重新提交，message 直接要求执行生成：
```bash
python3 {baseDir}/scripts/submit_run.py --thread-id THREAD_ID --message "请按照你刚才规划的创意方案和提示词，实际生成视频。直接执行，不要再写方案了。"
```
新 run 的 output 中会出现 `sandbox_generate_video` 调用（含 ModelName、Duration、ImageList），此时才是真正在渲染。然后按标准轮询流程继续。

### 陷阱 2：模型名称需精确指定

后端可能自动选择 VIP/增强版模型（如 `seedance2.0_fast_vision`）而非用户指定的基础版。如果用户指定了模型，务必在 message 中明确写出完整模型名称，并在提交后检查 `general_agent_settings` 确认。

已知模型标识参考（来自 biz API 返回的数据）：
- `seedance2.0_fast_vision` — Seedance 2.0 Fast 图生视频版（VIP）
- `seedance_2.0_fast` — Seedance 2.0 Fast

不同模型消耗的积分不同，务必按用户要求精确指定。

### 陷阱 3：get_thread 输出不显示 run_state 数值

`get_thread.py` 只打印 `"本次创作进行中"` 等文字，不显示数字状态码。如需精确判断完成/失败，直接调原始 API：

```bash
curl -s -X POST "https://xyq.jianying.com/api/biz/v1/skill/get_thread" \
  -H "Authorization: Bearer ***" \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"THREAD_ID","run_id":"RUN_ID","after_seq":0}' \
  | python3 -c 'import sys,json; d=json.loads(sys.stdin.read()); run=d["data"]["thread"]["run_list"][0]; print(f"state={run[\"state\"]} (3=完成, 2=运行中, 4=失败)")'
```

### 陷阱 4：模型选择被后端覆盖

客户端无法强制指定底层模型。即使消息中写明 `[使用模型: seedance_2.0_fast]`，后端 Agent 仍会按自己的逻辑选择（通常选 VIP 版）。

**正确做法：** 通过 `template_id` + `tool_key` 参数指定模板，而不是在消息里写模型名。从 `/api/biz/v1/template/list` 获取模板列表，匹配后传给 submit_run。

### 陷阱 5：Video URL 检测

视频生成完成后，产物 URL 可能在 artifact 的 `content[].data` 中（JSON 字符串嵌套）。需要用 `json.loads` 解嵌套后提取 `data.video.url`。不在 entry_list 的 message 文本中。检测时直接搜索 `"everphoto"` 比搜索 `"mp4"` 更可靠。

## 模型选择机制

xyq 后端 Agent 有自己的模型选择逻辑，**客户端无法强制指定底层模型**。即使在消息中写明模型名，后端也会根据任务类型自动选最优模型（通常是 VIP 版）。

**可用的控制方式：**
- 通过 `template_id` + `tool_key` 参数指定模板（如 Seedance_2_0），走特定模板流程
- 从 `/api/biz/v1/template/list` 获取模板列表，每个模板有 `template_id`、`tool_key`、`generation_mode`
- 底层模型标识（`seedance2.0_fast_vision`、`seedance_2.0_fast`）仅作参考，实际调用由后端决定

## OpenAI 兼容代理

可通过 `scripts/xyq_openai_proxy.py` 启动本地代理，将 xyq API 包装为 OpenAI `/v1/chat/completions` 格式。

```bash
python3 {baseDir}/scripts/xyq_openai_proxy.py --port 8800
```

代理功能：
- `GET /v1/models` — 返回 OpenAI 格式模型列表
- `GET /api/models` — 返回 xyq 完整模板列表（30 个模板 + 底层模型）
- `POST /v1/chat/completions` — 接收 OpenAI 格式请求，转发给 xyq
- `GET /health` — 健康检查
- 自动加载同目录或上级目录的 `.env` 文件（`XYQ_ACCESS_KEY=xxx`）
- 选中模板时自动传 `template_id` + `tool_key` 给 submit_run

## Windows .bat 注意事项

- **禁止在 .bat 文件中使用中文**：即使 `chcp 65001`，UTF-8 编码的中文仍会乱码（如"悊鏈嶅姣鍣"）。所有 .bat 内容必须用 ASCII 英文。
- 启动代理用 `start "title" python script.py` 新窗口方式，避免阻塞。

## 参考文件

- [brand-film-spec-example.md](references/brand-film-spec-example.md) — 品牌片创意方案示例（仙居杨梅），可作为同类任务的 prompt 风格参考
- [api-endpoints.md](references/api-endpoints.md) — 小云雀后端 API 端点参考（含 run_state 对照表、已知模型标识、认证方式说明）
- [style-library-api.md](references/style-library-api.md) — 小云雀 30 个模板全量列表（含 Agent 模式/产品推广/沉浸式短片/智能长视频四类）及小说风格库 API 获取方式
- [novel_style_library.md](references/novel_style_library.md) — 小云雀 Novel 风格库完整列表（70+ 种风格），按电影/影视、3D渲染/游戏、插画/二次元、特色/艺术四类整理。如需指定视觉风格，可从此文档选取风格名称传入 message。
- [asset-prompt-masters-guide.md](references/asset-prompt-masters-guide.md) — 资产提示词母版指路（角色定妆图A版/场景设计图A版/道具设计图A版），位于 OpenClaw agent-k1skt8 工作区，用于从剧本提取资产后生成专业 AI 提示词。
- [references/openai-proxy.md](references/openai-proxy.md) — OpenAI 兼容代理服务器使用说明
- [references/xyq-api-models.md](references/xyq-api-models.md) — xyq API 模型/模板列表参考
