"""Optional API authentication.

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
   interface (see api/auth.install_auth).

WebSocket ``/ws`` upgrades are gated by the same token (via ``?token=``) so a
token-configured deployment isn't left with a wide-open socket.
"""

from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# Paths that must stay reachable without a token (useful to ops/UI health).
_NO_AUTH_PATHS = {
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/health",
}


def token_configured() -> bool:
    return bool(os.environ.get("KAIROS_API_TOKEN", "").strip())


class _AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not token_configured():
            return await call_next(request)
        if request.method == "OPTIONS":
            # CORS preflight never carries our token; let it through.
            return await call_next(request)
        path = request.url.path
        if path in _NO_AUTH_PATHS or path.startswith("/docs"):
            return await call_next(request)
        token = os.environ["KAIROS_API_TOKEN"].strip()
        provided: str | None = None
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and len(auth) > 7:
            provided = auth[7:].strip()
        if provided is None:
            provided = request.headers.get("x-api-token")
        if provided is None:
            provided = request.query_params.get("token")
        if provided != token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid API token"},
            )
        return await call_next(request)


def install_auth(app) -> None:
    """Add the token-auth middleware to a FastAPI app.

    Idempotent: adding twice from tests is harmless.
    """
    app.add_middleware(_AuthMiddleware)
