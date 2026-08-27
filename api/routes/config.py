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
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

@router.get("/models")
async def list_models():
    return {
        "models": model_router.list_models(),
        "role_mappings": model_router.list_role_mappings(),
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
    return {
        "api_keys": masked,
        "raw_keys": api_keys,
        "custom_models": settings.get("custom_models", []),
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
        settings["custom_models"] = request.custom_models
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
        async with httpx.AsyncClient(timeout=10) as client:
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

@router.post("/models/custom/fetch")
async def fetch_custom_models(request: FetchModelsRequest):
    """Fetch models from OpenAI-compatible or Anthropic-compatible API."""
    base = request.base_url.rstrip("/")
    headers = {}

    if request.protocol == "anthropic":
        # Anthropic API typically has no public /models endpoint
        # Always return common models for Anthropic-compatible services
        # Try fetching but don't fail if it doesn't work
        try:
            if request.api_key:
                headers["x-api-key"] = request.api_key
                headers["anthropic-version"] = "2023-06-01"
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
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
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                models = [
                    {"id": m["id"], "name": m.get("id", "")}
                    for m in data.get("data", [])
                ]
                models.sort(key=lambda x: x["id"])
                return {"models": models, "count": len(models)}
    except Exception:
        import logging
        logging.getLogger(__name__).debug("Failed to fetch custom models", exc_info=True)

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
# Round 37: LLM test-connection (OpenAI / Anthropic compatible)
# ============================================================================

import logging
import urllib.error
import urllib.request
from pydantic import BaseModel
from fastapi import Body, HTTPException

logger = logging.getLogger(__name__)


class TestConnectionRequest(BaseModel):
    """Payload for the test-connection endpoint.

    `provider` is one of: ``"openai"`` (any OpenAI-compatible server)
    or ``"anthropic"``. The endpoint appends the right path
    (``/v1/models`` or ``/v1/messages``) and sends a minimal probe.
    """
    provider: str
    base_url: str
    api_key: str
    model: str = ""  # optional, only used for anthropic (in the body)


def _probe_get(base_url: str, api_key: str, timeout: float = 5.0):
    """Issue a GET ``/v1/models`` against an OpenAI-compatible base."""
    url = base_url.rstrip("/") + "/v1/models"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {
                "ok": 200 <= resp.status < 400,
                "status": resp.status,
                "detail": f"GET {url} -> {resp.status}",
            }
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return {
            "ok": False,
            "status": exc.code,
            "detail": f"HTTP {exc.code}: {body or exc.reason}",
        }
    except (urllib.error.URLError, OSError) as exc:
        return {
            "ok": False,
            "status": 0,
            "detail": f"connection failed: {exc}",
        }


def _probe_post_anthropic(base_url, api_key, model, timeout: float = 10.0):
    """Issue a minimal POST ``/v1/messages`` for Anthropic-compatible base."""
    url = base_url.rstrip("/") + "/v1/messages"
    body = json.dumps({
        "model": model or "claude-3-5-sonnet-latest",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {
                "ok": 200 <= resp.status < 400,
                "status": resp.status,
                "detail": f"POST {url} -> {resp.status}",
            }
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return {
            "ok": False,
            "status": exc.code,
            "detail": f"HTTP {exc.code}: {body or exc.reason}",
        }
    except (urllib.error.URLError, OSError) as exc:
        return {
            "ok": False,
            "status": 0,
            "detail": f"connection failed: {exc}",
        }


@router.post("/test_connection")
async def test_connection(req: TestConnectionRequest = Body(...)):
    """Verify that the user-supplied base URL + API key actually work.

    Returns ``{ok, status, detail}``:
      - ``{ok: true,  status: 200, detail: "GET ... -> 200"}`` on success
      - ``{ok: false, status: 401, detail: "HTTP 401: ..."}`` on auth failure
      - ``{ok: false, status: 0,   detail: "connection failed: ..."}`` on network error
    """
    if not req.base_url.strip():
        raise HTTPException(status_code=400, detail="base_url is required")
    if not req.api_key.strip():
        raise HTTPException(status_code=400, detail="api_key is required")
    provider = req.provider.strip().lower()
    if provider not in ("openai", "anthropic"):
        raise HTTPException(
            status_code=400,
            detail="provider must be 'openai' or 'anthropic'")
    if provider == "openai":
        result = _probe_get(req.base_url.strip(), req.api_key.strip())
    else:
        result = _probe_post_anthropic(
            req.base_url.strip(), req.api_key.strip(), req.model.strip())
    # Log the result for ops visibility (without the key).
    masked = req.api_key.strip()[:4] + "..." + req.api_key.strip()[-2:]
    logger.info("test_connection provider=%s ok=%s status=%s key=%s",
                provider, result["ok"], result["status"], masked)
    return result