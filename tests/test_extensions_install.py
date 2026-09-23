"""Installing a market MCP server: one call, one key, and no surprises.

The registry already lists the servers; installing one used to mean copying a
stanza into ``~/.kairos/mcp.yaml`` by hand. These tests are about the two things
that makes risky — a file the user already owns, and a write that can be
interrupted — and about the one thing that makes it real: the endpoint that
starts the server and makes it answer the handshake.

Everything runs against a throwaway ``~/.kairos`` (the ``home`` fixture); the
developer's own config is never opened, let alone written.
"""

import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from kairos import extensions_install as ei

MARKER = "# my own config, keep me"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway ``~/.kairos`` — the real home directory is never touched."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    return fake_home / ".kairos"


@pytest.fixture
def client():
    from api.app import app
    return TestClient(app)


def _servers(path: Path) -> dict:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("mcp_servers") or {}


# ---------------------------------------------------------------------------
# install_mcp — the file it writes
# ---------------------------------------------------------------------------


def test_install_creates_the_file_with_exactly_that_server(home):
    target = home / "mcp.yaml"
    assert not target.exists()

    result = ei.install_mcp("time", user_dir=home)

    assert result["ok"] is True
    assert result["changed"] is True
    assert result["path"] == str(target)
    assert result["entry"]["name"] == "time"          # the registry entry
    assert target.exists()
    assert _servers(target) == {"time": {"bundled": "time", "enabled": True}}


def test_installing_twice_changes_nothing(home):
    ei.install_mcp("time", user_dir=home)
    target = home / "mcp.yaml"
    before = target.read_bytes()

    again = ei.install_mcp("time", user_dir=home)

    assert again["ok"] is True
    assert again["changed"] is False
    assert target.read_bytes() == before  # not even rewritten identically


def test_install_keeps_the_users_own_servers_and_comments(home):
    """The whole point: this is not a template install, it is an edit."""
    home.mkdir(parents=True)
    target = home / "mcp.yaml"
    target.write_text(
        f"{MARKER}\n"
        "mcp_servers:\n"
        "  mine:\n"
        "    command: /usr/bin/python3\n"
        "    args: [\"-m\", \"mine\"]\n"
        "\n"
        "# a note about the next thing\n",
        encoding="utf-8")

    ei.install_mcp("time", user_dir=home)

    text = target.read_text(encoding="utf-8")
    assert MARKER in text                     # comments survive
    assert "# a note about the next thing" in text
    servers = _servers(target)
    assert set(servers) == {"mine", "time"}   # nothing was replaced
    assert servers["mine"]["args"] == ["-m", "mine"]


def test_install_replaces_only_its_own_key(home):
    home.mkdir(parents=True)
    target = home / "mcp.yaml"
    target.write_text(
        "mcp_servers:\n"
        "  time:\n"
        "    command: /stale/old-command\n"
        "  other:\n"
        "    command: /usr/bin/env\n",
        encoding="utf-8")

    result = ei.install_mcp("time", user_dir=home)

    assert result["changed"] is True
    servers = _servers(target)
    assert servers["time"] == {"bundled": "time", "enabled": True}
    assert servers["other"] == {"command": "/usr/bin/env"}


def test_install_keeps_a_crlf_file_crlf(home):
    """The write must not silently change the user's line endings."""
    home.mkdir(parents=True)
    target = home / "mcp.yaml"
    target.write_bytes(b"# crlf file\r\nmcp_servers:\r\n  mine:\r\n    command: C:/x/env\r\n")

    ei.install_mcp("time", user_dir=home)

    raw = target.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n"), raw
    assert b"bundled: time" in raw


def test_install_of_an_unknown_server_refuses_and_writes_nothing(home):
    with pytest.raises(ValueError) as excinfo:
        ei.install_mcp("definitely-not-a-server", user_dir=home)
    assert "registry" in str(excinfo.value)
    assert not (home / "mcp.yaml").exists()


@pytest.mark.parametrize("name", ["", "  ", "with: colon", "../escape", "a\nb: 1"])
def test_only_plain_server_names_are_accepted(name, home):
    with pytest.raises(ValueError):
        ei.install_mcp(name, user_dir=home)


def test_install_supports_the_project_layer(tmp_path, home):
    project = tmp_path / "project"
    result = ei.install_mcp("time", scope="project", project_path=project)
    assert result["path"] == str(project / ".kairos" / "mcp.yaml")
    assert _servers(project / ".kairos" / "mcp.yaml")["time"]["bundled"] == "time"

    with pytest.raises(ValueError):
        ei.install_mcp("time", scope="project")  # no project_path
    with pytest.raises(ValueError):
        ei.install_mcp("time", scope="everywhere", user_dir=home)


def test_upstream_servers_keep_their_command_and_reference_their_keys(home):
    ei.install_mcp("github", user_dir=home)
    body = _servers(home / "mcp.yaml")["github"]
    assert body["command"] == "npx"
    assert body["args"][-1] == "@modelcontextprotocol/server-github"
    # A credential is never written literally — only its ${VAR} reference.
    assert body["env"] == {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}"}


def test_remote_servers_are_written_as_a_url_and_a_header(home):
    ei.install_mcp("greptile", user_dir=home)
    body = _servers(home / "mcp.yaml")["greptile"]
    assert body["transport"] == "http"
    assert body["url"] == "https://api.greptile.com/mcp"
    assert list(body["headers"]) == ["Authorization"]


def test_what_is_written_is_what_the_mcp_loader_reads(home):
    """The file is a config, not text: the runtime's own loader must accept it."""
    from kairos.mcp_client import load_configs

    ei.install_mcp("time", user_dir=home)

    configs = load_configs(user_dir=home, include_bundled=False)
    assert set(configs) == {"time"}
    config = configs["time"]
    assert config.enabled is True
    assert config.command and (os.path.isabs(config.command) or config.command)
    assert "time" in " ".join([config.command, *config.args])


# ---------------------------------------------------------------------------
# uninstall_mcp
# ---------------------------------------------------------------------------


def test_uninstall_removes_only_the_target_key(home):
    home.mkdir(parents=True)
    target = home / "mcp.yaml"
    target.write_text(
        f"{MARKER}\n"
        "mcp_servers:\n"
        "  mine:\n"
        "    command: /usr/bin/python3\n"
        "  time:\n"
        "    bundled: time\n"
        "  fetch:\n"
        "    bundled: fetch\n",
        encoding="utf-8")

    result = ei.uninstall_mcp("time", user_dir=home)

    assert result["ok"] is True
    assert result["changed"] is True
    assert result["entry"] == {"bundled": "time"}
    text = target.read_text(encoding="utf-8")
    assert MARKER in text
    assert set(_servers(target)) == {"mine", "fetch"}


def test_uninstalling_twice_is_a_no_op(home):
    ei.install_mcp("time", user_dir=home)
    assert ei.uninstall_mcp("time", user_dir=home)["changed"] is True

    second = ei.uninstall_mcp("time", user_dir=home)
    assert second == {"ok": True, "changed": False,
                      "path": str(home / "mcp.yaml"), "entry": {}}


def test_uninstalling_from_a_layer_with_no_file_creates_nothing(home):
    result = ei.uninstall_mcp("time", user_dir=home)
    assert result["changed"] is False
    assert not (home / "mcp.yaml").exists()


# ---------------------------------------------------------------------------
# the write itself
# ---------------------------------------------------------------------------


def test_a_failed_write_leaves_the_original_file_untouched(home, monkeypatch):
    """Atomic means atomic: an interrupted install is a no-op, not a mess."""
    home.mkdir(parents=True)
    target = home / "mcp.yaml"
    target.write_text("mcp_servers:\n  mine:\n    command: /usr/bin/env\n",
                      encoding="utf-8")
    before = target.read_bytes()

    def boom(*_args, **_kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(ei.os, "replace", boom)
    with pytest.raises(OSError):
        ei.install_mcp("time", user_dir=home)

    assert target.read_bytes() == before, "the original config was damaged"
    assert [p.name for p in home.iterdir()] == ["mcp.yaml"], "a temp file was left behind"


def test_the_new_file_is_written_in_one_rename(home, monkeypatch):
    """Proof the write goes through os.replace instead of opening the target."""
    seen = []
    real_replace = ei.os.replace

    def spy(src, dst):
        seen.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(ei.os, "replace", spy)
    ei.install_mcp("time", user_dir=home)

    assert len(seen) == 1
    src, dst = seen[0]
    assert dst == str(home / "mcp.yaml")
    assert Path(src).parent == home


# ---------------------------------------------------------------------------
# the API
# ---------------------------------------------------------------------------


def test_installed_lists_layer_and_which_servers_really_will_run(client, home, tmp_path,
                                                                 monkeypatch):
    import api.routes.extensions as ext

    project = tmp_path / "project"
    monkeypatch.setattr(ext, "_project_root", lambda pid: project if pid else None)
    home.mkdir(parents=True)
    (home / "mcp.yaml").write_text(
        "mcp_servers:\n"
        "  time:\n"
        "    bundled: time\n"
        "  ghost:\n"
        "    command: definitely-not-a-real-binary-7f3\n"
        "  turned-off:\n"
        "    command: /usr/bin/env\n"
        "    enabled: false\n",
        encoding="utf-8")
    (project / ".kairos").mkdir(parents=True)
    (project / ".kairos" / "mcp.yaml").write_text(
        "mcp_servers:\n  fetch:\n    bundled: fetch\n", encoding="utf-8")

    data = client.get("/api/extensions/mcp/installed",
                      params={"project_id": "p1"}).json()

    by_name = {s["name"]: s for s in data["servers"]}
    assert set(by_name) == {"time", "ghost", "turned-off", "fetch"}
    assert by_name["time"]["layer"] == "user" and by_name["time"]["will_run"] is True
    assert by_name["fetch"]["layer"] == "project" and by_name["fetch"]["will_run"] is True
    # In the config, enabled, and still not going to start — that is the case
    # this endpoint exists to make visible.
    assert by_name["ghost"]["will_run"] is False
    assert "PATH" in by_name["ghost"]["problem"]
    assert by_name["turned-off"]["will_run"] is False
    assert by_name["turned-off"]["problem"] == "disabled"
    # Registry membership is separate from being installed.
    assert by_name["ghost"]["in_registry"] is False
    assert by_name["time"]["in_registry"] is True
    assert data["will_run"] == 2 and data["total"] == 4
    assert data["layers"] == {"bundled": 0, "user": 3, "project": 1}
    assert data["paths"]["user"].endswith("mcp.yaml")


def test_a_server_named_like_a_yaml_boolean_does_not_break_the_view(client, home):
    """`off`/`on`/`yes` are booleans to YAML — the view must survive one."""
    home.mkdir(parents=True)
    (home / "mcp.yaml").write_text(
        "mcp_servers:\n  off:\n    command: /usr/bin/env\n", encoding="utf-8")

    response = client.get("/api/extensions/mcp/installed")

    assert response.status_code == 200
    assert response.json()["total"] == 1, response.json()


def test_install_endpoint_writes_the_layer_and_returns_the_effective_config(client, home):
    response = client.post("/api/extensions/mcp/install", json={"name": "time"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["changed"] is True
    assert body["scope"] == "user"
    assert body["path"] == str(home / "mcp.yaml")
    assert body["entry"]["name"] == "time"
    assert body["config"] == {"bundled": "time", "enabled": True}
    assert body["server"]["layer"] == "user"
    assert body["server"]["will_run"] is True
    assert _servers(home / "mcp.yaml")["time"]["bundled"] == "time"

    # A second install is a no-op, and the endpoint says so.
    assert client.post("/api/extensions/mcp/install",
                       json={"name": "time"}).json()["changed"] is False


def test_install_endpoint_supports_the_project_scope(client, home, tmp_path, monkeypatch):
    import api.routes.extensions as ext

    project = tmp_path / "project"
    monkeypatch.setattr(ext, "_project_root", lambda pid: project)

    response = client.post("/api/extensions/mcp/install",
                           json={"name": "time", "scope": "project", "project_id": "p1"})
    assert response.status_code == 200
    assert response.json()["path"] == str(project / ".kairos" / "mcp.yaml")
    assert _servers(project / ".kairos" / "mcp.yaml")["time"]["bundled"] == "time"


def test_uninstall_endpoint_round_trips(client, home):
    client.post("/api/extensions/mcp/install", json={"name": "time"})
    gone = client.post("/api/extensions/mcp/uninstall", json={"name": "time"})
    assert gone.status_code == 200
    assert gone.json()["changed"] is True
    assert gone.json()["entry"] == {"bundled": "time", "enabled": True}
    assert "time" not in _servers(home / "mcp.yaml")
    assert client.post("/api/extensions/mcp/uninstall",
                       json={"name": "time"}).json()["changed"] is False


def test_the_endpoints_refuse_what_they_cannot_do(client, home):
    unknown = client.post("/api/extensions/mcp/install", json={"name": "no-such-server"})
    assert unknown.status_code == 400
    assert "registry" in unknown.json()["detail"]

    assert client.post("/api/extensions/mcp/install",
                       json={"name": "time", "scope": "everywhere"}).status_code == 400
    # A project scope without a resolvable project is refused, not guessed at.
    assert client.post("/api/extensions/mcp/install",
                       json={"name": "time", "scope": "project"}).status_code == 400
    assert not (home / "mcp.yaml").exists()


def test_probe_of_an_uninstalled_server_says_so_instead_of_starting_it(client, home):
    response = client.post("/api/extensions/mcp/probe", json={"name": "time"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["tools"] == []
    assert "install it first" in body["error"]


@pytest.mark.timeout(180)
def test_probe_really_starts_the_server_and_gets_its_tools(client, home):
    """The endpoint that matters: a real subprocess, a real MCP handshake."""
    assert client.post("/api/extensions/mcp/install",
                       json={"name": "time"}).status_code == 200

    response = client.post("/api/extensions/mcp/probe", json={"name": "time"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True, body
    assert body["error"] is None
    assert body["tools"], body
    # The bundled `time` server's own tools, reported by the server itself.
    assert {"time_now", "time_parse"} <= set(body["tools"])
    assert body["ms"] >= 0


@pytest.mark.timeout(180)
def test_probe_can_target_one_layer_and_reports_a_broken_command(client, home):
    home.mkdir(parents=True)
    (home / "mcp.yaml").write_text(
        "mcp_servers:\n"
        "  broken:\n"
        "    command: definitely-not-a-real-binary-7f3\n"
        "    args: [\"--nope\"]\n",
        encoding="utf-8")

    response = client.post("/api/extensions/mcp/probe",
                           json={"name": "broken", "scope": "user"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["tools"] == []
    assert body["error"], "a failure without a reason is not an answer"
    assert "definitely-not-a-real-binary-7f3" in body["error"]


def test_probe_refuses_an_unknown_scope(client, home):
    assert client.post("/api/extensions/mcp/probe",
                       json={"name": "time", "scope": "nowhere"}).status_code == 400
