"""Configuration API routes."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.deps import model_router, orchestrator
from api.schemas.agent import RoleModelAssignRequest
from kairos.llm.provider_registry import ProviderRegistry, create_provider
from kairos.llm.base import LLMConfig, LLMMessage

router = APIRouter()

from kairos.llm.model_router import SETTINGS_FILE

# LoopReview only ships two roles. Anything else is rejected at the API
# boundary so stale legacy roles from older settings.json files can't
# silently come back to life.
ALLOWED_ROLES = {"coder", "reviewer"}

def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}

def _save_settings(data: dict):
    # Merge, don't replace: data/settings.json also carries sections
    # owned by other subsystems (provider / provider_openai /
    # provider_anthropic from the SettingsDrawer, role_mappings from
    # the model router, loop_config). A plain replace would silently
    # wipe the user's LLM provider config and role assignments.
    existing = _load_settings()
    existing.update(data)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

@router.get("/models")
async def list_models():
    return {
        "models": model_router.list_models(),
        "role_mappings": model_router.list_role_mappings(),
        "tier_mappings": model_router.list_tier_mappings(),
    }


@router.get("/models/tiers")
async def list_tier_routing():
    """R38.6.4: per-task complexity tier routing.

    Returns the current fast / default / strong → model name
    mappings. The SettingsDrawer uses this to display and edit
    the tier assignments.
    """
    return {
        "tiers": model_router.list_tier_mappings(),
        "models": model_router.list_models(),
    }

@router.post("/models/assign")
async def assign_model(request: RoleModelAssignRequest):
    if request.role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown role '{request.role}'. Allowed: {sorted(ALLOWED_ROLES)}",
        )
    model_router.assign_role_model(request.role, request.model_name)
    # Auto-refresh agents with new model
    orchestrator.refresh_all_agents()
    return {"status": "ok", "role": request.role, "model": request.model_name}

@router.get("/providers")
async def list_providers():
    return {"providers": ProviderRegistry.list_providers()}

# ========== Settings ==========

@router.get("/settings")
async def get_settings():
    settings = _load_settings()
    api_keys = settings.get("api_keys", {})
    masked = {}
    for k, v in api_keys.items():
        if v and len(v) > 8:
            masked[k] = v[:4] + "****" + v[-4:]
        elif v:
            masked[k] = "****"
        else:
            masked[k] = ""
    # SECURITY: never return ``raw_keys``. Only the masked form leaves the
    # API; a caller that needs to *set* a key uses POST /api/config/settings.
    # Also strip any plaintext ``api_key`` inside ``custom_models`` (the
    # legacy R8 shape stores ``api_key`` per-model, see model_router.py).
    custom = []
    for m in settings.get("custom_models", []):
        safe = {k: v for k, v in m.items() if k not in ("api_key", "apiKey")}
        if m.get("api_key"):
            safe["api_key_set"] = True
        custom.append(safe)
    return {
        "api_keys": masked,
        "custom_models": custom,
    }

class SettingsRequest(BaseModel):
    api_keys: dict = {}
    custom_models: list[dict] = []

@router.post("/settings")
async def save_settings(request: SettingsRequest):
    settings = _load_settings()
    existing_keys = settings.get("api_keys", {})
    for k, v in request.api_keys.items():
        if v and "****" not in v:
            existing_keys[k] = v
    settings["api_keys"] = existing_keys
    if request.custom_models:
        # MERGE by name (don't replace wholesale). GET /settings never returns
        # the plaintext ``api_key`` (only ``api_key_set``), so the frontend
        # round-trips a key-less list — a blind replace would wipe every saved
        # custom-model key and the LLM would stop answering.
        prev_by_name = {
            m.get("name"): m
            for m in settings.get("custom_models", [])
            if isinstance(m, dict)
        }
        merged: list = []
        for m in request.custom_models:
            if not isinstance(m, dict):
                continue
            entry = dict(m)
            entry.pop("api_key_set", None)
            new_key = entry.get("api_key")
            prev = prev_by_name.get(entry.get("name"), {})
            if not new_key or "****" in str(new_key):
                # No new key supplied → keep the previously-saved one.
                if prev.get("api_key"):
                    entry["api_key"] = prev["api_key"]
                else:
                    entry.pop("api_key", None)
            merged.append(entry)
        settings["custom_models"] = merged
    _save_settings(settings)
    return {"status": "ok", "message": "Settings saved"}

# ========== DeepSeek Model List ==========

@router.get("/models/deepseek")
async def fetch_deepseek_models():
    settings = _load_settings()
    api_key = settings.get("api_keys", {}).get("deepseek", "")
    if not api_key:
        return {"models": [
            {"id": "deepseek-chat", "name": "DeepSeek Chat (V3)"},
            {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner (R1)"},
        ]}
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            resp = await client.get(
                "https://api.deepseek.com/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                models = [
                    {"id": m["id"], "name": m.get("id", m.get("name", ""))}
                    for m in data.get("data", [])
                ]
                return {"models": models}
    except Exception:
        import logging
        logging.getLogger(__name__).debug("Failed to fetch DeepSeek models", exc_info=True)
    return {"models": [
        {"id": "deepseek-chat", "name": "DeepSeek Chat (V3)"},
        {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner (R1)"},
    ]}

# ========== Custom Model ==========

class FetchModelsRequest(BaseModel):
    base_url: str
    api_key: str = ""
    protocol: str = "openai"  # "openai" or "anthropic"

def _normalize_models_base(base: str) -> str:
    """Trim a chat-completions suffix so ``{base}/models`` hits the API root.

    The SettingsDrawer derives ``baseUrl`` by dropping the last path segment
    of the endpoint URL, so
    ``https://api.deepseek.com/v1/chat/completions`` becomes
    ``https://api.deepseek.com/v1/chat``. Appending ``/models`` then requests
    ``/v1/chat/models`` and every provider answers 404 — the user-visible
    symptom is "拉取 model 列表失败" even though the key is fine. Strip the
    same suffixes ``LLMConfig`` strips, but keep ``/v1`` because that is
    where OpenAI-compatible providers serve ``/models``.
    """
    b = (base or "").rstrip("/")
    for suffix in ("/chat/completions", "/completions", "/messages", "/chat"):
        if b.lower().endswith(suffix):
            b = b[: -len(suffix)]
            break
    return b or (base or "").rstrip("/")


@router.post("/models/custom/fetch")
async def fetch_custom_models(request: FetchModelsRequest):
    """Fetch models from OpenAI-compatible or Anthropic-compatible API."""
    base = _normalize_models_base(request.base_url)
    # SSRF guard: never let a supplied base_url touch link-local /
    # cloud-metadata (169.254.0.0/16). Loopback + private LAN are allowed
    # so a local/internal LLM server still works once auth is enforced.
    from kairos.netsec import validate_config_url
    try:
        validate_config_url(base, what="base_url")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    headers = {}

    if request.protocol == "anthropic":
        # Anthropic API typically has no public /models endpoint
        # Always return common models for Anthropic-compatible services
        # Try fetching but don't fail if it doesn't work
        try:
            if request.api_key:
                headers["x-api-key"] = request.api_key
                headers["anthropic-version"] = "2023-06-01"
            async with httpx.AsyncClient(timeout=5, follow_redirects=False,
                                         trust_env=False) as client:
                resp = await client.get(f"{base}/models", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    models = [
                        {"id": m.get("id", m.get("name", "")), "name": m.get("name", m.get("id", ""))}
                        for m in data.get("data", [])
                    ]
                    if models:
                        models.sort(key=lambda x: x["id"])
                        return {"models": models, "count": len(models)}
        except Exception:
            import logging
            logging.getLogger(__name__).debug("Failed to fetch MiniMax models", exc_info=True)

        # Always return fallback list for Anthropic protocol
        return {"models": [
            {"id": "MiniMax-Text-01", "name": "MiniMax Text 01"},
            {"id": "abab6.5s-chat", "name": "Abab 6.5s Chat"},
            {"id": "abab5.5-chat", "name": "Abab 5.5 Chat"},
            {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet"},
            {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku"},
        ], "note": "Anthropic protocol - common models listed. You can also input model ID manually."}

    # OpenAI protocol
    try:
        url = f"{base}/models"
        if request.api_key:
            headers["Authorization"] = f"Bearer {request.api_key}"
        async with httpx.AsyncClient(timeout=10, follow_redirects=False,
                                     trust_env=False) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                models = [
                    {"id": m["id"], "name": m.get("id", "")}
                    for m in data.get("data", [])
                ]
                models.sort(key=lambda x: x["id"])
                return {"models": models, "count": len(models),
                        "note": f"fetched {len(models)} models from {base}"}
            # The provider exists but doesn't expose /v1/models
            # (common for proxies / aggregators). Return a friendly
            # error so the UI doesn't show a 500.
            return {"models": [], "count": 0,
                    "error": f"GET {url} returned {resp.status_code}",
                    "note": "provider has no /v1/models — pick a model manually"}
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug(
            "Failed to fetch custom models", exc_info=True)
        return {"models": [], "count": 0,
                "error": f"{type(exc).__name__} fetching {url}: {exc}"}

    return {"models": [], "error": "Failed to fetch models"}

# ========== Test Provider ==========

class TestProviderRequest(BaseModel):
    provider: str
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    protocol: str = "openai"  # "openai" or "anthropic"

# Built-in presets that don't need a custom base_url (D-03). Each entry
# resolves to (base_url, default_model, protocol). Anything else is
# treated as a generic custom endpoint and requires base_url.
_PROVIDER_PRESETS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat", "openai"),
    "openai":   (None, "gpt-4o-mini", "openai"),
    "anthropic": ("https://api.anthropic.com", "claude-sonnet-4-20250514", "anthropic"),
    "ollama":   ("http://localhost:11434/v1", "llama3.1", "openai"),
}

@router.post("/test-provider")
async def test_provider(request: TestProviderRequest):
    """Ping an LLM endpoint with a trivial request to verify connectivity.

    `provider` is resolved via a small preset table; everything else is
    treated as a custom endpoint that needs base_url + protocol.
    """
    api_key = request.api_key
    preset = _PROVIDER_PRESETS.get(request.provider)
    if preset:
        base_url, default_model, protocol = preset
        base_url = base_url or request.base_url
        model = request.model or default_model
        if not api_key:
            settings = _load_settings()
            api_key = settings.get("api_keys", {}).get(request.provider, "")
    elif request.provider == "custom":
        base_url = request.base_url
        model = request.model
        protocol = request.protocol
        if not base_url:
            return {"success": False, "message": "Base URL is required for custom provider"}
    else:
        return {"success": False, "message": f"Unknown provider: {request.provider}"}

    if not api_key and protocol == "anthropic":
        return {"success": False, "message": "API Key is required for Anthropic protocol"}

    # SSRF guard (config-mode): loopback + private LAN are allowed for
    # local/internal LLM servers, but link-local / cloud-metadata is not.
    if base_url:
        from kairos.netsec import validate_config_url
        try:
            validate_config_url(base_url, what="base_url")
        except ValueError as e:
            return {"success": False, "message": str(e)}

    try:
        config = LLMConfig(
            provider="anthropic" if protocol == "anthropic" else "openai",
            model=model,
            api_key=api_key or "sk-placeholder",
            base_url=base_url,
            max_tokens=32,
            timeout=15,
        )
        provider = create_provider(config)
        try:
            response = await provider.complete([LLMMessage(role="user", content="Say hi in 5 words.")])
        finally:
            await provider.close()
        return {"success": True, "message": f"OK: {response.content[:80]}"}
    except Exception as e:
        return {"success": False, "message": f"Error: {str(e)[:200]}"}

# ============================================================================
# Loop config (data/settings.json -> loop_config)
# ============================================================================

@router.get("/loop")
async def get_loop_config():
    """Return the current loop_config from data/settings.json.

    Falls back to defaults when the file is missing or malformed.
    """
    from kairos.core.orchestrator import _load_loop_config
    return _load_loop_config()

class LoopConfigRequest(BaseModel):
    specialists: list[str] = []
    best_of_n: int = 1

@router.post("/loop")
async def save_loop_config(request: LoopConfigRequest):
    """Persist loop_config to data/settings.json.

    Merges with whatever else is in the file so we don't clobber
    unrelated settings (custom_models, role_mappings, etc.).
    """
    from kairos.config.settings import settings
    path = settings.data_dir / "settings.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f) or {}
            except (json.JSONDecodeError, OSError):
                existing = {}
        existing["loop_config"] = {
            "specialists": list(request.specialists or []),
            "best_of_n": int(request.best_of_n or 1),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        return {"status": "ok", "loop_config": existing["loop_config"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Round 37: LLM test-connection (OpenAI and Anthropic compatible)
# ============================================================================

import logging
import re
import urllib.error
import urllib.request
from pydantic import BaseModel
from fastapi import Body, HTTPException

logger = logging.getLogger(__name__)


class TestConnectionRequest(BaseModel):
    """Payload for the test-connection endpoint.

    `provider` is one of: ``"openai"`` (any OpenAI-compatible server)
    or ``"anthropic"``.

    R38.5: the user provides a **full** ``endpoint_url`` (including
    the chat path), and the probe hits it as-is. No more path
    guessing / /v1 stripping / auto-appending — the user owns the
    URL. If ``endpoint_url`` is empty, the legacy ``base_url`` +
    auto-append path is used as a fallback (so old clients still
    work).
    """
    provider: str
    base_url: str = ""          # legacy: base URL for auto-append path
    api_key: str = ""
    endpoint_url: str = ""     # R38.5: full URL the probe will hit
    model: str = ""             # used as the `model` field in the probe body


def _strip_v1(base_url: str) -> str:
    """Strip a trailing ``/v1`` (or ``/V1``) from the base URL.

    Both ``https://api.openai.com/v1`` and ``https://api.openai.com``
    are common in the wild — and users reasonably paste the former
    (the OpenAI docs show it). We normalize so the probe never ends
    up at ``/v1/v1/models``.

    R38.5: this is now only used as a fallback when the user does
    not provide an explicit ``endpoint_url``.
    """
    return re.sub(r"/v1/?$", "", base_url.rstrip("/"), flags=re.IGNORECASE)


def _extract_error_message(body: str) -> str:
    """Pull a human-readable message out of a JSON error body.

    LLM APIs and proxies use a few common error shapes:
      - OpenAI and Anthropic / most proxies: ``{"error": {"message": "..."}}``
      - Some proxies wrap differently: ``{"error": "string"}``
      - Some proxies: ``{"message": "..."}`` at the top level
      - an OpenAI-compatible gateway apihub, others: ``{"error": {"message": "Invalid ..."}}``

    We return the first string we find that looks like an error
    message, so the user sees "HTTP 404: Invalid API key" instead
    of the raw ``HTTP 404: {"error":{"message":"Invalid API key"}}``.

    Returns ``""`` when the body isn't JSON-shaped (raw HTML error
    pages, plain text, etc.) — in that case the caller falls back
    to the raw body.
    """
    if not body:
        return ""
    body = body.strip()
    if not body.startswith("{"):
        return ""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return ""
    # OpenAI and Anthropic shape: {"error": {"message": "..."}}
    if isinstance(data.get("error"), dict):
        msg = data["error"].get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
        # Some proxies nest the message under a different key.
        for k in ("message", "detail", "error_description"):
            v = data["error"].get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    # Some proxies: {"error": "string message"}
    if isinstance(data.get("error"), str) and data["error"].strip():
        return data["error"].strip()
    # Some proxies: top-level {"message": "..."}
    msg = data.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg.strip()
    return ""


def _format_probe_error(status_code: int, body: str, fallback: str) -> str:
    """Build the user-facing ``detail`` string for a probe error.

    Prefer a parsed error message from the JSON body over the raw
    body, so the user sees "HTTP 404: Invalid API key" instead of
    ``HTTP 404: {"error":{"message":"Invalid API key"}}``.

    Truncates long raw bodies at 500 chars (was 200) so the user
    can see the full error message even when it's verbose.
    """
    parsed = _extract_error_message(body)
    if parsed:
        return f"HTTP {status_code}: {parsed}"
    if body:
        body = body.strip()
        if len(body) > 500:
            body = body[:500] + "…"
        return f"HTTP {status_code}: {body}"
    return f"HTTP {status_code}: {fallback}"


def _probe_post_openai_chat(
    endpoint_url: str,
    api_key: str,
    model: str,
    base_url: str = "",
    timeout: float = 10.0,
):
    """Issue a minimal ``POST`` against an OpenAI-compatible endpoint.

    R38.5: prefer the user-supplied ``endpoint_url`` (full URL,
    including the chat path). The probe hits it as-is — no path
    guessing, no /v1 stripping, no /v1/chat/completions appending.
    Falls back to the legacy auto-construct (``base_url`` +
    ``/v1/chat/completions``) only when ``endpoint_url`` is empty.

    The body is the smallest one most servers accept: one user
    message, ``max_tokens=1``. This costs the user ~1 token of
    real usage on paid APIs, but it's the price of a real probe.
    """
    if endpoint_url.strip():
        url = endpoint_url.strip()
    else:
        url = _strip_v1(base_url) + "/v1/chat/completions"
    body = json.dumps({
        "model": model or "gpt-4o-mini",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async def _do_probe():
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as c:
                return await c.post(url, content=body, headers=headers)
        # We can't await here (this function is sync, called from async
        # via run_in_executor-or-async wrapper). Re-implement as sync
        # via httpx.Client for simplicity.
        with httpx.Client(timeout=timeout, trust_env=False) as c:
            resp = c.post(url, content=body, headers=headers)
        return {
            "ok": 200 <= resp.status_code < 400,
            "status": resp.status_code,
            "detail": f"POST {url} -> {resp.status_code}",
        }
    except httpx.HTTPStatusError as exc:
        body_text = ""
        try:
            body_text = exc.response.text
        except Exception:
            pass
        return {
            "ok": False,
            "status": exc.response.status_code,
            "detail": _format_probe_error(
                exc.response.status_code, body_text, str(exc))
                     + f" (url: {url})",
        }
    except (httpx.RequestError, OSError) as exc:
        return {
            "ok": False,
            "status": 0,
            "detail": f"connection failed: {exc} (url: {url})",
        }


def _probe_post_anthropic(
    endpoint_url: str,
    api_key: str,
    model: str,
    base_url: str = "",
    timeout: float = 10.0,
):
    """Issue a minimal POST against an Anthropic-compatible endpoint.

    R38.5: prefer the user-supplied ``endpoint_url`` (full URL);
    fall back to ``base_url + /v1/messages`` if not provided.
    """
    if endpoint_url.strip():
        url = endpoint_url.strip()
    else:
        url = _strip_v1(base_url) + "/v1/messages"
    body = json.dumps({
        "model": model or "claude-3-5-sonnet-latest",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }).encode("utf-8")
    # R38.6.3: Anthropic uses ``x-api-key`` (lowercase) per their
    # docs, but MiniMax's Anthropic-compatible proxy expects
    # ``X-Api-Key`` (capital X) per their docs. Some proxies
    # are case-sensitive on header lookups even though HTTP
    # itself is case-insensitive. Send BOTH to cover both
    # real Anthropic and the MiniMax-style proxy.
    headers = {
        "x-api-key": api_key,
        "X-Api-Key": api_key,
        "Authorization": f"Bearer {api_key},  # also covers "
                                  "Anthropic-compat proxies that "
                                  "only check Bearer",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as c:
            resp = c.post(url, content=body, headers=headers)
        return {
            "ok": 200 <= resp.status_code < 400,
            "status": resp.status_code,
            "detail": f"POST {url} -> {resp.status_code}",
        }
    except httpx.HTTPStatusError as exc:
        body_text = ""
        try:
            body_text = exc.response.text
        except Exception:
            pass
        return {
            "ok": False,
            "status": exc.response.status_code,
            "detail": _format_probe_error(
                exc.response.status_code, body_text, str(exc))
                     + f" (url: {url})",
        }
    except (httpx.RequestError, OSError) as exc:
        return {
            "ok": False,
            "status": 0,
            "detail": f"connection failed: {exc} (url: {url})",
        }


@router.post("/test_connection")
async def test_connection(req: TestConnectionRequest = Body(...)):
    """Verify that the user-supplied endpoint URL + API key actually work.

    R38.5: the user provides a full ``endpoint_url`` (including the
    chat path). The probe hits it as-is. We still accept ``base_url``
    as a legacy fallback — when ``endpoint_url`` is empty, we auto-
    construct (baseUrl + ``/v1/chat/completions`` or
    ``/v1/messages``).

    Returns ``{ok, status, detail}``:
      - ``{ok: true,  status: 200, detail: "POST ... -> 200"}`` on success
      - ``{ok: false, status: 401, detail: "HTTP 401: ..."}`` on auth failure
      - ``{ok: false, status: 0,   detail: "connection failed: ..."}`` on network error
    """
    # R38.5: require EITHER endpoint_url OR base_url. The frontend
    # always sends endpoint_url now; base_url is the legacy fallback.
    if not req.endpoint_url.strip() and not req.base_url.strip():
        raise HTTPException(
            status_code=400,
            detail="endpoint_url (or legacy base_url) is required")
    if not req.api_key.strip():
        raise HTTPException(status_code=400, detail="api_key is required")
    provider = req.provider.strip().lower()
    if provider not in ("openai", "anthropic"):
        raise HTTPException(
            status_code=400,
            detail="provider must be 'openai' or 'anthropic'")
    # SSRF guard: endpoint_url / base_url is fully user-supplied, so block
    # link-local / cloud-metadata targets (169.254.0.0/16) before we POST
    # the API key to it. Loopback / private LAN stay allowed so a local
    # LLM server can still be tested.
    from kairos.netsec import validate_config_url
    target = req.endpoint_url.strip() or req.base_url.strip()
    try:
        validate_config_url(target, what="endpoint_url")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if provider == "openai":
        result = _probe_post_openai_chat(
            req.endpoint_url.strip(),
            req.api_key.strip(),
            req.model.strip(),
            base_url=req.base_url.strip(),
        )
    else:
        result = _probe_post_anthropic(
            req.endpoint_url.strip(),
            req.api_key.strip(),
            req.model.strip(),
            base_url=req.base_url.strip(),
        )
    # Log the result for ops visibility (without the key).
    masked = req.api_key.strip()[:4] + "..." + req.api_key.strip()[-2:]
    logger.info("test_connection provider=%s ok=%s status=%s key=%s",
                provider, result["ok"], result["status"], masked)
    return result