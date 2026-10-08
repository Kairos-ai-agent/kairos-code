"""微信（ClawBot / iLink）回复的**分段渐进投递**（全部离线）。

先读清楚：本项目当前拿到的回复就是一个**完整字符串**（``run_chat_reply`` /
``coder.chat`` 都返回 ``str``，不是 token 流 —— 见 ``kairos/weixin_ilink.py``
顶部「分段渐进投递」注释）。所以这里测的是**把最终文本按自然边界切成 ≤N 段、
分几条消息先后发出**这件事，而**不是** token 级流式。

覆盖（对照任务要求逐条）：

* 短回复 → **一条**且内容**逐字相同**；
* 长回复 → 条数 **≤ N** 且**拼接后逐字等于原文**（不重复、不丢字）；
* 中途发送失败 → **剩余部分仍被完整投递**（重投成功），或**明确报错**
  （``ok=False`` + ``failed_at`` 边界），**不允许静默截断**；
* **不切进代码块、不在字中间切**（含 ```` ``` ```` 代码块的长文本为样本）；
* 纯文本单条路径（短回复）与原来**完全一致**；``send_text``（审批推送 / 测试
  发送）仍是单条。

不连网、不需要真实账号：出站用一个只记账的假 client，失败由脚本注入。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import pytest

from kairos.weixin_ilink import (
    WEIXIN_STREAM_ENV,
    WEIXIN_STREAM_MAX_CHUNKS_HARD_LIMIT,
    WeixinAccountStore,
    WeixinChannel,
    _scan_cut_positions,
    split_reply_for_delivery,
    stream_max_chunks,
)


# --------------------------------------------------------------------- fakes

class _FakeClient:
    """只记账的假出站 client；``fail_calls`` 里的第 N 次调用（1-based）抛错。"""

    def __init__(self, *, fail_calls: Tuple[int, ...] = ()) -> None:
        self.sent: List[str] = []              # 成功发出的每段正文
        self.client_ids: List[str] = []        # 每次「尝试」用的 client_id（含失败）
        self.calls = 0
        self.fail_calls = set(fail_calls)

    async def send_message(self, msg: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        self.calls += 1
        self.client_ids.append(msg.get("client_id"))
        if self.calls in self.fail_calls:
            raise RuntimeError(f"boom-{self.calls}")
        item = (msg.get("item_list") or [{}])[0]
        self.sent.append(item.get("text_item", {}).get("text", ""))
        return {"ret": 0, "message_id": f"srv-{self.calls}"}


def _channel() -> WeixinChannel:
    # deliver_reply 不用 store，构造一个不带 dispatch 的 channel 即可。
    return WeixinChannel(None)


def _user_msg(text: str) -> Dict[str, Any]:
    return {"message_type": 1, "from_user_id": "user-1",
            "item_list": [{"type": 1, "text_item": {"text": text}}]}


def _long_reply() -> str:
    """一段 1000+ 字、含空行/句末自然边界的长回复。"""
    return "\n\n".join(
        f"第{i}段。这是第{i}段的内容，用来把回复撑长。" + "补充说明" * 40
        for i in range(1, 7))


# --------------------------------------------------------------- 短回复 → 一条

def test_short_reply_is_a_single_message_verbatim():
    client = _FakeClient()
    text = "好的，收到。"
    report = asyncio.run(_channel().deliver_reply(client, "u", text))
    assert client.sent == [text]              # 逐字
    assert report.ok and report.sent_chunks == 1 and report.total_chunks == 1
    assert report.delivered_text == text


def test_empty_reply_sends_nothing():
    client = _FakeClient()
    report = asyncio.run(_channel().deliver_reply(client, "u", ""))
    assert client.sent == []
    assert report.ok and report.total_chunks == 0


def test_short_threshold_is_inclusive():
    from kairos.weixin_ilink import WEIXIN_STREAM_SHORT_MAX_CHARS
    # 恰好等于阈值 → 单条；超过阈值且存在自然边界 → 才拆
    at_limit = "x" * WEIXIN_STREAM_SHORT_MAX_CHARS
    assert split_reply_for_delivery(at_limit, max_chunks=3) == [at_limit]
    over = ("y" * 80 + "\n\n") * 8          # 656 字符、含空行边界
    chunks = split_reply_for_delivery(over, max_chunks=3)
    assert len(chunks) >= 2 and "".join(chunks) == over


# ------------------------------------------------- 长回复 → ≤N 条，拼接逐字等

def test_long_reply_chunks_at_most_n_and_joins_verbatim():
    text = _long_reply()
    client = _FakeClient()
    report = asyncio.run(_channel().deliver_reply(client, "u", text, max_chunks=3))
    assert report.total_chunks <= 3
    assert "".join(client.sent) == text          # 逐字、不重复、不丢字
    assert report.delivered_text == text
    assert report.ok and report.failed_at is None


def test_split_is_verbatim_for_many_shapes():
    samples = [
        _long_reply(),
        "单行很长很长" + "的" * 900,                       # 无换行，只有中文句末? 无
        ("句子一。句子二。句子三。" * 60),                  # 全中文句末
        "para one here.\n\npara two here.\n\n" + "line\n" * 200,
        "混合 mixed 内容。hello world. " * 80,
    ]
    for s in samples:
        chunks = split_reply_for_delivery(s, max_chunks=3)
        assert "".join(chunks) == s, repr(s[:40])
        assert 1 <= len(chunks) <= 3


# --------------------------------------------------------- 代码块 / 字中间保护

def test_code_block_is_never_cut_inside():
    text = ("前言" + "甲" * 250 + "\n\n```python\n" + "print(1)\n" * 40
            + "```\n\n结语" + "乙" * 250)
    fence_start = text.index("```python")
    fence_end = text.index("```", fence_start + 3) + 3

    cuts = [pos for pos, _rank in _scan_cut_positions(text)]
    inside = [p for p in cuts if fence_start < p < fence_end]
    assert inside == [], f"切点落进了代码块内部: {inside}"

    chunks = split_reply_for_delivery(text, max_chunks=3)
    assert "".join(chunks) == text
    # 每个分块的 ``` 数必须是偶数：说明没有哪一段只拿到半个围栏。
    assert all(c.count("```") % 2 == 0 for c in chunks)


def test_decimal_and_dotted_tokens_are_not_split_mid_word():
    # 3.14 / file.py 这类「点」不是句末，不能在其后切
    text = "数字 3.14 很重要，" * 40 + "\n\n" + "文件 file.py 看一下，" * 40
    chunks = split_reply_for_delivery(text, max_chunks=3)
    assert "".join(chunks) == text
    for c in chunks[:-1]:
        assert not c.endswith("3.") and not c.endswith("file.")
        # 切点后不该紧接着一个纯字母/数字（会切成半截词）
        assert not (c and c[-1] == "." and c[-2:-1].isdigit())


def test_no_natural_boundary_means_no_split():
    blob = "x" * 1000                          # 找不到任何自然边界
    assert split_reply_for_delivery(blob, max_chunks=3) == [blob]


# --------------------------------------------------------------- env 可调 / 硬上限

def test_env_can_turn_off_and_tune_chunk_count(monkeypatch):
    text = _long_reply()

    monkeypatch.setenv(WEIXIN_STREAM_ENV, "1")
    assert stream_max_chunks() == 1
    assert split_reply_for_delivery(text) == [text]           # 关闭分段 → 单条

    monkeypatch.setenv(WEIXIN_STREAM_ENV, "2")
    assert stream_max_chunks() == 2
    assert len(split_reply_for_delivery(text)) <= 2

    monkeypatch.setenv(WEIXIN_STREAM_ENV, "999")              # 夹到硬上限
    assert stream_max_chunks() == WEIXIN_STREAM_MAX_CHUNKS_HARD_LIMIT

    monkeypatch.setenv(WEIXIN_STREAM_ENV, "abc")              # 非法 → 默认 3
    assert stream_max_chunks() == 3

    monkeypatch.delenv(WEIXIN_STREAM_ENV, raising=False)
    assert stream_max_chunks() == 3


def test_hard_limit_clamps_even_when_asked_for_more():
    text = _long_reply() * 3
    chunks = split_reply_for_delivery(text, max_chunks=999)
    assert len(chunks) <= WEIXIN_STREAM_MAX_CHUNKS_HARD_LIMIT
    assert "".join(chunks) == text


# ------------------------------------------------------------- 失败 → 兼底 / 报错

def test_midway_failure_redelivers_the_remainder_complete():
    """第 2 段失败 → 剩余部分（第 2+3 段）拼成一条**完整**重投。"""
    text = _long_reply()
    planned = split_reply_for_delivery(text, max_chunks=3)
    assert len(planned) >= 3, "样本要能切出 ≥3 段才能测中途失败"

    client = _FakeClient(fail_calls=(2,))      # 第 2 次调用（第 2 段）失败
    report = asyncio.run(_channel().deliver_reply(client, "u", text, max_chunks=3))

    assert report.ok, report
    assert "".join(client.sent) == text        # 用户拿到的拼接逐字等于原文
    assert report.delivered_text == text
    # 第 1 段单发；剩余（第 2 段起）合并成一条 → 共 2 条消息
    assert client.sent[0] == planned[0]
    assert client.sent[1] == "".join(planned[1:])
    assert report.sent_chunks == 2 and report.total_chunks == len(planned)


def test_persistent_failure_reports_boundary_and_never_truncates_silently():
    """重投也失败 → ``ok=False`` 且 ``failed_at`` 指明边界（不静默截断）。"""
    text = _long_reply()
    planned = split_reply_for_delivery(text, max_chunks=3)

    # 第 2 次调用起全部失败（第2段 + 两次剩余重投都失败）
    client = _FakeClient(fail_calls=(2, 3, 4, 5, 6))
    report = asyncio.run(_channel().deliver_reply(client, "u", text, max_chunks=3))

    assert report.ok is False
    assert report.failed_at == 1                       # 从第 2 段（0-based 1）起失败
    assert report.sent_chunks == 1                     # 只有第 1 段送达
    assert client.sent == [planned[0]]
    assert report.delivered_text == planned[0]         # 实际送达 = 已发前缀
    assert report.error                            # 有可读错误
    assert report.total_chunks == len(planned)         # 计划段数仍可见


def test_remainder_retries_reuse_one_client_id():
    """剩余部分的重投复用同一个 client_id（服务端去重），不制造重复消息。"""
    text = _long_reply()
    client = _FakeClient(fail_calls=(2, 3, 4, 5, 6))
    asyncio.run(_channel().deliver_reply(client, "u", text, max_chunks=3))
    attempts = client.client_ids
    # 调用序列：1=段1(ok) 2=段2(失败) 3=剩余重投1(失败) 4=剩余重投2(失败)
    # 两次「剩余」尝试必须同 id（同一条逻辑消息的重复投递）；与段2 不同（另一条消息）。
    assert len(attempts) >= 4, attempts
    assert attempts[2] == attempts[3], attempts      # 剩余重投之间复用同一 id
    assert attempts[1] != attempts[2]                # 段2 与剩余是两条不同消息


# ----------------------------------------------------------- 纯文本路径不回归

def test_handle_message_short_reply_still_sends_exactly_one(tmp_path):
    async def go():
        store = WeixinAccountStore(tmp_path / "wx.db")
        await store.init()
        await store.upsert_account("acct", token="T", status="online")

        async def dispatch(a, c, t):
            return "短回复"

        channel = WeixinChannel(store, dispatch=dispatch)
        client = _FakeClient()
        reply = await channel.handle_message("acct", client, _user_msg("hi"))
        return reply, client

    reply, client = asyncio.run(go())
    assert reply == "短回复"
    assert client.sent == ["短回复"]          # 与改动前逐字一致：就一条


def test_handle_message_long_reply_is_chunked(tmp_path):
    async def go():
        store = WeixinAccountStore(tmp_path / "wx.db")
        await store.init()
        await store.upsert_account("acct", token="T", status="online")
        text = _long_reply()

        async def dispatch(a, c, t):
            return text

        channel = WeixinChannel(store, dispatch=dispatch)
        client = _FakeClient()
        reply = await channel.handle_message("acct", client, _user_msg("hi"))
        return text, reply, client

    text, reply, client = asyncio.run(go())
    assert reply == text                       # handle_message 的返回不变（仍是原文）
    assert 1 <= len(client.sent) <= 3
    assert "".join(client.sent) == text        # 逐字


def test_send_text_still_sends_a_single_message(tmp_path):
    """审批推送 / 测试 `/send` 走的 ``send_text`` 不受分段影响：仍是一条。"""
    async def go():
        store = WeixinAccountStore(tmp_path / "wx.db")
        await store.init()
        await store.upsert_account("acct", token="T", status="online")
        channel = WeixinChannel(store)
        client = _FakeClient()
        channel._clients["acct"] = client       # 注入假 client，跳过真实建连
        long = _long_reply()
        await channel.send_text("acct", "u", long)
        return long, client

    long, client = asyncio.run(go())
    assert client.sent == [long]                # 单条，不拆


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
