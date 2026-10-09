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
import inspect
import json
import logging
import os
import re
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import (Any, Awaitable, Callable, Dict, List, Optional, Sequence,
                    Tuple)
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
    "WEIXIN_STREAM_MAX_CHUNKS",
    "WEIXIN_STREAM_MAX_CHUNKS_HARD_LIMIT",
    "WEIXIN_STREAM_SHORT_MAX_CHARS",
    "WEIXIN_STREAM_ENV",
    "stream_max_chunks",
    "split_reply_for_delivery",
    "extract_text",
    "is_user_message",
    "WEIXIN_CDN_BASE_URL",
    "WEIXIN_MEDIA_MAX_BYTES",
    "MediaRef",
    "MediaDownloadError",
    "extract_media_refs",
    "build_cdn_download_url",
    "parse_aes_key",
    "decrypt_aes_ecb",
    "download_media_to",
    "UploadMediaType",
    "WEIXIN_UPLOAD_MAX_ATTEMPTS",
    "WEIXIN_MAX_OUTBOUND_FILES",
    "MediaUploadError",
    "WeixinMediaUpload",
    "aes_ecb_padded_size",
    "encrypt_aes_ecb",
    "build_cdn_upload_url",
    "upload_file_to_cdn",
    "build_file_media",
    "build_file_message_item",
    "build_file_message",
    "resolve_sendable_files",
    "snapshot_tree_files",
    "diff_touched_files",
    "select_sendable_paths",
    "merge_sendable_candidates",
    "WEIXIN_SNAPSHOT_IGNORE_DIRS",
    "WEIXIN_SNAPSHOT_IGNORE_PREFIXES",
    "WEIXIN_SNAPSHOT_IGNORE_SUFFIXES",
    "WEIXIN_SNAPSHOT_MAX_ENTRIES",
    "WEIXIN_SNAPSHOT_MAX_SECONDS",
    "ILinkClient",
    "WeixinLoginSession",
    "WeixinAccount",
    "WeixinAccountStore",
    "WeixinChannel",
    "current_session",
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
# 入站媒体（图片 / 文件 / 视频）—— 协议常量
# ---------------------------------------------------------------------------
#
# 事实来源：官方 MIT 许可插件 ``@tencent-weixin/openclaw-weixin@2.4.9`` 的
# TypeScript 源码（本地只读副本，**代码不搬进本仓库**）：
#
# * item 形状 / 字段名        ``media/media-download.js``
# * CDN 下载 URL 形状         ``cdn/cdn-url.js`` + ``auth/accounts.js:CDN_BASE_URL``
# * AES-128-ECB + key 解析    ``cdn/aes-ecb.js`` + ``cdn/pic-decrypt.js``
# * 单媒体大小上限            ``channel.js`` configSchema ``maxSingleMediaBytes``
#                             （默认 26214400 = 25 MiB）
#
# 我们**没有**真实 iLink 入站响应样本：以上是照源码推断的字段名/形状，
# 与真实网关是否逐字一致未经实测（见模块末的“未经真样本验证”说明）。

#: 微信 CDN 根地址（官方 ``auth/accounts.js:9`` 的 ``CDN_BASE_URL``）。
WEIXIN_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"

#: 单个媒体文件大小上限：25 MiB（官方 ``maxSingleMediaBytes`` 默认值）。
WEIXIN_MEDIA_MAX_BYTES = 26214400

#: 我们要下载的媒体类型 → (分类名, item 里的子对象字段名)。
#: VOICE 不在本轮范围：语音已有转写文本兜底（``voice_item.text``）。
_MEDIA_ITEM_FIELDS = {
    MessageItemType["IMAGE"]: ("image", "image_item"),
    MessageItemType["VIDEO"]: ("video", "video_item"),
    MessageItemType["FILE"]: ("file", "file_item"),
}


# ---------------------------------------------------------------------------
# 当前正在处理的入站会话
# ---------------------------------------------------------------------------
#
# ``WeixinChannel.handle_message`` 在处理一条入站消息期间把它设成
# ``(account_id, from_user_id)``。审批桥（``kairos/weixin_approvals.py``）在闸门
# 把 ASK 变成问题时读它：只有**同一条协程链**里的问题才是这个微信会话发起的，
# 于是「微信发起的问题用微信的等待时限 / 推到微信」这件事是确定的，
# 不会顺手改到 Web 端的审批。
#
# 放进本模块（而不是桥里）是为了不产生循环导入：桥 import 本模块，本模块
# 不该反过来 import 桥。
current_session: ContextVar[Optional[Tuple[str, str]]] = ContextVar(
    "kairos_weixin_current_session", default=None)


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


# ---------------------------------------------------------------------------
# 出站：分段渐进投递（**不是 token 级流式** —— 务必先读这段）
# ---------------------------------------------------------------------------
#
# 实话实说：本通道拿到回复时，它已经是一个**完整字符串**了。依据：
#
#   * ``api/routes/weixin.py:_answer_message`` 调 ``kairos.skeleton.service.
#     run_chat_reply`` —— 它返回 ``Optional[str]``（``kairos/skeleton/service.py``
#     第 273/281 行前后），不是异步生成器；
#   * 同一条路径上的 ``project.coder.chat(text)`` 也返回 ``str``
#     （``kairos/agents/agent_parts/chat.py`` 的 ``async def chat(...) -> str``）。
#
# 所以这里做的**不是**「逐字吐出」，而是**把最终文本按自然边界切成几段、分几条
# 消息先后发出去**。用户能看到的是「分段到达」，**看不到**「打字机逐字」。这是
# 当前架构下能达到的真实感上限，请勿在文档/UI 里宣称它是流式。
#
# 本仓库**确实**存在 token 级流式，但只在 Coder/agent 内部：``kairos/agents/
# agent_parts/llm.py`` 的 ``_stream_complete`` 把每个 delta 发布成消息总线上的
# ``stream.chunk`` 事件（供网页端渲染打字机）。那是**推送式事件**，不是能
# ``await`` 出来的生成器，而且 ``agents/base.py``/``agents/roles/`` 属本轮红线、
# 不改。要让它驱动微信，得有一条「订阅总线 → 按段发微信」的桥且不碰红线，本轮
# 不做（见下）。
#
# 设计纪律（微信不是浏览器 —— 每发一条都可能给对方推提醒）：
#
#   1. 回复短 → **只发一条**，与改动前逐字一致，不做任何「打字机」；
#   2. 回复长 → 最多 N 条（默认 3，env 可调，另有硬上限），逐条发出；
#   3. 所有分段拼起来**逐字等于**最终回复（不重复、不丢字、不另补一条完整版）；
#   4. 只在自然边界（空行 / 换行 / 句末）切，**不切进代码块、不在字中间切**，
#      找不到合适边界就宁可不拆；
#   5. 中途发送失败 → 把**剩下的**拼成一条至少再投一次；仍失败则**明确报错**，
#      绝不静默截断（见 :class:`ReplyDelivery` 的 ``ok`` / ``failed_at``）。

#: 长回复默认最多拆成几条（env ``KAIROS_WEIXIN_STREAM_CHUNKS`` 可覆盖）。
WEIXIN_STREAM_MAX_CHUNKS = 3
#: 总条数**硬上限**：即使 env 要更多也不超过它，防止把聊天窗口刷爆。
WEIXIN_STREAM_MAX_CHUNKS_HARD_LIMIT = 6
#: 短回复阈值（字符）：``len(text) <= 此值`` 只发一条。依据：微信一个文本气泡
#: 舒适显示约 6 行，按每行 ~60 字符估约 360 字符；短于它的回复再拆，只会多推
#: 一条提醒而不增加可读性 —— 所以阈值取 360，宁少拆。
WEIXIN_STREAM_SHORT_MAX_CHARS = 360
#: 中途失败后，把「剩余部分」整体重投的次数（≥1，见模块顶部纪律 5）。
WEIXIN_STREAM_REMAINDER_RETRIES = 2
#: 段与段之间的停顿毫秒（env ``KAIROS_WEIXIN_STREAM_DELAY_MS``）。默认 0（不停顿）；
#: 给一个正值（如 400）能让分段**到达得更错落**，但**不改变条数、不改变内容**。
WEIXIN_STREAM_DELAY_MS = 0

#: env 名：最多拆几条。
WEIXIN_STREAM_ENV = "KAIROS_WEIXIN_STREAM_CHUNKS"
#: env 名：段间停顿毫秒。
WEIXIN_STREAM_DELAY_ENV = "KAIROS_WEIXIN_STREAM_DELAY_MS"


def stream_max_chunks() -> int:
    """最多拆几条：读 env ``KAIROS_WEIXIN_STREAM_CHUNKS``，夹在 ``1..硬上限``。

    取值非整数时退回 :data:`WEIXIN_STREAM_MAX_CHUNKS`。置 **1 即完全关闭分段**
    （永远单条，与改动前逐字一致）。每次调用都重新读 env，便于运行期调整/测试。
    """
    raw = os.environ.get(WEIXIN_STREAM_ENV, "").strip()
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = WEIXIN_STREAM_MAX_CHUNKS
    return max(1, min(n, WEIXIN_STREAM_MAX_CHUNKS_HARD_LIMIT))


def _stream_delay_seconds() -> float:
    """段间停顿（秒），读 env ``KAIROS_WEIXIN_STREAM_DELAY_MS``，默认 0。"""
    raw = os.environ.get(WEIXIN_STREAM_DELAY_ENV, "").strip()
    try:
        ms = int(raw)
    except (TypeError, ValueError):
        ms = WEIXIN_STREAM_DELAY_MS
    return max(0, ms) / 1000.0


#: 代码围栏（````` ``` ````` 或 ``~~~``，允许前导空格）。
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
#: 中文句末标点：本身即句子边界，可切。
_CJK_SENTENCE_END = "。！？；"
#: 英文句末标点：只在**后面是空白 / 串尾**、且前一个字符不是数字时才当边界，
#: 避免把 ``3.14``、``file.py``、``e.g.`` 拦腰切断。
_ASCII_SENTENCE_END = ".!?;"


def _line_cut_allowed(is_delim: bool, in_fence_before: bool) -> bool:
    """「这一行的行末能不能作为切点」——用来保护代码块。

    * 普通行：只有**不在围栏内部**才能切（围栏内部的代码行被保护）；
    * 围栏分隔行：**开**围栏的那一行行末不能切（否则把围栏与代码切开），
      **闭**围栏的那一行行末可以切（切在整段代码块之后是安全的）。
    """
    if is_delim:
        return in_fence_before
    return not in_fence_before


def _scan_cut_positions(text: str) -> List[Tuple[int, int]]:
    """扫出所有**安全**切点 ``(pos, rank)``（切在 ``pos`` 之前，左段 = ``text[:pos]``）。

    ``rank`` 越小越自然：0=空行、1=换行、2=句末；列表按 ``pos`` 升序、同一 ``pos``
    取最小 rank。安全 = 不在代码块内部、不在字中间：**代码块内部的换行 / 句号
    一律不作为切点**（``_line_cut_allowed`` 保证）。
    """
    n = len(text)
    cuts: Dict[int, int] = {}

    def _add(pos: int, rank: int) -> None:
        # 端点不作为切点：pos<=0 会切出空左段；pos>=n 会切出空右段。
        if pos <= 0 or pos >= n:
            return
        if pos not in cuts or rank < cuts[pos]:
            cuts[pos] = rank

    offset = 0
    in_fence = False
    for line in text.splitlines(keepends=True):
        is_delim = bool(_FENCE_RE.match(line))
        in_fence_before = in_fence
        line_start = offset
        line_end = offset + len(line)
        ends_nl = line.endswith(("\n", "\r"))

        # 行末切点：空行 → rank 0（段落），其余换行 → rank 1。
        if ends_nl and _line_cut_allowed(is_delim, in_fence_before):
            is_blank = line.strip("\r\n") == ""
            _add(line_end, 0 if is_blank else 1)

        # 行内句末切点：只在**普通、且在围栏外**的行里找。
        if (not is_delim) and (not in_fence_before):
            for j, ch in enumerate(line):
                after = line_start + j + 1
                if ch in _CJK_SENTENCE_END:
                    _add(after, 2)
                elif ch in _ASCII_SENTENCE_END:
                    nxt = text[after] if after < n else ""
                    prev = text[after - 2] if after >= 2 else ""
                    if (nxt == "" or nxt.isspace()) and not prev.isdigit():
                        _add(after, 2)

        if is_delim:
            in_fence = not in_fence
        offset = line_end

    return sorted(cuts.items())


def _pick_cut(cuts: List[Tuple[int, int]], start: int, target: int,
              min_left: int) -> Optional[int]:
    """在 ``(start, ...)`` 里挑一个切点，避免切出**过小的左段**。

    优先 ``<= target`` 的**最靠后**者（切得匀）；若它相对 ``start`` 还不够长
    （``< min_left``）且后方还有更远的自然边界，就改用 ``> target`` 的**最靠前**者
    —— 宁可这一段切长一点，也不要在开头切出一个 5 个字的小气泡。都没有 → ``None``。
    """
    last_le: Optional[int] = None
    first_gt: Optional[int] = None
    for pos, _rank in cuts:
        if pos <= start:
            continue
        if pos <= target:
            last_le = pos
        else:
            first_gt = pos
            break
    if last_le is not None and (last_le - start) >= min_left:
        return last_le
    if first_gt is not None:
        return first_gt
    return last_le


def split_reply_for_delivery(text: str, *,
                             max_chunks: Optional[int] = None,
                             short_max: Optional[int] = None) -> List[str]:
    """把**完整回复**切成 ≤N 段（拼接逐字等于原文），供分段投递使用。

    * 空串 → ``[]``；短回复（``len <= short_max``）或 ``max_chunks <= 1`` →
      ``[text]``（单段，调用方据此走与改动前一致的「一条」路径）；
    * 只切在 :func:`_scan_cut_positions` 给出的自然边界上；找不到边界就
      **不拆**（宁可单条，也不在字中间 / 代码块里切）。

    **逐字保证**：返回的是 ``text`` 的连续切片 ``[0:c1], [c1:c2], ...`` 且每个切点
    都 ``> 上一个切点``，故 ``"".join(result) == text`` 恒成立（不重复、不丢字）。
    """
    text = "" if text is None else str(text)
    if not text:
        return []
    if max_chunks is None:
        max_chunks = stream_max_chunks()
    max_chunks = max(1, min(int(max_chunks), WEIXIN_STREAM_MAX_CHUNKS_HARD_LIMIT))
    if short_max is None:
        short_max = WEIXIN_STREAM_SHORT_MAX_CHARS

    n = len(text)
    if max_chunks <= 1 or n <= short_max:
        return [text]

    cuts = _scan_cut_positions(text)
    if not cuts:
        return [text]

    chunks: List[str] = []
    start = 0
    remaining = max_chunks
    min_left = max(1, short_max // 3)     # 左段最小长度，避免切出小气泡
    while remaining > 1 and (n - start) > short_max:
        left = n - start
        target = start + -(-left // remaining)          # ceil(left / remaining)
        cut = _pick_cut(cuts, start, target, min_left)
        if cut is None:
            break                                        # 找不到自然边界 → 不拆
        chunks.append(text[start:cut])
        start = cut
        remaining -= 1
    if start < n:
        chunks.append(text[start:])
    return chunks if chunks else [text]


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


def _callable_accepts_media(fn: Any) -> bool:
    """dispatch 回调是否愿意接收第 4 个参数（入站媒体引用）。

    老的三参 ``dispatch(account_id, chat_id, text)`` 仍然完全可用——这条通道
    只在回调**声明了**第 4 个位置参数（或关键字 ``media``）时才把媒体交给它，
    否则退回原来的三参调用，保证既有注入的假 dispatch 不被破坏。
    """
    if fn is None:
        return False
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    positional = 0
    for p in sig.parameters.values():
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
            positional += 1
        elif p.kind == p.KEYWORD_ONLY and p.name == "media":
            return True
    return positional >= 4


# ---------------------------------------------------------------------------
# 入站媒体：取引用 → 下载 → 解密 → 落盘
# ---------------------------------------------------------------------------

@dataclass
class MediaRef:
    """一条待下载的入站媒体项（字段名/形状照官方协议，原样保留）。

    ``kind`` 是 ``image`` / ``video`` / ``file``。密钥有两种来源：

    * ``image_item.aeskey``（32 位十六进制串）优先用于图片；
    * ``media.aes_key``（base64）用于其它类型，图片无前者时也用它。

    服务端若直给 ``media.full_url`` 就优先用它，否则用
    ``encrypt_query_param`` 拼 CDN 下载地址。
    """

    kind: str
    item_type: int
    label: str
    encrypt_query_param: str = ""
    aes_key: str = ""
    image_aeskey_hex: str = ""
    full_url: str = ""
    file_name: str = ""

    @property
    def has_media(self) -> bool:
        """这条 item 里到底有没有可下载的媒体地址。"""
        return bool(self.encrypt_query_param or self.full_url)


class MediaDownloadError(RuntimeError):
    """入站媒体下载 / 解密 / 落盘失败；``reason`` 是可直接展示的中文原因。"""

    def __init__(self, reason: str, *, kind: str = "",
                 too_large: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind
        self.too_large = too_large


def extract_media_refs(msg: Dict[str, Any]) -> List[MediaRef]:
    """从一条 ``WeixinMessage`` 里取出图片 / 视频 / 文件的下载引用。

    与 :func:`extract_text` 并存：文本走 ``extract_text``（媒体仍给出占位符
    ``[图片]``，保证只有媒体、没有正文的消息也能通过 ``is_user_message``），
    媒体引用走这里再真正下载。没有可下载地址的 item 会被跳过。
    """
    refs: List[MediaRef] = []
    for item in (msg.get("item_list") or []):
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        spec = _MEDIA_ITEM_FIELDS.get(itype)
        if spec is None:
            continue
        kind, field = spec
        sub = item.get(field) or {}
        media = sub.get("media") or {}
        ref = MediaRef(
            kind=kind,
            item_type=itype,
            label=_MEDIA_LABELS.get(itype, f"[{kind}]"),
            encrypt_query_param=str(media.get("encrypt_query_param") or ""),
            aes_key=str(media.get("aes_key") or ""),
            image_aeskey_hex=(str(sub.get("aeskey") or "") if kind == "image"
                              else ""),
            full_url=str(media.get("full_url") or ""),
            file_name=str(sub.get("file_name") or ""),
        )
        if ref.has_media:
            refs.append(ref)
    return refs


def build_cdn_download_url(encrypted_query_param: str,
                           cdn_base_url: str = WEIXIN_CDN_BASE_URL) -> str:
    """拼 CDN 下载地址（官方 ``cdn/cdn-url.js:buildCdnDownloadUrl``）。"""
    return (f"{cdn_base_url.rstrip('/')}/download"
            f"?encrypted_query_param={quote(str(encrypted_query_param), safe='')}")


def parse_aes_key(aes_key_base64: str) -> bytes:
    """``CDNMedia.aes_key`` → 16 字节 AES key（官方 ``pic-decrypt.js:parseAesKey``）。

    两种编码都要认：base64(原始 16 字节) 和 base64(32 位十六进制串)。
    """
    try:
        decoded = base64.b64decode(str(aes_key_base64 or ""), validate=False)
    except Exception as exc:  # noqa: BLE001 - binascii.Error 等
        raise MediaDownloadError("aes_key 不是合法 base64") from exc
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        try:
            text = decoded.decode("ascii")
        except UnicodeDecodeError:
            text = ""
        if re.fullmatch(r"[0-9a-fA-F]{32}", text or ""):
            return bytes.fromhex(text)
    raise MediaDownloadError(
        "aes_key 必须是 16 字节原始 key 或 32 位十六进制串")


def _pkcs7_unpad(data: bytes) -> bytes:
    """去掉 PKCS7 填充（Node 的 decipher.final() 会自动做，Python 不会）。"""
    if not data:
        raise MediaDownloadError("解密结果为空")
    pad = data[-1]
    if pad < 1 or pad > 16 or pad > len(data):
        raise MediaDownloadError("解密结果填充非法（密钥或数据不匹配）")
    return data[:-pad]


def decrypt_aes_ecb(ciphertext: bytes, key: bytes) -> bytes:
    """AES-128-ECB + PKCS7 解密（官方 ``cdn/aes-ecb.js:decryptAesEcb``）。"""
    if len(key) != 16:
        raise MediaDownloadError("AES key 长度不是 16 字节")
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover - 部署缺依赖时才触发
        raise MediaDownloadError("服务端缺少 cryptography 依赖，无法解密媒体") from exc
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)


def _ext_from_magic(data: bytes) -> str:
    """按魔数猜图片后缀（图片协议不带原文件名）。"""
    for sig, ext in _IMAGE_MAGIC:
        if data.startswith(sig):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"BM":
        return ".bmp"
    return ""


def _safe_media_name(name: str) -> str:
    """只留 basename、去穿越、去非法字符（与 Web 附件目录同一套命名纪律）。"""
    base = Path(str(name or "")).name.replace("\\", "_").strip()
    if base in ("", ".", ".."):
        return ""
    for ch in '<>:"|?*\x00':
        base = base.replace(ch, "_")
    return base[:180]


def _unique_media_path(dir_path: Path, name: str) -> Path:
    """``cat.png`` → ``cat.png`` / ``cat-1.png`` …"""
    target = dir_path / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 1000):
        cand = dir_path / f"{stem}-{i}{suffix}"
        if not cand.exists():
            return cand
    return dir_path / f"{stem}-{secrets.token_hex(3)}{suffix}"


def _media_filename(ref: MediaRef, data: bytes) -> str:
    """给落盘文件起名：文件保留原名，图片/视频用带魔数后缀的生成名。"""
    token = secrets.token_hex(6)
    if ref.kind == "file":
        base = _safe_media_name(ref.file_name)
        if not base:
            return f"weixin-file-{token}{_ext_from_magic(data) or '.bin'}"
        if not Path(base).suffix:
            base += _ext_from_magic(data) or ".bin"
        return base
    if ref.kind == "video":
        return f"weixin-video-{token}.mp4"
    return f"weixin-image-{token}{_ext_from_magic(data) or '.img'}"


def _resolve_media_key(ref: MediaRef) -> Optional[bytes]:
    """取这条媒体的 AES key；图片可能没有（明文下载），返回 None。"""
    if ref.kind == "image":
        hex_key = (ref.image_aeskey_hex or "").strip()
        if hex_key:
            if re.fullmatch(r"[0-9a-fA-F]{32}", hex_key):
                return bytes.fromhex(hex_key)
            raise MediaDownloadError("image_item.aeskey 不是 32 位十六进制串")
        if ref.aes_key:
            return parse_aes_key(ref.aes_key)
        return None
    if ref.aes_key:
        return parse_aes_key(ref.aes_key)
    return None


async def _httpx_fetch_media(url: str, max_bytes: int) -> bytes:
    """默认取字节流：流式读，超限即中止（不把整个大文件先读进内存）。"""
    import httpx

    async with httpx.AsyncClient(timeout=API_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise MediaDownloadError(f"CDN 返回 HTTP {resp.status_code}")
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise MediaDownloadError(
                    f"文件超过 {max_bytes // (1024 * 1024)} MiB 上限",
                    too_large=True)
            chunks: List[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise MediaDownloadError(
                        f"文件超过 {max_bytes // (1024 * 1024)} MiB 上限",
                        too_large=True)
                chunks.append(chunk)
    return b"".join(chunks)


async def download_media_to(
    ref: MediaRef,
    dest_dir: Path,
    *,
    cdn_base_url: str = WEIXIN_CDN_BASE_URL,
    max_bytes: int = WEIXIN_MEDIA_MAX_BYTES,
    fetcher: Optional[Callable[[str, int], Awaitable[bytes]]] = None,
) -> Path:
    """下载 + 解密一条入站媒体，写进 ``dest_dir``，返回写入的绝对路径。

    失败一律抛 :class:`MediaDownloadError`（带可读 ``reason``），**绝不静默**。
    ``fetcher`` 仅供离线测试注入；默认走 ``httpx`` 打真实 CDN。
    """
    if not ref.has_media:
        raise MediaDownloadError("消息里没有可下载的媒体地址", kind=ref.kind)

    url = ref.full_url.strip() or build_cdn_download_url(
        ref.encrypt_query_param, cdn_base_url)
    fetch = fetcher or _httpx_fetch_media
    try:
        raw = await fetch(url, max_bytes + 16)
    except MediaDownloadError:
        raise
    except Exception as exc:  # noqa: BLE001 - 网络/超时/协议等
        raise MediaDownloadError(
            f"下载失败: {type(exc).__name__}", kind=ref.kind) from exc

    key = _resolve_media_key(ref)
    if key is None and ref.kind != "image":
        raise MediaDownloadError("缺少解密密钥（aes_key）", kind=ref.kind)

    data = raw
    if key is not None:
        try:
            data = _pkcs7_unpad(decrypt_aes_ecb(raw, key))
        except MediaDownloadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MediaDownloadError(
                f"解密失败: {type(exc).__name__}", kind=ref.kind) from exc

    if len(data) > max_bytes:
        raise MediaDownloadError(
            f"文件超过 {max_bytes // (1024 * 1024)} MiB 上限",
            kind=ref.kind, too_large=True)

    dest_dir.mkdir(parents=True, exist_ok=True)
    target = _unique_media_path(dest_dir, _media_filename(ref, data))
    try:
        target.write_bytes(data)
    except OSError as exc:
        raise MediaDownloadError(
            f"写盘失败: {type(exc).__name__}", kind=ref.kind) from exc
    return target


# ---------------------------------------------------------------------------
# 出站媒体（agent 生成/引用的文件）—— 取参数 → AES-ECB 加密 → 上传 CDN → 文件消息项
# ---------------------------------------------------------------------------
#
# 这是入站媒体那套（:func:`extract_media_refs` / :func:`download_media_to`）的
# **镜像**：入站是「拿 encrypt_query_param → 下载密文 → 用 aes_key 解密 → 落盘」；
# 出站是「本地明文 → 用**新生成的** aes_key 加密 → 上传 CDN → 拿回
# encrypt_query_param → 写进文件消息项」。两边用的是**同一个** AES-128-ECB +
# PKCS7 方案、**同一套 key 解析**（出站写进去的 ``media.aes_key`` 就是
# base64(32 位十六进制串)，正是入站 :func:`parse_aes_key` 认的两种编码之一），
# 所以「发出去的项」与「收到时的项」字段形状一一对应。
#
# 事实来源：官方 MIT 许可插件 ``@tencent-weixin/openclaw-weixin@2.4.9`` 的
# TypeScript 源码（本地只读副本，**代码不搬进本仓库**，只借字段形状/接口事实）：
#
# * 上传链各步（hash → 生成 filekey/aeskey → getUploadUrl → 加密 → POST）
#                              ``cdn/upload.ts`` 的 ``uploadMediaToCdn``
# * AES-128-ECB 加密 / 密文长度 ``cdn/aes-ecb.ts``
# * CDN 上传 URL 形状           ``cdn/cdn-url.ts:buildCdnUploadUrl``
# * CDN POST 与响应头           ``cdn/cdn-upload.ts``（``x-encrypted-param``）
# * 取上传参数的请求字段         ``api/api.ts:getUploadUrl`` / ``api/types.ts:GetUploadUrlReq``
# * media_type 取值            ``api/types.ts:UploadMediaType``
# * file 消息项字段形状         ``messaging/send.ts:sendFileMessageWeixin``
# * ``media.aes_key`` 的编码    ``messaging/send.ts``（``Buffer.from(hex).toString("base64")``）
#
# 我们**没有**真实 iLink 上传响应样本：以上是照源码推断的字段名/形状，与真实网关
# 是否逐字一致未经实测（见模块末的「未经真样本验证」说明）。
#
# 安全：``aeskey`` / ``filekey`` / 上传参数都是**每次调用新生成**的临时值，绝不写进
# 日志、异常信息或测试快照；日志只记文件名与错误类型。

#: proto: UploadMediaType —— 取上传参数时的 ``media_type``。
#: 出站只走 FILE（见 :func:`build_file_message_item`），与入站 ``MessageItemType.FILE``
#: (=4) 对应。
UploadMediaType = {"IMAGE": 1, "VIDEO": 2, "FILE": 3, "VOICE": 4}

#: CDN 上传失败重试次数（官方 ``cdn/cdn-upload.ts:UPLOAD_MAX_RETRIES`` = 3）。
#: 4xx（客户端错误）不重试；服务端错误 / 网络错重试。
WEIXIN_UPLOAD_MAX_ATTEMPTS = 3

#: 一条回复里**最多**发几个文件。保守：只发「回复里明确点名的、工作区内真实存在
#: 的文件」，且封顶，避免把聊天窗口刷爆（见 :func:`resolve_sendable_files`）。
WEIXIN_MAX_OUTBOUND_FILES = 3


class MediaUploadError(RuntimeError):
    """出站文件上传 / 加密失败；``reason`` 是可直接展示的中文原因。

    ``too_large=True`` 表示超限（不上传）；``client_error=True`` 表示 CDN 明确
    拒绝了这次上传（4xx），不应重试。
    """

    def __init__(self, reason: str, *, too_large: bool = False,
                 client_error: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.too_large = too_large
        self.client_error = client_error


@dataclass
class WeixinMediaUpload:
    """一次成功上传的结果（字段名照官方 ``UploadedFileInfo``）。"""

    filekey: str
    download_encrypted_query_param: str   # → file_item.media.encrypt_query_param
    aeskey_hex: str                       # → base64 后进 file_item.media.aes_key
    file_size: int                        # 明文大小 → file_item.len
    file_size_ciphertext: int             # 密文大小（AES-ECB + PKCS7）


def aes_ecb_padded_size(plaintext_size: int) -> int:
    """AES-128-ECB 加密后的密文长度（PKCS7 补齐到 16 的整数倍）。

    官方 ``cdn/aes-ecb.ts:aesEcbPaddedSize``：``ceil((n + 1) / 16) * 16`` ——
    注意 **+1**：明文恰好是 16 的整数倍时，PKCS7 仍要再加一整块填充。
    """
    n = max(0, int(plaintext_size))
    return ((n + 1 + 15) // 16) * 16


def encrypt_aes_ecb(plaintext: bytes, key: bytes) -> bytes:
    """AES-128-ECB + PKCS7 加密（官方 ``cdn/aes-ecb.ts:encryptAesEcb``）。

    Node 的 ``createCipheriv`` 默认就加 PKCS7 填充，Python 不会 —— 所以这里
    自己补填充。与 :func:`decrypt_aes_ecb` 严格互为逆运算（见测试）。
    """
    if len(key) != 16:
        raise MediaUploadError("AES key 长度不是 16 字节")
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover - 部署缺依赖时才触发
        raise MediaUploadError("服务端缺少 cryptography 依赖，无法加密媒体") from exc
    pad = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(padded) + enc.finalize()


def build_cdn_upload_url(upload_param: str, filekey: str,
                         cdn_base_url: str = WEIXIN_CDN_BASE_URL) -> str:
    """拼 CDN **上传**地址（官方 ``cdn/cdn-url.ts:buildCdnUploadUrl``）。

    与下载地址 :func:`build_cdn_download_url` 同源：同一个 CDN 根，路径改成
    ``/upload``，多带一个 ``filekey``。服务端若直给 ``upload_full_url`` 就优先用
    它（见 :func:`upload_file_to_cdn`），这个函数只是兜底拼接。
    """
    return (f"{cdn_base_url.rstrip('/')}/upload"
            f"?encrypted_query_param={quote(str(upload_param), safe='')}"
            f"&filekey={quote(str(filekey), safe='')}")


def build_file_media(encrypt_query_param: str, aeskey_hex: str) -> Dict[str, Any]:
    """构造 ``file_item.media``（proto: CDNMedia）。

    字段形状照官方 ``messaging/send.ts:sendFileMessageWeixin``：

    * ``encrypt_query_param``：CDN 下载参数（上传成功后由响应头给出）；
    * ``aes_key``：**base64(32 位十六进制串)** —— 官方是
      ``Buffer.from(hex).toString("base64")``，即「先把 hex 当 ASCII 字节，再 base64」。
      这正是入站 :func:`parse_aes_key` 认的第二种编码，两边因此可对扣；
    * ``encrypt_type``：1（打包缩略图/中图等信息），照官方出站固定值。
    """
    return {
        "encrypt_query_param": str(encrypt_query_param or ""),
        "aes_key": base64.b64encode(
            str(aeskey_hex or "").encode("ascii")).decode("ascii"),
        "encrypt_type": 1,
    }


def build_file_message_item(file_name: str, *, plaintext_size: int,
                            encrypt_query_param: str,
                            aeskey_hex: str) -> Dict[str, Any]:
    """构造一条 ``FILE``（type=4）消息项（proto: MessageItem）。

    字段形状照官方 ``messaging/send.ts:sendFileMessageWeixin``：

    ::

        {
          "type": 4,                         # MessageItemType.FILE
          "file_item": {
            "media": { "encrypt_query_param": <CDN 下载参数>,
                       "aes_key": <base64(hex)>, "encrypt_type": 1 },
            "file_name": "<用户看到的文件名>",
            "len": "<明文字节数，字符串>"
          }
        }

    与入站 :func:`extract_media_refs` 的 ``file_item`` 读法（``file_name`` +
    ``media.encrypt_query_param`` + ``media.aes_key``）严格对称。官方此处**不写**
    ``md5``（类型里是可选项），本实现同样不写，保持逐字段一致。
    """
    return {
        "type": MessageItemType["FILE"],
        "file_item": {
            "media": build_file_media(encrypt_query_param, aeskey_hex),
            "file_name": str(file_name or "")[:180],
            "len": str(int(plaintext_size)),
        },
    }


def build_file_message(to_user_id: str, item: Dict[str, Any], *,
                       context_token: Optional[str] = None,
                       run_id: Optional[str] = None,
                       client_id: Optional[str] = None) -> Dict[str, Any]:
    """把一条文件项包成 ``sendmessage`` 的 ``WeixinMessage``（一条消息只装一个项）。

    官方 ``messaging/send.ts`` 的 ``sendMediaItems`` 明确「Each item is sent as its
    own request so that item_list always has exactly one entry」—— 这里照做。
    其余字段（``from_user_id`` 空串、BOT、FINISH、``context_token`` 原样回带）与
    :func:`build_text_message` 完全一致。
    """
    msg: Dict[str, Any] = {
        "from_user_id": "",
        "to_user_id": to_user_id,
        "client_id": client_id or build_client_id(),
        "message_type": MessageType["BOT"],
        "message_state": MessageState["FINISH"],
        "item_list": [item],
    }
    if context_token:
        msg["context_token"] = context_token
    if run_id:
        msg["run_id"] = run_id
    return msg


async def _httpx_post_cdn(url: str, ciphertext: bytes) -> str:
    """默认上传实现：POST 密文到 CDN，返回响应头 ``x-encrypted-param``。

    这是**唯一**会打真实 CDN 的地方；测试一律注入 ``poster``，绝不联网。
    """
    import httpx

    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        resp = await client.post(
            url, content=ciphertext,
            headers={"Content-Type": "application/octet-stream"})
    if 400 <= resp.status_code < 500:
        # 明确被拒（含服务端给的 x-error-message）→ 不重试。
        raise MediaUploadError(
            f"CDN 上传被拒 HTTP {resp.status_code}", client_error=True)
    if resp.status_code != 200:
        raise MediaUploadError(f"CDN 上传失败 HTTP {resp.status_code}")
    param = resp.headers.get("x-encrypted-param")
    if not param:
        raise MediaUploadError("CDN 响应缺少 x-encrypted-param 头")
    return param


async def upload_file_to_cdn(
    path: Path | str,
    *,
    client: Any,
    to_user_id: str,
    cdn_base_url: str = WEIXIN_CDN_BASE_URL,
    max_bytes: int = WEIXIN_MEDIA_MAX_BYTES,
    poster: Optional[Callable[[str, bytes], Awaitable[str]]] = None,
    attempts: int = WEIXIN_UPLOAD_MAX_ATTEMPTS,
) -> WeixinMediaUpload:
    """把一个本地文件上传到微信 CDN，返回构造文件项所需的一切。

    步骤（官方 ``cdn/upload.ts:uploadMediaToCdn`` 的镜像）：

    1. 读明文，算 ``rawsize`` 与 ``rawfilemd5``（md5 十六进制）；
    2. 算密文长度 ``filesize``（AES-ECB + PKCS7）；明文超 ``max_bytes`` → 直接拒；
    3. 新生成 ``filekey``（16 随机字节的 hex）与 ``aeskey``（16 随机字节）；
    4. ``client.get_upload_url(...)`` 取上传参数；
    5. 用 ``aeskey`` 加密明文，POST 到上传地址，取回 ``x-encrypted-param``。

    失败一律抛 :class:`MediaUploadError`；``client`` 缺 ``get_upload_url``（例如
    测试注入的假 client）也抛这个（不静默）。``poster`` 仅供离线测试注入。
    """
    p = Path(str(path))
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise MediaUploadError(f"读取文件失败: {type(exc).__name__}") from exc
    if size > max_bytes:
        raise MediaUploadError(
            f"文件超过 {max_bytes // (1024 * 1024)} MiB 上限", too_large=True)
    try:
        plaintext = p.read_bytes()
    except OSError as exc:
        raise MediaUploadError(f"读取文件失败: {type(exc).__name__}") from exc

    import hashlib

    rawsize = len(plaintext)
    rawfilemd5 = hashlib.md5(plaintext).hexdigest()
    filesize = aes_ecb_padded_size(rawsize)
    filekey = secrets.token_hex(16)
    aeskey = secrets.token_bytes(16)

    get_upload_url = getattr(client, "get_upload_url", None)
    if get_upload_url is None:
        raise MediaUploadError("当前客户端不支持取上传参数（get_upload_url）")
    try:
        resp = await get_upload_url(
            filekey=filekey, media_type=UploadMediaType["FILE"],
            to_user_id=to_user_id, rawsize=rawsize, rawfilemd5=rawfilemd5,
            filesize=filesize, aeskey=aeskey.hex(), no_need_thumb=True)
    except MediaUploadError:
        raise
    except Exception as exc:  # noqa: BLE001 - 网络/协议/鉴权等
        raise MediaUploadError(
            f"取上传参数失败: {type(exc).__name__}") from exc
    if not isinstance(resp, dict):
        raise MediaUploadError("取上传参数返回了非对象")

    upload_full_url = str(resp.get("upload_full_url") or "").strip()
    upload_param = str(resp.get("upload_param") or "").strip()
    if not upload_full_url and not upload_param:
        raise MediaUploadError("服务端未返回上传地址（需要 upload_full_url 或 upload_param）")
    url = upload_full_url or build_cdn_upload_url(upload_param, filekey, cdn_base_url)

    ciphertext = encrypt_aes_ecb(plaintext, aeskey)
    post = poster or _httpx_post_cdn
    last_exc: MediaUploadError = MediaUploadError("CDN 上传失败")
    for attempt in range(1, max(1, int(attempts)) + 1):
        try:
            download_param = await post(url, ciphertext)
        except MediaUploadError as exc:
            last_exc = exc
            if exc.client_error:      # 4xx 明确被拒 → 不重试
                raise
        except Exception as exc:  # noqa: BLE001 - 网络错误 → 可重试
            last_exc = MediaUploadError(f"CDN 上传失败: {type(exc).__name__}")
        else:
            if not download_param:
                raise MediaUploadError("CDN 未返回下载参数")
            return WeixinMediaUpload(
                filekey=filekey,
                download_encrypted_query_param=str(download_param),
                aeskey_hex=aeskey.hex(),
                file_size=rawsize,
                file_size_ciphertext=filesize,
            )
        logger.debug("weixin: CDN 上传第 %d/%d 次失败", attempt, attempts)
    raise last_exc


# 路径候选：一段不含空白/引号/括号/中文标点的连续串；其**最后一段**要含一个点
# （像 ``报告.md`` / ``outputs/report.md`` / ``C:\x\a.txt`` 这样带后缀），否则不算。
# 「带不带路径分隔符」都可——裸文件名（``报告.md``）在项目根下真实存在时也是有意义
# 的相对路径；是否存在 / 是否在围墙内由 :func:`resolve_sendable_files` 用同一套
# 围墙判定来过滤。这样普通中文句子、单字、无后缀的词都不会被当成文件。
_PATH_TOKEN_RE = re.compile(r"""[^\s"'`<>|*?\[\](){}，。；：、]+""")


def _candidate_path_tokens(text: str) -> List[str]:
    """从回复文本里扫出「像文件路径」的候选串（保守：最后一段必须带 ``.后缀``）。"""
    out: List[str] = []
    for match in _PATH_TOKEN_RE.finditer(text or ""):
        token = match.group(0).strip(".,;:!?…—-")
        token = token.rstrip("/\\")
        if not token:
            continue
        last = re.split(r"[\\/]", token)[-1]
        if "." not in last.strip("."):
            continue
        out.append(token)
    return out


def _path_token_variants(token: str) -> List[str]:
    """一个候选 token 的若干切法：**中文常和路径粘连**（``已生成报告.md``）。

    中文里词与词之间没有空格，所以「已生成报告.md」会是一个整 token，直接拿去解析
    会找不到文件。这里额外给出「逐步去掉**前导**非 ASCII 字符」和「逐步去掉**尾随**
    非 ASCII 字符」的变体（``已生成报告.md`` → ``生成报告.md`` → ``成报告.md`` →
    ``报告.md`` …）。**哪一个真的存在由后面的围墙判定 + ``is_file`` 决定**，所以
    这些变体只是多几次「猜」，不会凭空造出文件、也不会越过围墙。
    """
    variants = [token]
    for i, ch in enumerate(token):
        if ord(ch) > 127:
            variants.append(token[i + 1:])
        else:
            break
    for j in range(len(token) - 1, -1, -1):
        if ord(token[j]) > 127:
            variants.append(token[:j])
        else:
            break
    return variants


#: 绝不外发的**文件名**（小写比较）。这些名字本身就意味"里面是凭据/配置"，
#: 不该因为模型在回复里提了一句就被自动上传到第三方 CDN。
WEIXIN_NEVER_SEND_NAMES = frozenset({
    ".env", "settings.json", "credentials.json", "secrets.json",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "known_hosts", ".netrc", "netrc",
})
#: 绝不外发的**后缀**。
WEIXIN_NEVER_SEND_SUFFIXES = (
    ".env", ".pem", ".key", ".pfx", ".p12", ".jks", ".keystore", ".ppk",
    ".db", ".sqlite", ".sqlite3", ".kdbx",
)
#: 路径里出现这些**目录名**（任一层）即拒。
WEIXIN_NEVER_SEND_DIRS = frozenset({".git", ".ssh", ".gnupg", ".aws"})
#: 文件名（主名）里含这些词即拒 —— 宁可少发，也不要因为模型提了一句
#: "token.txt" 就把令牌上传出去。（这条会影响自动外发，用户仍能在文本回复里
#: 看到路径，自行决定。）
WEIXIN_NEVER_SEND_WORDS = (
    "secret", "credential", "password", "passwd", "token", "apikey", "api_key", "private",
)
#: 内容嗅探读多少字节（只读这么多，避免为一个启发式把大文件整个读进内存）。
WEIXIN_CONTENT_SNIFF_BYTES = 65536


def _refuse_to_send(path: Path) -> bool:
    """这个文件**绝不**自动外发吗？

    触发规则是启发式（"回复里提到的、工作区内真实存在的文件"），已知会把模型只是
    *提及* 的文件也选中。若不做这道闸，一句"你正在看的 ``data/settings.json``"
    就会把带密钥的文件加密上传到第三方 CDN —— 那不是 UX 问题，是凭据外泄。

    三道判据，任一命中即拒（宁可不发，文本回复照旧会把文件在哪告诉用户）：
    文件/目录名形状、后缀、以及**内容里出现凭据形状**（复用 ``kairos.sentinel``
    自己的脱敏规则；内容变了就说明里面有东西会被脱敏 ⇒ 不发）。
    """
    name = path.name.lower()
    if name in WEIXIN_NEVER_SEND_NAMES:
        return True
    if name.endswith(WEIXIN_NEVER_SEND_SUFFIXES):
        return True
    if any(part.lower() in WEIXIN_NEVER_SEND_DIRS for part in path.parts):
        return True
    stem = path.stem.lower()
    if any(word in stem for word in WEIXIN_NEVER_SEND_WORDS):
        return True
    try:
        sample = path.open("rb").read(WEIXIN_CONTENT_SNIFF_BYTES)
    except OSError:
        return True     # 读不了就不要发
    if b"-----BEGIN" in sample and b"PRIVATE KEY" in sample:
        return True
    try:
        from kairos.sentinel import redact
    except Exception:  # noqa: BLE001
        return True     # 没有脱敏器就无法判断 ⇒ 不发
    text_sample = sample.decode("utf-8", errors="ignore")
    return redact(text_sample) != text_sample


# ---------------------------------------------------------------------------
# 「本轮真实产出」信号
# ---------------------------------------------------------------------------
# 出站文件不应只靠「模型嘴上有没有提到某个路径」来判断 —— 那是启发式，而且在
# 沙箱模式下模型的写盘地是 **coder 工作树**（``project.runtime.coder_worktree``
# 的 ``.path``），它嘴里的相对路径往往落不到项目根上。这里加一条**真信号**：
# 调 agent 之前对相关根（项目根 + coder 工作树）拍一次「路径 → (mtime, size)」
# 快照，agent 返回后再拍一次，**新增或 (mtime/size) 变化的文件**就是本轮真正写
# 出来的东西。快照只用于**挑候选**；发送仍走与启发式**完全同一套**安全门
# （围墙 / is_file / 大小上限 / 数量上限 / 名字黑名单 / 内容嗅探）。
#
# 全程 best-effort、有界（条目数 + 墙钟），任何异常都退化为「没有本轮产出信号」。

#: 快照扫描时**跳过**的目录名（任意一层命中即不进其子树）。集中一处，理由：
#: * ``.git`` / ``node_modules`` / ``.venv`` / ``venv`` / ``site-packages`` /
#:   ``__pycache__``：版本控制与依赖/字节码缓存 —— 它们的变动不是用户要的交付物；
#: * ``attachments``：**入站附件**暂存目录（用户自己发进来的东西）；把它当「本轮
#:   产出」回发，等于把用户刚上传的文件再弹回去，纯噪音；
#: * ``.kairos-worktrees`` / ``runs`` / 前缀 ``.kairos-``：运行时目录（沙箱工作树、
#:   任务运行记录、锁与日志）—— 工作树本身会作为**独立的根**被单独扫描，这里不重复进；
#: * ``dist`` / ``build`` / ``.mypy_cache`` / ``.pytest_cache`` / ``.tox`` / ``.eggs``：
#:   构建产物与工具缓存。
# The snapshot / diff machinery itself now lives in the neutral module
# ``kairos.file_snapshot`` (shared with the general chat lane). These names are
# re-exported here unchanged so every existing importer -- and this module's own
# ``__all__`` -- keeps working.
from kairos.file_snapshot import (  # noqa: E402  (mid-module on purpose)
    SNAPSHOT_IGNORE_DIRS as WEIXIN_SNAPSHOT_IGNORE_DIRS,
    SNAPSHOT_IGNORE_PREFIXES as WEIXIN_SNAPSHOT_IGNORE_PREFIXES,
    SNAPSHOT_IGNORE_SUFFIXES as WEIXIN_SNAPSHOT_IGNORE_SUFFIXES,
    SNAPSHOT_MAX_ENTRIES as WEIXIN_SNAPSHOT_MAX_ENTRIES,
    SNAPSHOT_MAX_SECONDS as WEIXIN_SNAPSHOT_MAX_SECONDS,
    diff_touched_files,
    normalize_roots as _normalize_roots,
    snapshot_ignored_dir as _snapshot_ignored_dir,
    snapshot_ignored_file as _snapshot_ignored_file,
    snapshot_tree_files,
)


# ``_normalize_roots`` / ``_snapshot_ignored_dir`` / ``_snapshot_ignored_file``
# / ``snapshot_tree_files`` / ``diff_touched_files`` are the shared
# implementations imported above from ``kairos.file_snapshot``.


def _resolve_first_within(path: Any, roots: Sequence[Path]) -> Optional[Path]:
    """把 ``path`` 解析到**任意一个**根之内；都不在就返回 ``None``。

    与只读文件工具用**同一套**围墙（``resolve_within_root``）—— 逐根判定，
    不在任何根内（或非法路径）的候选一律丢弃。
    """
    from kairos.tools.base import resolve_within_root

    for root in roots:
        try:
            return resolve_within_root(str(path), root)
        except (PermissionError, OSError, ValueError):
            continue
    return None


def select_sendable_paths(
    paths: Sequence[Any],
    roots: Any,
    *,
    max_files: int = WEIXIN_MAX_OUTBOUND_FILES,
    max_bytes: int = WEIXIN_MEDIA_MAX_BYTES,
) -> List[Path]:
    """把一批**已知**路径过一遍与启发式完全相同的安全门，返回合格文件。

    门（与 :func:`resolve_sendable_files` 逐条一致）：围墙内（``resolve_within_root``
    逐根判定）→ ``is_file()`` → ``≤ max_bytes`` → 过 ``_refuse_to_send``（名字黑名单
    + 后缀 + 目录 + 内容嗅探）。去重、封顶 ``max_files``，顺序按传入顺序。
    """
    root_list = [Path(str(r)) for r in _normalize_roots(roots)]
    if not root_list:
        return []
    seen: set = set()
    picked: List[Path] = []
    for raw in paths:
        if len(picked) >= max(0, int(max_files)):
            break
        candidate = _resolve_first_within(raw, root_list)
        if candidate is None:
            continue
        try:
            if not candidate.is_file():
                continue
            size = candidate.stat().st_size
        except OSError:
            continue
        if size > max_bytes:
            continue
        if _refuse_to_send(candidate):
            continue
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        picked.append(candidate)
    return picked


def merge_sendable_candidates(
    primary: Sequence[Path],
    fallback: Sequence[Path],
    *,
    max_files: int = WEIXIN_MAX_OUTBOUND_FILES,
) -> List[Path]:
    """合并「真产出信号」与「文本启发式」候选，**同一文件只留一次**、顺序稳定。

    真产出（``primary``）在前 —— 那是这一轮真正写出来的东西，比「模型嘴上提到
    了某个旧文件」更值得先发；然后补 ``fallback`` 里没重复的，封顶 ``max_files``。
    """
    out: List[Path] = []
    seen: set = set()
    for path in list(primary) + list(fallback):
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
        if len(out) >= max(0, int(max_files)):
            break
    return out


def resolve_sendable_files(
    text: str,
    root: Any,
    *,
    max_files: int = WEIXIN_MAX_OUTBOUND_FILES,
    max_bytes: int = WEIXIN_MEDIA_MAX_BYTES,
) -> List[Path]:
    """**启发式**：从回复文本里挑出「工作区内真实存在、且不超限」的文件路径。

    **这只是启发式（现在是 fallback，不是主信号）** —— ``run_chat_reply`` 与
    ``coder.chat`` 的**返回值**都只有 ``str``，没有任何「我产出了哪些文件」的字段
    （通用车道的产物走 ``run_chat_reply(artifacts_out=...)`` 出参，见
    ``kairos/skeleton/service.py``；coder 侧 ``kairos/agents/agent_parts/chat.py``
    仍是纯文本）。
    真正的「本轮产出」信号由 **快照差集**（:func:`diff_touched_files`）提供；这里
    退而求其次：**扫描回复文本里出现的、位于工作区内且真实存在的文件路径**，用来
    兜住「模型主动点名、但本轮没改动的既有文件」。最多 ``max_files`` 个、每个
    ≤ ``max_bytes``。

    围墙判定与只读文件工具**同一套**：``kairos.tools.base.resolve_within_root``
    （相对路径按 root 解析、绝对路径原样、``..``/软链都会解析后重新检查是否仍在
    root 之内；``is_full_access()`` 时按同一策略放行）。``root`` 可以是单个根，
    也可以是「项目根 + coder 工作树」的列表 —— **逐根判定**，不在任何根内的候选
    一律跳过。围墙外 / 不存在 / 目录 / 超限的候选一律跳过 —— 找不到就返回 ``[]``，
    调用方据此**不发任何文件**、只发文本。
    """
    if not text or root is None:
        return []
    root_paths = [Path(str(r)) for r in _normalize_roots(root)]
    if not root_paths:
        return []
    seen: set = set()
    picked: List[Path] = []
    for token in _candidate_path_tokens(str(text)):
        if len(picked) >= max(0, int(max_files)):
            break
        for variant in _path_token_variants(token):
            if len(picked) >= max(0, int(max_files)):
                break
            candidate = _resolve_first_within(variant, root_paths)
            if candidate is None:
                continue    # 不在任何围墙根内 / 非法路径 → 不发
            try:
                if not candidate.is_file():
                    continue
                size = candidate.stat().st_size
            except OSError:
                continue
            if size > max_bytes:
                continue    # 超限 → 不发
            if _refuse_to_send(candidate):
                continue    # 凭据/配置形状 → 绝不上传
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            picked.append(candidate)
    return picked


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

    async def get_upload_url(
        self, *,
        filekey: str,
        media_type: int,
        to_user_id: str,
        rawsize: int,
        rawfilemd5: str,
        filesize: int,
        aeskey: str,
        no_need_thumb: bool = True,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """取 CDN 上传参数（``ilink/bot/getuploadurl``）。

        字段照官方 ``api/api.ts:getUploadUrl`` / ``api/types.ts:GetUploadUrlReq``：
        ``filekey``、``media_type``（:data:`UploadMediaType`）、``to_user_id``、
        明文大小 ``rawsize``、明文 md5 ``rawfilemd5``、密文大小 ``filesize``、
        ``no_need_thumb``、``aeskey``（十六进制串）。响应形如
        ``{upload_param?, upload_full_url?, thumb_upload_param?}`` —— 前两者至少
        有一个非空（由 :func:`upload_file_to_cdn` 校验）。

        与 ``send_message`` 一样是**鉴权**请求（带 token）；``aeskey`` 是本条上传
        的临时密钥，除本请求体外绝不外传、不落日志。
        """
        body = {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": filesize,
            "no_need_thumb": no_need_thumb,
            "aeskey": aeskey,
            "base_info": self.base_info(),
        }
        raw = await self._request("POST", "ilink/bot/getuploadurl", body=body,
                                  timeout=timeout or self.api_timeout,
                                  with_auth=True)
        return self._check_error(parse_weixin_api_json(raw))

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

@dataclass
class ReplyDelivery:
    """一次分段投递的结果（给调用方 / 测试看「失败边界」用）。

    **不是 token 流**（见模块顶部说明）：``chunks`` 是对**完整回复**按自然边界
    切出的段，``"".join(chunks)`` 逐字等于原文。

    * ``ok=True`` ⇒ 用户拿到了**完整**回复（可能一条，也可能多条）；
      此时 ``delivered_text == "".join(chunks)``。
    * ``ok=False`` ⇒ 从第 ``failed_at`` 段起投递失败且重投仍失败；
      ``sent_chunks`` 是已成功发出的条数，``delivered_text`` 是**实际已送达**的
      前缀 —— 边界在此**显式可见**，绝不静默截断（日志另有一条 ERROR）。
    """

    chunks: List[str]
    sent_chunks: int
    ok: bool
    failed_at: Optional[int] = None
    error: str = ""
    delivered_text: str = ""

    @property
    def total_chunks(self) -> int:
        return len(self.chunks)


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
                 poll_interval: float = 0.3,
                 workspace_resolver: Optional[Callable[[str, str], Any]] = None) -> None:
        self.store = store
        self._dispatch = dispatch
        self._client_factory = client_factory
        # 「账号 + 聊天对象 → 该项目工作区根」的解析器（可由路由注入，同步或异步都
        # 行）。**为 None 时完全不发文件**（与改动前逐字一致）——文件发送是可选
        # 能力，且只在能确定围墙根时才启用（见 _resolve_root / deliver_files）。
        self.workspace_resolver = workspace_resolver
        # dispatch 是否声明了第 4 个参数（入站媒体引用）。老的三参回调保持原样。
        self._dispatch_takes_media = _callable_accepts_media(dispatch)
        self.base_url = base_url
        self.bot_agent = bot_agent
        self.poll_interval = poll_interval
        self._clients: Dict[str, ILinkClient] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._stopping: set = set()
        # 正在处理中的入站消息任务。每条消息单独一个任务（而不是在轮询协程里
        # await），否则一个卡住的 agent 轮次会把**同一个账号**的后续消息（包括
        # 用来回答审批的 /approve）一起堵在后面 —— 审批就永远收不到回答。
        self._message_tasks: set = set()

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
        # 收尾该账号还在处理的消息任务（可能正停在等审批上），不让它们
        # 随着账号一起变成孤儿任务。
        for msg_task in list(self._message_tasks):
            msg_task.cancel()
        for msg_task in list(self._message_tasks):
            try:
                await msg_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._message_tasks.clear()
        client = self._clients.pop(account_id, None)
        if client is not None:
            try:
                await client.notify_stop()
            except ILinkError as exc:
                # Best-effort shutdown notice to iLink; the account is already
                # stopped locally, but a failed notify_stop must leave a trace.
                logger.warning("weixin stop_account: notify_stop failed "
                               "(account=%s): %s", account_id, exc)
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
                # 每条消息交给独立任务：一个停在等审批（或跑长任务）的轮次
                # 不能把同一账号后续消息里的 /approve 一起堵死。异常在任务内部
                # 兜住（一条坏消息不该拖垮轮询）。
                self._spawn_message_task(account_id, client, msg)

            if not msgs:
                await asyncio.sleep(self.poll_interval)

    def _spawn_message_task(self, account_id: str, client: ILinkClient,
                            msg: Dict[str, Any]) -> None:
        """把一条入站消息丢进独立任务，并记账以便账号停止时一起收尾。"""
        task = asyncio.create_task(
            self._handle_message_safe(account_id, client, msg),
            name=f"weixin-msg-{account_id}")
        self._message_tasks.add(task)
        task.add_done_callback(self._message_tasks.discard)

    async def _handle_message_safe(self, account_id: str, client: ILinkClient,
                                   msg: Dict[str, Any]) -> None:
        """``handle_message`` 的任务壳：异常只记日志，不炸掉任务。"""
        try:
            await self.handle_message(account_id, client, msg)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 一条坏消息不该拖垮轮询
            logger.exception("weixin 处理消息失败 account=%s", account_id)

    async def handle_message(self, account_id: str, client: ILinkClient,
                             msg: Dict[str, Any]) -> Optional[str]:
        """处理一条入站消息：取正文 + 媒体引用 → dispatch → 发回回复。

        媒体（图片/文件/视频）不在这里下载：本层不知道消息属于哪个项目。
        我们把 :class:`MediaRef` 列表交给 dispatch 回调（若它声明了第 4 个
        参数），由它落到该项目的附件目录并按 Web 同一套折进提示词。
        """
        if not is_user_message(msg):
            return None
        from_user = (msg.get("from_user_id") or "").strip()
        text = extract_text(msg)
        media_refs = extract_media_refs(msg)

        context_token = msg.get("context_token") or \
            await self.store.get_context_token(account_id, from_user)
        if msg.get("context_token"):
            await self.store.set_context_token(
                account_id, from_user, msg["context_token"])

        # 「本轮真实产出」快照（1/2）—— 调 agent **之前**先对围墙根拍一次
        # 「路径 → (mtime, size)」。best-effort + 封顶：拿不到就当「没有本轮产出
        # 信号」，绝不影响下面的 dispatch 或文本回复。
        roots_before: List[str] = []
        snapshot_before: Dict[str, Tuple[float, int]] = {}
        try:
            roots_before = await self._resolve_workspace_roots(account_id, from_user)
            if roots_before:
                snapshot_before = snapshot_tree_files(roots_before)
        except Exception:  # noqa: BLE001 - 快照只是可选信号
            roots_before, snapshot_before = [], {}

        reply = ""
        if self._dispatch is not None:
            # 处理这条消息期间标记「当前会话」：审批桥据此判断闸门刚问出的
            # 那个问题属不属于这个微信会话（见模块顶部的 ``current_session``）。
            token = current_session.set((account_id, from_user))
            try:
                if self._dispatch_takes_media:
                    reply = await self._dispatch(
                        account_id, from_user, text, media_refs) or ""
                else:
                    reply = await self._dispatch(account_id, from_user, text) or ""
            finally:
                current_session.reset(token)
        if reply:
            # 「本轮真实产出」快照（2/2）—— agent 返回后再拍一次；**新增或
            # (mtime/size) 变化的文件**就是这一轮真正写出来的东西（沙箱模式下写在
            # coder 工作树里）。注意：首次消息时项目/工作树可能是在 dispatch 内才
            # 创建（此时 roots_before 为空 → 无可信基线 ``baseline_ok=False``，本轮
            # 不启用差集，避免把工作树 checkout 出的既有文件全当成「新产出」）。
            roots_after: List[str] = roots_before
            snapshot_after: Dict[str, Tuple[float, int]] = {}
            try:
                roots_after = await self._resolve_workspace_roots(
                    account_id, from_user)
                if roots_after:
                    snapshot_after = snapshot_tree_files(roots_after)
            except Exception:  # noqa: BLE001 - 快照只是可选信号
                roots_after, snapshot_after = roots_before, {}
            # 出站文件（可选能力）：**先发文件、再发文本** —— 用户先看到东西、再看
            # 说明。文件 = 「本轮真实产出」（快照差集，主信号）∪「回复里点名 +
            # 工作区内真实存在」（文本启发式，fallback），合并去重、过同一套安全门。
            # 这条路整体 best-effort：没有围墙根 / 没有文件 / 取参数失败 / 加密或
            # 上传失败 / 超限 / 围墙外 —— 一律**什么都不发**（不改下面这条文本回复），
            # **绝不**吞掉或重复发文本。
            await self.deliver_files_before_reply(
                client, account_id, from_user, reply, context_token,
                roots=roots_after,
                before=snapshot_before,
                after=snapshot_after,
                baseline_ok=bool(roots_before))
            # 分段渐进投递（不是 token 流）：短回复一条、长回复 ≤N 条，拼接逐字
            # 等于 reply。中途失败会把剩余整体重投，仍失败则 report.ok=False
            # 且 report.failed_at 指出边界 —— 下面记一条 ERROR，决不静默截断。
            report = await self.deliver_reply(
                client, from_user, reply, context_token=context_token)
            if not report.ok:
                logger.error(
                    "weixin: 回复未完整投递 account=%s chat=%s sent=%d/%d "
                    "failed_at=%s error=%s",
                    account_id, from_user, report.sent_chunks,
                    report.total_chunks, report.failed_at, report.error)
        return reply

    # -- 出站（发送） ----------------------------------------------------

    async def _send_reply_chunk(self, client: ILinkClient, to_user_id: str,
                                text: str, context_token: Optional[str],
                                client_id: str) -> Dict[str, Any]:
        """发一段回复。``client_id`` 在**同一段的多次尝试间复用**，供服务端去重。"""
        return await client.send_message(build_text_message(
            to_user_id, text, context_token=context_token, client_id=client_id))

    # -- 出站文件（真产出信号 + 启发式 fallback，best-effort）------------

    async def _resolve_workspace_roots(self, account_id: str,
                                       chat_id: str) -> List[str]:
        """解析「账号 + 聊天对象」对应的**所有**围墙根（项目根 + coder 工作树）。

        ``workspace_resolver`` 可以是同步函数、也可以是协程（路由那个要查 store，
        是异步的）—— 两种都支持；**返回值可以是单个根（``str``/``Path``）也可
        以是根列表**，都规范成去重后的字符串列表。任何异常都当「拿不到根」处理
        （返回 ``[]``，不抛），因为拿不到根只会导致「不发文件」，不该影响回复。
        """
        resolver = self.workspace_resolver
        if resolver is None:
            return []
        try:
            value = resolver(account_id, chat_id)
            if inspect.isawaitable(value):
                value = await value
        except Exception:  # noqa: BLE001 - 拿不到根只是不发文件
            logger.debug("weixin: 解析工作区根失败（不发文件）", exc_info=True)
            return []
        return _normalize_roots(value)

    async def _resolve_workspace_root(self, account_id: str,
                                      chat_id: str) -> Optional[str]:
        """兼容旧契约：只取第一个根（新代码请用 :meth:`_resolve_workspace_roots`）。"""
        roots = await self._resolve_workspace_roots(account_id, chat_id)
        return roots[0] if roots else None

    async def send_files_best_effort(self, client: ILinkClient, to_user_id: str,
                                     paths: Sequence[Path], *,
                                     context_token: Optional[str] = None) -> int:
        """逐个上传并发送文件项；**绝不抛异常**，返回成功发出的个数。

        一个文件失败（取参数 / 加密 / 上传 / 发送任一环节）只记一条日志 —— 记的是
        **文件名与错误类型**，不含 aeskey / filekey / 上传参数等临时密钥 —— 然后继续
        下一个，不让单个坏文件影响其它文件或后面的文本回复。
        """
        sent = 0
        for path in paths:
            try:
                upload = await upload_file_to_cdn(
                    path, client=client, to_user_id=to_user_id)
                item = build_file_message_item(
                    Path(str(path)).name,
                    plaintext_size=upload.file_size,
                    encrypt_query_param=upload.download_encrypted_query_param,
                    aeskey_hex=upload.aeskey_hex)
                await client.send_message(build_file_message(
                    to_user_id, item, context_token=context_token))
            except Exception as exc:  # noqa: BLE001 - 一个文件失败不该影响文本回复
                logger.error("weixin: 文件未发出 name=%s error=%s",
                             Path(str(path)).name, type(exc).__name__)
                continue
            sent += 1
        return sent

    async def deliver_files_before_reply(
        self, client: ILinkClient, account_id: str, to_user_id: str,
        reply: str, context_token: Optional[str] = None, *,
        roots: Any = None,
        before: Optional[Dict[str, Tuple[float, int]]] = None,
        after: Optional[Dict[str, Tuple[float, int]]] = None,
        baseline_ok: bool = True,
    ) -> int:
        """把「本轮真正产出的文件」+「回复里点名的文件」发出去（**先于**文本回复）。

        两路候选**合并去重、顺序稳定**：

        1. **真信号**（主）：``before``/``after`` 两份「路径 → (mtime, size)」快照的
           差集 = 本轮新增或改动的文件（``baseline_ok`` 为假时跳过 —— 没有可信基线）；
        2. **文本启发式**（fallback，见 :func:`resolve_sendable_files`）：模型在回复里
           点名、但本轮没改动的既有文件。

        两路都过**完全同一套**安全门（围墙 / ``is_file`` / 大小上限 / 数量上限 /
        名字黑名单 ``WEIXIN_NEVER_SEND_NAMES`` / 内容嗅探 ``_refuse_to_send``）。
        ``roots`` 可传（项目根 + coder 工作树）；不传则现解析。

        返回成功发出的文件数（``0`` = 没发任何文件：没配解析器 / 没找到合格文件 /
        全失败）。任何情况下都**不抛**、**不改**文本回复 —— 文件发不出时，文本本身
        就写着文件在哪，用户仍然知道东西在哪，故**不另补一条**说明（避免重复消息）。
        """
        try:
            if roots is None:
                root_list = await self._resolve_workspace_roots(
                    account_id, to_user_id)
            else:
                root_list = _normalize_roots(roots)
            if not root_list:
                return 0
            # 1) 真信号：本轮新增/改动的文件（与启发式同一套安全门）。
            touched: List[Path] = []
            if baseline_ok:
                touched = select_sendable_paths(
                    diff_touched_files(before, after), root_list)
            # 2) fallback：回复文本里点名、工作区内真实存在的文件。
            heuristic = resolve_sendable_files(reply, root_list)
            files = merge_sendable_candidates(touched, heuristic)
            if not files:
                return 0
            # 可观测（路径脱敏为文件名）：区分「本轮没产出」与「产出了但发失败」。
            logger.info(
                "weixin: 出站文件候选 candidates=%d (touched=%d heuristic=%d) "
                "names=%s", len(files), len(touched), len(heuristic),
                [p.name for p in files])
            sent = await self.send_files_best_effort(
                client, to_user_id, files, context_token=context_token)
            logger.info("weixin: 出站文件实发 sent=%d/%d names=%s",
                        sent, len(files), [p.name for p in files])
            return sent
        except Exception:  # noqa: BLE001 - 文件这条路整体 best-effort
            logger.exception("weixin: 出站文件流程异常（已忽略，文本照发）")
            return 0

    async def deliver_reply(self, client: ILinkClient, to_user_id: str,
                            text: str, *,
                            context_token: Optional[str] = None,
                            max_chunks: Optional[int] = None) -> ReplyDelivery:
        """把一条**完整回复**分段渐进投递给一个聊天对象。

        —— **不是 token 流**：``text`` 到手时已是完整字符串（见模块顶部说明）。
        短回复（≤ 阈值）就是一条，与改动前逐字一致；长回复按自然边界切成
        ≤ ``max_chunks`` 段逐条发出，拼接逐字等于 ``text``。

        中途失败 → 把**剩余部分**拼成一条再投
        :data:`WEIXIN_STREAM_REMAINDER_RETRIES` 次（≥1）；仍失败则返回
        ``ok=False``（``failed_at`` 指出边界），**绝不静默截断**。

        ``send_text``（审批推送 / 测试 `/send`）不走这里，仍是单条 —— 与改动前
        一致。
        """
        chunks = split_reply_for_delivery(text, max_chunks=max_chunks)
        if not chunks:
            return ReplyDelivery([], 0, True, None, "", "")

        delay = _stream_delay_seconds()
        delivered: List[str] = []
        for i, chunk in enumerate(chunks):
            if i and delay:
                await asyncio.sleep(delay)
            try:
                # client_id 生成一次、本段（含潜在重试）复用，防服务端重复落库。
                await self._send_reply_chunk(
                    client, to_user_id, chunk, context_token, build_client_id())
            except Exception as exc:  # noqa: BLE001 - 出站失败要显式处理，不吞
                return await self._deliver_remainder(
                    client, to_user_id, chunks, i, delivered, context_token, exc)
            delivered.append(chunk)
        return ReplyDelivery(chunks, len(chunks), True, None, "",
                             "".join(delivered))

    async def _deliver_remainder(
        self, client: ILinkClient, to_user_id: str, chunks: List[str],
        failed_index: int, delivered: List[str], context_token: Optional[str],
        first_exc: Exception,
    ) -> ReplyDelivery:
        """第 ``failed_index`` 段发送失败：把**剩余部分**拼成一条整体重投。

        剩余的 ``"".join(chunks[failed_index:])`` 是一条**完整**的剩余文本，所以
        用户最终要么拿到它（合并成一条），要么收到 ``ok=False`` 的明确失败 —— 不会
        在微信里留半截。重投复用同一个 ``client_id``（服务端去重）。
        """
        remainder = "".join(chunks[failed_index:])
        rest_client_id = build_client_id()
        last_exc: Exception = first_exc
        for _attempt in range(max(1, WEIXIN_STREAM_REMAINDER_RETRIES)):
            try:
                await self._send_reply_chunk(client, to_user_id, remainder,
                                             context_token, rest_client_id)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
            delivered.append(remainder)
            return ReplyDelivery(chunks, len(delivered), True, None, "",
                                 "".join(delivered))
        logger.error(
            "weixin: 分段投递失败，第 %d/%d 段起（已送达 %d 条）重投 %d 次仍失败: %s",
            failed_index + 1, len(chunks), len(delivered),
            WEIXIN_STREAM_REMAINDER_RETRIES, type(last_exc).__name__)
        return ReplyDelivery(
            chunks, len(delivered), False, failed_index,
            f"{type(last_exc).__name__}: {last_exc}", "".join(delivered))

    # -- 出站（主动发送） ------------------------------------------------

    async def send_text(self, account_id: str, to_user_id: str, text: str,
                        context_token: Optional[str] = None) -> Dict[str, Any]:
        """给一个聊天对象主动发文本（测试 / 主动推送用）。"""
        client = await self.client_for(account_id)
        if context_token is None:
            context_token = await self.store.get_context_token(account_id, to_user_id)
        return await client.send_message(build_text_message(
            to_user_id, text, context_token=context_token))


# ---------------------------------------------------------------------------
# 未经真样本验证（诚实边界）
# ---------------------------------------------------------------------------
#
# 本模块里**所有**「微信 CDN 媒体」相关的字段名 / 形状，都是照官方 MIT 许可插件
# ``@tencent-weixin/openclaw-weixin@2.4.9`` 的 TypeScript 源码推断的（源码文件名
# 见各段注释），**不是**从真实网关的响应抓下来的样本：
#
# * 入站：``item_list`` 里 ``image_item`` / ``video_item`` / ``file_item`` 的子字段、
#   ``media.encrypt_query_param`` / ``media.aes_key`` / ``image_item.aeskey``、
#   ``cdn/cdn-url.js`` 的下载 URL 形状；
# * 出站：``ilink/bot/getuploadurl`` 的请求字段与 ``{upload_param, upload_full_url}``
#   响应、``cdn/cdn-url.js`` 的上传 URL 形状、CDN 上传响应头 ``x-encrypted-param``、
#   以及 ``file_item``（type=4）的 ``file_name`` / ``len`` 与 ``media.aes_key`` 的
#   base64(hex) 编码。
#
# 这些**没有在真实微信上端到端跑过**（需要真机扫码登录 + 真实收发链路）。离线测试
# 只覆盖本地加解密往返、字段形状与兼底逻辑（见 ``tests/test_weixin_media_inbound.py``
# 与 ``tests/test_weixin_media_outbound.py``）。若真机字段名与推断不符，先改这里，
# 再改测试。

