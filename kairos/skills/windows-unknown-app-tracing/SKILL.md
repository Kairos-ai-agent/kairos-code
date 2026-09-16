---
name: "windows-unknown-app-tracing"
description: "溯源/取证用户电脑上\"不知道哪里来的\"程序（\"我电脑里有个 X 软件哪里来的？\" / \"X 安全吗？\" / \"X 是什么时候装的？\"）。多维度调查方法：文件系统搜索（中文名+拼音缩写+英文名）、PE VersionInfo 元数据（含 PowerShell GBK 编码 workaround）、注册表 Uninstall 项（HKLM vs WOW6432Node）、应用配置/日志（DelayPi"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/.archive/windows-unknown-app-tracing/SKILL.md"
---
# Windows Unknown App Tracing

用户提问 "我电脑里有个不认识的程序，哪里来的？" "这个 X 是谁家的？" "X 是广告软件吗？" 时使用此 skill。**绝不** 仅靠单一证据做判断 — 至少交叉验证三个维度的信号。

## 何时加载

- 用户报告电脑出现陌生应用 / 工具栏 / 服务 / 扩展
- 用户怀疑某程序是广告/捆绑安装
- 用户想确认程序是官方还是推广渠道拉来
- 控制面板卸载某个程序失败，需要溯源它从哪条路径装上

## 多维度调查方法（按权威性排序）

### 1. PE VersionInfo 元数据（最高权威 — 单一最强信号）

PE 资源里的 `CompanyName` / `ProductName` / `ProductVersion` 是**开发者签名**，不是搜索结果猜测。
- 中文 PE 字符串用 GBK 编码，bash heredoc + PowerShell `-Command` 会触发 `UnicodeDecodeError`。看 [references/pe-versioninfo-powershell-tricks.md](references/pe-versioninfo-powershell-tricks.md) 拿可靠的 .ps1 + UTF-8 输出 workaround。

### 2. 注册表 Uninstall 项（合规性信号）

- `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall` — 64-bit 程序
- `HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall` — 32-bit 程序跑在 64-bit Windows
- **没注册 = 静默安装**，正规安装器一定会注册到这里（"程序和功能"才能看见）

### 2.5 开机复活源必须覆盖 Windows 服务

用户说“开机还会出现”时，不能只查 `Run`、计划任务和 Startup 文件夹；还必须检查 `Win32_Service` / `sc.exe`。广告播放器常把真正的复活源藏成 `AUTO_START` 的 LocalSystem 服务，服务名甚至比主程序更像正常组件。

```powershell
Get-CimInstance Win32_Service |
  Where-Object { ($_.Name+' '+$_.DisplayName+' '+$_.PathName) -match '目标程序名|安装目录关键词' } |
  Select-Object Name,DisplayName,StartMode,State,PathName
```

清理顺序：停止相关进程 → `Stop-Service -Force` → `sc.exe delete <ServiceName>` → 删除程序目录 → 再查 Run/Task/Startup/Uninstall。若普通权限下目录删除失败且服务仍能被 `sc query` 查到，使用 `Start-Process powershell.exe -Verb RunAs -Wait ...` 运行精确清理脚本，并在 UAC 后重新独立验证。完整流程见 `references/windows-service-autostart-cleanup.md`。

### 3. 应用配置/数据目录（推广渠道指纹）

`%APPDATA%\<AppName>Data\` 或 `%APPDATA%\<AppName>Ins\` 下找 `mpConfig.ini` / `setting.ini` / `appconfig`，看以下字段：

| 字段 | 含义 |
|---|---|
| `InstallTime=<unix_ts>` | installer 首次触发时间（Unix epoch，本地时区） |
| `DelayCreateLink=1` | 延迟创建桌面快捷方式 |
| `DelayCreateRegisterKey=1` | 延迟写注册表 |
| `DelayCreateShellMenu=1` | 延迟注入右键菜单 |
| `DelayAutoRun=1` | 延迟加开机启动 |
| `Activate=0` | 尚未激活（首次启动触发延迟动作） |
| `DelayPid=tend_cadpaly` | **腾讯 CADP 广告渠道标识**（PID 形如 `<vendor>_<channel>_<product>`） |

四个 `Delay*` 全开 = 典型"先装着不打眼、用户启动后激活"的捆绑推广。

### 4. 时间线重建（推导出三个时间点）

- **触发时间**：`InstallTime=1785431669` 转 `datetime.fromtimestamp(...)` → 2026-06-29 19:14:29 (北京)
- **部署时间**：`C:\Program Files (x86)\AppName\` 内的文件 mtime（通常 install 日志里的"写入完成"时间）
- **部署时间 vs 触发时间的差** = installer stage 时间 vs 真正安装时间（静默/延迟安装会出现几小时甚至几天跨度）

### 5. 启动/SDK 日志（电话回拨证据）

`SlsLog/BSLog-*.log` 和 `UserData/log/SDKLog*.log` 里找：
- `module：phtrun.exe` — `phtrun` 是腾讯系广告 launcher 命名
- `SLS 发送所有日志！` + `SLS Old 发送成功：N 条日志` — 启动时上报
- `sls app config response failed!` + `disable https retry.` — 试图拉远程广告/活动配置失败
- `LogEnd` 单独一行（缺 LogBegin）= `Uninstall.exe` 被 SDK 拖起来过但没真卸

### 6. 自带 binary 命名模式（快速初筛）

| DLL / EXE 前缀 | 含义 |
|---|---|
| `WMei*Player*.exe`, `*Play*Box*.exe` | 带播放容器的广告播放器家族 |
| `phtrun.exe` | 腾讯系广告 launcher |
| `XD*` DLL | 自家 SDK（心动 / 引擎） |
| `QWK*` DLL | 自家 SDK（趣看客系） |
| `*ShellExts{32,64}.dll` | Explorer 右键菜单注入（替代 shell extension 服务注册，黑科技） |
| 5MB+ standalone `Uninstall.exe` | 自带卸载器，没走 MSI |

## 输出格式（按需精简）

1. **真实身份**：`ProductName=vX.Y.Z`, `CompanyName=<真实公司英文名>`（PE VersionInfo, 给文件路径+PowerShell 输出）
2. **安装渠道**：例如 "Tencent CADP 渠道 PID=`tend_cadpaly`"（从 mpConfig.ini 给证据）
3. **时间线**：触发 / 部署 / 用户最近操作三个时间点（从日志/mtime/InstallTime 给证据）
4. **行为**：是否电话回拨 / 写开机启动 / 改 shell extension（从日志+文件清单给证据）
5. **清理建议**：用 Uninstll.exe / 手动删目录 + 检查注册表 + 检查 HKCR CLSID

**必须给证据**：用户问"是 X 吗/改完了吗"时给 grep 行号 / 文件路径 / 配置字段，不要只说 yes/no。

## 完整清理与重启复现闭环

当用户说“开机时还会出现 / 没清干净”时，不要只重复删除普通 `Run` 项。先回看上一轮实际清理范围，确认是否只清了同公司伴生软件而保留了目标程序；随后把目标程序当作一个完整持久化家族处理。

### 清理顺序

1. **进程**：按精确产品目录和二进制家族筛选并终止；不要用泛化的 `*Player*` 误杀其他播放器。
2. **服务**：检查 `Win32_Service` 的 `Name + DisplayName + PathName`。广告播放器可能使用 `AUTO_START` 的 LocalSystem 服务，而不是 `Run` 项（例如服务路径指向产品目录内的 `*SVBox.exe`）。停止后执行 `sc.exe delete <name>`。
3. **启动面**：检查 HKCU/HKLM 的 Run、RunOnce、WOW6432Node，计划任务，用户/公共 Startup，桌面和开始菜单快捷方式。
4. **注册与壳扩展**：删除目标产品的 Uninstall 项；检查产品目录中的 `*ShellExts32.dll` / `*ShellExts64.dll` 及相关 CLSID 注册。
5. **三类目录**：同时清理二进制目录、`%APPDATA%\<Product>Data`、`%APPDATA%\<Product>Ins`，并检查 LocalAppData 同名目录与 CrashDumps。
6. **提权重跑**：Program Files 和 LocalSystem 服务通常需要管理员权限。若当前 shell 非提升态，用 `Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList ...` 运行清理脚本，并等待用户接受 UAC。

### 不允许静默假成功

- 不要在破坏性步骤上全局使用 `$ErrorActionPreference='SilentlyContinue'` 后直接宣称清理完成。删除服务/Program Files 失败时必须保留错误，或用独立探针验证。
- `Remove-Item` 返回后目录仍可能存在；服务删除也可能因权限不足失败。每一类持久化都要独立复查。
- 通过 bash 调 `powershell.exe -Command "...$var..."` 时，bash 会先展开 `$var`，导致 PowerShell 变量消失。复杂逻辑写入 `.ps1` 后用 `-File` 执行。
- Windows PowerShell 5.1 读取“UTF-8 无 BOM”脚本时，中文字符可能被错误解码并引发看似无关的 ParserError。可使用纯 ASCII 脚本，或保存为 UTF-8 BOM；不要把乱码解析错误误判成 PowerShell 语法错误。

### 完成标准（必须给证据）

至少同时满足：

```text
目标进程：无
目标服务：sc query 返回 1060（服务未安装）
程序目录：不存在
Data / Ins 目录：不存在
Run / RunOnce / 计划任务：无目标路径
```

若用户明确说“开机出现”，最好在条件允许时完成一次真实重启后的复现验证；若当前会话不能安全重启，必须明确说明当前只完成了静态持久化验证，不能把它表述成“重启验证通过”。

## 关键 Pitfalls

- **永远别信 DisplayName 网上搜索**："完美播放器"听起来很官方，PE `CompanyName` 才是开发者真名。每次都读 PE VersionInfo，别靠名字判断。
- **PE VersionInfo 中文 GBK 编码** —— 看 references 文件的 PowerShell workaround 详情。直接 `-Command` 嵌 bash heredoc 必报错。
- **不要混淆二进制目录和数据目录**：
  - `C:\Program Files (x86)\AppName\` — binary
  - `%APPDATA%\AppNameData\` — 用户数据/日志
  - `%APPDATA%\AppNameIns\` — installer workspace 残留
  - 三个都要清，缺一个会复活。
- **`Uninstall.exe` 自卸载经常假卸载**：log 里只有 `LogEnd` 一行没有 `LogBegin` = SDK 被拖起来但没真卸。
- **`Delay*` 字段全是 1 = 首次启动后激活**：不是"程序没启动"，是"注册表项 / 快捷方式还没创建"。检查时如果发现 Activate=0 + 没人启动过，不要误以为安全。
- **`phtrun.exe` 是腾讯渠道特征**：不是 360/2345/百毒标志。看到就大概率走 Tencent CADP。

## 相关 / 易混淆 skill

- `security-auditor-1.0.0` — 代码 review 安全视角
- `binary-analysis` — Windows/Linux 二进制分析（PE 内部结构会涉及，但本 skill 关注 app 安装溯源而非逆向）
- `windows-batch-scripts` — Windows 系统脚本与排查
