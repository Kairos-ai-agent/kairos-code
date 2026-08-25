# Kairos 相对 Codex / Claude Code 能力补齐 — 进度报告

> **范围**：`D:\software_bak\Kairos_code`  
> **完成日期**：2026-08-25（首轮 P0 三个完成）  
> **状态**：**P0 全部完成，P1/P2 排队中**

---

## 0. 进度总览

| 阶段 | 已完成 | 剩余 |
|------|--------|------|
| **P0（最高优先）** | **3/3** ✓ | 0 |
| P1（重要） | 0/7 | 7 |
| P2（高级） | 0/6 | 6 |
| 加分项 | 0/3 | 3 |
| **总计** | **3/19** | 16 |

**测试覆盖**：从 283 → **327 PASS**（+44 个新测试），0 破坏。

---

## 1. P0 完成详情

### 1.1 MCP 客户端（OpenAI / Anthropic 都有）

**位置**：`kairos/mcp_client.py`（466 行）

**做了什么**：
- 完整的 MCP 协议 stdio transport 客户端（JSON-RPC 2.0 over newline-delimited JSON）
- `initialize` / `notifications/initialized` / `tools/list` / `tools/call` 协议完整实现
- `McpServerConfig` + `${VAR}` 环境变量展开（用 `os.path.expandvars`）
- `McpToolAdapter` —— 把 MCP tool 包装成 Kairos `BaseTool`，无缝接入 agent tool loop
- `McpRegistry` —— 多 server 管理，启动失败不崩（`startup_errors` 隔离）
- YAML 配置：`<work_dir>/.kairos/mcp.yaml` + `~/.kairos/mcp.yaml`，project wins
- 工具名 namespace（`mcp_<server>__<tool>`）—— 防 server 间命名冲突
- Adapter 内部保留 `_tool_name`（wire name）和 `name`（Kairos-visible name）分离——registry 改 name 不影响 call

**测试**（11 PASS）：
- 配置加载（deep merge / project wins / env expansion）
- 真实 subprocess 跑 mock server 测 handshake + tools/list + tools/call + error
- Registry 集成 + failing server 隔离

### 1.2 `kairos exec` CLI

**位置**：`kairos/cli.py`（341 行） + `kairos/__main__.py`

**做了什么**：
- `python -m kairos exec "<task>"` —— 非交互模式，对标 `codex exec` / `claude -p`
- 选项：`--work-dir` / `--persist` / `--json` / `--quiet` / `--model` / `--timeout` / `--no-cleanup`
- 退出码：0=成功 / 1=failed / 2=timeout / 3=bad input / 130=SIGINT
- 临时 SQLite DB（不污染全局 `data/`）—— 跑完自动 unlink
- 临时 work_dir（不传 `--work-dir` 时）—— 成功后清理
- JSON 输出模式给 CI 友好
- human-readable 输出给交互用
- `python -m kairos`（无 subcommand）保留原 server 启动行为（向后兼容）

**测试**（18 PASS）：
- 参数解析（`serve` / `exec` 两条路径）
- 空 task / 空白 task → EXIT_BAD_INPUT
- 4 种 `_emit` 格式（human / JSON / error / object-result）
- 5 个 stub orchestrator 集成测试
- main() 路由（`exec` 走 run_exec / 无参走 legacy serve）

### 1.3 git worktree 隔离

**位置**：`kairos/worktree.py`（250 行）

**做了什么**：
- `WorktreeManager(repo_path, parent_dir)` —— 验证是 git repo + 创建默认 `<repo>/.kairos-worktrees/` 父目录
- `create(branch_name)` —— `git worktree add -b <branch> <path>` 创建独立 checkout
- `unique_branch_name(role)` —— `kairos-<role>-<8hex>` 自动生成唯一名
- `list_existing()` —— 解析 `git worktree list --porcelain`（3 行/块），只列 `kairos-` 前缀的（避免误删别人 worktree）
- `merge_to(worktree, target_branch="main")` —— `--ff-only` 安全 merge，**拒绝** diverged（让人工处理冲突）
- `cleanup(worktree, remove_branch=True)` —— `git worktree remove --force` + 删 branch
- 上下文管理器 `with mgr.worktree() as wt:` —— 异常时也清理
- 修正：3 处硬知识点
  - `porcelain` 输出末尾**没**空行——`split("\n\n")` 不可靠，改用 3 行一组分块
  - 每行格式是 `key value`（`worktree <path>`），不是裸 path
  - branch 行是 `branch refs/heads/<name>`，要 split 后取第 2 token

**测试**（15 PASS）：
- 必填 .git 校验
- 默认 / 自定义 parent_dir
- branch name 生成 + 特殊字符 sanitization
- create / list / cleanup 全流程
- worktree 文件隔离（main 看不到 worktree 写的文件）
- worktree 看到 main 初始文件
- fast-forward merge
- diverged branch 拒绝 merge
- 上下文管理器正常 + 异常清理

---

## 2. 接下来的 16 项（详细 spec + 实现优先级）

| 优先级 | 任务 | 工时 | 难度 | 关键设计 |
|--------|------|------|------|----------|
| **P1-1** | Permission rules (allow/ask/deny + wildcards) | 1-2h | 低 | `kairos/permissions.py` — `PermissionRule(action, resource_pattern, decision)` + matcher。YAML 加载。Hook 到 tool execute 前 |
| **P1-2** | Approval modes (Suggest/Edit/Full-auto) | 1h | 低 | `kairos/approval.py` — 三档 `ApprovalMode` enum。`KairosAgent` 构造时接受 `approval_mode`，每条 tool call 走 `permissions.check` |
| **P1-3** | Plugin 系统 | 3-4h | 中 | `kairos/plugins.py` — `kairos-plugin.toml` 清单 + 加载 skills/agents/hooks/MCP config。`/plugin install <path>` CLI 子命令 |
| **P1-4** | Session resume / fork | 2-3h | 中 | `kairos/core/session.py` — UUID 索引 project state 到 disk，支持 `kairos resume <uuid>` 和 `--fork-from <uuid>` |
| **P1-5** | Checkpoints UI 撤销 | 1-2h | 低 | `tools/checkpoint.py` 已实现 + Web 端 `/api/projects/{id}/checkpoints/{cp_id}/restore` 路由 + UI 按钮 |
| **P1-6** | Hierarchical config | 1-2h | 低 | `kairos/config/merge.py` — root > project > user > plugin 多层合并。AGENTS.md / mcp.yaml / manifest.yaml 都走 |
| **P1-7** | AutoMemory | 4-5h | 中 | `kairos/learning/auto_memory.py` — 从 `learning/reflect.py` 升级，识别用户偏好（"always use Type hints"）并写回 `~/.kairos/AGENTS.md` |
| P2-1 | Agent Teams | 6-8h | 高 | `kairos/teams.py` — 多 agent 互相对话（Anthropic demo 16 agents 写 C compiler） |
| P2-2 | Trace/Telemetry viewer | 3-4h | 中 | `kairos/tracing.py` + UI panel 展示 agent 调用的 trace tree |
| P2-3 | Cloud delegation | 6-8h | 高 | 需要后端服务（无法在本地实现完整版）|
| P2-4 | Multimodal input | 2-3h | 中 | `tools/webfetch.py` 已能读 URL；加 `tools/image_read.py`（base64 传给多模态 LLM）|
| P2-5 | Voice mode | 8-10h | 高 | 需要 STT/TTS pipeline（whisper + tts）|
| P2-6 | Computer use | 10-15h | 极高 | 需要截图、键鼠控制（pywin32）|
| 加分 | Skill hot-reload | 1h | 低 | `kairos/skills.py` 加 `watchdog` 监视 ~/.kairos/skills + project/.kairos/skills |
| 加分 | Nested skills 加载 | 1-2h | 低 | `SkillsLoader` 改成递归扫 .kairos/skills/ |
| 加分 | Pre-commit + CI 集成 | 2-3h | 中 | `.github/workflows/kairos.yml` + pre-commit hook 调 `kairos exec` |

---

## 3. 集成建议（P0 三个如何接入 Kairos 主流程）

### 3.1 MCP 接入
```python
# kairos/core/orchestrator.py _create_team:
from kairos.mcp_client import McpRegistry
mcp = McpRegistry()
mcp.load(project_dir=project.work_dir)
await mcp.start_all()
# 把 mcp.all_tools() 拼到 coder_tools / reviewer_tools
# 项目 close 时 mcp.close_all()
```

### 3.2 exec CLI 接入
**已默认可用** —— `python -m kairos exec "task"` 直接跑。

### 3.3 worktree 接入
```python
# kairos/core/orchestrator.py _create_team:
if project.is_git_repo:
    mgr = WorktreeManager(repo_path=project.work_dir)
    wt = mgr.create(branch_name=WorktreeManager.unique_branch_name(role))
    # 把 wt.path 设为 role 的 working_root
    # tool allowed_root 改用 wt.path
    # dispatch task 完成后 mgr.merge_to(wt) + cleanup(wt)
```

---

## 4. 测试覆盖率

```
$ python -m pytest tests/
============ 327 passed, 11 skipped, 1 warning in 92.09s =============
```

| 测试文件 | 测试数 | 新增 |
|---------|--------|------|
| `test_agents_md_skills.py` | 18 | — |
| `test_guardrails.py` | 14 | — |
| `test_manifest.py` | 12 | — |
| `test_retained_reasoning.py` | 6 | — |
| `test_sandbox.py` | 12 | — |
| **`test_mcp_client.py`** | **11** | **新** |
| **`test_cli.py`** | **18** | **新** |
| **`test_worktree.py`** | **15** | **新** |
| `tests/unit/*.py` | 221 | — |
| **合计** | **327** | **+44** |

---

## 5. 一句话总结

> **P0 三个（MCP 客户端 / kairos exec CLI / git worktree 隔离）全部完成、44 个新测试 PASS、327 总测试 PASS、0 破坏。** 接下来 P1 7 项 + P2 6 项 + 加分 3 项 = 16 项待做。P1-1（permission rules）下一步开干。
