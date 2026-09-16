---
name: "release-engineering"
description: "Use when publishing release artifacts and verifying them."
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\release-engineering\\SKILL.md"
---
# 发布工程：把产物真正发出去并验证

适用任何「打 tag → CI 构建产物 → GitHub Release」形态的项目（也适用于 CI 降级、必须手动发布时）。核心不是构建命令，而是**顺序**与**验证纪律**。

## 铁律（每次都适用）

1. **Release 步骤必须幂等**：`gh release view <tag>` 成功就 `gh release upload --clobber`，否则 `gh release create`。这是「CI 挂了也能发版」的全部前提 —— 先确认这条，再谈别的。
2. **验证对象是发布物，不是本地构建**：`gh release download` 取回来 → 解包 → 跑项目自己的冒烟脚本。本地跑通 ≠ 发布物可用。
3. **冒烟前确认端口空闲**：固定端口上可能已跑着另一个实例（用户自己的副本也挑同样的端口），冒烟会读到**别人的**状态 —— 典型误判是「版本号不对 / 硬编码版本」。先 `netstat -ano | grep ":PORT"`；读数离谱就换个端口复测再下结论。
4. **杀进程树，不杀进程**：会 fork 真身的启动器（PyInstaller onefile、包装脚本）在 `terminate()` 后留下无窗口子进程 —— 占着端口，在 Windows 上还占着 exe 文件锁，导致下次构建 `[WinError 5]`。Windows 用 `taskkill /F /T /PID <pid>`；POSIX 起进程时 `start_new_session=True` + `killpg`（**绝不**给自己的进程组发信号，那会把跑测试的进程一起带走）。把这段写进冒烟脚本本身，让每次运行自己收尾。
5. **资产命名与归档形状照抄历史版本**：先下一个旧 Release 看它的文件名与目录结构（`<name>-<ver>-<label>/<binary>` + README 之类），否则文档和用户既有下载链接会对不上。
6. **二进制体积要能解释**：干净 venv（只装核心依赖）冻出的包远小于开发 venv（带 extras）—— 与历史产物体积差一倍通常就是这个原因，发布前先确认构建环境。
7. **版本号是「多处 + 元数据」**：所有版本源都要改（`pyproject.toml`、包 `__init__.py`、前端 `package.json`）+ CHANGELOG；改完刷新已安装元数据（`pip install -e . --no-deps`），否则 `importlib.metadata.version()` 的自检会说谎。
8. **构建产物目录必须被 ignore 覆盖**，用一条 glob（如 `/dist*/`）：漏一个目录，恰恰是发版时才出现的那个就会被扫进源码包（实测把 5.6 MB 的 wheel 打进 sdist）。
9. **冻结 / 打包前清字节码缓存**：Python 判 `.pyc` 是否过期用「mtime（秒级）+ 文件大小」，等长修改（`"0.1.2"→"0.1.3"`）会让旧模块继续有效、被冻进产物；打包器的 `--clean` 只清它自己的缓存。
10. **发布物必须自带校验和**：同一个 CI job 为所有资产生成 `SHA256SUMS` 并随资产上传 —— 这是「用户能验证下载物」和「应用能自更新」的共同前提（见下文「自带更新」）。

## 自带更新（产物是独立二进制时）的铁律

自动更新的全部风险就是「下载即执行」，所以规则要写在代码里而不是文档里：

1. **没有校验和就不许替换**：release 必须在 `SHA256SUMS` 里给出该资产的 sha256，否则只通知、**不下载、不执行**。校验和必须由同一次 CI 生成并上传。
2. **运行中的可执行文件不能覆盖自己**：下载成 `<exe>.new` → 交给**分离的 helper**（等本进程退出 → 改名 → 重启）；Windows 用 `DETACHED_PROCESS`，POSIX 用 `start_new_session` 派生。
3. **不能自替换的地方只通知**：非独立二进制安装（pip / 源码）、签名不完整的平台、安装目录不可写 —— 一律返回一个 `reason` 让 UI 说人话，不要假装成功。
4. **永不静默**：启动只做一次只读检查（带缓存 + 可关开关），下载只在用户点击时发生，检查失败不报错、不弹窗。
5. **缓存要缓存「结果」**：只把上游响应存进缓存，会让每次命中仍要再取一次派生数据（如校验和文件）→ 缓存**已解析的 payload**（`{checkedAt, payload}`），TTL 内零网络。

## 交付一个「本地构建」给用户试用

发布前用户常要先自己跑一遍（「先打包到本地，我先用一下」）。顺序不能乱：

1. **先重建被打包的资源**（前端/界面包）：源码在上次构建之后又被改过，冻出来的产物里就是旧界面。
2. 冻结 → 冒烟 → 按 CI 同形状打包。
3. **自己验证新功能真的进去了**：冒烟通常只查 health + 首页，新端点/新界面要开进程打一遍、看真实响应。
4. **先备份「产物真会打开的那份数据」**：安装型应用的运行时数据在用户目录（`%LOCALAPPDATA%\<app>` / `~/.<app>`），**不是**仓库里的 `data/`；用只读连接做一致快照。
5. 交付时说明：启动命令 + **看哪里**（刷新键位、具体页面）+ 与已安装版本共用同一数据目录 + 快照路径。

命令级配方（校验和步骤、产物内新功能验证、本地构建与交付）见 `references/publish-and-verify.md`。

## CI 降级时照样发版（决策顺序）

1. **分层确认是不是平台问题**：`/api/v2/status.json` 只是滞后的总览，**别信它一句** → 看组件级 `/api/v2/components.json`（Actions / API Requests）与事故级 `/api/v2/incidents/unresolved.json`（`critical` = 别指望被调度）。
2. **仓库级判据**：事故后新触发的 run 秒变 `in_progress`，而事故期创建的 run 永远 `queued` ⇒ 旧条目是死队列项，不是「我们慢」。这种条目 `cancel` / `rerun` 会给出自相矛盾的报错（"already completed" + "already running"），别纠缠。
3. **重触发，而不是重做一切**：`gh workflow run <workflow> --ref <tag>` —— 用 tag 作 ref 时工作流看到 `GITHUB_REF=refs/tags/…`，于是被当成**真发布**；不带 tag 的同一命令是 dry-run（只构建不发布），正好当「runner 恢复了没」的探针。探针用完就取消，把 runner 让给真发布。
4. **本机能造的先传**：wheel / sdist / 本平台二进制先 `gh release upload --clobber`；其余（其他平台二进制、镜像）等 CI 恢复自动补齐 —— 幂等 Release 让这两步不冲突。
5. **「做不到」要限定条件，并先给出能做的路径**：把结论收窄到具体环境 —— 「**这台机器**造不出 Linux / macOS 二进制：PyInstaller 不能交叉编译、本机无 WSL、无容器运行时」；同时说清谁做得到（CI 的 ubuntu / macos runner、WSL、真机）**以及历史产物本来就是这么来的**，并附证据（`uname` / `docker --version` / 进程与端口表）。笼统讲「物理上做不到」会被追问「那之前的版本哪来的」—— 平台 runner 造过历史发布物这件事，应该由你主动先讲。

命令级配方（构建 / 上传 / 下载回来验证 / 匿名验证镜像 / CI 超时诊断）见 `references/publish-and-verify.md`。
