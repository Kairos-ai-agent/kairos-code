"""企业微信「自建应用」HTTP 接口 (R38.6 §35).

路由一览::

    GET    /api/wecom/config                 读取（脱敏）配置
    PUT    /api/wecom/config                 写入 5 个凭据 + 启用开关
    GET    /api/wecom/verify                 企业微信 URL 验证（回明文 echostr）
    POST   /api/wecom/webhook                接收成员消息 → 交给 agent → 加密回包
    POST   /api/wecom/test                   主动给某成员发测试消息
    GET    /api/wecom/bindings               列出 成员 → 项目 绑定
    DELETE /api/wecom/bindings/{chat_id}     删除一条绑定

这是 ``api/routes/feishu.py`` 的对应物，但按企业微信自建应用的规则来：
回调是 XML（不是 JSON）、URL 验证要 AES 解密 ``echostr`` 再原样返回、
回复要用 ``<xml><Encrypt>…</Encrypt></xml>`` 加密结构。

配置存进 ``kairos.settings_store``（落在 data/settings.json），所以 5 个
凭据不硬编码；``corp_secret`` / ``token`` / ``encoding_aes_key`` 绝不
通过本路由回显，也不写日志。
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from api.deps import get_orchestrator
from kairos.wecom import (
    WeComBindingStore,
    WeComBot,
    WeComConfig,
    WeComError,
    WeComEventForwarder,
    WeComSignatureError,
    parse_command,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wecom", tags=["wecom"])

# Module-level state, wired by api/app.py (same shape as the Feishu route).
# The orchestrator is resolved per request via Depends(get_orchestrator) so
# tests that monkeypatch the singleton see it.
_bot: Optional[WeComBot] = None
_bindings: Optional[WeComBindingStore] = None
_forwarder: Optional[WeComEventForwarder] = None


def set_dependencies(bot: Optional[WeComBot],
                     bindings: Optional[WeComBindingStore] = None,
                     forwarder: Optional[WeComEventForwarder] = None) -> None:
    """由 api/app.py 的 lifespan 调用，注入运行期依赖。"""
    global _bot, _bindings, _forwarder
    if bot is not None:
        _bot = bot
    if bindings is not None:
        _bindings = bindings
    if forwarder is not None:
        _forwarder = forwarder


def reset_dependencies() -> None:
    """测试用：清空注入的依赖，回到「未初始化」状态。"""
    global _bot, _bindings, _forwarder
    _bot = None
    _bindings = None
    _forwarder = None


def _config_from_settings() -> WeComConfig:
    """从 settings_store 读取企业微信配置（不缓存，保证改动即时生效）。"""
    from kairos.settings_store import get_store
    w = get_store().get().wecom
    return WeComConfig(
        corp_id=w.corp_id, corp_secret=w.corp_secret,
        agent_id=w.agent_id, token=w.token,
        encoding_aes_key=w.encoding_aes_key, enabled=w.enabled,
    )


def _get_bot() -> WeComBot:
    """返回当前 bot。若未注入则用 settings_store 里的配置构造一个。"""
    global _bot
    if _bot is None:
        _bot = WeComBot(_config_from_settings())
    return _bot


def _check_bindings() -> WeComBindingStore:
    if _bindings is None:
        raise HTTPException(status_code=503,
                            detail="wecom bindings not initialized")
    return _bindings


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

class WeComConfigBody(BaseModel):
    corp_id: str = ""
    corp_secret: str = ""
    agent_id: str = ""
    token: str = ""
    encoding_aes_key: str = ""
    enabled: bool = False


@router.get("/config")
async def get_config():
    """回显配置，但绝不包含任何密钥明文。"""
    return _get_bot().config.public_view()


@router.put("/config")
async def put_config(body: WeComConfigBody):
    """保存配置。

    敏感值（corp_secret / token / encoding_aes_key）与普通值都遵循
    「留空即保持原值」，这样前端回显脱敏内容后再提交不会把密钥清空。
    """
    from kairos.settings_store import get_store
    patch: Dict[str, Any] = {}
    if body.corp_id.strip():
        patch["corp_id"] = body.corp_id.strip()
    if body.agent_id.strip():
        patch["agent_id"] = body.agent_id.strip()
    if body.corp_secret.strip():
        patch["corp_secret"] = body.corp_secret.strip()
    if body.token.strip():
        patch["token"] = body.token.strip()
    if body.encoding_aes_key.strip():
        patch["encoding_aes_key"] = body.encoding_aes_key.strip()
    patch["enabled"] = bool(body.enabled)
    get_store().update({"wecom": patch})
    # 重建 bot，让新配置立即生效。
    global _bot
    _bot = WeComBot(_config_from_settings())
    return {"ok": True, "config": _bot.config.public_view()}


@router.post("/test")
async def test_message(body: Dict[str, Any]):
    """主动给一个成员发测试消息（body: {"userid": "...", "text": "..."}）"""
    bot = _get_bot()
    userid = (body.get("userid") or body.get("touser") or "").strip()
    text = body.get("text", "👋 Kairos 测试消息")
    return await bot.send_text(userid, text)


# ---------------------------------------------------------------------------
# 绑定（成员 UserID → 项目）
# ---------------------------------------------------------------------------

@router.get("/bindings")
async def list_bindings():
    return {"bindings": await _check_bindings().list_all()}


@router.delete("/bindings/{chat_id}")
async def delete_binding(chat_id: str):
    await _check_bindings().unbind(chat_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 回调：URL 验证 + 消息接收
# ---------------------------------------------------------------------------

def _extract_encrypt(xml_text: str) -> str:
    """从回调 body 的 XML 里取出 ``Encrypt`` 字段。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    node = root.find("Encrypt")
    return (node.text or "").strip() if node is not None else ""


@router.get("/verify")
async def verify_url(
    msg_signature: str = Query(default="", alias="msg_signature"),
    timestamp: str = Query(default="", alias="timestamp"),
    nonce: str = Query(default="", alias="nonce"),
    echostr: str = Query(default="", alias="echostr"),
):
    """企业微信保存回调地址时的 URL 验证。

    验签通过后 AES 解密 ``echostr``，把明文**原样**返回（纯文本，
    不能是 JSON —— 企业微信只认正文）。
    """
    bot = _get_bot()
    if not bot.config.configured():
        raise HTTPException(status_code=503, detail="wecom not configured")
    try:
        plain = bot.verify_url(msg_signature, timestamp, nonce, echostr)
    except WeComSignatureError as exc:
        raise HTTPException(status_code=401, detail="bad signature") from exc
    except WeComError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=plain, media_type="text/plain")


@router.post("/webhook")
async def webhook(
    request: Request,
    orchestrator=Depends(get_orchestrator),
    msg_signature: str = Query(default="", alias="msg_signature"),
    timestamp: str = Query(default="", alias="timestamp"),
    nonce: str = Query(default="", alias="nonce"),
):
    """接收企业微信推送的成员消息。

    流程：验签 → AES 解密 → 解析明文 XML → 分发（agent / 命令）→
    把回复加密成企业微信要求的 XML 返回。无法处理时返回空正文
    （企业微信视为「无回复」，不会重试报错）。
    """
    bot = _get_bot()
    if not bot.config.configured():
        raise HTTPException(status_code=503, detail="wecom not configured")
    raw = await request.body()
    encrypt = _extract_encrypt(raw.decode("utf-8", errors="replace"))
    if not encrypt:
        # 没有 Encrypt 字段：不是一条可处理的消息，直接静默确认。
        return Response(content="", media_type="text/plain")
    try:
        msg = bot.parse_incoming(encrypt, msg_signature, timestamp, nonce)
    except WeComSignatureError as exc:
        raise HTTPException(status_code=401, detail="bad signature") from exc
    except WeComError as exc:
        raise HTTPException(status_code=400,
                            detail=f"bad payload: {exc}") from exc

    from_user = (msg.get("from_user") or "").strip()
    to_user = (msg.get("to_user") or "").strip()
    text = (msg.get("content") or "").strip()
    # 目前只处理文本消息；其它类型（图片/事件等）静默确认。
    if msg.get("msg_type") != "text" or not text or not from_user:
        return Response(content="", media_type="text/plain")

    reply = await _dispatch(from_user, text, orchestrator)
    if not reply:
        return Response(content="", media_type="text/plain")
    xml = bot.build_encrypted_reply(reply, to_user=from_user,
                                    from_user=to_user)
    return Response(content=xml, media_type="application/xml")


async def _dispatch(chat_id: str, text: str, orchestrator) -> str:
    """把一条消息分发到命令或 agent，返回要发回的文本。"""
    cmd, args = parse_command(text)
    if cmd == "noop":
        return ""
    if cmd == "help":
        return ("/status · /projects · /use <id> · /chat <text> · /help\n"
                "直接发消息即与当前项目的 agent 对话。")
    bindings = _bindings
    if cmd == "use":
        if not args:
            return "用法: /use <project_id>"
        if bindings is None:
            return "绑定存储未初始化"
        await bindings.bind(chat_id, args[0])
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
        project_id = await bindings.lookup(chat_id) if bindings else None
        return (f"当前项目: {project_id}" if project_id
                else "尚未绑定项目，先 /use <id> 或直接发消息自动创建。")

    # 默认：普通文本 / /chat 都走 agent。
    prompt = " ".join(args) if cmd == "chat" else text
    project = await _resolve_project(chat_id, orchestrator)
    coder = getattr(project, "coder", None) if project is not None else None
    if coder is None:
        return "当前会话还没有可用的 agent。"
    try:
        reply = await coder.chat(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.exception("wecom: agent chat failed for %s", chat_id)
        return f"agent 出错: {type(exc).__name__}"
    return reply or ""


async def _resolve_project(chat_id: str, orchestrator) -> Optional[Any]:
    """找到（必要时创建）这个成员对应的项目。"""
    project_id = await _bindings.lookup(chat_id) if _bindings else None
    if project_id:
        project = orchestrator.get_project(project_id)
        if project is not None:
            return project
    try:
        project = orchestrator.create_project(
            name=f"企业微信 · {chat_id}"[:60],
            description=f"企业微信自建应用会话 (UserID {chat_id})",
        )
    except Exception:  # noqa: BLE001
        logger.exception("wecom: create_project failed for %s", chat_id)
        return None
    if _bindings is not None and getattr(project, "id", None):
        await _bindings.bind(chat_id, project.id)
    return project
