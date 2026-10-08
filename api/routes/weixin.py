"""微信官方 ClawBot / iLink 通道的 HTTP 接口。

路由一览::

    POST   /api/weixin/login/start              取二维码（点一下出码）
    GET    /api/weixin/login/status?qrcode=<id> 轮询扫码状态（扫码后自动建账号）
    GET    /api/weixin/login/qr.png?qrcode=<id> 登录二维码 PNG（可扫；省略 qrcode 则现取一张）
    GET    /api/weixin/accounts                 列出账号（脱敏，无 token）
    DELETE /api/weixin/accounts/{id}            删除账号（停轮询 + 清数据）
    POST   /api/weixin/accounts/{id}/send       用某账号主动发一条（测试用）
    GET    /api/weixin/bindings                 列出 账号+聊天对象 → 项目 的绑定

安全：``token`` 是密钥，本路由**绝不**把它放进任何响应（含错误响应），
也不写日志。``/login/status`` 只回 :meth:`WeixinLoginSession.public_state`
（结构里没有 token 字段）；``/accounts`` 用 ``WeixinAccount.to_dict()``。

扫描登录的完整流：``login/start`` 拿到二维码 id 和图片内容（前端渲染成
QR），前端轮询 ``login/status``；服务端每次 ``poll()`` 一次。当状态变为
``confirmed`` 时，token 落库（``WeixinAccountStore``）、该账号的后台长轮询
协程启动，接口只回账号 id。
"""
from __future__ import annotations

import io
import logging
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from kairos.feishu import parse_command
from kairos.weixin_ilink import (
    DEFAULT_BOT_TYPE,
    WEIXIN_MEDIA_MAX_BYTES,
    ILinkClient,
    ILinkError,
    MediaDownloadError,
    WeixinAccountStore,
    WeixinChannel,
    WeixinLoginSession,
    download_media_to,
)
from api.routes.projects import (
    ATTACHMENTS_DIRNAME,
    _project_root,
    attachment_prompt_block,
)
from kairos.weixin_approvals import APPROVAL_COMMANDS, stop_project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weixin", tags=["weixin"])

# 运行期依赖，由 api/app.py 的 lifespan 注入。
_channel: Optional[WeixinChannel] = None
_store: Optional[WeixinAccountStore] = None
# 审批桥：把闸门的问题推到微信、把 /approve /deny 接回现有的审批机制。
# 由 api/app.py 的 lifespan 注入；为 None 时这几个命令回一条可读提示
# （待审批的动作仍会按超时自动拒绝 —— fail-closed 不变）。
_approval_bridge: Optional[Any] = None
# 取二维码 / 轮询登录时用的客户端工厂（测试可指向本地 mock）。
_login_client_factory: Optional[Callable[[], ILinkClient]] = None
# 进行中的登录会话：qrcode id → WeixinLoginSession。
_sessions: Dict[str, WeixinLoginSession] = {}
# 二维码 PNG 缓存：qrcode id → PNG 字节，同一张码只渲染一次。
# 键是二维码 id，刷新后 id 会变，所以旧图自动失效；容量上限防止无限增长。
_qr_png_cache: Dict[str, bytes] = {}
_QR_PNG_CACHE_MAX = 32


def set_dependencies(channel: Optional[WeixinChannel] = None,
                     store: Optional[WeixinAccountStore] = None,
                     login_client_factory: Optional[Callable[[], ILinkClient]] = None
                     ) -> None:
    """由 api/app.py 的 lifespan 调用，注入运行期依赖。"""
    global _channel, _store, _login_client_factory
    if channel is not None:
        _channel = channel
    if store is not None:
        _store = store
    if login_client_factory is not None:
        _login_client_factory = login_client_factory


def set_approval_bridge(bridge: Optional[Any]) -> None:
    """注入审批桥（由 api/app.py 的 lifespan 调用）。"""
    global _approval_bridge
    _approval_bridge = bridge


def reset_dependencies() -> None:
    """测试用：清空注入的依赖与会话。"""
    global _channel, _store, _login_client_factory, _approval_bridge
    _channel = None
    _store = None
    _login_client_factory = None
    _approval_bridge = None
    _sessions.clear()
    _qr_png_cache.clear()


def _check() -> WeixinAccountStore:
    if _channel is None or _store is None:
        raise HTTPException(status_code=503, detail="weixin not initialized")
    return _store


def _make_login_client() -> ILinkClient:
    if _login_client_factory is not None:
        return _login_client_factory()
    return ILinkClient()


def _purge_sessions() -> None:
    """丢掉过期 / 已完成的登录会话，避免长时间累积。"""
    for qid, session in list(_sessions.items()):
        if session.connected or session.is_expired():
            _sessions.pop(qid, None)


def _render_qr_png(content: str) -> bytes:
    """把二维码内容渲染成 PNG 字节（segno：纯 Python，无原生依赖）。

    编码的内容就是 ``qrcode_img_content``（``liteapp.weixin.qq.com`` 的链接），
    **不是** token。``segno`` 是运行依赖，但这里懒导入：缺了只让这一个接口
    返回 503，不拖垮整个 app 的导入。
    """
    import segno  # noqa: PLC0415 —— 懒导入，见上

    buf = io.BytesIO()
    # error="m" 是微信登录码通常用的纠错级别；scale=8（每模块 8px）+ border=4
    # （规范要求的 4 模块静默区）确保手机在缩略图/强光下也能扫到。
    segno.make(content, error="m").save(buf, kind="png", scale=8, border=4)
    return buf.getvalue()


def _qr_png_cached(qrcode: str, content: str) -> bytes:
    """按二维码 id 缓存 PNG；同一张码不重复渲染。"""
    png = _qr_png_cache.get(qrcode)
    if png is None:
        png = _render_qr_png(content)
        # 简单 LRU 兜底：满了先丢最旧的那个键（dict 保序）。
        while len(_qr_png_cache) >= _QR_PNG_CACHE_MAX:
            _qr_png_cache.pop(next(iter(_qr_png_cache)), None)
        _qr_png_cache[qrcode] = png
    return png


async def _session_for_qr(qrcode: str) -> WeixinLoginSession:
    """找（或现取）一个登录会话。

    - 带 ``qrcode``：命中已有会话；未知则 404。
    - 省略 ``qrcode``：等价于先调一次 ``login/start``，把新会话放进表里。
    """
    if qrcode:
        session = _sessions.get(qrcode)
        if session is None:
            raise HTTPException(status_code=404, detail="未知或已结束的登录会话")
        return session
    _purge_sessions()
    session = WeixinLoginSession(_make_login_client(),
                                 bot_type=DEFAULT_BOT_TYPE)
    try:
        await session.start()
    except ILinkError as exc:
        raise HTTPException(status_code=502,
                            detail=f"取二维码失败: {exc}") from exc
    if not session.qrcode:
        raise HTTPException(status_code=502, detail="服务器未返回二维码")
    _sessions[session.qrcode] = session
    return session


# ---------------------------------------------------------------------------
# 分发：账号 + 聊天对象 → 独立项目 → agent
# ---------------------------------------------------------------------------

async def _fold_inbound_media(project, prompt: str, media) -> str:
    """把入站媒体落盘到项目附件目录，并按 Web 同一套折进提示词。

    复用 ``api/routes/projects.py`` 的 ``attachment_prompt_block``（成功项）——
    与 Web 会话完全一致的 ``[附件]`` 块，路径相对项目根、Coder 的 file 工具
    能直接读；**不另写一套附件逻辑**。

    失败 / 超限 / 不支持的类型都会追加一段可读的 ``[微信媒体]`` 说明，交给
    agent（它会把结论讲给用户），**绝不静默**。
    """
    root = _project_root(project)
    out_dir = root / ATTACHMENTS_DIRNAME
    rels: List[str] = []
    failures: List[str] = []

    for ref in media or []:
        label = getattr(ref, "label", "") or "[媒体]"
        try:
            path = await download_media_to(ref, out_dir)
        except MediaDownloadError as exc:
            reason = exc.reason
            if exc.too_large:
                reason += "（占位符 %s 保留）" % label
            failures.append(f"- {label} {reason}")
            logger.warning("weixin: 媒体未下载 kind=%s reason=%s",
                           getattr(ref, "kind", ""), reason)
            continue
        except Exception as exc:  # noqa: BLE001 - 一条坏媒体不该拖垮整条消息
            failures.append(f"- {label} 下载失败: {type(exc).__name__}")
            logger.exception("weixin: 媒体处理异常 kind=%s",
                             getattr(ref, "kind", ""))
            continue

        rels.append(f"{ATTACHMENTS_DIRNAME}/{path.name}")
        # 图片额外经 multimodal 校验（复用现有图片机制）。注意：Coder.chat 只收
        # 文本，这里不注入 content parts —— 那会改 Coder/loop，本轮被禁止。
        if getattr(ref, "kind", "") == "image":
            try:
                from kairos.multimodal import load_image
                load_image(path, max_bytes=WEIXIN_MEDIA_MAX_BYTES)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"- {label} 已保存但图片校验失败: {exc}")

    blocks: List[str] = []
    if prompt:
        blocks.append(prompt)
    if rels:
        try:
            block = attachment_prompt_block(root, rels)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"- 附件说明生成失败: {exc}")
        else:
            if block:
                blocks.append(block)
    if failures:
        blocks.append("[微信媒体] 以下内容未能交给 agent：\n" + "\n".join(failures))
    return "\n\n".join(blocks)


async def _answer_message(project, text: str, *, has_media: bool) -> str:
    """按 Web ``/chat`` 同一条路由决定车道：编码/长任务走 Coder，闲聊/问答走通用。

    - 复用 ``kairos.task_router.route_task``（``requirement`` = 用户原文，
      ``workspace`` = 项目根），判定输入与 ``api/routes/projects.py:chat`` 一致。
    - 通用车道返回空 / 无模型 / 抛异常时**回退到 Coder**，等价于改动前的行为，
      保证「纯文本消遣消息仍能拿到回复」不回归（不会变成什么都不回）。
    - **带媒体**（图片/文件/视频）的消息**保持原行为**直接走 Coder：通用车道只
      收文本，看不到已折进提示词的附件块，若走通用会丢掉用户刚发来的文件。
    """
    if has_media:
        return await project.coder.chat(text)
    try:
        from api.routes.projects import _project_root
        from kairos.skeleton.service import run_chat_reply
        from kairos.task_router import route_task
    except Exception:  # noqa: BLE001 - 路由只是优化，失败不致命
        logger.exception("weixin: 路由依赖不可用，保持 Coder 车道")
        return await project.coder.chat(text)

    root = _project_root(project)
    try:
        decision = route_task(requirement=text, workspace=root)
    except Exception:  # noqa: BLE001 - 判定失败即保持原行为
        logger.exception("weixin: 路由判定失败，保持 Coder 车道")
        return await project.coder.chat(text)

    if not decision.uses_skeleton:
        return await project.coder.chat(text)

    try:
        reply = await run_chat_reply(
            kind=decision.workspace_kind, root=str(root), message=text)
    except Exception:  # noqa: BLE001 - 通用车道出错不能丢掉这一轮
        logger.exception("weixin: 通用车道失败，回退 Coder")
        return await project.coder.chat(text)

    if reply is None or not reply.strip():
        return await project.coder.chat(text)
    return reply


async def _control_command(orchestrator, store: WeixinAccountStore,
                           account_id: str, chat_id: str, cmd: str,
                           args: List[str]) -> str:
    """微信里的 ``/approve`` · ``/deny`` · ``/stop``。

    **不另建一套**：``/approve`` / ``/deny`` 由审批桥调用
    ``ApprovalChannel.resolve``（与 Web 端 ``POST /api/approvals/{id}`` 同一个
    方法）；``/stop`` 走 ``orchestrator.stop_loop`` + ``stop_skeleton_run``
    （与 ``POST /{id}/stop`` 同一对调用）。桥没接线时 ``/stop`` 仍能停，
    ``/approve`` / ``/deny`` 回一条可读提示 —— 未决问题照旧按超时拒绝。
    """
    if _approval_bridge is not None:
        try:
            reply = await _approval_bridge.handle_command(account_id, chat_id,
                                                          cmd, args)
        except Exception as exc:  # noqa: BLE001
            logger.exception("weixin: 审批命令处理失败 %s", cmd)
            return f"处理失败：{type(exc).__name__}"
        if reply is not None:
            return reply
    if cmd == "stop":
        # 停止本身不依赖审批：桥不在也走同一条停止路径。
        try:
            project_id = await store.lookup(account_id, chat_id)
        except Exception:  # noqa: BLE001
            logger.exception("weixin: /stop 绑定查询失败")
            return "查询会话绑定失败，无法停止。"
        if not project_id:
            return "这个会话还没有绑定项目，没有可停止的任务。"
        _names, text = stop_project(orchestrator, project_id)
        return text
    return "审批通道未接线：这条命令暂时无效；需要人批的操作会按超时自动拒绝。"


def make_dispatch(orchestrator, store: WeixinAccountStore
                  ) -> Callable[..., Any]:
    """构造 channel 用的 dispatch 回调。

    与 ``api/routes/wecom.py:_dispatch`` 同构：普通文本走 agent，``/`` 开头
    的命令同上；每个「账号 + 聊天对象」映射到**独立的 Kairos 项目**（首次
    发消息时自动创建，绑定记录在 store）。

    入站媒体（图片/文件/视频）由 channel 以第 4 个参数 ``media`` 传来；这里
    下载到该项目的附件目录，再按 Web 会话同一套折进提示词。
    """

    async def dispatch(account_id: str, chat_id: str, text: str,
                       media: Optional[List[Any]] = None) -> str:
        cmd, args = parse_command(text)
        if cmd == "noop":
            return ""
        if cmd in APPROVAL_COMMANDS:
            # 审批 / 停止：接到**现有**的审批与停止机制上（见 _control_command）。
            return await _control_command(orchestrator, store, account_id,
                                          chat_id, cmd, args)
        if cmd == "help":
            return ("/status · /projects · /use <id> · /chat <text> · /help\n"
                    "/approve · /deny · /stop —— 回答需要审批的操作 / 停止当前运行\n"
                    "直接发消息即与当前项目的 agent 对话。")
        if cmd == "use":
            if not args:
                return "用法: /use <project_id>"
            await store.bind(account_id, chat_id, args[0])
            return f"已绑定当前会话 → {args[0]}"
        if cmd == "projects":
            try:
                projs = orchestrator.list_projects()
            except Exception as exc:  # noqa: BLE001
                return f"获取项目失败: {exc}"
            if not projs:
                return "还没有项目。"
            return "\n".join(f"• {p.id}  {p.name}" for p in projs[:20])
        if cmd == "status":
            project_id = await store.lookup(account_id, chat_id)
            return (f"当前项目: {project_id}" if project_id
                    else "尚未绑定项目，先 /use <id> 或直接发消息自动创建。")

        prompt = " ".join(args) if cmd == "chat" else text
        project = await _resolve_project(orchestrator, store, account_id, chat_id)
        coder = getattr(project, "coder", None) if project is not None else None
        if coder is None:
            return "当前会话还没有可用的 agent。"
        if media:
            # 入站图片/文件/视频：真下载到项目附件目录，按 Web 同一套折进提示词。
            prompt = await _fold_inbound_media(project, prompt, media)
        try:
            # Round 37 路由：编码/长任务走 Coder（原样），闲聊/问答走通用车道；
            # 带媒体的消息保持原行为（见 ``_answer_message``）。
            reply = await _answer_message(project, prompt, has_media=bool(media))
        except Exception as exc:  # noqa: BLE001
            logger.exception("weixin: agent chat failed for %s/%s",
                             account_id, chat_id)
            return f"agent 出错: {type(exc).__name__}"
        return reply or ""

    return dispatch


async def _resolve_project(orchestrator, store: WeixinAccountStore,
                           account_id: str, chat_id: str):
    """找到（必要时创建）这个「账号 + 聊天对象」对应的项目。"""
    project_id = await store.lookup(account_id, chat_id)
    if project_id:
        project = orchestrator.get_project(project_id)
        if project is not None:
            return project
    try:
        project = orchestrator.create_project(
            name=f"微信 · {account_id} · {chat_id}"[:60],
            description=f"微信 iLink 会话 (账号 {account_id} / 聊天对象 {chat_id})",
        )
    except Exception:  # noqa: BLE001
        logger.exception("weixin: create_project failed for %s/%s",
                         account_id, chat_id)
        return None
    if getattr(project, "id", None):
        await store.bind(account_id, chat_id, project.id)
    return project


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------

class LoginStartBody(BaseModel):
    bot_type: str = DEFAULT_BOT_TYPE


@router.post("/login/start")
async def login_start(body: Optional[LoginStartBody] = None):
    """取一张登录二维码，返回二维码 id + 图片内容（前端渲染 QR）。"""
    bot_type = (body.bot_type if body else DEFAULT_BOT_TYPE) or DEFAULT_BOT_TYPE
    _purge_sessions()
    client = _make_login_client()
    session = WeixinLoginSession(client, bot_type=bot_type)
    try:
        await session.start()
    except ILinkError as exc:
        raise HTTPException(status_code=502,
                            detail=f"取二维码失败: {exc}") from exc
    if not session.qrcode:
        raise HTTPException(status_code=502, detail="服务器未返回二维码")
    _sessions[session.qrcode] = session
    state = session.public_state()
    return {"qrcode": state["qrcode"], "qrcode_url": state["qrcode_url"],
            "status": state["status"], "bot_type": bot_type}


@router.get("/login/status")
async def login_status(qrcode: str = Query(...),
                       verify_code: str = Query(default="")):
    """轮询扫码状态；``confirmed`` 时落库并启动该账号的后台轮询。

    **响应里没有 token** —— 只有 ``public_state()`` 的公开字段。
    """
    session = _sessions.get(qrcode)
    if session is None:
        raise HTTPException(status_code=404, detail="未知或已结束的登录会话")
    if verify_code.strip():
        session.set_verify_code(verify_code.strip())
    try:
        await session.poll()
    except ILinkError as exc:
        raise HTTPException(status_code=502,
                            detail=f"轮询失败: {exc}") from exc

    state = session.public_state()

    if session.connected and _store is not None:
        # token 落库（脱敏视图之外），启动后台长轮询，然后结束这个登录会话。
        await _store.upsert_account(
            session.account_id or "",
            token=session.token,
            user_id=session.user_id or "",
            base_url=session.base_url_resolved or "",
            status="online",
        )
        if _channel is not None and session.account_id:
            try:
                await _channel.start_account(session.account_id)
            except ILinkError:
                logger.exception("weixin: 启动账号轮询失败 %s", session.account_id)
        _sessions.pop(qrcode, None)
        state = dict(state)
        state["status"] = "confirmed"

    return state


@router.get("/login/qr.png")
async def login_qr_png(qrcode: str = Query(default="")):
    """登录二维码的可扫 PNG（前端直接 ``<img>``，不需要前端 QR 库）。

    - 带 ``qrcode``：用该登录会话的二维码出图（同 id 命中缓存，不重复渲染）。
    - 省略 ``qrcode``：内部先取一张新码，一次请求就拿到图和
      ``X-Weixin-Qrcode`` 响应头（前端拿这个 id 去轮询 ``login/status``）。

    **响应里没有 token** —— 编码的内容是 ``qrcode_url``（``liteapp.weixin.qq.com``
    链接），图片里也没有任何密钥。
    """
    session = await _session_for_qr(qrcode)
    if not session.qrcode or not session.qrcode_url:
        raise HTTPException(status_code=502, detail="服务器未返回二维码内容")
    try:
        png = _qr_png_cached(session.qrcode, session.qrcode_url)
    except ImportError as exc:  # segno 未安装
        raise HTTPException(status_code=503,
                            detail="服务端缺少二维码依赖 segno") from exc
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Weixin-Qrcode": session.qrcode,
            "X-Weixin-Status": session.status,
            # 允许前端（同源/跨源调试）读这两个头。
            "Access-Control-Expose-Headers": "X-Weixin-Qrcode, X-Weixin-Status",
        },
    )


# ---------------------------------------------------------------------------
# 账号
# ---------------------------------------------------------------------------

@router.get("/accounts")
async def list_accounts():
    """列出账号（脱敏：无 token），并标注轮询是否在跑。"""
    store = _check()
    running = set(_channel.running_accounts()) if _channel else set()
    accounts = await store.list_accounts()
    out: List[Dict[str, Any]] = []
    for acct in accounts:
        view = acct.to_dict()
        view["running"] = acct.account_id in running
        out.append(view)
    return {"accounts": out}


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str):
    store = _check()
    if _channel is not None:
        try:
            await _channel.stop_account(account_id)
        except Exception:  # noqa: BLE001 - 停不掉也要把数据删了
            logger.exception("weixin: 停止账号失败 %s", account_id)
    await store.delete_account(account_id)
    return {"ok": True}


class SendBody(BaseModel):
    to: str = ""
    text: str


@router.post("/accounts/{account_id}/send")
async def send_test_message(account_id: str, body: SendBody):
    """用某个账号给一个聊天对象主动发文本（测试用）。"""
    _check()
    if not body.to.strip():
        raise HTTPException(status_code=400, detail="缺少收件人 to")
    try:
        result = await _channel.send_text(account_id, body.to.strip(), body.text)
    except ILinkError as exc:
        raise HTTPException(status_code=502, detail=f"发送失败: {exc}") from exc
    return {"ok": True, "message_id": (result or {}).get("message_id")}


# ---------------------------------------------------------------------------
# 绑定（账号 + 聊天对象 → 项目）
# ---------------------------------------------------------------------------

@router.get("/bindings")
async def list_bindings(account_id: str = Query(default="")):
    store = _check()
    return {"bindings": await store.list_bindings(account_id or None)}
