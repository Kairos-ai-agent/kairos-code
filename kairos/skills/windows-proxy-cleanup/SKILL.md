---
name: "windows-proxy-cleanup"
description: "Use when Windows startup proxy breaks network. Remove it."
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\software-development\\windows-proxy-cleanup\\SKILL.md"
---
# Windows 启动代理排查与清除

覆盖：开机后系统代理指向 `127.0.0.1:7897` / 请求报 `ProxyError ... WinError 10061` / Windows 设置里删不掉或反复出现的代理。

## 症状
- 所有请求报 `ProxyError: Unable to connect to proxy ... [WinError 10061] 目标计算机积极拒绝`（Hermes 的 web_search/web_extract 也会一起挂）
- `netstat -ano | grep <port>` 显示 **SYN_SENT** 而不是 LISTENING → 没人监听该端口，代理客户端已死，只剩残留开关还开着
- Windows 设置 → 网络和 Internet → 代理 里看到 `127.0.0.1:7897`

## 一、定位（一次批量跑完，别一条条试）
```bash
netstat -ano | grep 7897                       # SYN_SENT=死端口; LISTENING=客户端在跑
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" | grep -iE "Proxy|AutoConfig"
netsh winhttp show proxy                       # 与 WinINET 是两套，都要看
python -c "import urllib.request;print(urllib.request.getproxies())"   # Python 读注册表的结果；非空=所有 python 工具都被代理
env | grep -i proxy; reg query "HKCU\Environment" | grep -i proxy      # 环境变量层
```
关键点：**`urllib.request.getproxies()` 非空 = Hermes 自身网络也被这个残留代理劫持**（Python 在 Windows 上会读 `Internet Settings` 注册表）。这是判断「是不是这个代理害的」最直接证据。

## 二、找是谁在开机时植入的
```bash
reg query "HKCU\...\CurrentVersion\Run"; reg query "HKLM\...\CurrentVersion\Run"; reg query "HKLM\Software\WOW6432Node\...\Run"
ls "$HOME/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/"
schtasks /query /fo CSV /nh | iconv -f GBK -t UTF-8 | grep -viE "Microsoft"   # 非微软任务全列出
```
常见元凶：已卸载的 VPN/Clash 客户端留在 Task Scheduler 里的**登录触发器**任务（例：`\HoxxVPN`，指向已删除的 `C:\Program Files (x86)\Hoxx VPN\HoxxVPN.exe`，`RunLevel=Highest`）。系统代理类客户端卸载时几乎都会把 `ProxyEnable=1` 留在注册表里。

## 三、清除（先备份）
```bash
mkdir -p "$LOCALAPPDATA/Temp/proxy-backup" && reg export "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" "$LOCALAPPDATA/Temp/proxy-backup/InternetSettings-before.reg" /y
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyEnable /t REG_DWORD /d 0 /f
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyServer /f
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyOverride /f
schtasks /change /tn "<任务名>" /disable
```
`schtasks /change` 报 **拒绝访问**：任务文件 `C:\Windows\System32\Tasks\<名>` 的 ACL 只给当前用户 `Read`，必须提权，让用户点一次 UAC：
```bash
powershell.exe -NoProfile -Command "Start-Process schtasks.exe -ArgumentList '/change','/tn','HoxxVPN','/disable' -Verb RunAs -Wait"
```
（terminal 里 `schtasks` 输出是 GBK，套 `iconv -f GBK -t UTF-8` 才看得懂。）

## 四、验证（必须给证据，不能只说「改好了」）
```bash
reg query "HKCU\...\Internet Settings" | grep -iE ProxyEnable        # 0x0
python -c "import urllib.request;print(urllib.request.getproxies())"   # {}
netstat -ano | grep 7897            # 空
curl -s -o /dev/null -w "%{http_code}\n" --max-time 15 https://www.baidu.com
```
顺带查自动检测/PAC：`DefaultConnectionSettings` 二进制 flags（byte 8–11）`0x01`=直连、`0x02`=代理、`0x04`=PAC、`0x08`=自动检测；`AutoConfigURL` 值不存在即无 PAC。

## 五、收尾
- 清完后国内站点直连可通；**Google/GitHub 直连超时是 GFW 正常现象，不是修复失败**。需要外网就另起一个活着的代理客户端，别把死掉的 7897 写回去。
- 残留可一并清：已禁用/失效的计划任务、`HKCU\Software\<厂商>`、`%APPDATA%\clash_win`、旧的 Chrome 自启代理扩展目录。删文件/任务前问用户一句。
