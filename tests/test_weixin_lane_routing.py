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


def test_a_media_message_keeps_the_coder_lane(env, monkeypatch):
    """带媒体的消息保持原行为直接走 Coder（通用车道看不到附件块）。"""
    _, project = env

    async def fail_general(**kwargs):  # pragma: no cover - must not be called
        raise AssertionError("the general lane must not be used for media")

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fail_general)
    reply = asyncio.run(_answer_message(project, "[附件] 看看这张图",
                                        has_media=True))
    assert reply == "coder: [附件] 看看这张图"
    assert project.coder.calls == ["[附件] 看看这张图"]
