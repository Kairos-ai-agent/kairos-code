"""Bridge urllib-style test fakes onto the httpx probes.

History: `api/routes/config.py::_probe_post_*` used to issue its test-connection
request with ``urllib.request.urlopen``, and the tests in
``tests/test_r37_backend.py`` mock it at that level — each test builds a
``fake_urlopen(req)`` that records ``req.full_url`` / ``get_method()`` /
``header_items()`` / ``data`` and returns a fake response (or raises
``urllib.error.HTTPError`` / ``URLError``).

The probes now use ``httpx.Client(...).post(...)`` (and ``httpx.AsyncClient``
for the async path), so those 26 fakes were never called and the assertions saw
an empty capture dict. Rather than rewrite every fake, install this adapter:
it replaces ``httpx.Client`` / ``httpx.AsyncClient`` for the duration of a test,
turns each ``.post()`` into a urllib-shaped request, calls the test's fake, and
translates the outcome back into httpx terms (``HTTPStatusError`` with a
``.response`` that carries the body, ``ConnectError`` for ``URLError``).

Usage in a test (replacing the old ``urllib.request.urlopen = fake_urlopen`` /
``urllib.request.urlopen = orig`` pair):

    _httpx_bridge_install(fake_urlopen, captured)
    try:
        result = _probe_post_openai_chat(...)
    finally:
        _httpx_bridge_restore()
"""
from __future__ import annotations

import json
import urllib.error

import httpx

_STATE: dict[str, object] = {"client": None, "async_client": None}


class _BridgeRequest:
    """The urllib-shaped request the existing fakes expect."""

    def __init__(self, url: str, data=None, headers=None, method: str = "POST"):
        self.full_url = url
        self.data = data
        self.headers = dict(headers or {})
        self._method = method

    def get_method(self) -> str:
        return self._method

    def header_items(self):
        return list(self.headers.items())

    def has_header(self, name: str) -> bool:
        return name in self.headers


class _BridgeResponse:
    """httpx-shaped response built from whatever the fake returned."""

    def __init__(self, status_code: int, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is not None:
            return self._payload
        try:
            return json.loads(self.text or "{}")
        except Exception:  # noqa: BLE001
            return {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _status_error(self.status_code, self.text)


def _status_error(status: int, text: str) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://bridge.invalid")
    resp = httpx.Response(status, request=req, text=text or "")
    return httpx.HTTPStatusError(f"HTTP {status}", request=req, response=resp)


def _call_fake(fake, req: _BridgeRequest) -> _BridgeResponse:
    try:
        raw = fake(req)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            read = exc.read()
            body = read.decode("utf-8", "replace") if isinstance(read, bytes) else str(read)
        except Exception:  # noqa: BLE001
            pass
        raise _status_error(int(exc.code or 500), body) from None
    except urllib.error.URLError as exc:
        raise httpx.ConnectError(str(getattr(exc, "reason", exc))) from None

    status = int(getattr(raw, "status", 200) or 200)
    text = ""
    read = getattr(raw, "read", None)
    if callable(read):
        try:
            data = read()
            text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
        except Exception:  # noqa: BLE001
            pass
    return _BridgeResponse(status, text)


class _BridgeClient:
    def __init__(self, fake_urlopen, captured, *args, **kwargs):
        self._fake = fake_urlopen
        self._captured = captured

    def _record(self, method: str, url: str, content, headers):
        if content is not None and not isinstance(content, (bytes, str)):
            try:
                content = json.dumps(content).encode("utf-8")
            except Exception:  # noqa: BLE001
                pass
        self._captured.update({
            "url": url,
            "method": method,
            "headers": {k.lower(): v for k, v in (headers or {}).items()},
            "body": content,
        })
        return _BridgeRequest(url, data=content, headers=headers, method=method)

    def post(self, url, content=None, headers=None, **kwargs):
        return _call_fake(self._fake, self._record("POST", url, content, headers))

    def get(self, url, headers=None, **kwargs):
        return _call_fake(self._fake, self._record("GET", url, None, headers))

    def request(self, method, url, content=None, headers=None, **kwargs):
        return _call_fake(self._fake, self._record(method.upper(), url, content, headers))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        return None


class _BridgeAsyncClient(_BridgeClient):
    async def post(self, url, content=None, headers=None, **kwargs):  # type: ignore[override]
        return _call_fake(self._fake, self._record("POST", url, content, headers))

    async def get(self, url, headers=None, **kwargs):  # type: ignore[override]
        return _call_fake(self._fake, self._record("GET", url, None, headers))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _httpx_bridge_install(fake_urlopen, captured) -> None:
    """Install the bridge (call it right before patching was done before)."""
    class _Client(_BridgeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(fake_urlopen, captured, *args, **kwargs)

    class _AsyncClient(_BridgeAsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(fake_urlopen, captured, *args, **kwargs)

    _STATE["client"] = httpx.Client
    _STATE["async_client"] = httpx.AsyncClient
    httpx.Client = _Client          # type: ignore[assignment]
    httpx.AsyncClient = _AsyncClient  # type: ignore[assignment]


def _httpx_bridge_restore() -> None:
    if _STATE["client"] is not None:
        httpx.Client = _STATE["client"]  # type: ignore[assignment]
        _STATE["client"] = None
    if _STATE["async_client"] is not None:
        httpx.AsyncClient = _STATE["async_client"]  # type: ignore[assignment]
        _STATE["async_client"] = None
