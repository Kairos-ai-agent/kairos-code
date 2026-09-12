# Kairos 借鉴 Codex Harness 设计 — 实施计划

> **背景**：用户 2026-08-25 拍板"方案 3 走起"——借鉴 OpenAI 2026-08-19 开源的 Codex Harness 设计模式，抄到 Kairos 而不是集成 Rust runtime。  
> **目标**：把当前 Kairos v4 之后又自研 1 个月的产品级系统，再补 4 个 Codex Harness 核心模式。

---

## 0. 现状盘点（2026-08-25）

用户已经在 v1-v4 报告之后**自研重写**了 Kairos。当前实际架构（grep 确认）：

```
kairos/
├── agents/
│   ├── base.py              25KB — KairosAgent + stream + hooks + lock + plan_mode + token counting
│   └── roles/
│       ├── coder.py         — 取代原 backend_dev
│       ├── design.py        — 取代原 architect
│       ├── docs.py          — 取代原 product_manager
│       ├── perf.py          — 新
│       ├── refactor.py      — 新
│       ├── reviewer.py      — 取代原 code_reviewer
│       ├── security.py      — 新
│       └── test.py          — 取代原 qa_engineer
├── core/
│   ├── orchestrator.py      35KB — 大重写
│   ├── persistence.py       42KB — SQLite（B4 修了）
│   └── message_bus.py       7.5KB
├── hooks/                    — B17 修了
│   └── runner.py            4KB
├── learning/                — 新
│   └── reflect.py           10KB
├── llm/
│   ├── model_router.py      9.5KB — 5s TTL 缓存（B17 修了）
│   ├── resilient.py         8KB — 重试/fallback
│   └── providers/
├── loop/                    — 新
│   ├── loop_runner.py       33KB
│   ├── reviewers.py         9KB — **1 main + 7 specialists 加权平均**
│   ├── review_loop.py       5KB
│   ├── prompts.py           7KB
│   ├── plan_mode.py
│   ├── precheck.py
│   ├── gates.py
│   └── cross_loop.py
├── memory/                  — 新
│   ├── retrieval.py         12KB
│   ├── growth.py            10KB — **auto-promote failure→skill**
│   └── __init__.py
├── review/                  — 增强
│   ├── engine.py            7.6KB
│   ├── comments.py
│   └── mermaid.py
└── tools/                   11 个 tool
    ├── base.py / cache.py / checkpoint.py
    ├── file_read / file_edit / terminal
    ├── find / grep / git / subagent / webfetch
```

**v1-v4 报告的 bug 修复情况**：
- ✅ B1 (tools 接入) — 通过 base.py 完整实现
- ✅ B2 (yaml 真读) — 仍未做（AGENTS.md 是改进方向）
- ✅ B3 (Terminal 沙箱) — 已有 deny list + cwd 锁
- ✅ B4 (持久化) — persistence.py 42KB SQLite
- ✅ B5 (project_id 路由) — 通过项目隔离
- ✅ B6 (多 agent 派发) — orchestrator 重写
- ✅ B7 (memory token) — `_max_tokens = 80000` + truncate
- ✅ B8 (review 解析) — reviewers.py 多种 JSON 提取
- ✅ B9 (listener logging) — hooks/runner.py 用 logger
- ✅ B10 (WebSocket) — 不清楚
- ✅ B11-B15 — 大部分修了
- ✅ B17 (provider 缓存) — 5s TTL
- ✅ B18-B22 — 多数修了
- ✅ B23 (provider 协议) — anthropic/openai tool_use/tool_calls 转换
- ✅ B24 (agent 并发) — `asyncio.Lock` in base.py

---

## 1. Codex Harness 模式 vs Kairos 差距

| Codex Harness 模式 | 当前 Kairos | 需要做？ |
|------------------|-----------|--------|
| **AGENTS.md**（结构化指令） | system_prompt 硬编码在 .py | ❌ **必须做** |
| **Skills 框架**（按需 .md） | 只有 auto-promote，**没有显式 .md 加载** | ❌ **必须做** |
| **Manifest**（工作空间声明） | 无 | 🟡 可选 |
| **retained reasoning**（双层 memory） | 简单 truncate | ❌ **必须做** |
| **OS-level 沙箱** | deny list 字符串匹配 | 🟡 Windows 受限 |

**review 保留** — 已有 1 main + 7 specialists 比 Codex Harness 更强。

---

## 2. 实施阶段

### 阶段 1：AGENTS.md + Skills 框架（**本次实施**）

**预计 4-6h**

#### 2.1.1 AGENTS.md 加载器

**目的**：替代/补充 role .py 里的硬编码 system_prompt。

**位置**：
- 项目级：`<project.work_dir>/AGENTS.md`
- 全局级：`~/.kairos/AGENTS.md`
- 默认：内置的（fallback）

**格式**（参考 Codex Harness）：
```markdown
# Project: <name>

## Architecture
- This is a Flask + SQLite app using Bootstrap 5.

## Coding Conventions
- Use type hints for all new functions.
- Tests in tests/ directory.

## Don't
- Don't add new dependencies without discussion.
```

**API**：
```python
# kairos/agents_md.py
class AgentsMdLoader:
    def __init__(self, project_dir: Path, global_dir: Path | None = None):
        ...
    def load(self) -> str:
        """Load and merge global + project AGENTS.md."""
        ...
    def load_section(self, section_name: str) -> str | None:
        """Load only one section by header name (e.g., 'Don't')."""
        ...
```

**集成到 `KairosAgent.__init__`**：
```python
def __init__(self, ..., project_dir: Path | None = None):
    ...
    if project_dir:
        loader = AgentsMdLoader(project_dir)
        md = loader.load()
        # 拼到 system_prompt 末尾
        self.system_prompt = f"{self.system_prompt}\n\n# Project Instructions (AGENTS.md)\n\n{md}"
```

**测试**：
```python
# tests/test_agents_md.py
def test_loads_global_only():
    ...
def test_project_overrides_global():
    ...
def test_section_splitting():
    ...
```

#### 2.1.2 Skills 框架

**目的**：动态按需加载的领域知识，存为 .md 文件 + YAML frontmatter。

**位置**：
- 全局：`~/.kairos/skills/*.md`
- 项目：`<project.work_dir>/.kairos/skills/*.md`

**Skill 文件格式**：
```markdown
---
name: react-hooks-best-practices
description: React 18 hooks 最佳实践，包括 useState/useEffect/useMemo 常见坑
when: keyword=react OR tool=file_write+filename=*.tsx
priority: 0.7
---

# React Hooks Best Practices

## useState
- 不要在 setState 中直接修改 state
- ...
```

**API**：
```python
# kairos/skills.py
@dataclass
class Skill:
    name: str
    description: str
    when: dict
    priority: float
    body: str
    source_path: Path

class SkillsLoader:
    def __init__(self, project_dir: Path, global_dir: Path | None = None):
        ...
    def discover(self) -> list[Skill]:
        """扫描所有 .md 文件，解析 frontmatter，返回 Skill 列表。"""
        ...
    def match(self, context: dict) -> list[Skill]:
        """根据上下文（keyword, tool, filename）匹配相关 skill，按 priority 排序。"""
        ...
    def render(self, skills: list[Skill]) -> str:
        """渲染为可注入 system_prompt 的 markdown。"""
        ...
```

**集成到 `KairosAgent.run()`**：
```python
async def run(self, task):
    ...
    # 任务开始时，匹配相关 skill
    skills = self.skills_loader.match({
        "keyword": task.title + " " + task.description,
        "tool": "file_write",
        "filename": ...,  # 从 task.context 取
    })
    if skills:
        skills_md = self.skills_loader.render(skills[:3])  # 最多 3 个
        self._system_prompt_with_skills = f"{self.system_prompt}\n\n# Active Skills\n\n{skills_md}"
    ...
```

**测试**：
```python
def test_discover_skill_files():
    ...
def test_match_by_keyword():
    ...
def test_match_by_tool_filename():
    ...
def test_render_includes_body():
    ...
```

---

### 阶段 2：retained reasoning（下次再做）

**预计 4-6h**

#### 2.2.1 双层 memory

```python
class KairosAgent:
    def __init__(self):
        self._memory_recent: list[LLMMessage] = []      # 最近 8 条
        self._memory_summary: str = ""                # 远期总结（每 8 轮更新）
        self._memory_observations: list[str] = []      # 关键观察列表
    
    def _maybe_summarize(self):
        if len(self._memory_recent) >= 8:
            # 调 LLM 总结前 8 条 → 1 段 summary
            summary = await self._llm.complete([
                LLMMessage(role="system", content="Summarize the agent's progress in <200 words, focusing on: 1) what worked 2) what failed 3) key decisions made"),
                LLMMessage(role="user", content="\n".join(m.content for m in self._memory_recent))
            ])
            self._memory_summary = summary.content
            self._memory_recent = self._memory_recent[4:]  # 保留最近 4 条原始
```

#### 2.2.2 集成到 `_build_messages`

```python
def _build_messages(self):
    msgs = [LLMMessage(role="system", content=self.system_prompt)]
    if self._memory_summary:
        msgs.append(LLMMessage(role="system", 
                                content=f"# Earlier conversation summary\n\n{self._memory_summary}"))
    return msgs + self._memory_recent
```

---

### 阶段 3：Manifest 工作空间声明（可选）

**预计 2-3h**

```yaml
# <project.work_dir>/.kairos/manifest.yaml
workspace:
  name: my-app
  type: python
  entry: src/main.py
trust:
  paths:
    - src/**
    - tests/**
  deny:
    - .env
    - secrets/**
sandbox:
  network: false
  memory_mb: 512
  cpu: 1.0
```

**集成**：Orchestrator._create_team 读取 manifest，配置 TerminalTool 沙箱。

---

### 阶段 4：OS 沙箱（Windows 受限）

**预计 4-8h**

- Linux: `prctl(PR_SET_NO_NEW_PRIVS)` + Landlock
- Windows: Job Object + 限制 token（pywin32）
- 降级：dofld list 兜底（已实现）

---

## 3. 不做的（明确边界）

- ❌ 集成 codex-rs（Rust runtime）—— 推倒重来得不偿失
- ❌ 集成 codex app-server（JSON-RPC）—— 跨语言边界
- ❌ Skills 文件系统目录 + MCP server —— 跟 Codex Harness 一样的"everything is a plugin" 路线太重
- ❌ AGENTS.md 的 .claude 兼容 —— 我们的格式独立

---

## 4. 关键文件改动清单（阶段 1）

| 文件 | 动作 | 行数估算 |
|------|------|---------|
| `kairos/agents_md.py` | **新增** | ~80 |
| `kairos/skills.py` | **新增** | ~120 |
| `kairos/agents/base.py` | 改 `__init__` + `run()` | +20 |
| `kairos/agents/roles/__init__.py` | 加 `agents_md_loader` 参数 | +5 |
| `kairos/core/orchestrator.py` | `_create_team` 传 `project.work_dir` 到 agent | +10 |
| `tests/test_agents_md.py` | **新增** | ~80 |
| `tests/test_skills.py` | **新增** | ~100 |

合计：~400 行新代码，~35 行改动，~180 行测试。

---

## 5. 验证

1. 单元测试：`pytest tests/test_agents_md.py tests/test_skills.py -v`
2. 端到端：
   - 启动 server
   - 创建项目
   - 在 `workspace/<id>/AGENTS.md` 写一段项目说明
   - 在 `workspace/<id>/.kairos/skills/python.md` 写一段 skill
   - start_project
   - 验证 backend_dev 收到的 prompt 包含 AGENTS.md + 触发了 skill

---

## 6. 风险

- **AGENTS.md 太大撑爆 context** → 实现大小限制（≤4KB）+ 摘要 fallback
- **Skill 误触发** → priority 排序 + max 3 个
- **frontmatter 解析** → 用成熟的 `python-frontmatter` 库，不自己写 parser
- **Windows 路径** → 全部用 `pathlib.Path`，不用 `os.path`
