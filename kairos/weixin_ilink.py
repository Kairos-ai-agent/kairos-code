"""微信官方 ClawBot / iLink 通道（纯 Python 原生实现）。

这条通道把**微信个人号**接进 Kairos：扫码登录后，Kairos 用长轮询从
微信官方 iLink 网关收用户消息，交给 agent 处理，再把回复发回去。整套
实现只用仓库已有的 Python 依赖（``httpx`` / ``aiosqlite``），**不依赖
OpenClaw、npm、node、Docker 或任何第三方服务**。

> 协议参考腾讯官方 MIT 许可插件 ``@tencent-weixin/openclaw-weixin@2.4.9``
> （本地副本 ``weixin-ilink-ref``），按它的 TypeScript 源码重新实现。

协议要点
--------
网关地址 ``https://ilinkai.weixin.qq.com/``，每个请求都带这些头：

===========================  ==============================================
``iLink-App-Id``             ``bot``（取自插件 package.json 的 ilink_appid）
``iLink-App-ClientVersion``  版本号编成 uint32 ``0x00MMNNPP``（2.4.9 →
                             ``0x00020409`` = 132105）
``AuthorizationType``        ``ilink_bot_token``
``X-WECHAT-UIN``             random uint32 的十进制字符串再做 base64
``Authorization``            ``Bearer <token>``，登录后才有
``Content-Type``             ``application/json``
===========================  ==============================================

七个端点（长轮询 35s / 普通 15s / 轻量 10s）::

    GET  ilink/bot/get_bot_qrcode?bot_type=3      取登录二维码
    GET  ilink/bot/get_qrcode_status?qrcode=<id>  长轮询扫码状态
    POST ilink/bot/getupdates                     长轮询收消息（带游标）
    POST ilink/bot/sendmessage                    发消息
    POST ilink/bot/getconfig                      取 typing_ticket
    POST ilink/bot/sendtyping                     发「正在输入」
    POST ilink/bot/msg/notifystart / notifystop   会话开始 / 结束

安全
----
``token`` 是密钥：本模块**绝不**把它写进日志、API 响应或异常信息。
``ILinkClient`` 只在 ``Authorization`` 头里用它；``WeixinAccountStore``
把 token 存在**单独的** ``get_credentials()`` 调用里，``list_accounts()``
的 SELECT 列显式排除 token。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import quote, urljoin

import aiosqlite

logger = logging.getLogger(__name__)

__all__ = [
    "ILINK_BASE_URL",
    "ILINK_APP_ID",
    "ILINK_CHANNEL_VERSION",
    "ILINK_CLIENT_VERSION",
    "DEFAULT_BOT_TYPE",
    "DEFAULT_BOT_AGENT",
    "MessageType",
    "MessageItemType",
    "MessageState",
    "TypingStatus",
    "LOGIN_STATUSES",
    "ILinkError",
    "ILinkTimeoutError",
    "ILinkAuthError",
    "ILinkApiError",
    "build_client_version",
    "random_wechat_uin",
    "parse_weixin_api_json",
    "build_text_message",
    "build_client_id",
    "extract_text",
    "is_user_message",
    "ILinkClient",
    "WeixinLoginSession",
    "WeixinAccount",
    "WeixinAccountStore",
    "WeixinChannel",
]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: iLink 网关根地址（协议里叫 baseUrl）。
ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"

#: ``iLink-App-Id``。官方插件 package.json 顶层 ``ilink_appid`` 字段。
ILINK_APP_ID = "bot"

#: ``base_info.channel_version`` —— 对齐官方插件版本。
ILINK_CHANNEL_VERSION = "2.4.9"

#: ``iLink-App-ClientVersion``：uint32 ``0x00MMNNPP``，2.4.9 → 0x00020409。
ILINK_CLIENT_VERSION = 0x00020409

#: 扫码接口的 bot_type（本通道用的类型）。
DEFAULT_BOT_TYPE = "3"

#: 没有配置时的 ``base_info.bot_agent``（仅用于服务端观测，不参与鉴权）。
DEFAULT_BOT_AGENT = "Kairos"

#: 三类超时（秒）。长轮询请求服务端会压住连接，客户端到点自己放行。
LONG_POLL_TIMEOUT = 35.0
API_TIMEOUT = 15.0
LIGHT_TIMEOUT = 10.0

#: 扫码状态机取值，来自 ``login-qr.ts``。
LOGIN_STATUSES = (
    "wait",              # 等待扫码
    "scaned",            # 已扫码，等待确认
    "confirmed",         # 已确认，返回 token
    "expired",           # 二维码过期
    "scaned_but_redirect",  # 已扫码，需要切到 redirect_host 继续轮询
    "need_verifycode",   # 需要用户输入配对数字
    "verify_code_blocked",  # 多次输错被锁
    "binded_redirect",   # 该 bot 已绑定过
)

#: proto: MessageType
MessageType = {"NONE": 0, "USER": 1, "BOT": 2}
#: proto: MessageItemType
MessageItemType = {
    "NONE": 0, "TEXT": 1, "IMAGE": 2, "VOICE": 3, "FILE": 4, "VIDEO": 5,
    "TOOL_CALL_START": 11, "TOOL_CALL_RESULT": 12,
}
#: proto: MessageState
MessageState = {"NEW": 0, "GENERATING": 1, "FINISH": 2}
#: proto: TypingStatus
TypingStatus = {"TYPING": 1, "CANCEL": 2}

_MEDIA_LABELS = {
    MessageItemType["IMAGE"]: "[图片]",
    MessageItemType["VIDEO"]: "[视频]",
    MessageItemType["FILE"]: "[文件]",
    MessageItemType["VOICE"]: "[语音]",
}


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------

class ILinkError(RuntimeError):
    """iLink 通道的通用错误。"""


class ILinkTimeoutError(ILinkError):
    """请求超时（长轮询里属正常控制流，由调用方决定怎么处理）。"""


class ILinkAuthError(ILinkError):
    """鉴权失败：HTTP 401/403，或服务端 ``errcode=-14``（session timeout）。"""


class ILinkApiError(ILinkError):
    """服务端返回了非零 ``ret`` / ``errcode``。"""

    def __init__(self, message: str, *, ret: Optional[int] = None,
                 errcode: Optional[int] = None) -> None:
        super().__init__(message)
        self.ret = ret
        self.errcode = errcode


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def build_client_version(version: str) -> int:
    """把 ``"2.4.9"`` 编成 uint32 ``0x00MMNNPP``（高 8 位固定为 0）。"""
    parts = (version or "0.0.0").split(".")
    nums = []
    for p in parts[:3]:
        try:
            nums.append(int(p))
        except (TypeError, ValueError):
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    major, minor, patch = nums
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


def random_wechat_uin() -> str:
    """``X-WECHAT-UIN``：随机 uint32 → 十进制字符串 → base64。"""
    return base64.b64encode(str(secrets.randbits(32)).encode("utf-8")).decode("ascii")


def _normalize_base_url(url: str) -> str:
    url = (url or ILINK_BASE_URL).strip()
    return url if url.endswith("/") else url + "/"


# 需要原样保留（不丢精度）的 uint64 字段名。跟官方 parseWeixinApiJson 一致。
_LOSSLESS_ID_FIELDS = frozenset({"message_id", "msg_id", "svr_id"})


def parse_weixin_api_json(raw_text: str) -> Any:
    """容忍腾讯非标准 JSON 的解析器。

    腾讯偶尔把 uint64 消息 ID 直接当 JSON number 返回，超过 JS/Python 的
    安全整数范围就会丢精度。这里先把 ``message_id`` / ``msg_id`` /
    ``svr_id`` 的裸数字**包成字符串**，再交给 ``json.loads``。只会改写
    真正的对象属性，绝不会命中 JSON 字符串里的同名文本。

    解析失败时抛 ``ILinkError``（不把原文塞进异常，避免意外带出敏感数据）。
    """
    out: List[str] = []
    index = 0
    n = len(raw_text)
    while index < n:
        ch = raw_text[index]
        if ch != '"':
            out.append(ch)
            index += 1
            continue

        string_start = index
        index += 1
        escaped = False
        while index < n:
            c = raw_text[index]
            index += 1
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                break
        string_token = raw_text[string_start:index]
        out.append(string_token)

        cursor = index
        while cursor < n and raw_text[cursor].isspace():
            cursor += 1
        if cursor >= n or raw_text[cursor] != ":":
            continue
        try:
            key = json.loads(string_token)
        except (ValueError, TypeError):
            continue
        if not isinstance(key, str) or key not in _LOSSLESS_ID_FIELDS:
            continue

        out.append(raw_text[index:cursor + 1])  # 冒号
        cursor += 1
        while cursor < n and raw_text[cursor].isspace():
            out.append(raw_text[cursor])
            cursor += 1
        number_start = cursor
        if cursor < n and raw_text[cursor] == "-":
            cursor += 1
        while cursor < n and raw_text[cursor].isdigit():
            cursor += 1
        if cursor > number_start and not (
                cursor == number_start + 1 and raw_text[number_start] == "-"):
            out.append('"' + raw_text[number_start:cursor] + '"')
            index = cursor
        else:
            index = number_start

    try:
        return json.loads("".join(out))
    except ValueError as exc:  # 不把原文放进异常
        raise ILinkError("iLink 返回了无法解析的 JSON") from exc


def build_client_id() -> str:
    """出站消息的 ``client_id``（本地唯一，服务端用来去重）。"""
    return f"kairos-weixin-{secrets.token_hex(8)}"


def build_text_message(to_user_id: str, text: str, *,
                       context_token: Optional[str] = None,
                       run_id: Optional[str] = None,
                       client_id: Optional[str] = None) -> Dict[str, Any]:
    """构造 ``sendmessage`` 需要的那条 ``WeixinMessage``。

    字段照 ``messaging/send.ts`` 的 ``buildTextMessageReq``：``from_user_id``
    为空串（服务端按 token 填），``message_type`` = BOT，``message_state`` =
    FINISH，正文放在 ``item_list[0].text_item.text``。``context_token`` 是
    收消息时下发的、必须原样回带。
    """
    item_list = [{"type": MessageItemType["TEXT"], "text_item": {"text": text}}] \
        if text else []
    msg: Dict[str, Any] = {
        "from_user_id": "",
        "to_user_id": to_user_id,
        "client_id": client_id or build_client_id(),
        "message_type": MessageType["BOT"],
        "message_state": MessageState["FINISH"],
        "item_list": item_list or None,
    }
    if context_token:
        msg["context_token"] = context_token
    if run_id:
        msg["run_id"] = run_id
    return msg


def extract_text(msg: Dict[str, Any]) -> str:
    """从一条 ``WeixinMessage`` 里取正文。

    优先文本项；语音自带 ``text``（语音转文字）时用它；否则退回媒体占位符
    ``[图片]`` 等。取不到返回空串。与 ``inbound.ts:bodyFromItemList`` 对齐。
    """
    for item in (msg.get("item_list") or []):
        itype = item.get("type")
        if itype == MessageItemType["TEXT"]:
            text_item = item.get("text_item") or {}
            if text_item.get("text") is not None:
                return str(text_item["text"])
        elif itype == MessageItemType["VOICE"]:
            voice = item.get("voice_item") or {}
            if voice.get("text"):
                return str(voice["text"])
        elif itype in _MEDIA_LABELS:
            return _MEDIA_LABELS[itype]
    return ""


def is_user_message(msg: Dict[str, Any]) -> bool:
    """这条消息是否值得交给 agent 处理。

    跳过 bot 自己发的（``message_type==2``）、没有发送者的、以及解析不出
    正文的。群消息（``group_id``）目前不做特殊处理，仍按发送者会话。
    """
    if not isinstance(msg, dict):
        return False
    if msg.get("message_type") == MessageType["BOT"]:
        return False
    if not (msg.get("from_user_id") or "").strip():
        return False
    return bool(extract_text(msg))


# ---------------------------------------------------------------------------
# ILinkClient
# ---------------------------------------------------------------------------

class ILinkClient:
    """iLink 七个端点的封装：请求头、超时、重试、错误映射。

    ``token`` 只在 ``Authorization`` 头里出现。任何异常/日志都不含它。
    """

    def __init__(
        self,
        *,
        base_url: str = ILINK_BASE_URL,
        token: Optional[str] = None,
        bot_agent: str = DEFAULT_BOT_AGENT,
        channel_version: str = ILINK_CHANNEL_VERSION,
        client_version: int = ILINK_CLIENT_VERSION,
        long_poll_timeout: float = LONG_POLL_TIMEOUT,
        api_timeout: float = API_TIMEOUT,
        light_timeout: float = LIGHT_TIMEOUT,
    ) -> None:
        self.base_url = _normalize_base_url(base_url)
        self._token = (token or "").strip() or None
        self.bot_agent = bot_agent or DEFAULT_BOT_AGENT
        self.channel_version = channel_version
        self.client_version = client_version
        self.long_poll_timeout = long_poll_timeout
        self.api_timeout = api_timeout
        self.light_timeout = light_timeout

    # -- 内部 -----------------------------------------------------------

    def base_info(self) -> Dict[str, Any]:
        """每个请求都带上的 ``base_info``。"""
        return {"channel_version": self.channel_version,
                "bot_agent": self.bot_agent}

    def _build_headers(self, *, with_auth: bool) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": str(self.client_version),
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": random_wechat_uin(),
        }
        if with_auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _request(self, method: str, endpoint: str, *,
                       body: Optional[Dict[str, Any]] = None,
                       timeout: float, with_auth: bool) -> str:
        import httpx

        url = urljoin(self.base_url, endpoint)
        headers = self._build_headers(with_auth=with_auth)
        # 只记方法 + 端点（不含 query / header / body），避免任何密钥落日志。
        logger.debug("ilink %s %s", method, endpoint.split("?")[0])
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    resp = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise ILinkTimeoutError(
                f"{method} {endpoint.split('?')[0]} 超时") from exc
        except httpx.HTTPError as exc:
            raise ILinkError(
                f"{method} {endpoint.split('?')[0]} 失败: "
                f"{type(exc).__name__}") from exc

        if resp.status_code in (401, 403):
            raise ILinkAuthError(
                f"{method} {endpoint.split('?')[0]} 鉴权失败"
                f" (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise ILinkError(
                f"{method} {endpoint.split('?')[0]} HTTP {resp.status_code}")
        return resp.text

    @staticmethod
    def _check_error(data: Any) -> Any:
        """把服务端错误码映射成异常。"""
        if not isinstance(data, dict):
            return data
        errcode = data.get("errcode")
        ret = data.get("ret")
        if errcode in (-14,):
            raise ILinkAuthError("session timeout (errcode -14)")
        if errcode not in (None, 0):
            raise ILinkApiError(f"errcode={errcode} errmsg={data.get('errmsg', '')}",
                                errcode=errcode, ret=ret)
        return data

    # -- 端点 -----------------------------------------------------------

    async def get_bot_qrcode(self, *, bot_type: str = DEFAULT_BOT_TYPE,
                             local_token_list: Optional[Sequence[str]] = None,
                             timeout: Optional[float] = None) -> Dict[str, Any]:
        """取登录二维码。

        返回 ``{"qrcode": "<id>", "qrcode_img_content": "<url>", "ret": 0}``。
        不需要 token（登录前调用）。``local_token_list`` 是本机既有 token，
        服务端用它判断是否已绑定过。
        """
        endpoint = f"ilink/bot/get_bot_qrcode?bot_type={quote(str(bot_type))}"
        body = {"local_token_list": list(local_token_list or [])}
        raw = await self._request("POST", endpoint, body=body,
                                  timeout=timeout or self.api_timeout,
                                  with_auth=False)
        return self._check_error(parse_weixin_api_json(raw))

    async def get_qrcode_status(self, qrcode: str, verify_code: Optional[str] = None,
                                *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """长轮询扫码状态。

        客户端超时返回 ``{"ret": 0, "status": "wait"}``（长轮询的正常控制流）。
        成功时字段见 ``login-qr.ts``：``status`` 取 :data:`LOGIN_STATUSES`；
        ``confirmed`` 时带 ``bot_token`` / ``ilink_bot_id`` / ``ilink_user_id`` /
        ``baseurl``；``scaned_but_redirect`` 时带 ``redirect_host``。
        """
        endpoint = f"ilink/bot/get_qrcode_status?qrcode={quote(qrcode)}"
        if verify_code:
            endpoint += f"&verify_code={quote(verify_code)}"
        try:
            raw = await self._request("GET", endpoint,
                                      timeout=timeout or self.long_poll_timeout,
                                      with_auth=False)
        except ILinkTimeoutError:
            return {"ret": 0, "status": "wait"}
        return self._check_error(parse_weixin_api_json(raw))

    async def get_updates(self, get_updates_buf: str = "", *,
                          timeout: Optional[float] = None) -> Dict[str, Any]:
        """长轮询收消息。

        ``get_updates_buf`` 是**游标**：第一次传空串，之后把上一次响应里的
        ``get_updates_buf`` 原样带回。客户端超时返回空响应（``msgs=[]``、游标
        不变），调用方直接重试即可。鉴权失效（errcode -14）抛
        :class:`ILinkAuthError`。
        """
        body = {"get_updates_buf": get_updates_buf or "",
                "base_info": self.base_info()}
        try:
            raw = await self._request("POST", "ilink/bot/getupdates", body=body,
                                      timeout=timeout or self.long_poll_timeout,
                                      with_auth=True)
        except ILinkTimeoutError:
            return {"ret": 0, "msgs": [], "get_updates_buf": get_updates_buf}
        return self._check_error(parse_weixin_api_json(raw))

    async def send_message(self, msg: Dict[str, Any], *,
                           timeout: Optional[float] = None) -> Dict[str, Any]:
        """发送一条消息（``msg`` 由 :func:`build_text_message` 构造）。

        返回 ``{"ret": 0, "message_id": "<server id>", ...}``；``ret`` 非零抛
        :class:`ILinkApiError`。
        """
        body = {"msg": msg, "base_info": self.base_info()}
        raw = await self._request("POST", "ilink/bot/sendmessage", body=body,
                                  timeout=timeout or self.api_timeout,
                                  with_auth=True)
        data = parse_weixin_api_json(raw)
        if isinstance(data, dict) and data.get("ret"):
            raise ILinkApiError(
                f"sendMessage ret={data.get('ret')} "
                f"errmsg={data.get('errmsg', '')}", ret=data.get("ret"))
        return data

    async def get_config(self, ilink_user_id: str,
                         context_token: Optional[str] = None, *,
                         timeout: Optional[float] = None) -> Dict[str, Any]:
        """取 bot 配置（含 ``typing_ticket``）。"""
        body = {"ilink_user_id": ilink_user_id,
                "context_token": context_token,
                "base_info": self.base_info()}
        raw = await self._request("POST", "ilink/bot/getconfig", body=body,
                                  timeout=timeout or self.light_timeout,
                                  with_auth=True)
        return self._check_error(parse_weixin_api_json(raw))

    async def send_typing(self, ilink_user_id: str, typing_ticket: str,
                          status: int = TypingStatus["TYPING"], *,
                          timeout: Optional[float] = None) -> Dict[str, Any]:
        """发送「正在输入」指示（``status`` 1=typing，2=cancel）。"""
        body = {"ilink_user_id": ilink_user_id,
                "typing_ticket": typing_ticket,
                "status": status,
                "base_info": self.base_info()}
        raw = await self._request("POST", "ilink/bot/sendtyping", body=body,
                                  timeout=timeout or self.light_timeout,
                                  with_auth=True)
        try:
            return parse_weixin_api_json(raw)
        except ILinkError:
            return {"ret": 0}

    async def notify_start(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """通知服务端「本客户端会话开始」。"""
        raw = await self._request("POST", "ilink/bot/msg/notifystart",
                                  body={"base_info": self.base_info()},
                                  timeout=timeout or self.light_timeout,
                                  with_auth=True)
        return parse_weixin_api_json(raw)

    async def notify_stop(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """通知服务端「本客户端会话结束」。"""
        raw = await self._request("POST", "ilink/bot/msg/notifystop",
                                  body={"base_info": self.base_info()},
                                  timeout=timeout or self.light_timeout,
                                  with_auth=True)
        return parse_weixin_api_json(raw)


# ---------------------------------------------------------------------------
# 登录会话
# ---------------------------------------------------------------------------

class WeixinLoginSession:
    """一次扫码登录会话：取二维码 → 轮询状态 → 拿到 token。

    **token 是密钥**：只在 :attr:`token` 这个显式属性上暴露，绝不会出现在
    :meth:`public_state` 的返回值里，路由层应只回传 ``public_state()``。
    """

    def __init__(self, client: ILinkClient, *, bot_type: str = DEFAULT_BOT_TYPE,
                 ttl_seconds: float = 300.0, max_refresh: int = 3,
                 now_fn: Callable[[], float] = time.time) -> None:
        self.client = client
        self.bot_type = bot_type
        self.ttl_seconds = ttl_seconds
        self.max_refresh = max_refresh
        self._now = now_fn

        self.qrcode: Optional[str] = None
        self.qrcode_url: Optional[str] = None
        self.status: str = "wait"
        self.error: str = ""
        self.connected: bool = False
        self.already_connected: bool = False
        self.account_id: Optional[str] = None
        self.user_id: Optional[str] = None
        self.base_url_resolved: Optional[str] = None
        self.pending_verify_code: Optional[str] = None

        self._token: Optional[str] = None
        self.started_at: float = 0.0
        self._refresh_count = 1

    # -- 生命周期 -------------------------------------------------------

    def is_expired(self) -> bool:
        return bool(self.started_at) and \
            (self._now() - self.started_at) > self.ttl_seconds

    async def start(self) -> Dict[str, Any]:
        """取一张二维码，记录会话起点。"""
        resp = await self.client.get_bot_qrcode(bot_type=self.bot_type)
        self.qrcode = resp.get("qrcode")
        self.qrcode_url = resp.get("qrcode_img_content")
        self.started_at = self._now()
        self.status = "wait"
        if not self.qrcode:
            self.status = "error"
            self.error = "服务器未返回二维码"
        return self.public_state()

    async def _refresh(self) -> None:
        if self._refresh_count >= self.max_refresh:
            self.status = "expired"
            self.error = "二维码多次失效"
            return
        self._refresh_count += 1
        resp = await self.client.get_bot_qrcode(bot_type=self.bot_type)
        self.qrcode = resp.get("qrcode")
        self.qrcode_url = resp.get("qrcode_img_content")
        self.started_at = self._now()
        self.status = "wait"
        self.pending_verify_code = None

    def set_verify_code(self, code: str) -> None:
        """登记用户在手机上看到的配对数字，下一次轮询带上。"""
        self.pending_verify_code = (code or "").strip() or None

    async def poll(self) -> str:
        """轮询一次扫码状态，返回当前 ``status``。

        会处理官方状态机里的分支：``scaned_but_redirect`` 切换轮询主机、
        ``expired`` 自动刷新二维码、``confirmed`` 捕获 token 与账号信息、
        ``binded_redirect`` 视为「已连接过」。
        """
        if not self.qrcode:
            raise ILinkError("登录会话尚未取到二维码")
        if self.is_expired() and not self.connected:
            self.status = "expired"
            return self.status

        resp = await self.client.get_qrcode_status(
            self.qrcode, self.pending_verify_code)
        status = str(resp.get("status") or "wait")
        self.status = status

        if status == "wait":
            pass
        elif status == "scaned":
            # 配对码若被接受，服务端不会再要求输入。
            self.pending_verify_code = None
        elif status == "need_verifycode":
            self.pending_verify_code = None  # 等用户下一轮 set_verify_code
        elif status == "expired":
            await self._refresh()
        elif status == "verify_code_blocked":
            self.pending_verify_code = None
            self.error = "多次输入错误"
            await self._refresh()
        elif status == "scaned_but_redirect":
            host = (resp.get("redirect_host") or "").strip()
            if host:
                self.client.base_url = _normalize_base_url(f"https://{host}")
        elif status == "binded_redirect":
            self.already_connected = True
            self.status = "binded_redirect"
        elif status == "confirmed":
            bot_id = (resp.get("ilink_bot_id") or "").strip()
            if not bot_id:
                self.status = "error"
                self.error = "服务器未返回 ilink_bot_id"
            else:
                self._token = (resp.get("bot_token") or "").strip() or None
                self.account_id = bot_id
                self.user_id = (resp.get("ilink_user_id") or "").strip() or None
                self.base_url_resolved = (resp.get("baseurl") or "").strip() or None
                self.connected = True
        return self.status

    # -- 只读视图 -------------------------------------------------------

    @property
    def token(self) -> Optional[str]:
        """bot token（密钥）。只在真正要落盘 / 建客户端时读。"""
        return self._token

    def public_state(self) -> Dict[str, Any]:
        """可以安全回给前端的视图 —— 注意**没有** ``token`` 字段。"""
        return {
            "qrcode": self.qrcode,
            "qrcode_url": self.qrcode_url,
            "status": self.status,
            "connected": self.connected,
            "already_connected": self.already_connected,
            "account_id": self.account_id,
            "user_id": self.user_id,
            "error": self.error,
            "expired": self.is_expired(),
            "need_verifycode": self.status == "need_verifycode",
        }


# ---------------------------------------------------------------------------
# 账号 / 账号存储
# ---------------------------------------------------------------------------

@dataclass
class WeixinAccount:
    """一个微信账号的公开视图（**没有** token 字段）。"""

    account_id: str
    name: str = ""
    user_id: str = ""
    base_url: str = ""
    enabled: bool = True
    status: str = "offline"          # online | offline | error
    last_error: str = ""
    created_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.account_id,
            "account_id": self.account_id,
            "name": self.name,
            "user_id": self.user_id,
            "base_url": self.base_url,
            "enabled": self.enabled,
            "status": self.status,
            "online": self.status == "online",
            "last_error": self.last_error,
            "created_at": self.created_at,
        }


class WeixinAccountStore:
    """多账号的 SQLite 存储（每个账号一个 token + 自己的游标）。

    四张表：账号（含 token）、同步游标、context_token、会话绑定。所有查询
    都显式带 ``account_id`` 谓词，所以两个账号即使撞上同一个 chat id 也不会
    互相覆盖（绑定主键是 ``(account_id, chat_id)``）。

    token 只通过 :meth:`get_credentials` 单独取；:meth:`list_accounts` /
    :meth:`get_account` 的 SELECT 列显式排除它。
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS weixin_accounts (
                    account_id TEXT PRIMARY KEY,
                    token      TEXT NOT NULL DEFAULT '',
                    user_id    TEXT NOT NULL DEFAULT '',
                    base_url   TEXT NOT NULL DEFAULT '',
                    name       TEXT NOT NULL DEFAULT '',
                    enabled    INTEGER NOT NULL DEFAULT 1,
                    status     TEXT NOT NULL DEFAULT 'offline',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            # 游标与账号分开存：get_credentials 不该顺手把游标拖出来。
            await db.execute("""
                CREATE TABLE IF NOT EXISTS weixin_sync (
                    account_id      TEXT PRIMARY KEY,
                    get_updates_buf TEXT NOT NULL DEFAULT '',
                    updated_at      REAL NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS weixin_context_tokens (
                    account_id    TEXT NOT NULL,
                    user_id       TEXT NOT NULL,
                    context_token TEXT NOT NULL DEFAULT '',
                    updated_at    REAL NOT NULL,
                    PRIMARY KEY (account_id, user_id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS weixin_bindings (
                    account_id TEXT NOT NULL,
                    chat_id    TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (account_id, chat_id)
                )
            """)
            await db.commit()

    # -- 账号 -----------------------------------------------------------

    async def upsert_account(self, account_id: str, *, token: Optional[str] = None,
                             user_id: str = "", base_url: str = "",
                             name: str = "", enabled: bool = True,
                             status: str = "offline") -> WeixinAccount:
        """新增或更新一个账号。``token`` 为空字符串时保留原值。"""
        account_id = (account_id or "").strip()
        if not account_id:
            raise ILinkError("account_id 不能为空")
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO weixin_accounts"
                " (account_id, token, user_id, base_url, name, enabled,"
                "  status, last_error, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?)"
                " ON CONFLICT(account_id) DO UPDATE SET"
                "  token = CASE WHEN excluded.token = ''"
                "               THEN weixin_accounts.token"
                "               ELSE excluded.token END,"
                "  user_id = CASE WHEN excluded.user_id = ''"
                "                 THEN weixin_accounts.user_id"
                "                 ELSE excluded.user_id END,"
                "  base_url = CASE WHEN excluded.base_url = ''"
                "                  THEN weixin_accounts.base_url"
                "                  ELSE excluded.base_url END,"
                "  name = CASE WHEN excluded.name = ''"
                "              THEN weixin_accounts.name"
                "              ELSE excluded.name END,"
                "  enabled = excluded.enabled,"
                "  status = excluded.status,"
                "  updated_at = excluded.updated_at",
                (account_id, (token or "").strip(), user_id, base_url, name,
                 1 if enabled else 0, status, now, now),
            )
            if not token:
                # 新账号且没给 token：补一条同步游标行。
                await db.execute(
                    "INSERT OR IGNORE INTO weixin_sync"
                    " (account_id, get_updates_buf, updated_at) VALUES (?, '', ?)",
                    (account_id, now))
            await db.commit()
        acct = await self.get_account(account_id)
        if acct is None:  # pragma: no cover - 上面的插入保证存在
            raise ILinkError(f"账号 {account_id!r} 写入后消失了")
        return acct

    @staticmethod
    def _to_account(row: Any) -> WeixinAccount:
        # 注意 row 的列顺序与下方 SELECT 对齐，**不含 token**。
        return WeixinAccount(
            account_id=row[0], name=row[1] or "", user_id=row[2] or "",
            base_url=row[3] or "", enabled=bool(row[4]), status=row[5] or "offline",
            last_error=row[6] or "", created_at=row[7] or 0.0,
        )

    _PUBLIC_COLUMNS = ("account_id, name, user_id, base_url, enabled,"
                       " status, last_error, created_at")

    async def list_accounts(self) -> List[WeixinAccount]:
        """所有账号，**不含 token**。"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"SELECT {self._PUBLIC_COLUMNS} FROM weixin_accounts"
                " ORDER BY created_at DESC")
            rows = await cursor.fetchall()
        return [self._to_account(r) for r in rows]

    async def get_account(self, account_id: str) -> Optional[WeixinAccount]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"SELECT {self._PUBLIC_COLUMNS} FROM weixin_accounts"
                " WHERE account_id = ?", (account_id,))
            row = await cursor.fetchone()
        return self._to_account(row) if row else None

    async def get_credentials(self, account_id: str) -> Optional[Dict[str, Any]]:
        """账号的**凭据**：token + base_url。单独调用，别处不外传。"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT account_id, token, user_id, base_url, enabled"
                " FROM weixin_accounts WHERE account_id = ?", (account_id,))
            row = await cursor.fetchone()
        if not row:
            return None
        return {"account_id": row[0], "token": row[1] or "",
                "user_id": row[2] or "", "base_url": row[3] or "",
                "enabled": bool(row[4])}

    async def set_status(self, account_id: str, status: str,
                         error: str = "") -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE weixin_accounts SET status = ?, last_error = ?,"
                " updated_at = ? WHERE account_id = ?",
                (status, error, time.time(), account_id))
            await db.commit()

    async def delete_account(self, account_id: str) -> None:
        """删除账号及其游标 / context_token / 绑定。"""
        async with aiosqlite.connect(self.db_path) as db:
            for table in ("weixin_accounts", "weixin_sync",
                          "weixin_context_tokens", "weixin_bindings"):
                await db.execute(
                    f"DELETE FROM {table} WHERE account_id = ?", (account_id,))
            await db.commit()

    # -- 游标 -----------------------------------------------------------

    async def save_cursor(self, account_id: str, get_updates_buf: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO weixin_sync (account_id, get_updates_buf, updated_at)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(account_id) DO UPDATE SET"
                "  get_updates_buf = excluded.get_updates_buf,"
                "  updated_at = excluded.updated_at",
                (account_id, get_updates_buf or "", time.time()))
            await db.commit()

    async def load_cursor(self, account_id: str) -> str:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT get_updates_buf FROM weixin_sync WHERE account_id = ?",
                (account_id,))
            row = await cursor.fetchone()
        return (row[0] or "") if row else ""

    # -- context_token --------------------------------------------------

    async def set_context_token(self, account_id: str, user_id: str,
                                context_token: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO weixin_context_tokens"
                " (account_id, user_id, context_token, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(account_id, user_id) DO UPDATE SET"
                "  context_token = excluded.context_token,"
                "  updated_at = excluded.updated_at",
                (account_id, user_id, context_token or "", time.time()))
            await db.commit()

    async def get_context_token(self, account_id: str,
                                user_id: str) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT context_token FROM weixin_context_tokens"
                " WHERE account_id = ? AND user_id = ?", (account_id, user_id))
            row = await cursor.fetchone()
        return (row[0] or None) if row else None

    # -- 绑定 -----------------------------------------------------------

    async def bind(self, account_id: str, chat_id: str, project_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO weixin_bindings"
                " (account_id, chat_id, project_id, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(account_id, chat_id) DO UPDATE SET"
                "  project_id = excluded.project_id,"
                "  updated_at = excluded.updated_at",
                (account_id, chat_id, project_id, time.time()))
            await db.commit()

    async def lookup(self, account_id: str, chat_id: str) -> Optional[str]:
        """一个账号下、一个聊天对象绑定的项目。"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT project_id FROM weixin_bindings"
                " WHERE account_id = ? AND chat_id = ?", (account_id, chat_id))
            row = await cursor.fetchone()
        return (row[0] or None) if row else None

    async def list_bindings(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            if account_id:
                cursor = await db.execute(
                    "SELECT account_id, chat_id, project_id, updated_at"
                    " FROM weixin_bindings WHERE account_id = ?"
                    " ORDER BY updated_at DESC", (account_id,))
            else:
                cursor = await db.execute(
                    "SELECT account_id, chat_id, project_id, updated_at"
                    " FROM weixin_bindings ORDER BY updated_at DESC")
            rows = await cursor.fetchall()
        return [{"account_id": r[0], "chat_id": r[1], "project_id": r[2],
                 "updated_at": r[3]} for r in rows]

    async def unbind(self, account_id: str, chat_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM weixin_bindings"
                " WHERE account_id = ? AND chat_id = ?", (account_id, chat_id))
            await db.commit()


# ---------------------------------------------------------------------------
# WeixinChannel
# ---------------------------------------------------------------------------

class WeixinChannel:
    """后台编排：每账号一条长轮询协程，收到消息交给 agent 再发回。

    隔离要点：

    * **每账号一个 asyncio 任务 + 一个 ILinkClient**，互不共享；
    * **每账号一个游标**（``weixin_sync`` 表），一个账号推进游标不影响另一个；
    * **每条「账号 + 聊天对象」映射到独立项目**（``weixin_bindings`` 主键
      ``(account_id, chat_id)``），所以同名聊天对象在不同账号下不会串。

    ``dispatch`` 是一个 ``async (account_id, chat_id, text) -> str`` 回调，
    由 ``api/routes/weixin.py`` 注入（负责建项目 / 走 agent / 命令）。测试
    可以注入假实现，从而完全离线。
    """

    def __init__(self, store: WeixinAccountStore, *,
                 dispatch: Optional[Callable[[str, str, str], Any]] = None,
                 client_factory: Optional[Callable[..., ILinkClient]] = None,
                 base_url: str = ILINK_BASE_URL,
                 bot_agent: str = DEFAULT_BOT_AGENT,
                 poll_interval: float = 0.3) -> None:
        self.store = store
        self._dispatch = dispatch
        self._client_factory = client_factory
        self.base_url = base_url
        self.bot_agent = bot_agent
        self.poll_interval = poll_interval
        self._clients: Dict[str, ILinkClient] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._stopping: set = set()

    # -- 客户端 ---------------------------------------------------------

    def make_client(self, *, token: Optional[str], base_url: str = "") -> ILinkClient:
        if self._client_factory is not None:
            return self._client_factory(token=token, base_url=base_url or self.base_url)
        return ILinkClient(base_url=base_url or self.base_url, token=token,
                           bot_agent=self.bot_agent)

    async def client_for(self, account_id: str) -> ILinkClient:
        client = self._clients.get(account_id)
        if client is not None:
            return client
        creds = await self.store.get_credentials(account_id)
        if creds is None:
            raise ILinkError(f"未知账号 {account_id!r}")
        client = self.make_client(token=creds.get("token") or None,
                                  base_url=creds.get("base_url") or "")
        self._clients[account_id] = client
        return client

    # -- 启停 -----------------------------------------------------------

    async def start_account(self, account_id: str, *,
                            client: Optional[ILinkClient] = None) -> None:
        """为一个账号起后台长轮询协程。幂等。"""
        if client is not None:
            self._clients[account_id] = client
        else:
            await self.client_for(account_id)
        self._stopping.discard(account_id)

        task = self._tasks.get(account_id)
        if task is not None and not task.done():
            return
        try:
            await self._clients[account_id].notify_start()
        except ILinkError as exc:
            logger.debug("weixin notifystart 失败（忽略）: %s", type(exc).__name__)
        await self.store.set_status(account_id, "online")
        self._tasks[account_id] = asyncio.create_task(
            self._poll_loop(account_id), name=f"weixin-poll-{account_id}")

    async def stop_account(self, account_id: str) -> None:
        self._stopping.add(account_id)
        task = self._tasks.pop(account_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        client = self._clients.pop(account_id, None)
        if client is not None:
            try:
                await client.notify_stop()
            except ILinkError:
                pass
        await self.store.set_status(account_id, "offline")

    async def stop_all(self) -> None:
        for account_id in list(self._tasks) + list(self._clients):
            await self.stop_account(account_id)

    def running_accounts(self) -> List[str]:
        return [a for a, t in self._tasks.items() if not t.done()]

    # -- 轮询 -----------------------------------------------------------

    async def _poll_loop(self, account_id: str) -> None:
        client = self._clients[account_id]
        while account_id not in self._stopping:
            cursor = await self.store.load_cursor(account_id)
            try:
                resp = await client.get_updates(cursor)
            except ILinkAuthError as exc:
                await self.store.set_status(account_id, "error",
                                            f"鉴权失败: {exc}")
                logger.warning("weixin account %s 鉴权失败，停止轮询", account_id)
                return
            except ILinkError as exc:  # 网络抖动：记一下，继续
                logger.debug("weixin getupdates 出错: %s", type(exc).__name__)
                await asyncio.sleep(self.poll_interval)
                continue

            msgs = resp.get("msgs") or []
            new_cursor = resp.get("get_updates_buf")
            if new_cursor is not None and new_cursor != cursor:
                await self.store.save_cursor(account_id, new_cursor)

            for msg in msgs:
                if account_id in self._stopping:
                    break
                try:
                    await self.handle_message(account_id, client, msg)
                except Exception:  # noqa: BLE001 - 一条坏消息不该拖垮轮询
                    logger.exception("weixin 处理消息失败 account=%s", account_id)

            if not msgs:
                await asyncio.sleep(self.poll_interval)

    async def handle_message(self, account_id: str, client: ILinkClient,
                             msg: Dict[str, Any]) -> Optional[str]:
        """处理一条入站消息：取正文 → dispatch → 发回回复。"""
        if not is_user_message(msg):
            return None
        from_user = (msg.get("from_user_id") or "").strip()
        text = extract_text(msg)

        context_token = msg.get("context_token") or \
            await self.store.get_context_token(account_id, from_user)
        if msg.get("context_token"):
            await self.store.set_context_token(
                account_id, from_user, msg["context_token"])

        reply = ""
        if self._dispatch is not None:
            reply = await self._dispatch(account_id, from_user, text) or ""
        if reply:
            await client.send_message(build_text_message(
                from_user, reply, context_token=context_token))
        return reply

    # -- 出站 -----------------------------------------------------------

    async def send_text(self, account_id: str, to_user_id: str, text: str,
                        context_token: Optional[str] = None) -> Dict[str, Any]:
        """给一个聊天对象主动发文本（测试 / 主动推送用）。"""
        client = await self.client_for(account_id)
        if context_token is None:
            context_token = await self.store.get_context_token(account_id, to_user_id)
        return await client.send_message(build_text_message(
            to_user_id, text, context_token=context_token))
