"""Shared utilities for LLM providers."""

from __future__ import annotations

import json
from typing import List

from kairos.llm.base import LLMMessage

def format_messages_for_openai(messages: List[LLMMessage]) -> List[dict]:
    """Convert LLMMessage list to OpenAI-compatible format with tool_calls support."""
    result = []
    for msg in messages:
        entry = {"role": msg.role}

        # Content
        if msg.content:
            entry["content"] = msg.content

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
            if "content" not in entry:
                entry["content"] = ""

        result.append(entry)
    return result
