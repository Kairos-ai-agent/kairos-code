"""微信 iLink 入站媒体（图片 / 文件 / 视频）—— 全离线测试。

不联网、不用真微信账号：用一个**假的 CDN 取字节函数**（注入
``download_media_to(fetcher=...)``）和一段本地 AES-128-ECB/PKCS7 加密，
覆盖：

* 媒体 item → 落盘文件真的出现在**项目附件目录**（``<work_dir>/attachments/``）；
* 折进提示词：走 Web 同一套 ``attachment_prompt_block``；
* 下载失败 / 超限 / 未知类型 → 走“失败可见”分支，不静默、不崩；
* 纯文本消息行为**逐字不变**（回归）；
* 只有媒体、没有正文的消息仍通过 ``is_user_message``（去重放行）。

协议字段名/形状来自官方插件源码（见 ``kairos/weixin_ilink.py`` 顶部注释），
**不是真响应样本**。
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api.routes.weixin as wx  # noqa: E402
from kairos.weixin_ilink import (  # noqa: E402
    WEIXIN_MEDIA_MAX_BYTES,
    MediaDownloadError,
    MediaRef,
    WeixinAccountStore,
    WeixinChannel,
    build_cdn_download_url,
    download_media_to,
    extract_media_refs,
    extract_text,
    is_user_message,
    parse_aes_key,
)

# ---------------------------------------------------------------------------
# 本地加解密 / 假取字节
# ---------------------------------------------------------------------------

KEY = bytes(range(16))                    # 16 字节 AES-128 key
KEY_HEX = KEY.hex()                       # 32 位十六进制串
PNG = b"\x89PNG\r\n\x1a\n" + b"kairos-image-bytes"


def _aes_ecb_encrypt(plaintext: bytes, key: bytes = KEY) -> bytes:
    """本地实现 AES-128-ECB + PKCS7，用来造“CDN 上传后的密文”。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    pad = 16 - (len(plaintext) % 16)
    data = plaintext + bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(data) + enc.finalize()


class _Fetcher:
    """假 CDN：按 URL 返回预置字节；也可直接抛错。"""

    def __init__(self, payload: bytes | None = None,
                 error: Optional[Exception] = None) -> None:
        self.payload = payload
        self.error = error
        self.urls: List[str] = []

    async def __call__(self, url: str, max_bytes: int) -> bytes:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        data = self.payload if self.payload is not None else b""
        if len(data) > max_bytes:
            raise MediaDownloadError(
                f"文件超过 {max_bytes // (1024 * 1024)} MiB 上限", too_large=True)
        return data


# ---------------------------------------------------------------------------
# 入站消息构造
# ---------------------------------------------------------------------------

def _image_msg(*, aeskey_hex: str = "", aes_key: str = "",
               eqp: str = "EQP-IMG", full_url: str = "") -> Dict[str, Any]:
    media: Dict[str, Any] = {"encrypt_query_param": eqp}
    if aes_key:
        media["aes_key"] = aes_key
    if full_url:
        media["full_url"] = full_url
    img: Dict[str, Any] = {"media": media}
    if aeskey_hex:
        img["aeskey"] = aeskey_hex
    return {"message_type": 1, "from_user_id": "user-1", "to_user_id": "bot",
            "item_list": [{"type": 2, "image_item": img}]}


def _file_msg(name: str, *, aes_key: str, eqp: str = "EQP-FILE") -> Dict[str, Any]:
    return {"message_type": 1, "from_user_id": "user-1", "to_user_id": "bot",
            "item_list": [{"type": 4, "file_item": {
                "file_name": name,
                "media": {"encrypt_query_param": eqp, "aes_key": aes_key}}}]}


def _video_msg(*, aes_key: str, eqp: str = "EQP-VID") -> Dict[str, Any]:
    return {"message_type": 1, "from_user_id": "user-1", "to_user_id": "bot",
            "item_list": [{"type": 5, "video_item": {
                "media": {"encrypt_query_param": eqp, "aes_key": aes_key}}}]}


def _text_msg(text: str) -> Dict[str, Any]:
    return {"message_type": 1, "from_user_id": "user-1", "to_user_id": "bot",
            "item_list": [{"type": 1, "text_item": {"text": text}}]}


# ---------------------------------------------------------------------------
# 协议层：取引用 / URL / 密钥
# ---------------------------------------------------------------------------

def test_extract_media_refs_reads_image_file_video_and_skips_text_voice():
    msg = {"item_list": [
        {"type": 1, "text_item": {"text": "hi"}},
        {"type": 2, "image_item": {"aeskey": KEY_HEX,
                                   "media": {"encrypt_query_param": "Q1"}}},
        {"type": 3, "voice_item": {"text": "转写", "media": {
            "encrypt_query_param": "Qv", "aes_key": "x"}}},
        {"type": 4, "file_item": {"file_name": "报告.pdf", "media": {
            "encrypt_query_param": "Q2", "aes_key": "k"}}},
        {"type": 5, "video_item": {"media": {"encrypt_query_param": "Q3"}}},
        # 没有可下载地址的 item 应被跳过
        {"type": 2, "image_item": {"media": {}}},
    ]}
    refs = extract_media_refs(msg)
    assert [r.kind for r in refs] == ["image", "file", "video"]
    assert refs[0].label == "[图片]" and refs[0].image_aeskey_hex == KEY_HEX
    assert refs[0].encrypt_query_param == "Q1"
    assert refs[1].file_name == "报告.pdf" and refs[1].aes_key == "k"
    assert refs[2].kind == "video"
    # 语音不在本轮范围（有转写文本兜底）
    assert all(r.kind != "voice" for r in refs)


def test_build_cdn_download_url_matches_official_shape():
    url = build_cdn_download_url("a b&c=d", "https://cdn.example/c2c/")
    assert url == ("https://cdn.example/c2c/download"
                   "?encrypted_query_param=a%20b%26c%3Dd")


def test_parse_aes_key_accepts_raw16_and_hex32():
    assert parse_aes_key(base64.b64encode(KEY).decode()) == KEY
    assert parse_aes_key(base64.b64encode(KEY_HEX.encode()).decode()) == KEY
    with pytest.raises(MediaDownloadError):
        parse_aes_key(base64.b64encode(b"short").decode())


# ---------------------------------------------------------------------------
# 下载 / 解密 / 落盘
# ---------------------------------------------------------------------------

async def test_image_download_decrypts_and_lands_in_attachments(tmp_path):
    ref = extract_media_refs(_image_msg(aeskey_hex=KEY_HEX))[0]
    out = tmp_path / "proj" / "attachments"
    fetcher = _Fetcher(_aes_ecb_encrypt(PNG))

    path = await download_media_to(ref, out, fetcher=fetcher)

    assert path.parent == out and out.is_dir()
    assert path.read_bytes() == PNG                       # 解密正确
    assert path.suffix == ".png"                          # 魔数判后缀
    assert path.name.startswith("weixin-image-")
    assert fetcher.urls[0].endswith("encrypted_query_param=EQP-IMG")


async def test_file_download_keeps_original_name_and_hex_key(tmp_path):
    ref = extract_media_refs(_file_msg(
        "季度报告.pdf", aes_key=base64.b64encode(KEY_HEX.encode()).decode()))[0]
    out = tmp_path / "attachments"
    body = b"%PDF-1.7 fake"
    path = await download_media_to(ref, out, fetcher=_Fetcher(
        _aes_ecb_encrypt(body)))

    assert path.name == "季度报告.pdf" and path.read_bytes() == body


async def test_video_download_uses_full_url_when_present(tmp_path):
    ref = extract_media_refs(_video_msg(
        aes_key=base64.b64encode(KEY).decode(), eqp="ignored"))[0]
    ref.full_url = "https://full.example/v.mp4"
    out = tmp_path / "attachments"
    fetcher = _Fetcher(_aes_ecb_encrypt(b"video-bytes"))
    path = await download_media_to(ref, out, fetcher=fetcher)
    assert fetcher.urls == ["https://full.example/v.mp4"]   # full_url 优先
    assert path.suffix == ".mp4" and path.read_bytes() == b"video-bytes"


async def test_oversize_raises_too_large_and_writes_nothing(tmp_path):
    ref = extract_media_refs(_image_msg(aeskey_hex=KEY_HEX))[0]
    out = tmp_path / "attachments"
    big = b"A" * 300
    with pytest.raises(MediaDownloadError) as exc:
        await download_media_to(ref, out, max_bytes=100,
                                fetcher=_Fetcher(_aes_ecb_encrypt(big)))
    assert exc.value.too_large is True and "上限" in exc.value.reason
    assert not out.exists() or list(out.iterdir()) == []


async def test_fetch_failure_is_a_readable_media_error(tmp_path):
    ref = extract_media_refs(_image_msg(aeskey_hex=KEY_HEX))[0]
    with pytest.raises(MediaDownloadError) as exc:
        await download_media_to(ref, tmp_path / "a",
                                fetcher=_Fetcher(error=RuntimeError("boom")))
    assert "下载失败" in exc.value.reason and "RuntimeError" in exc.value.reason


async def test_non_image_without_key_is_rejected(tmp_path):
    ref = MediaRef(kind="file", item_type=4, label="[文件]",
                   encrypt_query_param="Q")
    with pytest.raises(MediaDownloadError) as exc:
        await download_media_to(ref, tmp_path / "a", fetcher=_Fetcher(b"x"))
    assert "密钥" in exc.value.reason


# ---------------------------------------------------------------------------
# dispatch 集成（真下载函数 + 注入假 CDN，全离线）
# ---------------------------------------------------------------------------

class _FakeCoder:
    def __init__(self) -> None:
        self.seen: List[str] = []

    async def chat(self, text: str, *, voice_mode: bool = False) -> str:
        self.seen.append(text)
        return "ok"


class _FakeProject:
    def __init__(self, work_dir: Path) -> None:
        self.id = "p1"
        self.name = "微信 · test"
        self.work_dir = str(work_dir)
        self.workspace = work_dir
        self.coder = _FakeCoder()
        self.runtime = None
        self.loop_task = None


class _FakeOrch:
    def __init__(self, project: _FakeProject) -> None:
        self._p = project
        self._db = None

    def get_project(self, pid: str):
        return self._p if pid == self._p.id else None

    def create_project(self, name: str, description: str = "") -> _FakeProject:
        return self._p

    def list_projects(self) -> list:
        return [self._p]


async def _store(tmp_path: Path) -> WeixinAccountStore:
    store = WeixinAccountStore(tmp_path / "weixin.db")
    await store.init()
    return store


def _patch_fetcher(monkeypatch, fetcher) -> None:
    """让路由里的真 ``download_media_to`` 用假 CDN（不发任何网络请求）。"""
    real = download_media_to

    async def _patched(ref, dest_dir, **kw):
        return await real(ref, dest_dir, fetcher=fetcher, **kw)

    monkeypatch.setattr(wx, "download_media_to", _patched)


@pytest.fixture(autouse=True)
def _pin_lane_to_coder(monkeypatch):
    """本文件只验「媒体下载 + 折进提示词」，不验车道选择；把车道钉在 Coder。

    ``dispatch`` 现在对带媒体的消息与纯文本一致地过 ``_answer_message`` 路由
    （通用车道带上只读文件工具后能读附件）。车道选择由
    ``test_weixin_lane_routing.py`` 覆盖；这里只断言媒体真的落盘、真的按 Web 同一
    套折进 agent 收到的提示词。为了让这些用例保持**离线**且不依赖是否配了模型，
    把 ``_answer_message`` 钉在 Coder —— 与 ``test_dispatch_plain_text_is_unchanged``
    此前自己打的桩同一个形状。
    """
    async def _coder_only(proj, text, **kwargs):
        return await proj.coder.chat(text)

    monkeypatch.setattr(wx, "_answer_message", _coder_only)



async def test_dispatch_downloads_media_into_project_attachments(tmp_path,
                                                                 monkeypatch):
    work = tmp_path / "proj"
    work.mkdir()
    project = _FakeProject(work)
    store = await _store(tmp_path)
    _patch_fetcher(monkeypatch, _Fetcher(_aes_ecb_encrypt(PNG)))
    dispatch = wx.make_dispatch(_FakeOrch(project), store)

    ref = extract_media_refs(_image_msg(aeskey_hex=KEY_HEX))[0]
    await dispatch("acct-1", "user-1", "[图片]", [ref])

    saved = list((work / "attachments").iterdir())
    assert len(saved) == 1
    assert saved[0].read_bytes() == PNG                    # 真落盘、内容正确

    prompt = project.coder.seen[-1]
    assert "[图片]" in prompt                              # 占位符保留
    assert "[附件" in prompt                               # Web 同一套块
    assert "- attachments/" in prompt
    # 附件块里的路径能被项目的 file 工具按相对路径读到
    rel = prompt.split("- attachments/")[1].split(" ")[0]
    assert (work / "attachments" / rel).is_file()


async def test_dispatch_reports_download_failure_without_dropping_silently(
        tmp_path, monkeypatch):
    work = tmp_path / "proj"
    work.mkdir()
    project = _FakeProject(work)
    store = await _store(tmp_path)
    _patch_fetcher(monkeypatch, _Fetcher(
        error=MediaDownloadError("CDN 返回 HTTP 404")))
    dispatch = wx.make_dispatch(_FakeOrch(project), store)

    ref = extract_media_refs(_image_msg(aeskey_hex=KEY_HEX))[0]
    await dispatch("acct-1", "user-1", "[图片]", [ref])

    prompt = project.coder.seen[-1]
    assert "[微信媒体]" in prompt and "HTTP 404" in prompt   # 可读提示给 agent
    assert "[图片]" in prompt                              # 占位符保留
    assert not (work / "attachments").exists()             # 没有半成品


async def test_dispatch_reports_oversize_and_keeps_placeholder(tmp_path,
                                                               monkeypatch):
    work = tmp_path / "proj"
    work.mkdir()
    project = _FakeProject(work)
    store = await _store(tmp_path)
    _patch_fetcher(monkeypatch, _Fetcher(
        error=MediaDownloadError("文件超过 25 MiB 上限", too_large=True)))
    dispatch = wx.make_dispatch(_FakeOrch(project), store)

    ref = extract_media_refs(_file_msg(
        "大文件.bin", aes_key=base64.b64encode(KEY_HEX.encode()).decode()))[0]
    await dispatch("acct-1", "user-1", "[文件]", [ref])

    prompt = project.coder.seen[-1]
    assert "[微信媒体]" in prompt
    assert "25 MiB" in prompt and "占位符" in prompt
    assert "[文件]" in prompt


async def test_dispatch_plain_text_is_unchanged(tmp_path, monkeypatch):
    """回归：纯文本消息（无媒体）逐字进 agent，不加任何块。"""
    work = tmp_path / "proj"
    work.mkdir()
    project = _FakeProject(work)
    store = await _store(tmp_path)
    # 只验“媒体折叠”这一层：把车道选择固定为 Coder，避免受路由改动/网络影响。
    # （带媒体的路径同理，由上面的 autouse fixture 统一钉住。）
    async def _coder_only(proj, text, **kwargs):
        return await proj.coder.chat(text)

    monkeypatch.setattr(wx, "_answer_message", _coder_only)
    dispatch = wx.make_dispatch(_FakeOrch(project), store)

    await dispatch("acct-1", "user-1", "你好 世界")
    assert project.coder.seen[-1] == "你好 世界"

    # media=None 与 media=[] 都不该改变行为
    await dispatch("acct-1", "user-1", "第二句", None)
    await dispatch("acct-1", "user-1", "第三句", [])
    assert project.coder.seen[-2:] == ["第二句", "第三句"]


async def test_dispatch_unsupported_media_yields_visible_note(tmp_path,
                                                              monkeypatch):
    """未知/不支持的媒体对象 → 走失败可见分支，不崩。"""
    work = tmp_path / "proj"
    work.mkdir()
    project = _FakeProject(work)
    store = await _store(tmp_path)
    dispatch = wx.make_dispatch(_FakeOrch(project), store)

    class _Broken:
        kind = "file"
        label = "[文件]"

    await dispatch("acct-1", "user-1", "[文件]", [_Broken()])
    prompt = project.coder.seen[-1]
    assert "[微信媒体]" in prompt and "下载失败" in prompt


# ---------------------------------------------------------------------------
# channel：媒体引用转交 + 老的三参回调不破坏
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []

    async def send_message(self, msg, *, timeout=None):
        self.sent.append(msg)
        return {"ret": 0}


async def test_channel_forwards_media_refs_to_four_arg_dispatch(tmp_path):
    store = await _store(tmp_path)
    seen: List[tuple] = []

    async def dispatch(account_id, chat_id, text, media=None):
        seen.append((account_id, chat_id, text, media))
        return ""

    channel = WeixinChannel(store, dispatch=dispatch)
    assert channel._dispatch_takes_media is True

    await channel.handle_message("acct", _FakeClient(), _image_msg(aeskey_hex=KEY_HEX))

    assert seen and seen[0][0] == "acct" and seen[0][2] == "[图片]"
    assert [r.kind for r in seen[0][3]] == ["image"]


async def test_channel_keeps_working_with_legacy_three_arg_dispatch(tmp_path):
    store = await _store(tmp_path)
    seen: List[tuple] = []

    async def dispatch(account_id, chat_id, text):
        seen.append((account_id, chat_id, text))
        return f"reply: {text}"

    channel = WeixinChannel(store, dispatch=dispatch)
    assert channel._dispatch_takes_media is False

    client = _FakeClient()
    reply = await channel.handle_message("acct", client, _text_msg("hi"))

    assert reply == "reply: hi"
    assert seen == [("acct", "user-1", "hi")]
    assert client.sent[0]["item_list"][0]["text_item"]["text"] == "reply: hi"


# ---------------------------------------------------------------------------
# 去重 / 只有媒体无正文仍放行
# ---------------------------------------------------------------------------

def test_media_only_message_passes_is_user_message_and_text_is_unchanged():
    msg = _image_msg(aeskey_hex=KEY_HEX)
    # 没有正文，但占位符保证它会被交给 agent（去重逻辑不变）
    assert extract_text(msg) == "[图片]"
    assert is_user_message(msg) is True
    assert extract_text(_video_msg(aes_key="x")) == "[视频]"
    assert is_user_message(_video_msg(aes_key="x")) is True


def test_voice_with_transcription_still_wins_over_media_path():
    msg = {"message_type": 1, "from_user_id": "u", "item_list": [
        {"type": 3, "voice_item": {"text": "语音转写",
                                   "media": {"encrypt_query_param": "Q",
                                             "aes_key": "k"}}}]}
    assert extract_text(msg) == "语音转写"
    assert extract_media_refs(msg) == []          # 语音不下载


def test_safe_media_name_strips_traversal(tmp_path):
    ref = extract_media_refs(_file_msg(
        "../../evil.txt", aes_key=base64.b64encode(KEY_HEX.encode()).decode()))[0]
    assert ref.file_name == "../../evil.txt"        # 原始字段不动
    # 落盘时只留 basename（此处直接验证下载路径的命名）
    from kairos.weixin_ilink import _media_filename
    name = _media_filename(ref, b"data")
    assert name == "evil.txt" and "/" not in name and ".." not in name
