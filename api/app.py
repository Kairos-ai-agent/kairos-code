"""FastAPI Application for Kairos Code."""

from __future__ import annotations

import asyncio
import importlib.util
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
from api import deps as _deps
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
from kairos.browser import BrowserManager, set_default_manager
# R38.6 §33: Feishu (Lark) bot integration — push notifications
# and remote-control commands.
from api.routes import feishu as feishu_routes
from kairos.feishu import (FeishuBindingStore, FeishuBot,
                             FeishuEventForwarder)
# R38.6 §35: 企业微信「自建应用」双向通道（收消息回调 + 主动发送）。
from api.routes import wecom as wecom_routes
from kairos.wecom import (WeComBindingStore, WeComBot, WeComConfig,
                          WeComEventForwarder)
# R38.6 §34: Borrowed features from top AI agents (Plan Mode,
# Approval, Hooks, Skills invoke, Sandbox, Session Fork, IM
# platforms, FTS5 memory, more LLM providers).
from api.routes import borrowed as borrowed_routes
# Per-account IM (WeChat and friends): many accounts on one
# deployment, each conversation bound to its own workspace.
from api.routes import im as im_routes
from kairos.im_accounts import IMAccountStore
# 微信官方 ClawBot / iLink 通道：纯 Python 原生实现（无 OpenClaw/npm）。
from api.routes import weixin as weixin_routes
from kairos.weixin_ilink import WeixinAccountStore, WeixinChannel

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
# R38.6 §35: WeCom self-built-app forwarder (agent events → a member).
_wecom_forwarder: WeComEventForwarder | None = None
# Per-account IM store; None until the lifespan wires it.
_im_store: IMAccountStore | None = None
# 微信 iLink 通道：账号存储 + 每账号一条后台长轮询协程。
_weixin_store: WeixinAccountStore | None = None
_weixin_channel: WeixinChannel | None = None
# 微信审批桥：把闸门的 ASK 推到微信会话、把 /approve /deny 接回审批通道。
_weixin_approval_bridge = None


# ---------------------------------------------------------------------------
# Structured startup-failure registry.
#
# Why this exists: this module assembles ~a dozen subsystems at startup, each
# in its own ``try`` so one failure degrades only that feature. That design is
# right — but the failures used to be *silent*: a swallowed ``NameError`` here
# once took the approval channel, the long-running registry and the daemon
# supervisor down as a single log line, and nobody noticed the gate had stopped
# asking for permission (``kairos/sentinel.py``: with no channel an ASK verdict
# falls through to allow). A log line is not a signal a test can assert on.
#
# Every ``except`` in the lifespan now records here at least once, and the
# ``_STARTUP_SUBSYSTEMS`` table below lets a test iterate the subsystems that
# must prove they came up instead of hard-coding their names.
# ---------------------------------------------------------------------------
STARTUP_FAILURES: list[dict] = []


def _record_startup_failure(subsystem: str, exc: BaseException,
                            *, phase: str = "startup",
                            reason: str | None = None) -> dict:
    """Record that ``subsystem`` failed to assemble (never raises)."""
    entry: dict = {
        "subsystem": subsystem,
        "phase": phase,          # "startup" | "shutdown" | "probe"
        "error": f"{type(exc).__name__}: {exc}",
    }
    if reason:
        entry["reason"] = reason
    STARTUP_FAILURES.append(entry)
    return entry


def get_startup_failures() -> list[dict]:
    """Read entry for tests / diagnostics: the failed-assembly record.

    Empty means every assembled subsystem came up. Deliberately a plain
    function (``STARTUP_FAILURES`` is also mirrored onto
    ``app.state.startup_failures``) so a test can assert on the outcome
    without parsing log lines.
    """
    return list(STARTUP_FAILURES)


def _has_startup_failure(subsystem: str) -> bool:
    return any(f["subsystem"] == subsystem for f in STARTUP_FAILURES)


# Legitimately-skipped subsystems. Some subsystems are an *optional* extra that
# a given build simply does not ship (``browser_manager`` / playwright): they
# are expected to be absent, so recording them as a failure every startup is
# the boy-who-cried-wolf signal that trains a reader to ignore the list. They
# are recorded here instead, with the reason they may legitimately be missing,
# and ``STARTUP_FAILURES`` stays a list where every entry means something is
# actually wrong. Read via ``get_startup_skips()`` and mirrored on
# ``app.state.startup_skips``.
STARTUP_SKIPS: list[dict] = []


def _record_startup_skip(subsystem: str, reason: str,
                         *, phase: str = "probe") -> dict:
    """Record that ``subsystem`` was legitimately skipped (never raises)."""
    entry: dict = {
        "subsystem": subsystem,
        "phase": phase,          # "startup" | "shutdown" | "probe"
        "reason": reason,
    }
    STARTUP_SKIPS.append(entry)
    return entry


def get_startup_skips() -> list[dict]:
    """Read entry for tests / diagnostics: the legitimate-skip record.

    Empty means no optional subsystem was skipped because it is not part of
    this build. Deliberately a plain function (``STARTUP_SKIPS`` is also
    mirrored onto ``app.state.startup_skips``).
    """
    return list(STARTUP_SKIPS)


def _has_startup_skip(subsystem: str) -> bool:
    return any(s["subsystem"] == subsystem for s in STARTUP_SKIPS)


def _browser_is_optional() -> str | None:
    """Why the browser manager may *legitimately* be absent, or ``None``.

    ``playwright`` is the optional ``browser`` extra (``pyproject.toml``:
    ``browser = ["playwright>=1.40"]``), so a base install — and therefore any
    packaged build without the extra — has no browser at all. A browser manager
    that did not start because playwright is not installed is not a fault; the
    lifespan degrades the Browser tab gracefully and this returns the reason so
    it is recorded as a *skip*, not a failure (see ``_verify_startup_subsystems``
    and the browser ``except`` in ``lifespan``).

    When playwright *is* installed the browser is a required subsystem again:
    this returns ``None``, so a manager that still failed to come up is recorded
    as a real failure. That asymmetry is the point — optionality must never be
    used to launder a genuine fault.
    """
    if importlib.util.find_spec("playwright") is None:
        from kairos.browser import PLAYWRIGHT_MISSING_HINT
        return PLAYWRIGHT_MISSING_HINT
    return None


# Registered subsystems: (name, probe, optional_when). A probe returns truthy
# when the subsystem is *live in this process*. ``optional_when`` is a callable
# that returns a non-empty reason (a ``str``) when the subsystem may *legitimately*
# be absent in this build, or ``None`` when it is required. Lifespan verifies
# every entry after assembly and ``tests/test_startup_registry.py`` iterates this
# same tuple, so a subsystem that is wired up but never actually starts is caught
# by a test instead of by a user noticing the feature is a no-op.
def _probe_approvals() -> bool:
    from kairos import approvals as _approvals
    return _approvals.get_channel() is not None


def _probe_long_running_registry() -> bool:
    from kairos import long_running as _lr
    # get_registry() lazily *creates* one, so it cannot be the probe: only a
    # non-None module global proves the lifespan primed the real registry.
    return _lr._REGISTRY is not None


def _probe_autonomous_worker() -> bool:
    from kairos import autonomous_worker as _aw
    return _aw._WORKER is not None


def _probe_daemon_supervisor() -> bool:
    from kairos import daemon as _dm
    return _dm._SUPERVISOR is not None


_STARTUP_SUBSYSTEMS: tuple[tuple[str, object, object], ...] = (
    ("approvals", _probe_approvals, None),
    ("long_running_registry", _probe_long_running_registry, None),
    ("autonomous_worker", _probe_autonomous_worker, None),
    ("daemon_supervisor", _probe_daemon_supervisor, None),
    # The browser is the optional `browser` extra: absent playwright ⇒ the
    # manager is legitimately not there, not broken. Present playwright ⇒ a
    # manager that still did not start is a real failure (_browser_is_optional
    # returns None then). Late-bound through the module global so the
    # optionality decision is resolved at probe time (and a test can monkeypatch
    # ``_browser_is_optional`` — exactly as it does the ``_browser_manager``
    # probe — to simulate playwright being present).
    ("browser_manager", lambda: _browser_manager is not None,
     lambda: _browser_is_optional()),
    ("feishu", lambda: _feishu_store is not None, None),
    ("wecom", lambda: _wecom_forwarder is not None, None),
    ("im_store", lambda: _im_store is not None, None),
    ("weixin_ilink",
     lambda: _weixin_store is not None and _weixin_channel is not None, None),
)


def _record_missing_subsystem(name: str, exc: BaseException, reason: str,
                              optional_when) -> None:
    """Classify a subsystem that did not come up as a skip or a failure.

    ``optional_when`` (when not ``None``) is consulted first: a non-empty
    reason means the subsystem may legitimately be absent, so it is recorded in
    ``STARTUP_SKIPS`` (once) and the caller moves on. Otherwise it is recorded
    in ``STARTUP_FAILURES`` with ``reason`` (once), so a required subsystem is
    never excused by optionality it does not have.
    """
    optional_reason = optional_when() if optional_when is not None else None
    if optional_reason:
        if not _has_startup_skip(name):
            _record_startup_skip(name, optional_reason, phase="probe")
        return
    if not _has_startup_failure(name):
        _record_startup_failure(name, exc, phase="probe", reason=reason)


def _verify_startup_subsystems() -> list[dict]:
    """Probe every registered subsystem; record any that did not come up.

    Both the "probe raised" and the "probe reported not alive" paths consult
    the entry's ``optional_when`` first, so an optional subsystem that is
    legitimately absent lands in ``STARTUP_SKIPS`` instead of
    ``STARTUP_FAILURES`` — while a required subsystem (or an optional one whose
    optionality does not apply, e.g. playwright present) still records a
    failure, with the reason naming which of the two paths fired.
    """
    for name, probe, optional_when in _STARTUP_SUBSYSTEMS:
        try:
            alive = bool(probe())
        except Exception as exc:  # noqa: BLE001
            _record_missing_subsystem(name, exc, "probe raised", optional_when)
            continue
        if not alive:
            _record_missing_subsystem(
                name, RuntimeError("subsystem not alive after startup"),
                "probe reported not alive", optional_when)
    return get_startup_failures()


def _orch():
    """Resolve the live orchestrator at call time.

    Same shim as ``api/routes/projects.py:_orch`` (keep the two in sync): look
    the instance up on ``api.deps`` on every call instead of closing over the
    ``from api.deps import orchestrator`` name above, so tests that monkeypatch
    ``api.deps.orchestrator`` are seen here too.

    These lifespan blocks used to *call* ``_orch()`` without it ever being
    defined in this module. The ``NameError`` was swallowed by the surrounding
    ``except``, so the approval channel, the long-running registry / autonomous
    worker and the daemon supervisor never came up — the gate's ASK verdict had
    no channel and fell through to allowing the action. See
    ``tests/test_lifespan_subsystems.py``.
    """
    return _deps.orchestrator


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    global _browser_manager
    # Subsystems that must not depend on the browser being up.
    #
    # These used to sit *inside* the browser-manager ``try`` below, one level
    # deeper: a browser that failed to start (no browser in the image, a
    # locked profile) took the approval channel, the long-running registry and
    # the daemon supervisor down with it. Each one gets its own ``try`` now and
    # degrades alone. (They also used to call an ``_orch()`` this module never
    # defined — the swallowed ``NameError`` is why none of them ever came up.)
    from kairos.config.settings import settings as kairos_settings

    # Fresh record per startup, so a test that asserts "nothing failed" is not
    # fooled by a failure recorded by an earlier lifespan in the same process.
    STARTUP_FAILURES.clear()
    STARTUP_SKIPS.clear()
    app.state.startup_failures = STARTUP_FAILURES
    app.state.startup_skips = STARTUP_SKIPS

    # The gate can ask now. Before this line the permission ladder's ASK
    # verdict had no channel to ask through and fell through to allowing the
    # action (see kairos/approvals.py). The UI polls /api/approvals and
    # answers; a question nobody answers still resolves, to a refusal.
    try:
        from kairos import approvals
        approvals.set_channel(
            approvals.ApprovalChannel(message_bus=_orch().message_bus))
        log.info("Approval channel ready")
    except Exception as exc:  # noqa: BLE001
        _record_startup_failure("approvals", exc)
        log.warning("approval channel unavailable: %s", exc)

    # R38.6.4: prime the long-running registry with the message bus so async
    # subagents / goals / autonomous runs can publish subagent.completed
    # events. ``reg`` stays None when this fails, and the two blocks below are
    # skipped cleanly rather than raising NameError.
    reg = None
    try:
        from kairos.long_running import LongRunningRegistry, set_registry
        reg = LongRunningRegistry(
            message_bus=_orch().message_bus,
            persist_dir=kairos_settings.data_dir / ".kairos",
        )
        set_registry(reg)
        log.info("Long-running registry primed (R38.6 §34)")
    except Exception as exc:  # noqa: BLE001
        _record_startup_failure("long_running_registry", exc)
        log.debug("long-running registry init failed: %s", exc)

    if reg is not None:
        # R38.6.4: the autonomous worker consumes /autonomous job_ids off
        # that same registry.
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
            _record_startup_failure("autonomous_worker", exc)
            log.debug("autonomous worker start failed: %s", exc)

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
            _record_startup_failure("daemon_supervisor", exc)
            log.debug("daemon supervisor start failed: %s", exc)

    # R38.6 §32: bring up the browser manager. We do this
    # BEFORE the agents load so the Workbench "Browser" tab
    # is ready by the time the user opens it. Failure here
    # must NOT crash the whole app — the user can still
    # chat / edit code; only the Browser tab is degraded.
    try:
        _browser_manager = BrowserManager(
            data_dir=kairos_settings.data_dir,
        )
        await _browser_manager.start()
        browser_routes.set_manager(_browser_manager)
        # The agent's `browser` tool drives this same manager, so a
        # navigation the model performs is visible in the Browser tab and
        # its screenshot is of the page the user is already looking at.
        set_default_manager(_browser_manager)
        log.info("Browser manager started (R38.6 §32)")
    except Exception as exc:  # noqa: BLE001
        # The browser is the optional `browser` extra, so a failure here when
        # playwright is not installed is the *expected* state of a base build,
        # not a fault: record it as a skip so the failure list stays a real
        # signal. If playwright *is* installed (optionality returns None) the
        # manager not starting is a genuine fault and is recorded as one.
        optional_reason = _browser_is_optional()
        if optional_reason:
            if not _has_startup_skip("browser_manager"):
                _record_startup_skip("browser_manager", optional_reason,
                                     phase="startup")
            log.info("Browser manager skipped (%s): %s", optional_reason, exc)
        else:
            _record_startup_failure("browser_manager", exc)
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
            _record_startup_failure("feishu_forwarder", exc)
            log.debug("feishu forwarder bus attach failed: %s", exc)
        feishu_routes.set_dependencies(
            bot=_feishu_bot, store=_feishu_store,
            forwarder=_feishu_forwarder, orchestrator=orchestrator,
        )
        log.info("Feishu integration ready (R38.6 §33)")
    except Exception as exc:  # noqa: BLE001
        _record_startup_failure("feishu", exc)
        log.warning("Feishu setup failed: %s", exc)

    # R38.6 §35: 企业微信自建应用。配置由 settings_store 持有；这里
    # 只把 bot / 绑定表 / 事件转发器建起来并注入路由。转发器只在用户
    # 启用后启动（它需要收件人 UserID，未启用时无需空转）。
    global _wecom_forwarder
    try:
        from kairos.config.settings import settings as kairos_settings
        from kairos.settings_store import get_store as _get_settings
        wecom_db = kairos_settings.data_dir / "wecom.db"
        _wecom_bindings = WeComBindingStore(db_path=wecom_db)
        await _wecom_bindings.init()
        w = _get_settings().get().wecom
        _wecom_cfg = WeComConfig(
            corp_id=w.corp_id, corp_secret=w.corp_secret,
            agent_id=w.agent_id, token=w.token,
            encoding_aes_key=w.encoding_aes_key, enabled=w.enabled)
        _wecom_bot = WeComBot(config=_wecom_cfg)
        _wecom_forwarder = WeComEventForwarder(bot=_wecom_bot)
        if _wecom_cfg.enabled:
            try:
                _wecom_forwarder.attach(orchestrator.message_bus)
                await _wecom_forwarder.start()
            except Exception as exc:  # noqa: BLE001
                _record_startup_failure("wecom_forwarder", exc)
                log.debug("wecom forwarder bus attach failed: %s", exc)
        wecom_routes.set_dependencies(
            bot=_wecom_bot, bindings=_wecom_bindings,
            forwarder=_wecom_forwarder,
        )
        log.info("WeCom self-app integration ready (R38.6 §35)")
    except Exception as exc:  # noqa: BLE001
        _record_startup_failure("wecom", exc)
        log.warning("WeCom setup failed: %s", exc)

    # Per-account IM: the account / binding / queue store the
    # connectors talk to. No connector runs here -- they live
    # outside the repository and authenticate as one account each.
    global _im_store
    try:
        from kairos.config.settings import settings as _kairos_settings
        _im_store = IMAccountStore(
            db_path=_kairos_settings.data_dir / "im.db")
        await _im_store.init()
        im_routes.set_store(_im_store)
        log.info("IM account store ready")
    except Exception as exc:  # noqa: BLE001
        _record_startup_failure("im_store", exc)
        log.warning("IM store setup failed: %s", exc)

    # 微信官方 ClawBot / iLink 通道（纯 Python，无 OpenClaw/npm）：多账号
    # 扫码登录 + 每账号一条独立长轮询协程。账号/游标/绑定落在 data/weixin.db；
    # 只有已登录（有 token）且启用的账号才会在启动时起轮询。
    global _weixin_store, _weixin_channel, _weixin_approval_bridge
    try:
        from kairos.config.settings import settings as _kairos_settings
        _weixin_store = WeixinAccountStore(
            db_path=_kairos_settings.data_dir / "weixin.db")
        await _weixin_store.init()
        _weixin_channel = WeixinChannel(
            _weixin_store,
            dispatch=weixin_routes.make_dispatch(orchestrator, _weixin_store),
            # 出站文件（agent 生成/引用的文件）需要项目工作区根做围墙判定；解析器
            # 只读绑定表，拿不到根就不发文件（见 make_workspace_resolver）。
            workspace_resolver=weixin_routes.make_workspace_resolver(
                orchestrator, _weixin_store))
        weixin_routes.set_dependencies(
            channel=_weixin_channel, store=_weixin_store)
        for _acct in await _weixin_store.list_accounts():
            _creds = await _weixin_store.get_credentials(_acct.account_id)
            if _acct.enabled and _creds and _creds.get("token"):
                try:
                    await _weixin_channel.start_account(_acct.account_id)
                except Exception as exc:  # noqa: BLE001
                    _record_startup_failure("weixin_account", exc)
                    log.debug("weixin account start failed %s: %s",
                              _acct.account_id, exc)
        log.info("WeChat iLink channel ready")
        # 审批接线：闸门（kairos/sentinel.py 的 authorize_async）问出的问题不再
        # 只有网页在看 —— 属于微信会话的问题会被脱敏后推到那个会话，该会话里的
        # /approve /deny 通过 ApprovalChannel.resolve 接回来（与 Web 端同一个
        # 方法），超时（KAIROS_WEIXIN_APPROVAL_TIMEOUT，默认 300 秒）按拒绝结清。
        # 这里失败只是这条通道降级（问题仍会超时拒绝），不影响 app 启动。
        try:
            from kairos import approvals as _approvals
            from kairos.weixin_approvals import WeixinApprovalBridge
            _weixin_approval_bridge = WeixinApprovalBridge(
                _weixin_store, _weixin_channel, orchestrator=orchestrator)
            # ``orchestrator`` is the instance imported from api.deps at the top of
            # this module; the sibling blocks above call a ``_orch()`` that does
            # not exist in this file (see the warning below), so this one uses the
            # real name.
            _weixin_approval_bridge.attach(
                message_bus=orchestrator.message_bus,
                approval_channel=_approvals.get_channel())
            weixin_routes.set_approval_bridge(_weixin_approval_bridge)
            log.info("WeChat approval bridge ready")
            if _approvals.get_channel() is None:
                # No channel means the gate never asks, so there is nothing for
                # this bridge to push; approvals stay fail-closed and silent.
                log.warning(
                    "WeChat approval bridge: no approval channel in this "
                    "process — WeChat approvals stay fail-closed "
                    "(see docs/WEIXIN_ILINK.md §7)")
        except Exception as exc:  # noqa: BLE001
            _record_startup_failure("weixin_approval_bridge", exc)
            _weixin_approval_bridge = None
            log.warning("WeChat approval bridge unavailable: %s", exc)
    except Exception as exc:  # noqa: BLE001
        _record_startup_failure("weixin_ilink", exc)
        log.warning("WeChat iLink setup failed: %s", exc)

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
                        _record_startup_failure("mcp_warmup", exc,
                                                phase="shutdown")
                        log.debug("mcp warm-up failed: %s", exc)
            app.state.mcp_warm_task = asyncio.create_task(_warm_mcp())
            log.info("MCP warm-up scheduled for %d registry(ies)", len(pending))
    except Exception as exc:  # noqa: BLE001
        # Loud on purpose: a silent failure here means the servers never
        # start, and the only symptom is tools that quietly are not there.
        _record_startup_failure("mcp_warmup_schedule", exc)
        log.warning("MCP warm-up scheduling failed: %s", exc)

    # Every registered subsystem must now prove it is actually live. A probe
    # that reports "not alive" (and had no exception of its own) is recorded
    # here by name, so a startup call that silently does nothing becomes a
    # structured failure instead of a no-op the user discovers weeks later.
    _verify_startup_subsystems()
    app.state.startup_failures = STARTUP_FAILURES
    app.state.startup_skips = STARTUP_SKIPS

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
            # Expected: we just cancelled it on the line above.
            pass
        except Exception as exc:  # noqa: BLE001
            _record_startup_failure("mcp_warm_task", exc, phase="shutdown")
            log.debug("mcp warm task did not settle cleanly: %s", exc)

    # Shutdown: close the browser first, then LLM clients
    if _browser_manager is not None:
        try:
            await _browser_manager.stop()
        except Exception as exc:  # noqa: BLE001
            _record_startup_failure("browser_manager_stop", exc,
                                    phase="shutdown")
            log.debug("browser manager stop failed: %s", exc)
    # R38.6.4: the daemon supervisor (a heartbeat every 5s) and the autonomous
    # worker (the /autonomous queue) own background tasks. They are started in
    # the startup half above and must be stopped here, or they outlive the
    # client that started them and the test/process never settles.
    try:
        from kairos.daemon import get_supervisor
        _supervisor = get_supervisor()
        if _supervisor is not None:
            await _supervisor.stop()
    except Exception as exc:  # noqa: BLE001
        _record_startup_failure("daemon_supervisor_stop", exc, phase="shutdown")
        log.debug("daemon supervisor stop failed: %s", exc)
    try:
        from kairos.autonomous_worker import get_worker
        _autonomous_worker = get_worker()
        if _autonomous_worker is not None:
            await _autonomous_worker.stop()
    except Exception as exc:  # noqa: BLE001
        _record_startup_failure("autonomous_worker_stop", exc, phase="shutdown")
        log.debug("autonomous worker stop failed: %s", exc)
    if _feishu_forwarder is not None:
        try:
            await _feishu_forwarder.stop()
        except Exception as exc:  # noqa: BLE001
            _record_startup_failure("feishu_forwarder_stop", exc,
                                    phase="shutdown")
            log.debug("feishu forwarder stop failed: %s", exc)
    if _wecom_forwarder is not None:
        try:
            await _wecom_forwarder.stop()
        except Exception as exc:  # noqa: BLE001
            _record_startup_failure("wecom_forwarder_stop", exc,
                                    phase="shutdown")
            log.debug("wecom forwarder stop failed: %s", exc)
    # 微信 iLink：停掉每个账号的长轮询协程（并尽量通知服务端会话结束）。
    if _weixin_approval_bridge is not None:
        try:
            _weixin_approval_bridge.detach()
        except Exception as exc:  # noqa: BLE001
            _record_startup_failure("weixin_approval_bridge_detach", exc,
                                    phase="shutdown")
            log.debug("weixin approval bridge detach failed: %s", exc)
    if _weixin_channel is not None:
        try:
            await _weixin_channel.stop_all()
        except Exception as exc:  # noqa: BLE001
            _record_startup_failure("weixin_channel_stop", exc,
                                    phase="shutdown")
            log.debug("weixin channel stop failed: %s", exc)
    log.info("Shutting down: closing LLM provider clients...")
    for agent in orchestrator._agents.values():
        try:
            await agent._llm.close()
        except Exception as exc:  # noqa: BLE001
            _record_startup_failure("agent_llm_close", exc, phase="shutdown")
    # Then the project runtimes: the watchers, the worktrees, and each MCP
    # registry. Without that last part every MCP child this app spawned
    # outlives it -- and a bundled server is a second copy of this same
    # executable, so one run used to leave five of them behind, about 500 MB
    # of memory, still holding the binary against the next update.
    try:
        await orchestrator.close()
    except Exception as exc:  # noqa: BLE001
        _record_startup_failure("orchestrator_close", exc, phase="shutdown")
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
# R38.6 §35: 企业微信自建应用 — 回调收消息 + 主动发送
app.include_router(wecom_routes.router, tags=["wecom"])
app.include_router(im_routes.router, tags=["im"])
# 微信官方 ClawBot / iLink 通道 — 扫码登录 + 多账号
app.include_router(weixin_routes.router, tags=["weixin"])
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

# R2: durable/background tasks and the gate's approval channel. Both routers
# carry their own prefix, like browser/borrowed/sentinel above.
from api.routes import approvals as approvals_routes  # noqa: E402
from api.routes import tasks as tasks_routes  # noqa: E402

app.include_router(tasks_routes.router, tags=["tasks"])
app.include_router(approvals_routes.router, tags=["approvals"])

from api.routes import artifacts as artifacts_routes  # noqa: E402

app.include_router(artifacts_routes.router, tags=["artifacts"])

from api.routes import hooks as hooks_routes  # noqa: E402

app.include_router(hooks_routes.router, tags=["hooks"])

# Voice mode: speak the agent's replies. The synthesis engine lives in
# kairos/voice.py; this exposes it to the interface.
from api.routes import voice as voice_routes  # noqa: E402
app.include_router(voice_routes.router, tags=["voice"])

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
