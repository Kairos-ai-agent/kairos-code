"""R38.12 ⑦ — the capability view.

The view exists because a registry can say "26 installed" while the runtime
returns nothing, and nothing in the API contradicted it. So these tests assert
the *runtime* truth (the loader's own result), the invariant that would have
caught the bug (every file on disk parses), that secrets never leak, and that
whatever is missing is named in `problems` rather than dropped.
"""

import copy
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import extensions as ext

SECRET = "sk-do-not-echo-this-1234567890"


@pytest.fixture(autouse=True)
def _allow_bundled_defaults(monkeypatch):
    """The view is supposed to show the shipped servers; unmask them here."""
    monkeypatch.delenv("KAIROS_NO_BUNDLED_MCP", raising=False)


@pytest.fixture()
def client(monkeypatch, tmp_path: Path) -> TestClient:
    home = tmp_path / "home"
    (home / ".kairos").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(ext, "_HOME_GLOBAL_SKILLS", home / ".kairos" / "skills")

    (home / ".kairos" / "mcp.yaml").write_text(f"""
mcp_servers:
  remote:
    transport: http
    url: https://example.invalid/mcp
    headers:
      Authorization: "Bearer {SECRET}"
  ghost:
    command: kairos-no-such-binary-xyz
""", encoding="utf-8")

    app = FastAPI()
    app.include_router(ext.router)
    return TestClient(app)


def test_it_reports_the_loader_not_the_registry(client: TestClient):
    body = client.get("/extensions/capabilities").json()

    skills = body["skills"]
    # The invariant the old code broke: 36 files on disk, 36 loaded (no
    # duplicates, no unparsable file silently skipped).
    assert skills["filesOnDisk"] > 0
    assert skills["count"] == skills["filesOnDisk"], (
        f"{skills['filesOnDisk']} files on disk but {skills['count']} loaded")
    assert skills["byScope"]["bundled"] >= 30, skills["byScope"]
    assert all(item["path"] for item in skills["items"])


def test_every_entry_carries_its_scope_and_triggers(client: TestClient):
    body = client.get("/extensions/capabilities").json()
    by_name = {item["name"]: item for item in body["skills"]["items"]}
    assert "test-driven-development" in by_name
    tdd = by_name["test-driven-development"]
    assert tdd["scope"] == "bundled"
    # The merged metadata is what makes the skill fire.
    assert tdd["priority"] == 0.8
    assert tdd["when"], "the TDD trigger was lost again"
    # Highest priority first.
    priorities = [item["priority"] for item in body["skills"]["items"]]
    assert priorities == sorted(priorities, reverse=True)


def test_configured_servers_are_described_without_their_secrets(client: TestClient):
    response = client.get("/extensions/capabilities")
    body = response.json()

    assert SECRET not in response.text, "a header value leaked into the view"

    servers = {s["name"]: s for s in body["mcp"]["configured"]}
    assert servers["remote"]["transport"] == "http"
    assert servers["remote"]["needsNetwork"] is True
    assert servers["remote"]["headerNames"] == ["Authorization"]
    assert "headers" not in servers["remote"]
    # A server whose command cannot be launched is still reported — rejected,
    # with the reason, instead of vanishing the way load_configs leaves it.
    assert "ghost" in body["mcp"]["rejected"]
    assert "PATH" in body["mcp"]["rejected"]["ghost"]


def test_bundled_offline_servers_report_their_tools_without_starting(client: TestClient):
    body = client.get("/extensions/capabilities").json()
    bundled = {s["name"]: s for s in body["mcp"]["bundledServers"]}
    assert set(bundled) >= {"filesystem", "git", "sqlite", "time", "fetch"}
    assert bundled["time"]["offline"] is True
    assert "time_now" in bundled["time"]["tools"]
    assert sum(s["toolCount"] for s in bundled.values()) >= 10
    # probe=false means nothing was spawned.
    assert body["mcp"]["probed"] is False
    assert body["mcp"]["startupErrors"] == {}


def test_what_is_missing_is_named_in_problems(client: TestClient):
    body = client.get("/extensions/capabilities").json()
    joined = "\n".join(body["problems"])
    # The unresolvable command is a problem, not a silent omission.
    assert "ghost" in joined
    assert "not found on PATH" in joined
    # And so are the skills whose upstream source is gone.
    assert "source-missing" in joined


def test_native_tools_are_listed(client: TestClient):
    body = client.get("/extensions/capabilities").json()
    assert body["nativeTools"], "the agent's own tools should be listed"
    assert any("terminal" in n.lower() for n in body["nativeTools"])


def test_a_broken_mcp_yaml_does_not_500_the_view(client: TestClient, tmp_path: Path):
    (Path.home() / ".kairos" / "mcp.yaml").write_text(
        "mcp_servers: [this, is, not, a, mapping]\n", encoding="utf-8")
    response = client.get("/extensions/capabilities")
    assert response.status_code == 200
    assert response.json()["skills"]["count"] > 0


def test_an_unknown_project_is_reported_not_guessed(client: TestClient):
    body = client.get("/extensions/capabilities?project_id=nope-xyz").json()
    assert body["scanned"]["projectRoot"] is None
    assert any("nope-xyz" in p for p in body["problems"])
    # …and the other scopes still answered.
    assert body["skills"]["count"] > 0
