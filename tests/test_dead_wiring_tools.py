"""The two wires that were cut and are now connected.

(1) ``write_todos`` — ``kairos/agents/base.py`` intercepts a call named
    ``write_todos`` and applies it to the agent's plan tracker, but no such
    tool was ever registered, so no model was ever told the tool existed and
    the call never came. The Loop page's "Current plan" panel stayed empty.

(2) ``subagent_status`` / ``subagent_result`` — ``spawn_subagent``'s schema
    and its ``background=True`` result both told the model to use these two
    tools, and neither was registered. A background handle was a dead end.

These tests pin both wirings: the tools reach the Coder's toolset with valid
schemas, a ``write_todos`` call really lands in the plan tracker, the handle
readers answer correctly for a completed / still-running / unknown handle, and
they cannot reach another session's handle. Last, the new source is run through
the same secret scanner the release build uses.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from kairos.agents.base import AgentTask, KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.core.orchestrator import Orchestrator, Project
from kairos.core.persistence import Persistence
from kairos.llm.base import LLMConfig, LLMResponse, ToolCall
from kairos.loop.plan import Plan
from kairos.tools.subagent import (SubagentResultTool, SubagentStatusTool,
                                   SubagentTool)
from kairos.tools.todos import TODO_STATUSES, WriteTodosTool

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# (1) wiring: the tools reach the Coder, and their schemas are valid
# ---------------------------------------------------------------------------


class _Router:
    """Only ``get_provider_for_role`` is reached before ``_make_agent`` is stubbed."""

    def get_provider_for_role(self, role):
        return None


def _coder_and_reviewer_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_NO_BUNDLED_MCP", "1")
    orch = Orchestrator(model_router=_Router(), workspace_base=tmp_path / "ws",
                        db=Persistence(tmp_path / "kairos.db"))
    project = Project("p1", "n", "d", tmp_path / "ws" / "p1", db=orch._db)
    project.workspace.mkdir(parents=True, exist_ok=True)

    seen: dict = {}

    def fake_make_agent(project_id, role, role_cls, provider, tools, bus, prompts):
        seen[role] = list(tools)
        return type("A", (), {"agent_id": project_id + "." + role, "tools": tools})()

    monkeypatch.setattr(orch, "_make_agent", fake_make_agent)
    orch._create_agents(project)
    return seen


def test_the_coder_gets_the_new_tools_and_the_reviewer_does_not(tmp_path, monkeypatch):
    seen = _coder_and_reviewer_tools(tmp_path, monkeypatch)
    coder = sorted(t.name for t in seen["coder"])
    reviewer = sorted(t.name for t in seen["reviewer"])

    # write_todos: the Coder plans a change; the Reviewer does not plan the
    # change it then has to review.
    assert "write_todos" in coder, coder
    assert "write_todos" not in reviewer, reviewer

    # The background-sub-agent readers, registered beside their spawn tool.
    assert "spawn_subagent" in coder, coder
    assert "subagent_status" in coder, coder
    assert "subagent_result" in coder, coder
    assert "subagent_status" not in reviewer, reviewer
    assert "subagent_result" not in reviewer, reviewer


def test_write_todos_schema_matches_apply_write_todos():
    schema = WriteTodosTool().to_schema()
    assert schema["name"] == "write_todos"
    params = schema["parameters"]
    assert params["type"] == "object"
    assert params["required"] == ["todos"]
    todos = params["properties"]["todos"]
    assert todos["type"] == "array"
    item = todos["items"]
    assert item["required"] == ["content", "status"]
    assert item["properties"]["content"]["type"] == "string"
    assert item["properties"]["status"]["enum"] == list(TODO_STATUSES)
    # activeForm is accepted (kairos.loop.plan.TodoItem carries it), optional.
    assert "activeForm" in item["properties"]
    assert "activeForm" not in item["required"]


def test_the_subagent_handle_reader_schemas_are_valid():
    for cls in (SubagentStatusTool, SubagentResultTool):
        schema = cls().to_schema()
        params = schema["parameters"]
        assert params["required"] == ["handle"]
        assert params["properties"]["handle"]["type"] == "string"
        assert params["type"] == "object"


# ---------------------------------------------------------------------------
# (1) behaviour: a write_todos call really updates the plan tracker
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    """Returns the scripted responses in order; forces the non-stream path."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.seen_tools = []

    async def complete(self, messages, tools=None, **kw):
        self.seen_tools.append(tools)
        resp = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return resp

    def stream(self, messages, tools=None, **kw):
        # ``_stream_complete`` falls back to ``complete()`` when stream() is
        # unavailable — which is exactly what this test wants.
        raise RuntimeError("streaming is not used in this test")

    async def close(self):
        pass


def test_a_write_todos_tool_call_updates_the_plan_tracker():
    plan = Plan()
    bus = MessageBus()
    published = []
    bus.add_listener(lambda m: published.append(m))

    write_call = LLMResponse(
        content="",
        model="m",
        tool_calls=[ToolCall(id="c1", name="write_todos", arguments={
            "todos": [
                {"content": "Read README", "status": "in_progress",
                 "activeForm": "Reading README"},
                {"content": "Add CSV reader", "status": "pending"},
            ],
        })],
    )
    final = LLMResponse(content="done", model="m")

    agent = KairosAgent(
        agent_id="a1", name="coder", role="coder",
        system_prompt="sys",
        llm_config=LLMConfig(provider="anthropic", model="m", api_key="sk-test"),
        message_bus=bus, tools=[WriteTodosTool()], plan_tracker=plan,
    )
    llm = _ScriptedLLM([write_call, final])
    agent._llm = llm

    result = asyncio.run(agent.run(AgentTask(id="t1", title="t", description="d")))

    # The plan really moved.
    assert [t.content for t in plan.todos] == ["Read README", "Add CSV reader"]
    assert plan.todos[0].status == "in_progress"
    assert plan.todos[0].activeForm == "Reading README"
    assert plan.todos[1].status == "pending"

    # ...and the UI got the event that drives the plan panel.
    plan_msgs = [m for m in published if m.topic == "plan.updated"]
    assert len(plan_msgs) == 1
    assert plan_msgs[0].metadata["plan"]["todos"][0]["content"] == "Read README"

    # The model was told the tool exists — this is what was missing before.
    first_turn_tools = llm.seen_tools[0] or []
    assert "write_todos" in [s["name"] for s in first_turn_tools]
    assert result == "done"


def test_write_todos_is_intercepted_even_without_a_registered_tool():
    """The interception keys on the *name*, so adding the tool cannot break it.

    An agent with a plan tracker but no WriteTodosTool in its list must still
    route the call to the plan, not to "Unknown tool".
    """
    plan = Plan()
    bus = MessageBus()
    bus.add_listener(lambda m: None)

    agent = KairosAgent(
        agent_id="a1", name="coder", role="coder", system_prompt="sys",
        llm_config=LLMConfig(provider="anthropic", model="m", api_key="sk-test"),
        message_bus=bus, tools=[], plan_tracker=plan,
    )
    res = asyncio.run(agent._handle_write_todos(
        {"todos": [{"content": "x", "status": "pending"}]},
        task=AgentTask(id="t1", title="t", description="d"), turn=0,
    ))
    assert res.success
    assert [t.content for t in plan.todos] == ["x"]


def test_write_todos_tool_falls_back_honestly_without_a_tracker():
    """No tracker -> acknowledge, and do not claim it persisted."""
    tool = WriteTodosTool()          # plan_tracker stays None
    res = asyncio.run(tool.execute(todos=[{"content": "x", "status": "pending"}]))
    assert res.success
    assert res.metadata["persisted"] is False
    assert "nothing was persisted" in res.output


def test_write_todos_tool_applies_when_a_tracker_is_bound():
    plan = Plan()
    tool = WriteTodosTool()
    tool.plan_tracker = plan
    res = asyncio.run(tool.execute(todos=[
        {"content": "a", "status": "pending"},
        {"content": "b", "status": "completed"},
    ]))
    assert res.success
    assert res.metadata["persisted"] is True
    assert [t.content for t in plan.todos] == ["a", "b"]


# ---------------------------------------------------------------------------
# (2) behaviour: the handle readers
# ---------------------------------------------------------------------------


@pytest.fixture
def registry():
    from kairos import long_running as lr
    prev = lr._REGISTRY
    reg = lr.LongRunningRegistry()
    lr.set_registry(reg)
    try:
        yield reg
    finally:
        lr._REGISTRY = prev


@pytest.fixture
def handle_tools():
    """The two readers, scoped to session p1 / p1.coder."""
    spawn = SubagentTool()
    spawn.parent_agent = SimpleNamespace(agent_id="p1.coder")
    spawn.project_id = "p1"
    return SubagentStatusTool(spawn_tool=spawn), SubagentResultTool(spawn_tool=spawn)


def _spawn(reg, body, parent_id="p1.coder", project_id="p1"):
    """Spawn a sub-agent that finishes immediately, and wait for it."""

    async def _go():
        async def _coro():
            return body
        handle = reg.spawn(parent_id=parent_id, project_id=project_id,
                           task_text="do a thing", coro_factory=_coro)
        await reg.wait(handle, timeout_s=5)
        return handle
    return asyncio.run(_go())


def test_status_reports_a_completed_handle(registry, handle_tools):
    status_tool, _ = handle_tools
    handle = _spawn(registry, "child report")
    res = asyncio.run(status_tool.execute(handle=handle))
    assert res.success, res.error
    assert f"handle: {handle}" in res.output
    assert "status: completed" in res.output
    assert "finished: yes" in res.output
    assert res.metadata["result_ready"] is True


def test_result_returns_a_completed_body(registry, handle_tools):
    _, result_tool = handle_tools
    handle = _spawn(registry, "the answer is 42")
    res = asyncio.run(result_tool.execute(handle=handle))
    assert res.success, res.error
    assert "the answer is 42" in res.output
    assert res.metadata["truncated"] is False


def test_an_unknown_handle_is_refused(registry, handle_tools):
    status_tool, result_tool = handle_tools
    st = asyncio.run(status_tool.execute(handle="deadbeef00"))
    rt = asyncio.run(result_tool.execute(handle="deadbeef00"))
    assert st.success is False and "No such background sub-agent" in st.error
    assert rt.success is False and "No such background sub-agent" in rt.error


def test_another_sessions_handle_is_not_reachable(registry, handle_tools):
    """A handle owned by a different parent/project reads as 'no such handle'.

    Both facts must match, so neither a guessed parent id nor a guessed
    project id is enough to read someone else's sub-agent.
    """
    status_tool, result_tool = handle_tools
    other = _spawn(registry, "private", parent_id="p2.coder", project_id="p2")

    st = asyncio.run(status_tool.execute(handle=other))
    rt = asyncio.run(result_tool.execute(handle=other))
    assert st.success is False and "No such background sub-agent" in st.error
    assert rt.success is False
    assert "private" not in rt.output

    # The same project, but a different parent, is also refused.
    sibling = _spawn(registry, "also private",
                     parent_id="p1.reviewer", project_id="p1")
    st2 = asyncio.run(status_tool.execute(handle=sibling))
    assert st2.success is False


def test_result_is_refused_while_the_subagent_is_still_running(registry, handle_tools):
    status_tool, result_tool = handle_tools

    async def _go():
        started = asyncio.Event()
        release = asyncio.Event()

        async def _coro():
            started.set()
            await release.wait()
            return "late"
        handle = registry.spawn(parent_id="p1.coder", project_id="p1",
                                task_text="slow", coro_factory=_coro)
        await started.wait()
        st = await status_tool.execute(handle=handle)
        rt = await result_tool.execute(handle=handle)
        release.set()
        await registry.wait(handle, timeout_s=5)
        return st, rt
    st, rt = asyncio.run(_go())
    assert st.success and "status: running" in st.output
    assert rt.success is False and "still running" in rt.error


def test_a_huge_result_is_truncated_and_the_full_body_saved(registry, handle_tools, tmp_path, monkeypatch):
    from kairos.config.settings import settings as ksettings
    monkeypatch.setattr(ksettings, "data_dir", tmp_path)
    _, result_tool = handle_tools
    body = "line of a long report\n" * 1000        # ~21k chars
    handle = _spawn(registry, body)

    res = asyncio.run(result_tool.execute(handle=handle))
    assert res.success, res.error
    assert res.metadata["truncated"] is True
    assert "chars omitted" in res.output
    assert "[full report saved:" in res.output
    # The file it points at exists and holds the whole (redacted) report.
    path = res.output.split("[full report saved: ")[1].rstrip("]")
    assert Path(path).exists()
    assert len(Path(path).read_text(encoding="utf-8")) >= len(body)


def test_a_credential_in_the_child_report_is_redacted(registry, handle_tools):
    _, result_tool = handle_tools
    # Built at runtime so this file itself carries no secret-shaped literal.
    secret = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    handle = _spawn(registry, f"found a key: {secret}\n")
    res = asyncio.run(result_tool.execute(handle=handle))
    assert res.success, res.error
    assert secret not in res.output
    assert "redacted" in res.output


def test_a_handle_is_required(handle_tools):
    status_tool, result_tool = handle_tools
    assert asyncio.run(status_tool.execute(handle="")).success is False
    assert asyncio.run(result_tool.execute(handle="")).success is False


# ---------------------------------------------------------------------------
# (3) the new source must not look like a secret to the release scanner
# ---------------------------------------------------------------------------

SCANNER = REPO / "scripts" / "scan_binary_secrets.py"
_spec = importlib.util.spec_from_file_location(
    "scan_binary_secrets_dead_wiring", SCANNER)
scanner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scanner)


@pytest.mark.parametrize("rel", [
    "kairos/tools/todos.py",
    "kairos/tools/subagent.py",
    "kairos/long_running.py",
    "kairos/core/orchestrator.py",
    "tests/test_dead_wiring_tools.py",
])
def test_the_new_source_carries_no_secret_shapes(rel):
    result = scanner.scan(REPO / rel, local_values=[])
    assert result["hits"] == [], (rel, result["hits"])
