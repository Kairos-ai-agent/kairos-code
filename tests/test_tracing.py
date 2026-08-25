"""Tests for the trace recorder + reader (P2-2: Trace viewer)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.tracing import (
    EventKind,
    Trace,
    TraceEvent,
    TraceRecorder,
    list_sessions,
    load_trace,
    trace_file_path,
)


# ---------------------------------------------------------------------------
# TraceEvent serialization
# ---------------------------------------------------------------------------


def test_event_to_and_from_dict_round_trip():
    ev = TraceEvent(
        kind=EventKind.COMPLETION,
        turn=3,
        timestamp=1234.5,
        payload={"content": "hello", "tool_calls": []},
        seq=7,
        agent_id="p1.coder",
        session_id="s1",
    )
    d = ev.to_dict()
    assert d["kind"] == "completion"
    again = TraceEvent.from_dict(d)
    assert again.kind == EventKind.COMPLETION
    assert again.turn == 3
    assert again.payload["content"] == "hello"
    assert again.agent_id == "p1.coder"


# ---------------------------------------------------------------------------
# TraceRecorder
# ---------------------------------------------------------------------------


def test_recorder_writes_jsonl(tmp_path):
    rec = TraceRecorder(project_id="p1", session_id="s1",
                         agent_id="p1.coder", trace_dir=tmp_path)
    rec.start_session()
    rec.record_prompt(turn=1, messages=[{"role": "user", "content": "hi"}])
    rec.record_completion(turn=1, content="hello", tool_calls=[],
                           usage={"prompt_tokens": 5, "completion_tokens": 2},
                           latency_ms=120, model="gpt-x")
    rec.record_tool_call(turn=1, name="file_read", args={"path": "x.py"})
    rec.record_tool_result(turn=1, name="file_read", output="file body",
                            success=True, duration_ms=15)
    rec.end_session()
    rec.close()
    path = trace_file_path("p1", "s1", tmp_path)
    assert path.exists()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # 5 events from record_* + 2 lifecycle (start_session + end_session)
    assert len(lines) == 6
    kinds = [json.loads(ln)["kind"] for ln in lines]
    assert kinds == ["session_start", "prompt", "completion", "tool_call",
                     "tool_result", "session_end"]


def test_recorder_appends_across_instances(tmp_path):
    """Two recorder instances for the same session should append, not overwrite."""
    rec1 = TraceRecorder(project_id="p1", session_id="s1",
                          trace_dir=tmp_path)
    rec1.start_session()
    rec1.record_prompt(turn=1, messages=[])
    rec1.close()
    rec2 = TraceRecorder(project_id="p1", session_id="s1",
                          trace_dir=tmp_path)
    rec2.record_completion(turn=1, content="hi")
    rec2.close()
    path = trace_file_path("p1", "s1", tmp_path)
    lines = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # 2 events from rec1 (start + prompt) + 1 from rec2 = 3
    assert len(lines) == 3
    assert lines[0]["kind"] == "session_start"
    assert lines[1]["kind"] == "prompt"
    assert lines[2]["kind"] == "completion"


def test_recorder_disabled_writes_nothing(tmp_path):
    rec = TraceRecorder(project_id="p1", session_id="s1",
                         trace_dir=tmp_path, enabled=False)
    rec.start_session()
    rec.record_prompt(turn=1, messages=[])
    rec.close()
    path = trace_file_path("p1", "s1", tmp_path)
    assert not path.exists()


def test_recorder_thread_safety(tmp_path):
    """Concurrent recorders should produce unique seqs (the seqs are
    allocated under a lock, so uniqueness is guaranteed; file order
    is not, because writers race for the file lock)."""
    import threading
    rec = TraceRecorder(project_id="p1", session_id="s1",
                         trace_dir=tmp_path)
    rec.start_session()
    errors = []

    def worker(n: int):
        try:
            for i in range(n):
                rec.record_completion(turn=1, content=f"m{i}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(20,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    rec.close()
    path = trace_file_path("p1", "s1", tmp_path)
    seqs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        seqs.append(json.loads(line)["seq"])
    # 1 start + 80 completions = 81 events
    assert len(seqs) == 81
    # All seqs unique (monotonicity is the contract)
    assert len(seqs) == len(set(seqs))
    # And they form a contiguous range starting from 1
    assert min(seqs) == 1
    assert max(seqs) == 81


def test_recorder_tool_invocation_context(tmp_path):
    rec = TraceRecorder(project_id="p1", session_id="s1",
                         trace_dir=tmp_path)
    rec.start_session()
    with rec.tool_invocation(turn=1, name="file_read",
                              args={"path": "x.py"}) as ctx:
        ctx["output"] = "ok"
        ctx["success"] = True
    rec.end_session()
    rec.close()
    path = trace_file_path("p1", "s1", tmp_path)
    events = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    tool_call = next(e for e in events if e["kind"] == "tool_call")
    tool_result = next(e for e in events if e["kind"] == "tool_result")
    assert tool_call["payload"]["name"] == "file_read"
    assert tool_call["payload"]["args"] == {"path": "x.py"}
    assert tool_call["payload"]["call_id"] == tool_result["payload"]["call_id"]
    assert tool_result["payload"]["success"] is True
    assert tool_result["payload"]["output"] == "ok"
    assert tool_result["payload"]["duration_ms"] >= 0


def test_recorder_tool_invocation_exception_marks_failure(tmp_path):
    rec = TraceRecorder(project_id="p1", session_id="s1",
                         trace_dir=tmp_path)
    rec.start_session()
    with pytest.raises(RuntimeError):
        with rec.tool_invocation(turn=1, name="bad_tool", args={}) as ctx:
            raise RuntimeError("boom")
    rec.close()
    events = [json.loads(ln) for ln in
              trace_file_path("p1", "s1", tmp_path).read_text(
                  encoding="utf-8").splitlines() if ln.strip()]
    result = next(e for e in events if e["kind"] == "tool_result")
    assert result["payload"]["success"] is False
    assert "boom" in result["payload"]["error"]


# ---------------------------------------------------------------------------
# Read API: load_trace + list_sessions
# ---------------------------------------------------------------------------


def test_load_trace_returns_none_for_missing(tmp_path):
    assert load_trace("missing", "missing", trace_dir=tmp_path) is None


def test_load_trace_reconstructs_events(tmp_path):
    rec = TraceRecorder(project_id="p1", session_id="s1",
                         trace_dir=tmp_path)
    rec.start_session(extra_field="x")
    rec.record_prompt(turn=1, messages=[{"role": "user", "content": "hi"}])
    rec.record_completion(turn=1, content="hello", tool_calls=[],
                           usage={"prompt_tokens": 5, "completion_tokens": 3})
    rec.record_tool_call(turn=2, name="file_read", args={"path": "x.py"})
    rec.record_tool_result(turn=2, name="file_read", output="body",
                            success=True, duration_ms=10)
    rec.end_session()
    rec.close()
    trace = load_trace("p1", "s1", trace_dir=tmp_path)
    assert trace is not None
    assert trace.project_id == "p1"
    assert trace.session_id == "s1"
    assert trace.agent_id == ""  # not set on this recorder
    assert trace.started_at > 0
    assert trace.finished_at >= trace.started_at
    assert trace.turns() == [1, 2]
    assert trace.total_tokens() == {"prompt_tokens": 5, "completion_tokens": 3,
                                     "total_tokens": 8}
    assert len(trace.tool_calls()) == 1
    assert len(trace.errors()) == 0


def test_load_trace_handles_empty_lines(tmp_path):
    """Robustness: stray blank lines in the JSONL file shouldn't break load."""
    path = tmp_path / "p1" / "s1.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = TraceRecorder(project_id="p1", session_id="s1",
                         trace_dir=tmp_path)
    rec.start_session()
    rec.record_prompt(turn=1, messages=[])
    rec.close()
    # Insert blank lines.
    text = path.read_text(encoding="utf-8")
    path.write_text("\n" + text + "\n\n", encoding="utf-8")
    trace = load_trace("p1", "s1", trace_dir=tmp_path)
    assert trace is not None
    assert len(trace.events) == 2


def test_list_sessions_returns_metadata(tmp_path):
    rec1 = TraceRecorder(project_id="p1", session_id="s1",
                          trace_dir=tmp_path)
    rec1.start_session()
    rec1.record_prompt(turn=1, messages=[])
    rec1.close()
    rec2 = TraceRecorder(project_id="p1", session_id="s2",
                          trace_dir=tmp_path)
    rec2.start_session()
    rec2.close()
    sessions = list_sessions("p1", trace_dir=tmp_path)
    ids = sorted(s["session_id"] for s in sessions)
    assert ids == ["s1", "s2"]
    # event_count > 0 and size_bytes > 0
    for s in sessions:
        assert s["event_count"] >= 1
        assert s["size_bytes"] > 0


def test_list_sessions_empty_project(tmp_path):
    assert list_sessions("nope", trace_dir=tmp_path) == []


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------


def test_trace_turns_sorted_unique():
    trace = Trace(project_id="p1", session_id="s1", agent_id="a", events=[
        TraceEvent(kind=EventKind.PROMPT, turn=2, timestamp=0, seq=1),
        TraceEvent(kind=EventKind.PROMPT, turn=1, timestamp=0, seq=2),
        TraceEvent(kind=EventKind.PROMPT, turn=2, timestamp=0, seq=3),
        TraceEvent(kind=EventKind.SESSION_START, turn=0, timestamp=0, seq=0),
    ])
    assert trace.turns() == [1, 2]


def test_trace_events_for_turn_filters():
    trace = Trace(project_id="p1", session_id="s1", agent_id="a", events=[
        TraceEvent(kind=EventKind.PROMPT, turn=1, timestamp=0, seq=1),
        TraceEvent(kind=EventKind.COMPLETION, turn=1, timestamp=0, seq=2),
        TraceEvent(kind=EventKind.PROMPT, turn=2, timestamp=0, seq=3),
    ])
    assert len(trace.events_for_turn(1)) == 2
    assert len(trace.events_for_turn(2)) == 1
    assert len(trace.events_for_turn(99)) == 0


def test_trace_to_dict_shape():
    trace = Trace(project_id="p1", session_id="s1", agent_id="a",
                   started_at=1.0, finished_at=2.0, events=[
        TraceEvent(kind=EventKind.COMPLETION, turn=1, timestamp=0,
                    payload={"content": "hi",
                             "usage": {"prompt_tokens": 5,
                                        "completion_tokens": 2}},
                    seq=1),
    ])
    d = trace.to_dict()
    assert d["project_id"] == "p1"
    assert d["turn_count"] == 1
    assert d["event_count"] == 1
    assert d["tokens"] == {"prompt_tokens": 5, "completion_tokens": 2,
                            "total_tokens": 7}
    assert d["events"][0]["kind"] == "completion"


# ---------------------------------------------------------------------------
# Summary, guardrail, error events
# ---------------------------------------------------------------------------


def test_recorder_records_summary_event(tmp_path):
    rec = TraceRecorder(project_id="p1", session_id="s1",
                         trace_dir=tmp_path)
    rec.start_session()
    rec.record_summary(turn=5, summary="digest of past 8 turns",
                        replaced_messages=8)
    rec.close()
    events = [json.loads(ln) for ln in
              trace_file_path("p1", "s1", tmp_path).read_text(
                  encoding="utf-8").splitlines() if ln.strip()]
    summary = next(e for e in events if e["kind"] == "summary")
    assert summary["payload"]["summary"] == "digest of past 8 turns"
    assert summary["payload"]["replaced_messages"] == 8


def test_recorder_records_guardrail_event(tmp_path):
    rec = TraceRecorder(project_id="p1", session_id="s1",
                         trace_dir=tmp_path)
    rec.start_session()
    rec.record_guardrail(turn=2, verdict="block", reason="contains secret",
                          blocked=True)
    rec.close()
    events = [json.loads(ln) for ln in
              trace_file_path("p1", "s1", tmp_path).read_text(
                  encoding="utf-8").splitlines() if ln.strip()]
    g = next(e for e in events if e["kind"] == "guardrail")
    assert g["payload"]["verdict"] == "block"
    assert g["payload"]["blocked"] is True


def test_recorder_records_error_event(tmp_path):
    rec = TraceRecorder(project_id="p1", session_id="s1",
                         trace_dir=tmp_path)
    rec.start_session()
    rec.record_error(turn=3, error="LLM provider timeout", exc_type="TimeoutError")
    rec.close()
    trace = load_trace("p1", "s1", trace_dir=tmp_path)
    assert trace is not None
    assert len(trace.errors()) == 1
    assert trace.errors()[0].payload["exc_type"] == "TimeoutError"
