"""Tests for cloud delegation (P2-3)."""
from __future__ import annotations

import json
import sys
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.cloud import (
    CloudDelegator,
    DelegationError,
    DelegationRequest,
    DelegationResult,
    DelegationStatus,
    LocalDelegator,
    make_default_delegator,
)


# ---------------------------------------------------------------------------
# DelegationRequest / DelegationResult
# ---------------------------------------------------------------------------


def test_request_to_and_from_dict_round_trip():
    r = DelegationRequest(
        task_id="t1", project_id="p1",
        description="do a thing", work_dir="/tmp",
        context={"key": "value"},
        attachments=[{"path": "a.txt", "url": "https://x/a.txt"}],
    )
    d = r.to_dict()
    again = DelegationRequest.from_dict(d)
    assert again.task_id == "t1"
    assert again.context == {"key": "value"}
    assert again.attachments[0]["url"] == "https://x/a.txt"


def test_result_to_and_from_dict_round_trip():
    r = DelegationResult(
        task_id="t1", status=DelegationStatus.COMPLETED,
        output="all done", files_changed=["a.py", "b.py"],
        rounds=[{"round": 1, "score": 80}],
        started_at=1.0, finished_at=2.0,
    )
    d = r.to_dict()
    assert d["status"] == "completed"
    again = DelegationResult.from_dict(d)
    assert again.status == DelegationStatus.COMPLETED
    assert again.files_changed == ["a.py", "b.py"]


# ---------------------------------------------------------------------------
# CloudDelegator with mocked urllib
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, body: dict, code: int = 200):
        self._body = json.dumps(body).encode("utf-8")
        self.code = code
        self.reason = "OK" if code == 200 else "Error"

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_opener(responses: List[Dict[str, Any]]):
    """Return a fake urlopen that pops the next canned response on each call."""
    responses = list(responses)
    calls: List[Dict[str, Any]] = []
    lock = threading.Lock()

    def fake_urlopen(req, timeout=None):
        with lock:
            calls.append({
                "url": req.full_url,
                "method": req.method,
                "headers": dict(req.headers),
                "timeout": timeout,
                "data": req.data.decode("utf-8") if req.data else None,
            })
            if not responses:
                raise AssertionError("fake_urlopen called more times than expected")
            spec = responses.pop(0)
        if "raise" in spec:
            raise spec["raise"]
        if "http_error" in spec:
            # Simulate an HTTPError (which has .code and .reason)
            from urllib import error as urlerror
            raise urlerror.HTTPError(
                url="http://test", code=spec["http_error"],
                msg=spec.get("reason", "Error"), hdrs=None, fp=None,
            )
        return FakeResponse(spec.get("body", {}), code=spec.get("code", 200))

    fake_urlopen.calls = calls  # type: ignore[attr-defined]
    return fake_urlopen


def test_cloud_delegator_constructor_requires_base_url():
    with pytest.raises(ValueError):
        CloudDelegator(base_url="")


def test_cloud_delegator_submit_posts_task():
    opener = _make_opener([{"body": {"task_id": "remote-1"}}])
    d = CloudDelegator(base_url="http://test", timeout_s=5)
    d._opener = opener
    task_id = d.submit(DelegationRequest(
        task_id="local-1", project_id="p1", description="x"))
    # The server told us the canonical id; we should pass it through.
    assert task_id == "remote-1"
    # Confirm POST shape.
    assert len(opener.calls) == 1
    call = opener.calls[0]
    assert call["url"] == "http://test/tasks"
    assert call["method"] == "POST"
    body = json.loads(call["data"])
    assert body["project_id"] == "p1"
    assert body["description"] == "x"


def test_cloud_delegator_submit_assigns_id_if_server_doesnt():
    opener = _make_opener([{"body": {}}])
    d = CloudDelegator(base_url="http://test")
    d._opener = opener
    req = DelegationRequest(task_id="", project_id="p1", description="x")
    task_id = d.submit(req)
    # Server didn't echo — we keep our own.
    assert task_id == req.task_id
    assert len(task_id) == 8


def test_cloud_delegator_sends_auth_header():
    opener = _make_opener([{"body": {"task_id": "x"}}])
    d = CloudDelegator(base_url="http://test", auth_token="secret-token")
    d._opener = opener
    d.submit(DelegationRequest(task_id="t", project_id="p1", description="x"))
    headers = opener.calls[0]["headers"]
    assert headers.get("Authorization") == "Bearer secret-token"


def test_cloud_delegator_get_result_parses_payload():
    opener = _make_opener([{
        "body": {
            "task_id": "t1", "status": "completed",
            "output": "ok", "files_changed": ["a.py"],
            "started_at": 1.0, "finished_at": 2.0,
        }
    }])
    d = CloudDelegator(base_url="http://test")
    d._opener = opener
    result = d.get_result("t1")
    assert result.status == DelegationStatus.COMPLETED
    assert result.output == "ok"
    assert result.files_changed == ["a.py"]


def test_cloud_delegator_404_raises_not_found():
    opener = _make_opener([{"http_error": 404}])
    d = CloudDelegator(base_url="http://test")
    d._opener = opener
    with pytest.raises(DelegationError, match="not found"):
        d.get_result("missing")


def test_cloud_delegator_500_raises_error():
    opener = _make_opener([{"http_error": 500, "reason": "Internal Server Error"}])
    d = CloudDelegator(base_url="http://test")
    d._opener = opener
    with pytest.raises(DelegationError, match="HTTP 500"):
        d.get_result("t1")


def test_cloud_delegator_urlerror_raises():
    from urllib import error as urlerror
    opener = _make_opener([{"raise": urlerror.URLError("connection refused")}])
    d = CloudDelegator(base_url="http://test")
    d._opener = opener
    with pytest.raises(DelegationError, match="connection refused"):
        d.get_result("t1")


def test_cloud_delegator_poll_until_done_completes():
    opener = _make_opener([
        {"body": {"task_id": "t1", "status": "running"}},
        {"body": {"task_id": "t1", "status": "running"}},
        {"body": {"task_id": "t1", "status": "completed", "output": "ok"}},
    ])
    d = CloudDelegator(base_url="http://test", max_polls=5, poll_interval=0.01)
    d._opener = opener
    result = d.poll_until_done("t1")
    assert result.status == DelegationStatus.COMPLETED
    assert result.output == "ok"
    assert len(opener.calls) == 3


def test_cloud_delegator_poll_until_done_failed_terminal():
    opener = _make_opener([
        {"body": {"task_id": "t1", "status": "failed", "error": "boom"}},
    ])
    d = CloudDelegator(base_url="http://test", max_polls=5, poll_interval=0.01)
    d._opener = opener
    result = d.poll_until_done("t1")
    assert result.status == DelegationStatus.FAILED
    assert result.error == "boom"


def test_cloud_delegator_poll_exceeds_max_polls():
    # Always return running — poll should time out.
    opener = _make_opener([
        {"body": {"task_id": "t1", "status": "running"}},
    ] * 3)
    d = CloudDelegator(base_url="http://test", max_polls=2, poll_interval=0.001)
    d._opener = opener
    with pytest.raises(DelegationError, match="did not complete"):
        d.poll_until_done("t1")


def test_cloud_delegator_cancel_sends_delete():
    opener = _make_opener([{"code": 204, "body": {}}])
    d = CloudDelegator(base_url="http://test")
    d._opener = opener
    d.cancel("t1")
    assert opener.calls[0]["method"] == "DELETE"
    assert opener.calls[0]["url"] == "http://test/tasks/t1"


def test_cloud_delegator_cancel_404_is_noop():
    """Cancel of a non-existent task should NOT raise (404 == already gone)."""
    opener = _make_opener([{"http_error": 404}])
    d = CloudDelegator(base_url="http://test")
    d._opener = opener
    # Should NOT raise.
    d.cancel("t1")


# ---------------------------------------------------------------------------
# LocalDelegator
# ---------------------------------------------------------------------------


def test_local_delegator_runs_sync_runner():
    def runner(req: DelegationRequest) -> DelegationResult:
        return DelegationResult(
            task_id=req.task_id,
            status=DelegationStatus.COMPLETED,
            output=f"hello {req.project_id}",
        )
    d = LocalDelegator(runner=runner)
    req = DelegationRequest(task_id="t1", project_id="p1", description="x")
    task_id = d.submit(req)
    assert task_id == "t1"
    result = d.get_result("t1")
    assert result.status == DelegationStatus.COMPLETED
    assert result.output == "hello p1"
    assert result.started_at > 0
    assert result.finished_at >= result.started_at


def test_local_delegator_assigns_id_when_empty():
    def runner(req):
        return DelegationResult(task_id=req.task_id,
                                 status=DelegationStatus.COMPLETED)
    d = LocalDelegator(runner=runner)
    req = DelegationRequest(task_id="", project_id="p1", description="x")
    task_id = d.submit(req)
    assert len(task_id) == 8
    assert d.get_result(task_id).status == DelegationStatus.COMPLETED


def test_local_delegator_captures_runner_exception():
    def bad_runner(req):
        raise RuntimeError("kaboom")
    d = LocalDelegator(runner=bad_runner)
    task_id = d.submit(DelegationRequest(task_id="t1", project_id="p1",
                                          description="x"))
    result = d.get_result(task_id)
    assert result.status == DelegationStatus.FAILED
    assert "RuntimeError" in result.error
    assert "kaboom" in result.error


def test_local_delegator_cancel_running_task():
    """A task that hasn't returned yet (simulate with a slow runner)
    should be cancellable. We test the post-hoc state: after cancel,
    a follow-up get_result shows CANCELLED."""
    started = threading.Event()
    proceed = threading.Event()

    def slow(req):
        started.set()
        proceed.wait(timeout=2)
        return DelegationResult(task_id=req.task_id,
                                 status=DelegationStatus.COMPLETED)

    d = LocalDelegator(runner=slow)
    # Submit in a background thread because the runner blocks.
    submit_thread = threading.Thread(
        target=lambda: d.submit(DelegationRequest(
            task_id="t1", project_id="p1", description="x")),
    )
    submit_thread.start()
    started.wait(timeout=2)
    # Now cancel while the runner is still blocked.
    d.cancel("t1")
    result = d.get_result("t1")
    assert result.status == DelegationStatus.CANCELLED
    # Unblock the runner so the thread can finish.
    proceed.set()
    submit_thread.join(timeout=2)


def test_local_delegator_cancel_completed_is_noop():
    def runner(req):
        return DelegationResult(task_id=req.task_id,
                                 status=DelegationStatus.COMPLETED)
    d = LocalDelegator(runner=runner)
    d.submit(DelegationRequest(task_id="t1", project_id="p1", description="x"))
    # Cancelling a completed task is a no-op.
    d.cancel("t1")
    assert d.get_result("t1").status == DelegationStatus.COMPLETED


def test_local_delegator_get_unknown_task_raises():
    d = LocalDelegator(runner=lambda r: DelegationResult(
        task_id=r.task_id, status=DelegationStatus.COMPLETED))
    with pytest.raises(DelegationError, match="not found"):
        d.get_result("nope")


def test_local_delegator_poll_returns_immediately():
    """For LocalDelegator, by submit() return, the result is ready.
    poll_until_done should be effectively a no-op."""
    def runner(req):
        return DelegationResult(task_id=req.task_id,
                                 status=DelegationStatus.COMPLETED,
                                 output="done")
    d = LocalDelegator(runner=runner)
    d.submit(DelegationRequest(task_id="t1", project_id="p1", description="x"))
    result = d.poll_until_done("t1")
    assert result.status == DelegationStatus.COMPLETED
    assert result.output == "done"


def test_local_delegator_simulate_latency():
    """simulate_latency > 0 should make the runner take at least that long."""
    def runner(req):
        return DelegationResult(task_id=req.task_id,
                                 status=DelegationStatus.COMPLETED)
    d = LocalDelegator(runner=runner, simulate_latency=0.1)
    t0 = time.time()
    d.submit(DelegationRequest(task_id="t1", project_id="p1", description="x"))
    elapsed = time.time() - t0
    assert elapsed >= 0.1


# ---------------------------------------------------------------------------
# make_default_delegator
# ---------------------------------------------------------------------------


def test_make_default_delegator_offline_requires_runner(monkeypatch):
    monkeypatch.setenv("KAIROS_CLOUD_OFFLINE", "1")
    with pytest.raises(ValueError, match="requires a runner"):
        make_default_delegator(runner=None)


def test_make_default_delegator_offline_builds_local(monkeypatch):
    monkeypatch.setenv("KAIROS_CLOUD_OFFLINE", "true")
    def runner(req):
        return DelegationResult(task_id=req.task_id,
                                 status=DelegationStatus.COMPLETED)
    d = make_default_delegator(runner=runner)
    assert isinstance(d, LocalDelegator)


def test_make_default_delegator_cloud_from_env(monkeypatch):
    monkeypatch.delenv("KAIROS_CLOUD_OFFLINE", raising=False)
    monkeypatch.setenv("KAIROS_CLOUD_URL", "https://runners.example.com")
    monkeypatch.setenv("KAIROS_CLOUD_TOKEN", "secret")
    d = make_default_delegator()
    assert isinstance(d, CloudDelegator)
    assert d.base_url == "https://runners.example.com"
    assert d.auth_token == "secret"


def test_make_default_delegator_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("KAIROS_CLOUD_OFFLINE", raising=False)
    monkeypatch.delenv("KAIROS_CLOUD_URL", raising=False)
    d = make_default_delegator()
    assert d is None
