"""An MCP server is third-party code, so it does not inherit the host's secrets.

Muse keeps credentials away from the agent by handing it surrogates. When the
"agent" is a subprocess on the user's own machine, the equivalent is to stop
passing the whole environment down: before this change a server was started with
``{**os.environ, ...}``, which meant any server -- or anything that managed to
run inside one -- could read every key the user had ever exported.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from kairos import mcp_client as mc


@pytest.fixture()
def clean_env(monkeypatch):
    """A host environment with secrets in it, and a known allowlisted variable."""
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN",
                 "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD", "KAIROS_MCP_INHERIT_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "a" * 24)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "b" * 24)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "c" * 24)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "d" * 30)
    monkeypatch.setenv("DB_PASSWORD", "hunter2")
    monkeypatch.setenv("PATH", "C:/fake/bin")
    monkeypatch.setenv("KAIROS_PROBE_VISIBLE", "yes")


def test_the_hosts_credentials_are_withheld(clean_env):
    env = mc.child_env()
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN",
                 "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD"):
        assert name not in env, f"{name} was handed to an MCP server"


def test_the_child_still_gets_what_it_needs_to_run(clean_env):
    env = mc.child_env()
    assert env["PATH"] == "C:/fake/bin"
    # PYTHONIOENCODING is deliberately *not* asserted here: child_env() does not
    # set it, the spawn does, and a host that happens to export it made this
    # assertion pass locally while CI, which does not, failed with a KeyError.
    # The spawn path is asserted in test_the_real_spawn_uses_the_minimised_environment.


def test_a_server_keeps_the_variables_its_configuration_declares(clean_env):
    """Naming a token in mcp.yaml is an explicit decision by the user."""
    env = mc.child_env({"GITHUB_TOKEN": "user-declared", "TOOL_MODE": "fast"})
    assert env["GITHUB_TOKEN"] == "user-declared"
    assert env["TOOL_MODE"] == "fast"


def test_the_old_behaviour_is_one_variable_away(clean_env, monkeypatch):
    monkeypatch.setenv("KAIROS_MCP_INHERIT_ENV", "1")
    env = mc.child_env()
    assert "OPENAI_API_KEY" in env


def test_the_real_spawn_uses_the_minimised_environment(clean_env, monkeypatch):
    """Not just the helper: the environment the child is actually started with."""
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured.update(kwargs)
        raise OSError("stop before a real process exists")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    client = mc.StdioMcpClient(mc.McpServerConfig(name="probe", command="probe-bin"))

    with pytest.raises(mc.McpError):
        asyncio.run(client.start())

    env = captured["env"]
    assert "OPENAI_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PATH"] == "C:/fake/bin"


def test_declared_credentials_survive_the_real_spawn(clean_env, monkeypatch):
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured.update(kwargs)
        raise OSError("stop")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    config = mc.McpServerConfig(name="probe", command="probe-bin",
                                env={"GITHUB_TOKEN": "${GITHUB_TOKEN}"})
    client = mc.StdioMcpClient(config)

    with pytest.raises(mc.McpError):
        asyncio.run(client.start())

    assert captured["env"]["GITHUB_TOKEN"] == os.environ["GITHUB_TOKEN"]
