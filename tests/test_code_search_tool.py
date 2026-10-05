"""The code_search tool: semantic search without a network, a model, or semble.

Everything here runs against a fake index. The real backend downloads a model
from HuggingFace on first build, which is exactly what a test must not do, and
what ``kairos.tools.code_search`` is written to keep behind an injectable
factory. What is under test is the contract the agent depends on:

* a missing ``semble`` is a readable tool error, never an ImportError at import
  or agent-construction time;
* indexing and search run off the event-loop thread;
* results are formatted as ``file:start-end`` and both the per-snippet and the
  whole-payload budgets are enforced;
* the tool is registered in the live Coder/Reviewer tool lists and survives
  read-only mode.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import types
from pathlib import Path

import pytest

from kairos.tools.code_search import (
    CodeSearchTool,
    SembleUnavailableError,
    build_semble_index,
)

# ---------------------------------------------------------------------------
# a fake backend, shaped like semble's SearchResult / Chunk
# ---------------------------------------------------------------------------

class FakeChunk:
    def __init__(self, content, file_path, start_line, end_line, language="python"):
        self.content = content
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.language = language


class FakeResult:
    def __init__(self, chunk, score):
        self.chunk = chunk
        self.score = score


class FakeIndex:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls: list[dict] = []

    def search(self, query, top_k=10, alpha=None, filter_languages=None,
               filter_paths=None, rerank=None, max_snippet_lines=None):
        self.calls.append({"query": query, "top_k": top_k, "filter_paths": filter_paths})
        return self.results[:top_k]


def _result(path, start, end, content="def f():\n    return 1", score=0.9):
    return FakeResult(FakeChunk(content, path, start, end), score)


def make_tool(tmp_path, results=None):
    idx = FakeIndex(results)
    return CodeSearchTool(allowed_root=tmp_path, index=idx), idx


def run(tool, **kwargs):
    return asyncio.run(tool.execute(**kwargs))


# ---------------------------------------------------------------------------
# argument validation
# ---------------------------------------------------------------------------

def test_query_is_required(tmp_path):
    tool, idx = make_tool(tmp_path)
    res = run(tool, query="")
    assert not res.success
    assert "query is required" in res.error
    assert idx.calls == [], "an invalid call must not reach the backend"


def test_blank_query_is_refused(tmp_path):
    tool, _ = make_tool(tmp_path)
    res = run(tool, query="   \n  ")
    assert not res.success
    assert "query is required" in res.error


def test_nonexistent_path_is_an_error_not_a_crash(tmp_path):
    tool, _ = make_tool(tmp_path)
    res = run(tool, query="x", path="does/not/exist")
    assert not res.success
    assert "does not exist" in res.error


# ---------------------------------------------------------------------------
# success formatting and budgets
# ---------------------------------------------------------------------------

def test_results_are_formatted_with_file_and_line_range(tmp_path):
    tool, _ = make_tool(tmp_path, [_result("kairos/foo.py", 10, 14, "def f():\n    return 1")])
    res = run(tool, query="where is f defined")
    assert res.success, res.error
    assert "kairos/foo.py:10-14" in res.output
    assert "def f():" in res.output
    assert res.metadata["results"] == 1


def test_score_is_shown_when_present(tmp_path):
    tool, _ = make_tool(tmp_path, [_result("a.py", 1, 2, "x = 1", score=0.5)])
    res = run(tool, query="x")
    assert "0.500" in res.output


def test_long_snippet_is_truncated_per_chunk(tmp_path):
    body = "\n".join(f"line{i}" for i in range(100))
    tool, _ = make_tool(tmp_path, [_result("a.py", 1, 100, body)])
    res = run(tool, query="line", max_snippet_lines=5)
    assert res.success, res.error
    assert "line4" in res.output
    assert "line5" not in res.output
    assert "+95 more lines" in res.output


def test_oversized_payload_is_capped(tmp_path):
    body = "z" * 2000
    results = [_result(f"f{i}.py", 1, 1, body) for i in range(40)]
    tool, _ = make_tool(tmp_path, results)
    res = run(tool, query="z", top_k=40, max_snippet_lines=100)
    assert res.success, res.error
    assert res.metadata["truncated"] is True
    assert len(res.output) <= 50_000 + 200
    assert "truncated" in res.output


def test_no_matches_is_a_clean_success(tmp_path):
    tool, _ = make_tool(tmp_path, [])
    res = run(tool, query="nothing here")
    assert res.success
    assert res.output == "(no matches)"
    assert res.metadata["results"] == 0


def test_top_k_defaults_and_is_clamped(tmp_path):
    tool, idx = make_tool(tmp_path, [])
    run(tool, query="x")
    run(tool, query="x", top_k=999)
    run(tool, query="x", top_k=0)
    assert [c["top_k"] for c in idx.calls] == [8, 50, 1]


def test_filter_paths_reach_the_backend(tmp_path):
    tool, idx = make_tool(tmp_path, [_result("a.py", 1, 1)])
    run(tool, query="x", filter_paths=["a.py"])
    assert idx.calls[0]["filter_paths"] == ["a.py"]


# ---------------------------------------------------------------------------
# threading and the index lifecycle
# ---------------------------------------------------------------------------

def test_search_runs_off_the_event_loop_thread(tmp_path):
    main = threading.get_ident()
    seen: dict = {}

    class ThreadProbe:
        def search(self, query, **kwargs):
            seen["ident"] = threading.get_ident()
            return []

    tool = CodeSearchTool(allowed_root=tmp_path, index=ThreadProbe())
    run(tool, query="x")
    assert "ident" in seen, "the backend was never called"
    assert seen["ident"] != main, "blocking search must run in a worker thread"


def test_index_is_built_once_and_reused(tmp_path):
    calls: list = []

    def factory(root):
        calls.append(root)
        return FakeIndex([_result("a.py", 1, 1)])

    tool = CodeSearchTool(allowed_root=tmp_path, index_factory=factory)
    run(tool, query="one")
    run(tool, query="two")
    assert len(calls) == 1, "the second search must reuse the in-memory index"


def test_refresh_rebuilds_the_index(tmp_path):
    calls: list = []

    def factory(root):
        calls.append(root)
        return FakeIndex([])

    tool = CodeSearchTool(allowed_root=tmp_path, index_factory=factory)
    run(tool, query="one")
    run(tool, query="two", refresh=True)
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# graceful degradation: no semble
# ---------------------------------------------------------------------------

def test_constructing_the_tool_never_imports_semble(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "semble", None)
    tool = CodeSearchTool(allowed_root=tmp_path)  # must not raise
    assert tool.name == "code_search"
    assert "semble" not in sys.modules or sys.modules["semble"] is None


def test_build_semble_index_reports_the_missing_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "semble", None)
    with pytest.raises(SembleUnavailableError):
        build_semble_index(Path("."))


def test_missing_semble_is_a_tool_error_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "semble", None)
    tool = CodeSearchTool(allowed_root=tmp_path)  # real factory
    res = run(tool, query="anything")
    assert not res.success
    assert "semble" in (res.error or "").lower()


def test_backend_exception_becomes_a_tool_error(tmp_path):
    class Boom:
        def search(self, *args, **kwargs):
            raise RuntimeError("index corrupt")

    tool = CodeSearchTool(allowed_root=tmp_path, index=Boom())
    res = run(tool, query="x")
    assert not res.success
    assert "code search failed" in res.error
    assert "index corrupt" in res.error


# ---------------------------------------------------------------------------
# schema and integration with the surrounding policy/registration
# ---------------------------------------------------------------------------

def test_schema_describes_the_query_and_the_limits(tmp_path):
    tool, _ = make_tool(tmp_path)
    schema = tool.to_schema()
    assert schema["name"] == "code_search"
    assert schema["parameters"]["required"] == ["query"]
    assert "query" in schema["parameters"]["properties"]
    assert "top_k" in schema["parameters"]["properties"]


def test_code_search_survives_read_only_mode(tmp_path):
    from kairos.coder_modes import CoderMode, apply_mode

    tool, _ = make_tool(tmp_path)
    kept, _policy = apply_mode([tool], CoderMode.READ_ONLY)
    assert [t.name for t in kept] == ["code_search"]
    assert "code_search" in getattr(tool, "name", "")


def test_code_search_is_a_read_only_tool_for_approval():
    from kairos.approval import READ_ONLY_TOOLS
    assert "code_search" in READ_ONLY_TOOLS


class _Router:
    """Only ``get_provider_for_role`` is reached before ``_make_agent`` is stubbed."""

    def get_provider_for_role(self, role):
        return None


def test_orchestrator_wires_code_search_into_both_roles(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_NO_BUNDLED_MCP", "1")
    from kairos.core.orchestrator import Orchestrator, Project
    from kairos.core.persistence import Persistence

    orch = Orchestrator(model_router=_Router(), workspace_base=tmp_path / "ws",
                        db=Persistence(tmp_path / "kairos.db"))
    project = Project("p1", "n", "d", tmp_path / "ws" / "p1", db=orch._db)
    project.workspace.mkdir(parents=True, exist_ok=True)

    # MCP startup waits out a timeout when `npx` is absent; it is orthogonal to
    # which static tools the roles get, and the existing wiring test pays it too.
    monkeypatch.setattr(orch, "_attach_mcp", lambda *a, **k: [])

    seen: dict = {}

    def fake_make_agent(project_id, role, role_cls, provider, tools, bus, prompts):
        seen[role] = list(tools)
        return type("A", (), {"agent_id": project_id + "." + role})()

    monkeypatch.setattr(orch, "_make_agent", fake_make_agent)
    orch._create_agents(project)

    assert "code_search" in [t.name for t in seen["coder"]]
    assert "code_search" in [t.name for t in seen["reviewer"]]


# ---------------------------------------------------------------------------
# HuggingFace 端点兜底：huggingface.co 在国内不通，首次建索引会失败
# ---------------------------------------------------------------------------

def _fake_semble(from_path):
    """一个最小可用的假 semble 模块：ContentType.CODE + SembleIndex.from_path。"""
    class _ContentType:
        CODE = "code"

    class _Index:
        @staticmethod
        def from_path(root, content=None, show_progress_bar=False):
            return from_path(root)

    mod = types.ModuleType("semble")
    mod.ContentType = _ContentType
    mod.SembleIndex = _Index
    return mod


def test_build_semble_index_retries_through_the_hf_mirror(tmp_path, monkeypatch):
    """默认端点失败时，自动改用 hf-mirror 重试一次（否则国内首次检索必失败）。"""
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    seen: list[str | None] = []

    def from_path(_root):
        seen.append(os.environ.get("HF_ENDPOINT"))
        if len(seen) == 1:
            raise OSError("connection to huggingface.co timed out")
        return "INDEX"

    monkeypatch.setitem(sys.modules, "semble", _fake_semble(from_path))

    assert build_semble_index(tmp_path) == "INDEX"
    assert seen == [None, "https://hf-mirror.com"]


def test_build_semble_index_respects_an_explicit_endpoint(tmp_path, monkeypatch):
    """运维显式设了 HF_ENDPOINT 就尊重它：失败照抛，不再偷偷换端点。"""
    monkeypatch.setenv("HF_ENDPOINT", "https://internal.example/hf")
    seen: list[str | None] = []

    def from_path(_root):
        seen.append(os.environ.get("HF_ENDPOINT"))
        raise OSError("nope")

    monkeypatch.setitem(sys.modules, "semble", _fake_semble(from_path))

    with pytest.raises(OSError):
        build_semble_index(tmp_path)
    assert seen == ["https://internal.example/hf"]
