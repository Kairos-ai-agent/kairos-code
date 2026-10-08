"""微信会话的审批：推送 / 回答 / 超时 / 并发 / 不确定 —— 全部离线。

``kairos/weixin_approvals.py`` 把闸门（``kairos/approvals.py`` 的
``ApprovalChannel``）的问题接到微信会话上。这里用的是**真实的**消息总线与审批
通道（只有微信出站 ``send_text`` 是假的），所以测的确实是那条真路径：
``approval.requested`` 真的被广播出去、``ApprovalChannel.resolve`` 真的结清了
那个正等在工具调用里的 ``asyncio.wait_for``。

覆盖三条 fail-closed 纪律：
* 超时（默认 300 秒，env 可调）→ **拒绝** + 一条可读文本；
* 同一会话同时只允许一个未决问题，且一个会话等审批不阻塞同一账号的其它消息；
* 推不出去 / 会话对不上 / 参数解析不了 → **拒绝**，绝无自动批准。

本文件不连网、不连真实微信。真实链路的边界见 ``docs/WEIXIN_ILINK.md`` 第七节。
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from kairos import approvals
from kairos.core.message_bus import MessageBus
from kairos.feishu import parse_command
from kairos.weixin_approvals import (
    DEFAULT_APPROVAL_TIMEOUT_S,
    TIMEOUT_ENV,
    WeixinApprovalBridge,
    approval_timeout_s,
    stop_project,
)
from kairos.weixin_ilink import (
    WeixinAccountStore,
    WeixinChannel,
    current_session,
)

ACCOUNT = "acct"
CHAT = "chat"
PROJECT = "proj-1"


# --------------------------------------------------------------------- fakes

class _FakeOutbound:
    """假的微信出站通道：只记录 ``send_text``；``fail=True`` 模拟推不出去。"""

    def __init__(self, fail: bool = False) -> None:
        self.sent: List[tuple] = []
        self.fail = fail

    async def send_text(self, account_id: str, to_user_id: str, text: str,
                        context_token: Optional[str] = None) -> Dict[str, Any]:
        if self.fail:
            raise RuntimeError("network down")
        # 真发微信是一次网络请求 —— 一定有挂起点。这里也让出一次，好让
        # 「发通知的任务被误 cancel」这类 bug 在测试里就现形。
        await asyncio.sleep(0)
        self.sent.append((account_id, to_user_id, text))
        return {"message_id": "m1"}

    def texts(self) -> List[str]:
        return [t for _a, _c, t in self.sent]


class _FakeProject:
    def __init__(self, pid: str) -> None:
        self.id = pid
        self.name = f"微信 · {pid}"


class _FakeOrchestrator:
    """只实现本桥用到的两个方法：``get_project`` / ``stop_loop``。"""

    def __init__(self, project_id: str = PROJECT) -> None:
        self.project = _FakeProject(project_id)
        self.stopped: List[str] = []

    def get_project(self, pid: str):
        return self.project if pid == self.project.id else None

    def stop_loop(self, pid: str) -> bool:
        if pid != self.project.id:
            return False
        self.stopped.append(pid)
        return True

    def list_projects(self) -> List[Any]:
        return [self.project]


class _FakeClient:
    """``WeixinChannel.handle_message`` 只用到 ``send_message``。"""

    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []

    async def send_message(self, msg: Dict[str, Any], **_kw: Any) -> Dict[str, Any]:
        self.sent.append(msg)
        return {"ret": 0}

    def texts(self) -> List[str]:
        out = []
        for msg in self.sent:
            items = msg.get("item_list") or []
            for item in items:
                out.append((item.get("text_item") or {}).get("text", ""))
        return out


@contextmanager
def _in_weixin_session(account_id: str = ACCOUNT, chat_id: str = CHAT):
    """在调用栈上标记「这是某个微信会话正在处理的消息」。

    ``WeixinChannel.handle_message`` 就是这么做的；这里手动设置是为了直接驱动
    ``ApprovalChannel.request``（等价于闸门在 agent 的工具分发里问出问题）。
    """
    token = current_session.set((account_id, chat_id))
    try:
        yield
    finally:
        current_session.reset(token)


# ------------------------------------------------------------------ fixtures

async def _env(tmp_path, *, bridge_timeout: float = 5.0,
               channel_default: float = 0.2,
               outbound: Optional[_FakeOutbound] = None,
               bind: bool = True, bound_project: str = PROJECT):
    """一套真实的（总线 + 审批通道 + 账号存储）+ 假的出站通道。"""
    store = WeixinAccountStore(tmp_path / "wx.db")
    await store.init()
    if bind:
        await store.bind(ACCOUNT, CHAT, bound_project)
    bus = MessageBus()
    channel = approvals.ApprovalChannel(message_bus=bus, timeout_s=channel_default)
    wx = outbound or _FakeOutbound()
    orch = _FakeOrchestrator()
    bridge = WeixinApprovalBridge(store, wx, orchestrator=orch,
                                  timeout_s=bridge_timeout)
    bridge.attach(message_bus=bus, approval_channel=channel)
    return store, bus, channel, wx, bridge, orch


async def _wait_sent(wx: _FakeOutbound, n: int = 1, tries: int = 200) -> None:
    for _ in range(tries):
        if len(wx.sent) >= n:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"出站消息没有出现（期望 {n} 条，实际 {len(wx.sent)}）")


async def _ask(channel, *, tool: str = "file_write", resource: str = "notes.txt",
               reason: str = "writes a file", project_id: str = PROJECT):
    """在微信会话里问一个问题（等价于闸门把 ASK 变成问题）。"""
    with _in_weixin_session():
        task = asyncio.create_task(channel.request(
            tool=tool, resource=resource, reason=reason,
            project_id=project_id))
        return task


# ------------------------------------------------------------------ 推送问题

async def test_an_ask_in_a_weixin_session_is_pushed_as_a_text_message(tmp_path):
    _store, _bus, channel, wx, bridge, _orch = await _env(tmp_path)
    task = await _ask(channel, tool="file_write", resource="notes.txt",
                      reason="writes a file")
    await _wait_sent(wx)

    account_id, to_user_id, text = wx.sent[0]
    assert (account_id, to_user_id) == (ACCOUNT, CHAT)
    assert "file_write" in text and "notes.txt" in text
    assert "/approve" in text and "/deny" in text and "/stop" in text
    # 登记为「等这个会话回答」
    assert bridge.pending_sessions().get((ACCOUNT, CHAT)) is not None

    assert "已批准" in await bridge.handle_command(ACCOUNT, CHAT, "approve", [])
    assert (await task)["allow"] is True
    assert bridge.pending_sessions() == {}


async def test_the_pushed_question_never_carries_a_secret(tmp_path):
    wx = _FakeOutbound()
    _store, _bus, channel, wx, bridge, _orch = await _env(tmp_path, outbound=wx)
    secret = "sk-abcdefghijklmnop0123456789"
    await _ask(channel, tool="terminal",
               resource=f"curl -H 'Authorization: Bearer {secret}' https://x",
               reason="password=hunter2")
    await _wait_sent(wx)
    text = wx.texts()[0]
    assert secret not in text
    assert "hunter2" not in text
    assert "terminal" in text          # 动作名还在（是人看的）
    await bridge.handle_command(ACCOUNT, CHAT, "deny", [])


async def test_a_connection_string_is_redacted_and_long_values_are_truncated(
        tmp_path):
    wx = _FakeOutbound()
    _store, _bus, channel, wx, bridge, _orch = await _env(tmp_path, outbound=wx)
    await _ask(channel, tool="terminal",
               resource="psql postgres://user:s3cr3t@db.internal:5432/app "
                        + "x" * 500,
               reason="")
    await _wait_sent(wx)
    text = wx.texts()[0]
    assert "s3cr3t" not in text
    assert "<redacted>" in text
    assert "…" in text                      # 截断，不倾倒大块命令
    assert len(text) < 700
    await bridge.handle_command(ACCOUNT, CHAT, "deny", [])


# ---------------------------------------------------------------- 入站回答

async def test_deny_refuses(tmp_path):
    _store, _bus, channel, wx, bridge, _orch = await _env(tmp_path)
    task = await _ask(channel)
    await _wait_sent(wx)
    assert "已拒绝" in await bridge.handle_command(ACCOUNT, CHAT, "deny", [])
    assert (await task)["allow"] is False


async def test_approve_one_selects_the_pending_question(tmp_path):
    _store, _bus, channel, wx, bridge, _orch = await _env(tmp_path)
    task = await _ask(channel)
    await _wait_sent(wx)
    assert "已批准" in await bridge.handle_command(ACCOUNT, CHAT, "approve", ["1"])
    assert (await task)["allow"] is True


async def test_an_unparseable_approve_is_refused_not_guessed(tmp_path):
    """``/approve 2`` / ``/approve abc`` 都不能自动批准 —— 按拒绝处理。"""
    _store, _bus, channel, wx, bridge, _orch = await _env(tmp_path)
    for bad in (["2"], ["abc"], ["1", "2"]):
        task = await _ask(channel)
        await _wait_sent(wx, len(wx.sent) + 1)
        reply = await bridge.handle_command(ACCOUNT, CHAT, "approve", bad)
        assert "拒绝" in reply
        assert (await task)["allow"] is False


async def test_approve_with_no_pending_question_changes_nothing(tmp_path):
    _store, _bus, _channel, _wx, bridge, _orch = await _env(tmp_path)
    assert "没有待审批" in await bridge.handle_command(ACCOUNT, CHAT, "approve", [])
    assert "没有待审批" in await bridge.handle_command(ACCOUNT, CHAT, "deny", [])


async def test_commands_are_case_insensitive_and_may_carry_args():
    assert parse_command("/APPROVE") == ("approve", [])
    assert parse_command("/Deny 1") == ("deny", ["1"])
    assert parse_command("/STOP") == ("stop", [])


async def test_handle_command_ignores_commands_it_does_not_own(tmp_path):
    _store, _bus, _channel, _wx, bridge, _orch = await _env(tmp_path)
    assert await bridge.handle_command(ACCOUNT, CHAT, "status", []) is None


# ------------------------------------------------------------------- 超时

async def test_a_timeout_refuses_and_says_so(tmp_path):
    _store, _bus, channel, wx, bridge, _orch = await _env(tmp_path,
                                                          bridge_timeout=0.15)
    task = await _ask(channel)
    await _wait_sent(wx)
    answer = await asyncio.wait_for(task, 3)
    assert answer["allow"] is False                     # 超时 = 拒绝
    assert bridge.pending_sessions() == {}
    assert any("超时" in t for t in wx.texts())          # 并且说给用户听


async def test_the_weixin_window_replaces_the_channel_default(tmp_path):
    """微信问题用微信的时限（0.15s），而不是通道默认（5s）。"""
    _store, _bus, channel, wx, bridge, _orch = await _env(tmp_path,
                                                          bridge_timeout=0.15,
                                                          channel_default=5.0)
    task = await _ask(channel)
    answer = await asyncio.wait_for(task, 3)
    assert answer["allow"] is False


async def test_the_resolver_leaves_non_weixin_requests_alone(tmp_path):
    """不在微信会话上下文里 → 返回 None（Web 端仍是通道默认时限）。"""
    _store, _bus, _channel, _wx, bridge, _orch = await _env(tmp_path,
                                                            bridge_timeout=5.0)
    assert bridge._resolve_timeout("file_write", "x", PROJECT) is None
    with _in_weixin_session():
        assert bridge._resolve_timeout("file_write", "x", PROJECT) == 5.0


def test_the_timeout_default_is_300_seconds_and_env_overrides_it(monkeypatch):
    monkeypatch.delenv(TIMEOUT_ENV, raising=False)
    assert approval_timeout_s() == DEFAULT_APPROVAL_TIMEOUT_S == 300.0
    monkeypatch.setenv(TIMEOUT_ENV, "12.5")
    assert approval_timeout_s() == 12.5
    # 写坏了（空 / 非数字 / 非正数）退回默认，不会变成「瞬间超时」。
    for broken in ("", "abc", "0", "-3"):
        monkeypatch.setenv(TIMEOUT_ENV, broken)
        assert approval_timeout_s() == DEFAULT_APPROVAL_TIMEOUT_S


# ------------------------------------------------------- 不确定 = 一律拒绝

async def test_a_push_failure_refuses(tmp_path):
    wx = _FakeOutbound(fail=True)
    _store, _bus, channel, wx, bridge, _orch = await _env(tmp_path, outbound=wx)
    answer = await asyncio.wait_for(await _ask(channel), 3)
    assert answer["allow"] is False
    assert wx.sent == []                       # 没推出去
    assert bridge.pending_sessions() == {}


async def test_a_session_mismatch_refuses(tmp_path):
    """微信会话里发起的问题，但项目没绑到这个会话 —— 不推、直接拒绝。"""
    wx = _FakeOutbound()
    _store, _bus, channel, wx, bridge, _orch = await _env(
        tmp_path, outbound=wx, bound_project="some-other-project")
    answer = await asyncio.wait_for(
        await _ask(channel, project_id=PROJECT), 3)
    assert answer["allow"] is False
    assert wx.sent == []


async def test_an_unbound_session_refuses(tmp_path):
    wx = _FakeOutbound()
    _store, _bus, channel, wx, bridge, _orch = await _env(
        tmp_path, outbound=wx, bind=False)
    answer = await asyncio.wait_for(await _ask(channel), 3)
    assert answer["allow"] is False
    assert wx.sent == []


async def test_an_ask_without_a_weixin_session_is_left_to_the_web(tmp_path):
    """没有微信上下文、项目也没绑到微信 → 本桥不介入（Web 端行为不变）。"""
    wx = _FakeOutbound()
    _store, _bus, channel, wx, bridge, _orch = await _env(tmp_path, outbound=wx)
    loop = asyncio.get_running_loop()
    started = loop.time()
    answer = await asyncio.wait_for(channel.request(
        tool="file_write", resource="x", reason="", project_id="web-project"), 3)
    assert answer["allow"] is False
    assert wx.sent == []
    # 用的是通道默认时限（0.2s），不是微信的 5s —— 说明时限没被顺手改掉。
    assert loop.time() - started < 1.0


async def test_a_second_question_in_the_same_session_is_refused(tmp_path):
    """一个会话同时只允许一个未决问题：第二个 ASK 直接拒绝。"""
    _store, _bus, channel, wx, bridge, _orch = await _env(tmp_path,
                                                          bridge_timeout=0.3)
    first = await _ask(channel)
    await _wait_sent(wx, 1)
    second = await _ask(channel)
    answer2 = await asyncio.wait_for(second, 3)
    assert answer2["allow"] is False
    assert len(wx.sent) == 1                     # 第二个问题没有被推出去
    answer1 = await asyncio.wait_for(first, 3)   # 第一个仍然按自己的时限结清
    assert answer1["allow"] is False


# ------------------------------------------------------------------- 停止

async def test_stop_refuses_the_pending_question_and_stops_the_project(tmp_path):
    _store, _bus, channel, wx, bridge, orch = await _env(tmp_path,
                                                         bridge_timeout=5.0)
    task = await _ask(channel)
    await _wait_sent(wx)
    reply = await bridge.handle_command(ACCOUNT, CHAT, "stop", [])
    assert "已停止" in reply
    assert orch.stopped == [PROJECT]             # 与 POST /{id}/stop 同一对调用
    assert (await asyncio.wait_for(task, 3))["allow"] is False
    assert bridge.pending_sessions() == {}


async def test_stop_without_a_binding_says_so(tmp_path):
    _store, _bus, _channel, _wx, bridge, _orch = await _env(tmp_path, bind=False)
    assert "没有可停止" in await bridge.handle_command(ACCOUNT, CHAT, "stop", [])


def test_stop_project_reports_nothing_running(tmp_path):
    orch = _FakeOrchestrator()
    orch.stop_loop = lambda pid: False           # type: ignore[assignment]
    names, text = stop_project(orch, PROJECT)
    assert names == [] and "没有在运行" in text
    names, text = stop_project(orch, "nope")
    assert names == [] and "不存在" in text


# --------------------------------------------- 通道层：上下文 + 不阻塞

async def test_handle_message_marks_the_current_session(tmp_path):
    """``handle_message`` 处理期间标记会话；处理完清掉。"""
    store = WeixinAccountStore(tmp_path / "wx.db")
    await store.init()
    seen: List[Any] = []

    async def dispatch(account_id: str, chat_id: str, text: str) -> str:
        seen.append(current_session.get())
        return "ok"

    channel = WeixinChannel(store, dispatch=dispatch)
    client = _FakeClient()
    await channel.handle_message("acct-A", client, {
        "message_type": 1, "from_user_id": "user-1",
        "item_list": [{"type": 1, "text_item": {"text": "hi"}}]})
    assert seen == [("acct-A", "user-1")]
    assert current_session.get() is None


async def test_a_parked_message_does_not_block_the_next_one(tmp_path):
    """一条消息停在等审批上，同一账号的下一条消息照样被处理（能回 /approve）。"""
    store = WeixinAccountStore(tmp_path / "wx.db")
    await store.init()
    await store.upsert_account("acct-A", token="T", status="online")

    class _ScriptedClient:
        def __init__(self) -> None:
            self.sent: List[Dict[str, Any]] = []
            self._served = False

        async def get_updates(self, cursor: str = "", timeout=None):
            if not self._served:
                self._served = True
                return {"ret": 0, "get_updates_buf": "CUR-1", "msgs": [
                    _msg("first"), _msg("second")]}
            await asyncio.sleep(30)          # 之后一直挂着
            return {"ret": 0, "msgs": []}

        async def send_message(self, msg, **_kw):
            self.sent.append(msg)
            return {"ret": 0}

        async def notify_start(self):
            return {"ret": 0}

        async def notify_stop(self):
            return {"ret": 0}

    def _msg(text: str) -> Dict[str, Any]:
        return {"message_type": 1, "from_user_id": "user-1",
                "item_list": [{"type": 1, "text_item": {"text": text}}]}

    parked = asyncio.Event()

    async def dispatch(account_id: str, chat_id: str, text: str) -> str:
        if text == "first":
            parked.set()
            await asyncio.sleep(30)          # 模拟「停在等审批」
            return "first-done"
        return f"second:{text}"

    client = _ScriptedClient()
    channel = WeixinChannel(store, dispatch=dispatch, poll_interval=0.01)
    await channel.start_account("acct-A", client=client)
    try:
        await asyncio.wait_for(parked.wait(), 3)
        for _ in range(300):
            texts = [(i.get("text_item") or {}).get("text")
                     for m in client.sent for i in (m.get("item_list") or [])]
            if "second:second" in texts:
                break
            await asyncio.sleep(0.01)
        assert "second:second" in [
            (i.get("text_item") or {}).get("text")
            for m in client.sent for i in (m.get("item_list") or [])]
    finally:
        await channel.stop_account("acct-A")


# ------------------------------------------------- 路由层：入口与 /help

class _DispatchOrch(_FakeOrchestrator):
    def create_project(self, name: str, description: str = "",
                       work_dir: str = ""):
        return self.project


async def test_help_lists_approve_deny_and_stop(tmp_path):
    from api.routes.weixin import make_dispatch

    store = WeixinAccountStore(tmp_path / "wx.db")
    await store.init()
    await store.bind(ACCOUNT, CHAT, PROJECT)
    dispatch = make_dispatch(_DispatchOrch(), store)
    reply = await dispatch(ACCOUNT, CHAT, "/help")
    for word in ("/approve", "/deny", "/stop"):
        assert word in reply


async def test_dispatch_routes_approve_to_the_bridge(tmp_path, monkeypatch):
    from api.routes import weixin as weixin_routes

    store = WeixinAccountStore(tmp_path / "wx.db")
    await store.init()
    await store.bind(ACCOUNT, CHAT, PROJECT)
    calls: List[tuple] = []

    class _Bridge:
        async def handle_command(self, account_id, chat_id, cmd, args):
            calls.append((account_id, chat_id, cmd, list(args)))
            return "桥：已收到"

    monkeypatch.setattr(weixin_routes, "_approval_bridge", _Bridge())
    dispatch = weixin_routes.make_dispatch(_DispatchOrch(), store)
    assert await dispatch(ACCOUNT, CHAT, "/APPROVE 1") == "桥：已收到"
    assert calls == [(ACCOUNT, CHAT, "approve", ["1"])]
    assert await dispatch(ACCOUNT, CHAT, "/stop") == "桥：已收到"
    assert calls[-1][2] == "stop"


async def test_dispatch_stop_still_works_without_a_bridge(tmp_path, monkeypatch):
    """桥没接线时 /stop 仍走同一条停止路径（停止不依赖审批）。"""
    from api.routes import weixin as weixin_routes

    store = WeixinAccountStore(tmp_path / "wx.db")
    await store.init()
    await store.bind(ACCOUNT, CHAT, PROJECT)
    orch = _DispatchOrch()
    monkeypatch.setattr(weixin_routes, "_approval_bridge", None)
    dispatch = weixin_routes.make_dispatch(orch, store)
    assert "已停止" in await dispatch(ACCOUNT, CHAT, "/stop")
    assert orch.stopped == [PROJECT]


async def test_dispatch_approve_without_a_bridge_says_it_is_not_wired(
        tmp_path, monkeypatch):
    from api.routes import weixin as weixin_routes

    store = WeixinAccountStore(tmp_path / "wx.db")
    await store.init()
    await store.bind(ACCOUNT, CHAT, PROJECT)
    monkeypatch.setattr(weixin_routes, "_approval_bridge", None)
    dispatch = weixin_routes.make_dispatch(_DispatchOrch(), store)
    reply = await dispatch(ACCOUNT, CHAT, "/approve")
    assert "未接线" in reply and "超时" in reply


# ----------------------------------------------- 与既有审批通道的兼容

async def test_the_resolver_hook_defaults_to_off_for_other_callers():
    """没有被设置 resolver 的通道，行为与以前一字不差。"""
    channel = approvals.ApprovalChannel(timeout_s=0.05)
    answer = await channel.request(tool="file_write", resource="x",
                                   reason="", project_id="p")
    assert answer["status"] == "timeout" and answer["allow"] is False


async def test_a_bridge_without_a_channel_stays_inert(tmp_path):
    """没有审批通道：不推、不批、不登记 —— 空转，而不是不安全。"""
    store = WeixinAccountStore(tmp_path / "wx.db")
    await store.init()
    await store.bind(ACCOUNT, CHAT, PROJECT)
    wx = _FakeOutbound()
    bridge = WeixinApprovalBridge(store, wx, timeout_s=0.2)
    bridge.attach(message_bus=MessageBus(), approval_channel=None)
    assert bridge._approvals is None
    with _in_weixin_session():
        await asyncio.sleep(0)
    assert wx.sent == [] and bridge.pending_sessions() == {}
    bridge.detach()


def test_reset_dependencies_clears_the_injected_bridge():
    from api.routes import weixin as weixin_routes

    weixin_routes.set_approval_bridge(object())
    try:
        assert weixin_routes._approval_bridge is not None
    finally:
        weixin_routes.reset_dependencies()
    assert weixin_routes._approval_bridge is None


async def test_new_module_has_no_direct_coder_chat_call():
    """``kairos/weixin_approvals.py`` 不得直连 ``coder.chat()``（CI 守卫同源）。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "kairos" / "weixin_approvals.py").read_text(encoding="utf-8")
    assert ".chat(" not in src
