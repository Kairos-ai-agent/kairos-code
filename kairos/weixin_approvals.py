"""微信（ClawBot / iLink）会话的审批：把闸门的问题推到微信、把回答接回闸门。

要补的洞
--------
``kairos/approvals.py`` 的 :class:`~kairos.approvals.ApprovalChannel` 是进程里
**唯一**「问用户」的通道：闸门（``kairos/sentinel.py`` 的 ``authorize_async``）
把一个 ASK 变成一个待回答的问题，Web 端在 ``/api/approvals`` 上看到并回答。
微信通道此前只有文本层 —— 用户能在微信里聊天，却**看不见闸门问了他什么**，
也无从回答；于是需要人批的动作等于默默卡住/被拒。安全，但不可用。

本模块**不新建第二套审批**。它只是 :class:`ApprovalChannel` 的一个观察者与
回答者：

* 订阅总线上的 ``approval.requested``：属于某个微信会话的问题，脱敏后作为
  **一条文本消息**推到那个会话（复用
  :meth:`WeixinChannel.send_text`，即现有出站能力），并登记为
  「等这个 (账号, 聊天对象) 回答」；
* 把该会话里的 ``/approve`` / ``/deny`` 解析成
  :meth:`ApprovalChannel.resolve` —— 与 Web 端 ``POST /api/approvals/{id}``
  调的是**同一个**方法；
* ``/stop`` 走合并后的停止路径（``orchestrator.stop_loop`` +
  ``skeleton.runner.stop_skeleton_run``），与 ``POST /{id}/stop`` 同一对调用。

三条 fail-closed 纪律（任何一条不满足都**不放行**）
--------------------------------------------------
1. **超时**：等待有时限（默认 300 秒，环境变量
   ``KAIROS_WEIXIN_APPROVAL_TIMEOUT`` 可调）。到点按**拒绝**结清，并回一条
   可读文本（「已超时、默认拒绝」）。时限通过
   :meth:`ApprovalChannel.set_timeout_resolver` 只作用于微信发起的问题，
   Web 端的默认等待时间不变。
2. **并发**：同一个会话同时只允许一个未决问题；已有未决时新的 ASK **直接
   拒绝**（不排队）。每条消息在独立任务里处理，所以一个会话等审批不会阻塞
   同一个微信账号下的其它会话。
3. **不确定**：推不出去（出站失败）、会话对不上（项目没绑到这个会话）、
   参数解析不了 —— 一律**拒绝**。绝无「自动批准」路径。

诚实边界
--------
本模块的正确性由离线测试覆盖（``tests/test_weixin_approvals.py``），
**没有在真实微信上端到端验证过**：需要真机扫码、真实的 iLink ``getupdates``
流。见 ``docs/WEIXIN_ILINK.md`` 第七节。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from kairos.weixin_ilink import current_session

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_APPROVAL_TIMEOUT_S",
    "TIMEOUT_ENV",
    "APPROVAL_COMMANDS",
    "approval_timeout_s",
    "WeixinApprovalBridge",
    "stop_project",
]

#: 等待微信用户回答审批的默认时限（秒）。
DEFAULT_APPROVAL_TIMEOUT_S = 300.0

#: 调时限的环境变量名。取值非法 / 非正数时退回默认值（fail-safe：不会变成 0）。
TIMEOUT_ENV = "KAIROS_WEIXIN_APPROVAL_TIMEOUT"

#: 本桥负责的三个命令（``parse_command`` 已经小写化 ``cmd``）。
APPROVAL_COMMANDS = frozenset({"approve", "deny", "stop"})

#: 推给微信的正文里，单个字段最多多少字符（不倾倒大块命令/长路径）。
_MAX_FIELD_CHARS = 200

#: ``kairos.sentinel.redact`` 不覆盖连接串里的 ``user:password@``；补一条。
_CONNSTRING_RE = re.compile(
    r"(?i)\b([a-z][a-z0-9+.\-]*://)([^/\s:@]+):([^/\s:@]+)@")

_TIMEOUT_NOTICE = "⏱ 该审批问题已超时（{secs:.0f} 秒），已按**拒绝**处理。"
_ALREADY_DONE_NOTICE = "该审批问题已经结束了（没有在这里回答）。"


def approval_timeout_s() -> float:
    """微信审批的等待时限：``KAIROS_WEIXIN_APPROVAL_TIMEOUT`` 或默认 300 秒。

    环境变量为空 / 解析不出 / 非正数 → 默认值。**绝不返回 0 或负数**：那会让
    每个问题瞬间超时，看起来像「审批总是失败」，其实是配置写坏了。
    """
    raw = (os.environ.get(TIMEOUT_ENV) or "").strip()
    if raw:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return DEFAULT_APPROVAL_TIMEOUT_S


def _redact(text: str) -> str:
    """脱敏 + 压平空白 + 截断；无法脱敏时返回空串（宁可不显示，也不泄露）。

    脱敏规则**复用** ``kairos.sentinel.redact``（审计/日志同一套：``sk-``、
    ``Bearer``、``password=``、``token=``、AKIA、GitHub token …），再补一条
    连接串里的 ``user:password@``。
    """
    text = (text or "").strip()
    if not text:
        return ""
    try:
        from kairos.sentinel import redact  # noqa: PLC0415 — 与审计同一套规则
    except Exception:  # pragma: no cover - 导入失败就不显示这一段
        logger.debug("weixin approval: redact 不可用，省略该字段")
        return ""
    out = _CONNSTRING_RE.sub(r"\1\2:<redacted>@", redact(text))
    out = " ".join(out.split())
    if len(out) > _MAX_FIELD_CHARS:
        out = out[:_MAX_FIELD_CHARS] + "…"
    return out


def stop_project(orchestrator: Any, project_id: str) -> Tuple[List[str], str]:
    """停止该项目上正在跑的东西，返回 ``(停掉的种类, 可读文本)``。

    与 ``POST /api/projects/{id}/stop`` 走的**同一对调用**（见
    ``api/routes/projects.py`` 的 ``stop_loop``）：先 ``orchestrator.stop_loop``
    （循环），再 ``stop_skeleton_run``（通用车道 / 骨架任务）。**不另建一套
    停止机制**，也不改那条路由。
    """
    names: List[str] = []
    if orchestrator is None:
        return names, "没有可用的编排器，无法停止。"
    project = None
    try:
        project = orchestrator.get_project(project_id)
    except Exception:  # noqa: BLE001
        logger.exception("weixin approval: get_project 失败 %s", project_id)
    if project is None:
        return names, f"项目 {project_id} 不存在。"
    try:
        if orchestrator.stop_loop(project_id):
            names.append("循环")
    except Exception:  # noqa: BLE001
        logger.exception("weixin approval: stop_loop 失败 %s", project_id)
    try:
        from kairos.skeleton.runner import stop_skeleton_run  # noqa: PLC0415
        if stop_skeleton_run(project):
            names.append("通用任务")
    except Exception:  # noqa: BLE001
        logger.exception("weixin approval: stop_skeleton_run 失败 %s", project_id)
    if not names:
        return names, "当前没有在运行的任务。"
    return names, "已停止：" + "、".join(names) + "。"


class WeixinApprovalBridge:
    """把 :class:`ApprovalChannel` 的问题接到微信会话上（观察者 + 回答者）。

    ``store`` 是 :class:`~kairos.weixin_ilink.WeixinAccountStore`（读绑定），
    ``channel`` 是 :class:`~kairos.weixin_ilink.WeixinChannel`（出站文本）。
    两者都可为 ``None``：那就推不出去 —— 一律拒绝（fail-closed）。
    """

    def __init__(self, store: Any = None, channel: Any = None, *,
                 orchestrator: Any = None,
                 message_bus: Any = None,
                 approval_channel: Any = None,
                 timeout_s: Optional[float] = None) -> None:
        self._store = store
        self._wx = channel
        self._orchestrator = orchestrator
        self._bus = message_bus
        self._approvals = approval_channel
        #: 显式时限（测试/运维用）；None → 读环境变量 / 默认 300 秒。
        self._timeout_s = float(timeout_s) if timeout_s else None
        self._listener_token: Optional[int] = None
        #: (account_id, chat_id) → request_id；**一个会话同时只有一个未决**。
        self._pending: Dict[Tuple[str, str], str] = {}
        #: request_id → 看门狗任务。
        self._watchdogs: Dict[str, asyncio.Task] = {}

    # -- 接线 -------------------------------------------------------------

    def attach(self, *, message_bus: Any = None,
               approval_channel: Any = None) -> None:
        """挂上总线（听问题）与审批通道（回答问题 / 设时限）。幂等。"""
        if message_bus is not None:
            self._bus = message_bus
        if approval_channel is not None:
            self._approvals = approval_channel
        if self._bus is not None and self._listener_token is None:
            self._listener_token = self._bus.add_listener(self._on_event)
        if self._approvals is not None:
            self._approvals.set_timeout_resolver(self._resolve_timeout)

    def detach(self) -> None:
        """取消订阅、停掉看门狗、清空登记（进程收尾 / 测试用）。"""
        if self._bus is not None and self._listener_token is not None:
            try:
                self._bus.remove_listener(self._listener_token)
            except Exception:  # noqa: BLE001
                logger.debug("weixin approval: remove_listener 失败",
                             exc_info=True)
        self._listener_token = None
        for task in list(self._watchdogs.values()):
            task.cancel()
        self._watchdogs.clear()
        self._pending.clear()

    # -- 观察 -------------------------------------------------------------

    def timeout_for(self) -> float:
        return self._timeout_s or approval_timeout_s()

    def _resolve_timeout(self, tool: str = "", resource: str = "",
                         project_id: str = "") -> Optional[float]:
        """只有**微信会话里**发出的问题才用微信的长时限；否则返回 None。

        判据是 ``kairos.weixin_ilink.current_session``：它只在
        ``WeixinChannel.handle_message`` 处理入站消息期间被设置，而审批请求就在
        同一条协程链上发出，所以「是不是微信发起」是确定的 —— Web 端的问题
        在这里返回 None，继续用通道默认时限，行为一字不变。
        """
        if current_session.get() is None:
            return None
        return self.timeout_for()

    async def _on_event(self, msg: Any) -> None:
        topic = getattr(msg, "topic", None)
        meta = getattr(msg, "metadata", None) or {}
        if topic == "approval.resolved":
            self._forget(str(meta.get("request_id") or ""))
        elif topic == "approval.requested":
            await self._on_requested(meta)

    def _forget(self, request_id: str) -> None:
        """一个请求结束了（谁答的都算）：清登记、停看门狗。"""
        if not request_id:
            return
        for session, rid in list(self._pending.items()):
            if rid == request_id:
                self._pending.pop(session, None)
        task = self._watchdogs.pop(request_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _on_requested(self, meta: Dict[str, Any]) -> None:
        """总线上的 ``approval.requested``：属于微信会话就推、就等。"""
        request_id = str(meta.get("request_id") or "")
        if not request_id or self._approvals is None:
            return
        sessions = await self._sessions_for(meta)
        if sessions is None:
            return  # 不是微信会话的问题：交给原来的（Web）通道，不动它
        if not sessions:
            # 明确是微信会话发起的，但项目没绑到这个会话 —— **对不上**，拒绝。
            await self._deny(request_id, "会话对不上（项目未绑定到该微信会话）")
            return
        # 一个会话同时只允许一个未决问题：已有未决 → 新的直接拒绝（fail-closed）。
        for session in sessions:
            if session in self._pending:
                await self._deny(request_id, "该会话已有未决的审批问题")
                return
        text = self._question_text(meta)
        for session in sessions:
            try:
                await self._push(session, text)
            except Exception as exc:  # noqa: BLE001 - 推不出去就是拒绝
                logger.warning("weixin approval: 推送失败 %s: %s",
                               session, type(exc).__name__)
                await self._deny(request_id, "审批问题无法推送到该微信会话")
                return
        for session in sessions:
            self._pending[session] = request_id
        self._start_watchdog(request_id, sessions)

    async def _sessions_for(
            self, meta: Dict[str, Any]) -> Optional[List[Tuple[str, str]]]:
        """这条问题对应哪些微信会话。

        * ``None``  —— 不是微信会话的问题（没有会话上下文，且项目也没绑到任何
          微信会话）；交给 Web 端，本桥不介入。
        * ``[]``    —— 明确是微信会话发起的，但和绑定对不上（**会话对不上**）。
        * 非空列表  —— 要推送的会话。
        """
        project_id = str(meta.get("project_id") or "")
        current = current_session.get()
        if current is not None:
            account_id, chat_id = current
            if self._store is None:
                return []
            try:
                bound = await self._store.lookup(account_id, chat_id)
            except Exception:  # noqa: BLE001
                logger.exception("weixin approval: 绑定查询失败")
                return []
            if not bound:
                return []
            # 直接证据是「正在跑这个会话的 agent」（contextvar）；但项目必须
            # 真的绑在这个会话上，否则说明不是它的问题 —— 不推。
            if project_id and bound != project_id:
                return []
            return [(account_id, chat_id)]
        if not project_id or self._store is None:
            return None
        try:
            bindings = await self._store.list_bindings()
        except Exception:  # noqa: BLE001
            logger.exception("weixin approval: 绑定列表查询失败")
            return None
        out = [(str(b.get("account_id") or ""), str(b.get("chat_id") or ""))
               for b in bindings
               if str(b.get("project_id") or "") == project_id]
        out = [(a, c) for a, c in out if a and c]
        return out or None

    # -- 出站 -------------------------------------------------------------

    def _question_text(self, meta: Dict[str, Any]) -> str:
        """给用户看的问题：动作名 + 关键参数的人类可读描述（已脱敏、已截断）。"""
        tool = _redact(str(meta.get("tool") or "")) or "（未知动作）"
        resource = _redact(str(meta.get("resource") or ""))
        reason = _redact(str(meta.get("reason") or ""))
        project_id = str(meta.get("project_id") or "")
        lines = ["🔐 需要你确认一个操作（微信审批）", "", f"动作：{tool}"]
        if resource:
            lines.append(f"对象：{resource}")
        if reason:
            lines.append(f"原因：{reason}")
        if project_id:
            lines.append(f"项目：{project_id}")
        lines += [
            "",
            "回复 /approve 同意 · /deny 拒绝 · /stop 停止",
            f"（{self.timeout_for():.0f} 秒内未回复将自动拒绝）",
        ]
        return "\n".join(lines)

    async def _push(self, session: Tuple[str, str], text: str) -> None:
        """把一条文本推到某个微信会话（现有出站能力，不另造）。"""
        if self._wx is None:
            raise RuntimeError("微信出站通道不可用")
        await self._wx.send_text(session[0], session[1], text)

    async def _notify(self, sessions: List[Tuple[str, str]], text: str) -> None:
        """尽力回一条可读文本；发不出去只记日志（绝不因此放行任何东西）。"""
        for session in sessions:
            try:
                await self._push(session, text)
            except Exception:  # noqa: BLE001
                logger.debug("weixin approval: 通知发送失败 %s", session)

    # -- 超时 -------------------------------------------------------------

    def _start_watchdog(self, request_id: str,
                        sessions: List[Tuple[str, str]]) -> None:
        task = asyncio.create_task(
            self._watchdog(request_id, sessions),
            name=f"weixin-approval-timeout-{request_id}")
        self._watchdogs[request_id] = task

    async def _watchdog(self, request_id: str,
                        sessions: List[Tuple[str, str]]) -> None:
        """到点仍未被回答 → 按拒绝结清 + 回一条可读文本。

        比通道自己的 ``wait_for`` 早 50ms 醒（两者时限相同）：这样「谁先结清」
        是确定的 —— 由本桥结清并**把话说给用户**，而不是静默超时。
        ``resolve`` 返回 False 表示问题已经结束（比如网页端先答了），那就只
        报一声「已结束」，绝不再动它。
        """
        timeout_s = self.timeout_for()
        try:
            await asyncio.sleep(max(0.01, timeout_s - 0.05))
        except asyncio.CancelledError:
            raise
        # 清登记时**不要**走 ``_forget``：它会把「正在跑的自己」cancel 掉，
        # 于是后面那条「已超时」的通知在下一个真正的挂起点（真发微信时是网络
        # 请求）就再也发不出去了。这里只清映射与自己的记账。
        self._watchdogs.pop(request_id, None)
        for session in sessions:
            if self._pending.get(session) == request_id:
                self._pending.pop(session, None)
        resolved = False
        if self._approvals is not None:
            resolved = bool(self._approvals.resolve(request_id, allow=False))
        if resolved:
            await self._notify(sessions, _TIMEOUT_NOTICE.format(secs=timeout_s))
        else:
            await self._notify(sessions, _ALREADY_DONE_NOTICE)

    # -- 入站回答 ---------------------------------------------------------

    async def handle_command(self, account_id: str, chat_id: str, cmd: str,
                             args: List[str]) -> Optional[str]:
        """把微信会话里的 ``/approve`` / ``/deny`` / ``/stop`` 接到现有机制上。

        返回直接回给用户的文本；``cmd`` 不是本桥管的三个之一时返回 ``None``
        （调用方继续按原来的方式处理）。
        """
        if cmd not in APPROVAL_COMMANDS:
            return None
        session = (account_id, chat_id)
        if cmd in ("approve", "deny"):
            return await self._answer(session, cmd, args)
        return await self._stop(session)

    async def _answer(self, session: Tuple[str, str], cmd: str,
                      args: List[str]) -> str:
        request_id = self._pending.get(session)
        if request_id is None:
            return "当前没有待审批的操作。"
        if cmd == "deny":
            # 拒绝总是安全的：带不带参数、参数是什么，都按拒绝结清。
            ok = self._resolve(session, request_id, allow=False)
            return "已拒绝。" if ok else _ALREADY_DONE_NOTICE
        # /approve：只有能**唯一确定**指向这一问时才批准；不确定 → 拒绝。
        if not self._selects_pending(args):
            self._resolve(session, request_id, allow=False)
            return ("无法确认你要批准哪一问（这个会话只有 1 个待审批）。"
                    "为安全起见已按拒绝处理；需要放行请让 agent 重新发起。")
        ok = self._resolve(session, request_id, allow=True)
        return "已批准，这一步会继续。" if ok else _ALREADY_DONE_NOTICE

    @staticmethod
    def _selects_pending(args: List[str]) -> bool:
        """``/approve`` 的参数是否明确指向这一问（本会话只有一个未决）。

        空参数 → 是。``/approve 1`` → 是（多问时的选号，这里只有第 1 问）。
        其它（``/approve 2``、``/approve abc``、多个参数）→ **否**，按拒绝处理。
        """
        if not args:
            return True
        if len(args) != 1:
            return False
        try:
            return int(str(args[0]).strip()) == 1
        except (TypeError, ValueError):
            return False

    def _resolve(self, session: Tuple[str, str], request_id: str, *,
                 allow: bool) -> bool:
        """回答一个问题（与 Web 端 ``POST /api/approvals/{id}`` 同一个方法）。"""
        if self._approvals is None:
            return False
        ok = bool(self._approvals.resolve(request_id, allow=allow))
        if ok:
            self._forget(request_id)
        return ok

    async def _deny(self, request_id: str, why: str) -> None:
        """不放行：把一个不确定的问题直接按拒绝结清。"""
        if self._approvals is None:
            return
        if self._approvals.resolve(request_id, allow=False):
            logger.info("weixin approval: 拒绝 %s（%s）", request_id, why)

    async def _stop(self, session: Tuple[str, str]) -> str:
        """``/stop``：先撤掉这个会话的未决审批，再停这个项目 —— 走合并后的停止路径。"""
        request_id = self._pending.get(session)
        if request_id is not None:
            self._resolve(session, request_id, allow=False)
        project_id = ""
        if self._store is not None:
            try:
                project_id = await self._store.lookup(*session) or ""
            except Exception:  # noqa: BLE001
                logger.exception("weixin approval: /stop 绑定查询失败")
        if not project_id:
            return "这个会话还没有绑定项目，没有可停止的任务。"
        _names, text = stop_project(self._orchestrator, project_id)
        return text

    # -- 观测（测试 / 排查用） --------------------------------------------

    def pending_sessions(self) -> Dict[Tuple[str, str], str]:
        return dict(self._pending)
