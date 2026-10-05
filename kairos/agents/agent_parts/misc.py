"""Mixin AgentMiscMixin — split from kairos/agents/base.py."""
from __future__ import annotations
import asyncio
import json
import logging
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from kairos.llm.base import LLMConfig, LLMMessage, LLMResponse, ToolCall
from kairos.llm.errors import is_context_length_error
from kairos.llm.provider_registry import create_provider
from kairos.context_governor import (
    DEFAULT_KEEP_RECENT_TOOL_RESULTS,
    elide_old_tool_results,
    shrink_for_overflow,
)
from kairos.core.message_bus import Message, MessageBus
from kairos.sentinel import get_sentinel
from kairos.taint import (TaintTracker, classify, current_tracker, mcp_server_of,
                          release_tracker, use_tracker)
from kairos.tools.base import ToolResult
from kairos.voice_text import VOICE_REPLY_DIRECTIVE




class AgentMiscMixin:
    def set_temperature(self, t: Optional[float]) -> None:
        """Override the per-call temperature for subsequent LLM calls.

        Pass None to revert to the provider default (LLMConfig.temperature).
        Used by the Coder loop to cool down the Coder across rounds —
        high creativity at the start (exploration), low near the end
        (conservative polish).
        """
        self.temperature = t
