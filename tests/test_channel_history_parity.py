"""CI 守卫 + 离线用例：每个入站通道都要把「进消息」落库、把「通用车道回复」上总线。

背景 bug：微信 ClawBot / 企业微信收发的消息，**在网页的聊天/回复页面看不到**。
网页线程的数据源是 ``GET /api/projects/{id}/chat-messages?chat_only=true`` →
``Persistence.load_messages(chat_only=True)``（只取 ``CHAT_TOPICS``），它**只读库**、
不看别的来源。所以一个通道要在网页里「看得到」，必须做两件事：

  * **进消息写库**（``topic="user.chat"``）：否则用户自己发的那条在刷新后看不见；
  * **通用车道的回复发到总线上**（``topic="agent.chat"``，总线订阅者落库）：
    否则 agent 的回话看不见。Coder 车道的回复由 ``coder.chat`` 自己发
    （``kairos/agents/base.py``），**通道层绝不能再发一次**，否则双气泡。

本文件两部分：

1. **源码守卫**（``ast``，不 import FastAPI / 被测模块）：四个入站通道模块
   ``api/routes/{projects,im,wecom,weixin}.py`` 每一个都必须
   ① 有把进消息落库成 ``user.chat`` 的调用；② 在**跑通用车道**（``run_chat_reply``）
   的函数里发布 ``agent.chat``。缺一即失败并指名 文件。静态判定不到的分支要在
   ``HELPER_EXEMPT`` 里登记并写 reason；白名单条目对不上（helper 找不到、或模块其实
   已能静态判定）即报错，防止白名单腐烂成免死金牌。
2. **离线行为用例**（全部离线：假 client / stub）：微信 / 企微 / IM 的进消息落库
   （topic、content 是折好附件后的文本、metadata 带 project_id 与 source）、
   回复恰好发一次（无双气泡）、以及落库 / 发布失败时回复逐字不变（历史是锦上添花，
   绝不影响发送）。
"""
from __future__ import annotations

import ast
import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from api.routes import wecom as wecom_routes
from api.routes import weixin as weixin_routes
from api.routes.im import _answer_inbound as im_answer_inbound
from api.routes.wecom import _answer_message as wecom_answer_message
from api.routes.wecom import _dispatch as wecom_dispatch
from api.routes.weixin import _answer_message as weixin_answer_message
from api.routes.weixin import make_dispatch as weixin_make_dispatch
from kairos.weixin_ilink import WeixinAccountStore


# ===========================================================================
# 一、源码守卫
# ===========================================================================

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: 必须覆盖的入站通道（path, 人类可读的名字）。
CHANNEL_MODULES: Tuple[Tuple[str, str], ...] = (
    ("api/routes/projects.py", "网页端 Web /chat"),
    ("api/routes/im.py", "IM 入站 /inbound"),
    ("api/routes/wecom.py", "企业微信自建应用 webhook"),
    ("api/routes/weixin.py", "微信 iLink 分发"),
)

PERSIST_TOPIC = "user.chat"
REPLY_TOPIC = "agent.chat"

#: 若某个通道通过「共享 helper」落库 / 发回复（静态扫描看不出 helper 内部做了
#: 什么），就在此登记：``(path, kind) -> {"helper": <函数名>, "reason": <理由>}``，
#: ``kind`` ∈ {"persist", "reply"}。**每条必须写 reason 且必须对得上**：登记的
#: helper 在该模块里找不到调用，或该模块其实已经能静态判定到 → 条目失效并报错。
HELPER_EXEMPT: Dict[Tuple[str, str], Dict[str, str]] = {}


def _callee_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def scan_module(path: str, root: Path = _REPO_ROOT) -> Dict[str, Any]:
    """扫描一个通道模块，返回它落库 / 发回复的调用点（行号）与所有被调用名。

    - ``persist``：``save_message(...)``（其源码片段含 ``topic="user.chat"``）的行号。
    - ``reply``：``publish(...)``（其源码片段含 ``topic="agent.chat"``）的行号。
    - ``reply_lane``：``reply`` 里，**所在函数也调用了 run_chat_reply** 的那些行号。
    - ``callees``：该模块所有被调用函数的名字（供 helper 白名单核对）。
    """
    src = (root / path).read_text(encoding="utf-8")
    tree = ast.parse(src, filename=path)
    lines = src.splitlines()

    def fn_src(fn: ast.AST) -> str:
        start = fn.lineno - 1
        end = getattr(fn, "end_lineno", fn.lineno)
        return "\n".join(lines[start:end])

    def seg(node: ast.AST) -> str:
        try:
            return ast.get_source_segment(src, node) or ""
        except Exception:  # noqa: BLE001 - 取不到片段就当没有
            return ""

    persist: List[int] = []
    reply: List[int] = []
    reply_lane: List[int] = []
    callees: set = set()

    def walk(node: ast.AST, fn: Any) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child)
                continue
            if isinstance(child, ast.Call):
                name = _callee_name(child.func)
                if name:
                    callees.add(name)
                text = seg(child)
                attr = child.func.attr if isinstance(child.func, ast.Attribute) else ""
                if attr == "save_message" and (
                        'topic="user.chat"' in text or "topic='user.chat'" in text):
                    persist.append(child.lineno)
                if attr == "publish" and (
                        'topic="agent.chat"' in text or "topic='agent.chat'" in text):
                    reply.append(child.lineno)
                    if fn is not None and "run_chat_reply" in fn_src(fn):
                        reply_lane.append(child.lineno)
            walk(child, fn)

    walk(tree, None)
    return {"path": path, "persist": persist, "reply": reply,
            "reply_lane": reply_lane, "callees": callees}


def collect_problems(
    root: Path = _REPO_ROOT,
    exempt: Dict[Tuple[str, str], Dict[str, str]] = HELPER_EXEMPT,
) -> Dict[str, List[str]]:
    """返回分类的失败信息（各类为空 = 该类通过）。"""
    out: Dict[str, List[str]] = {
        "persist": [], "reply": [], "double_bubble": [], "whitelist": [],
    }
    known = {p for p, _ in CHANNEL_MODULES}

    for path, label in CHANNEL_MODULES:
        info = scan_module(path, root)

        # ---- ① 进消息落库 ----
        entry = exempt.get((path, "persist"))
        helper_hit = bool(entry and entry.get("helper") in info["callees"])
        if not (info["persist"] or helper_hit):
            out["persist"].append(
                f'{path}（{label}）：没有把进消息落库成 topic="user.chat" 的消息'
                f'——用户自己发的那条在网页线程里看不到（数据源只读库）。'
                f'请在入站处 save_message(... topic="user.chat" ...)，'
                f'或调用共享 helper 并登记进 HELPER_EXEMPT 写明理由。')
        if entry and info["persist"]:
            out["whitelist"].append(
                f'{path}：HELPER_EXEMPT 登记了 persist helper '
                f'{entry.get("helper")!r}，但该模块已能被静态判定到落库——'
                f'条目失效，请删除。')
        if entry and not helper_hit:
            out["whitelist"].append(
                f'{path}：HELPER_EXEMPT 的 persist 条目对不上——模块里找不到对 '
                f'{entry.get("helper")!r} 的调用。（条目失效，请修正或删除。）')

        # ---- ② 通用车道回复上总线 ----
        entry_r = exempt.get((path, "reply"))
        helper_hit_r = bool(entry_r and entry_r.get("helper") in info["callees"])
        if not (info["reply_lane"] or helper_hit_r):
            out["reply"].append(
                f'{path}（{label}）：通用车道路径没有发布 topic="agent.chat" 的'
                f'回复——agent 的回话在网页线程里看不到。请在跑 run_chat_reply 的'
                f'函数里 bus.publish(... topic="agent.chat" ...)，或调用共享 helper '
                f'并登记进 HELPER_EXEMPT 写明理由。')
        for ln in info["reply"]:
            if ln not in info["reply_lane"]:
                out["double_bubble"].append(
                    f'{path}:{ln}：发布了 topic="agent.chat"，但它所在的函数没有跑'
                    f'通用车道（run_chat_reply）——Coder 车道已由 coder.chat 自己'
                    f'发布，这样会双气泡。回复发布必须只在通用车道分支。')
        if entry_r and info["reply_lane"]:
            out["whitelist"].append(
                f'{path}：HELPER_EXEMPT 登记了 reply helper '
                f'{entry_r.get("helper")!r}，但该模块已能被静态判定到发布——'
                f'条目失效，请删除。')
        if entry_r and not helper_hit_r:
            out["whitelist"].append(
                f'{path}：HELPER_EXEMPT 的 reply 条目对不上——模块里找不到对 '
                f'{entry_r.get("helper")!r} 的调用。（条目失效，请修正或删除。）')

    # ---- 白名单自身的完整性 ----
    for (path, kind), entry in exempt.items():
        where = f"{path}[{kind}]"
        if path not in known:
            out["whitelist"].append(f"HELPER_EXEMPT 指向了非通道模块: {where}")
        if kind not in ("persist", "reply"):
            out["whitelist"].append(f"HELPER_EXEMPT 的 kind 非法: {where}")
        if len(str(entry.get("reason") or "").strip()) < 12:
            out["whitelist"].append(f"HELPER_EXEMPT 条目缺 reason（或太短）: {where}")
        if not str(entry.get("helper") or "").strip():
            out["whitelist"].append(f"HELPER_EXEMPT 条目缺 helper 名: {where}")
    return out


# ---- 守卫用例 -------------------------------------------------------------

def test_guard_every_channel_persists_inbound_user_message():
    """每个入站通道都必须把进消息落库（否则网页线程看不到用户那条）。"""
    problems = collect_problems()["persist"]
    assert not problems, (
        "以下入站通道没有把用户的进消息落库（网页线程只读库，看不到）:\n  "
        + "\n  ".join(problems))


def test_guard_every_channel_general_lane_publishes_agent_chat():
    """每个入站通道的通用车道都必须把回复发上总线（否则看不到 agent 回话）。"""
    problems = collect_problems()["reply"]
    assert not problems, (
        "以下入站通道的通用车道没有把回复发上总线（网页看不到 agent 回话）:\n  "
        + "\n  ".join(problems))


def test_guard_no_agent_chat_publish_outside_the_general_lane():
    """agent.chat 发布只能出现在通用车道函数里，否则 Coder 车道双气泡。"""
    problems = collect_problems()["double_bubble"]
    assert not problems, (
        "存在可能在 Coder 车道造成双气泡的 agent.chat 发布:\n  "
        + "\n  ".join(problems))


def test_guard_helper_whitelist_is_explicit_and_live():
    problems = collect_problems()["whitelist"]
    assert not problems, "HELPER_EXEMPT 白名单有问题:\n  " + "\n  ".join(problems)


def test_guard_sees_the_known_channels():
    """非空转：扫描确实抓到了已工作一侧（projects.py）的落库与回复发布。"""
    info = scan_module("api/routes/projects.py")
    assert info["persist"], "扫描没抓到 projects.py 的 user.chat 落库——扫描逻辑坏了"
    assert info["reply_lane"], "扫描没抓到 projects.py 通用车道的 agent.chat 发布"
    for path, _ in CHANNEL_MODULES:
        assert (_REPO_ROOT / path).is_file(), f"通道模块不存在: {path}"


_GOOD_MODULE_SRC = (
    "from kairos.core.message_bus import Message\n"
    "async def answer(project, text):\n"
    "    from kairos.skeleton.service import run_chat_reply\n"
    "    project._db.save_message(Message(sender='user', topic='user.chat',\n"
    "                                     content=text))\n"
    "    reply = await run_chat_reply(kind='repo', root='/x', message=text)\n"
    "    bus = getattr(project, 'message_bus', None)\n"
    "    if bus is not None:\n"
    "        await bus.publish(Message(sender='p.skeleton', topic='agent.chat',\n"
    "                                  content=reply))\n"
    "    return reply\n"
)


def _write(tmp_path: Path, rel: str, src: str) -> None:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src, encoding="utf-8")


def _write_all_channels(tmp_path: Path, src: str) -> None:
    for path, _ in CHANNEL_MODULES:
        _write(tmp_path, path, src)


def test_guard_flags_a_channel_missing_history(tmp_path):
    """自验证：一个既不落库也不发回复的通道模块，守卫必须报红并指名文件。"""
    _write_all_channels(tmp_path, _GOOD_MODULE_SRC)
    _write(tmp_path, "api/routes/wecom.py",
           "async def _dispatch(project, text):\n"
           "    return await project.coder.chat(text)\n")
    _write(tmp_path, "api/routes/weixin.py", "X = 1\n")
    problems = collect_problems(root=tmp_path)
    assert problems["persist"], "守卫没报出缺失的进消息落库"
    assert any("wecom.py" in p for p in problems["persist"]), problems["persist"]
    assert any("weixin.py" in p for p in problems["persist"]), problems["persist"]
    assert problems["reply"], "守卫没报出缺失的通用车道回复发布"
    assert any("im.py" in p or "wecom.py" in p or "weixin.py" in p
               for p in problems["reply"]), problems["reply"]


def test_guard_accepts_a_complete_channel(tmp_path):
    """正面对照：四个通道都齐了，不该报任何问题。"""
    _write_all_channels(tmp_path, _GOOD_MODULE_SRC)
    problems = collect_problems(root=tmp_path)
    assert not any(problems.values()), problems


def test_guard_flags_a_publish_outside_the_general_lane(tmp_path):
    """自验证：在通用车道之外发布 agent.chat → 报双气泡风险。"""
    _write_all_channels(tmp_path, _GOOD_MODULE_SRC)
    _write(tmp_path, "api/routes/wecom.py",
           "from kairos.core.message_bus import Message\n"
           "async def _dispatch(project, text):\n"
           "    await project.message_bus.publish(Message(topic='agent.chat'))\n"
           "    return await project.coder.chat(text)\n")
    problems = collect_problems(root=tmp_path)
    assert problems["double_bubble"], "守卫没报出通用车道之外的 agent.chat 发布"


def test_guard_flags_a_stale_whitelist_entry(tmp_path):
    """自验证：模块已能静态判定，却还登记 helper → 条目失效，必须报错。"""
    _write_all_channels(tmp_path, _GOOD_MODULE_SRC)
    exempt = {
        ("api/routes/im.py", "persist"): {
            "helper": "never_called_helper",
            "reason": "这个 helper 根本没有被调用，条目应被视为失效。"},
    }
    problems = collect_problems(root=tmp_path, exempt=exempt)
    assert problems["whitelist"], "守卫没报出失效的白名单条目"


# ===========================================================================
# 二、离线行为用例（假 client / stub，不连微信 / 企微）
# ===========================================================================

class _RecordingDB:
    def __init__(self) -> None:
        self.saved: List[Any] = []

    def save_message(self, message: Any) -> None:
        self.saved.append(message)


class _FakeBus:
    def __init__(self) -> None:
        self.published: List[Any] = []

    async def publish(self, message: Any) -> None:
        self.published.append(message)


class _FakeCoder:
    def __init__(self) -> None:
        self.calls: List[str] = []

    async def chat(self, text: str, **kwargs: Any) -> str:
        self.calls.append(text)
        return f"coder: {text}"


class _FakeProject:
    def __init__(self, root: Path) -> None:
        self.id = "p1"
        self.name = "通道测试"
        self.workspace = root
        self.work_dir = str(root)          # _project_root prefers work_dir
        self.coder = _FakeCoder()


class _FakeOrch:
    """带落库 + 总线句柄的假编排器（与 test_weixin/wecom_lane_routing 同形）。"""

    def __init__(self, project: _FakeProject) -> None:
        self._project = project
        self._db = _RecordingDB()
        self.message_bus = _FakeBus()

    def get_project(self, project_id: str) -> Any:
        return self._project if project_id == self._project.id else None

    def create_project(self, name: str, description: str = "",
                       work_dir: str = "") -> Any:
        return self._project

    def list_projects(self) -> List[Any]:
        return [self._project]


# ----------------------------------------------------------- 微信 (weixin)

def _weixin_env(tmp_path: Path):
    store = WeixinAccountStore(tmp_path / "wx.db")
    asyncio.run(store.init())
    root = tmp_path / "ws"
    root.mkdir()
    project = _FakeProject(root)
    asyncio.run(store.bind("acct", "chat", project.id))
    orch = _FakeOrch(project)
    dispatch = weixin_make_dispatch(orch, store)
    return dispatch, project, orch


def test_weixin_persists_the_folded_inbound_message(tmp_path, monkeypatch):
    """进消息落库：topic=user.chat、content 是**折好附件块后的** prompt、带来源。"""
    dispatch, project, orch = _weixin_env(tmp_path)

    async def fake_fold(project, prompt, media):
        return f"{prompt}\n\n[附件 / attachments] 折好的附件块"

    monkeypatch.setattr(weixin_routes, "_fold_inbound_media", fake_fold)

    raw = "修复 src/auth 里的登录 bug"          # 编码意图 -> Coder 车道，不碰通用车道
    prompt = f"{raw}\n\n[附件 / attachments] 折好的附件块"
    reply = asyncio.run(dispatch("acct", "chat", raw, media=[object()]))

    assert reply == f"coder: {prompt}"
    assert len(orch._db.saved) == 1
    msg = orch._db.saved[0]
    assert msg.topic == "user.chat"
    assert msg.content == prompt                     # 折好后的 prompt，不是原文
    assert msg.metadata["project_id"] == project.id
    assert msg.metadata["source"] == "weixin"
    assert msg.metadata["account_id"] == "acct"
    assert msg.metadata["chat_id"] == "chat"
    # Coder 车道：通道层绝不发 agent.chat（避免双气泡）
    assert orch.message_bus.published == []


def test_weixin_general_lane_reply_is_published_exactly_once(tmp_path, monkeypatch):
    """通用车道回复恰好发一次 agent.chat；进消息也落了库。"""
    dispatch, project, orch = _weixin_env(tmp_path)

    async def fake_general(*, kind, root, message, **kwargs):
        return "通用车道回复"

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fake_general)
    reply = asyncio.run(dispatch("acct", "chat", "你好呀"))

    assert reply == "通用车道回复"
    assert project.coder.calls == []
    agent_chats = [m for m in orch.message_bus.published if m.topic == "agent.chat"]
    assert len(agent_chats) == 1
    assert agent_chats[0].content == "通用车道回复"
    assert agent_chats[0].metadata["project_id"] == project.id
    assert [s.topic for s in orch._db.saved] == ["user.chat"]


def test_weixin_history_failure_never_changes_the_reply(tmp_path, monkeypatch):
    """落库 / 发布都抛异常，回复逐字不变（历史是锦上添花，绝不能拖垮正事）。"""
    dispatch, project, orch = _weixin_env(tmp_path)

    async def fake_general(*, kind, root, message, **kwargs):
        return "通用车道回复"

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fake_general)

    def boom_save(message):
        raise RuntimeError("db down")

    async def boom_publish(message):
        raise RuntimeError("bus down")

    orch._db.save_message = boom_save
    orch.message_bus.publish = boom_publish

    assert asyncio.run(dispatch("acct", "chat", "你好呀")) == "通用车道回复"


# ----------------------------------------------------------- 企微 (wecom)

def _wecom_env(tmp_path: Path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    project = _FakeProject(root)
    orch = _FakeOrch(project)
    # 无绑定记录也不影响离线用例（走 orchestrator 直接建/取项目）。
    monkeypatch.setattr(wecom_routes, "_bindings", None, raising=False)
    return orch, project


def test_wecom_persists_the_inbound_message(tmp_path, monkeypatch):
    orch, project = _wecom_env(tmp_path, monkeypatch)
    raw = "修复 src/auth 里的登录 bug"
    reply = asyncio.run(wecom_dispatch("user-1", raw, orch))

    assert reply == f"coder: {raw}"
    assert len(orch._db.saved) == 1
    msg = orch._db.saved[0]
    assert msg.topic == "user.chat"
    assert msg.content == raw
    assert msg.metadata["project_id"] == project.id
    assert msg.metadata["source"] == "wecom"
    assert msg.metadata["chat_id"] == "user-1"
    assert orch.message_bus.published == []          # Coder 车道不双发


def test_wecom_general_lane_reply_is_published_exactly_once(tmp_path, monkeypatch):
    orch, project = _wecom_env(tmp_path, monkeypatch)

    async def fake_general(*, kind, root, message, **kwargs):
        return "通用车道回复"

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fake_general)
    reply = asyncio.run(wecom_dispatch("user-1", "你好呀", orch))

    assert reply == "通用车道回复"
    assert project.coder.calls == []
    agent_chats = [m for m in orch.message_bus.published if m.topic == "agent.chat"]
    assert len(agent_chats) == 1
    assert agent_chats[0].content == "通用车道回复"
    assert [s.topic for s in orch._db.saved] == ["user.chat"]


def test_wecom_history_failure_never_changes_the_reply(tmp_path, monkeypatch):
    orch, project = _wecom_env(tmp_path, monkeypatch)

    async def fake_general(*, kind, root, message, **kwargs):
        return "通用车道回复"

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fake_general)

    def boom_save(message):
        raise RuntimeError("db down")

    async def boom_publish(message):
        raise RuntimeError("bus down")

    orch._db.save_message = boom_save
    orch.message_bus.publish = boom_publish

    assert asyncio.run(wecom_dispatch("user-1", "你好呀", orch)) == "通用车道回复"


# ------------------------------------------------------------- IM (im.py)

class _IMOrch:
    """带落库 + 总线句柄的假编排器，返回**真实** Project（同 test_im_api 的教训）。"""

    def __init__(self, base: Path) -> None:
        from kairos.core.orchestrator import Project
        self._Project = Project
        self.workspace_base = base
        self.projects: Dict[str, Any] = {}
        self._db = _RecordingDB()
        self.message_bus = _FakeBus()

    def create_project(self, name: str, description: str,
                       work_dir: str = "") -> Any:
        pid = f"proj{len(self.projects) + 1}"
        ws = self.workspace_base / pid
        ws.mkdir(parents=True, exist_ok=True)
        project = self._Project(pid, name, description, ws, work_dir, db=None)
        project.coder = _FakeCoder()
        self.projects[pid] = project
        return project

    def get_project(self, project_id: str) -> Any:
        return self.projects.get(project_id)


@pytest.fixture
def im_env(tmp_path):
    from fastapi.testclient import TestClient
    from api.app import app
    from api.deps import get_orchestrator
    from api.routes import im as im_routes
    from kairos.im_accounts import IMAccountStore

    store = IMAccountStore(tmp_path / "im.db")
    asyncio.run(store.init())
    orch = _IMOrch(tmp_path / "ws")
    im_routes.set_store(store)
    app.dependency_overrides[get_orchestrator] = lambda: orch
    client = TestClient(app)                 # 无 context manager：不跑真实 lifespan
    yield client, store, orch
    app.dependency_overrides.clear()
    im_routes.set_store(None)


def _im_sign(secret: str, body: bytes, ts: str | None = None) -> Dict[str, str]:
    from kairos.im_accounts import (SIGNATURE_HEADER, TIMESTAMP_HEADER,
                                    sign_request)
    stamp = ts or str(int(time.time()))
    return {TIMESTAMP_HEADER: stamp,
            SIGNATURE_HEADER: sign_request(secret, stamp, body)}


def _im_post(client, account_id: str, secret: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    return client.post(f"/api/im/{account_id}/inbound", content=body,
                       headers=_im_sign(secret, body))


def _im_account(store, account_id: str = "wx-a", secret: str = "s3cret") -> None:
    asyncio.run(store.upsert_account(account_id, name=account_id.upper(),
                                     secret=secret, enabled=True))


def test_im_persists_inbound_with_source_metadata(im_env):
    client, store, orch = im_env
    _im_account(store)
    raw = "修复 src/auth 里的登录 bug"
    r = _im_post(client, "wx-a", "s3cret", {"chat_id": "c1", "text": raw})

    assert r.status_code == 200
    assert len(orch._db.saved) == 1
    msg = orch._db.saved[0]
    assert msg.topic == "user.chat"
    assert msg.content == raw
    assert msg.metadata["project_id"] == r.json()["project_id"]
    assert msg.metadata["source"] == "im"
    assert msg.metadata["chat_id"] == "c1"


def test_im_general_lane_reply_is_published_exactly_once(im_env, monkeypatch):
    client, store, orch = im_env
    _im_account(store)

    async def fake_general(*, kind, root, message, **kwargs):
        return "通用车道回复"

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fake_general)
    r = _im_post(client, "wx-a", "s3cret",
                 {"chat_id": "c1", "text": "你好呀，今天过得怎么样"})

    assert r.status_code == 200
    pid = r.json()["project_id"]
    assert orch.projects[pid].coder.calls == []
    agent_chats = [m for m in orch.message_bus.published if m.topic == "agent.chat"]
    assert len(agent_chats) == 1
    assert agent_chats[0].content == "通用车道回复"
    assert agent_chats[0].metadata["project_id"] == pid


def test_im_history_failure_never_blocks_the_reply(im_env, monkeypatch):
    client, store, orch = im_env
    _im_account(store)

    async def fake_general(*, kind, root, message, **kwargs):
        return "通用车道回复"

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fake_general)

    def boom_save(message):
        raise RuntimeError("db down")

    async def boom_publish(message):
        raise RuntimeError("bus down")

    orch._db.save_message = boom_save
    orch.message_bus.publish = boom_publish

    r = _im_post(client, "wx-a", "s3cret",
                 {"chat_id": "c1", "text": "你好呀，今天过得怎么样"})
    assert r.status_code == 200
    queued = asyncio.run(store.pending("wx-a"))
    assert [m.text for m in queued] == ["通用车道回复"]


if __name__ == "__main__":  # pragma: no cover - 手跑时给人看的报告
    _probs = collect_problems()
    for _kind, _items in _probs.items():
        for _p in _items:
            print(f"[{_kind}] {_p}")
    raise SystemExit(1 if any(_probs.values()) else 0)
