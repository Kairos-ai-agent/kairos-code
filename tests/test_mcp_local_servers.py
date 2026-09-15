"""The bundled MCP servers: real tools, no network, no npx.

Covers the launch args (source *and* frozen), the four servers' tools against
real fixtures (a git repo, a sqlite database), the read-only guard on sqlite, and
that the MCP SDK actually accepts the constructed server.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from kairos import mcp_local_servers as mls


# ---------------------------------------------------------------------------
# launch arguments
# ---------------------------------------------------------------------------

def test_a_source_install_runs_the_module(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    cfg = mls.bundled_mcp_command("git", Path("/repo"))
    assert cfg["command"] == sys.executable
    assert cfg["args"][:4] == ["-m", "kairos.mcp_local_servers", "--server", "git"]
    assert cfg["transport"] == "stdio"
    assert cfg["args"][-2:] == ["--root", str(Path("/repo").resolve())]


def test_the_filesystem_server_keeps_its_own_module_and_positional_root(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    cfg = mls.bundled_mcp_command("filesystem", Path("/work"))
    assert cfg["args"][:2] == ["-m", "kairos.mcp_filesystem_server"]
    assert cfg["args"][-1] == str(Path("/work").resolve())


def test_a_frozen_build_asks_the_executable_instead_of_using_dash_m(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cfg = mls.bundled_mcp_command("sqlite", Path("/db.sqlite"))
    assert cfg["command"] == sys.executable
    assert "-m" not in cfg["args"], "a frozen build cannot run `-m`"
    assert cfg["args"] == ["--mcp-serve", "sqlite"]


def test_unknown_servers_are_rejected():
    with pytest.raises(ValueError):
        mls.bundled_mcp_command("not-a-server")
    assert mls.resolve_bundled("not-a-server") is None
    assert mls.resolve_bundled("time")["transport"] == "stdio"


def test_every_bundled_server_has_a_tool_table():
    for name in mls.BUNDLED_SERVERS:
        if name == "filesystem":      # served by kairos.mcp_filesystem_server
            continue
        table = mls.tools_for(name)
        assert table, f"{name} exposes no tools"
        for spec in table.values():
            assert spec["name"] and spec["description"]
            assert spec["inputSchema"]["type"] == "object"


# ---------------------------------------------------------------------------
# git (offline)
# ---------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", *ident, "init", "-q"], cwd=d, check=True,
                   capture_output=True)
    (d / "a.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", *ident, "add", "-A"], cwd=d, check=True,
                   capture_output=True)
    subprocess.run(["git", *ident, "commit", "-qm", "first commit"], cwd=d,
                   check=True, capture_output=True)
    (d / "b.txt").write_text("second\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True, capture_output=True)
    return d


def test_the_git_server_reports_real_state(repo):
    table = mls.tools_for("git", repo)
    status = table["git_status"]["handler"]()
    assert "b.txt" in status, status            # staged, so it shows up
    log = table["git_log"]["handler"]()
    assert "first commit" in log, log
    assert table["git_branches"]["handler"]().strip()


def test_the_git_server_survives_a_directory_that_is_not_a_repository(tmp_path):
    table = mls.tools_for("git", tmp_path)
    out = table["git_status"]["handler"]()          # must not raise
    assert isinstance(out, str) and out


# ---------------------------------------------------------------------------
# sqlite (offline, read-only)
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    path = tmp_path / "x.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO notes (body) VALUES ('first'), ('second')")
    conn.commit()
    conn.close()
    return path


def test_the_sqlite_server_lists_reads_and_refuses_writes(db):
    table = mls.tools_for("sqlite", db)
    assert "notes" in table["sqlite_tables"]["handler"]()
    assert "CREATE TABLE notes" in table["sqlite_schema"]["handler"]("notes")

    out = table["sqlite_query"]["handler"]("SELECT id, body FROM notes ORDER BY id")
    assert "first" in out and "second" in out
    assert out.splitlines()[0].startswith("id")

    # Read-only by construction.
    refusal = table["sqlite_query"]["handler"]("DELETE FROM notes")
    assert refusal.startswith("refused")
    refusal = table["sqlite_query"]["handler"]("SELECT 1; DROP TABLE notes")
    assert refusal.startswith("refused")
    # ... and the data is still there.
    assert "second" in table["sqlite_query"]["handler"]("SELECT body FROM notes")


def test_the_sqlite_server_caps_rows(db):
    table = mls.tools_for("sqlite", db)
    out = table["sqlite_query"]["handler"]("SELECT * FROM notes", 1)
    assert len([ln for ln in out.splitlines() if ln.strip()]) == 2   # header + 1


# ---------------------------------------------------------------------------
# time / fetch
# ---------------------------------------------------------------------------

def test_the_time_server_answers_in_a_machine_readable_shape():
    table = mls.tools_for("time")
    payload = json.loads(table["time_now"]["handler"]())
    assert {"iso", "utc", "unix", "weekday"} <= set(payload)
    parsed = json.loads(table["time_parse"]["handler"]("2026-09-15T12:00:00Z"))
    assert parsed["iso"].startswith("2026-09-15")
    assert table["time_parse"]["handler"]("not a date").startswith("could not parse")


def test_the_fetch_server_refuses_anything_but_http():
    table = mls.tools_for("fetch")
    assert table["http_get"]["handler"]("file:///etc/passwd").startswith("refused")
    assert table["http_get"]["handler"]("ftp://example.com").startswith("refused")


# ---------------------------------------------------------------------------
# the MCP layer itself
# ---------------------------------------------------------------------------

def test_the_sdk_accepts_every_constructed_server(tmp_path):
    pytest.importorskip("mcp")
    for name in ("git", "sqlite", "time", "fetch"):
        server, names = mls.build_mcp_server(name, tmp_path)
        assert server is not None
        assert names and all(isinstance(n, str) for n in names)


def test_the_cli_parses_each_server_name():
    for name in ("git", "sqlite", "time", "fetch"):
        # argparse exits on an unknown name, so this is a real check.
        with pytest.raises(SystemExit):
            mls.main(["--server", name, "--help"])
