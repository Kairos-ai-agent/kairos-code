"""微信 iLink **出站媒体**（把 agent 生成/引用的文件发给用户）—— 全离线测试。

不联网、不用真微信账号：CDN 上传用一个**假 poster**（注入
``_httpx_post_cdn``），取上传参数用一个**假 client**（注入 ``get_upload_url``）。
覆盖任务要求的每一条：

* **AES-ECB 往返**：``encrypt_aes_ecb`` 与入站 ``decrypt_aes_ecb`` 严格互逆，
  且与本地独立实现的 PKCS7 加密逐字节相同；密文长度 == ``aes_ecb_padded_size``；
* **密钥编码镜像**：出站写进 ``media.aes_key`` 的 base64(hex) 正是入站
  ``parse_aes_key`` 认的编码 —— 用入站那套 ``extract_media_refs`` + 解密读回来；
* **文件消息项字段形状**与 ``kairos/weixin_ilink.py`` 注释里写的一字不差；
* **超限（> 25 MiB）不发**、**围墙外路径不发**、**目录 / 不存在 / 无分隔符不发**；
* **拿不到上传参数 / 上传报错** → 退回纯文本且**文本不丢不重**；
* **无文件时不产生任何额外消息**（与改动前逐字一致）；
* **文件先于文本发出**。

字段名/形状来自官方插件源码（见 ``kairos/weixin_ilink.py`` 各段注释），**不是真
响应样本**。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kairos.weixin_ilink as wx  # noqa: E402
from kairos.weixin_ilink import (  # noqa: E402
    WEIXIN_MAX_OUTBOUND_FILES,
    WEIXIN_MEDIA_MAX_BYTES,
    MediaUploadError,
    UploadMediaType,
    WeixinAccountStore,
    WeixinChannel,
    _candidate_path_tokens,
    _resolve_media_key,
    aes_ecb_padded_size,
    build_cdn_upload_url,
    build_file_media,
    build_file_message,
    build_file_message_item,
    decrypt_aes_ecb,
    encrypt_aes_ecb,
    extract_media_refs,
    parse_aes_key,
    resolve_sendable_files,
    upload_file_to_cdn,
)

KEY = bytes(range(16))              # 固定 16 字节 AES-128 key（测试用，非线上密钥）
KEY_HEX = KEY.hex()                 # "000102...0f"


# --------------------------------------------------------------------------- 参考实现
# 与 tests/test_weixin_media_inbound.py 同款：手工 PKCS7 + cryptography，
# 用来和被测的 encrypt_aes_ecb 对扣（两条独立写出的补齐逻辑应逐字节一致）。

def _ref_encrypt(plaintext: bytes, key: bytes = KEY) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    pad = 16 - (len(plaintext) % 16)
    data = plaintext + bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(data) + enc.finalize()


# =========================================================== A. AES-ECB 往返

def test_encrypt_decrypt_roundtrip_and_matches_reference():
    key = bytes(range(16))
    samples = [b"", b"a", b"0123456789abcde", b"0123456789abcdef",   # 0/1/15/16 字节
               b"x" * 17, b"%PDF-1.7 " + "报告内容".encode("utf-8") * 50,
               bytes(range(200))]
    for pt in samples:
        ct = encrypt_aes_ecb(pt, key)
        # 与本地参考实现逐字节相同（补齐逻辑一致）
        assert ct == _ref_encrypt(pt, key), len(pt)
        # 长度 == 官方 aesEcbPaddedSize（注意正好 16 倍数时还要加一整块）
        assert len(ct) == aes_ecb_padded_size(len(pt))
        assert len(ct) % 16 == 0
        # 与**入站**解密对扣：encrypt → decrypt(入站函数) → 去掉填充 == 原文
        assert wx._pkcs7_unpad(decrypt_aes_ecb(ct, key)) == pt


def test_aes_ecb_padded_size_matches_official_formula():
    # 官方 cdn/aes-ecb.ts: ceil((n + 1) / 16) * 16
    for n in (0, 1, 15, 16, 17, 31, 32, 100):
        expected = ((n + 1 + 15) // 16) * 16
        assert aes_ecb_padded_size(n) == expected
    assert aes_ecb_padded_size(16) == 32      # 16 倍数也要加一整块


def test_encrypt_rejects_wrong_key_length():
    with pytest.raises(MediaUploadError):
        encrypt_aes_ecb(b"data", b"short-key")


# ================================================== B. 密钥编码镜像（base64(hex)）

def test_file_media_key_is_base64_of_hexstring_and_inbound_can_parse_it():
    media = build_file_media("EQP-1", KEY_HEX)
    # 官方：Buffer.from(hex).toString("base64")
    assert media["aes_key"] == base64.b64encode(KEY_HEX.encode()).decode()
    assert media["encrypt_type"] == 1
    # 入站那套解析器认这个编码 → 拿到原始 16 字节 key
    assert parse_aes_key(media["aes_key"]) == KEY


def test_outbound_file_item_feeds_inbound_extract_and_decrypt():
    """镜像证明：出站构造的 file 项，用**入站**那套读出来并成功解密。"""
    plaintext = b"%PDF-1.7 fake report body"
    ct = encrypt_aes_ecb(plaintext, KEY)          # 上传时加密
    item = build_file_message_item(
        "季度报告.pdf", plaintext_size=len(plaintext),
        encrypt_query_param="EQP-OUT", aeskey_hex=KEY_HEX)

    # 出站项包成一条消息 → 入站解析
    msg = build_file_message("user-1", item, context_token="CTX")
    refs = extract_media_refs(msg)
    assert len(refs) == 1
    ref = refs[0]
    assert ref.kind == "file" and ref.label == "[文件]"
    assert ref.file_name == "季度报告.pdf"
    assert ref.encrypt_query_param == "EQP-OUT"

    # 入站 key 解析 → 解密出站密文 → 得到原文（两边同一套 AES 方案）
    key = _resolve_media_key(ref)
    assert key == KEY
    assert wx._pkcs7_unpad(decrypt_aes_ecb(ct, key)) == plaintext


# ========================================================= C. 文件消息项字段形状

def test_file_message_item_field_shape_is_exactly_as_documented():
    item = build_file_message_item(
        "report.md", plaintext_size=1234,
        encrypt_query_param="PARAM", aeskey_hex=KEY_HEX)
    assert item == {
        "type": 4,                                  # MessageItemType.FILE
        "file_item": {
            "media": {
                "encrypt_query_param": "PARAM",
                "aes_key": base64.b64encode(KEY_HEX.encode()).decode(),
                "encrypt_type": 1,
            },
            "file_name": "report.md",
            "len": "1234",                          # 字符串
        },
    }


def test_build_file_message_wraps_single_item_like_text_message():
    item = build_file_message_item("a.txt", plaintext_size=1,
                                   encrypt_query_param="P", aeskey_hex=KEY_HEX)
    msg = build_file_message("user-9", item, context_token="CTX", run_id="R1",
                             client_id="cid-1")
    assert msg["from_user_id"] == "" and msg["to_user_id"] == "user-9"
    assert msg["message_type"] == 2 and msg["message_state"] == 2
    assert msg["client_id"] == "cid-1" and msg["context_token"] == "CTX"
    assert msg["run_id"] == "R1"
    assert msg["item_list"] == [item]               # 一条消息只装一个项

    # 不带可选字段时不应出现 context_token / run_id 键
    plain = build_file_message("u", item)
    assert "context_token" not in plain and "run_id" not in plain


def test_build_cdn_upload_url_matches_official_shape():
    url = build_cdn_upload_url("a b&c", "fk/1", "https://cdn.example/c2c/")
    assert url == ("https://cdn.example/c2c/upload"
                   "?encrypted_query_param=a%20b%26c&filekey=fk%2F1")


# ========================================================= D. resolve_sendable_files

def _root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    return root


def test_resolve_picks_existing_file_named_in_reply(tmp_path):
    root = _root(tmp_path)
    out = root / "outputs"
    out.mkdir()
    (out / "报告.md").write_text("hi", encoding="utf-8")

    picked = resolve_sendable_files("已生成 outputs/报告.md，请查收。", root)
    assert [p.name for p in picked] == ["报告.md"]
    assert picked[0].is_file()


def test_resolve_picks_absolute_path_inside_root(tmp_path):
    root = _root(tmp_path)
    f = root / "a.txt"
    f.write_text("x", encoding="utf-8")
    picked = resolve_sendable_files(f"文件在 {f}", root)
    assert picked == [f]


def test_resolve_skips_path_outside_the_wall(tmp_path, monkeypatch):
    # 保证不是全盘访问：把同一套围墙判定里读的 is_full_access 钉成 False
    monkeypatch.delenv("KAIROS_FULL_ACCESS", raising=False)
    monkeypatch.setattr("kairos.tools.base.is_full_access", lambda: False)

    root = _root(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (root / "inside.txt").write_text("ok", encoding="utf-8")

    picked = resolve_sendable_files(
        f"看 {outside} 和 inside.txt 这两个", root)
    assert [p.name for p in picked] == ["inside.txt"]      # 围墙外的被跳过


def test_resolve_skips_oversize_and_missing_and_dirs(tmp_path):
    root = _root(tmp_path)
    (root / "big.bin").write_bytes(b"A" * 200)
    (root / "small.bin").write_bytes(b"B" * 50)
    (root / "adir").mkdir()

    picked = resolve_sendable_files(
        "big.bin small.bin nope.txt adir/x.txt", root, max_bytes=100)
    assert [p.name for p in picked] == ["small.bin"]       # 超限 / 缺失 / 目录都不发


def test_resolve_refuses_real_25mib_file_with_default_limit(tmp_path):
    """超 25 MiB（用稀疏文件，不真写 25 MiB 数据）→ 不发。"""
    assert WEIXIN_MEDIA_MAX_BYTES == 26214400
    root = _root(tmp_path)
    big = root / "huge.iso"
    with open(big, "wb") as fh:
        fh.truncate(WEIXIN_MEDIA_MAX_BYTES + 1)             # 稀疏：只占元数据
    assert big.stat().st_size == WEIXIN_MEDIA_MAX_BYTES + 1
    assert resolve_sendable_files("huge.iso", root) == []


def test_resolve_caps_at_max_files_and_dedupes(tmp_path):
    root = _root(tmp_path)
    for i in range(6):
        (root / f"f{i}.txt").write_text("x", encoding="utf-8")
    text = " ".join(f"f{i}.txt" for i in range(6)) + " f0.txt"   # 末尾重复
    picked = resolve_sendable_files(text, root)
    assert len(picked) == WEIXIN_MAX_OUTBOUND_FILES == 3
    assert [p.name for p in picked] == ["f0.txt", "f1.txt", "f2.txt"]
    assert len(set(map(str, picked))) == 3               # 去重


def test_candidate_tokens_require_a_dotted_last_segment():
    # 没有后缀的裸词 / 普通中文句子都被排除；带 .后缀 的（含裸文件名）算候选
    assert _candidate_path_tokens("这是一句话，没有路径。") == []
    assert _candidate_path_tokens("please read the code") == []
    assert _candidate_path_tokens("see file.py") == ["file.py"]
    assert _candidate_path_tokens("见 outputs/a.md 与 C:\\x\\b.txt") == [
        "outputs/a.md", "C:\\x\\b.txt"]


# ========================================================= E. 上传链（离线）

class _UploadClient:
    """假 ILinkClient：记录 get_upload_url 入参、记录发出的消息。"""

    def __init__(self, *, upload_param: str = "UP-PARAM",
                 upload_error: Optional[Exception] = None) -> None:
        self.sent: List[Dict[str, Any]] = []
        self.upload_calls: List[Dict[str, Any]] = []
        self._upload_param = upload_param
        self._upload_error = upload_error

    async def get_upload_url(self, **kwargs: Any) -> Dict[str, Any]:
        self.upload_calls.append(dict(kwargs))
        if self._upload_error is not None:
            raise self._upload_error
        return {"upload_param": self._upload_param}

    async def send_message(self, msg: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        self.sent.append(msg)
        return {"ret": 0, "message_id": "srv"}


class _Poster:
    """假 CDN 上传：记录 (url, ciphertext)；可注入错误 / 连续失败。"""

    def __init__(self, *, param: str = "DL-PARAM",
                 error: Optional[Exception] = None,
                 fail_times: int = 0) -> None:
        self.param = param
        self.error = error
        self.fail_times = fail_times
        self.calls: List[tuple] = []

    async def __call__(self, url: str, ciphertext: bytes) -> str:
        self.calls.append((url, ciphertext))
        if self.error is not None:
            raise self.error
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("transient")
        return self.param


def test_upload_file_to_cdn_full_chain_fields(tmp_path):
    root = _root(tmp_path)
    body = b"%PDF-1.7 real-ish report" * 3
    f = root / "report.pdf"
    f.write_bytes(body)

    client = _UploadClient(upload_param="UP")
    poster = _Poster(param="DL")
    upload = asyncio.run(upload_file_to_cdn(
        f, client=client, to_user_id="user-1", poster=poster))

    # 取上传参数的请求字段（官方 GetUploadUrlReq 形状）
    call = client.upload_calls[0]
    assert call["media_type"] == UploadMediaType["FILE"] == 3
    assert call["to_user_id"] == "user-1"
    assert call["rawsize"] == len(body)
    assert call["rawfilemd5"] == hashlib.md5(body).hexdigest()
    assert call["filesize"] == aes_ecb_padded_size(len(body))
    assert call["no_need_thumb"] is True
    assert len(call["aeskey"]) == 32 and call["filekey"]

    # 上传 URL 形状 + 密文用「向服务端声明的那个 aeskey」加密
    url, ciphertext = poster.calls[0]
    assert url == build_cdn_upload_url("UP", call["filekey"])
    assert len(ciphertext) == upload.file_size_ciphertext
    assert wx._pkcs7_unpad(
        decrypt_aes_ecb(ciphertext, bytes.fromhex(call["aeskey"]))) == body

    assert upload.download_encrypted_query_param == "DL"
    assert upload.file_size == len(body)
    assert upload.aeskey_hex == call["aeskey"]

    # 构造文件项 → 与注释里的形状一致
    item = build_file_message_item(
        f.name, plaintext_size=upload.file_size,
        encrypt_query_param=upload.download_encrypted_query_param,
        aeskey_hex=upload.aeskey_hex)
    assert item["type"] == 4
    assert item["file_item"]["file_name"] == "report.pdf"
    assert item["file_item"]["len"] == str(len(body))


def test_upload_prefers_upload_full_url(tmp_path):
    root = _root(tmp_path)
    f = root / "a.bin"
    f.write_bytes(b"data")

    class _FullUrlClient(_UploadClient):
        async def get_upload_url(self, **kwargs):
            self.upload_calls.append(dict(kwargs))
            return {"upload_full_url": "  https://cdn.example/up?x=1  "}

    poster = _Poster()
    asyncio.run(upload_file_to_cdn(f, client=_FullUrlClient(),
                                   to_user_id="u", poster=poster))
    assert poster.calls[0][0] == "https://cdn.example/up?x=1"


def test_upload_oversize_is_refused_before_any_post(tmp_path):
    root = _root(tmp_path)
    f = root / "big.bin"
    f.write_bytes(b"A" * 200)
    poster = _Poster()
    with pytest.raises(MediaUploadError) as exc:
        asyncio.run(upload_file_to_cdn(
            f, client=_UploadClient(), to_user_id="u", max_bytes=100,
            poster=poster))
    assert exc.value.too_large is True and "上限" in exc.value.reason
    assert poster.calls == []                            # 根本没上传


def test_upload_without_upload_params_raises(tmp_path):
    root = _root(tmp_path)
    f = root / "a.txt"
    f.write_bytes(b"x")

    class _NoParams(_UploadClient):
        async def get_upload_url(self, **kwargs):
            self.upload_calls.append(dict(kwargs))
            return {}                                     # 既无 param 也无 full_url

    with pytest.raises(MediaUploadError) as exc:
        asyncio.run(upload_file_to_cdn(f, client=_NoParams(), to_user_id="u",
                                       poster=_Poster()))
    assert "上传地址" in exc.value.reason


def test_upload_4xx_client_error_is_not_retried(tmp_path):
    root = _root(tmp_path)
    f = root / "a.txt"
    f.write_bytes(b"x")
    poster = _Poster(error=MediaUploadError("CDN 上传被拒 HTTP 403",
                                            client_error=True))
    with pytest.raises(MediaUploadError):
        asyncio.run(upload_file_to_cdn(f, client=_UploadClient(),
                                       to_user_id="u", poster=poster))
    assert len(poster.calls) == 1                        # 明确被拒 → 不重试


def test_upload_retries_server_errors_then_succeeds(tmp_path):
    root = _root(tmp_path)
    f = root / "a.txt"
    f.write_bytes(b"x")
    poster = _Poster(fail_times=2)                       # 前两次失败，第三次成功
    upload = asyncio.run(upload_file_to_cdn(f, client=_UploadClient(),
                                            to_user_id="u", poster=poster))
    assert len(poster.calls) == 3
    assert upload.download_encrypted_query_param == "DL-PARAM"


def test_upload_client_without_get_upload_url_raises(tmp_path):
    root = _root(tmp_path)
    f = root / "a.txt"
    f.write_bytes(b"x")

    class _NoMethod:
        async def send_message(self, msg, **kwargs):
            return {"ret": 0}

    with pytest.raises(MediaUploadError) as exc:
        asyncio.run(upload_file_to_cdn(f, client=_NoMethod(), to_user_id="u",
                                       poster=_Poster()))
    assert "上传参数" in exc.value.reason


def test_ilink_client_get_upload_url_request_shape(monkeypatch):
    from kairos.weixin_ilink import ILinkClient

    captured: Dict[str, Any] = {}

    async def _fake_request(self, method, endpoint, *, body=None, timeout,
                            with_auth):
        captured.update(method=method, endpoint=endpoint, body=body,
                        with_auth=with_auth)
        return '{"upload_param": "UP"}'

    monkeypatch.setattr(ILinkClient, "_request", _fake_request)
    resp = asyncio.run(ILinkClient(token="T-secret").get_upload_url(
        filekey="FK", media_type=3, to_user_id="u", rawsize=5,
        rawfilemd5="m", filesize=16, aeskey="ab"))

    assert captured["method"] == "POST"
    assert captured["endpoint"] == "ilink/bot/getuploadurl"
    assert captured["with_auth"] is True
    body = captured["body"]
    assert set(body) == {"filekey", "media_type", "to_user_id", "rawsize",
                         "rawfilemd5", "filesize", "no_need_thumb", "aeskey",
                         "base_info"}
    assert body["media_type"] == 3 and body["no_need_thumb"] is True
    assert body["base_info"] == {"channel_version": "2.4.9", "bot_agent": "Kairos"}
    assert resp == {"upload_param": "UP"}
    # token 是密钥：请求体里绝不含它（只在 Authorization 头里）
    assert "T-secret" not in str(body)


# ================================================== F. handle_message 集成（离线）

def _user_msg(text: str) -> Dict[str, Any]:
    return {"message_type": 1, "from_user_id": "user-1", "to_user_id": "bot",
            "context_token": "CTX",
            "item_list": [{"type": 1, "text_item": {"text": text}}]}


def _item_types(msg: Dict[str, Any]) -> List[int]:
    return [it.get("type") for it in (msg.get("item_list") or [])]


async def _store(tmp_path: Path) -> WeixinAccountStore:
    store = WeixinAccountStore(tmp_path / "weixin.db")
    await store.init()
    await store.upsert_account("acct", token="T", status="online")
    return store


def _run_handle(store, *, reply, root, client, monkeypatch, poster=None):
    """同步驱动一次 ``handle_message``（内部自建事件循环，不嵌套）。"""

    async def _main():
        async def dispatch(a, c, t):
            return reply

        resolver = None if root is None else (lambda a, c: str(root))
        channel = WeixinChannel(store, dispatch=dispatch,
                                workspace_resolver=resolver)
        if poster is not None:
            monkeypatch.setattr(wx, "_httpx_post_cdn", poster)
        return await channel.handle_message("acct", client, _user_msg("hi"))

    return asyncio.run(_main())


def _go(tmp_path, reply, root, client, monkeypatch, poster=None):
    """建一个临时 store 再跑一次 ``handle_message``（全离线）。"""
    store = asyncio.run(_store(tmp_path))
    return _run_handle(store, reply=reply, root=root, client=client,
                       monkeypatch=monkeypatch, poster=poster)


def test_handle_message_sends_file_before_text(tmp_path, monkeypatch):
    root = _root(tmp_path)
    body = b"quarterly numbers"
    (root / "报告.md").write_bytes(body)
    reply = "已生成报告.md，请查收。"

    client = _UploadClient()
    poster = _Poster(param="DL-PARAM")
    _go(tmp_path, reply, root, client, monkeypatch, poster)

    # 顺序：第一条是文件（type=4），之后才是文本（type=1）
    assert _item_types(client.sent[0]) == [4]
    assert _item_types(client.sent[-1]) == [1]
    assert client.sent[-1]["item_list"][0]["text_item"]["text"] == reply
    assert client.sent[-1]["context_token"] == "CTX"

    # 文件项内容正确：名字 + 可被入站读回 + 能解密
    item = client.sent[0]["item_list"][0]
    assert item["file_item"]["file_name"] == "报告.md"
    assert item["file_item"]["len"] == str(len(body))
    ref = extract_media_refs(client.sent[0])[0]
    assert ref.encrypt_query_param == "DL-PARAM"
    key = _resolve_media_key(ref)
    assert wx._pkcs7_unpad(
        decrypt_aes_ecb(poster.calls[0][1], key)) == body


def test_handle_message_no_file_means_no_extra_message(tmp_path, monkeypatch):
    """回复里没有（合格的）文件路径 → 只发一条文本，与改动前逐字一致。"""
    root = _root(tmp_path)
    reply = "好的，收到，没有问题。"

    # 带回解析器 + 假上传 client：若误触发文件，client.upload_calls 会非空
    client = _UploadClient()
    _go(tmp_path, reply, root, client, monkeypatch, _Poster())

    assert client.upload_calls == []                     # 根本没尝试上传
    assert len(client.sent) == 1
    assert client.sent[0]["item_list"] == [
        {"type": 1, "text_item": {"text": reply}}]

    # 与「不带解析器」的基线逐字一致
    client2 = _UploadClient()
    _go(tmp_path, reply, None, client2, monkeypatch, None)
    assert client2.sent[0]["item_list"] == client.sent[0]["item_list"]


def test_handle_message_upload_error_falls_back_to_text_once(tmp_path, monkeypatch):
    """拿不到上传参数 / 上传报错 → 退纯文本，文本**不丢不重**（只发一次、逐字）。"""
    root = _root(tmp_path)
    (root / "报告.md").write_bytes(b"data")
    reply = "已生成报告.md，请查收。"

    client = _UploadClient(upload_error=RuntimeError("boom-getuploadurl"))
    _go(tmp_path, reply, root, client, monkeypatch, _Poster())

    assert client.sent, "文本回复被吞掉了"
    assert len(client.sent) == 1                          # 只一条，没重复
    assert client.sent[0]["item_list"][0]["text_item"]["text"] == reply


def test_handle_message_upload_post_error_falls_back_to_text_once(tmp_path,
                                                                  monkeypatch):
    """取到参数但 CDN POST 失败 → 同样退纯文本，文本不丢不重。"""
    root = _root(tmp_path)
    (root / "报告.md").write_bytes(b"data")
    reply = "已生成报告.md，请查收。"

    client = _UploadClient()
    poster = _Poster(error=MediaUploadError("CDN 上传被拒 HTTP 403",
                                           client_error=True))
    _go(tmp_path, reply, root, client, monkeypatch, poster)

    assert len(client.sent) == 1
    assert client.sent[0]["item_list"][0]["text_item"]["text"] == reply


def test_handle_message_out_of_wall_file_is_not_sent(tmp_path, monkeypatch):
    monkeypatch.delenv("KAIROS_FULL_ACCESS", raising=False)
    monkeypatch.setattr("kairos.tools.base.is_full_access", lambda: False)
    root = _root(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"secret")
    reply = f"你的文件在 {outside}"

    client = _UploadClient()
    _go(tmp_path, reply, root, client, monkeypatch, _Poster())

    assert client.upload_calls == []                      # 围墙外 → 没上传
    assert len(client.sent) == 1                          # 只剩文本
    assert client.sent[0]["item_list"][0]["text_item"]["text"] == reply


def test_handle_message_long_reply_chunks_with_files_first(tmp_path, monkeypatch):
    """长回复：文件先发，其余分段照旧；文本拼接逐字等于原文、条数 ≤3。"""
    root = _root(tmp_path)
    (root / "报告.md").write_bytes(b"data")
    reply = "已生成报告.md。\n\n" + ("补充说明" * 100)

    client = _UploadClient()
    _go(tmp_path, reply, root, client, monkeypatch, _Poster())

    assert _item_types(client.sent[0]) == [4]             # 文件第一条
    text_msgs = [m for m in client.sent if _item_types(m) == [1]]
    assert 1 <= len(text_msgs) <= 3                       # 分段上限不被突破
    joined = "".join(
        m["item_list"][0]["text_item"]["text"] for m in text_msgs)
    assert joined == reply                                # 逐字


def test_handle_message_resolver_failure_does_not_break_text(tmp_path,
                                                              monkeypatch):
    """解析器抛异常 → 不发文件，文本照发（不丢）。"""
    root = _root(tmp_path)
    (root / "报告.md").write_bytes(b"data")
    reply = "已生成报告.md"

    async def dispatch(a, c, t):
        return reply

    def _boom(a, c):
        raise RuntimeError("resolver down")

    store = asyncio.run(_store(tmp_path))
    channel = WeixinChannel(store, dispatch=dispatch, workspace_resolver=_boom)
    client = _UploadClient()
    monkeypatch.setattr(wx, "_httpx_post_cdn", _Poster())
    asyncio.run(channel.handle_message("acct", client, _user_msg("hi")))

    assert client.upload_calls == []
    assert len(client.sent) == 1
    assert client.sent[0]["item_list"][0]["text_item"]["text"] == reply


def test_handle_message_client_without_upload_support_still_sends_text(
        tmp_path, monkeypatch):
    """解析器有根、文件也在，但 client 不支持上传 → 只发文本（不丢不重）。"""
    root = _root(tmp_path)
    (root / "报告.md").write_bytes(b"data")
    reply = "已生成报告.md，请查收。"

    class _NoUpload:                      # 老式 fake client：没有 get_upload_url
        def __init__(self) -> None:
            self.sent: List[Dict[str, Any]] = []

        async def send_message(self, msg, **kwargs):
            self.sent.append(msg)
            return {"ret": 0}

    client = _NoUpload()
    _go(tmp_path, reply, root, client, monkeypatch, _Poster())
    assert len(client.sent) == 1
    assert client.sent[0]["item_list"][0]["text_item"]["text"] == reply


# ================================================ G. 路由接线：workspace_resolver

class _ResolverProject:
    def __init__(self, root: Path) -> None:
        self.id = "p1"
        self.work_dir = str(root)
        self.workspace = root


class _ResolverOrch:
    def __init__(self, project) -> None:
        self._project = project

    def get_project(self, pid: str):
        return self._project if pid == self._project.id else None


def test_make_workspace_resolver_resolves_bound_chat_only(tmp_path):
    """路由注入的解析器：只对**已绑定**的会话给出项目根，未绑定返回 None。"""
    from api.routes.weixin import make_workspace_resolver

    async def go():
        store = await _store(tmp_path)
        root = _root(tmp_path)
        project = _ResolverProject(root)
        resolve = make_workspace_resolver(_ResolverOrch(project), store)

        assert await resolve("acct", "nobody") is None      # 未绑定 → 不发文件
        await store.bind("acct", "user-1", project.id)
        got = await resolve("acct", "user-1")
        assert got is not None
        assert Path(got) == root.resolve()                 # 已 resolve 的项目根

    asyncio.run(go())


def _ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "outputs").mkdir(parents=True)
    (root / "data").mkdir()
    (root / ".git").mkdir()
    return root


@pytest.mark.parametrize(
    "rel, why",
    [
        (".env", "环境文件"),
        ("data/settings.json", "配置里就是密钥"),
        ("id_ed25519", "私钥文件名"),
        ("key.pem", "私钥后缀"),
        (".git/config", "git 目录里的东西"),
        ("credentials.json", "名字就是凭据"),
        ("notes.token.md", "文件名含 token"),
    ],
)
def test_credential_shaped_files_are_never_auto_sent(tmp_path, rel, why):
    """触发规则是启发式，会把模型只是**提及**的文件也选中。

    没有这道闸，一句「你正在看的 data/settings.json」就会把带密钥的文件加密上传到
    第三方 CDN —— 那是凭据外泄，不是 UX 问题。所以这些形状一律不发（文本回复照旧
    会告诉用户文件在哪）。
    """
    root = _ws(tmp_path)
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    content = "x = 1\n"
    if rel.endswith(".pem"):
        content = "-----BEGIN PRIVATE KEY-----\nzzz\n-----END PRIVATE KEY-----\n"
    if rel == ".env":
        content = "DEEPSEEK_API_KEY=sk-abcdefghijklmnopqrstuvwxyz\n"
    if rel == "data/settings.json":
        content = '{"apiKey": "sk-abcdefghijklmnopqrstuvwxyz"}'
    target.write_text(content, encoding="utf-8")

    assert wx._refuse_to_send(target) is True, f"{rel} 应该被拒（{why}）"
    picked = wx.resolve_sendable_files(f"已生成 {rel}，请查收。", root)
    assert target.resolve() not in [p.resolve() for p in picked], f"{rel} 被误发"


def test_a_file_whose_contents_hold_a_credential_is_not_sent(tmp_path):
    """名字无害、内容有密钥 —— 靠内容嗅探挡住（复用 sentinel 的脱敏规则）。"""
    root = _ws(tmp_path)
    leak = root / "report.md"
    leak.write_text("结论：通过。配置里那行是 sk-abcdefghijklmnopqrstuvwxyz\n", encoding="utf-8")
    assert wx._refuse_to_send(leak) is True
    assert wx.resolve_sendable_files("见 report.md", root) == []


def test_real_deliverables_are_still_sent(tmp_path):
    """别把功能一起挡死：正常交付物必须照旧发。"""
    root = _ws(tmp_path)
    (root / "outputs" / "report.md").write_text("# 正常报告\n\n结论：通过。\n", encoding="utf-8")
    (root / "notes.md").write_text("一些笔记。\n", encoding="utf-8")
    assert wx._refuse_to_send(root / "outputs" / "report.md") is False
    picked = [
        p.resolve()
        for p in wx.resolve_sendable_files(
            "已生成 outputs/report.md，另外参考 data/settings.json、.env 和 notes.md。", root)
    ]
    assert (root / "outputs" / "report.md").resolve() in picked
    assert (root / "notes.md").resolve() in picked
    assert all(not wx._refuse_to_send(p) for p in picked)
    assert len(picked) == 2, "只该选中那两个安全的"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
