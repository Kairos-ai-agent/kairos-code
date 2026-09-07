"""Optional API authentication (HTTP + WebSocket).

Kairos is a local-first tool. Two layers keep it safe:

1. **Loopback bind (default).** ``KAIROS_HOST`` now defaults to
   ``127.0.0.1``, so the API isn't reachable from the network at all
   unless the operator deliberately binds a non-loopback address.

2. **Shared API token (opt-in).** Set ``KAIROS_API_TOKEN`` and every
   ``/api`` route **and the /ws socket** (except a small allow-list and CORS
   preflight) requires it, presented as ``Authorization: Bearer <token>``,
   ``X-API-Token``, or ``?token=``. The browser WS handshake can't set a
   header, so in this mode the SPA must append ``?token=<token>`` to the
   WebSocket URL. When no token is configured the API stays open for local
   use on loopback — don't expose a token-less instance on a non-loopback
   interface.

Implementation note: this is a *pure ASGI* middleware (not
``starlette.middleware.base.BaseHTTPMiddleware``). ``BaseHTTPMiddleware``
only dispatches ``scope["type"] == "http"`` and passes WebSocket upgrade
requests straight through, so token checks on ``/ws`` would never run.
Checking ``scope["type"]`` explicitly is what actually gates the socket.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs

from starlette.responses import JSONResponse

# Paths that must stay reachable without a token (useful to ops/UI health).
_NO_AUTH_PATHS = {"/docs", "/redoc", "/openapi.json", "/api/health"}


def token_configured() -> bool:
    return bool(os.environ.get("KAIROS_API_TOKEN", "").strip())


def _find_header(headers, name: bytes) -> bytes | None:
    """Case-insensitive lookup of a bytes header in a scope['headers'] list."""
    name = name.lower()
    for k, v in headers or []:
        if k.lower() == name:
            return v
    return None


def _extract_token(scope) -> str | None:
    """Pull the token out of an ASGI scope (headers or ``?token=``)."""
    headers = scope.get("headers") or []
    auth = _find_header(headers, b"authorization")
    if auth and auth.lower().startswith(b"bearer "):
        return auth[7:].strip().decode("utf-8", "replace")
    x = _find_header(headers, b"x-api-token")
    if x:
        return x.decode("utf-8", "replace")
    qs = scope.get("query_string", b"")
    if qs:
        parsed = parse_qs(qs.decode("utf-8", "replace"))
        tok = parsed.get("token")
        if tok:
            return tok[0]
    return None


class AuthMiddleware:
    """Pure ASGI middleware gating HTTP and WebSocket requests."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if not token_configured():
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        is_public = path in _NO_AUTH_PATHS or path.startswith("/docs")

        if scope["type"] == "websocket":
            if is_public:
                return await self.app(scope, receive, send)
            if _extract_token(scope) != os.environ["KAIROS_API_TOKEN"].strip():
                # Reject the upgrade before it is accepted.
                await send({"type": "websocket.close", "code": 1008})
                return
            return await self.app(scope, receive, send)

        # HTTP scope.
        if is_public or scope.get("method") == "OPTIONS":
            return await self.app(scope, receive, send)
        if _extract_token(scope) != os.environ["KAIROS_API_TOKEN"].strip():
            response = JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid API token"},
            )
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)


def install_auth(app) -> None:
    """Add the token-auth middleware to a FastAPI app.

    Idempotent: adding twice from tests is harmless.
    """
    app.add_middleware(AuthMiddleware)
