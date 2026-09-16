---
name: "openclaw-data-import"
description: "从 OpenClaw 迁移数据到 Hermes Agent — skills、agent 记忆、会话记录、工作区文件。"
priority: 0.5
version: "1.0.0"
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\.archive\\openclaw-data-import\\SKILL.md"
---
# OpenClaw → Hermes 数据导入

当用户有现有 OpenClaw 安装（`.openclaw-autoclaw/` 目录）并想迁移到 Hermes 时使用。

## OpenClaw 目录结构

```
.openclaw-autoclaw/
├── skills/              # OpenClaw 技能（每个子目录一个 skill）
├── agents/              # OpenClaw agent 配置
│   └── <agent-name>/
│       ├── agent/       # auth-profiles.json, models.json
│       ├── sessions/    # sessions.json（索引） + *.jsonl（会话记录）
│       └── workspace/   # 工作区文件
│           ├── memory/  # 每日记忆（markdown 文件）
│           └── ...      # 项目文件
```

## 1. Skills 批量导入

### 方法 A：目录复制（推荐，最快）

```bash
# 复制所有 OpenClaw skills 到 Hermes skills 目录
cp -r /c/Users/you/.openclaw-autoclaw/skills/* \
      /c/Users/you/AppData/Local/hermes/skills/
```

Hermes 自动检测 `skills/` 下的 SKILL.md 文件，无需手动注册。复制后执行 `/reload-skills` 或在下次会话中生效。

### 方法 B：逐个从技能市场安装

```bash
# 从 skills.sh 搜索
hermes skills search --source skills-sh <query>

# 从 skills.sh 安装
echo y | hermes skills install skills-sh/owner/repo/skill-name

# 从 ClawHub 安装
echo y | hermes skills install clawhub:skill-id

# 从 GitHub 直接安装
echo y | hermes skills install github/owner/repo/skill-name
```

## 2. Agent 数据迁移

### 复制整个 agents 目录

```bash
cp -r /c/Users/you/.openclaw-autoclaw/agents \
      /c/Users/you/AppData/Local/hermes/agents-from-openclaw
```

> **⚠️ 嵌套目录陷阱**：如果目标目录已存在，`cp -r agents/ agents-from-openclaw/` 会创建嵌套 `agents-from-openclaw/agents/`，而非覆盖。**修复**：
> 1. 先删除目标：`rm -rf /c/Users/you/AppData/Local/hermes/agents-from-openclaw`
> 2. 或复制后再清理：`rm -rf /c/Users/you/AppData/Local/hermes/agents-from-openclaw/agents`（如果内层有重复）
>
> 建议直接用 `cp -r source/* dest/` 复制内容，而非父目录。

### Agent 记忆导入

OpenClaw agent 的每日记忆存储在 `workspace/memory/*.md` 文件中（markdown 格式，文件名如 `2026-05-19.md`）。

导入到 Hermes 记忆的方法：

```bash
# 遍历所有 memory 文件，用 memory tool 逐个导入
for memfile in /c/Users/you/.../workspace/memory/*.md; do
    date=$(basename "$memfile" .md)
    content=$(cat "$memfile")
    # 通过 memory 工具添加（需在 Hermes 会话中执行）
    memory add "$date 的工作记忆: $content"
done
```

### 会话记录

OpenClaw 的 `sessions/*.jsonl` 是 JSON Lines 格式的会话记录，与 Hermes 的 SQLite state.db 格式不同，不能直接导入。**建议**：
- 保留为备份文件供查阅
- 有需要时手动提取关键信息写入 Hermes 记忆

### 认证配置

OpenClaw 的 `agent/auth-profiles.json` 包含 API Key，格式不同无法直接导入。需手动提取后写入 `~/.hermes/.env`。

### 工作区文件

`workspace/` 目录包含项目文件（storyboard、character-studio 等），复制后可作为参考但不自动集成。

## 3. 常见问题

### GitHub API 限流

```
Error: GitHub API rate limit exhausted (unauthenticated: 60 requests/hour)
```

**解决**：将 GitHub Token 写入 `.env`

方法一：直接配置
```bash
# 写入 Hermes 的 .env
# 编辑 C:\Users\you\AppData\Local\hermes\.env
# 添加: GITHUB_TOKEN=ghp_xxxxxxxxxxxx
```

方法二：从环境变量写入
```python
# Python 方式
env_path = 'C:/Users/you/AppData/Local/hermes/.env'
with open(env_path, 'r') as f:
    lines = f.readlines()
found = False
for i, line in enumerate(lines):
    if line.strip().startswith('GITHUB_TOKEN=') or line.strip().startswith('# GITHUB_TOKEN='):
        lines[i] = f'GITHUB_TOKEN={token}\n'
        found = True
if not found:
    lines.append(f'GITHUB_TOKEN={token}\n')
with open(env_path, 'w') as f:
    f.writelines(lines)
```

带上 Token 后限额提升到 **5000 次/小时**。

### 安全扫描拦截

安装 community 源 skill 时可能被 DANGEROUS  verdict 拦截：
```
Decision: BLOCKED — Blocked (community source + dangerous verdict)
```

- `<source>` 为 `builtin` / `official` 的不会被拦截
- 如需强制安装，先 `hermes config set security.tirith_enabled false` 再重试

### 网络问题（国内）

GitHub / raw.githubusercontent.com 在国内可能慢或不通：
- 使用 gh-proxy.com 镜像下载
- 优先使用官方源（`hermes skills install official/...`）而非 community 源
- ClawHub 源比 skills.sh 更稳定（GitHub 依赖少）

## 验证

```bash
# 检查新安装的 skills
hermes skills list

# 确认数量变化
hermes skills list 2>&1 | grep -c "enabled"
```
