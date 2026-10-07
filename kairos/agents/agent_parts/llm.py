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


#: How many characters of the model's hidden reasoning one ``agent.thinking``
#: message carries. The full channel can run to ~24k characters on a single
#: turn and only the tail is ever displayed, so only the tail leaves the process.
AGENT_THINKING_TAIL_CHARS = 400

#: Minimum seconds between two streamed ``agent.thinking`` messages. A reasoning
#: model streams one delta per token; without a floor that is one WebSocket
#: frame (and one DB write path) per token, which swamps the socket.
AGENT_THINKING_MIN_INTERVAL_S = 0.15


def _thinking_tail(text: str, n: int = AGENT_THINKING_TAIL_CHARS) -> str:
    """The last ``n`` characters of ``text``."""
    if not text:
        return ""
    return text if len(text) <= n else text[-n:]




class AgentLLMMixin:
    @property
    def _llm_timeout_s(self) -> Optional[float]:
        """Per-call wall-clock timeout in seconds, or ``None`` for no limit.

        ``LLMConfig.timeout_s`` (provider level) governs when set: 0 or a
        negative number means "no limit" — a local model on modest hardware can
        legitimately need several minutes for one turn, and the 120s historical
        default is what made Kairos disconnect (``Client disconnected. Stopping
        generation...``) mid-answer. Unset (``None``) keeps the exact previous
        behaviour (``timeout or 60``), so a cloud provider's deadline is never
        silently lengthened or shortened.
        """
        raw = getattr(self._llm_config, "timeout_s", None)
        if raw is not None:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = None
            else:
                return None if value <= 0 else value
        return float(self._llm_config.timeout or 60)

    @property
    def _llm_timeout_label(self) -> str:
        """Human label for the per-call timeout, safe when it is unlimited."""
        seconds = self._llm_timeout_s
        return "no limit" if seconds is None else f"{seconds:.0f}s"

    def _light_tools_active(self) -> bool:
        """Whether only the core tool schemas should be advertised (task ③)."""
        from kairos.tools.light_mode import is_light_mode
        return is_light_mode(self._llm_config)

    def _prompt_budget(self) -> Optional[int]:
        """Total prompt-token ceiling for one request, or ``None`` (unset).

        An explicit provider-level ``max_prompt_tokens`` wins; otherwise a known
        ``context_window`` yields the window minus room for the answer and the
        fixed request overhead (tool schemas). ``None`` means "no pre-emptive
        trimming" — the historical behaviour — so nothing changes for a cloud
        model whose window nobody configured.
        """
        explicit = getattr(self._llm_config, "max_prompt_tokens", None)
        if explicit is not None:
            try:
                value = int(explicit)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
        window = getattr(self, "_context_window", None)
        if not window:
            return None
        try:
            window = int(window)
        except (TypeError, ValueError):
            return None
        if window <= 0:
            return None
        reserve_out = getattr(self._llm_config, "max_tokens", 0) or 4096
        # The reply must fit in the same window; never reserve more than half of
        # it, or a small window would leave no room for the prompt at all.
        reserve = min(int(reserve_out), max(512, window // 2))
        return max(1024, window - reserve)

    async def _fit_request_budget(self, messages, tool_schemas,
                                  *, task_id: str = "", turn: int = 0):
        """Trim an assembled request to the prompt budget before it is sent.

        Returns ``(messages, tool_schemas, report)``. A no-op that returns the
        inputs untouched when no budget is configured. Telemetry rides
        ``agent.progress`` so a trim is auditable and is never silent.
        """
        from kairos.context_governor import fit_to_budget
        from kairos.tools.light_mode import CORE_TOOL_NAMES

        budget = self._prompt_budget()
        if not budget:
            return messages, tool_schemas, None
        fitted, fitted_tools, report = fit_to_budget(
            messages, tool_schemas, budget,
            core_tool_names=CORE_TOOL_NAMES,
        )
        if report is None or not report.trimmed:
            return fitted, fitted_tools, report
        # Dropping whole messages can break tool-call/reply pairing; the same
        # repair the overflow path uses keeps the request provider-valid.
        sanitize = getattr(self, "_sanitize_memory", None)
        if sanitize is not None:
            fitted = sanitize(fitted)
        logger.info(
            "%s: request trimmed to the %d-token prompt budget — %s "
            "(~%d → ~%d tokens)",
            getattr(self, "agent_id", "?"), budget, report.summary(),
            report.approx_tokens_before, report.approx_tokens_after,
        )
        try:
            await self.message_bus.publish(Message(
                sender=self.agent_id,
                topic="agent.progress",
                content=(
                    f"Prompt trimmed to fit the model window "
                    f"(~{report.approx_tokens_before} → "
                    f"~{report.approx_tokens_after} tokens): {report.summary()}."
                ),
                msg_type="text",
                metadata={
                    "task_id": task_id,
                    "turn": turn,
                    "reason": "prompt_budget_trimmed",
                    "budget_tokens": budget,
                    "trimmed": {
                        "elided": report.elided,
                        "tools_dropped": report.tools_dropped,
                        "dropped": report.dropped,
                        "system_trimmed": report.system_trimmed,
                    },
                },
            ))
        except Exception:  # noqa: BLE001 - telemetry must never break a call
            logger.debug("prompt-trim telemetry publish failed", exc_info=True)
        return fitted, fitted_tools, report

    async def _publish_thinking(
        self, text: str, *, task_id: str = "", turn: int = 0,
        reasoning_chars: int = 0, transient: bool = False, done: bool = False,
    ) -> None:
        """Publish the TAIL of the model's hidden reasoning as ``agent.thinking``.

        The WebSocket/persistence layer already knew this topic
        (``api/routes/websocket.py`` AGENT_STATE_TRIGGERS,
        ``Persistence.CHAT_TOPICS``) but nothing ever published it, so the UI's
        "thinking" line had no real source. This is that source.

        ``transient=True`` marks a mid-stream hint: the UI shows it as a single
        rolling line and drops it when the answer starts, so it is not worth
        persisting (``Orchestrator._persist_message`` skips it). ``done=True``
        marks the closing hint of a stream — the answer is about to start, so
        the UI must NOT re-show the tail it just cleared.

        Invariant: the reasoning text travels on this channel ONLY. It is never
        appended to ``LLMResponse.content`` — thinking is not an answer.
        """
        tail = _thinking_tail(text)
        if not tail:
            return
        metadata = {"task_id": task_id, "turn": turn,
                    "reasoning_chars": reasoning_chars}
        if transient:
            metadata["transient"] = True
        elif done:
            metadata["thinking_done"] = True
        try:
            await self.message_bus.publish(Message(
                sender=self.agent_id,
                topic="agent.thinking",
                content=tail,
                msg_type="text",
                metadata=metadata,
            ))
        except Exception:
            logger.debug("agent.thinking publish failed", exc_info=True)

    async def _publish_complete_reasoning(self, resp, task_id: str = "",
                                          turn: int = 0) -> None:
        """Publish a NON-streaming reply's reasoning tail, when it has one.

        ``chat()`` uses ``complete()``, not ``stream()``, so the whole reasoning
        channel arrives in one piece after the call returns. There is nothing to
        throttle here — a single message is what lets the UI show what the model
        was thinking instead of a bare spinner. It is NOT marked ``done``: there
        is no earlier tail to clear, so the rolling line should show this text.
        """
        tail = getattr(resp, "reasoning_tail", "") or ""
        if not tail:
            return
        await self._publish_thinking(
            tail, task_id=task_id, turn=turn,
            reasoning_chars=int(getattr(resp, "reasoning_chars", 0) or 0),
        )

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
        reasoning_chars = 0
        reply_from_reasoning = False
        chunk_seq = 0
        # Hidden-reasoning text streamed so far, and the last moment we
        # published a (throttled) hint from it.
        reasoning_text = ""
        last_think_at = 0.0

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
            await self._publish_complete_reasoning(resp, task.id, turn_no)
            return resp

        # Iterate chunks. The provider's stream() is an AsyncIterator[str]
        # — OpenAI emits raw delta strings AND a final sentinel
        # '{"type":"tool_calls", "tool_calls":[...]}' line that we have to
        # parse out. Other providers may differ; we tolerate either.
        try:
            async for chunk in stream_ctx:
                # The stream contract is "plain text deltas plus typed JSON
                # envelopes". Only *known* envelope types are consumed;
                # anything else — including content that merely starts with
                # "{" — falls through and is treated as text, exactly as it
                # was before.
                if isinstance(chunk, str) and chunk.startswith("{"):
                    parsed = None
                    try:
                        parsed = json.loads(chunk)
                    except (json.JSONDecodeError, ValueError):
                        parsed = None
                    if isinstance(parsed, dict):
                        kind = parsed.get("type")
                        if kind == "tool_calls":
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
                        if kind == "stream_meta":
                            # What the provider only knows at the end of the
                            # stream: why it stopped, how much hidden thinking
                            # it did, and the token usage the cost ledger
                            # needs. Without this every streamed call was
                            # recorded as 0 tokens and an empty reply could
                            # not say why it was empty.
                            finish_reason = (
                                parsed.get("finish_reason") or finish_reason
                            )
                            reasoning_chars = int(
                                parsed.get("reasoning_chars") or reasoning_chars
                            )
                            reply_from_reasoning = bool(
                                parsed.get("reply_from_reasoning")
                                or reply_from_reasoning
                            )
                            stream_usage = parsed.get("usage") or {}
                            if stream_usage:
                                usage = stream_usage
                            continue
                        if kind == "reasoning":
                            # A hidden-reasoning increment (see the provider:
                            # ``reasoning_content`` is never merged into the
                            # content deltas). Accumulate it for the closing
                            # message and, throttled, publish its tail so the
                            # UI can show a live thinking line.
                            reasoning_text += parsed.get("text") or ""
                            now = time.monotonic()
                            if (reasoning_text
                                    and now - last_think_at
                                        >= AGENT_THINKING_MIN_INTERVAL_S):
                                last_think_at = now
                                await self._publish_thinking(
                                    reasoning_text,
                                    task_id=task.id, turn=turn_no,
                                    reasoning_chars=len(reasoning_text),
                                    transient=True,
                                )
                            continue
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
                await self._publish_complete_reasoning(resp, task.id, turn_no)
                return resp
            except Exception:
                # Re-raise the original stream error if complete also fails.
                raise

        # The reasoning is over. Publish a closing hint that carries the tail
        # and the total character count, so the UI can fold the rolling line
        # back into the process block (and a refresh still shows the model did
        # think). Mid-stream hints above are marked transient; this one is not.
        if reasoning_text:
            await self._publish_thinking(
                reasoning_text, task_id=task.id, turn=turn_no,
                reasoning_chars=len(reasoning_text), done=True,
            )

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
            reasoning_chars=reasoning_chars,
            reply_from_reasoning=reply_from_reasoning,
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
        """Get tool schemas for LLM function calling.

        In light mode (a small local model, or an explicit ``light_tools``) only
        the core tools' schemas are returned: the ~22-schema catalogue is the
        largest single block of a small prompt and the block that pushed a real
        request past an 8192-token window. The tools stay wired on the agent
        (an unexpected call is still dispatched); only what the model is *told*
        about is narrowed. Default mode returns every tool, unchanged.
        """
        if not self.tools:
            return None
        from kairos.tools.light_mode import is_light_mode, select_light_tools

        chosen = select_light_tools(self.tools) if is_light_mode(
            self._llm_config) else list(self.tools)
        if not chosen:
            # A model misconfigured into light mode but wired with none of the
            # core tools must not end up with an empty toolset — fall back to
            # the full list rather than silently disabling tools.
            chosen = list(self.tools)
        schemas = []
        for tool in chosen:
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
