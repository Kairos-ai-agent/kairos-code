"""Shared utilities for LLM providers."""

from __future__ import annotations

import json
from typing import List

from kairos.llm.base import LLMMessage

def format_messages_for_openai(messages: List[LLMMessage]) -> List[dict]:
    """Convert LLMMessage list to OpenAI-compatible format with tool_calls support.

    R38.6.4: every entry now ALWAYS has a ``content`` field (even if
    empty / null). The previous ``if msg.content:`` skip dropped the
    field entirely for tool role messages that had a tool_call_id
    but no content, which the OpenAI API rejects with
    ``missing field 'content'``. Tool role messages that carry only
    a tool result ID are still valid — they just need the content
    key present (OpenAI accepts ``""`` or ``None``).
    """
    result = []
    for msg in messages:
        entry: dict = {"role": msg.role}

        # Content — always include the key. Empty string or None is
        # accepted by the OpenAI API; missing key is not.
        if msg.content is not None and msg.content != "":
            entry["content"] = msg.content
        else:
            # Use empty string for the no-content case. OpenAI
            # rejects missing key but accepts empty string.
            entry["content"] = ""

        # Name (for tool role identification)
        if msg.name:
            entry["name"] = msg.name

        # tool_call_id (tool role references which tool_call)
        if msg.tool_call_id:
            entry["tool_call_id"] = msg.tool_call_id

        # tool_calls (assistant role's tool invocations)
        if msg.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": (
                            tc.arguments
                            if isinstance(tc.arguments, str)
                            else json.dumps(tc.arguments, ensure_ascii=False)
                        ),
                    },
                }
                for tc in msg.tool_calls
            ]
            # OpenAI requires content when tool_calls present (can be empty string)
            if "content" not in entry or not entry["content"]:
                entry["content"] = ""

        result.append(entry)
    return result
