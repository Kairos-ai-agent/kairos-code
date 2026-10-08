"""Round 37 路由覆盖到企业微信自建应用入口（全部离线）。

``api/routes/wecom.py`` 之前把每条成员消息都直连 ``coder.chat``。现在它跑与
Web ``/chat`` 同一条路由：没有编码意图的闲聊在通用车道上回答，编码 / 长任务
仍走 Coder。这些用例钉住两侧、以及「纯文本消息仍能拿到回复」的不回归保证。

不联网：命中通用车道的用例把 ``run_chat_reply`` 打桩；专门验回退的用例把
``default_generator`` 钉成 ``None``。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from api.routes import wecom as wecom_routes
from api.routes.wecom import _answer_message, _dispatch


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
        self.name = "企业微信测试"
        self.workspace = root
        self.work_dir = str(root)          # _project_root prefers work_dir
        self.coder = _FakeCoder()


class _FakeOrchestrator:
    def __init__(self, project: _FakeProject) -> None:
        self._project = project

    def get_project(self, project_id: str):
        return self._project if project_id == self._project.id else None

    def create_project(self, name: str, description: str = "",
                       work_dir: str = "") -> _FakeProject:
        return self._project

    def list_projects(self):
        return [self._project]


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def env(tmp_path, monkeypatch):
    """一个空工作区、一个成员会话分发的入口。"""
    root = tmp_path / "ws"
    root.mkdir()
    project = _FakeProject(root)
    # _dispatch 走 orchestrator 直接建/取项目；无绑定记录也不影响离线用例。
    monkeypatch.setattr(wecom_routes, "_bindings", None, raising=False)
    return _FakeOrchestrator(project), project


# --------------------------------------------------------------------- tests

def test_a_coding_message_keeps_the_coder_lane(env):
    orch, project = env
    reply = asyncio.run(_dispatch("user-1", "修复登录 bug", orch))
    assert reply == "coder: 修复登录 bug"
    assert project.coder.calls == ["修复登录 bug"]


def test_a_plain_message_is_answered_on_the_general_lane(env, monkeypatch):
    """闲聊走通用车道：不复用 Coder，但回复照样返回。"""
    orch, project = env
    seen = []

    async def fake_general(*, kind, root, message, **kwargs):
        seen.append((kind, message))
        return "通用车道回复"

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fake_general)
    reply = asyncio.run(_dispatch("user-1", "你好呀", orch))
    assert reply == "通用车道回复"
    assert seen == [("repo", "你好呀")]
    assert project.coder.calls == []


def test_a_plain_message_still_gets_a_reply_without_a_general_lane_model(
        env, monkeypatch):
    """不回归：通用车道没有模型时回退 Coder，纯文本消息仍能拿到回复。"""
    orch, project = env
    monkeypatch.setattr("kairos.skeleton.service.default_generator",
                        lambda: None)
    reply = asyncio.run(_dispatch("user-1", "今天天气不错呀", orch))
    assert reply == "coder: 今天天气不错呀"
    assert project.coder.calls == ["今天天气不错呀"]


def test_the_explicit_chat_command_also_routes(env, monkeypatch):
    orch, project = env

    async def fake_general(*, kind, root, message, **kwargs):
        return f"通用：{message}"

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fake_general)
    reply = asyncio.run(_dispatch("user-1", "/chat 帮我看看这个", orch))
    assert reply == "通用：帮我看看这个"


def test_the_general_lane_failure_falls_back_to_the_coder(env, monkeypatch):
    """通用车道抛异常也不能丢掉这一轮——回退 Coder。"""
    _, project = env

    async def boom(**kwargs):
        raise RuntimeError("general lane is down")

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", boom)
    reply = asyncio.run(_answer_message(project, "随便聊聊"))
    assert reply == "coder: 随便聊聊"
    assert project.coder.calls == ["随便聊聊"]


def test_answer_message_returns_the_general_reply_directly(env, monkeypatch):
    """直接调 helper 也走同一套判定（供 webhook 与其它入口复用）。"""
    _, project = env

    async def fake_general(*, kind, root, message, **kwargs):
        return f"hlp：{message}"

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fake_general)
    reply = asyncio.run(_answer_message(project, "随便说点什么吧"))
    assert reply == "hlp：随便说点什么吧"
    assert project.coder.calls == []
