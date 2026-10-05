"""Mixin AgentLLMMixin — split from kairos/agents/base.py."""
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

logger = logging.getLogger(__name__)




class AgentLLMMixin:
    @property
    def _llm_timeout_s(self) -> float:
        return float(self._llm_config.timeout or 60)

    async def _stream_complete(self, messages, tools, task, turn_no: int):
        """Call LLM with streaming. Each content delta is published as a
        `stream.chunk` event so the UI can render a typewriter effect.

        Falls back to non-streaming `complete()` if the provider raises
        any streaming-specific error — better a working non-stream than a
        crashed tool call.
        """
        accumulated_text = ""
        accumulated_tool_calls = []
        finish_reason = ""
        model_name = self._llm_config.model
        usage = {}
        chunk_seq = 0

        try:
            with self._traced_llm_call(messages, tools) as _trace_span:
                stream_ctx = self._llm.stream(messages, tools=tools, temperature=self.temperature)
        except Exception as e:
            logger.debug("stream() unavailable, falling back to complete(): %s", e)
            with self._traced_llm_call(messages, tools) as _trace_span:
                resp = await self._llm.complete(messages, tools=tools, temperature=self.temperature)
            _trace_span.set_output(
                content=resp.content,
                prompt_tokens=resp.usage.get("prompt_tokens", 0),
                completion_tokens=resp.usage.get("completion_tokens", 0),
                finish_reason=resp.finish_reason,
            )
            return resp

        # Iterate chunks. The provider's stream() is an AsyncIterator[str]
        # — OpenAI emits raw delta strings AND a final sentinel
        # '{"type":"tool_calls", "tool_calls":[...]}' line that we have to
        # parse out. Other providers may differ; we tolerate either.
        try:
            async for chunk in stream_ctx:
                if isinstance(chunk, str) and chunk.startswith("{"):
                    # Final tool_calls payload from OpenAI streaming.
                    try:
                        parsed = json.loads(chunk)
                        if isinstance(parsed, dict) and parsed.get("type") == "tool_calls":
                            for tc in parsed.get("tool_calls") or []:
                                args = tc.get("arguments")
                                if isinstance(args, str):
                                    try:
                                        args = json.loads(args)
                                    except (json.JSONDecodeError, TypeError):
                                        pass
                                accumulated_tool_calls.append(ToolCall(
                                    id=tc.get("id", ""),
                                    name=tc.get("name", ""),
                                    arguments=args if args is not None else "",
                                ))
                            continue
                    except (json.JSONDecodeError, ValueError):
                        pass
                # Plain text delta — emit as stream.chunk and accumulate.
                accumulated_text += chunk
                chunk_seq += 1
                # Publish every delta. Throttling is the UI's job.
                await self.message_bus.publish(Message(
                    sender=self.agent_id,
                    topic="stream.chunk",
                    content=chunk,
                    msg_type="text",
                    metadata={
                        "task_id": task.id,
                        "turn": turn_no,
                        "seq": chunk_seq,
                        "accumulated_len": len(accumulated_text),
                    },
                ))
        except Exception as e:
            logger.debug("Stream interrupted mid-way (%s); falling back", e)
            # Fall back to non-stream so we still get a final answer.
            try:
                with self._traced_llm_call(messages, tools) as _trace_span:
                    resp = await self._llm.complete(messages, tools=tools, temperature=self.temperature)
                _trace_span.set_output(
                    content=resp.content,
                    prompt_tokens=resp.usage.get("prompt_tokens", 0),
                    completion_tokens=resp.usage.get("completion_tokens", 0),
                    finish_reason=resp.finish_reason,
                )
                return resp
            except Exception:
                # Re-raise the original stream error if complete also fails.
                raise

        # Record the streaming call's output on the trace span
        # (started at the top of _stream_complete). usage is populated
        # by the underlying provider if it tracks it; otherwise empty.
        try:
            _trace_span.set_output(
                content=accumulated_text[:500] if accumulated_text else "",
                prompt_tokens=int(usage.get("prompt_tokens", 0)) if usage else 0,
                completion_tokens=int(usage.get("completion_tokens", 0)) if usage else 0,
                finish_reason=finish_reason,
            )
        except Exception:
            pass
        return LLMResponse(
            content=accumulated_text,
            model=model_name,
            usage=usage,
            finish_reason=finish_reason,
            tool_calls=accumulated_tool_calls if accumulated_tool_calls else None,
        )

    def _traced_llm_call(self, messages, tools=None):
        """Return a context manager that wraps an LLM call in a tracer
        span. The default tracer is a no-op (in-memory) so this is
        safe to call from any code path; production users wire
        Langfuse / OTLP via ``init_default_tracer()``.

        Usage:
            with self._traced_llm_call(messages, tools) as span:
                response = await self._llm.complete(messages, tools=tools)
                span.set_output(...)
        """
        from kairos.observability import get_default_tracer
        tracer = get_default_tracer()
        # Build a list-shaped copy for the message_count attribute
        msgs_list = list(messages) if messages else []
        return tracer.llm_call(
            model=getattr(self._llm_config, "model", "unknown"),
            provider=getattr(self._llm_config, "provider", ""),
            messages=msgs_list,
        )

    def _get_tool_schemas(self) -> Optional[List[dict]]:
        """Get tool schemas for LLM function calling."""
        if not self.tools:
            return None
        schemas = []
        for tool in self.tools:
            schema = tool.to_schema()
            schemas.append({
                "name": schema["name"],
                "description": schema.get("description", ""),
                "parameters": schema.get("parameters", {
                    "type": "object",
                    "properties": {},
                }),
            })
        return schemas
