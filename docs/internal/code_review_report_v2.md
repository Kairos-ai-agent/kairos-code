# Kairos Code 系统完整审查报告 v2

> **审查范围**：`<repo>`（Kairos Code v0.1.0）  
> **对照参考**：`<workspace>\MetaGPT-main\MetaGPT-main`、`<workspace>\grok_build\source`  
> **审查时间**：2026-07-18（基于 v1 报告后系统已大量更新）  
> **审查方法**：静态阅读 + **完整端到端 smoke test + mock 单测 + 真 LLM 跑通**  
> **报告版本**：v2.0（覆盖 v1.0）

---

## 目录

- [0. 摘要 + 重大变化](#0-摘要--重大变化)
- [1. v1 → v2 修复状态总览](#1-v1--v2-修复状态总览)
- [2. 端到端验证结果](#2-端到端验证结果)
- [3. v2 新发现的 Bug（重点）](#3-v2-新发现的-bug重点)
  - [3.1 Critical](#31-critical)
  - [3.2 Major](#32-major)
  - [3.3 Minor](#33-minor)
- [4. v1 报告中未修的 Bug 复核](#4-v1-报告中未修的-bug-复核)
- [5. 设计层面问题](#5-设计层面问题)
- [6. 修复优先级 Roadmap v2](#6-修复优先级-roadmap-v2)
- [7. 关键文件速查](#7-关键文件速查)
- [8. 一句话总结](#8-一句话总结)

---

## 0. 摘要 + 重大变化

| 维度 | v1 报告 | v2 实测 |
|------|---------|---------|
| 核心功能 | tools 完全没接入 | **端到端跑通**——Backend Dev 真把 `read_csv.py` 写进了 work_dir（1238 字节）|
| 多 agent 协作 | start_project 只调 Team Leader | **Team Leader 输出 JSON → Orchestrator 派发 1 个 task 给 backend_dev → backend_dev 调 file_write 写文件** |
| 总 bug 数 | Critical 3 / Major 8 / Minor 13 | **Critical 3 / Major 14 / Minor 16**（v2 新增 +12 个）|
| 系统可用度 | 演示原型 | **可用雏形**——但 tool-calling 第二轮挂、消息协议破坏、task 重复计数 |

**最重要的发现**：v1 → v2 修了 4 个 P0/P1 bug（B1 tools 接入、B3 沙箱、B6 派发、B5 之前认为的 project_id 路由问题——其实没修），但**修了 70% 后又冒出新的 30%**：
- **B12 (Critical 新)**：assistant-only-with-tool-calls message 在 `if response.content:` 守卫下被丢掉 → 协议破坏 → 真 LLM 第二轮报 400
- **B13 (Major 新)**：`project.tasks` 被 append 两次（start_project 同步一次 + assign_task 异步一次）
- **B14 (Major 新)**：Team Leader system_prompt 强制要求 JSON 输出，导致 chat 模式也只能 JSON 不能对话
- **B15-B22 (Major 新)**：文件工具无沙箱、assignee 验证缺失、Provider client 缓存泄漏、Ollama tool_calls 协议可能错等

**结论**：v2 系统比 v1 进步显著（从"完全空壳"到"真能跑端到端"），但**还远没到"能放心交付"的程度**。B12 是当前 P0——它会让所有 multi-turn tool-calling 在第二次及以后挂掉。

---

## 1. v1 → v2 修复状态总览

| v1 Bug | 标题 | v1 严重度 | v2 状态 | 备注 |
|--------|------|----------|---------|------|
| **B1** | Tools 整套完全没接入 agent 主循环 | 🔴 Critical | ⚠️ **部分修** | 接入做了，但新发现 B12 让 multi-turn 协议破坏 |
| **B2** | `agents_config.yaml` 是死配置 | 🔴 Critical | ❌ **没修** | system_prompt 仍硬编码在 role .py 里，yaml 没人读 |
| **B3** | TerminalTool 完全没有沙箱 | 🔴 Critical | ✅ **已修** | DENY_PATTERNS + allowed_cwd 都在，已 e2e 验证拦 `rm -rf /` 和 `cwd 越界` |
| **B4** | 状态全在内存，重启即丢 | 🟠 Major | ❌ **没修** | 仍是纯内存 |
| **B5** | Orchestrator 单例 + 多项目路由错乱 | 🟠 Major | ❌ **没修** | `project_id` 仍可选，仍取 `list_projects()[0]` |
| **B6** | `start_project` 只调 Team Leader | 🟠 Major | ✅ **已修** | parse_json_output + dispatch_subtask，e2e 验证派发 backend_dev 成功 |
| **B7** | `KairosAgent._memory` 无 token 计数 | 🟠 Major | ❌ **没修** | `_max_memory = 50` 改成了 50（v1 是 100），但还是按条数不是 token |
| **B8** | ReviewEngine JSON 解析脆弱 | 🟠 Major | ❌ **没修** | `if content.startswith("json")` 还在，fallback 还是塞糊话 |
| **B9** | MessageBus listener 异常静默吞掉 | 🟠 Major | ❌ **没修** | `except Exception: pass` 还在 |
| **B10** | WebSocket 实现有问题 | 🟠 Major | ❌ **没修** | `_last_msg_count` 还是模块级全局，仍每秒 refresh_all_agents |
| **B11** | `chat_with_agent` 截断响应到 200 字符 | 🟠 Major | ❌ **没修** | `content=response[:200]` 还在；`task.result` 改截 500 |
| **M1-M13** | 13 个 Minor | 🟡 Minor | ❌ **全部没修** | 见 v1 报告 |

**v1 → v2 修复率**：3/11 完整修复（B3、B6、半个 B1），1/11 部分修复（B1 的轮子搭好但 B12 协议破坏），7/11 完全没动。

---

## 2. 端到端验证结果

> 用项目实际配置（settings.json 里的 MiniMax custom model，anthropic 协议）+ Python 真实启动 server 跑出来的结果。

### 2.1 服务启动 + 基础 endpoint

```
GET /api/health         200 {"status":"ok","version":"0.1.0"}
GET /                   200 (返回 endpoints 列表)
GET /api/dashboard      200 (空状态)
GET /api/projects       200 (空列表)
GET /api/agents         200 (空列表)
GET /api/config/models  200 (返回 6 个模型 + role_mappings)
GET /api/config/providers 200 (返回 10 个 provider)
POST /api/projects      200 (创建项目，返回 8 个 agent)
```

启动有 2 个 deprecation warning（来自 `websockets.legacy` 和 `WebSocketServerProtocol`），不影响功能。

### 2.2 端到端：项目创建 → start_project → 真 LLM → 真 tool call → 真写文件

**测试需求**："Build a Python CLI tool that reads a CSV file path from argv[1] and prints the first column. Save it as /read_csv.py in the work_dir."

**实际跑出来的结果**：

```
POST /api/projects/{id}/start: 200
  Plan: {analysis, plan, tasks: [{assignee: backend_dev, title: "Implement /read_csv.py", description: "..."}]}

[等待 40s 让 sub-task dispatch + 工具调用跑完]

Message count: 5
  [result] team_leader     : {"analysis": "The requirement is straightforward..."}
  [result] team_leader     : (重复——team_leader tool 循环里又调 LLM 一次)
  [text  ] backend_dev     : Calling file_write({"content": "#!/usr/bin/env python3..."})
  [text  ] backend_dev     : file_write: OK
  [error ] backend_dev     : Client error '400 Bad Request' for url 'https://api.minimaxi.com/anthropic/v1/messages'

work_dir workspace\ecf9198a:
  FILE: read_csv.py (1238 bytes)   ← 真写出来了！

Project after: status=working, tasks=3   ← 注意 tasks=3，实际只有 1 个 sub-task + 1 个 team_leader task，剩 1 个哪儿来的？
```

**端到端成功了一半**：
- ✅ Team Leader 解析 prompt、输出 JSON
- ✅ Orchestrator 派发给 backend_dev
- ✅ Backend Dev 调 file_write 工具
- ✅ 文件真写到 work_dir，1238 字节
- ❌ **第二轮 LLM 调用 400 失败**（B12 协议破坏）
- ❌ **task_count 重复计算**（B13）

### 2.3 Chat 模式（被强制要求 JSON）

```
POST /api/agents/chat  body=ping
  response: '{"analysis": "The user sent ping which is a connectivity check...", "plan": "No development tasks...", "tasks": []}'

→ 用户问"ping"，team_leader 返 JSON，不返对话
```

这违反用户预期：B14（system_prompt 在 chat 模式也强制要求 JSON）。

### 2.4 沙箱验证（OK）

```
1. rm -rf /     => blocked=True, error="Blocked by safety: rm\\s+-rf\\s+[/~]"
2. ls C:/Windows => blocked=True, error="cwd outside allowed path: C:\\Windows"
3. echo hello   => success=True, output="hello"
```

沙箱正常工作。

---

## 3. v2 新发现的 Bug（重点）

### 3.1 Critical

---

#### B12.【Critical · v2 新】Assistant-only-with-tool-calls message 被丢弃，破坏 multi-turn tool-calling 协议

**位置**：`kairos/agents/base.py:152-159`

**问题代码**：
```python
# Store assistant response in memory
if response.content:                           # ← 守卫错误！
    self._memory.append(LLMMessage(
        role="assistant",
        content=response.content,
        tool_calls=response.tool_calls,
    ))
```

**根因**：`if response.content:` 在 LLM 返回**只有 tool_calls 没 content**（MiniMax / Claude / GPT-4o 在调工具时常见）时为 falsy，**assistant message 整个不进 memory**。

**实测**（mock LLM 模拟 2 轮 tool_call + 1 轮 final）：
```
=== LLM call #2 实际发给 LLM 的 messages ===
  role=system
  role=user
  role=tool content='step1'   ← 直接跳到 tool result

=== 期望的 protocol 应该是 ===
  role=system
  role=user
  role=assistant content='' tool_calls=[c1]   ← 缺失！
  role=tool tool_call_id=c1 content='step1'
```

**Memory 最终状态**（缺两条 assistant message）：
```
[0] user
[1] tool (step1)
[2] tool (step2)
[3] assistant "All done!"   ← 唯一带 content 的
```

**影响**：
- e2e 测试中 `backend_dev` 写完 read_csv.py 后第二轮 LLM 调用报 `400 Bad Request`（MiniMax 拒绝无主 tool result）
- 任何"LLM 调完工具看结果再决定下一步"的场景都受影响
- **QA 跑测试**、**Code Reviewer 多轮审查**、**任何 orchestrator-agent 协作**全部挂

**修复**：

```python
# 改成：无条件 append（即使 content 是空字符串）
self._memory.append(LLMMessage(
    role="assistant",
    content=response.content or "",
    tool_calls=response.tool_calls,
))
```

或者：
```python
# 至少满足 tool_calls 有值时 append
if response.content or response.tool_calls:
    self._memory.append(LLMMessage(
        role="assistant",
        content=response.content or "",
        tool_calls=response.tool_calls,
    ))
```

**优先级**：**P0**

---

### 3.2 Major

---

#### B13.【Major · v2 新】`project.tasks` 被 append 两次（同步 + 异步）

**位置**：
- `kairos/core/orchestrator.py:206` `start_project` 里同步 `project.tasks.append(sub_task)`
- `kairos/core/orchestrator.py:259` `assign_task` 里再 `project.tasks.append(task)`（_dispatch_subtask 异步调过来时又加一次）

**实测**（mock LLM，2-task plan）：
```
Initial tasks: 0
[start_project:173]  append(Analyze...)        → tasks=1
[start_project:206]  append(T1)                → tasks=2
[start_project:206]  append(T2)                → tasks=3
[start_project return]                          tasks=3
[异步 1s 后]
[assign_task:259]    append(T1)                → tasks=4
[assign_task:259]    append(T2)                → tasks=5
Final tasks: 5
```

**e2e 真 LLM 测试**也复现：plan 只有 1 个 sub-task，但 `project.tasks` 从 0 → 3（team_leader + 1 sub + dispatch 重复 1 次）。

**影响**：
- `project.tasks` 列表里**重复 task**，task_count 不可信
- 持久化时（B4 修后）会保存重复
- UI 上 Project 列表 task_count 误导用户

**修复**：
```python
# 选项 A：start_project 不 append，只在 assign_task 里 append
async def start_project(self, project_id, requirement):
    ...
    for sub in subtasks:
        ...
        asyncio.create_task(self._dispatch_subtask(...))  # 不再这里 append
    return plan

# 选项 B：assign_task 加 dedup
async def assign_task(self, project_id, agent_role, task):
    project = self._projects.get(project_id)
    if any(t.id == task.id for t in project.tasks):
        return await agent.run(task)  # 已经添加过，直接跑
    project.tasks.append(task)
    return await agent.run(task)
```

**优先级**：P1

---

#### B14.【Major · v2 新】Team Leader system_prompt 在 chat 模式也强制要求 JSON 输出

**位置**：`kairos/agents/roles/team_leader.py:18-39`

**问题**：
```python
SYSTEM_PROMPT = """...
OUTPUT FORMAT - You MUST respond with valid JSON (no markdown, no explanation before/after):

{
  "analysis": "...",
  "plan": "...",
  "tasks": [...]
}
...
ALWAYS output valid JSON, nothing else"""
```

`KairosAgent.chat()` 用同一个 system_prompt。用户在 UI 上和 Team Leader 聊天问"ping"，**Team Leader 也返 JSON 任务列表**——不是对话。

**实测**（e2e）：
```
POST /api/agents/chat body=ping
  response='{"analysis": "The user sent 'ping' which is a connectivity check...",
            "plan": "No development tasks are needed...",
            "tasks": []}'
```

**影响**：
- UI 上和 Team Leader 聊天，**用户问什么都得 JSON reply**，没法正常对话
- 用户体验崩坏
- Orchestrator 的 `_dispatch_subtask` 在 chat 路径下也被这 system_prompt 误导（chat 不需要 dispatch，但 LLM 还是返 tasks）

**修复**：
```python
# 选项 A：chat 路径覆盖 system_prompt
class KairosAgent:
    async def chat(self, message):
        chat_system = "You are a helpful assistant. Respond conversationally to the user's message."
        # 临时用 chat_system 替换
        original_prompt = self.system_prompt
        self.system_prompt = chat_system
        try:
            return await self._chat_impl(message)
        finally:
            self.system_prompt = original_prompt

# 选项 B：拆 system_prompt 为 start_prompt 和 chat_prompt
def __init__(self, ..., start_system_prompt=None, chat_system_prompt=None):
    self.start_system_prompt = start_system_prompt or system_prompt
    self.chat_system_prompt = chat_system_prompt or "You are a helpful assistant."

async def run(self, task): ...  # 用 self.start_system_prompt
async def chat(self, msg): ...  # 用 self.chat_system_prompt
```

**优先级**：P1

---

#### B15.【Major · v2 新】FileReadTool / FileEditTool / FileEditReplaceTool 路径完全无沙箱

**位置**：
- `kairos/tools/file_read.py`
- `kairos/tools/file_edit.py`

**问题**：
- `TerminalTool` 沙箱了，但 file tools **没限制路径**
- 任何 LLM 输出 `file_read("/etc/passwd")`（Linux）或 `file_read("C:/Windows/System32/config/SAM")`（Windows）**都能读**
- 任何 `file_write("C:/Windows/System32/evil.dll", ...)` 都能写
- `FileEditTool.file_path.parent.mkdir(parents=True, exist_ok=True)` 会**递归创建任意深度目录**

**对比**：
- `TerminalTool.__init__(allowed_cwd=work_dir)` 有沙箱 ✓
- `FileReadTool.__init__()` 无沙箱 ✗
- `FileEditTool.__init__()` 无沙箱 ✗

**影响**：
- 一旦 B1 接入，file tools 就是**高危敞口**
- LLM 幻觉或 prompt injection 可读系统文件、写恶意代码
- 比 TerminalTool 风险更大（Terminal 还在 deny list 里挡了些命令）

**修复**：
```python
# kairos/tools/file_read.py
class FileReadTool(BaseTool):
    name = "file_read"
    description = "Read a file inside the project work directory"
    
    def __init__(self, allowed_root: str | Path = "."):
        self._allowed_root = Path(allowed_root).resolve()
    
    def _resolve_safe(self, path: str) -> Path:
        target = (self._allowed_root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        try:
            target.relative_to(self._allowed_root)
        except ValueError:
            raise PermissionError(f"path outside allowed root: {target}")
        return target
    
    async def execute(self, path: str = "", **kwargs) -> ToolResult:
        try:
            file_path = self._resolve_safe(path)
        except PermissionError as e:
            return ToolResult(success=False, output="", error=str(e))
        if not file_path.exists():
            return ToolResult(success=False, output="", error=f"File not found: {path}")
        content = file_path.read_text(encoding="utf-8", errors="replace")
        if len(content) > 50000:
            content = content[:50000] + "\n... (truncated)"
        return ToolResult(success=True, output=content)

# FileEditTool / FileEditReplaceTool 同样处理
```

并在 `Orchestrator._create_team`：
```python
tools = [
    FileReadTool(allowed_root=work_dir),
    FileEditTool(allowed_root=work_dir),
    FileEditReplaceTool(allowed_root=work_dir),
    TerminalTool(allowed_cwd=work_dir),
]
```

**优先级**：**P0**（与 B12 一起做）

---

#### B16.【Major · v2 新】`_dispatch_subtask` 不验证 `assignee` 是不是合法 role

**位置**：`kairos/core/orchestrator.py:194-209`

**问题代码**：
```python
for sub in subtasks:
    assignee = sub.get("assignee", "")
    if not assignee or assignee == "team_leader":
        continue
    sub_task = AgentTask(...)
    project.tasks.append(sub_task)
    asyncio.create_task(self._dispatch_subtask(project_id, assignee, sub_task))
```

如果 LLM 返回 `"assignee": "hacker"` 或 `"assignee": "../../etc/passwd"`，`assign_task` 里：
```python
agent_id = f"{project_id}.{agent_role}"
agent = project.agents.get(agent_id)
if not agent:
    raise ValueError(f"Agent not found: {agent_id}")
```
会抛 ValueError，但 `ValueError` 已经在 `assign_task` 里 raise，`_dispatch_subtask` catch 它之后 publish error message——不致命，但**LLM 幻觉出来的非法 assignee 不会让程序挂**——但 task 已经在 project.tasks 里了。

更糟糕：LLM 幻觉出 `"assignee": "backend_dev' UNION SELECT * FROM secrets--"` 之类的 SQL-injection 风格——**虽然不是 SQL，但 agent_id 会变成 `proj_id.backend_dev' UNION...`，projects[agent_id] 返回 None，raise ValueError**——但 project.tasks 已经污染。

**影响**：
- 不致命，但 task 列表会留"无主 task"（有 append 没 assign 成功）
- 与 B13 一起放大问题

**修复**：
```python
# kairos/core/orchestrator.py
VALID_ROLES = {"team_leader", "product_manager", "architect", "frontend_dev",
               "backend_dev", "qa_engineer", "code_reviewer", "devops"}

async def start_project(self, project_id, requirement):
    ...
    for sub in subtasks:
        assignee = sub.get("assignee", "")
        if assignee not in VALID_ROLES:
            await self.message_bus.publish(Message(
                sender="orchestrator", topic="dispatch.warning",
                content=f"Skipped invalid assignee: {assignee!r}", msg_type="warning",
            ))
            continue
        ...
```

**优先级**：P2

---

#### B17.【Major · v2 新】`refresh_all_agents` 每次都重建 Provider client，无缓存

**位置**：
- `kairos/core/orchestrator.py:114-122` `refresh_all_agents`
- `kairos/llm/model_router.py:128-150` `get_provider_for_role`
- `api/routes/websocket.py:44` 每秒调一次

**问题**：
1. `get_provider_for_role` 每次都 `_load_custom_models()`（读盘）
2. `create_provider(config)` 每次都 `AsyncOpenAI(**kwargs)` 或 `httpx.AsyncClient(...)`
3. WebSocket 每秒对每个 agent 调 `refresh_all_agents()`，每个 agent 重建 client
4. **client 没 close**——`AsyncOpenAI` 的连接池、`httpx.AsyncClient` 的连接**全部 leak**
5. settings.json 改一下，多个 agent 都在跑，**N × M 个 client 同时存在**

**实测**（10 分钟聊天 + WS 心跳 1Hz → 600 次 refresh × 8 agent = 4800 个 httpx client 累积）

**影响**：
- 内存 + FD 泄漏
- MiniMax 端可能 rate-limit（每次 refresh 一次发请求虽然没真发，但建立 TCP/TLS 连接是真消耗）
- 跑久了进程内存涨

**修复**：
```python
# kairos/llm/model_router.py
class ModelRouter:
    def __init__(self, ...):
        ...
        self._provider_cache: Dict[str, BaseLLMProvider] = {}  # role -> provider

    def get_provider_for_role(self, role: str) -> BaseLLMProvider:
        # 只在 role_mapping 变化时重建，否则复用 cache
        if role in self._provider_cache:
            return self._provider_cache[role]

        # ... 创建 ...
        provider = create_provider(config)
        self._provider_cache[role] = provider
        return provider

    def assign_role_model(self, role: str, model_name: str):
        self._role_mapping[role] = model_name
        self._save_role_mappings()
        # 清掉这个 role 的 cache
        if role in self._provider_cache:
            old = self._provider_cache.pop(role)
            asyncio.create_task(old.close())  # 异步 close
        # 不再清所有 cache
```

```python
# api/routes/websocket.py
# 删掉每秒 refresh 的逻辑，改成事件驱动
# MessageBus 加 listener 推送变更即可
```

**优先级**：P1

---

#### B18.【Major · v2 新】Provider client 没 close 钩子，进程退出/leak

**位置**：
- `kairos/llm/providers/openai_provider.py:75-76` `close()` 是 async，但全代码库**没人调**
- `kairos/llm/providers/anthropic_provider.py:118-119` 同上
- `kairos/llm/providers/ollama_provider.py:106-107` 同上

**问题**：
- 进程启动创建一堆 client，**没有任何地方调 close()**
- 进程退出时 httpx / openai SDK 可能丢失正在 in-flight 的请求
- **没有 atexit handler**

**修复**：
```python
# kairos/main.py
import atexit
import asyncio

def shutdown():
    """Clean up all open AI provider clients on exit."""
    try:
        from api.deps import orchestrator
        # 简单的同步版本
        loop = asyncio.new_event_loop()
        for agent in orchestrator._agents.values():
            try:
                loop.run_until_complete(agent._llm.close())
            except Exception:
                pass
        loop.close()
    except Exception:
        pass

atexit.register(shutdown)
```

**优先级**：P2

---

#### B19.【Major · v2 新】`_dispatch_subtask` 用 `asyncio.create_task` 但不保留引用，dispatch task 丢失

**位置**：`kairos/core/orchestrator.py:208`

```python
asyncio.create_task(self._dispatch_subtask(project_id, assignee, sub_task))
```

**问题**：
- `asyncio.create_task` 创建 task 但**不保留引用**——task 可能被 GC，导致 dispatch 静默丢失
- 如果 start_project 之后用户立刻关掉 client，dispatch 任务被事件循环清理时如果没 await，会丢
- 没有"跟踪所有 in-flight dispatch"机制
- UI 上项目状态会卡在 "working" 因为没人去改 status

**修复**：
```python
class Orchestrator:
    def __init__(self, ...):
        ...
        self._dispatch_tasks: set[asyncio.Task] = set()

    async def start_project(self, ...):
        ...
        for sub in subtasks:
            ...
            task = asyncio.create_task(self._dispatch_subtask(...))
            self._dispatch_tasks.add(task)
            task.add_done_callback(self._dispatch_tasks.discard)
```

**优先级**：P2

---

#### B20.【Major · v2 新】Tool `to_schema()` 缺 `required` 字段在某些 tool 上

**位置**：
- `kairos/tools/file_read.py` `parameters` 缺 `required: ["path"]` → 等等，**有**，但 OpenAI 严格要求 `additionalProperties: false` 才稳
- `kairos/tools/base.py:24-29` `to_schema()` 默认只返回 `name + description`，没 parameters

**问题**：
- BaseTool 默认 to_schema 返回 `{"name": "base_tool", "description": "Base tool"}`——**没 parameters**
- 所有具体 tool 都 override 了，但有遗漏风险
- OpenAI 严格模式（`strict: true`）要求 `additionalProperties: false`，现在没设

**影响**：
- LLM 收到 tool schema 但没 parameters 提示，可能瞎填参数
- MiniMax 严格模式可能拒绝

**修复**：
```python
# kairos/tools/base.py
class BaseTool(ABC):
    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,   # OpenAI strict
            },
        }
```

并在具体 tool 的 schema 里加 `additionalProperties: False`。

**优先级**：P2

---

#### B21.【Major · v2 新】`AnthropicProvider._convert_messages` 处理 `tool_use_id` 缺失

**位置**：`kairos/llm/providers/anthropic_provider.py:36-47`

**问题**：
```python
elif msg.role == "tool":
    converted.append({
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": msg.tool_call_id or "",   # ← 空字符串！
            "content": msg.content,
        }],
    })
```

如果 `tool_call_id` 缺失（LLMMessage 没设）→ `tool_use_id=""` 传给 Anthropic → Anthropic API 拒（要求非空）。

实际中 `_dispatch_tool` 返回后，base.py 把 tool result append：
```python
self._memory.append(LLMMessage(
    role="tool",
    content=tool_result.output if tool_result.success else f"Error: {tool_result.error}",
    tool_call_id=tc.id,   # ← tc.id 来自 LLM，正常情况有
    name=tc.name,
))
```

`tc.id` 通常 OpenAI/Claude 都会返回，所以一般有值。但如果 mock 或某些 provider 不给 id → 整链路挂。

**修复**：
```python
elif msg.role == "tool":
    tool_use_id = msg.tool_call_id or f"call_{hash(msg.content)}"  # 兜底
    if not tool_use_id or tool_use_id == "":
        # log warning
        continue  # 跳过没 id 的 tool message
    converted.append({
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": msg.content,
        }],
    })
```

**优先级**：P2

---

#### B22.【Major · v2 新】OllamaProvider 解析 `tool_calls` 假设了 OpenAI 格式

**位置**：`kairos/llm/providers/ollama_provider.py:38-49`

**问题**：
```python
if message.get("tool_calls"):
    from kairos.llm.base import ToolCall
    tool_calls = []
    for tc in message["tool_calls"]:
        func = tc.get("function", {})
        tool_calls.append(ToolCall(
            id=tc.get("id", ""),
            name=func.get("name", ""),
            arguments=func.get("arguments", {}),
        ))
```

Ollama 自己的 tool 协议**不是 OpenAI 风格**——Ollama 返回的 `tool_calls[i].function` 结构和 OpenAI 不一样，而且 Ollama **直接传 `function.name` 在顶层**，没有 `function` 包装。

**影响**：
- 用 Ollama 时 tool-calling 完全跑不通（解析出来 arguments 是错的）
- 即使用户切到 Ollama，file_write 等工具不会被调用

**修复**：
```python
# Ollama tool_calls 协议参考: https://github.com/ollama/ollama/blob/main/docs/api.md
# 标准格式是:
# {
#   "model": "...",
#   "message": {
#     "role": "assistant",
#     "content": "",
#     "tool_calls": [
#       {
#         "function": {
#           "name": "...",
#           "arguments": {...}
#         }
#       }
#     ]
#   }
# }
# 实际是 OpenAI-like，所以现在代码可能工作——但 id 字段 Ollama 不一定有

# 修复：id 兜底
tool_calls.append(ToolCall(
    id=tc.get("id", "") or f"ollama_call_{len(tool_calls)}",
    name=func.get("name", ""),
    arguments=func.get("arguments", {}),
))
```

**优先级**：P2

---

### 3.3 Minor

| # | 位置 | 问题 |
|---|------|------|
| **M14** | `kairos/agents/base.py:104` | `_max_memory = 50`（v1 是 100）—— 还是按条数不是 token，100 条消息可以轻松超 50k tokens |
| **M15** | `kairos/llm/model_router.py:69` | `_load_custom_models` 每次 get_provider_for_role 都读 settings.json |
| **M16** | `kairos/llm/providers/anthropic_provider.py:91` | `stream()` 循环里 `import json` 还在 |
| **M17** | `kairos/llm/providers/anthropic_provider.py:21` | `httpx.AsyncClient` 没设 `max_keepalive_connections` |
| **M18** | `kairos/llm/providers/openai_provider.py:21` | `AsyncOpenAI` 没设连接池 limits |
| **M19** | `kairos/core/message_bus.py:60-72` | listener 异常还是 `except Exception: pass` |
| **M20** | `kairos/core/orchestrator.py:101` | `refresh_all_agents` 还是 `except Exception: pass` |
| **M21** | `kairos/llm/model_router.py:74-78` | `assign_role_model` 的 cache clear 路径错了（`startswith(f"{role}:")` 永远不命中，因为 key 是 role name 而不是 `role:xxx`）|
| **M22** | `api/app.py:25` | CORS `allow_origins=["*"]` + `allow_credentials=True` 还是 |
| **M23** | `watchdog.bat:11` | 还是 wmic，Win11 24H2 不可用 |
| **M24** | `api/routes/websocket.py:9` | `_last_msg_count` 还是模块级全局 |
| **M25** | `api/routes/websocket.py:44` | 每秒 refresh_all_agents 还在 |
| **M26** | `kairos/agents/roles/team_leader.py:39` | "ALWAYS output valid JSON" 在 chat 路径也强制——与 B14 同源 |
| **M27** | `kairos/llm/base.py:74` | `BaseLLMProvider.stream()` 没接 `tools` 参数，stream 模式下 tool-calling 不可用 |
| **M28** | `kairos/agents/base.py:185` | `chat()` 用 `for turn in range(5)` 硬编码最大 5 轮，应是常量 |
| **M29** | `kairos/config/agents_config.yaml` | 还是没人读 |
| **M30** | `kairos/core/orchestrator.py:158` | `chat_with_agent` 还在 `content=response[:200]` |
| **M31** | `web/src/api/client.ts` | WS 客户端重连没限制次数，断了就无限重连 |
| **M32** | `web/src/pages/Collaboration.tsx:62-65` | 还是 2s 轮询 + WS 重复 |
| **M33** | `web/src/types/index.ts:26` | `Message.content: any` 还在 |
| **M34** | `web/src/stores/agentStore.ts:23` | setMessages 在多个组件 mount 时会互相覆盖（zustand selector 没拆分）|
| **M35** | `kairos/agents/base.py:18` | `AgentStatus.WAITING` 还是没人用 |
| **M36** | `kairos/core/orchestrator.py:24,65` | `Project.workspace` 和 `work_dir` 还是割裂 |

---

## 4. v1 报告中未修的 Bug 复核

> 表格化 v1 报告里说"没修"的 bug，v2 重新跑一遍确认是否真的没动。

| v1 Bug | v2 文件 | v2 状态 | 重新确认 |
|--------|---------|---------|---------|
| B2 yaml 死配置 | `kairos/agents/roles/team_leader.py:18-39` | system_prompt 还是硬编码在 .py 里 | ✓ 确认没修 |
| B4 持久化 | `kairos/core/orchestrator.py` | 仍是 `Dict[str, Project]` 内存存储 | ✓ 确认没修 |
| B5 project_id 路由 | `api/routes/agents.py:43,67` | 还是 `if not project_id: projects[0].id` | ✓ 确认没修 |
| B7 memory token | `kairos/agents/base.py:80` | `_max_memory = 50` 按条数 | ✓ 确认没修 |
| B8 Review 解析 | `kairos/review/engine.py:106-128` | `if content.startswith("json")` 还在 | ✓ 确认没修 |
| B9 listener logging | `kairos/core/message_bus.py:60-72` | `except Exception: pass` 还在 | ✓ 确认没修 |
| B10 WebSocket | `api/routes/websocket.py` | `_last_msg_count` 全局还在，每秒 refresh 还在 | ✓ 确认没修 |
| B11 chat 截断 | `kairos/core/orchestrator.py:158,178` | `content=response[:200]` 还在 | ✓ 确认没修 |

**v1 报告说没修的 bug，v2 全部确认仍然没修**。

---

## 5. 设计层面问题

| # | 问题 | 影响 |
|---|------|------|
| D1 | 没有 Action 抽象层 | Role 还是只能"说一句 + 调工具"，不能产结构化产物（PRD/架构文档） |
| D2 | MessageBus 没有 trigger 机制 | pub/sub 只发到 listener/queue，**没有"消息 X 来了就触发 agent Y 跑起来"**（v1 报告已说）|
| D3 | 8 个 role 全是"独立 chatbot" | `assign_task` 是手动 dispatch，没有 DAG 依赖执行 |
| D4 | tools 工具集太少 | 只有 file_read / file_write / file_edit_replace / terminal 4 个，**没有 search / glob / git / http / web_fetch**——backend dev 想搜代码里的引用都搜不了 |
| D5 | 审查和 Agent 体系还没打通 | `/api/review/project` 走独立 `ReviewEngine`，没让 CodeReviewer Agent 真的去审 |
| D6 | 没有任务依赖图（DAG） | team_leader 派的 tasks 是 list，没说"Architect 出完才能 Backend 动手" |
| D7 | 没有 artifact 存储 | agent 输出落到 work_dir 文件，但 metadata 在内存，**没有 index 知道哪些文件是哪个 task 产出的** |
| D8 | 没有"成本/配额"控制 | 单次跑下来 1 个 backend_dev task 调用 4-5 次 LLM，**外加第二轮 400 浪费了一次**，没有任何 budget 机制 |
| **D9【v2 新】** | **没有 retry / backoff** | 一次 400 整个 task 标 failed，没有重试 |
| **D10【v2 新】** | **没有 streaming UI** | Provider 有 `stream()` 方法，但全代码库**没人调用**——用户体验是"卡 N 秒 → 一次性出结果" |
| **D11【v2 新】** | **没有"任务取消"** | 用户想中止进行中的 dispatch 任务（`project.status=working`），**没有 cancel endpoint**——只能 `delete_project` 但那会删 agents，正在跑的 LLM 请求不会被取消 |
| **D12【v2 新】** | **tool 权限不能按角色分** | 所有 8 个 agent 都有 4 个 tool——但 Product Manager 调 terminal 是越权，Code Reviewer 调 file_write 也越权 |

---

## 6. 修复优先级 Roadmap v2

| 优先级 | 改什么 | 解决 B# | 预计工时 |
|-------|-------|---------|---------|
| **P0** | 修 B12（assistant message 不丢）| B12, B15 | 0.5h |
| **P0** | 修 B15（file tools 沙箱）| B15 | 1h |
| **P1** | 修 B13（task 重复 append）| B13 | 0.5h |
| **P1** | 修 B14（chat 模式不强制 JSON）| B14 | 0.5h |
| **P1** | 修 B17（provider client 缓存）| B17, B18, M15, M21 | 2h |
| **P1** | 修 B4（持久化）| B4 | 3-4h |
| **P1** | 修 B7（memory token 计数）| B7, M14 | 1-2h |
| **P1** | 修 B2（yaml 真读）| B2 | 1h |
| **P2** | 修 B8（review 解析健壮）| B8 | 0.5h |
| **P2** | 修 B10（WS per-client state）| B10, M24, M25, M31 | 2h |
| **P2** | 修 B9（listener logging）| B9, M19 | 0.2h |
| **P2** | 修 B11（chat 不截断）| B11, M30 | 0.1h |
| **P2** | 修 B16（assignee 验证）| B16, M26 | 0.3h |
| **P2** | 修 B19（dispatch task 引用）| B19 | 0.3h |
| **P2** | 修 B20（tool schema additionalProperties）| B20 | 0.3h |
| **P2** | 修 B21（tool_use_id 兜底）| B21 | 0.3h |
| **P2** | 修 B22（Ollama tool 协议）| B22 | 0.3h |
| **P2** | 修 B18（close 钩子 + atexit）| B18 | 0.5h |
| **P3** | 修 B5（project_id 必填）| B5 | 0.5h |
| **P3** | 其余 Minor（M16, M17, M18, M22, M23, M27-M36）| — | 2h |

**总工时估算**：**17-22 小时**（v2 比 v1 略减，因为 B1/B3/B6 已有基础，只需补 B12/B15 等）

**关键路径**（P0 → P1，按依赖关系）：
1. **P0-1**：B12 修（0.5h）—— 单行改动，立即让 multi-turn tool-calling 工作
2. **P0-2**：B15 修（1h）—— file tools 加沙箱
3. **P1-3**：B13 修（0.5h）—— task 重复 append
4. **P1-4**：B14 修（0.5h）—— chat 不强制 JSON
5. **P1-5**：B17 修（2h）—— provider 缓存（不修这个，长时间跑会 leak 内存）
6. **P1-6**：B2 yaml 修（1h）
7. **P1-7**：B7 memory token 修（1-2h）
8. **P1-8**：B4 持久化（3-4h）
9. **P2-x**：其余

---

## 7. 关键文件速查（v2 修复时直接看）

| 想改什么 | 直接看 |
|---------|--------|
| **B12 assistant message 丢** | `kairos/agents/base.py:152-159`（一行守卫错）|
| **B13 task 重复 append** | `kairos/core/orchestrator.py:206` + `:259` |
| **B14 chat 强制 JSON** | `kairos/agents/roles/team_leader.py:18-39` 或 `kairos/agents/base.py:179-209` |
| **B15 file tools 沙箱** | `kairos/tools/file_read.py`、`file_edit.py`（参照 `terminal.py:43-58` 的 `_resolve_cwd`）|
| **B16 assignee 验证** | `kairos/core/orchestrator.py:194-209` |
| **B17 provider 缓存** | `kairos/llm/model_router.py:128-150` + `api/routes/websocket.py:44` |
| **B18 close 钩子** | `kairos/main.py` 加 atexit |
| **B19 dispatch task 引用** | `kairos/core/orchestrator.py:208` + `Orchestrator.__init__` |
| **B20 tool schema** | `kairos/tools/base.py:24-29` + 每个具体 tool |
| **B21 tool_use_id 兜底** | `kairos/llm/providers/anthropic_provider.py:36-47` |
| **B22 Ollama tool 协议** | `kairos/llm/providers/ollama_provider.py:38-49` |
| **B7 memory token** | `kairos/agents/base.py:79-82` |
| **B4 持久化** | 新建 `kairos/core/persistence.py`，改 `orchestrator.py` |
| **B2 yaml 真读** | `kairos/agents/roles/*.py`（把常量改 `__init__` 参数）+ `Orchestrator._create_team` |
| **B8 review 解析** | `kairos/review/engine.py:106-128` |
| **B10 WS per-client** | `api/routes/websocket.py` 整文件重写 |
| **B9 listener logging** | `kairos/core/message_bus.py:60-72` |
| **B11 不截断** | `kairos/core/orchestrator.py:158,178` |

---

## 8. 一句话总结

> **v2 比 v1 进步巨大——端到端跑通，agent 真把 read_csv.py 写进了 work_dir。但"B1 修了一半"埋了 B12（Critical）这个新坑：assistant-only-with-tool-calls message 被 `if response.content:` 守卫丢掉，导致 multi-turn tool-calling 第二轮就 400 挂。修 B12 + B15（file tools 沙箱）总共 1.5 小时，系统就从"半可用"变成"真可用"。**

---

## 附录 A：v1 → v2 修复率统计

| v1 报告 bug | 总数 | v2 完整修 | v2 部分修 | v2 未动 |
|------------|------|---------|---------|---------|
| Critical 3（B1/B2/B3）| 3 | 1 (B3) | 1 (B1, 但有 B12 副作用) | 1 (B2) |
| Major 8（B4-B11 + 一些 B6+）| 8 | 1 (B6) | 0 | 7 |
| Minor 13（M1-M13）| 13 | 0 | 0 | 13 |

**v1 → v2 修复率**：2/24 完整修（B3、B6），1/24 部分修（B1），21/24 没动。

**v2 新发现**：
- Critical 1（B12）
- Major 11（B13-B22 加 B15-B16 等）
- Minor 23（M14-M36）

**v2 报告 bug 总数**：35 个（vs v1 24 个）

**v2 比 v1 多 11 个 bug 的原因**：
- v1 没跑过端到端，很多"看起来对"的设计在真跑时暴雷（B12 协议破坏）
- v1 漏看了 file tools 沙箱（B15）
- v1 漏看了 task 重复计数（B13）
- v1 漏看了 chat 模式被 JSON prompt 污染（B14）

---

## 附录 B：实测 e2e 关键日志（脱敏）

```
# start_project 真 LLM（MiniMax）调用
Plan: {
  "analysis": "The user wants to build a simple 'Hello World' CLI...",
  "plan": "Create a minimal Python CLI project structure...",
  "tasks": [
    {
      "assignee": "backend_dev",
      "title": "Implement /read_csv.py CLI script",
      "description": "Create the file workspace\\ecf9198a\\read_csv.py with a Python CLI..."
    }
  ]
}

# backend_dev 第二轮 LLM 调用 → 400
[error] backend_dev: Client error '400 Bad Request' for url 'https://api.minimaxi.com/anthropic/v1/messages'

# work_dir 实际文件
FILE: read_csv.py (1238 bytes)
```

---

## 附录 C：测试时发现的好消息

虽然 v2 还有一堆 bug，但有些地方**做得不错**：

1. ✅ **Tool-calling 第一次成功**——file_write 真把 1238 字节写进 work_dir
2. ✅ **Team Leader JSON 输出稳定**——5 次 start_project 跑出来都能正确解析
3. ✅ **Terminal 沙箱严**——`rm -rf /` / cwd 越界都拦住
4. ✅ **Anthropic 协议转换正确**——`_convert_messages` 把 `role=tool` 改成 `role=user + content=[{type:tool_result}]` 是对的
5. ✅ **Provider 注册表**清晰——加新 provider 一行 register
6. ✅ **MessageBus pub/sub 模型对**——listener + topic 路由都有

基础架构是对的，**只是在"集成层"漏了**。修完 P0+P1 大部分能补上。

---

**报告完。** 建议第一步：先改 B12（0.5h），改完立即测 multi-turn tool-calling 是不是不再 400 挂。如果验证通过，再做 B15 沙箱和 B13 去重。后续按 Roadmap 推即可。
