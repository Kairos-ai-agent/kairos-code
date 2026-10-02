"""企业微信「自建应用」双向通道 (R38.6 §35).

与 ``kairos.feishu`` 的差别在于接入方式：飞书用的是「自定义机器人
webhook」（单向群推送），而这里用的是**企业微信自建应用**，它既能
被动接收成员消息（回调），也能主动给成员发消息，因此可以做到真正
的双向对话 —— 用户在企业微信 App / PC 客户端里直接和 agent 聊天。

配置 5 个值（均由用户在企业微信管理后台创建自建应用时得到）：
  - ``corp_id``            企业 ID（我的企业 → 企业信息）
  - ``corp_secret``        自建应用的 Secret（应用管理 → 自建 → Secret）
  - ``agent_id``           自建应用的 AgentId
  - ``token``              回调配置里的 Token（随机字符串）
  - ``encoding_aes_key``   回调配置里的 EncodingAESKey（43 字符）

两条数据流
----------
1. **企业微信 → Kairos**：企业微信把成员消息 POST 到回调地址，body 是
   带 ``Encrypt`` 字段的 XML。我们先用 ``msg_signature`` 验签，再做
   AES-256-CBC 解密，拿到明文 XML（``FromUserName`` / ``MsgType`` /
   ``Content`` …），交给 agent；同步回复时把结果加密成企业微信要求的
   XML 结构返回。
2. **Kairos → 企业微信**：``WeComBot.send_text()`` 调 ``message/send``
   接口把事件/结果主动推给某个成员。``WeComEventForwarder`` 订阅
   orchestrator 的 message bus，按 topic 过滤后推送。

取 access_token
---------------
``GET /cgi-bin/gettoken`` 返回的 ``access_token`` 有效期 7200 秒，且接口
有频率限制，所以**必须缓存**：``WeComBot`` 在进程内缓存它，并在过期前
（留 300 秒余量）才重新请求。

安全
----
- ``corp_secret`` / ``token`` / ``encoding_aes_key`` 都不硬编码、不写日志、
  不通过 API 回显（``WeComConfig.public_view()`` 只暴露脱敏信息）。
- 验签失败一律拒绝；``token`` 未配置时按「失败关闭」处理。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import secrets
import struct
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

logger = logging.getLogger(__name__)

# 企业微信 API 根地址与 access_token 缓存策略。
WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"
# access_token 官方有效期 7200 秒；缓存时留出余量，避免边界上用到
# 一个刚好过期的 token。
TOKEN_TTL_SECONDS = 7200
TOKEN_REFRESH_MARGIN_SECONDS = 300

# 转发到企业微信的默认事件集合（与飞书保持一致的一组关键事件）。
DEFAULT_FORWARD_TOPICS = {
    "loop_done",        # agent 一轮运行结束
    "plan_ready",       # 多步计划待确认
    "checkpoint",       # 新检查点可用
    "user_input",       # agent 在向用户提问
    "error",            # 不可恢复错误
    "deliverable",      # 有新交付物
}


class WeComError(RuntimeError):
    """企业微信通道的通用错误。"""


class WeComSignatureError(WeComError):
    """回调签名校验失败。"""


class WeComCryptoError(WeComError):
    """AES 加解密失败（key 不合法 / 缺少加密后端）。"""


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class WeComConfig:
    """一个自建应用的配置（5 个值 + 启用开关）。

    注意：``enabled`` 只管「Kairos → 企业微信」的主动推送；回调入站
    是否放行由签名校验决定，不额外受 ``enabled`` 控制 —— 用户既然配好
    了回调地址，就说明希望收到消息。
    """
    corp_id: str = ""
    corp_secret: str = ""
    agent_id: str = ""
    token: str = ""
    encoding_aes_key: str = ""
    enabled: bool = False

    def configured(self) -> bool:
        """回调相关的最小配置是否齐全（收消息/验 URL 需要）。"""
        return bool(self.corp_id and self.token and self.encoding_aes_key)

    def can_send(self) -> bool:
        """主动发送是否具备条件。"""
        return bool(self.enabled and self.corp_id and self.corp_secret
                    and self.agent_id)

    def public_view(self) -> Dict[str, Any]:
        """脱敏后的配置视图，只用于回显，绝不包含任何密钥明文。"""
        return {
            "corp_id": self.corp_id,
            "agent_id": self.agent_id,
            "enabled": self.enabled,
            "configured": self.configured(),
            "has_corp_secret": bool(self.corp_secret),
            "has_token": bool(self.token),
            "has_encoding_aes_key": bool(self.encoding_aes_key),
        }


# ---------------------------------------------------------------------------
# 签名与 AES 加解密
# ---------------------------------------------------------------------------
#
# 企业微信回调使用 AES-256-CBC：
#   - EncodingAESKey 是 43 字符 → 补一个 "=" 后 base64 解码得到 32 字节 key；
#   - IV 取 key 的前 16 字节；
#   - 明文结构 random(16B) + msg_len(4B 大端) + msg + receiveid；
#   - PKCS7 填充，块大小 32（企业微信官方示例用的就是 32，不是 AES 的 16）。

def _aes_key(encoding_aes_key: str) -> bytes:
    """把 43 字符的 EncodingAESKey 解成 32 字节 AES key。"""
    raw = (encoding_aes_key or "").strip()
    if len(raw) != 43:
        raise WeComCryptoError("encoding_aes_key 必须是 43 个字符")
    try:
        key = base64.b64decode(raw + "=")
    except Exception as exc:  # noqa: BLE001
        raise WeComCryptoError(f"encoding_aes_key 不是合法的 base64: {exc}") from exc
    if len(key) != 32:
        raise WeComCryptoError("encoding_aes_key 解码后不是 32 字节")
    return key


def _pkcs7_pad(data: bytes, block_size: int = 32) -> bytes:
    pad = block_size - (len(data) % block_size)
    if pad == 0:
        pad = block_size
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise WeComCryptoError("空密文无法去填充")
    pad = data[-1]
    if pad < 1 or pad > 32 or pad > len(data):
        raise WeComCryptoError("PKCS7 填充字节非法")
    return data[:-pad]


def _aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-256-CBC 加密。优先 pycryptodome，退回 cryptography。"""
    try:
        from Crypto.Cipher import AES  # type: ignore
        return AES.new(key, AES.MODE_CBC, iv).encrypt(data)
    except ImportError:
        pass
    try:
        from cryptography.hazmat.primitives.ciphers import (
            Cipher,
            algorithms,
            modes,
        )
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise WeComCryptoError(
            "企业微信消息加解密需要 pycryptodome 或 cryptography，"
            "请安装其一（pip install pycryptodome）"
        ) from exc
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def _aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-256-CBC 解密。优先 pycryptodome，退回 cryptography。"""
    try:
        from Crypto.Cipher import AES  # type: ignore
        return AES.new(key, AES.MODE_CBC, iv).decrypt(data)
    except ImportError:
        pass
    try:
        from cryptography.hazmat.primitives.ciphers import (
            Cipher,
            algorithms,
            modes,
        )
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise WeComCryptoError(
            "企业微信消息加解密需要 pycryptodome 或 cryptography，"
            "请安装其一（pip install pycryptodome）"
        ) from exc
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def compute_signature(token: str, timestamp: str, nonce: str,
                      encrypt: str) -> str:
    """企业微信回调签名：token/timestamp/nonce/encrypt 排序后拼接的
    SHA1 十六进制。
    """
    items = sorted([str(token), str(timestamp), str(nonce), str(encrypt)])
    return hashlib.sha1("".join(items).encode("utf-8")).hexdigest()


def verify_signature(token: str, msg_signature: str, timestamp: str,
                     nonce: str, encrypt: str) -> bool:
    """常量时间校验回调签名。token 为空时失败关闭。"""
    if not token or not msg_signature:
        return False
    expected = compute_signature(token, timestamp, nonce, encrypt)
    return hmac.compare_digest(expected, msg_signature or "")


def decrypt_message(encoding_aes_key: str,
                    encrypt_b64: str) -> Tuple[bytes, bytes]:
    """解密企业微信密文，返回 ``(明文, receiveid)``。"""
    key = _aes_key(encoding_aes_key)
    iv = key[:16]
    try:
        ciphertext = base64.b64decode(encrypt_b64)
    except Exception as exc:  # noqa: BLE001
        raise WeComCryptoError(f"密文不是合法的 base64: {exc}") from exc
    if not ciphertext or len(ciphertext) % 16 != 0:
        raise WeComCryptoError("密文长度非法")
    plaintext = _pkcs7_unpad(_aes_cbc_decrypt(key, iv, ciphertext))
    if len(plaintext) < 20:
        raise WeComCryptoError("解密后的明文太短")
    msg_len = struct.unpack(">I", plaintext[16:20])[0]
    msg = plaintext[20:20 + msg_len]
    receiveid = plaintext[20 + msg_len:]
    return msg, receiveid


def encrypt_message(encoding_aes_key: str, msg: str,
                    receiveid: str) -> str:
    """把明文加密成企业微信要求的 base64 密文。"""
    key = _aes_key(encoding_aes_key)
    iv = key[:16]
    msg_bytes = msg.encode("utf-8") if isinstance(msg, str) else msg
    plaintext = (os.urandom(16)
                 + struct.pack(">I", len(msg_bytes))
                 + msg_bytes
                 + receiveid.encode("utf-8"))
    padded = _pkcs7_pad(plaintext, 32)
    ciphertext = _aes_cbc_encrypt(key, iv, padded)
    return base64.b64encode(ciphertext).decode("utf-8")


def _cdata(text: str) -> str:
    """把文本放进 CDATA，规避内容里出现 ``]]>`` 破坏 XML。"""
    return (text or "").replace("]]>", "]]]]><![CDATA[>")


# ---------------------------------------------------------------------------
# Bot —— 主动发送 + 回调解析
# ---------------------------------------------------------------------------

class WeComBot:
    """企业微信自建应用的出站 + 回调处理。

    这是 ``kairos.feishu.FeishuBot`` 的对应物，但多了一半职责：飞书的
    自定义机器人只负责发，自建应用还要解析回调。两类能力都收在这里，
    因为它们共用同一份配置（token / encoding_aes_key / corp_id）。
    """

    def __init__(self, config: WeComConfig):
        self.config = config
        # access_token 进程内缓存。企业微信的 gettoken 有频率限制，
        # 每次发送都去换取会被限频。
        self._access_token: str = ""
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()
        self._httpx = None
        # 便于测试注入假的计数器/客户端工厂。
        self.token_fetches = 0

    def _get_httpx(self):
        if self._httpx is None:
            import httpx
            self._httpx = httpx
        return self._httpx

    async def get_token(self, *, force: bool = False) -> str:
        """取得并缓存 access_token（默认 7200 秒，缓存到到期前 300 秒）。"""
        now = time.time()
        if not force and self._access_token and now < self._token_expires_at:
            return self._access_token
        async with self._token_lock:
            # 双重检查：并发调用只让一个请求真的去打 gettoken。
            now = time.time()
            if not force and self._access_token and now < self._token_expires_at:
                return self._access_token
            if not (self.config.corp_id and self.config.corp_secret):
                raise WeComError("企业微信 corp_id / corp_secret 未配置")
            httpx = self._get_httpx()
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{WECOM_API_BASE}/gettoken",
                    params={"corpid": self.config.corp_id,
                            "corpsecret": self.config.corp_secret},
                )
            data = r.json()
            self.token_fetches += 1
            if data.get("errcode", 0) != 0:
                # 注意：不要把 corp_secret 或请求参数写进日志。
                raise WeComError(
                    f"gettoken 失败: errcode={data.get('errcode')} "
                    f"errmsg={data.get('errmsg')}")
            self._access_token = data["access_token"]
            ttl = int(data.get("expires_in", TOKEN_TTL_SECONDS))
            self._token_expires_at = now + max(
                ttl - TOKEN_REFRESH_MARGIN_SECONDS, 60)
            return self._access_token

    async def send_text(self, userid: str, text: str) -> Dict[str, Any]:
        """主动给一个成员发文本消息。"""
        if not self.config.can_send():
            return {"ok": False, "skipped": "wecom not configured"}
        if not text:
            return {"ok": False, "skipped": "empty text"}
        if not userid:
            return {"ok": False, "skipped": "no touser"}
        try:
            token = await self.get_token()
            agent_id: Any = self.config.agent_id
            try:
                agent_id = int(self.config.agent_id)
            except (TypeError, ValueError):
                pass
            body = {
                "touser": userid,
                "msgtype": "text",
                "agentid": agent_id,
                "text": {"content": text},
            }
            httpx = self._get_httpx()
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{WECOM_API_BASE}/message/send",
                    params={"access_token": token},
                    json=body,
                )
            data = r.json()
            ok = (r.status_code == 200 and data.get("errcode", 0) == 0)
            return {"ok": ok, "status": r.status_code,
                    "errcode": data.get("errcode"),
                    "errmsg": data.get("errmsg")}
        except Exception as exc:  # noqa: BLE001
            # 只记类型：httpx 的异常消息里可能带上含 access_token 的 URL。
            logger.warning("wecom send failed: %s", type(exc).__name__)
            return {"ok": False, "error": type(exc).__name__}

    # -- 回调 ---------------------------------------------------------------

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str,
                   echostr: str) -> str:
        """处理回调地址的 URL 验证：验签 → 解密 echostr → 返回明文。

        企业微信后台保存回调地址时会发一次 GET，要求把解密后的
        echostr 原样返回。
        """
        if not verify_signature(self.config.token, msg_signature,
                                timestamp, nonce, echostr):
            raise WeComSignatureError("URL 验证签名失败")
        msg, _ = decrypt_message(self.config.encoding_aes_key, echostr)
        return msg.decode("utf-8")

    def parse_incoming(self, encrypt: str, msg_signature: str,
                       timestamp: str, nonce: str) -> Dict[str, Any]:
        """验签并解密一条入站消息，返回解析后的字段。"""
        if not verify_signature(self.config.token, msg_signature,
                                timestamp, nonce, encrypt):
            raise WeComSignatureError("回调签名失败")
        msg_bytes, _receiveid = decrypt_message(
            self.config.encoding_aes_key, encrypt)
        xml_text = msg_bytes.decode("utf-8")
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise WeComError(f"解密后的内容不是合法 XML: {exc}") from exc

        def _text(tag: str) -> str:
            node = root.find(tag)
            return (node.text or "") if node is not None else ""

        return {
            "to_user": _text("ToUserName"),
            "from_user": _text("FromUserName"),
            "create_time": _text("CreateTime"),
            "msg_type": _text("MsgType"),
            "content": _text("Content"),
            "agent_id": _text("AgentID"),
            "raw": xml_text,
        }

    def build_encrypted_reply(self, reply_text: str, to_user: str,
                              from_user: str,
                              timestamp: Optional[str] = None,
                              nonce: Optional[str] = None) -> str:
        """把回复文本加密成企业微信要求的外层 XML。

        结构：``<xml><Encrypt/><MsgSignature/><TimeStamp/><Nonce/></xml>``。
        """
        ts = str(timestamp if timestamp is not None else int(time.time()))
        nc = nonce or secrets.token_hex(8)
        inner = (
            "<xml>"
            f"<ToUserName><![CDATA[{_cdata(to_user)}]]></ToUserName>"
            f"<FromUserName><![CDATA[{_cdata(from_user)}]]></FromUserName>"
            f"<CreateTime>{int(time.time())}</CreateTime>"
            "<MsgType><![CDATA[text]]></MsgType>"
            f"<Content><![CDATA[{_cdata(reply_text)}]]></Content>"
            "</xml>"
        )
        encrypt = encrypt_message(self.config.encoding_aes_key, inner,
                                  self.config.corp_id)
        signature = compute_signature(self.config.token, ts, nc, encrypt)
        return (
            "<xml>"
            f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
            f"<TimeStamp>{ts}</TimeStamp>"
            f"<Nonce><![CDATA[{nc}]]></Nonce>"
            "</xml>"
        )


# ---------------------------------------------------------------------------
# 出站：agent 事件 → 企业微信
# ---------------------------------------------------------------------------

class WeComEventForwarder:
    """订阅 orchestrator 的 message bus，把关键事件推给企业微信成员。

    与 ``FeishuEventForwarder`` 同构：``start()`` 起后台任务，
    ``stop()`` 取消它。差异是企业微信要指定收件人（``to_user``），
    所以 attach 时可以给一个默认收件人。
    """

    def __init__(self, bot: WeComBot, topics: Optional[set] = None,
                 to_user: str = ""):
        self.bot = bot
        self.topics = topics or set(DEFAULT_FORWARD_TOPICS)
        self.to_user = to_user
        self._task: Optional[asyncio.Task] = None
        self._bus = None
        self._stop = asyncio.Event()

    def attach(self, message_bus, to_user: str = "") -> None:
        self._bus = message_bus
        if to_user:
            self.to_user = to_user

    def _format(self, msg: Any, topic: str) -> str:
        """把 bus 消息转成一行人类可读文本。"""
        if isinstance(msg, dict):
            subject = msg.get("subject") or msg.get("title") or topic
            detail = msg.get("detail") or msg.get("text") or ""
        else:
            subject = getattr(msg, "subject", topic) or topic
            detail = getattr(msg, "detail", "") or ""
        return f"[Kairos] {topic}: {subject}\n{detail}".strip()

    async def _run(self) -> None:
        if self._bus is None:
            logger.warning("WeComEventForwarder started without a bus")
            return
        async for msg in self._bus.subscribe():
            if self._stop.is_set():
                break
            try:
                topic = getattr(msg, "type", None)
                if topic is None and isinstance(msg, dict):
                    topic = msg.get("type", "")
                if topic not in self.topics:
                    continue
                to_user = self.to_user
                if isinstance(msg, dict):
                    to_user = msg.get("to_user") or to_user
                else:
                    to_user = getattr(msg, "to_user", to_user) or to_user
                await self.bot.send_text(to_user, self._format(msg, topic))
            except Exception as exc:  # noqa: BLE001
                logger.debug("wecom forward error: %s", type(exc).__name__)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(),
                                         name="wecom-forwarder")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None


# ---------------------------------------------------------------------------
# 入站：企业微信 → kairos 命令
# ---------------------------------------------------------------------------

def parse_command(text: str) -> Tuple[str, List[str]]:
    """把一条企业微信消息解析成 ``(command, args)``。

    普通文本返回 ``("chat", [text])``，即整条消息都交给 agent；
    以 ``/`` 开头的当作命令：``/status``、``/projects``、``/use <id>``、
    ``/chat <text>``、``/help``。实现与飞书保持一致。
    """
    from kairos.feishu import parse_command as _feishu_parse
    return _feishu_parse(text)


class WeComBindingStore:
    """SQLite 保存企业微信成员 → 项目 的绑定。

    与企业微信会话的标识是成员 UserID（``FromUserName``），所以
    ``chat_id`` 这里放的是 UserID。与飞书一样单独一个小表。
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS wecom_bindings (
                    chat_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            await db.commit()

    async def bind(self, chat_id: str, project_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO wecom_bindings"
                " (chat_id, project_id, updated_at) VALUES (?, ?, ?)",
                (chat_id, project_id, time.time()),
            )
            await db.commit()

    async def lookup(self, chat_id: str) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT project_id FROM wecom_bindings WHERE chat_id = ?",
                (chat_id,),
            )
            row = await cur.fetchone()
            return row[0] if row else None

    async def list_all(self) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT chat_id, project_id, updated_at FROM wecom_bindings"
                " ORDER BY updated_at DESC",
            )
            rows = await cur.fetchall()
            return [{"chat_id": c, "project_id": p, "updated_at": u}
                    for (c, p, u) in rows]

    async def unbind(self, chat_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM wecom_bindings WHERE chat_id = ?", (chat_id,))
            await db.commit()
