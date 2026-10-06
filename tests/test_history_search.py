"""HistorySearchTool — recall of *past* sessions, read-only and whitelisted.

The Coder could only ever see the current session, so 「上次我们是怎么做的」
had no answer. This tool reads the stored messages of the *other* sessions out
of ``kairos.db``. The tests below pin the properties that make that safe and
useful:

* a hit is quoted with its session title/id, time and topic;
* the current session is excluded;
* "no hit" is stated plainly, not as an empty string;
* the result list and the output length are capped, and the model is told to
  narrow the keyword;
* the database is opened read-only — a write must fail at the engine;
* a missing database returns one clear Chinese sentence, never an exception;
* a settings-like table (which may hold credentials) is never read;
* snippet text passes through the redactor before it can reach the model.

Behaviour is exercised against a temporary SQLite fixture that mirrors the real
schema (``messages`` + ``projects``); no test touches the user's real database.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

import kairos.tools.history_search as hs
from kairos.tools.history_search import (
    MAX_RESULTS,
    MAX_SNIPPET,
    MAX_TOTAL,
    UNAVAILABLE,
    HistorySearchTool,
    _open_readonly,
)

#: A settings-like table is planted with this value; it must never be returned.
SETTINGS_SENTINEL = "SETTINGS-TABLE-SENTINEL-VALUE"


# ---------------------------------------------------------------------------
# fixture: a minimal copy of the real schema
# ---------------------------------------------------------------------------

def _make_db(path: Path, messages, projects, *, with_settings=True) -> Path:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE messages (id TEXT, sender TEXT, receiver TEXT, topic TEXT,"
        " content TEXT, msg_type TEXT, timestamp REAL, metadata TEXT,"
        " project_id TEXT)")
    con.execute(
        "CREATE TABLE projects (id TEXT, name TEXT, description TEXT,"
        " workspace TEXT, work_dir TEXT, requirements TEXT, status TEXT,"
        " created_at REAL, updated_at REAL, archived_at REAL)")
    for pid, name in projects:
        con.execute(
            "INSERT INTO projects (id, name, status, created_at)"
            " VALUES (?, ?, 'active', 0.0)", (pid, name))
    for row in messages:
        con.execute(
            "INSERT INTO messages (id, sender, receiver, topic, content,"
            " msg_type, timestamp, metadata, project_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
    if with_settings:
        con.execute("CREATE TABLE settings (k TEXT, v TEXT)")
        con.execute("INSERT INTO settings (k, v) VALUES ('api_key', ?)",
                    (SETTINGS_SENTINEL,))
    con.commit()
    con.close()
    return path


def _msg(mid, project_id, content, ts, topic="user.chat", sender="user"):
    return (mid, sender, "caster", topic, content, "text", ts, "{}", project_id)


def _run(tool: HistorySearchTool, **kwargs):
    return asyncio.run(tool.execute(**kwargs))


@pytest.fixture()
def db(tmp_path):
    """p1 is the current session; p2/p3 are past sessions."""
    return _make_db(
        tmp_path / "kairos.db",
        messages=[
            _msg("m1", "p2", "上次我们是用 pytest 跑门禁的。", 1_700_000_000.0),
            _msg("m2", "p3", "门禁脚本放在 tests/ 里，用 pytest -q 执行。",
                 1_700_003_600.0, topic="agent.chat", sender="p3.coder"),
            # Current session — must never be returned.
            _msg("m3", "p1", "当前会话也提到了 pytest 但是不能出现。",
                 1_700_007_200.0),
        ],
        projects=[("p1", "Current"), ("p2", "Old Work"), ("p3", "Probe")],
    )


# ---------------------------------------------------------------------------
# 1) a hit is quoted with its source
# ---------------------------------------------------------------------------

def test_a_hit_is_quoted_with_session_title_id_time_and_topic(db):
    tool = HistorySearchTool(project_id="p1", db_path=db)
    res = _run(tool, query="pytest", limit=5)

    assert res.success is True
    out = res.output
    assert "pytest" in out
    # session title + id
    assert "Old Work" in out and "id=p2" in out
    # a time (the fixture timestamp is 2023-11-14 22:13 UTC → local date shown)
    assert "2023-11-1" in out or "2023-11-14" in out or "2023-11-15" in out
    # provenance: the topic
    assert "user.chat" in out


def test_a_hit_reports_a_snippet_not_the_whole_message(db):
    long_body = "门禁" + ("细节" * 500) + " 用 pytest 收尾。"
    path = _make_db(Path(db).parent / "long.db",
                    messages=[_msg("m1", "p2", long_body, 1_700_000_000.0)],
                    projects=[("p2", "Old Work")])
    tool = HistorySearchTool(project_id="p1", db_path=path)
    res = _run(tool, query="pytest", limit=5)
    assert "pytest" in res.output
    # The 1000-char body must NOT arrive whole: a windowed snippet is capped.
    assert "…" in res.output
    assert long_body not in res.output
    assert len(res.output) < len(long_body)


# ---------------------------------------------------------------------------
# 2) the current session is excluded
# ---------------------------------------------------------------------------

def test_the_current_session_is_excluded(db):
    tool = HistorySearchTool(project_id="p1", db_path=db)
    res = _run(tool, query="pytest", limit=8)
    assert "当前会话也提到了" not in res.output, (
        "the current session must never appear in its own history search"
    )
    # Both past sessions do.
    assert "Old Work" in res.output and "Probe" in res.output


def test_session_argument_restricts_to_one_past_session(db):
    tool = HistorySearchTool(project_id="p1", db_path=db)
    res = _run(tool, query="pytest", limit=8, session="p2")
    assert "Old Work" in res.output
    assert "Probe" not in res.output


# ---------------------------------------------------------------------------
# 3) a miss is stated plainly
# ---------------------------------------------------------------------------

def test_no_hit_is_stated_plainly(db):
    tool = HistorySearchTool(project_id="p1", db_path=db)
    res = _run(tool, query="zzz-definitely-absent-zzz", limit=5)
    assert res.success is True
    assert "没有在过去会话里找到" in res.output
    assert res.metadata.get("hits") == 0


def test_an_empty_query_asks_for_one(db):
    tool = HistorySearchTool(project_id="p1", db_path=db)
    res = _run(tool, query="   ", limit=5)
    assert res.success is True
    assert "关键词" in res.output


# ---------------------------------------------------------------------------
# 4) the result list and the output length are capped
# ---------------------------------------------------------------------------

def test_results_are_capped_and_the_model_is_told_to_narrow(tmp_path):
    msgs = [_msg(f"m{i}", f"p{i}", f"登录 相关记录编号 {i}", 1_700_000_000.0 + i)
            for i in range(12)]
    projects = [(f"p{i}", f"Session {i}") for i in range(12)]
    path = _make_db(tmp_path / "many.db", messages=msgs, projects=projects)

    tool = HistorySearchTool(project_id="current", db_path=path)
    res = _run(tool, query="登录", limit=3)
    blocks = [ln for ln in res.output.splitlines() if ln.startswith("[")]
    assert len(blocks) == 3, res.output
    # and it says there is more
    assert "缩小关键词" in res.output
    assert res.metadata["more"] is True


def test_limit_is_hard_capped_at_max_results(tmp_path):
    msgs = [_msg(f"m{i}", f"p{i}", f"部署 步骤 {i}", 1_700_000_000.0 + i)
            for i in range(MAX_RESULTS + 4)]
    projects = [(f"p{i}", f"Session {i}") for i in range(MAX_RESULTS + 4)]
    path = _make_db(tmp_path / "cap.db", messages=msgs, projects=projects)

    tool = HistorySearchTool(project_id="current", db_path=path)
    res = _run(tool, query="部署", limit=999)
    blocks = [ln for ln in res.output.splitlines() if ln.startswith("[")]
    assert len(blocks) <= MAX_RESULTS, res.output


def test_total_output_stays_under_the_budget(tmp_path):
    body = "缓存 " + ("填充" * 400)
    msgs = [_msg(f"m{i}", f"p{i}", body, 1_700_000_000.0 + i)
            for i in range(MAX_RESULTS + 2)]
    projects = [(f"p{i}", f"Session {i}") for i in range(MAX_RESULTS + 2)]
    path = _make_db(tmp_path / "big.db", messages=msgs, projects=projects)

    tool = HistorySearchTool(project_id="current", db_path=path)
    res = _run(tool, query="缓存", limit=MAX_RESULTS)
    assert len(res.output) <= MAX_TOTAL + 200, len(res.output)


def test_snippet_never_exceeds_its_cap():
    text = "前文" * 300 + " 目标词 " + "后文" * 300
    snippet = hs._snippet(text, "目标词")
    assert len(snippet) <= MAX_SNIPPET + 2  # +2 allows the leading/trailing …
    assert "目标词" in snippet


# ---------------------------------------------------------------------------
# 5) read-only
# ---------------------------------------------------------------------------

def test_the_database_is_opened_read_only(db):
    conn = _open_readonly(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE should_not_exist (x)")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO messages (id, content) VALUES ('x', 'y')")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM messages")
    finally:
        conn.close()


def test_a_read_still_works_on_the_readonly_connection(db):
    conn = _open_readonly(db)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        assert rows[0] == 3
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6) a missing database is a friendly message, never an exception
# ---------------------------------------------------------------------------

def test_missing_database_returns_a_clear_message(tmp_path):
    tool = HistorySearchTool(project_id="p1",
                             db_path=tmp_path / "does-not-exist.db")
    res = _run(tool, query="anything", limit=5)
    assert res.success is True
    assert res.output == UNAVAILABLE
    assert "不可用" in res.output
    assert res.metadata.get("available") is False


def test_a_locked_or_corrupt_database_does_not_raise(tmp_path):
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"this is not a sqlite database at all")
    tool = HistorySearchTool(project_id="p1", db_path=bad)
    res = _run(tool, query="anything", limit=5)
    assert res.success is True
    assert res.output  # some clear sentence, never an empty string


# ---------------------------------------------------------------------------
# 7) a settings-like table is never read
# ---------------------------------------------------------------------------

def test_settings_table_is_never_read(db):
    """The sentinel lives ONLY in the settings table; it must not leak."""
    tool = HistorySearchTool(project_id="p1", db_path=db)
    res = _run(tool, query="SETTINGS-TABLE-SENTINEL", limit=8)
    assert SETTINGS_SENTINEL not in res.output, (
        "content from a settings-like table must never be returned"
    )
    assert "没有在过去会话里找到" in res.output


def test_only_the_whitelisted_tables_are_named():
    """The tool's queries name only messages/projects; nothing else."""
    src = Path(hs.__file__).read_text(encoding="utf-8")
    assert hs.ALLOWED_TABLES == ("messages", "projects")
    assert "FROM messages" in src
    assert "LEFT JOIN projects" in src
    # The whitelist is actually enforced before any query runs.
    assert "set(ALLOWED_TABLES).issubset" in src


# ---------------------------------------------------------------------------
# 8) snippets pass through the redactor before they reach the model
# ---------------------------------------------------------------------------

def test_snippet_content_passes_through_the_redactor(monkeypatch, db):
    seen = []
    real = hs._redact

    def spy(text):
        seen.append(text)
        return real(text)

    # _render/_snippet resolve _redact from the module, so patch it there.
    monkeypatch.setattr(hs, "_redact", spy)
    tool = HistorySearchTool(project_id="p1", db_path=db)
    res = _run(tool, query="pytest", limit=5)
    assert res.success is True
    assert seen, "every snippet must be redacted before it is returned"


# ---------------------------------------------------------------------------
# 9) the tool is wired into the Coder (and the chat path runs on it)
# ---------------------------------------------------------------------------

def _coder_tool_names(tmp_path, monkeypatch):
    from kairos.core.message_bus import MessageBus  # noqa: F401 (import guard)
    from kairos.core.orchestrator import Orchestrator, Project
    from kairos.core.persistence import Persistence

    class _Router:
        def get_provider_for_role(self, role):
            return None

    monkeypatch.setenv("KAIROS_NO_BUNDLED_MCP", "1")
    orch = Orchestrator(model_router=_Router(), workspace_base=tmp_path / "ws",
                        db=Persistence(tmp_path / "kairos.db"))
    project = Project("p1", "n", "d", tmp_path / "ws" / "p1", db=orch._db)
    project.workspace.mkdir(parents=True, exist_ok=True)

    seen: dict = {}

    def fake_make_agent(project_id, role, role_cls, provider, tools, bus, prompts):
        seen[role] = list(tools)
        return type("A", (), {"agent_id": project_id + "." + role})()

    monkeypatch.setattr(orch, "_make_agent", fake_make_agent)
    orch._create_agents(project)
    return sorted(t.name for t in seen["coder"])


def test_the_coder_is_given_history_search(tmp_path, monkeypatch):
    names = _coder_tool_names(tmp_path, monkeypatch)
    assert "history_search" in names, names
    # The pre-existing tools are still there.
    for name in ("file_read", "terminal", "webfetch"):
        assert name in names, name


# ---------------------------------------------------------------------------
# 10) the tool schema is well-formed
# ---------------------------------------------------------------------------

def test_tool_schema_shape():
    tool = HistorySearchTool()
    assert tool.name == "history_search"
    schema = tool.to_schema()
    assert schema["name"] == "history_search"
    assert schema["description"]
    props = schema["parameters"]["properties"]
    for key in ("query", "limit", "session"):
        assert key in props, key
    assert schema["parameters"]["required"] == ["query"]


# ---------------------------------------------------------------------------
# 11) it is exported from the tool registry
# ---------------------------------------------------------------------------

def test_history_search_is_exported():
    import kairos.tools as tools
    assert "HistorySearchTool" in tools.__all__
    assert tools.HistorySearchTool is HistorySearchTool


# ---------------------------------------------------------------------------
# 12) a read-only tool survives the Coder's read_only mode
# ---------------------------------------------------------------------------

def test_history_search_survives_read_only_mode():
    from kairos.coder_modes import CoderMode, apply_mode
    out, policy = apply_mode([HistorySearchTool()], CoderMode.READ_ONLY)
    names = [getattr(t, "name", "") for t in out]
    assert "history_search" in names, policy.to_dict()
