"""FastAPI Application for Kairos Code."""

from __future__ import annotations

import asyncio
import logging
import os
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from kairos import __version__
from kairos.config.settings import settings
from kairos.metrics import install_middleware, install_metrics_endpoint
from api.auth import install_auth, token_configured
from api.deps import orchestrator
from api.routes.agents import router as agents_router
from api.routes.agents_md import router as agents_md_router
from api.routes.projects import router as projects_router
from api.routes.review import router as review_router
from api.routes.config import router as config_router
from api.routes.fs import router as fs_router
from api.routes.workbench import router as workbench_router
from api.routes.extensions import router as extensions_router
from api.routes.websocket import router as ws_router
from api.routes.memory import router as memory_router
from api.routes.teams import router as teams_router
from api.routes.checkpoints import router as checkpoints_router
from api.routes.update import router as update_router
from api.routes.traces import router as traces_router
from api.routes.cloud import router as cloud_router
from api.routes.cost import router as cost_router
from api.routes.skill_search import router as skill_search_router
from api.routes.trend import router as trend_router
from api.routes.alerts import router as alerts_router
from api.routes.gate import router as gate_router
# R38.6 §32: Browser panel — Playwright-backed per-project browser
# sessions used by the 5th tab in the Workbench.
from api.routes import browser as browser_routes
from kairos.browser import BrowserManager
# R38.6 §33: Feishu (Lark) bot integration — push notifications
# and remote-control commands.
from api.routes import feishu as feishu_routes
from kairos.feishu import (FeishuBindingStore, FeishuBot,
                             FeishuEventForwarder)
# R38.6 §34: Borrowed features from top AI agents (Plan Mode,
# Approval, Hooks, Skills invoke, Sandbox, Session Fork, IM
# platforms, FTS5 memory, more LLM providers).
from api.routes import borrowed as borrowed_routes

log = logging.getLogger(__name__)

# R38.6 §32: process-wide browser manager. The data dir is
# exposed in settings so tests can point it at a tmp dir.
_browser_manager: BrowserManager | None = None
# R38.6 §33: Feishu integration singletons (bot / store /
# forwarder). The store + bot are None until lifespan wires
# them; the forwarder is a no-op until attached to a bus.
_feishu_bot: FeishuBot | None = None
_feishu_store: FeishuBindingStore | None = None
_feishu_forwarder: FeishuEventForwarder | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    global _browser_manager
    # R38.6 §32: bring up the browser manager. We do this
    # BEFORE the agents load so the Workbench "Browser" tab
    # is ready by the time the user opens it. Failure here
    # must NOT crash the whole app — the user can still
    # chat / edit code; only the Browser tab is degraded.
    try:
        from kairos.config.settings import settings as kairos_settings
        _browser_manager = BrowserManager(
            data_dir=kairos_settings.data_dir,
        )
        await _browser_manager.start()
        browser_routes.set_manager(_browser_manager)
        log.info("Browser manager started (R38.6 §32)")
        # R38.6.4: prime the long-running registry with the
        # message bus so async subagents / goals / autonomous
        # runs can publish subagent.completed events.
        try:
            from kairos.long_running import LongRunningRegistry, set_registry
            reg = LongRunningRegistry(
                message_bus=_orch().message_bus,
                persist_dir=kairos_settings.data_dir / ".kairos",
            )
            set_registry(reg)
            log.info("Long-running registry primed (R38.6 §34)")
            # R38.6.4: also start the autonomous worker that
            # consumes /autonomous job_ids off the same registry.
            try:
                from kairos.autonomous_worker import (
                    AutonomousWorker, set_worker,
                )
                w = AutonomousWorker(orchestrator=_orch(), long_running_registry=reg)
                w.attach(_orch(), reg)
                await w.start()
                set_worker(w)
                log.info("Autonomous worker running (R38.6 §34)")
            except Exception as exc:  # noqa: BLE001
                log.debug("autonomous worker start failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            log.debug("long-running registry init failed: %s", exc)
        # R38.6.4: Daemon supervisor. Emits a daemon.heartbeat
        # event every 5s. Even though the current uvicorn process
        # is single-process, this gives the UI a "daemon alive"
        # signal and provides the ``/api/daemon/attach`` endpoint
        # the user-facing run command can call.
        try:
            from kairos.daemon import DaemonSupervisor, set_supervisor
            s = DaemonSupervisor(message_bus=_orch().message_bus)
            s.attach(reg, _orch().message_bus)
            await s.start()
            set_supervisor(s)
            log.info("Daemon supervisor started id=%s", s.daemon_id)
        except Exception as exc:  # noqa: BLE001
            log.debug("daemon supervisor start failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.warning("Browser manager failed to start: %s", exc)
        _browser_manager = None

    # R38.6 §33: Feishu bot. We bring up the binding store
    # regardless of whether the user has configured a webhook
    # (so the UI can show the config panel). The bot stays
    # disabled until the user enables it in Settings.
    global _feishu_bot, _feishu_store, _feishu_forwarder
    try:
        from kairos.config.settings import settings as kairos_settings
        feishu_db = kairos_settings.data_dir / "feishu.db"
        _feishu_store = FeishuBindingStore(db_path=feishu_db)
        await _feishu_store.init()
        cfg = await _feishu_store.get_config()
        _feishu_bot = FeishuBot(config=cfg)
        _feishu_forwarder = FeishuEventForwarder(bot=_feishu_bot)
        # Attach to the orchestrator's message bus so the
        # forwarder can subscribe to agent events.
        try:
            _feishu_forwarder.attach(orchestrator.message_bus)
            await _feishu_forwarder.start()
        except Exception as exc:  # noqa: BLE001
            log.debug("feishu forwarder bus attach failed: %s", exc)
        feishu_routes.set_dependencies(
            bot=_feishu_bot, store=_feishu_store,
            forwarder=_feishu_forwarder, orchestrator=orchestrator,
        )
        log.info("Feishu integration ready (R38.6 §33)")
    except Exception as exc:  # noqa: BLE001
        log.warning("Feishu setup failed: %s", exc)

    # MCP servers are configured by the user, so they can hang: a command
    # that is not installed, or one that has to fetch something over a
    # blocked network. Starting them on a background task keeps the port
    # opening in seconds -- the servers warm up while the UI loads, and the
    # first page a user sees is the app rather than a refused connection.
    try:
        from kairos.mcp_client import pending_registries
        pending = pending_registries()
        if pending:
            async def _warm_mcp() -> None:
                for reg in pending:
                    try:
                        await reg.start_all()
                    except Exception as exc:  # noqa: BLE001
                        log.debug("mcp warm-up failed: %s", exc)
            app.state.mcp_warm_task = asyncio.create_task(_warm_mcp())
            log.info("MCP warm-up scheduled for %d registry(ies)", len(pending))
    except Exception as exc:  # noqa: BLE001
        # Loud on purpose: a silent failure here means the servers never
        # start, and the only symptom is tools that quietly are not there.
        log.warning("MCP warm-up scheduling failed: %s", exc)

    yield

    # Shutdown: a warm-up still in flight would outlive the loop that owns it
    # ("Task was destroyed but it is pending", then a traceback about a closed
    # event loop), so end it here.
    _warm = getattr(app.state, "mcp_warm_task", None)
    if _warm is not None and not _warm.done():
        _warm.cancel()
        try:
            await _warm
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            pass

    # Shutdown: close the browser first, then LLM clients
    if _browser_manager is not None:
        try:
            await _browser_manager.stop()
        except Exception:  # noqa: BLE001
            pass
    if _feishu_forwarder is not None:
        try:
            await _feishu_forwarder.stop()
        except Exception:  # noqa: BLE001
            pass
    log.info("Shutting down: closing LLM provider clients...")
    for agent in orchestrator._agents.values():
        try:
            await agent._llm.close()
        except Exception:
            pass
    # Then the project runtimes: the watchers, the worktrees, and each MCP
    # registry. Without that last part every MCP child this app spawned
    # outlives it -- and a bundled server is a second copy of this same
    # executable, so one run used to leave five of them behind, about 500 MB
    # of memory, still holding the binary against the next update.
    try:
        await orchestrator.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("orchestrator close failed: %s", exc)
    log.info("Shutdown complete.")

# Create FastAPI app
app = FastAPI(
    title="Kairos Code",
    description="Multi-Agent Collaboration Platform for Software Development",
    version=__version__,
    lifespan=lifespan,
)

# CORS: read allowed origins from KAIROS_CORS_ORIGINS (comma-separated).
# Avoid wildcard when credentials are enabled — browsers reject that combo
# and we don't actually need either "*" in the default dev setup.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)

# Prometheus metrics: request count + latency histogram, exposed at /metrics
install_middleware(app)
install_metrics_endpoint(app)

# Optional API auth: when KAIROS_API_TOKEN is set, every /api route (except
# the allow-list) requires it. Default bind is 127.0.0.1 (see settings.py);
# if you bind a non-loopback host you MUST set KAIROS_API_TOKEN.
install_auth(app)
if not token_configured() and settings.host not in ("127.0.0.1", "localhost", "::1"):
    log.warning(
        "KAIROS_API_TOKEN is not set but KAIROS_HOST=%s is non-loopback. "
        "The API is reachable without authentication — set KAIROS_API_TOKEN.",
        settings.host,
    )


# R38.6 §22: global exception handler. Without this, any unhandled
# exception in a route handler returns FastAPI's default plain-text
# "Internal Server Error" body — which axios (and the browser) treat
# as an opaque 500 with no `detail` field. The frontend error toast
# then shows the unhelpful "Request failed with status code 500".
#
# With this handler, every unhandled exception:
#   1. logs the FULL Python traceback (so the developer can see
#      the root cause from the backend log)
#   2. returns a JSON body with `detail` (so the frontend can show
#      the real error message) and a correlation `error_id` (so
#      the user can paste it when reporting the bug)
#   3. keeps the route's existing HTTPException path untouched —
#      we only catch unhandled exceptions, not intentional raises.
@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    error_id = uuid.uuid4().hex[:12]
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    log.error(
        "[%s] Unhandled exception on %s %s: %s\n%s",
        error_id, request.method, request.url.path, exc, "".join(tb),
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"{type(exc).__name__}: {exc}",
            "error_id": error_id,
        },
    )

# Include routers
app.include_router(agents_router, prefix="/api/agents", tags=["agents"])
# R38.6 §29: project memory — read / write the project's
# AGENTS.md (loaded into every agent's system_prompt).
app.include_router(agents_md_router, prefix="/api", tags=["agents-md"])
app.include_router(projects_router, prefix="/api/projects", tags=["projects"])
app.include_router(review_router, prefix="/api/review", tags=["review"])
app.include_router(config_router, prefix="/api/config", tags=["config"])
# R38.6 §25: filesystem browse endpoints power the "click to pick a
# folder" UX in the FolderPicker modal. /api/fs/roots + /api/fs/list.
app.include_router(fs_router, prefix="/api", tags=["fs"])

# R38.6 §26: right-side Workbench panel — file tree, diff,
# checkpoint/restore, task progress, deliverables. /api/workbench/*.
app.include_router(workbench_router, prefix="/api", tags=["workbench"])

# R38.6 §27: Extensions API — pre-installed Skills / MCPs /
# Plugins (see scripts/install_extensions.py and
# docs/ROUND_37_REPORT.md §27).
app.include_router(extensions_router, prefix="/api", tags=["extensions"])
app.include_router(memory_router, prefix="/api/projects", tags=["memory"])
app.include_router(ws_router, prefix="/ws", tags=["websocket"])
app.include_router(teams_router, prefix="/api/projects", tags=["teams"])
app.include_router(checkpoints_router, prefix="/api/projects", tags=["checkpoints"])
app.include_router(traces_router, prefix="/api/projects", tags=["traces"])
app.include_router(cloud_router, prefix="/api/projects", tags=["cloud"])
app.include_router(cost_router, prefix="/api/cost", tags=["cost"])
app.include_router(skill_search_router, prefix="/api/skill_search", tags=["skill_search"])
app.include_router(trend_router, prefix="/api/trend", tags=["trend"])
app.include_router(alerts_router, prefix="/api/alerts", tags=["alerts"])
app.include_router(gate_router, prefix="/api/projects", tags=["gate"])
# R38.6 §32: Playwright-backed browser panel — /api/browser/{pid}/*
app.include_router(browser_routes.router, tags=["browser"])
# R38.6 §33: Feishu (Lark) bot — push notifications + remote commands
app.include_router(feishu_routes.router, tags=["feishu"])
# R38.6 §34: Borrowed features — Plan / Approval / Hooks / Skills /
# Sandbox / Fork / IM / Memory / Providers
app.include_router(borrowed_routes.router, tags=["borrowed"])
app.include_router(update_router, prefix="/api/update", tags=["update"])
# R38.6 §34: P2 features — Verification / Approval mode / Events /
# Async / LSP / Trajectory / A2A
from api.routes import p2_features as p2_features_routes
from api.routes import sentinel as sentinel_routes
app.include_router(p2_features_routes.router, tags=["borrowed-p2"])
app.include_router(sentinel_routes.router, tags=["sentinel"])

# Global message stream — mounted at /api/messages (not under /projects
# because FastAPI's path-param matching can shadow literal /messages
# routes). Used by the Collaboration page for the project-agnostic feed.
@app.get("/api/messages")
async def global_messages(limit: int = 100):
    return {"messages": orchestrator.get_message_history(limit=limit)}

@app.get("/")
async def root():
    """Root endpoint - redirects to the SPA when a built frontend dist is
    present (packaged single-EXE / production), else shows API info JSON.

    NOTE: the StaticFiles mount at "/" is added at the very end of this
    module, AFTER this route. Starlette matches routes in registration
    order, so this explicit ``@app.get("/")`` wins over the catch-all
    mount for the literal "/" path (and was shadowing the SPA). Redirecting
    here makes a browser hitting "/" land on the real UI.
    """
    if _frontend_dist_dir() is not None:
        return RedirectResponse(url="/index.html")
    return JSONResponse({
        "name": "Kairos Code",
        "version": __version__,
        "status": "running",
        "endpoints": {
            "health": "/api/health",
            "dashboard": "/api/dashboard",
            "messages": "/api/messages",
            "docs": "/docs",
            "frontend": "http://localhost:3000",
        },
    })

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": __version__}

@app.get("/api/dashboard")
async def dashboard():
    """Get dashboard overview data."""
    projects = orchestrator.list_projects()
    agent_states = orchestrator.get_all_agent_states()
    messages = orchestrator.get_message_history(limit=20)
    return {
        "project_count": len(projects),
        "agent_count": len(agent_states),
        "agents": agent_states,
        "recent_messages": messages,
    }


# ---------------------------------------------------------------------------
# Single-EXE / production frontend serving. MUST stay the LAST thing added
# so the "/" mount never shadows the /api routes registered above.
#
# Resolves web/dist from (in order):
#   1. KAIROS_FRONTEND_DIST — set by the packaged launcher (kairos/desktop_main.py)
#   2. <repo>/web/dist       — a normal checkout
#   3. sys._MEIPASS/web/dist — PyInstaller onefile extraction dir
# If none exists, "/" keeps returning the API info JSON above.
# ---------------------------------------------------------------------------
def _frontend_dist_dir() -> Path | None:
    env = os.environ.get("KAIROS_FRONTEND_DIST", "").strip()
    if env and Path(env).is_dir():
        return Path(env)
    try:
        import sys
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            p = Path(meipass) / "web" / "dist"
            if p.is_dir():
                return p
    except Exception:  # noqa: BLE001
        pass
    checkout = Path(__file__).resolve().parent.parent / "web" / "dist"
    if checkout.is_dir():
        return checkout
    return None


try:
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException

    class SPAStaticFiles(StaticFiles):
        """StaticFiles with an SPA fallback.

        The UI uses real paths (``/run``, ``/history``, ``/chat/<sid>``) rather
        than hash routes, so a deep link or a page refresh hits the server with
        a path that has no file behind it. Plain ``StaticFiles`` answers 404 and
        the user sees ``{"detail":"Not Found"}`` instead of the app — which is
        also why ``/run?project=…`` links were broken outside the dev server.

        Anything that is not an API path, not a built asset and has no file
        extension is served ``index.html`` and the client router takes over.
        """

        async def get_response(self, path: str, scope):  # type: ignore[override]
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code != 404 or scope.get("method") not in ("GET", "HEAD"):
                    raise
                clean = path.lstrip("/")
                if clean.startswith(("api/", "ws/")) or "." in Path(clean).name:
                    raise
                return await super().get_response("index.html", scope)

    _dist = _frontend_dist_dir()
    if _dist is not None:
        app.mount("/", SPAStaticFiles(directory=str(_dist), html=True),
                  name="frontend")
except Exception:  # noqa: BLE001
    log.debug("frontend static mount skipped", exc_info=True)
