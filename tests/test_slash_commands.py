"""The composer may only offer commands the backend actually runs.

``/slash`` had handlers but no caller: nothing in the UI knew the commands
existed. Surfacing them is only worth doing if the advertised list cannot drift
away from the dispatch chain — hence the source-reading test below.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from api.routes.p2_features import (
    CHAT_SLASH_HELP,
    slash_command,
    slash_command_list,
)

MODULE = (Path(__file__).resolve().parent.parent
          / "api" / "routes" / "p2_features.py")


def test_the_advertised_commands_are_exactly_the_handled_ones():
    """Every advertised name has a branch, and every branch is advertised."""
    source = MODULE.read_text(encoding="utf-8", errors="replace")
    handled = set(re.findall(r'if cmd == "(\w+)"', source))

    assert handled, "the dispatch chain moved — update this test"
    assert handled == set(CHAT_SLASH_HELP), (
        f"advertised {sorted(CHAT_SLASH_HELP)} but handled {sorted(handled)}")


def test_the_list_endpoint_returns_names_with_help():
    payload = asyncio.run(slash_command_list("p1"))
    commands = payload["commands"]

    assert {c["name"] for c in commands} == set(CHAT_SLASH_HELP)
    assert all(c.get("help") for c in commands)


def test_an_informational_command_does_not_continue_into_chat():
    """`/compact` answers a question; the literal text is not a message for
    the agent, so it must not be sent on afterwards."""
    for text in ("/compact", "/verify"):
        out = asyncio.run(slash_command("p1", {"text": text}))
        assert out["reply"].strip()
        assert out["continue_chat"] is False


def test_an_unknown_command_says_so_and_gets_out_of_the_way():
    """`/etc/hosts` typed as a path must still reach the agent untouched."""
    out = asyncio.run(slash_command("p1", {"text": "/nope"}))
    assert "Unknown command" in out["reply"]
    assert out["continue_chat"] is True


def test_a_goal_query_is_read_only():
    out = asyncio.run(slash_command("p1", {"text": "/goal"}))
    assert out["reply"].startswith("Current goal:")
