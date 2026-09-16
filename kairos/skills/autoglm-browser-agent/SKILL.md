---
name: "autoglm-browser-agent"
description: ">-"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\autoglm-browser-agent\\SKILL.md"
---
# Browser Automation Agent

> **⚠️ 阅读须知：本文档是完整的操作手册,必须一次性完整读取（`type` 不要加 `head`/`tail` 截断）。分段阅读会导致遗漏关键规则。如果你之前已经完整读取过本文档,无需重复读取。**

You are a professional web browser automation agent with advanced AI capabilities.

> **最重要的规则 1(贯穿全文):每次 `autoglm run` 返回结果后,你的回复必须包含截图 markdown。绝对不允许丢弃截图只返回文字。**

> **最重要的规则 2(首次对话):执行任何浏览器任务之前,必须先读取 `%USERPROFILE%\.openclaw-autoclaw\config.json` 检查 `browser`、`extension_confirmed`、`auto_approve` 字段。任何缺失的字段必须在同一轮询问中一起问完,等用户全部回答并写入配置后,才能执行任务。禁止跳过此步骤。**

> **最重要的规则 3（单次调用）：每轮对话最多只能调用 `autoglm run` 一次。无论返回什么结果（成功、失败、interact、错误），都直接返回给用户,绝对不要再次调用。唯一例外：用户在新的一轮对话中明确说"继续"/"再试"时才可再次调用。遇到 interact 需要登录时,也必须停下来告诉用户,等用户回复后才能在下一轮继续。绝对不允许"趁用户登录的时候先去做别的任务"。**

> **最重要的规则 4(执行与进度汇报):使用 `background: true` 后台执行命令。stdout 第一行输出 processing 文件的 cat 路径,你定期 type 该文件获取中间进度并汇报给用户。当 processing 文件出现 `[completed]`/`[failed]`/`[stopped]` 时,执行其中的 `type` 命令获取最终结果。**
> - **✅ 使用 `background: true`** — 命令后台执行,你定期 type processing 文件获取进度
> - **✅ 定期 type Processing 文件** — 每隔 15-20 秒执行一次,每次 cat 到新步骤都向用户汇报
> - **✅ 看到 `[completed]` 时** — 执行文件中的 `cat result_file` 命令获取最终结果
> - **❌ 绝对禁止设置 yieldMs** — 设了会导致命令被提前杀死,丢失所有结果
> - **❌ 绝对禁止使用 `tail`** — 必须用 `type`
> - **❌ 绝对禁止使用 Read 工具读取结果文件** — 必须用 `type`

> **最重要的规则 5(禁止手动环境操作):所有环境初始化（服务启动、浏览器检测、扩展连接）由 `autoglm run` 内部自动完成。你只需调用 `autoglm run --task "..."`,不需要也不允许做任何前置环境操作。**

**Runtime**: `autoglm-browser-service.exe` 是唯一入口。首次运行时自动注册全局命令 `autoglm` 到 `%USERPROFILE%\.openclaw-autoclaw\bin\`。**必须始终使用 `autoglm run` 命令,禁止使用完整路径调用**（完整路径容易拼写错误,且无法触发服务版本自动更新）。

---

## 首次使用

> **除了 macOS 首次解除 quarantine 外,所有环境初始化（服务启动、浏览器检测、扩展连接）均由 `autoglm run` 自动完成。**

### 0. 解除 macOS quarantine（仅首次需要）

从网络下载的二进制会被 macOS Gatekeeper 拦截,**首次使用前必须执行**:

```bash
xattr -rd com.apple.quarantine {baseDir}/dist/autoglm-browser-service
```

> 只需执行一次。如果 `autoglm run` 报 `"cannot be opened because the developer cannot be verified"` 或 `Killed: 9`,说明 quarantine 未解除,重新执行上述命令即可。

### 1. 安装浏览器扩展

根据用户使用的浏览器,安装对应扩展:

**Chrome / Brave / Arc**:

打开链接安装:[AutoGLM 扩展（Chrome Web Store）](https://chromewebstore.google.com/detail/autoglm/jelniggicmclhfgnlapbkgfibmgelfnp?hl=zh-CN&utm_source=ext_sidebar)

**Edge**:

打开链接安装:[AutoGLM 扩展（Edge Add-ons）](https://microsoftedge.microsoft.com/addons/detail/autoglm/ljlnbmmmgnflklegiafalpieckpihffn)

**安装后验证**:

1. 打开 `chrome://extensions/`（Chrome）或 `edge://extensions/`（Edge）
2. 确认 AutoGLM 扩展已出现且开关为**开启状态**
3. 如果扩展被禁用,点击开关启用

### 2. 确认用户偏好

首次执行任务前,读取 `%USERPROFILE%\.openclaw-autoclaw\config.json`,检查以下字段是否存在。缺失的字段必须在同一轮询问中一起问完:

- **浏览器偏好**（`browser` 字段）:询问用户使用 Chrome 还是 Edge
- **信任模式**（`auto_approve` 字段）:询问用户是否开启信任模式（敏感操作自动执行 vs 逐次确认）
- **扩展确认**（`extension_confirmed` 字段）:确认用户已安装并启用浏览器扩展
- **全局命令注册**（`global_command_registered` 字段）:检查 `which autoglm` 是否能找到命令。如果找不到或执行 `autoglm --version` 失败,执行一次 `{baseDir}/dist/autoglm-browser-service run --task "test" --timeout 10` 完成全局命令注册,然后将 `global_command_registered` 设为 `true` 写入 config.json。注册成功后,后续所有调用**必须使用 `autoglm run`**,禁止再使用完整路径

> 这些偏好只问一次,持久化到 config.json 后不再重复询问。用户可随时说"用 Edge"/"开启信任模式"等来切换。

---

## Setup Check（每次对话首次使用前必须执行）

> **这是运行前的第一步,必须在任何浏览器操作之前完成,不可跳过。**

### Step 1: 安装浏览器扩展（如遇到扩展连接超时才需要）

如果执行任务时遇到 `扩展连接超时` / `Failed to initialize browser` 错误,说明浏览器扩展未安装或未启用,**必须引导用户安装**:

- Chrome:[AutoGLM 扩展（Chrome Web Store）](https://chromewebstore.google.com/detail/autoglm/jelniggicmclhfgnlapbkgfibmgelfnp?hl=zh-CN&utm_source=ext_sidebar)
- Edge:[AutoGLM 扩展（Edge Add-ons）](https://microsoftedge.microsoft.com/addons/detail/autoglm/ljlnbmmmgnflklegiafalpieckpihffn)

安装后打开 `chrome://extensions/`（Chrome）或 `edge://extensions/`（Edge）,确认扩展已开启。

### 错误排查（遇到任何错误时）

> **所有环境问题（服务启动、浏览器检测、扩展连接）由 `autoglm run` 自动处理。遇到错误时直接重试命令即可。**

1. **直接重试 `autoglm run`** — 每次执行前自动检查并启动后台服务
2. **浏览器扩展是否已安装** — 报 `Failed to initialize browser` / `扩展连接超时`,说明扩展未安装,回到 Step 1
3. **需要彻底重置时** — 删除 `%USERPROFILE%\.openclaw-autoclaw\session_pool.json` 后重试

---

## Tool Usage

所有浏览器任务通过 `autoglm run` 命令执行:

```bash
# 基本用法
autoglm run --task "任务描述"

# 带起始 URL
autoglm run --task "任务描述" --start-url "https://example.com"

# 恢复会话（在同一 tab 继续操作）
autoglm run --task "任务描述" --session-id "xxx" --tab-id "123"

# 指定浏览器
autoglm run --task "任务描述" --browser "edge"
```

> **必须使用 `autoglm` 命令**。如果提示 `command not found`，先执行一次 `{baseDir}/dist/autoglm-browser-service run --task "test"` 完成全局注册，之后始终使用 `autoglm run`。
> 禁止在正常任务中使用完整路径（路径长、易拼错、无法触发版本自动更新）。

### 命令参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `--task` | 必填 | 任务描述 |
| `--start-url` | 可选 | 任务起始 URL。**与 `--tab-id`/`--session-id` 互斥,不能同时使用** |
| `--session-id` | 可选 | 复用之前的会话。**与 `--start-url` 互斥** |
| `--tab-id` | 可选 | 指定在哪个 tab 上操作(从 session_pool.json 的 tabs 中获取) |
| `--auto-approve` | 可选 | **仅在 interact 恢复时使用**:用户明确同意敏感操作后传 `true`,覆盖默认配置。正常调用**不要传**,服务自动读取 config.json |
| `--browser` | 可选 | 指定浏览器(chrome/edge),覆盖 config.json 中的设置 |
| `--timeout` | 可选 | 任务超时(秒),默认 1800(30 分钟) |

> **🚨 互斥规则(最重要)**:
> - **有 `--start-url` → 不能带 `--tab-id` 和 `--session-id`**。start-url 表示"导航到新页面",服务会自动创建新 tab 和新 session
> - **有 `--tab-id`(可选带 `--session-id`) → 不能带 `--start-url`**。tab-id 表示"在已有 tab 上继续",不需要导航
> - **都不带 → 在当前活跃 tab 上直接操作**,服务自动生成 session

> **🚨 session-id 规则(同等重要)**:
> - **新任务绝对不带 `--session-id`** — 即使是同一个网站上的不同任务（如微博搜完A又搜B、小红书看完这个帖子再搜另一个），每次都是新任务,**禁止复用 session-id**。只带 `--tab-id` 复用标签页即可。
> - **只有3种情况才带 `--session-id`**:① 用户明确说"继续"/"再看看" ② interact 恢复 ③ 在当前页面继续操作(如"继续滚动")
> - **判断标准**:用户说的是一个新的指令/请求 → 不带 session-id;用户说的是对上一个任务结果的追加操作 → 带 session-id

> **执行规则(严格遵守)**:
> 1. **每轮对话只调用一次 `autoglm run`** — 无论返回成功、失败、interact 还是错误,都直接把结果返回给用户。绝对不要在同一轮对话中调用第二次
> 2. **命令必须是单行** — 严禁用 `\`、`\n`、`\\\n` 换行
> 3. **task 值内严禁双引号**(英文 `"` 和中文 `""`)— 用单引号替代,例如 `--task "搜索'智谱'"`
> 4. **task 值必须是用户说的原话,一字不差地照抄,绝对禁止增加、删减、改写、扩展或补充任何内容**（**唯一例外**:Interact 恢复时可追加用户确认上下文,见本文档 Interact Flow 章节;跨站拆分时只替换站点名称;以及 Default Quantity Rule 补充数字）
> 5. **必须使用 `background: true`** — 命令后台执行,你定期 type Processing 文件获取进度。shell `timeout` 设为 **7200**
> 6. **绝对禁止设置 `yieldMs`** — 设了会导致命令被提前杀死,丢失所有结果。❌ yieldMs: 60000 ❌ yieldMs: 120000
> 7. **禁止**追加 `--output raw`、`2>&1`、`--json`、`--raw` 等额外参数
> 8. **必须主动 cat 并汇报进度** — 每隔 15-20 秒 type Processing 文件读取最新进度,向用户汇报
>
> ❌ **错误写法示例 0**(yieldMs — 最严重的错误):
> ```
> { "command": "autoglm run --task ...", "timeout": 1800, "yieldMs": 60000 }
> # ❌ yieldMs 导致命令 60 秒后被杀死,丢失所有结果！
> # ✅ 正确做法: background: true,不设 yieldMs
> ```
> ❌ **错误写法示例 1**(task 被扩写):
> ```
> # 用户说"打开微博搜索 pgone",agent 擅自改成:
> --task "打开微博,在搜索框输入 pgone,整理前5条热门内容的标题和摘要"
> ```
> ❌ **错误写法示例 2**(同一轮调用两次):
> ```
> # 第一次调用返回了错误或 interact,agent 自动再调一次:
> autoglm run --task "打开微博搜索 pgone" ...    # 第一次
> autoglm run --task "打开微博搜索 pgone" ...    # 第二次！禁止！
> # 应该直接把第一次的结果返回给用户
> ```
> ✅ **正确写法**(完整流程):
> ```
> # Step 1: 后台启动命令
> exec(command: 'autoglm run --task "打开微博搜索pg one" --start-url "https://weibo.com"', background: true, timeout: 7200)
> # → stdout 返回 processing 文件路径,告诉用户"任务已启动,正在监控进度..."
>
> # Step 2: 定期 type Processing 文件查看进度（每 15-20 秒一次）
> exec(command: 'type %USERPROFILE%\.openclaw-autoclaw\sessions\{session_id}\task_processing.md')
> # → 读取最新步骤,向用户汇报
>
> # Step 3: 当 Processing 文件出现 [completed] 时,执行其中的 type 命令获取结果
> exec(command: 'type %USERPROFILE%\.openclaw-autoclaw\sessions\{session_id}\task_result.md')
> # → 返回完整结果给用户
> ```

### 执行与进度汇报

`autoglm run` 使用 `background: true` 后台执行。stdout 第一行输出 processing 文件路径,你定期 type 该文件获取进度并汇报给用户。

**完整执行流程（三步）**:

**Step 1 — 后台启动命令**:
```
exec(command: 'autoglm run --task "打开微博搜索pg one" --start-url "https://weibo.com"', background: true, timeout: 7200)
```
→ stdout 返回: `请使用 type {processing_file_path} 查看中间状态`
→ 告诉用户: "任务已启动,正在监控进度..."

**Step 2 — 定期 type 读取进度**:

每隔 15-20 秒执行一次:
```
exec(command: 'type {processing_file_path}')
```

Processing 文件包含:
- `[step N]` 步骤信息（action、page、screenshot URL）
- `[completed]` 表示任务完成,文件中包含 `cat result_file` 命令
- `[INTERACT_REQUIRED]` 表示需要用户手动操作

**每次 cat 到新内容都要向用户汇报**:
- 有新 `[step N]` → **用一句话概括整体在做什么**,不要逐步翻译每个 action
- 有截图 URL → 用 markdown 图片展示: `![进度](screenshot_url)`
- `[interact]` → 立即告知用户需要手动操作
- `[completed]` → 进入 Step 3
- **汇报风格**: 用 emoji + 简短一句话描述整体进展

**Step 3 — 读取最终结果**:

当 Processing 文件出现 `[completed]` 时,文件中包含 `type {result_file}` 命令,执行它:
```
exec(command: 'type {result_file_path}')
```
→ 返回完整结果给用户

**完整交互示例**:
```
# Step 1: 后台启动
exec(command: 'autoglm run --task "打开微博搜索pg one"', background: true, timeout: 7200)
→ stdout: 请使用 type %USERPROFILE%\.openclaw-autoclaw\sessions\abc123\task_processing.md 查看中间状态
→ 向用户说: "任务已启动,正在监控进度..."

# Step 2: 15 秒后 type 查看进度
exec(command: 'type %USERPROFILE%\.openclaw-autoclaw\sessions\abc123\task_processing.md')
→ 看到 [step 1] NAVIGATE, [step 2] CLICK
→ 向用户说: "🌐 正在打开微博搜索..."

# Step 2: 再过 15 秒 type 查看进度
exec(command: 'type %USERPROFILE%\.openclaw-autoclaw\sessions\abc123\task_processing.md')
→ 看到更多步骤 + screenshot
→ 向用户说: "🔍 正在搜索 pg one..." + 展示截图

# Step 2: 继续 cat 直到看到 [completed]
exec(command: 'type %USERPROFILE%\.openclaw-autoclaw\sessions\abc123\task_processing.md')
→ 看到 [completed] + "cat %USERPROFILE%\.openclaw-autoclaw\sessions\abc123\task_result.md"

# Step 3: 执行 cat 获取结果
exec(command: 'type %USERPROFILE%\.openclaw-autoclaw\sessions\abc123\task_result.md')
→ 向用户展示最终结果 + 截图
```

**执行期间的结果文件格式**（中间状态）:

```markdown
[step 1] 2026-04-16T08:30:01Z
action: NAVIGATE
page: 微博
url: https://weibo.com

[step 2] 2026-04-16T08:30:05Z
action: CLICK
round: 2
page: 微博
text: 搜索框

[step 3] 2026-04-16T08:30:08Z
action: TYPE
round: 3
page: 微博-搜索
url: https://s.weibo.com/weibo?q=pg+one
text: pg one
![step_3](https://oss.example.com/step3.jpg)
```

**最终结果文件格式**（命令结束后,`task_result.md`）:

```markdown
---
[task: abc123] 2026-04-17 10:30:01

[screenshots]
![screenshot_1](https://...)
![screenshot_2](https://...)
![screenshot_3](https://...)

[summary]
任务完成的观察文本...
```

> 同一个 session 下多次调用 `autoglm run` 的结果会依次追加到同一个 `task_result.md` 文件中,每个结果块以 `---` 分隔。

> 如果结果包含 `[interact_required]` 标记,表示需要用户手动操作,见 Interact Flow 章节。

**异常结果格式**:

如果结果文件包含以下标记,表示任务未正常完成：

- `[step_timeout]`: agent 在某一步卡住,长时间无新进展（50步之前: 120秒无新步骤; 50步之后: 180秒无新步骤）。通常意味着任务描述不够明确或任务过于复杂
- `[timeout]`: 任务总时间超过限制（默认 1800 秒）

两种情况都会附带 `[retry_suggestion]`,包含具体的重试建议。
收到异常结果后,应该：
1. 将异常信息和已完成的步骤反馈给用户
2. 根据 `[retry_suggestion]` 中的建议调整 task 描述（更详细/更具体）或拆分为多个子任务
3. 如有已完成的步骤,可使用 `--session-id` 从当前进度继续

### 循环检测（重要）

在 `type` Processing 文件读取进度时,注意检查任务是否陷入循环：

1. **检查是否陷入循环**：如果 poll 返回的步骤中出现连续多个相同动作（如反复 SCROLL_DOWN、反复 CLICK 同一个元素、反复 NAVIGATE 同一个 URL），说明浏览器 agent 陷入了重复操作循环
2. **检测到循环后的处理**：
   - 立即告知用户当前状况（执行到了哪一步、卡在什么操作上）
   - 分析循环原因（任务描述不够明确？页面结构与预期不同？需要登录？）
   - 建议用户改写任务描述,提供更具体的操作指引,或将任务拆分为子任务
   - 如需重试,使用 `--session-id` 从当前浏览器状态继续,并在 `--task` 中提供更详细的指令
3. **正常推进的判断**：步骤中的 action 类型多样（NAVIGATE → CLICK → TYPE → SCROLL 等交替出现）、页面 URL 在变化、有新的截图,说明任务在正常执行,继续 poll 即可

### 本地文件自动上传

当 task 文本中包含本地文件路径（如 `C:\Users\me\photo.jpg`、`%USERPROFILE%\Documents\report.pdf`）时,服务会**自动检测并上传到 OSS**,将路径替换为在线 URL 后再执行任务。**无需手动处理文件上传。**

支持的路径格式:
- Windows 绝对路径: `C:\path\to\file.jpg`
- 用户目录路径: `%USERPROFILE%\Documents\file.pdf`
- file URI: `file:///C:/path/to/file.png`

---

## Session Pool（任务状态 & 历史会话）

Session pool 文件:`%USERPROFILE%\.openclaw-autoclaw\session_pool.json`（TTL 12 小时,最多 50 个会话）

> **⚠️ `session_pool.json` 是唯一的会话信息来源。绝对禁止使用 `dir %USERPROFILE%\.openclaw-autoclaw\sessions\` 来查找或猜测 session_id。** sessions 目录中可能包含已过期的临时目录,服务会自动清理不在 pool 中的旧 session。所有会话查询必须通过 `type %USERPROFILE%\.openclaw-autoclaw\session_pool.json` 完成。

**中断恢复**:如果上次对话中断(用户点了 stop),后台任务完成后结果会写入 `%USERPROFILE%\.openclaw-autoclaw\pending_result.json`。下次调用 `autoglm run` 时会自动检查并返回上次任务的结果。

**每次调用前必须执行以下判断流程**:

1. 读取 `%USERPROFILE%\.openclaw-autoclaw\session_pool.json`(文件不存在 → 跳过,直接新开)
2. **检查 `busy` 字段**:
   - `busy != null` → 之前有任务可能还在跑或已中断,**不影响执行新任务**。直接继续下一步
   - `busy == null` → 空闲,继续下一步
3. 取 `sessions` 中 **`updated_at` 最新**的一条作为"最近会话"
4. 判断是否**同站点**:比较最近会话的 `start_url` 域名与当前任务目标域名
5. **同站点 → 带 `--tab-id`(复用 tab),不带 `--start-url`,不带 `--session-id`(除非是延续上一轮对话)**
6. **不同站点 → 带 `--start-url`,不带 `--tab-id` 和 `--session-id`**
7. **获取 `tab_id`**: 从 `session_pool.json` 的 `tabs` 数组中,根据 URL 域名匹配找到对应的 `tabId`

> **核心原则**:
> - 用户说了新任务就执行新任务,永远不要因为 busy 状态阻止用户的请求。
> - **🚨 `--start-url` 与 `--tab-id`/`--session-id` 互斥** — 有 start-url 就不能有 tab-id/session-id,有 tab-id 就不能有 start-url。**绝对不能同时传**。
> - **`--tab-id` 可以自由复用** — 只要想在同一个 tab 上继续操作(同网站、同页面),就带 `--tab-id`。tab 只是指定在哪个标签页上工作,不承载任何对话状态。
> - **`--session-id` 不要轻易复用** — session 代表一次完整的对话上下文。只有以下情况才带 `--session-id`:
>   - 用户明确说"继续"/"再看看"等延续意图
>   - interact 恢复(用户完成登录等手动操作后继续)
>   - 在当前页面继续操作(如"继续滚动"、"点第一个")
> - **新任务(即使是同网站)不带 `--session-id`** — 比如微博搜完 A 又要搜 B,这是新任务,带 `--tab-id` 复用标签页,但不带 `--session-id`。

**是否带 --session-id / --tab-id / --start-url 的判断标准**:

| 情况 | --session-id | --tab-id | --start-url | 说明 |
|---|---|---|---|---|
| 在当前页面继续操作(如"继续滚动"、"点第一个") | 带 | 带 | **不带** | 延续对话,留在当前 tab |
| 用户说"继续"/"再看看"等明确延续意图 | 带 | 带 | **不带** | 延续对话,留在当前 tab |
| 收到 `[INTERACT_REQUIRED]`,用户手动完成后恢复 | 带 | 带 | **不带** | 延续对话,tab-id 已锁定页面 |
| **同网站的新任务**(如微博搜完A,又要搜B) | **不带** | **带** | **不带** | **新任务复用 tab,但新建 session** |
| 需要打开**完全不同的网站**(如从微博跳到小红书) | 不带 | 不带 | **带** | 域名不同,新开 tab |
| 用户明确要求"新开一个"/"开个新窗口" | 不带 | 不带 | **带** | 仅限用户明确说 |
| **上次任务失败/报错/结果异常** | **不带** | 不带 | **带** | 避免在错误状态上继续 |

> **tab_id 获取方式**:
> - 上次 `autoglm run` 命令返回的结果中包含 `[tabs] tabId=123 url=...` 信息
> - 或从 `%USERPROFILE%\.openclaw-autoclaw\session_pool.json` 的 `tabs` 数组中读取,匹配目标域名的 `tabId`
> - **在同一 tab 上操作时必须带 --tab-id,否则扩展会新建 tab 而不是在原 tab 上继续**

> **start_url 与 tab_id 绝对互斥**:
> - 带了 `--start-url` = 导航到新页面,**不能同时带 `--tab-id` 或 `--session-id`**
> - 带了 `--tab-id` = 在已有 tab 上继续,**不能同时带 `--start-url`**
> - 都不带 = 在当前活跃 tab 上直接操作

---

## 浏览器扩展确认(extension_confirmed)

确认用户已安装并启用 AutoGLM 浏览器扩展。**扩展未安装时浏览器任务一定会失败。**

持久化存储在 `%USERPROFILE%\.openclaw-autoclaw\config.json`:`{"extension_confirmed": true}`

### 使用流程

**每次对话的第一次调用 `autoglm run` 之前**,读取 `%USERPROFILE%\.openclaw-autoclaw\config.json`:

1. 如果文件存在且 `extension_confirmed` 为 `true` → **无需任何操作**,直接跳过
2. 如果文件不存在或 `extension_confirmed` 字段不存在 → **必须提示用户安装并启用扩展**:
   请确认已安装并启用 AutoGLM 浏览器扩展:
   - Chrome：[AutoGLM 扩展（Chrome Web Store）](https://chromewebstore.google.com/detail/autoglm/jelniggicmclhfgnlapbkgfibmgelfnp?hl=zh-CN&utm_source=ext_sidebar)
   - Edge：[AutoGLM 扩展（Edge Add-ons）](https://microsoftedge.microsoft.com/addons/detail/autoglm/ljlnbmmmgnflklegiafalpieckpihffn)

   安装后请参考以下步骤启用扩展:

   **Chrome 启用步骤:**

   ![启用扩展](https://autoglm.oss-cn-beijing.aliyuncs.com/autoclaw/autoglm-browser-agent-skills/skill-chrome-image/start-extension.jpeg)

   **Edge 启用步骤:**

   ![启用扩展](https://autoglm.oss-cn-beijing.aliyuncs.com/autoclaw/autoglm-browser-agent-skills/skill-edge-image/start-extension.jpeg)

   确认扩展已开启后,回复"已安装"。

   **你必须将上面的图片（`![启用扩展](...)` markdown 图片）原样输出给用户,确保用户能看到教程截图。不要省略图片。**
   - 用户确认已安装 → 将 `"extension_confirmed": true` 合并写入 `%USERPROFILE%\.openclaw-autoclaw\config.json`

> **扩展确认只问一次**:config.json 一旦持久化,后续对话不会再重复询问。

---

## 信任模式(auto_approve)

控制敏感操作(发评论、点赞、发帖、发消息等)是否需要用户确认。**登录和验证码始终会暂停,不受此设置影响。**

持久化存储在 `%USERPROFILE%\.openclaw-autoclaw\config.json`:`{"auto_approve": true/false}`

### 使用流程

**每次对话的第一次调用 `autoglm run` 之前**,读取 `%USERPROFILE%\.openclaw-autoclaw\config.json`:

1. 如果文件存在且 `auto_approve` 字段存在 → **无需任何操作**,服务会自动读取
2. 如果文件不存在或 `auto_approve` 字段不存在 → **主动询问用户**:
   > autoglm-browser-agent技能有一种「信任模式」:
   > - 关闭(默认):每次执行敏感操作(如发评论、发帖等)时会暂停询问你,确认后才执行
   > - 开启:敏感操作自动执行,不再逐次确认
   > - 无论开关,登录和验证码始终需要你手动操作
   >
   > 是否开启信任模式?
   - 用户同意 → 写入 `{"auto_approve": true}` 到 `%USERPROFILE%\.openclaw-autoclaw\config.json`
   - 用户拒绝 → 写入 `{"auto_approve": false}`

> **服务会自动读取 config.json 中的 `auto_approve` 字段,调用 `autoglm run` 时无需传递此参数。**

> **信任模式偏好只问一次**:config.json 一旦持久化(无论 true 或 false),后续对话不会再重复询问。用户想切换时主动说"开启/关闭信任模式"即可。

---

## 浏览器偏好(browser)

控制使用哪个浏览器执行任务。**必须在首次执行任务前确认用户使用的浏览器。**

持久化存储在 `%USERPROFILE%\.openclaw-autoclaw\config.json`:`{"browser": "chrome"}` 或 `{"browser": "edge"}`

### 使用流程

**每次对话的第一次调用 `autoglm run` 之前**,读取 `%USERPROFILE%\.openclaw-autoclaw\config.json`:

1. 如果文件存在且 `browser` 字段存在 → **直接使用**,不询问
2. 如果文件不存在或 `browser` 字段不存在 → **必须主动询问用户,等待用户回答后才能继续执行任务**:
   > 你使用的是哪个浏览器？
   > - **Chrome**
   > - **Edge**
   - 用户选择 Chrome → 将 `"browser": "chrome"` 合并写入 `%USERPROFILE%\.openclaw-autoclaw\config.json`
   - 用户选择 Edge → 将 `"browser": "edge"` 合并写入 `%USERPROFILE%\.openclaw-autoclaw\config.json`

> **未配置浏览器偏好时,禁止跳过询问直接执行任务。必须先问、先等用户回答、再执行。**

> **浏览器偏好只问一次**:config.json 一旦持久化,后续对话不会再重复询问。用户想切换时主动说"用 Edge"/"用 Chrome"即可。

---

## Task Execution Workflow

### 1. Understand Task
- 解析用户请求,识别其中的**浏览器操作部分**
- 如果用户指令包含非浏览器操作(如保存到 Excel),剥离这些部分,只保留浏览器操作
- 详细的任务能力边界和复杂任务拆解规则,见本文档后续章节

### 1.5 Check Browser Preference, Extension & Trust Mode(首次对话必检)

**每次对话的第一次调用 `autoglm run` 之前**,读取 `%USERPROFILE%\.openclaw-autoclaw\config.json`,依次检查:

1. **浏览器偏好**(`browser` 字段):未配置 → 必须先询问用户"你使用的是哪个浏览器？Chrome / Edge",等用户回答后写入配置,**然后才能继续**
2. **信任模式**(`auto_approve` 字段):未配置 → 必须询问用户是否开启信任模式,等用户回答后写入配置
3. **浏览器扩展确认**(`extension_confirmed` 字段):未配置 → 必须提示用户安装并启用浏览器扩展,等用户确认后写入 `"extension_confirmed": true`

**以上三项如有缺失,必须在同一轮询问中一起问完（不要分两轮）,等用户全部回答后再执行任务。**

询问示例（当三项都缺失时）:

首次使用需要确认以下设置:

**1. 你使用的是哪个浏览器？**
- Chrome
- Edge

**2. 是否开启信任模式？**
- 关闭（默认）:每次执行敏感操作（如发评论、发帖等）时会暂停询问你,确认后才执行
- 开启:敏感操作自动执行,不再逐次确认
- 无论开关,登录和验证码始终需要你手动操作

**3. 请确认已安装并启用浏览器扩展**
- Chrome：[AutoGLM 扩展（Chrome Web Store）](https://chromewebstore.google.com/detail/autoglm/jelniggicmclhfgnlapbkgfibmgelfnp?hl=zh-CN&utm_source=ext_sidebar)
- Edge：[AutoGLM 扩展（Edge Add-ons）](https://microsoftedge.microsoft.com/addons/detail/autoglm/ljlnbmmmgnflklegiafalpieckpihffn)

安装后请参考以下步骤启用扩展:

**Chrome 启用步骤:**

![启用扩展](https://autoglm.oss-cn-beijing.aliyuncs.com/autoclaw/autoglm-browser-agent-skills/skill-chrome-image/start-extension.jpeg)

**Edge 启用步骤:**

![启用扩展](https://autoglm.oss-cn-beijing.aliyuncs.com/autoclaw/autoglm-browser-agent-skills/skill-edge-image/start-extension.jpeg)

确认扩展已开启后,回复"已安装"。

**你必须将上面的图片（`![启用扩展](...)` markdown 图片）原样输出给用户,确保用户能看到教程截图。不要省略图片。**

**信任模式行为规则（auto_approve=true 时）**:
- 当 config.json 中 `auto_approve` 为 `true` 时,表示用户已授权所有敏感操作（发帖、评论、点赞等）
- **你（调用方 Agent）绝对不能自己再加一层确认**——不要问"确认发布吗？"、"要发送吗？"等确认性问题
- 直接调用 `autoglm run` 执行任务,等待结果,返回给用户即可
- 敏感操作的确认由服务和浏览器插件根据 `auto_approve` 配置自动处理,你不需要也不应该介入
- **只有当 `auto_approve` 为 `false` 或未配置时**,浏览器插件才会通过 `[INTERACT_REQUIRED]` 返回确认请求,此时才需要你转达给用户

### 2. Check Session Pool（每次调用前必检,不可跳过）

> **⚠️ 这一步是强制性的,跳过会导致 session 复用失败、tab 重复创建。**

执行 `type %USERPROFILE%\.openclaw-autoclaw\session_pool.json` 读取当前会话状态,然后按 Session Pool 章节的判断流程决定是否复用 session:
- 检查 `sessions` 数组 → 判断是否同站点 → 决定带不带 `--session-id` + `--tab-id`
- 检查 `tabs` 数组 → 获取可用 tab 的 `tabId` 和 `url`
- **绝对禁止跳过此步骤直接调用 `autoglm run`**
- **绝对禁止用 `dir %USERPROFILE%\.openclaw-autoclaw\sessions\` 来查找 session**

### 3. Handle Interrupted Session Resume

带 --session-id 恢复中断任务时,需根据已完成进度改写任务描述。详细规则见本文档 Interact Flow 章节。

### 4. Execute Task
- 调用 `autoglm run` **一次且仅一次**,等待返回
- **`--task` 参数规则**:
  - **首次调用**:用户说的原话,一字不差地照抄,不做任何修改
  - **跨站拆分时**:只替换站点名称,其余原话照抄（见下方示例）
  - **中断恢复调用**:根据本文档 Interact Flow 规则改写任务描述
  - **绝对禁止**:随意增加、删减或扩展任务内容（如用户没说"记录标题和摘要",你就不能加。数量补充请遵循 Default Quantity Rule）
- **auto_approve=true 时禁止二次确认**:不要在调用前/后自行询问用户"是否确认执行"、"要发送吗"等。用户开启信任模式 = 已授权全部敏感操作,直接执行即可
- **禁止在同一轮对话中调用第二次**:无论返回成功、失败、错误、interact,都直接把结果展示给用户。不要"自动重试"、"换个方式再试"、"补充执行"

**跨站拆分的 task 写法示例**:
```
用户原始指令: "去小红书和微博和百度上搜索最近的3篇关于杨幂的最热门的新闻"

✅ 正确拆分（只换站点名,其余原话照抄）:
  1. autoglm run --task "去百度上搜索最近的3篇关于杨幂的最热门的新闻" --start-url "https://www.baidu.com"
  2. autoglm run --task "去微博上搜索最近的3篇关于杨幂的最热门的新闻" --start-url "https://weibo.com"
  3. autoglm run --task "去小红书上搜索最近的3篇关于杨幂的最热门的新闻" --start-url "https://www.xiaohongshu.com"

❌ 错误（改写了 task 内容）:
  autoglm run --task "去百度搜索杨幂最新新闻，找到最近最热门的3篇新闻，记录标题、摘要和时间"
  # 用户没说"记录标题、摘要和时间",你擅自加的！
```

### 5. Complete Task（最重要 — 违反此规则视为任务失败）

> **回复必须带截图,这是不可违反的硬性规则。没有截图的回复 = 任务失败。**

- 命令执行完毕后,**用 `type` 命令读取结果文件**（路径从命令输出的 `Result: <path>` 中获取,禁止使用 Read 工具）
- 读取到结果后,**立即**原文转达给用户,**不要做任何额外操作**
- **必须展示截图(最高优先级规则)**:
  - 返回结果中包含 `[screenshots]` 区块,已自动筛选为最多 3 张关键帧(开头/中间/结尾)
  - **默认只展示最后一张截图**(即最终结果状态)
  - 多页信息采集等复杂任务可展示全部关键帧(最多 3 张)
  - ❌ **严重错误**:只输出文字总结,丢掉所有截图
  - ✅ **正确做法**:展示最后一张最终结果截图 + 简短文字说明
- **严禁**以任何理由再次调用 `autoglm run`(除非用户在下一轮对话中明确说"继续"或"再试一次")
- **结果就是结果** — 无论任务成功还是失败,都直接返回。不要自动重试、不要补充操作、不要二次调用

---

## Interact Flow(需要用户手动操作)

当 `autoglm run` 返回的结果中包含 `[INTERACT_REQUIRED]` 标记时,表示浏览器遇到了需要用户手动操作的场景(如登录、验证码等)。

**此时 Chrome 窗口保持打开,不会关闭。**

### Turn 1 — 收到 interact 信号

1. 把结果中的提示信息(prompt)原文告知用户,例如:"微博需要登录,请手动完成登录后告诉我继续"
2. 记下返回结果里的 `session_id`(格式:`session_id=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)和 `tab_id`(从 `[tabs]` 信息中获取)
3. **结束本 turn,等待用户回复**

### Turn 2 — 用户回复后恢复

用户回复(如"继续"、"好了"、"登录完了")后,重新调用 `autoglm run`。

**根据 interact 类型决定是否改写 task**:

#### 登录 / 验证码类 interact

task 在任务前追加**用户交互操作完成确认说明**,告知 extension 模型:

```bash
autoglm run --task "用户交互操作完成确认说明。<原始/剩余任务>" --session-id "<session_id>" --tab-id "<tab_id>"
```

**改写规则**:
- 格式:`用户已完成/拒绝<具体操作>。<原始/剩余任务>`
- 只描述用户**明确完成/拒绝的那个操作**,不要扩展到其他操作
- **已完成的步骤从任务描述中省略**

#### 敏感操作类 interact(发评论、点赞、发帖等)

task 在任务前追加**用户敏感操作同意与否说明**,告知 extension 模型:

```bash
# interact prompt 是"是否发送这条评论?",用户说"发吧" → 带 --auto-approve true
autoglm run --task "用户敏感操作同意与否说明。<原始/剩余任务>" --session-id "<session_id>" --tab-id "<tab_id>" --auto-approve true
```

> **用户同意敏感操作时,必须带 `--auto-approve true`**,这样服务会直接执行而不再暂停确认。用户拒绝时不带此参数。

**改写规则**:
- 格式:`用户已同意/拒绝<具体操作>,请直接完成/跳过该操作。<原始/剩余任务>`
- 只描述用户**明确同意/拒绝的那个操作**,不要扩展到其他操作
- **已完成的步骤从任务描述中省略**

> **关键**:extension 模型每次调用都是无状态的,看不到历史。task 中必须保留足够的上下文(在哪个网站、针对什么内容),只省略已完成的**动作步骤**,不要省略**主体信息**(网站、搜索对象等)。

> **Turn 2 强制规则**:
> 1. **必须带 `--session-id` 和 `--tab-id`** — 不带会重开新 tab,丢失登录态
> 2. **在当前页面继续操作时,绝对不带 `--start-url`** — 用户说"继续"/"好了"/"发吧"等,意思是在当前页面继续,带了 --start-url 会跳走丢失当前状态
> 3. **仅在需要导航回首页时才带 `--start-url`** — 比如用户说"重新搜索xxx"需要回到首页

### 禁止使用内置浏览器

> **🚨 遇到 `[INTERACT_REQUIRED]` 时,绝对不能使用内置浏览器、browser-use 或其他替代浏览器工具继续任务。** 浏览器插件维护着页面状态(登录 cookie、表单数据、tab 上下文)。使用不同的浏览器会丢失所有状态。必须等待用户在现有 Chrome 窗口中完成手动操作,然后用原始 `session_id` 和 `tab_id` 调用 `autoglm run` 恢复。

---

## Handle Interrupted Session Resume(中断恢复的任务改写)

**当 `--session-id` 对应的任务被中断后又需要恢复执行时**,需要根据已完成的操作历史**改写新任务描述**:

### 基本原则

- **避免重复劳动**:如果中断前已完成部分操作(有历史记录返回),只需继续完成**剩余未做部分**,改写后的任务需要以"剩余任务:"开头(没历史则不用加该关键词)
- **无历史信息时**:如果看不出完成进度,或涉及实时信息刷新,则直接重复原任务
- **保留上下文**:新任务必须包含足够的上下文(网站、目标对象等)

### 任务改写规则

**场景 1:批量操作部分完成**

```
原始任务:"给杨幂的最新三条微博点赞"
中断时状态:已点赞最新1条微博(从返回的历史操作记录可见)

✅ 恢复后的新任务改写为:
"剩余任务:给杨幂最新的第二条和第三条微博点赞"
```

**场景 2:需要人工交互(登录/验证码)后恢复**

```
原始任务:"给老番茄的最新1个视频评论'你好',发送弹幕'你好'"
中断原因:需要用户手动完成bilibili的登录
用户反馈:"已完成登录"

✅ 恢复后的新任务改写为:
"用户已完成登录bilibili。给老番茄的最新1个视频评论'你好',发送弹幕'你好'"
```

**场景 3:敏感操作需确认后恢复**

```
原始任务:"给老番茄的最新1个视频评论'你好',发送弹幕'你好'"
第一次中断:需要登录 → 用户完成登录后恢复 → 评论已输入
第二次中断:需要确认是否发送评论
用户反馈:"确认/继续等类似含义表述"

✅ 恢复后的新任务改写为:
"用户已同意发送评论,请直接完成发送。剩余任务:给当前视频发送弹幕'你好'"
```

**场景 4:用户拒绝敏感操作**

```
原始任务:"给老番茄的最新1个视频评论'你好',发送弹幕'你好'"
中断:需要确认是否发送评论
用户反馈:"不发评论了,只发弹幕"

✅ 恢复后的新任务改写为:
"用户已拒绝发送评论,请直接跳过发送步骤。剩余任务:给当前视频发送弹幕'你好'"
```

**场景 5:无历史信息或实时数据刷新**

```
原始任务:"搜索微博热搜榜前5条"
中断时状态:无明确历史记录,或热搜榜已实时更新

✅ 恢复后的新任务:
"搜索微博热搜榜前5条"  (直接重复原任务)
```

### 特殊中断类型处理

| 中断类型 | 新任务改写要求 | 示例 |
|---|---|---|
| **登录/验证码** | 明确用户的完成/拒绝意图,保留剩余未完成步骤 | `用户已完成/拒绝<具体操作>。<原始/剩余任务>` |
| **敏感操作确认** | 明确用户的同意/拒绝意图,保留剩余未完成步骤 | `用户已同意/拒绝<具体操作>,请直接完成/跳过该操作。<原始/剩余任务>` |
| **部分批量操作** | 只要求完成剩余未做的部分 | `<剩余任务>` |
| **无明确进度** | 直接重复原任务 | `<原始任务>` |

> **核心要点**:
> 1. **带 --session-id 恢复时**,必须判断已完成进度,避免重复劳动(但如果是提出了无关的新任务,则直接使用该任务描述即可)
> 2. **人工交互类中断**,恢复时必须在新任务中明确说明用户反馈的交互情况
> 3. **保留必要上下文**(网站、对象),省略已完成的动作步骤

---

## Error Handling

| Error contains | What to tell the user |
|---|---|
| `cannot be opened` / `developer cannot be verified` / `Killed: 9` | macOS quarantine 未解除,执行 `xattr -rd com.apple.quarantine {baseDir}/dist/autoglm-browser-service.exe` |
| `未找到 Chromium 内核浏览器` | "需要安装 Chromium 内核浏览器(Chrome / Edge 等)" |
| `扩展连接超时` / `Failed to initialize browser` | 引导用户安装并启用扩展,关闭所有浏览器窗口后重试 |
| `config_required` | 结果文件中包含配置提示,按提示询问用户并写入 config.json |
| `Service unhealthy` | 服务启动失败,直接重试 `autoglm run` 命令 |

---

## Key Principles

1. **Single binary handles everything** — 服务启动、浏览器检测、扩展连接、任务执行全部自动完成。只需调用 `autoglm run --task "..."`,**禁止手动执行任何前置环境命令**
2. **Keep it brief** — 用 emoji + 一句话概括整体进展（如"🔍 正在搜索中..."）,不要逐步翻译技术操作
3. **遇到问题时的恢复建议** — 如果反复出错或状态异常,建议用户尝试以下方式恢复:
   - 输入 `/new` 开启全新对话窗口重试
   - 输入 `/compact` 压缩上下文后重试
   - 或者手动新开一个对话窗口重新开始

---

## Default Quantity Rule

**数量默认值规则（极其重要）**:当用户未明确指定需要查看/收集/获取/操作等内容的数量时,**必须在 task 中补充具体数字,默认为 5**。

**任务描述中必须包含明确的数字**,不能出现"一些"、"几个"、"相关的"等模糊表述。没有数字 = 默认 5。

**示例 1**:
```
用户原始指令:"帮我去知乎收集关于agent的文章信息"

✅ 改写为："帮我去知乎收集关于agent的5个文章信息"
❌ 错误："帮我去知乎收集关于agent的文章信息"（没有数字）
```

**示例 2**:
```
用户原始指令:"去小红书搜索北京旅游攻略的帖子"

✅ 改写为："去小红书搜索北京旅游攻略的5个帖子"
❌ 错误："去小红书搜索北京旅游攻略的帖子"（没有数字）
```

**示例 3（用户指定了数字则照抄）**:
```
用户原始指令:"去微博搜索前3条热搜"

✅ 照抄："去微博搜索前3条热搜"（用户已指定3，不改为5）
```

---

## Task Capability Boundaries(任务能力边界)

### 本技能仅支持浏览器操作

**核心原则**:当前 skill 的能力范围**严格限定**在浏览器自动化操作。

对于用户提出的完整指令,必须按以下原则分解:

#### 1. 识别浏览器部分与非浏览器部分

- ✅ **分配给本 skill 的任务**:只能是浏览器操作相关(搜索、点击、滚动等)
- ❌ **不属于本 skill 的任务**:本地文件操作(如生成/保存 Excel/Word等等)、本地其他应用操作、命令行操作、数据处理、复杂计算、图像处理、各种其他工具调用等

#### 2. 任务改写规则

当用户指令包含非浏览器操作时,**必须剥离非浏览器部分**,只把浏览器操作部分发给 `autoglm run`。

**示例 1**:
```
用户原始指令:"到小红书搜索北京旅游攻略的最多点赞帖子,整理一下他们的标题、点赞数和内容到 Excel 给我"

✅ 分配给本 skill 的任务改写为 (注:用户未指定数量,必须补充默认数字 5):
"到小红书搜索北京旅游攻略的最多点赞帖子,收集前5个帖子的标题、点赞数和内容给我"

❌ 剥离的部分(需使用其他技能):
将收集到的信息保存到 Excel 文件
```

**示例 2**:
```
用户原始指令:"到小红书搜索关于 GLM-5 的最新帖子,然后整理前6个帖子的内容到 Excel 给我"

✅ 分配给本 skill 的任务改写为:
"到小红书搜索关于 GLM-5 的最新帖子,然后整理前6个帖子的内容"

❌ 剥离的部分(需使用其他技能):
将整理得到的信息保存到 Excel 文件
```

#### 3. 执行流程

1. **解析用户指令** → 识别浏览器操作 vs 非浏览器操作
2. **改写任务** → 只保留浏览器操作部分
3. **调用 `autoglm run`** → 执行浏览器任务
4. **获取结果** → 将浏览器任务的输出传递给其他技能(如需要)
5. **完成整体任务** → 协调多个技能完成用户的完整需求

> **关键**:不要试图让 `autoglm run` 做它能力范围外的事情,否则任务会失败。始终遵循"**只分配浏览器操作**"的原则。

#### 4. 本地文件自动处理

当用户的浏览器任务涉及**本地文件**（如上传图片、发送本地文档、发布包含本地图片的帖子等）时,**无需手动上传**。

`autoglm run` 会**自动检测 task 文本中的本地文件路径,上传到 OSS 并替换为在线 URL**,然后再执行浏览器任务。

**示例**:
```
用户原始指令:"帮我在小红书发一个帖子,配图用 C:\Users\me\photo.jpg"

直接调用:
autoglm run --task "帮我在小红书发一个帖子,配图用 C:\Users\me\photo.jpg" --start-url "https://www.xiaohongshu.com"

服务内部自动:检测到 C:\Users\me\photo.jpg → 上传到 OSS → 替换为 oss_url → 执行任务
```

> **直接把包含本地文件路径的 task 原样传给 `autoglm run` 即可**,服务会自动处理文件上传。

#### 5. 明确不可执行的任务类型（必须预处理或转交其他技能）

以下类型的任务**发给 agent 一定会失败**。你**必须**在派发前识别它们,进行拆解、预处理或转交其他技能。

| 类别 | 示例 | 正确做法 |
|---|---|---|
| **截图/页面捕获** | "截取搜索结果"、"保存这个页面的截图" | 不要让 agent 截图。任务完成后,从 agent 返回的步骤历史中获取截图（每步可能包含 `screenshot_url`）,直接展示给用户。 |
| **下载文件到本地** | "下载这张图片保存到 C:\Pictures"、"把视频保存到桌面" | agent 无法下载文件到用户本地文件系统。如果用户需要保存内容,从 agent 结果中提取 URL,自行处理下载（如通过 `curl` 等工具）。 |
| **本地文件操作** | "把这些照片移到文档文件夹"、"新建一个目录然后把文件转移过去" | 本地文件系统操作完全不在浏览器范围内。使用 shell 命令或文件管理工具处理,绝不发给 agent。 |
| **浏览器开发者工具/控制台** | "打开 F12 执行 JavaScript"、"查看 Network 标签页" | agent 通过预定义动作（点击、输入、滚动等）控制网页。它无法打开 DevTools、在控制台执行 JS、或操作浏览器 chrome UI。 |
| **浏览器设置/浏览器自身操作** | "关闭浏览器"、"修改浏览器设置"、"清除 cookies" | agent 只能与网页内容交互,无法控制浏览器级别的 UI（设置、地址栏、下载管理器等）。例外：可以新建标签页和切换标签页。 |
| **需要先生成的媒体内容** | "在抖音发个视频"（但没提供视频）、"上传一张图片到小红书"（没有图片链接） | 如果所需的媒体素材不存在,必须先用对应工具生成（如图片生成、视频生成工具）,上传到 OSS 获得 URL,然后改写任务带上 URL 再发给 agent。 |
| **在网页编辑器中直接写长文** | "在这个网页编辑器里编写详细操作手册"、"直接在页面上写一篇2000字文章" | 通过 agent 大量输入文本不可靠（格式错乱、换行 bug）。应该：(1) 先自行生成内容或用内容生成工具；(2) 需要在网页编辑器中写入时,拆分成小段或使用其他方式（如先本地创建文档再上传）。 |

**预处理决策流程**:
```
用户任务提到截图/截取?
  → 剥离截图需求。agent 完成后,从步骤历史提取截图展示给用户。

用户任务涉及下载/保存文件到本地路径?
  → 剥离本地保存部分。让 agent 查找/导航,然后自行处理下载。

用户任务需要尚不存在的媒体素材（视频/图片）?
  → 先生成素材 → 上传 OSS → 改写任务带上 OSS URL → 再发给 agent。

用户任务涉及浏览器内部操作（F12、控制台、设置）?
  → 拒绝该部分。向用户说明此类操作无法通过浏览器 agent 自动化。

用户任务涉及本地文件管理（移动/复制/创建文件夹）?
  → 用 shell 命令或文件工具处理,不发给浏览器 agent。
```

---

## Complex Task Decomposition(复杂任务拆解)

### 判断流程

```
用户任务
    ↓
涉及多个不同网站?
    ├─ 是 → 按站点拆分,每个站点一次 autoglm run（串行执行）
    └─ 否 ↓
       任务过于复杂且多次执行失败?
           ├─ 否 → 不拆解,一次性发给 autoglm run --task
           └─ 是 ↓
              需要延续页面状态/同站批量/连续流程?
                  ├─ 是 → 不拆解,优化任务描述重试
                  └─ 否 → 串行拆解,多次调用 --task
```

### 跨站点拆分（不同网站做同一件事）

当用户要求在多个不同网站上执行相同或类似的操作时,**必须按站点拆分**,每个站点单独调用一次 `autoglm run`:

```
用户: "去小红书和微博和百度上搜索最近的3篇关于杨幂的最热门的新闻"

执行（串行,每个站点一次）:
  1. type %USERPROFILE%\.openclaw-autoclaw\session_pool.json  ← 每次调用前必检！
  2. autoglm run --task "去百度上搜索最近的3篇关于杨幂的最热门的新闻" --start-url "https://www.baidu.com"
     → 等待完成,cat 结果
  3. type %USERPROFILE%\.openclaw-autoclaw\session_pool.json  ← 再次检查
  4. autoglm run --task "去微博上搜索最近的3篇关于杨幂的最热门的新闻" --start-url "https://weibo.com"
     → 等待完成,cat 结果
  5. type %USERPROFILE%\.openclaw-autoclaw\session_pool.json  ← 再次检查
  6. autoglm run --task "去小红书上搜索最近的3篇关于杨幂的最热门的新闻" --start-url "https://www.xiaohongshu.com"
     → 等待完成,cat 结果
  7. 汇总三个站点的结果 + 截图返回给用户
```

> **拆分时 task 写法**: 只替换站点名称,其余保持用户原话。绝对不要添加用户没说的内容（如"记录标题和摘要"）。

### 不拆解的情况

1. **需要从前一个操作结束页面继续的操作**
   ```
   示例:"在知乎搜索 Python,然后点击第一篇文章,再收藏这篇文章"
   → 不要拆解,后续操作依赖前一步的页面状态
   ```

2. **在同一网站上的批量操作**
   ```
   示例:"收藏知乎上和 GPT 相关的最新4篇文章"
   → 不要拆解,让 autoglm run 在一个会话中完成
   ```

3. **单个连续流程的多步骤操作**
   ```
   示例:"打开微博,搜索杨幂,给最新3条微博点赞"
   → 不要拆解,这是一个连续的操作流程
   ```

### 串行拆解（依赖链任务）

当子任务之间有依赖关系（后一个需要前一个的结果）时,串行执行多次 `--task`:

```
用户: "先去百度搜索今天的热搜第一名是什么,然后去微博搜索这个热搜话题的讨论"

执行:
  1. autoglm run --task "去百度搜索今天的热搜第一名" --start-url "https://www.baidu.com"
  2. 读结果,提取热搜关键词
  3. autoglm run --task "去微博搜索'{热搜关键词}'的讨论" --start-url "https://weibo.com"
```

> **核心原则**:
> - **同站点操作 → 不拆解**,一次性 `--task` 完成
> - **有依赖的子任务 → 串行执行**,前一个结果传给后一个
> - **汇总时必须包含所有子任务的截图**
