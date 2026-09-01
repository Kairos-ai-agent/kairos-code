"""Cloud delegation — run a coding task on a remote runner.

Mirrors the the cloud task-Harness "cloud delegation" feature: instead of
running the Coder/Reviewer loop locally, hand a task off to a remote
service (a Docker container, a cloud VM, or a the cloud task-style hosted
runner) and poll for results.

Design:

* `CloudDelegator` is an HTTP client. It does NOT care what the remote
  service is — the contract is just "POST a task, poll status, fetch
  result". This means we can plug in:
  - A self-hosted `RemoteRunner` server (the user runs it on a VM)
  - A Docker container exposing the same HTTP API
  - A future the cloud task cloud service
* `LocalDelegator` runs the task in-process. Useful for tests, and
  for "offline" mode where you want delegation semantics without
  actually leaving the box.
* Both delegators implement the same `submit / poll / get_result /
  cancel` interface so the rest of Kairos doesn't care which one is
  in use.
* No new pip dependencies — we use `urllib.request` (stdlib) so the
  whole thing works in the offline venv.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib import error as urlerror
from urllib import request as urlrequest

logger = logging.getLogger(__name__)


class DelegationStatus(str, Enum):
    """Lifecycle of a delegated task."""
    QUEUED = "queued"        # accepted, not yet started
    RUNNING = "running"      # remote worker is processing
    COMPLETED = "completed"  # success, result ready
    FAILED = "failed"        # error, see `error`
    CANCELLED = "cancelled"  # user cancelled


@dataclass
class DelegationRequest:
    """What we send to the remote runner."""
    task_id: str
    project_id: str
    description: str
    work_dir: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    # Optional file attachments: list of {path, base64_content} or
    # {path, sha256, url}. The remote runner fetches and writes them
    # into work_dir before starting.
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    # When the result is ready, the runner may post files back. We
    # accept an optional callback URL.
    callback_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DelegationRequest":
        return cls(**d)


@dataclass
class DelegationResult:
    """What the remote runner returns."""
    task_id: str
    status: DelegationStatus
    output: str = ""
    error: str = ""
    files_changed: List[str] = field(default_factory=list)
    # Structured per-round digests if the runner did a loop. Same
    # shape as `loop_rounds` in the local persistence layer.
    rounds: List[Dict[str, Any]] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DelegationResult":
        d = dict(d)
        d["status"] = DelegationStatus(d.get("status", "queued"))
        return cls(**d)


class DelegationError(RuntimeError):
    """Raised when the remote service returned an error or the
    transport itself failed."""


class CloudDelegator:
    """HTTP-based delegator.

    Configuration:
        base_url:  e.g. "https://runners.example.com" or
                   "http://10.0.0.5:8080" for a self-hosted runner
        auth_token: optional bearer token, sent as `Authorization: Bearer ...`
        timeout_s:  per-request timeout in seconds (default 30)
        max_polls:  max poll attempts before giving up (default 60)
        poll_interval: seconds between polls (default 5)

    Endpoints (the remote server must implement these):
        POST   {base_url}/tasks              -> {task_id, status}
        GET    {base_url}/tasks/{task_id}    -> DelegationResult
        DELETE {base_url}/tasks/{task_id}    -> 204
    """

    def __init__(self,
                 base_url: str,
                 auth_token: str = "",
                 timeout_s: float = 30.0,
                 max_polls: int = 60,
                 poll_interval: float = 5.0):
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout_s = float(timeout_s)
        self.max_polls = int(max_polls)
        self.poll_interval = float(poll_interval)
        # Allow tests to inject a custom urllib opener (for mocking).
        self._opener = urlrequest.urlopen

    # ---- low-level HTTP ----

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json",
             "Accept": "application/json"}
        if self.auth_token:
            h["Authorization"] = f"Bearer {self.auth_token}"
        return h

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8")
        req = urlrequest.Request(url, data=data, headers=self._headers(),
                                  method="POST")
        try:
            with self._opener(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urlerror.HTTPError as e:
            raise DelegationError(
                f"POST {url} failed: HTTP {e.code} {e.reason}") from e
        except urlerror.URLError as e:
            raise DelegationError(f"POST {url} failed: {e.reason}") from e

    def _get(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        req = urlrequest.Request(url, headers=self._headers(), method="GET")
        try:
            with self._opener(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urlerror.HTTPError as e:
            if e.code == 404:
                raise DelegationError(f"task not found: {path}") from e
            raise DelegationError(
                f"GET {url} failed: HTTP {e.code} {e.reason}") from e
        except urlerror.URLError as e:
            raise DelegationError(f"GET {url} failed: {e.reason}") from e

    def _delete(self, path: str) -> None:
        url = f"{self.base_url}{path}"
        req = urlrequest.Request(url, headers=self._headers(),
                                  method="DELETE")
        try:
            with self._opener(req, timeout=self.timeout_s) as resp:
                resp.read()
        except urlerror.HTTPError as e:
            # 404 means already gone — that's fine for cancel.
            if e.code != 404:
                raise DelegationError(
                    f"DELETE {url} failed: HTTP {e.code} {e.reason}") from e
        except urlerror.URLError as e:
            raise DelegationError(f"DELETE {url} failed: {e.reason}") from e

    # ---- public API ----

    def submit(self, request: DelegationRequest) -> str:
        """POST the task; return the server-assigned task_id."""
        if not request.task_id:
            request.task_id = uuid.uuid4().hex[:8]
        body = request.to_dict()
        resp = self._post("/tasks", body)
        # If the server doesn't echo task_id, use ours.
        return resp.get("task_id", request.task_id)

    def get_result(self, task_id: str) -> DelegationResult:
        """One-shot fetch. Does NOT wait for completion."""
        raw = self._get(f"/tasks/{task_id}")
        return DelegationResult.from_dict(raw)

    def poll_until_done(self, task_id: str) -> DelegationResult:
        """Poll `get_result` until status is terminal. Returns the
        final result. Raises DelegationError on transport failure or
        if max_polls is exceeded."""
        for attempt in range(self.max_polls):
            result = self.get_result(task_id)
            if result.status in (DelegationStatus.COMPLETED,
                                 DelegationStatus.FAILED,
                                 DelegationStatus.CANCELLED):
                return result
            time.sleep(self.poll_interval)
        raise DelegationError(
            f"task {task_id} did not complete after {self.max_polls} polls"
        )

    def cancel(self, task_id: str) -> None:
        self._delete(f"/tasks/{task_id}")


# ---------------------------------------------------------------------------
# LocalDelegator — in-process implementation for tests + offline mode
# ---------------------------------------------------------------------------


# A "local runner" just calls a function with the request and returns
# the result. The function is async-aware: if it's a coroutine
# function, we run it with asyncio.run; if sync, we just call it.
RunnerFn = Callable[[DelegationRequest], DelegationResult]


class LocalDelegator:
    """In-process delegator. Runs the task synchronously on the
    caller's thread (or event loop, if `runner` is async).

    Primarily used by tests that want to exercise the delegation flow
    without spinning up a remote service. Also useful as a "dry run"
    mode where the user wants delegation semantics (queued/running/
    completed transitions) without leaving the box.
    """

    def __init__(self, runner: RunnerFn, simulate_latency: float = 0.0):
        if runner is None:
            raise ValueError("runner is required")
        self.runner = runner
        self.simulate_latency = float(simulate_latency)
        self._lock = threading.Lock()
        self._results: Dict[str, DelegationResult] = {}

    def submit(self, request: DelegationRequest) -> str:
        if not request.task_id:
            request.task_id = uuid.uuid4().hex[:8]
        # Initial state: queued. The runner is invoked synchronously
        # so by submit() return, the task is already done (or failed).
        # Tests can inspect the stored result via get_result.
        initial = DelegationResult(
            task_id=request.task_id,
            status=DelegationStatus.RUNNING,
            started_at=time.time(),
        )
        with self._lock:
            self._results[request.task_id] = initial
        if self.simulate_latency > 0:
            time.sleep(self.simulate_latency)
        try:
            final = self.runner(request)
        except Exception as e:  # noqa: BLE001
            logger.exception("local runner failed for %s", request.task_id)
            final = DelegationResult(
                task_id=request.task_id,
                status=DelegationStatus.FAILED,
                error=f"{type(e).__name__}: {e}",
                started_at=initial.started_at,
                finished_at=time.time(),
            )
        else:
            if final.task_id != request.task_id:
                final.task_id = request.task_id
            if not final.started_at:
                final.started_at = initial.started_at
            final.finished_at = time.time()
        with self._lock:
            self._results[request.task_id] = final
        return request.task_id

    def get_result(self, task_id: str) -> DelegationResult:
        with self._lock:
            r = self._results.get(task_id)
        if r is None:
            raise DelegationError(f"task not found: {task_id}")
        return r

    def poll_until_done(self, task_id: str) -> DelegationResult:
        # In-process, so by the time submit() returns, the task is
        # already done. We just return the latest stored result.
        return self.get_result(task_id)

    def cancel(self, task_id: str) -> None:
        with self._lock:
            r = self._results.get(task_id)
            if r is None:
                return
            if r.status in (DelegationStatus.COMPLETED,
                            DelegationStatus.FAILED,
                            DelegationStatus.CANCELLED):
                return
            r.status = DelegationStatus.CANCELLED
            r.finished_at = time.time()


# ---------------------------------------------------------------------------
# Module-level convenience: read the default delegator from env
# ---------------------------------------------------------------------------


def make_default_delegator(runner: Optional[RunnerFn] = None) -> Any:
    """Build a delegator from env vars.

    KAIROS_CLOUD_URL  — base URL for the remote runner
    KAIROS_CLOUD_TOKEN — bearer token
    KAIROS_CLOUD_OFFLINE — "1" / "true" to force LocalDelegator
    """
    if os.environ.get("KAIROS_CLOUD_OFFLINE", "").lower() in ("1", "true"):
        if runner is None:
            raise ValueError("offline mode requires a runner function")
        return LocalDelegator(runner=runner)
    base = os.environ.get("KAIROS_CLOUD_URL", "")
    if not base:
        return None  # caller decides what to do
    return CloudDelegator(
        base_url=base,
        auth_token=os.environ.get("KAIROS_CLOUD_TOKEN", ""),
    )
