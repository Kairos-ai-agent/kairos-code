"""Round 37 routing on the Weixin iLink dispatch entry (all offline).

``api/routes/weixin.py`` used to hand every inbound message straight to the
project's ``coder.chat``. It now runs the same router the web ``/chat`` route
uses, so a signal-free conversational message is answered on the general lane
and a coding / long task keeps the Coder. These tests pin both sides, the
media bypass, and the no-regression guarantee (a plain message still gets a
reply even when the general lane has no model).

Nothing here reaches the network: the general lane's generator is stubbed in
the one test that exercises it, and pinned to ``None`` where the fallback is
the point.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from api.routes.weixin import _answer_message, make_dispatch
from kairos.weixin_ilink import WeixinAccountStore


# --------------------------------------------------------------------- fakes

class _FakeCoder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def chat(self, text: str, **kwargs: Any) -> str:
        self.calls.append(text)
        return f"coder: {text}"


class _FakeProject:
    def __init__(self, root: Path) -> None:
        self.id = "p1"
        self.name = "微信测试"
        self.workspace = root
        self.work_dir = str(root)          # _project_root prefers work_dir
        self.coder = _FakeCoder()


class _FakeOrchestrator:
    def __init__(self, project: _FakeProject) -> None:
        self._project = project

    def get_project(self, project_id: str):
        return self._project if project_id == self._project.id else None

    def create_project(self, name: str, description: str, work_dir: str = ""):
        return self._project

    def list_projects(self):
        return [self._project]


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def env(tmp_path):
    """A store with one binding, an empty project workspace, and the dispatch."""
    store = WeixinAccountStore(tmp_path / "wx.db")
    asyncio.run(store.init())
    root = tmp_path / "ws"
    root.mkdir()
    project = _FakeProject(root)
    asyncio.run(store.bind("acct", "chat", project.id))
    dispatch = make_dispatch(_FakeOrchestrator(project), store)
    return dispatch, project


# --------------------------------------------------------------------- tests

def test_a_coding_message_keeps_the_coder_lane(env):
    dispatch, project = env
    reply = asyncio.run(dispatch("acct", "chat", "修复登录 bug"))
    assert reply == "coder: 修复登录 bug"
    assert project.coder.calls == ["修复登录 bug"]


def test_a_plain_message_is_answered_on_the_general_lane(env, monkeypatch):
    """闲聊走通用车道：不复用 Coder，但回复照样返回。"""
    dispatch, project = env
    seen = []

    async def fake_general(*, kind, root, message, **kwargs):
        seen.append((kind, message))
        return "通用车道回复"

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fake_general)
    reply = asyncio.run(dispatch("acct", "chat", "你好呀"))
    assert reply == "通用车道回复"
    assert seen == [("repo", "你好呀")]
    assert project.coder.calls == []


def test_a_plain_message_still_gets_a_reply_without_a_general_lane_model(
        env, monkeypatch):
    """不回归：通用车道没有模型时回退 Coder，纯文本消息仍能拿到回复。"""
    dispatch, project = env
    monkeypatch.setattr("kairos.skeleton.service.default_generator", lambda: None)
    reply = asyncio.run(dispatch("acct", "chat", "今天天气不错呀"))
    assert reply == "coder: 今天天气不错呀"
    assert project.coder.calls == ["今天天气不错呀"]


def test_the_explicit_chat_command_also_routes(env, monkeypatch):
    dispatch, project = env

    async def fake_general(*, kind, root, message, **kwargs):
        return f"通用：{message}"

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fake_general)
    reply = asyncio.run(dispatch("acct", "chat", "/chat 帮我看看这个"))
    assert reply == "通用：帮我看看这个"


def test_a_media_message_routes_like_text_to_the_general_lane(env, monkeypatch):
    """旁路已解除：带媒体的消息与纯文本走同一条路由（通用车道可读附件）。

    ``_answer_message`` 不再有一条「has_media -> 直连 Coder」的分支；带媒体的
    消息照样过 ``route_task``，命中通用车道就由通用车道回答——真正的转弯点是
    通用车道现在带只读文件工具，能读到折进提示词的附件块。
    """
    _, project = env
    seen = []

    async def fake_general(*, kind, root, message, **kwargs):
        seen.append((kind, message))
        return "通用车道：已读附件"

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fake_general)
    folded = ("[附件] 看看这张图\n\n[附件 / attachments] 用户上传了以下文件，"
              "已经保存在项目目录里，可以直接用 file_read / grep 等工具按相对路径读取：\n"
              "- attachments/pic.png (12.0 KB, image/png)")
    reply = asyncio.run(_answer_message(project, folded, route_text="[附件] 看看这张图"))
    assert reply == "通用车道：已读附件"
    # 车道拿到的是折了附件块的提示词，判定却只看用户原文
    assert seen == [("repo", folded)]
    # 通用车道回答了，Coder 未被触碰（不再为带媒体强制绕回 Coder）
    assert project.coder.calls == []


def test_media_route_uses_the_user_text_not_the_attachment_block(env, monkeypatch):
    """附件块里的「文件」二字不能把带附件的闲聊推回 Coder。

    ``attachment_prompt_block`` 的正文含「文件」（路由编码词表里的一个词）。
    若判定用折好的提示词，带附件的闲聊会因这个词被读成编码意图、绕回 Coder。
    ``route_text`` 让判定只看用户原文，与 Web ``/chat`` 传 ``request.message``
    一致。
    """
    _, project = env
    seen = []

    async def fake_general(*, kind, root, message, **kwargs):
        seen.append(message)
        return "通用车道回复"

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fake_general)
    block = ("[附件 / attachments] 用户上传了以下文件，已经保存在项目目录里，"
             "可以直接用 file_read / grep 等工具按相对路径读取：\n"
             "- attachments/报告.docx (2.0 KB, application/vnd.openxmlformats-officedocument.wordprocessingml.document)")
    reply = asyncio.run(_answer_message(project, f"看看这个\n\n{block}",
                                        route_text="看看这个"))
    assert reply == "通用车道回复"
    assert project.coder.calls == []

    # 让这个断言有牙齿：若拿折好的提示词去判定，路由会把这条闲聊推回 Coder。
    from api.routes.projects import _project_root
    from kairos.task_router import route_task
    folded = route_task(requirement=f"看看这个\n\n{block}",
                        workspace=_project_root(project))
    assert folded.uses_skeleton is False   # 「文件」被读成编码意图
    raw = route_task(requirement="看看这个", workspace=_project_root(project))
    assert raw.uses_skeleton is True


def test_a_media_message_still_gets_a_reply_when_the_general_lane_misses(
        env, monkeypatch):
    """回退路径不能断：通用车道返回空 / 无模型时，带媒体的消息仍回退 Coder。"""
    _, project = env

    async def empty_general(**kwargs):
        return ""

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", empty_general)
    reply = asyncio.run(_answer_message(project, "[附件] 看看这张图",
                                        route_text="[附件] 看看这张图"))
    assert reply == "coder: [附件] 看看这张图"
    assert project.coder.calls == ["[附件] 看看这张图"]
