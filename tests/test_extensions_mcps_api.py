"""R38.11 — the MCP registry tells the truth about what will run.

Before this, every entry advertised an upstream command (``npx -y ...`` /
``uvx ...``) that fetches a package from npm or PyPI at first use — including
the servers this install could run itself. Now the five offline-capable ones
resolve to the local command, and the API says which is which.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from api.app import app
    return TestClient(app)


@pytest.fixture
def by_name(client):
    data = client.get("/api/extensions/mcps").json()
    assert data["total"] >= 20
    return {s["name"]: s for s in data["servers"]}


def test_bundled_servers_resolve_to_a_local_command(by_name):
    for name in ("filesystem", "git", "sqlite", "time", "fetch"):
        entry = by_name[name]
        assert entry["bundled"] is True, name
        joined = " ".join([entry["command"], *entry["args"]])
        assert ("mcp_local_servers" in joined
                or "mcp_filesystem_server" in joined), joined
        # The whole point: nothing has to be fetched to start it.
        assert "npx" not in joined and "uvx" not in joined, joined
        assert entry["command"], name


def test_offline_servers_say_so(by_name):
    for name in ("filesystem", "git", "sqlite", "time"):
        assert by_name[name]["needs_network"] is False, name
    # fetch exists to reach the network, so it is honest about needing it.
    assert by_name["fetch"]["needs_network"] is True


def test_upstream_servers_are_left_alone(by_name):
    entry = by_name["github"]
    assert entry["bundled"] is False
    assert entry["needs_network"] is True
    assert entry["command"] in ("npx", "uvx"), entry["command"]
    assert entry["source"]                      # provenance is still shown


def test_a_bundled_command_is_launchable_as_described(by_name, tmp_path, monkeypatch):
    """The advertised argv must be the argv ``bundled_mcp_command`` produces."""
    import sys

    from kairos.mcp_local_servers import bundled_mcp_command

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    entry = by_name["git"]
    expected = bundled_mcp_command("git")
    assert entry["command"] == expected["command"]
    assert entry["args"] == expected["args"]
