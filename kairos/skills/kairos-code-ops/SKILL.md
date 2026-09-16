---
name: "kairos-code-ops"
description: "Troubleshoot Kairos_code: chat silent, 500s, restart"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/software-development/kairos-code-ops/SKILL.md"
---
# Kairos_code 运维 / 排障

Repo: `<repo>` (was on D:, moved to E:). FastAPI backend on **9527**, vite frontend on **3000**.

## 运行结构（先记住这几点）

- 启动入口 `start.vbs` → `start_backend.bat`（**绝对路径** `.venv\Scripts\python.exe -m kairos.main`；不要用 `activate.bat`，它硬编码旧的 D: 路径）→ 日志追加到 `logs/backend_out.log`（stderr 也进同一文件）。
- `watchdog.bat` 轮询端口：**后端挂了会自动拉起（实测 6 秒）**，所以直接 kill 后端进程就等于热重启；前端 3000 一起挂掉时 watchdog 会退出并杀掉后端。
- 改代码/改 settings 后必须重启后端才生效（settings 有 5s TTL 但 agent 的 provider 是 attach 时建的，不会自动换）。

```bash
# 热重启（watchdog 会自动拉起）
powershell -NoProfile -Command "Stop-Process -Id <backend_pid> -Force"
netstat -ano | grep "127.0.0.1:9527 .*LISTENING"   # 等它回来
curl -s http://127.0.0.1:9527/api/health
```

注意：git-bash 里 `taskkill //F //PID` 会被参数转换搞坏，用 powershell 的 `Stop-Process`。

## 桌面版（下载的 exe）运行时事实（2026-09-13 实测，别再猜）

- **数据目录不是 `<exe>/data`**（`settings.py` 的注释还那么写，已过期）：`kairos_code_launcher.py` 把冻结版钉在 **`%LOCALAPPDATA%\kairos-code`**（非 Windows 是 `~/.kairos-code`），而且**忽略 `KAIROS_DATA_DIR`**。要隔离测试就覆盖 **`LOCALAPPDATA`** 指向临时目录 —— 绝不要为了测试去改用户真实配置。
- 冻结版**自己挑端口**（忽略 `KAIROS_PORT`），URL 打在自己的 stdout：`Uvicorn running on http://127.0.0.1:<port>` → 验证脚本从日志里解析端口，别假设端口。
- 关掉它必须 **`taskkill /F /T /PID`**：PyInstaller 引导器会 fork 出真正跑 server 的子进程，只 terminate 父进程会留下**孤儿**，继续占着 exe 文件锁 → 下一次构建报 `PermissionError: [WinError 5]`（我踩过：留下两个，还得换目录构建）。
- **首次运行必踩的 401**：没有真 key 时，出厂 settings 里的**占位密钥** `…placeholder` 会被当真的发出去，DeepSeek 回 `Authentication Fails (governor)` + HTTP 401（报文里能读到 `api key: ****lder is invalid`）。报这个错 = 配置里没有可用 key，不是网络问题。

## 端点两个字段的分裂（2026-09-13 修复，配置类问题先查这里）

`provider.openai` 同时有 `endpointUrl` 和 `baseUrl`。老实现里 `endpointUrl` **只给「测试连接」探针用**（见 `settings_store.py::OpenAIProviderConfig` 的 docstring），真正聊天读的是 `model_router` 的 `baseUrl` → **用户在界面改了端点，聊天照旧打旧主机**（实测：界面显示 DeepSeek，请求却发到 agnes-ai.com，被 Cloudflare 拦，用户完全看不出原因）。现在 `kairos/llm/endpoints.py::resolve_base_url()` 以 `endpointUrl` 为准（`baseUrl` 仅兜底），并剥离 SDK 不该带的后缀（`/chat/completions`、`/messages`，含尾随斜杠）。改 provider 配置前先确认这两个字段的优先级。

**报错可读性**：`kairos/llm/errors.py::describe_provider_error()` 把 provider 原始响应压成一行（HTML 拦页保留主机名 + Cloudflare Ray ID；401/403 附可操作提示）。新增 provider 调用点必须走它 —— 以前把 `exc` 直接插进 detail，用户界面里会出现几 KB 的 HTML。

## 前端：预设是「用户的选择」，不能从字段反推（2026-09-13 修复）

用户报：“选自定义 URL + 下面选 deepseek 模型，测试连接正常，但软件把服务商预设自动跳回 DeepSeek，并且 chat 不正常。”

三个缺陷叠加（都在前端，后端无辜）：

1. `SettingsDrawer.tsx` 里有一个 `useEffect`，**每次** `endpointUrl`/`model` 变化就 `setPresetId(matchPreset(...))` → 用户的显式选择永远赢不了。**已删除**：现在只在加载时推导一次（`useState` 初始化），之后忠实反映用户点选。
2. `presets.ts::matchPreset` 有“模型名启发”：`if (model.startsWith('deepseek')) return 'deepseek'` → **只看模型名就推翻 URL**（正是本例的触发条件）。**已删除**：模型名不是服务商身份。
3. `matchPreset` 的 URL 匹配用 `lower.includes(<主机名>)` → `https://api.deepseek.com.mirror.example/v1` 也会被判成 deepseek。**已改为** `new URL(...).host === new URL(preset.endpointUrl).host` 精确比较。

**症状的另一半**：用户填的自定义 URL **根本没落盘**（`settings.json` 的 mtime 停在旧时刻）→ 界面显示的和后端实际发出的是两回事。诊断此类问题时，**先看对应数据目录 settings.json 的 mtime + 内容**，比问用户快得多。

回归测试：`web/src/test/llmPresets.test.ts`（4 条，修复前 2 条必失败）。验收门禁：`npx vitest run`（全量）+ `npx tsc --noEmit` + **`npm run build`**（tsc 过 ≠ 构建过，本仓库栽过）。

**审计同类问题的办法**：grep 出组件里所有 `onChange({...})` / `useEffect` 回写点，逐个确认“是否由用户动作触发”。本例审计后确认无第二处。

## 冒烟脚本必须杀进程树（2026-09-13 修复）

`scripts/smoke_binary.py` 原来只 `proc.terminate()` ✓ —— 但冻结版是 PyInstaller 引导器 ✓，**它会 fork 出真正跑 server 的子进程** ✗ → 父进程死了、子进程活着 ✓（无窗口、占着端口、**在 Windows 上还锁住 exe** → 下一次构建 `[WinError 5]`）✓。CI 永远发现不了（runner 用完就扔 ✓）。

现在 `terminate_tree()`：Windows `taskkill /F /T`；POSIX 用独立会话 + 进程组信号（避免信号回头打死 pytest 自己 ✓ —— 就是当初搞掉整个 CI shard 那个坑 ✓）。回归测试 `tests/test_smoke_tree_cleanup.py`（构造“父进程立刻 fork 长命子进程”的形状 ✓ 断言两者都没了 ✓）。

**判据**：跑完冒烟后立刻列 `kairos-code.exe` 进程，按 **CreationDate** 看有没有“刚刚创建”的条目 ✓ —— 有就是又漏了。

## CI shard 超时必须能点名（2026-09-13 改进）

`scripts/ci_shard.sh` 里 `--timeout=150` **没指定方法** ✗ → 跑的是默认 `thread` ✓ → 只 dump 旁支线程 ✗，日志里是一页 `asyncio-waitpid` 栈 ✓（那只是“有子进程活着”的正常现象 ✓），**卡住的主线程从未被打印** ✗ —— 这就是 shard 5 在 Linux 上反复挂却查不出原因的真正障碍。

现在：`--timeout-method=signal`（仅 Linux ✓，Windows 无 SIGALRM 自动跳过 ✓）→ 超时在**测试自己的线程**里抛异常 ✓ → 日志直接给出**测试名 + 行号** ✓。

**已定位并修复（commit 7fb46d5，CI 首次全绿：26m19s → 2m46s，shard5 1336s → 161s，内存 14.55 GB → 正常）**：

1. **递归**（真正原因）✗：`loop_runner` 每轮调 `pre_check_workspace`，它**自动探测**到工作区有 `pyproject.toml` 就跑 `pytest -q --tb=short -x` —— 而工作区就是本项目，于是**从 `tests/unit/test_loop_run.py` 里又跑了一整套测试**，那套里又有 `test_loop_run.py` → 又驱动循环 → 又跑 pytest……逐层翻倍 ✓（1.09 GB → 14.55 GB ✓）。修法：会话级 `KAIROS_INSIDE_TESTS`（conftest 设 ✓）→ `_auto_detect_test_command` 直接返回 None ✓；断言“探测行为”的用例用 `monkeypatch.delenv` 局部豁免 ✓（与 `KAIROS_NO_CHECKPOINTS` 同一模式 ✓）。
2. **`asyncio.wait_for(proc.communicate(), timeout=…)` 超时不杀进程** ✗：它只取消“读” ✓，子进程（及其子进程）继续活着、管道还开着 ✓ → 每轮漏一个 ✓。`_run` 现在走 `_terminate_tree()` ✓（Windows `taskkill /F /T` ✓；POSIX 独立会话 + `killpg` ✓）并 `await proc.wait()` 回收 ✓。

**注意本地为何一直绿** ✗：Windows 下 `changed` 为空时 precheck **直接跳过** ✓，嵌套循环根本不发生 ✓ —— 所以这个缺陷 **只在 CI 上显形** ✓（且它在**生产里也成立**：真实循环同样会漏进程 ✓）。

**可迁移的教训**：① `wait_for(...)` 包 `communicate()` **不等于**“超时会善后” ✓ —— 必须显式杀进程树并回收 ✓（本项目已第三次踩：hooks ✓、smoke ✓、precheck ✓）；② 任何“在项目里自动跑测试/lint”的功能都要**防递归** ✓（设一个“已在测试内”的标记 ✓）；③ `ci_shard.sh` 的 `--timeout-method=signal` 是这类问题**唯一可靠的入口** ✓ —— 换成它之前几轮都只能看到无关线程栈 ✓。

## 反馈链路（0.1.4 起）：预填 GitHub Issue + 不依赖 watch 的通知（2026-09-14 实测）

入口在设置抽屉底部 ✓，实现是**打开一条预填的 GitHub Issue 链接**（`web/src/utils/feedback.ts`）✓ —— 无服务端、无凭据、提交前用户在 GitHub 上还能再看一遍 ✓。四个实测结论：

1. **模板头部引用的 label 必须先在仓库里存在** ✗ —— `feedback.md` 写了 `labels: feedback` ✓ 但仓库没这个标签 ✓ → GitHub **不认整个模板** ✗（副标题一直 "Blank issue" ✓、Labels 永远 no labels ✓）。`gh label create feedback` 一创建，立即全对 ✓。**动模板头部前先 `gh api repos/X/Y/labels` 对一遍** ✓。
2. **`?template=` 与 `title=&body=` 同用时，预填才是可靠的那部分** ✓ —— 模板没生效时（blank 编辑器 ✓）title/body 依然完整带上 ✓（用户截图双重证实 ✓）。所以设计上**优先保证预填** ✓，模板/标签只当外观 ✓。
3. 仓库 `config.yml` 里 `blank_issues_enabled: false` ✗ —— 但**带参数的 URL 仍会打开空白编辑器** ✓（实测 ✓）→ App 的链接不受影响 ✓。
4. **通知不能靠“我是 owner 所以我在 watch”** ✗ —— 那取决于用户账号的通知设置，且 token 缺 `notifications` scope 根本查不了 ✗。改用 `.github/workflows/notify-feedback.yml` ✓：新 issue 一开就**指派 maintainer + 评论里 @他** ✓（mention 是 GitHub 最强通知 ✓）。`gh api repos/X/Y/actions/workflows` 确认它 `active` ✓ 即可证 YAML 有效 ✓。

## 杀进程必须重新核对 PID（真实事故）

PID 会被 Windows **复用** ✓ —— 用几小时前抓的 PID 列表 `taskkill /F /T` ✗ 可能杀掉**现在**持有该 PID 的别的进程 ✗（本项目真实发生：清理自己的 9 个残留后，用户自己那 7 个实例也不见了 ✓、无法确定是不是误杀 ✓）。

**规则**：杀之前用 `Get-CimInstance Win32_Process -Filter "Name='x.exe'"` **重新列出 PID + ExecutablePath** ✓，按**路径**匹配出目标 ✓，再逐条 `taskkill /F /T /PID` ✓；绝不复用陈旧 PID 列表 ✓。（`taskkill /F` 会触发审批弹窗 ✓ —— 等用户点 ✓，别反复重试被拦的命令 ✓。）

## 关键路径 / 接口

- LLM 配置：`data/settings.json` → `provider.openai.{model,endpointUrl,baseUrl,apiKey}`（还有一份 legacy `provider_openai`，改模型要**两处一起改**）。备份写 `data/settings.json.bak-<ts>`。
- 聊天：`POST /api/projects/{id}/chat` body `{message, agent_role}`（`agent_role` 填 `coder`）→ 内部走 `project.coder.chat()` → `KairosAgent._chat_impl`（`kairos/agents/base.py`）。回复经 message bus 的 `agent.chat` 落库，聊天页从 `GET /api/projects/{id}/chat-messages?chat_only=true&limit=N` 重新水合（用户消息 topic 是 `user.chat`）。
- 排查用的只读 DB 查询：`data/kairos.db`，表 `messages(project_id, sender, topic, content, timestamp)`；curl 请求体里的中文用 `--data-binary @file.json`，避免 MSYS 引号/编码坑。
- 临时脚本放 `.agn_tmp/`（已 gitignore），跑仓库内脚本要 `PYTHONPATH=<repo>`。

## Playbook: “普通 chat 没有回复任何消息”

界面什么都不显示 = 后端 200 但 `reply` 为空。按顺序查：

1. `grep -a "EMPTY reply" logs/backend_out.log | tail -1`
   → 有这行就直接读出来：`model=`、`finish_reason=`、`usage=`。
2. `finish_reason=length` 且 `reasoning_tokens` == `completion_tokens` == max_tokens(8192)
   → **当前配置的模型是思考型模型，把整个输出预算烧在隐藏推理上，正文为空**。
3. `grep model data/settings.json`；DB 里 `topic='agent.chat'` 为 0 条（全是 `user.chat`）可佐证“从来没成功回过”。

已实测的模型行为（DeepSeek 网关，2026-09）：

| model 配置 | 结果 |
|---|---|
| `deepseek-chat` | ✅ 正常出正文（短问 0.7s，长代码 18s） |
| `deepseek-flash` / `deepseek-v4-pro` / `*-expires-on-*` 别名 | ❌ 只出 reasoning，正文空；**调大 max_tokens 无效**（8192→32768 也一样烧光，还白等 139s） |
| `deepseek-v4.1-flash` | ❌ API 400：只支持 `deepseek-flash` / `deepseek-v4-pro` → 路由包装成 HTTP 500（聊天页只看到 500） |

修法：把 `settings.json` 里两处 `model` 改成 `deepseek-chat` → 重启后端 → 复测。不要靠加 token 预算救思考型模型。

## Reviewer 已简化为“只找 bug”（R38.7）

用户原话：「reviewer agent 太复杂，改成简单的只检查代码是否存在bug即可」。改动位置：

- `kairos/agents/roles/reviewer.py`：`SYSTEM_PROMPT` 重写。旧的是四维加权评分（correctness 40/design 25/quality 20/security 15）+ 必跑测试证据 + ask_human + 置信度 + 四级严重度。新的只问一件事：有没有 bug；输出 `{"has_bugs": bool, "bugs": [{file,line,description,fix}], "summary": str}`；明确列出“不算 bug”的清单（风格/命名/架构/性能/安全加固/测试覆盖），并强调“没问题就直接说没 bug，不要为了显得有用而编一个”。
- `kairos/loop/reviewers.py`：新增 `_normalize_bug_verdict()`，把上面这个简单形状映射到内部旧的 `{approve, score, issues, summary}`（无 bug → approve=True, score=100；N 个 bug → approve=False, score=100-20N），并打上 `_simple_bug_review=True`。**保留旧 rubric JSON 的解析**（老 session/回放/测试不能碎）。
- `kairos/loop/loop_runner.py::_calibrate_review`：当 verdict 是简单形状时**跳过**“无测试证据不给过”的校准——否则 require_test_evidence=true 时每次“没 bug”都会被压到 84 分以下，**loop 永远不会结束**。这是最隐蔽的坑。
- `kairos/loop/prompts.py`：reviewer 任务描述只要求找 bug；coder 下一轮 prompt 改成“The Reviewer found N bug(s)” + “Bugs to fix this round”，并明说不要 refactor/rename/restyle。
- `web/src/pages/Loop.tsx`：顶部 tag 从 `score N` 改成 `no bugs` / `N bug(s)`，柱状图 tooltip 同步；内部 score 仍是 100/80/60…（门禁还用）。

**不变量**：approve/score 这套门禁管道别抽掉（UI、DB、记忆、测试都依赖它）——只改 reviewer 的职责和输出形状，用适配层转换。

回归测试：`tests/test_r38_7_simple_reviewer.py`（19 个：prompt 去掉评分机器、无 bug 通过、有 bug 拒绝、fenced JSON、旧格式兼容、校准不再卡、下一轮 prompt 措辞）。真实模型验证：故意写一个含 2 个 bug（空列表除零、sort 方向错+改入参）的 `calc.py`，reviewer 精准报出这 2 个、不再提风格/测试覆盖，raw 输出无 rubric 残留。

## Chat 附件（R38.7，已实现）

📎 回形针以前是硬编码 `disabled` + “Attach file (coming soon)”。现在：

- `POST /api/projects/{id}/attachments`（multipart 重复 `files` 部分，**任意格式**，单文件 50MB 上限）→ 文件落盘到 `<project root>/attachments/<name>`，返回 `{name,size,mime,rel_path}`；`GET` 列、`DELETE /{rel_path}` 删。
- `rel_path` 随消息回传给 `POST /chat`（或 `/start`）的 `attachments: []`，后端把它折成 `[附件 / attachments]` 块**拼进 user 消息正文**（同时落库，刷新后仍在），并将拼好的正文回显在响应的 `message` 字段（前端用它替换乐观气泡）。
- 为何落盘而不入库：`project_files`（参考资料）是 SQLite TEXT 内联 + 5MB 上限 + 会被 digest 注入 loop 提示词；二进制进去会变 base64 垃圾。附件走磁盘，正好落在 Coder 工具沙箱（`project.work_dir or workspace`）里，`file_read attachments/x.csv` 直接可读。
- 前端：回形针 → 隐藏 `<input type=file multiple>`（不过滤格式）+ 拖拽 + 粘贴（截图）+ 芯片列表（名称/大小/× 删除，删除会调 DELETE）。上传选完即传（有“上传中”状态），发送时把 `rel_path` 列表带过去。

坑（都已在代码里处理）：

1. 客户端常不带 part 的 content-type（或给 `application/octet-stream`）→ mime 要用文件名兜底，且用自建扩展名表覆盖 `mimetypes`（Windows 注册表把 `.csv` 映射成 `application/vnd.ms-excel`）。
2. 有些 curl/代理栈会把 filename 按 latin-1 解码成乱码（`½çÃæ...`）→ `_repair_mojibake()` 用 `encode('latin-1').decode('utf-8')` 还原；真实浏览器（UTF-8 multipart）本来就正常。
3. 附件路径是客户端可控字符串 → 必须 `resolve()` 后校验 `relative_to(root)`，否则是目录穿越。
4. 纯文本模型看不到图片内容（会诚实说看不到）——图片理解是另一个功能，别承诺。

回归测试：`tests/test_chat_attachments.py`（14 个，含穿越/超大/乱码名/413/mime）+ `web/src/test/composerAutoMode.test.tsx`（11 个，含选文件→芯片→提交带附件、删除、上传失败提示）。

## 多国语言（i18n：zh/en 全量 + 70 语言架构，2026-09）

用户要求「完全切换，不是部分」，后续要求「能支持的语种都加上」。

**63 种语言**已可选（antd 的 70 个 locale 去掉 7 个同语言变体：en-GB / fr-BE / fr-CA / nl-BE / pt-PT / zh-TW / zh-HK —— 用户明确要求「同语言变体不保留」；每个语言只保留主 locale，这条不变量有 vitest 守卫。要恢复繁体中文等变体，把代码加进 `scripts/gen_languages.mjs` 的 `KEEP_VARIANTS`）：

- 源文案 `web/src/i18n/parts/*.json`（`{key:{zh,en}}`）+ 译文 `web/src/i18n/catalog/<lang>.json`（`{key:"文本"}`）→ `python scripts/merge_i18n.py` 生成 `locales/<lang>.ts`（**别手改生成文件**）。
- `web/src/i18n/languages.json`/`languages.ts` 由 `.agn_tmp/gen_languages.mjs` 生成（原生语言名用 `Intl.DisplayNames` 取，antd/dayjs 映射自动校验；排除 antd 的 `context.js`/`useLocale.js` 内部文件）。
- **按需加载**：每语言的 antd locale + dayjs locale + 词典都是动态 `import()`（各自一个 chunk），首屏只加载当前语言 + 常驻的 en/zh 兜底列——千万不要把 70 份词典静态 import，会撑爆 bundle。
- 回退链 `catalog[lang] → zh-CN(中文变体)/en-US → humanizeKey()`，所以半翻的词典也不会露出原始键。
- 翻译：`python scripts/translate_i18n.py --all --workers 5`（读 `data/settings.json` 的 key；**强制 `deepseek-chat`**，因为配置里那个思考型模型会返回空正文）。每 batch 落盘，可断点续跑；校验键集/空值/`{placeholder}` 一致性。
- 切语言：设置抽屉顶部 `[data-testid="settings-language-select"]`（带 showSearch，70 项必需），写 `localStorage['kairos-lang']`，同步 `<html lang>` 与 `<html dir>`。
- **RTL**（ar/fa/he/ku/ur）：AntD 读的是 `<ConfigProvider direction>` 而非 `<html dir>`；间距必须用逻辑属性（`marginInlineStart` 等），已把 15 个文件 41 处硬编码 `marginLeft/Right` 等换掉。
- 门禁：`scripts/check_i18n.mjs`（**在仓库根** ✓ 不是 `web/scripts/` ✗ —— 这一条我记错过一次 ✓；且它**没有**接进 `web/package.json` ✓，必须手动从仓库根跑 ✓）（43 文件，0 硬编码/0 缺键，含 `call-arg` 规则：`msgApi.error('…')`/`setXxxError('…')` 这类不经 JSX 的界面文案）+ `i18nKeys.test.ts`（vitest 守卫：全语言完整键集 + 覆盖率，`I18N_STRICT=1` 时要求 100%）+ `merge_i18n.py --coverage/--strict`。

迁移期的坑（都踩过，改文案/加文案前先看）：

1. **动态键两个校验器都看不见**。`src/llm/presets.ts` 的 `label/hint` 存的是**键字符串**，渲染处必须 `t(p.label)`；漏了就会在界面上看到 `preset.deepseek.label`。同理 `KIND_KEYS`/`SEV_KEYS` 之类的映射表要用字面量键。
2. **单小写词是文案**（`crit`/`muted`/`flaky`/`active`），驼峰/全大写标识符不是。早期 isProperNoun 启发式放过所有小写单词 → 漏检 15 处，现已收紧。
3. **替换裸词会误伤代码**：`turn`/`score`/`add` 这类词同时出现在 JSX 文本和 `event.turn`/`r.score` 里；行内替换后必须立刻 `tsc` 并 grep `\.\{t\(` 扫残留。
4. **JSX 内嵌片段**（`{n} chars · {m} bytes`、`Showing {n} case(s)`）要抽成带参数的键，别只替换半边。
5. **函数实参也是界面文案**：`msgApi.error('…')` / `setModelFetchError('…')` 不经过 JSX，check_i18n 的 `call-arg` 规则专治这个。
6. 后端生成的句子（`kairos/alerts.py` 的 `Total cost up 12%`）**不要翻译存储值**，用 alert 自带的结构化字段在 `AlertPanel.alertText()` 里重建句子；数据表里的旧英文行属于数据。
7. 模块级函数必须用 `tGlobal()`，用 `t()` 会直接 tsc 报 “Cannot find name”。
8. **作者列不容 catalog 覆盖**：翻译脚本曾把 `en-US` 也翻了，产出的 `catalog/en-US.json` 中一条被模型回吐成嵌套对象，覆盖作者英文列 → 界面渲染 `{'en': '0% done'}`。merge 现在对 en-US/zh-CN **忽略 catalog**，并拒绝序列化对象值（`{n} Dateien` 这类以占位符开头的正常文案不能误杀）。
9. **翻译 payload 必须单层扁平**：发 `{key:{en,zh_hint}}` 会让模型照抄嵌套结构返回 `{"strings":{...}}`，导致带占位符的键永远被拒、覆盖率卡死；现在只发 `{key:english}` + 响应侧 `unwrap()` 解包 `strings/translations/items`。
10. 译文占位符集合必须与源文案一致（脚本拒绝 + vitest 跨 63 语言守卫），否则会渲染成 “1 score 1”。

验收（“完全切换”的实证）：逐页（对话/今天/工具/循环/项目 + 设置抽屉）扫英文残留；切到 English 后同样页面 **CJK 必须为 0**（聊天正文除外）。RTL 语言确认 `<html dir>=rtl` 与布局镜像。

操作教训：这个仓库上跑子代理做机械迁移会**集体撞 600s 墙**——把范围切到「一个文件一个任务」，并要求它每完成一个文件就落盘片段；更可靠的做法是自己写行锚定的 python 替换脚本（`.agn_tmp/migrate_generic.py`）。

## 别让空回复静默（代码侧，已修）

`KairosAgent._chat_impl` 以前对空 content 只写日志、返回 `""`，UI 就一片空白、无从判断。现在：空回复会**发布并返回一条中文提示气泡**（`agent.chat`），内容是模型名 + finish_reason + reasoning_tokens + 建议换非思考模型；回归测试在 `tests/test_chat_empty_reply.py`（stub LLM 返回空 response，断言返回值非空且已上 bus）。

改这里时保持不变量：**chat 永远不要返回空字符串**，且必须 publish 到 `agent.chat`（前端气泡只认 bus 事件）。

## 门禁报告 / 演示 / 三视图（R38.8，已实现）

**Gate Report** —— 一轮循环的「凭证」（谁审的、评分曲线、打回几次、花多少钱）：

- CLI：`python -m kairos gate report --project <id|名称前缀> --out gate.html [--format html|md|json] [--lang en|zh] [--session X] [--db 路径]`
- API：`GET /api/projects/{id}/gate-report?format=html|md|json&lang=&session=&download=1`（404=项目不存在，400=format 非法；`lang` 非 en/zh 静默回退 en）
- HTML 是**单文件自包含**（无 CDN/无图片，内嵌 EN↔中文原地切换），可直接贴 PR / 发 Slack；Markdown 贴 PR 描述；JSON 给 CI 断言。
- **成本口径**：账本（`data/cost.jsonl` 的 `CostEntry`）**没有 project 列**，是按进程/全局的 —— 所以报告按「会话时间窗」汇总本次花费，窗口为空时退回全局总额并把 `cost_windowed=False` 标出来（`to_markdown` 会写明是哪种）。别把它当成精确到项目的账。
- tokens 同理：`review_json` 里常常没有 usage → 从账本汇总兜底。
- 排障：报告空 → 先看 `loop_rounds` 有没有行（见下），再看 `parse_issues()` 是否兼容 verdict 形状（简化版 `bugs` / 旧 rubric `issues` 都支持）。

**`kairos demo`** —— 不需要 API Key、不联网，约 6 秒看完整个门禁：

- `.venv/Scripts/python.exe -m kairos demo [--out DIR] [--keep] [--open] [--lang zh] [--json]`
- 实现：`kairos/demo.py`（迷你仓库 + 三轮剧本） + `kairos/llm/scripted.py`（`ScriptedProvider`，实现 `complete()`+`stream()`，含 OpenAI 式 `{"type":"tool_calls"}` 哨兵行）。
- **循环是真的**（`run_loop` + 真实沙箱工具），只有模型是脚本；Reviewer 的结论靠**读 relay.py 内容**推导，所以三轮 3 bug→1 bug→0 bug 是真实推导；结尾用仓库自带测试验证（`3 passed`）。
- 脚本 provider 会往账本记 **$0 条目**（`cost.record_entry`，非 litellm 路径的公开入口），所以「花了多少」卡片会显示调用数与 token 而不是空表。
- 踩过的坑（改 demo/写 scripted provider 时必看）：
  1. **`KairosAgent._run_impl` 对空 api_key 直接早退**（"No API key for X"）→ 脚本 provider 的 `LLMConfig` 必须给个占位 key，否则 `coder_calls=1` 且没有异常。
  2. **`terminal` 工具有命令白名单**（head 必须是 `pytest`/`ls`/`git` 这类）；传绝对解释器路径会被 "Blocked by safety" 拒。
  3. demo 必须 `KAIROS_SKIP_WORKTREES=1`：角色级 worktree 会让 Coder 写在自己的树里、Reviewer 读自己的树（永远看到旧代码）。生产 `start_backend.bat` 也是这么设的。
  4. 想「有数据的 UI」：把 demo 的 `kairos.db` 行 + `cost.jsonl` 条目移植进 `data/`，项目名标成示例，用完可删。

**前端三视图**（R38.8）：`/` → `/run`（业务输出：过关了吗/花了多少/比上轮好还是差），`/history`（每次运行的结论+轮次+花费+对比上轮），设置仍是抽屉。其余（对话/今天/工具/循环/追踪/全部项目/看板）收进侧边栏 **高级**（默认折叠，`data-testid=footer-advanced[-group]`）。
- `?project=<id>` 深链：Run/History 都支持，便于分享/排查。
- Run/History 在 `/sessions` 不可用（后端重启后项目未载入内存）时**回退到门禁报告**（DB 直读）——只读凭证不该依赖 orchestrator 内存。

## checkpoint 提交会扫掉父仓库（已修，务必先读）

**症状**：工作区突然多出 `kairos: round N approved/rejected (score X)` 提交，里面是**整个仓库**的改动；跑测试套件就会复现（`tests/unit/test_loop_run.py` 跑真实 `run_loop`，workspace 默认 = 进程 CWD = 仓库根）。历史里那个 640MB rar 也是这么进去的。

**根因**：`kairos/tools/checkpoint.py::checkpoint_round` 用 `git -C <workspace> add -A` + `git commit`。当 workspace 位于某个仓库**内部**（`<repo>/workspace/<id>`、或就是仓库根），`git -C` 作用于**拥有它的那个仓库**，`add -A` 于是把父仓库整个暂存。

**修法（三层，别只做一层）**：

1. `_scope()` 解析 `git rev-parse --show-toplevel`，把 `add`/`commit`/`diff --cached` 都限定到 workspace 相对路径（`-- <pathspec>`）；workspace 就是仓库根时 pathspec 为 `.`。回归测试 `tests/test_checkpoint_scoping.py`（嵌套只提交子树、父仓库已暂存的文件不受影响、根 workspace 仍提交自己的树）。
2. `KAIROS_NO_CHECKPOINTS=1` → `checkpoint_round` 直接 no-op（**测试/CI 的保险丝**）。`tests/conftest.py` 的 `pytest_configure` 全局设上。
3. `tests/unit/test_loop_run.py::_session()` 给 LoopSession 一个临时 workspace，不再继承 CWD。

**注意**：全局保险丝会让**故意测提交路径**的用例变 no-op（`test_checkpoint_scoping.py`、`test_checkpoints_api.py` 都踩过）→ 这些文件各自用 `monkeypatch.delenv("KAIROS_NO_CHECKPOINTS")` 局部豁免（它们只在 `tmp_path` 的临时仓库里操作）。

**验证方式**：`BEFORE=$(git rev-parse HEAD)` → 跑测试 → `AFTER`，两者必须相同且 `git status` 干净。

## 打包/交付相关的既有缺陷（2026-09 开源准备时修）

- **`npm run build` 曾直接失败**（`NewChatButton.tsx` 少类型、`chatStore.ts` merge 返回类型不对）→ 生产 UI 根本构建不出来，Docker 也废。前端交付前**必须真跑一次 `npm run build`**，不能只看 `npx tsc --noEmit`（当时的 tsc 报错被当成“历史遗留”忽略了）。
- **深链在打包 UI 上 404**：`api/app.py` 原来 `StaticFiles(html=True)` 挂在 `/`，没有 SPA fallback → `/run?project=…`、`/history`、刷新全 404（dev server 有 fallback，所以只在浏览器测 dev 时看不出来）。现用 `SPAStaticFiles`：只有非 `/api`、非 `/ws`、无扩展名的 GET/HEAD 才回 `index.html`，缺失静态资源仍 404。
- **`loop.*` WS 事件被主题白名单提前 `return` 吞掉** → `Chat.tsx` 里「切会话 + 刷顶栏」那段是死代码。加 `topic.startsWith('loop.')` 分支落到下游处理块。
- **`/test`、`/lint`、`/format` 是死功能**：它们拼的是 `python -m pytest` / `python -m ruff`，而终端工具**有意**屏蔽解释器 head → 必然被自己的安全策略拒。改成直接用允许的 head（`pytest` / `ruff`），并把 `ruff` 加进 `BUILD_COMMANDS`（默认关闭，`KAIROS_ENABLE_BUILD_COMMANDS=1` 开启）。
- **可选依赖没声明**：`textual`（TUI）、`prometheus-client`（/metrics）、`mcp`（MCP 客户端+文件系统 server）以前是隐式依赖，导致 24 个测试在干净环境必挂 → 现在有 `tui`/`metrics`/`mcp`/`all` extras，`dev` 里也带上。新增这类后端时**顺手加 extra**。
- **测试打桩的层错了**：`api/routes/config.py` 的 `_probe_post_*` 从 `urllib` 换成了 `httpx`，而 `tests/test_r37_backend.py` 的 26 个 fake 还在打 `urllib.request.urlopen` → 断言读到空字典。修法不是重写 26 个 fake，而是加适配层 `tests/_httpx_bridge.py`（httpx → urllib 形状的 Request，并把 `HTTPError`/`URLError` 翻成 `httpx.HTTPStatusError`/`ConnectError`），测试里把 `import urllib.request / urlopen = fake / finally` 三行换成 `_httpx_bridge_install(fake_urlopen, captured)` + `_httpx_bridge_restore()`。**改实现的 HTTP 客户端前先 grep 测试里打桩的层**。
- **vitest 不退出（worker teardown 卡死）**：FolderPicker 的弹窗里挂了重型 `BrowsePanel`（rc-util 滚动锁 + layout effects），断言过了进程却不退（单文件跑会 `Worker exited unexpectedly`）。测试里 `vi.mock('../components/BrowsePanel')` 打桩即可；顺带知道**手动路径输入藏在 antd Collapse 折叠面板里**（browse-first UX，默认折叠、children 懒渲染），测试要先点 `folder-picker-manual-toggle` 再找输入框。
- **测试间状态泄漏**：zustand store 是模块级的，上一个用例创建的项目会留在下一个用例里（FolderPicker 的触发按钮从图标变成下拉框）→ `beforeEach` 里 `useChatStore.getState().reset()`。
- **计时型用例要留余量**：并行加速断言别写 `par < seq`（负载下必挂），流式用例的 timeout 别压到 0.5s（光 Python 启动就可能超）。

## 发布产物（Release 流水线，2026-09 实测）

`release.yml`：打 `v*` tag 或手动 dry-run → wheel+sdist / 三平台独立可执行 / Docker 镜像，全部**先冒烟再上传**，tag 时汇总成 GitHub Release（dry-run 不会发布）。四个坑全是第一次真实运行才暴露的：

1. **`python-multipart` 是隐藏的核心依赖**。FastAPI 只要注册了 `Form(...)`/文件上传路由，就会在 **import 期** `raise RuntimeError: Form data requires "python-multipart"` → **任何干净安装的服务都起不来**（三平台二进制 + `pip install kairos-code` 全灭），而开发机 venv 恰好装过它所以本地永远正常。同类前科：`aiosqlite`。**判据：`pip install .`（不带 extras）后必须能起服务**。
2. **`python -m build` 是从 sdist 构建 wheel** ⇒ wheel 里没有 `web/dist`（gitignore 的东西不进 sdist）。要带界面的 wheel 必须 `python -m build --wheel`（直接从源码树），sdist 单独 `--sdist`。
3. **Dockerfile 漏 `COPY hatch_build.py`** ⇒ `pip install .` 报 `OSError: Build script does not exist: hatch_build.py`（pyproject 里声明了自定义构建钩子，文件必须进镜像）。
4. **冒烟脚本绝不能丢弃子进程输出**（当时用 `stdout=DEVNULL` 兼容 windowed 构建）→ 三平台失败却零诊断。改成重定向到临时文件、失败时打印最后 40 行；windowed（GUI 子系统）应用写重定向句柄也正常。

配套新增的门禁：`ci.yml` 的 **clean-install job**（空 venv 只装核心依赖 → `kairos --version` / `kairos demo` / **真实起服务** health 200 + 内置 UI）；`scripts/smoke_binary.py --installed <console script>` 让装出来的包也能同样冒烟；release 的 wheel job 同样起服务验证（只跑 demo 跑不到路由注册）。

**CI 时长与分片**：本地 3 分钟的套件在 2 核 runner 上要 **40+ 分钟**（大量 spawn 子进程，demo CLI 一套跑十几遍），单 job 必然被 25/45 分钟上限掐死。`scripts/split_tests.py --shard N --shards 3` 做确定性均衡分片（按 `def test_` 计数、贪心、`--verify` 自检覆盖率与均衡度）；**注意按用例数均衡 ≠ 按耗时均衡**，慢文件（demo CLI / bench）容易堆在一个分片里。另外加 `--timeout=300` 让真卡住的用例**报出名字**而不是耗尽 job 预算。

**网络（GFW 实测）**：`git push` 走 SSH-over-443（`ssh://git@ssh.github.com:443/...` + `core.sshCommand` 指定密钥）比 HTTPS 可靠得多；HTTPS 到 github.com:443 会间歇性 `Connection was reset`。`api.github.com` 一直可用 → 建仓/加 topics/看 CI/取日志都走 gh API。gh 的 device-flow 授权需要 `github.com/login/oauth/access_token`，被墙时段会超时。

**拿 CI 日志的正确姿势（省时利器）**：`gh run view --log-failed` 在 run 未结束时会直接拒绝（"run is still in progress"），但 **raw API 对“已完成的 job”是开放的**：

```bash
gh api repos/{owner}/{repo}/actions/jobs/{job_id}/logs --allow-escape-sequences | sed 's/\x1b\[[0-9;]*m//g'
```

无需等整个 run 跑完（否则一个卡住的分片能拖你一小时）。job_id 用 `gh run view <run> --json jobs --jq '.jobs[]|select(.name=="Python tests (2/3)")|.databaseId'` 取。若 log 为空说明该 job 还没完成。另外：**不要用 `gh run cancel` 来“提前结束”以图看日志** —— 取消后的 job 日志会变成 `log not found`。

**平台敏感测试（本项目真实翻车）**：CI（Linux）与本地（Windows）结论不同时，先怀疑这类写法：

- 测试 monkeypatch 了**实现根本不读的属性**（例：实现看 `os.name`，测试却 patch `sys.platform`）→ 在作者的系统上碰巧通过，在别的平台必挂。修法：patch 实现真正读的那个，并**把两个分支都测上**（新加的 "POSIX + uvloop → uvloop" 用例让该分支在 Windows 上也能测）。
- `Path()` **不是空路径**，它是 `Path(".")` 且**永远为真** → 任何 `if policy.allowed_root:` 式的生成器都会给出真实分支。要测“未设置”就得传 `""`/`None`。
- 只在 Linux 上跑的模块用 `pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"))` 保护 → 这类文件在 Windows 上永远 skip，本地全绿也会骗人。改动后若无法本地验证，就**直接调用被测函数按测试的断言手工验证一遍**。

## 发布前的检查（本仓库）

- `OPEN_SOURCE_CHECKLIST.md` 有完整审计与证据；`scripts/ci_local.sh` 一次跑完 CI 的全部四道门禁；`scripts/prepare_github.py --owner X --repo Y [--apply]` 替换 `OWNER/REPO` 占位符。
- **历史重写后 `.git` 里不含 web/dist**（gitignore），但打包 wheel 需要 `web/dist`（走 `hatch_build.py`，见下文）→ 发 wheel 前先 `npm run build`。
- 个人路径泄漏点（曾存在，已修）：`scripts/gen_languages.mjs`、`build_exe_with_icon.py`、`kairos/bench/real_eval.py`、`web/public/branding/_build_icons.py`、`web/src/components/BrowsePanel.tsx`。

### 密钥/隐私审计：**必须扫全历史，工作树扫描会漏**

工作树 `git grep` 只能证明「现在没泄漏」，**不能证明历史里没有** —— 实测漏过的都是「曾经提交过、后来删掉」的文件：根目录 `settings.json`（早期版本，后被删）和 `scripts/debug_agnes_apihub.py`（注释写着 "from screenshot"）里各躺着一把真 key，另有 2 把散在 `docs/*` 与几个已删测试脚本，共 4 把。

```bash
# 1. 逐提交扫（每个 sha 跑一次 git grep，别用 Python 逐 blob 读管道，Windows 上慢 10 倍+）
for c in $(git rev-list --all); do git grep -hIE "sk-[A-Za-z0-9_-]{20,}" "$c"; done | sort -u
# 2. 分类：占位符 vs 真值（真值 = 不含 here/xxx/your/redact/example/fake/dummy/test）
# 3. 「这把还在用吗」用哈希比对回答，**永远不要打印密钥本身**
#    sha256(key)[:12] 与 data/settings.json 里的值对比
# 4. 抹除（保留提交，只替换字符串）：文件放仓库外，事后立刻删
git-filter-repo --force --replace-text /tmp/redact.txt   # 内容：literal:<key>==>REDACTED
#    同样的办法可抹机器用户名：literal:<user>==>user（当前文件 + 全历史一起改）
```

配套要点：

- **`git diff` / `git status` 对“被 gitignore 的文件”是空判**（真实踩坑）：`data/settings.json` 被 `.gitignore` 挡住 ✓ → `git diff --quiet -- data/settings.json` 报告“无差异” ✓、`git show HEAD:data/settings.json` 返回 **0 字节** ✓ —— 于是“工作树与已提交版本一致”这个“证据”是**假的** ✓（它根本不在库内 ✓）。
  **规则**：验证一个文件的内容前，先 `git ls-files --error-unmatch <path>` 或 `git rev-list --all -- <path> | wc -l` 确认它**在不在版本库里** ✓；不在库内就只能用**文件自身**的哈希/内容比对 ✓，不要说“与 HEAD 一致” ✓。
  推论：`data/settings.json` **从未进过 git** ✓ → 它里面有真 key 也不构成泄漏 ✓；判断“仓库干净”唯一有效的办法是**扫全历史对象**（见上方逐提交 git grep / `git cat-file --batch` 扫全 blob ✓）。

- `data/settings.json` 里存的是**占位符**（`sk-tes…`/`sk-ant…`，11-12 字符），真 key 走环境变量（`provider.apiKeyEnv`）→ 首次判断别慌，先看长度。
- **重写前建的 bundle 含明文密钥** → 别发布、别留在会被打包的目录；重写后重建一份干净的，删掉旧的（实测 724MB 那份就是重写前快照）。
- 替换用的 redact 文件**本身含明文**，在仓库外生成、用完 `rm`。
- 重写后回归验证：`REAL-looking keys = 0`（全历史）+ `git status` 干净 + 提交数不变 + 工作树里只剩占位符。
- 每次重写都会换掉所有哈希；无远端时零代价，但要提醒「别处 clone 过的需重新 clone」。

## 打包：怎么把 web/dist 放进 wheel（实测过的坑）

`pip install kairos-code` 应该同时给 API **和界面**（`api/app.py` 从 `<site-packages>/web/dist` 提供 UI）。三条路的实测结果：

| 写法 | 结果 |
|---|---|
| `artifacts = ["web/dist"]`（放 `[tool.hatch.build]`） | ❌ wheel 里 **0 条** web/dist |
| `artifacts = ["web/dist"]`（放 wheel target） | ❌ 同样 0 条 —— 这个选项对 wheel 基本没用 |
| 静态 `force-include` | ✅ 能进（199 条），但缺目录时**硬失败**：`FileNotFoundError: Forced include not found: .../web/dist` → 全新 clone 的 `pip install -e .` 直接炸 |
| **`hatch_build.py` + `[tool.hatch.build.targets.wheel.hooks.custom]`** | ✅ 有 dist 就进（199 条含 index.html，wheel 5.6MB），没 dist 也能正常构建（只剩后端） |

钩子类**必须叫 `CustomBuildHook`** 且 `PLUGIN_NAME = "custom"`，用 `hooks.custom` 段引用；自己起名字会报 `UnknownPluginError: Unknown build hook: <名字>`（该名字需要单独发插件包才行）。

验证方式：`pip wheel . -w /tmp/x --no-deps` 后用 `zipfile` 数 `web/dist/` 条目；再 `mv web/dist` 后重跑，确认不报错。**别只改配置就声称“已打进 wheel”**（我就是先写错了注释、实测才发现是 0）。

## 两个「表建好了但没人写」的历史 bug（重要）

- **`loop_rounds` 在 R38.8 之前没有任何生产写入点**（表、加载器、UI 端点、FTS 镜像、跨轮记忆全都有，就缺写）。后果：重启即丢轮次历史、门禁报告/历史页全空。已在 `loop_runner` 的 round-memory 块里补 `persistence.save_loop_round(...)`（和 `index_loop_round` 同一 try 块）。改循环时别删它。
- `save_checkpoint` 同样没有生产调用者 → `loop_checkpoints` 表长期为空（报告里的检查点段落因此通常不出现，属正常，不是 bug 复发）。

## 仓库/运维杂项（本仓库特有）

- 后端端口：`start_backend.bat` 设 `KAIROS_PORT=9527` + `KAIROS_SKIP_WORKTREES=1`，再用绝对路径的 venv python 跑 `-m kairos.main`。**git-bash 里 `cmd //c xxx.bat` 会被 MSYS 拆坏**（变成交互式 cmd），要起后端就直接 `KAIROS_PORT=9527 KAIROS_SKIP_WORKTREES=1 .venv/Scripts/python.exe -m kairos.main`（日志 `>> logs/backend_out.log`）。不设 `KAIROS_PORT` 会跑在默认 8900。
- **`kairos/cli.py::main()` 有个 legacy 守卫**：`argv[0]` 不在白名单里就当成 "serve" 启动服务器。新增子命令（gate/demo…）必须同步加进那个 tuple，否则 `kairos 新命令` 会静默变成起服务。
- 历史瘦身：`.git` 曾 731MB，其中 **`_codex56.rar` 一个文件 640MB**（还有 `_dsh_extract/` 7530 文件、`build/` 45MB）。已用 `git filter-repo --invert-paths --path ...` 剔除 → **31MB**。备份 bundle 在 `<outside>/<a bundle outside the repo>`（759MB，`git clone <bundle>` 可还原）。想再瘦身先 `git rev-list --objects --all | git cat-file --batch-check=...` 找大 blob。
- `logs/` 已从 git 取消跟踪（运行时输出，永远脏）；`.gitignore` 覆盖 `_dsh_extract/_dsh_pkg/build/.agn_tmp/logs/.kairos/web/dist/` 等。
- i18n：`en-US` 是**源语言**（无 catalog），`merge_i18n.py --coverage` 里它显示 0% 是正常的；改了 en 文案要**先把该键从 61 个 catalog 里删掉**再跑 `translate_i18n.py --all`，否则旧译文会被保留。
- `check_i18n.mjs` 会抓两类真 bug：JSX 里的 `//` 注释（会被当文本渲染 → 必须写 `{/* */}`），以及函数实参里的文案。

## 扩展三面（MCP / 插件 / skills）：装好了 ≠ 用得上（R38.11–38.12 实测）

**核心判据**：任何「装了什么 / 能不能用」的接口，必须问**运行时对象**（`SkillsLoader.discover()` / `McpRegistry` / `PluginManager`），不能数注册表 JSON。本项目真实翻车：`/extensions/summary` 报 “29 installed” 而 `SkillsLoader` 一个都返回不了 —— 因为包里的 `kairos/skills/` 从未被复制到代码里写死的 `<data_dir>/bundled_skills`（字符串只出现在那两行里）。诊断这类问题：**磁盘文件数 vs 加载器返回数**，两者不等就是它。`GET /api/extensions/capabilities` 是这道题的答案（也带 `problems` 数组点名不可用项）。

**不要猜 MCP SDK 的名字/签名** —— 我连撞两次：

| 猜的 ✗ | 这个版本的真实 API ✓ |
|---|---|
| `streamablehttp_client(url, headers=…)` | `streamable_http_client(url, *, http_client=…)`；头信息走 `create_mcp_http_client(headers=…)` |
| `sse_client(url, headers=…)` | ✓ 就是这个（headers 直接收） |
| 返回值是 2/3 元组 | `TransportStreams` 对象（取 `.read/.write`，也兼容元组 —— 已写 `_read_write()` 防御） |

动手前先 `dir(module)` + `inspect.signature(fn)`，一次就能省掉两轮失败。工具返回要 `model_dump(by_alias=True)`，否则 `inputSchema` 这类驼峰字段会丢。

**配置文件的形状**：MCP 配置的顶层键是 **`mcp_servers:`**（不是 `servers:`）；远程条目用 `transport: http|sse` + `url` + `headers`（不要 `command`）；`load_configs()` 会**静默丢弃**建不出来的条目 → 要解释「配了没反应」就用 `audit_configs()`（同一份选择 + `rejected` 原因）。

**自带的离线服务器工具表分裂在两处**：`mcp_filesystem_server._TOOLS`（老模块，自己一套）与 `mcp_local_servers.tools_for()`（新模块，git/sqlite/time/fetch）→ 任何计数都要走 `kairos/mcp_local_servers.bundled_tool_names()`（`strict=True` 取真实异常，默认安静）✓ 否则出现“5 台 11 工具”与“5 台 16 工具”两个数字。

**skills 有两套布局，同名会互相覆盖**：`x.md`（适配版，带 `priority`/`when:` 触发元数据）与 `x/SKILL.md`（原版，带正文）—— 加载器按 front-matter 的 `name:` 去重，**保住的那份常常是丢掉元数据的那份** → `test-driven-development` 因此不再匹配 TDD 提示词 ✓。修法是**合并而非并存**（`scripts/merge_duplicate_skills.py`，幂等）：把元数据折进目录版再删平铺件。

- 合并时**正文注释必须放在 front matter 之后** —— 放前面会让加载器判定“没有前置元数据”并**跳过整个技能**（我踩过）。
- 目录式布局下**技能名取自目录名**（`docx/SKILL.md` → `docx`），不是文件名。
- 迁移脚本先给 `--dry-run`，并**用加载器自己的 `_parse_skill` 解析名字**做键（用文件名会得到 “merged 0”）。
- 老测试常常**编码了缺陷形态**（断言平铺路径存在、断言 `>=40` 个文件）→ 改断言前先想清楚它在保护什么；正确做法是换成更严格的不变量（按技能名断言、断言“磁盘数 == 加载数”）。

**深链/doctor 一致性**：`kairos doctor` 的检查必须与 API 用**同一个函数**取数，否则两处数字会分叉（doctor 说 11、视图说 16）。改一处计数逻辑就 `grep` 另一处。

## 用脚本改代码时：必须复制被匹配行的缩进 + 立刻 py_compile

本仓库文件是 CRLF ✓、且经常需要**多点重复修改** ✓（例如给某个文件的每一处 `X = Foo()` 后插入一行 ✓）—— 这种活我用一个 python 脚本批量做过多次 ✓。踩过一次 ✓：脚本里把插入行的缩进**写死了 8 个空格** ✗，而其中一处匹配行在**更深一层**（async 函数内 ✓）→ 文件立刻 `IndentationError` ✗ → pytest 收集阶段就崩 ✓，白跑一轮 ✓。

**规则**：

1. 插入行一律用 **`indent = line[:len(line) - len(line.lstrip())]`** 取**被匹配行**的缩进 ✓，不要写死 ✓。
2. 脚本跑完**立刻** `python -m py_compile <file>` ✓，再跑 pytest ✓ —— 这一步能在 0.8 秒内抓住语法错 ✓，否则 pytest 的报错信息看起来像“测试炸了”而不是“我改坏了” ✓。
3. 脚本要**可重入** ✓：先 `replace()` 掉自己上次插入的标记 ✓ 再重新插入 ✓（我那次就是靠“先撤销再重插”一次修好的 ✓）。
4. 只改**几处**时优先用 `patch` 工具（它会校验唯一匹配 ✓）；批量插入才写脚本 ✓。

## 打包/交付：`rm -rf <dir>` 在被占用时会先删光内容再失败（真实事故）

打包到某一个目录前**不要** `rm -rf` 它 ✗ —— 目录里有进程在跑（例如用户正在试用那个 exe ✓）时，`rm -rf` 会**先把里面的文件全删掉** ✓、**最后才**在目录本身上失败 ✓（`Device or resource busy` ✓）→ 结果是：目录变成**空的** ✓、而正在跑的那个进程**照常运行** ✓（Windows 把已删除的 exe 继续映射 ✓）→ 用户一退出就**再也打不开** ✗，而且他很可能没注意到 ✓。

**规则**：① 构建到**新目录** ✓（`--out dist_pre` ✓），确认无误后再谈替换 ✓；② 真要重建同一个目录 ✓，先**只读**查有没有进程在用 ✓（`Get-CimInstance Win32_Process | Where ExecutablePath -like '*<dir>*'` ✓），有就换目录 ✗ —— **绝不为了构建去杀用户的实例** ✓；③ 万一已经删了 ✓，**立刻**用同样的 `--out <dir>` 重建回去 ✓（目录空着时能写 ✓），并在回复里主动说明 ✓。

**判据**：交付后自问“用户下次双击那个路径，文件还在吗？” ✗ —— 只验证“我的冒烟通过了”是不够的 ✓。

## Playbook：历史里混进了第三方大文件（本仓库真发生过）

症状：`.git` 几百 MB，但工作树里看不到任何大文件。

1. 先找大 blob（不要在 6000 文件的目录上跑 `du`，会超时）：
   `git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' | awk '$1=="blob" && $3>2000000 {print $3, $4}' | sort -rn | head`
   实测抓到过：**`_codex56.rar` 一个文件 640 MB**，以及 `_leila_extract/8-05codex5.6产品/Leila-Codex-Offline-1.0.7-...`（第三方产品解包目录）。
2. 查是谁加进来的（这一步很有价值）：
   `git log --all --date=iso --format='%h | %ad | %an | %s' -- <path>`
   → 实测作者是 **`Kairos Coder`**、说明是 `kairos: round 1 approved/rejected`：**是早期 Kairos 循环自己的 checkpoint 自动提交扫进去的**，因为那次 loop 的 `work_dir` 指向了仓库根。不是人提交的。
3. 先备份再重写：`git bundle create <outside-repo>.bundle --all`（本次 759 MB）。
4. 剔除：`git filter-repo --force --invert-paths --path A --path B`（多个 `--path` 一起给），然后 `git reflog expire --expire=now --all && git gc --aggressive --prune=now`。
   注意：**filter-repo 会重写所有提交哈希**（含没被碰过的），有远端时要重新加远端并 force push；无远端时只影响本地。空提交默认会被清掉。
5. 验证：`git rev-list --count HEAD`（数量应不变）+ `git log --all --oneline -- <path> | wc -l`（应为 0）+ `git status`（只应有本来就脏的项）。
6. 预防：**不要把 loop 的 work_dir 指到仓库根**。更根本的修复已经落地（见上面「checkpoint 提交会扫掉父仓库」）：提交被 `_scope()` 限定在 workspace 子树，且 `.gitignore` 挡住 `*.rar/*.7z/_*_extract/`。想额外兜底可以对 checkpoint 加 blob 体积上限（未做）。
