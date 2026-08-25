"""Agent trace recorder — structured per-turn record of an agent run.

A "trace" is the ground truth for "what did this agent actually do?".
For each turn we record:

* the messages sent to the LLM (the prompt)
* the LLM's response (text + tool calls)
* each tool invocation (name, args, output, success, duration)
* timing + token usage

Traces are written to a JSONL file under `data/traces/{project_id}/{session_id}.jsonl`
so they survive restarts. The Trace API reads them back as a single
`Trace` object for the UI's "Trace viewer" tab.

Design notes:

* `TraceRecorder` is the **collector** — agents call `record_turn`,
  `record_tool`, `record_prompt`. It is thread-safe so a worker
  thread can record events while the agent's main coroutine is
  awaiting the LLM.
* `Trace` is the **read model** — `load_trace(project_id, session_id)`
  parses the JSONL file into a list of `TraceEvent`s with the
  correct ordering.
* Events are append-only and immutable. We never rewrite a turn; if
  the agent re-tries with a different prompt, that's a new event.
* The recorder is opt-in: agents that don't import / create one are
  unaffected. The default agent code path in `agents/base.py` does
  not yet auto-wire a recorder; the API and CLI do, so when running
  through the API you get traces for free.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


# Where traces are persisted. Override via KAIROS_TRACE_DIR for tests.
def _default_trace_dir() -> Path:
    env = os.environ.get("KAIROS_TRACE_DIR")
    if env:
        return Path(env)
    return Path("data/traces")


TRACE_DIR = _default_trace_dir()


class EventKind(str, Enum):
    """Type of a single trace event.

    SESSION_START  — agent began a new session (one per Trace)
    SESSION_END    — agent finished a session
    PROMPT         — messages sent to the LLM
    COMPLETION     — raw LLM response (text + tool calls)
    TOOL_CALL      — a tool was invoked (name + args)
    TOOL_RESULT    — tool returned (output + success + duration)
    SUMMARY        — periodic memory summary (from retained reasoning)
    GUARDRAIL      — output guardrail verdict
    ERROR          — an exception was caught
    """
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    PROMPT = "prompt"
    COMPLETION = "completion"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SUMMARY = "summary"
    GUARDRAIL = "guardrail"
    ERROR = "error"


@dataclass
class TraceEvent:
    """A single event in a trace.

    `turn` is the agent's logical turn number (1-based). Multiple
    events can share a turn (PROMPT, COMPLETION, TOOL_CALL,
    TOOL_RESULT all happen within turn 3, for example).

    `seq` is a monotonic sequence number assigned at record-time so
    we can replay events in the order they were emitted even across
    threads.
    """
    kind: EventKind
    turn: int
    timestamp: float
    payload: Dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    agent_id: str = ""
    session_id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TraceEvent":
        d = dict(d)
        d["kind"] = EventKind(d["kind"])
        return cls(**d)


@dataclass
class Trace:
    """A complete trace for one agent session."""
    project_id: str
    session_id: str
    agent_id: str
    events: List[TraceEvent] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    def turns(self) -> List[int]:
        """Return the sorted list of unique turn numbers."""
        return sorted({e.turn for e in self.events if e.turn > 0})

    def events_for_turn(self, turn: int) -> List[TraceEvent]:
        return [e for e in self.events if e.turn == turn]

    def total_tokens(self) -> Dict[str, int]:
        """Sum prompt/completion tokens across all COMPLETION events."""
        prompt = 0
        completion = 0
        for e in self.events:
            if e.kind == EventKind.COMPLETION:
                usage = e.payload.get("usage", {}) or {}
                prompt += int(usage.get("prompt_tokens", 0) or 0)
                completion += int(usage.get("completion_tokens", 0) or 0)
        return {"prompt_tokens": prompt, "completion_tokens": completion,
                "total_tokens": prompt + completion}

    def tool_calls(self) -> List[TraceEvent]:
        return [e for e in self.events if e.kind == EventKind.TOOL_CALL]

    def errors(self) -> List[TraceEvent]:
        return [e for e in self.events if e.kind == EventKind.ERROR]

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "turn_count": len(self.turns()),
            "event_count": len(self.events),
            "tokens": self.total_tokens(),
            "events": [e.to_dict() for e in self.events],
        }


class TraceRecorder:
    """Thread-safe collector that appends events to a JSONL file.

    Usage:
        rec = TraceRecorder(project_id="p1", session_id="s1",
                             agent_id="p1.coder", trace_dir=Path("data/traces"))
        rec.start_session()
        rec.record_prompt(turn=1, messages=[...])
        rec.record_completion(turn=1, content="...", tool_calls=[...])
        rec.record_tool_call(turn=1, name="file_read", args={"path": "x.py"})
        rec.record_tool_result(turn=1, output="...", success=True, duration_ms=12)
        rec.end_session()
    """

    def __init__(self,
                 project_id: str,
                 session_id: str,
                 agent_id: str = "",
                 trace_dir: Optional[Path] = None,
                 enabled: bool = True):
        self.project_id = project_id
        self.session_id = session_id
        self.agent_id = agent_id
        self.trace_dir = Path(trace_dir) if trace_dir else TRACE_DIR
        self.enabled = enabled
        self._seq = 0
        self._lock = threading.Lock()
        self._file = None
        self._started_at = 0.0

    # ---- file management ----

    def _file_path(self) -> Path:
        return self.trace_dir / self.project_id / f"{self.session_id}.jsonl"

    def _open_file(self) -> None:
        path = self._file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # append-mode; we never rewrite.
        self._file = open(path, "a", encoding="utf-8", newline="\n")

    def _write(self, event: TraceEvent) -> None:
        if not self.enabled:
            return
        if self._file is None:
            self._open_file()
        line = json.dumps(event.to_dict(), ensure_ascii=False)
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                finally:
                    self._file = None

    def __del__(self):
        # Best-effort; we don't want __del__ errors to break the agent.
        try:
            self.close()
        except Exception:
            pass

    # ---- recording API ----

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def start_session(self, **extra) -> TraceEvent:
        self._started_at = time.time()
        ev = TraceEvent(
            kind=EventKind.SESSION_START,
            turn=0,
            timestamp=self._started_at,
            payload={"extra": extra} if extra else {},
            seq=self._next_seq(),
            agent_id=self.agent_id,
            session_id=self.session_id,
        )
        self._write(ev)
        return ev

    def end_session(self, **extra) -> TraceEvent:
        ev = TraceEvent(
            kind=EventKind.SESSION_END,
            turn=0,
            timestamp=time.time(),
            payload={"extra": extra} if extra else {},
            seq=self._next_seq(),
            agent_id=self.agent_id,
            session_id=self.session_id,
        )
        self._write(ev)
        self.close()
        return ev

    def record_prompt(self, turn: int, messages: List[Dict[str, Any]],
                      **extra) -> TraceEvent:
        ev = TraceEvent(
            kind=EventKind.PROMPT,
            turn=turn,
            timestamp=time.time(),
            payload={"messages": list(messages), "extra": extra} if extra
                    else {"messages": list(messages)},
            seq=self._next_seq(),
            agent_id=self.agent_id,
            session_id=self.session_id,
        )
        self._write(ev)
        return ev

    def record_completion(self, turn: int, content: str = "",
                          tool_calls: Optional[List[Dict[str, Any]]] = None,
                          usage: Optional[Dict[str, int]] = None,
                          latency_ms: int = 0,
                          model: str = "",
                          **extra) -> TraceEvent:
        payload: Dict[str, Any] = {"content": content}
        if tool_calls is not None:
            payload["tool_calls"] = list(tool_calls)
        if usage is not None:
            payload["usage"] = dict(usage)
        if latency_ms:
            payload["latency_ms"] = latency_ms
        if model:
            payload["model"] = model
        if extra:
            payload["extra"] = extra
        ev = TraceEvent(
            kind=EventKind.COMPLETION,
            turn=turn,
            timestamp=time.time(),
            payload=payload,
            seq=self._next_seq(),
            agent_id=self.agent_id,
            session_id=self.session_id,
        )
        self._write(ev)
        return ev

    def record_tool_call(self, turn: int, name: str, args: Dict[str, Any],
                         call_id: str = "", **extra) -> TraceEvent:
        payload: Dict[str, Any] = {"name": name, "args": dict(args)}
        if call_id:
            payload["call_id"] = call_id
        if extra:
            payload["extra"] = extra
        ev = TraceEvent(
            kind=EventKind.TOOL_CALL,
            turn=turn,
            timestamp=time.time(),
            payload=payload,
            seq=self._next_seq(),
            agent_id=self.agent_id,
            session_id=self.session_id,
        )
        self._write(ev)
        return ev

    def record_tool_result(self, turn: int, name: str,
                           output: str = "",
                           success: bool = True,
                           error: str = "",
                           duration_ms: int = 0,
                           call_id: str = "",
                           **extra) -> TraceEvent:
        payload: Dict[str, Any] = {
            "name": name,
            "output": output,
            "success": bool(success),
            "duration_ms": int(duration_ms or 0),
        }
        if error:
            payload["error"] = error
        if call_id:
            payload["call_id"] = call_id
        if extra:
            payload["extra"] = extra
        ev = TraceEvent(
            kind=EventKind.TOOL_RESULT,
            turn=turn,
            timestamp=time.time(),
            payload=payload,
            seq=self._next_seq(),
            agent_id=self.agent_id,
            session_id=self.session_id,
        )
        self._write(ev)
        return ev

    def record_summary(self, turn: int, summary: str,
                       replaced_messages: int = 0, **extra) -> TraceEvent:
        payload: Dict[str, Any] = {"summary": summary,
                                    "replaced_messages": replaced_messages}
        if extra:
            payload["extra"] = extra
        ev = TraceEvent(
            kind=EventKind.SUMMARY,
            turn=turn,
            timestamp=time.time(),
            payload=payload,
            seq=self._next_seq(),
            agent_id=self.agent_id,
            session_id=self.session_id,
        )
        self._write(ev)
        return ev

    def record_guardrail(self, turn: int, verdict: str, reason: str = "",
                         blocked: bool = False, **extra) -> TraceEvent:
        payload: Dict[str, Any] = {
            "verdict": verdict, "reason": reason, "blocked": bool(blocked),
        }
        if extra:
            payload["extra"] = extra
        ev = TraceEvent(
            kind=EventKind.GUARDRAIL,
            turn=turn,
            timestamp=time.time(),
            payload=payload,
            seq=self._next_seq(),
            agent_id=self.agent_id,
            session_id=self.session_id,
        )
        self._write(ev)
        return ev

    def record_error(self, turn: int, error: str,
                     exc_type: str = "", **extra) -> TraceEvent:
        payload: Dict[str, Any] = {"error": error}
        if exc_type:
            payload["exc_type"] = exc_type
        if extra:
            payload["extra"] = extra
        ev = TraceEvent(
            kind=EventKind.ERROR,
            turn=turn,
            timestamp=time.time(),
            payload=payload,
            seq=self._next_seq(),
            agent_id=self.agent_id,
            session_id=self.session_id,
        )
        self._write(ev)
        return ev

    # ---- decorator / context manager ----

    @contextmanager
    def tool_invocation(self, turn: int, name: str, args: Dict[str, Any],
                        call_id: str = "") -> Iterator[Dict[str, Any]]:
        """Context manager: record a tool call, then a result on exit.

        Usage:
            with rec.tool_invocation(turn=1, name="file_read",
                                      args={"path": "x.py"}) as ctx:
                result = tool.run(**args)
                ctx["output"] = result
                ctx["success"] = True
        The `ctx` dict is mutated by the caller; the result event is
        written when the with-block exits.
        """
        call_id = call_id or uuid.uuid4().hex[:8]
        self.record_tool_call(turn, name, args, call_id=call_id)
        t0 = time.time()
        ctx: Dict[str, Any] = {"output": "", "success": True, "error": ""}
        try:
            yield ctx
        except Exception as e:  # noqa: BLE001
            ctx["success"] = False
            ctx["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            duration_ms = int((time.time() - t0) * 1000)
            self.record_tool_result(
                turn, name,
                output=ctx.get("output", ""),
                success=ctx.get("success", True),
                error=ctx.get("error", ""),
                duration_ms=duration_ms,
                call_id=call_id,
            )


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def trace_file_path(project_id: str, session_id: str,
                    trace_dir: Optional[Path] = None) -> Path:
    base = Path(trace_dir) if trace_dir else TRACE_DIR
    return base / project_id / f"{session_id}.jsonl"


def load_trace(project_id: str, session_id: str,
               trace_dir: Optional[Path] = None) -> Optional[Trace]:
    """Read a trace back from disk. Returns None if the file doesn't exist."""
    path = trace_file_path(project_id, session_id, trace_dir)
    if not path.exists():
        return None
    events: List[TraceEvent] = []
    started_at = 0.0
    finished_at = 0.0
    agent_id = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        d = json.loads(raw)
        ev = TraceEvent.from_dict(d)
        events.append(ev)
        if ev.kind == EventKind.SESSION_START:
            started_at = ev.timestamp
            agent_id = ev.agent_id
        elif ev.kind == EventKind.SESSION_END:
            finished_at = ev.timestamp
    return Trace(
        project_id=project_id,
        session_id=session_id,
        agent_id=agent_id,
        events=events,
        started_at=started_at,
        finished_at=finished_at,
    )


def list_sessions(project_id: str,
                  trace_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """List all session trace files for a project (metadata only)."""
    base = Path(trace_dir) if trace_dir else TRACE_DIR
    proj_dir = base / project_id
    if not proj_dir.exists():
        return []
    out = []
    for f in sorted(proj_dir.glob("*.jsonl")):
        session_id = f.stem
        size = f.stat().st_size
        # Count events without loading the whole file.
        with f.open("r", encoding="utf-8") as fh:
            event_count = sum(1 for _ in fh if _.strip())
        out.append({
            "session_id": session_id,
            "path": str(f),
            "size_bytes": size,
            "event_count": event_count,
        })
    return out
