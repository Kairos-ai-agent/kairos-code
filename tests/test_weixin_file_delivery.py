"""微信出站「把生成的文件发回来」：**真产出信号** + coder 工作树围墙对齐（离线）。

不联网、不用真微信账号：CDN 上传注入假 ``poster``，取上传参数注入假 ``client``。

覆盖任务要求的每一条：

* (a) 项目根下本轮新建的文件 → 成为候选并发出；
* (b) **coder 工作树（项目根之外）本轮新建的文件 → 也成为候选**（沙箱真实情形）；
* (c) ``.git`` / ``node_modules`` / ``attachments`` / ``runs`` / ``*.tmp`` 里的变动被忽略；
* (d) 内容触发敏感嗅探的文件 → **不**发（文本回复照旧、不抛）；
* (e) ``WEIXIN_NEVER_SEND_NAMES`` 里的名字 → 不发；
* (f) 没候选 / 没 root / 上传失败 ⇒ 文本回复原样返回、不抛异常；
* (g) 快照差集自身：新增 / mtime 变化 / size 变化都能识别；未变动的旧文件不因
  「被文本提到」而误判为本轮产出；文本 fallback 仍工作、且与真信号去重正确；
* 可观测：候选与实发各记一条日志（caplog 断言）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kairos.weixin_ilink as wx  # noqa: E402
from kairos.weixin_ilink import (  # noqa: E402
    WEIXIN_NEVER_SEND_NAMES,
    WeixinAccountStore,
    WeixinChannel,
    diff_touched_files,
    merge_sendable_candidates,
    snapshot_tree_files,
)


# --------------------------------------------------------------------------- 假件

class _UploadClient:
    """假 ILinkClient：记录 get_upload_url 入参、记录发出的消息。"""

    def __init__(self, *, upload_error: Optional[Exception] = None) -> None:
        self.sent: List[Dict[str, Any]] = []
        self.upload_calls: List[Dict[str, Any]] = []
        self._upload_error = upload_error

    async def get_upload_url(self, **kwargs: Any) -> Dict[str, Any]:
        self.upload_calls.append(dict(kwargs))
        if self._upload_error is not None:
            raise self._upload_error
        return {"upload_param": "UP-PARAM"}

    async def send_message(self, msg: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        self.sent.append(msg)
        return {"ret": 0, "message_id": "srv"}


class _Poster:
    """假 CDN 上传：记录 (url, ciphertext)；可注入错误。"""

    def __init__(self, *, param: str = "DL-PARAM",
                 error: Optional[Exception] = None) -> None:
        self.param = param
        self.error = error
        self.calls: List[tuple] = []

    async def __call__(self, url: str, ciphertext: bytes) -> str:
        self.calls.append((url, ciphertext))
        if self.error is not None:
            raise self.error
        return self.param


def _user_msg(text: str) -> Dict[str, Any]:
    return {"message_type": 1, "from_user_id": "user-1", "to_user_id": "bot",
            "context_token": "CTX",
            "item_list": [{"type": 1, "text_item": {"text": text}}]}


def _item_types(msg: Dict[str, Any]) -> List[int]:
    return [it.get("type") for it in (msg.get("item_list") or [])]


def _file_names(client: _UploadClient) -> List[str]:
    out: List[str] = []
    for m in client.sent:
        items = m.get("item_list") or []
        if items and items[0].get("type") == 4:
            out.append(items[0]["file_item"]["file_name"])
    return out


def _text_messages(client: _UploadClient) -> List[str]:
    out: List[str] = []
    for m in client.sent:
        items = m.get("item_list") or []
        if items and items[0].get("type") == 1:
            out.append(items[0]["text_item"]["text"])
    return out


async def _store(tmp_path: Path) -> WeixinAccountStore:
    store = WeixinAccountStore(tmp_path / "weixin.db")
    await store.init()
    await store.upsert_account("acct", token="T", status="online")
    return store


def _run_handle(tmp_path: Path, *, dispatch, roots, client: _UploadClient,
                monkeypatch, poster=None) -> str:
    """同步驱动一次 handle_message（自建事件循环，不嵌套）。"""

    async def _main():
        store = await _store(tmp_path)
        channel = WeixinChannel(
            store, dispatch=dispatch, workspace_resolver=lambda a, c: roots)
        if poster is not None:
            monkeypatch.setattr(wx, "_httpx_post_cdn", poster)
        return await channel.handle_message("acct", client, _user_msg("hi"))

    return asyncio.run(_main())


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    return root


# ============================================== (a) 项目根下本轮新建的文件

def test_a_new_file_in_project_root_is_delivered(tmp_path, monkeypatch):
    root = _root(tmp_path)

    async def dispatch(a, c, t):
        (root / "report.md").write_text("# 报告\n结论：通过。\n", encoding="utf-8")
        return "好的，已生成，请查收。"          # 文本里**没**点名文件

    client = _UploadClient()
    reply = _run_handle(tmp_path, dispatch=dispatch, roots=[str(root)],
                        client=client, monkeypatch=monkeypatch, poster=_Poster())

    assert reply == "好的，已生成，请查收。"
    assert _file_names(client) == ["report.md"]          # 靠真信号发出
    assert _item_types(client.sent[0]) == [4]            # 文件先于文本
    assert _text_messages(client) == [reply]


# ============================================== (b) 工作树（项目根之外）本轮新建

def test_b_new_file_in_coder_worktree_is_delivered(tmp_path, monkeypatch):
    """沙箱真实情形：agent 写在 **coder 工作树**里，文件在项目根之外。"""
    root = _root(tmp_path)
    worktree = tmp_path / "sandbox-worktree"
    worktree.mkdir()

    async def dispatch(a, c, t):
        (worktree / "budget.xlsx").write_text("col\n1\n", encoding="utf-8")
        return "好的，已生成，请查收。"

    client = _UploadClient()
    _run_handle(tmp_path, dispatch=dispatch, roots=[str(root), str(worktree)],
                client=client, monkeypatch=monkeypatch, poster=_Poster())

    assert _file_names(client) == ["budget.xlsx"]        # 工作树里的文件也能发
    assert _text_messages(client) == ["好的，已生成，请查收。"]


# ============================================== (c) 忽略目录 / 文件

def test_c_changes_in_ignored_dirs_and_files_are_not_delivered(tmp_path,
                                                               monkeypatch):
    root = _root(tmp_path)

    async def dispatch(a, c, t):
        for rel in (".git/config", "node_modules/pkg/index.js",
                    "attachments/upload.pdf", "runs/run-1.json",
                    ".kairos-worktrees/x/y.txt", "scratch.tmp", ".vet.note.pyc"):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")
        return "好的，已生成，请查收。"

    client = _UploadClient()
    _run_handle(tmp_path, dispatch=dispatch, roots=[str(root)],
                client=client, monkeypatch=monkeypatch, poster=_Poster())

    assert _file_names(client) == []                     # 一个都没发
    assert client.upload_calls == []                     # 也没尝试上传
    assert _text_messages(client) == ["好的，已生成，请查收。"]


# ============================================== (d) 敏感内容嗅探

def test_d_sensitive_content_file_is_refused_but_text_ok(tmp_path, monkeypatch):
    root = _root(tmp_path)
    # 一行**长得像真密钥**的内容（sk- 后接 ≥16 位），会被 sentinel.redact 改写 →
    # _refuse_to_send 必须拒发。（注意：本字符串在工具回显里可能被脱敏，属正常。）
    secret_line = "配置里那行是 sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2"

    async def dispatch(a, c, t):
        (root / "report.md").write_text(secret_line + "\n", encoding="utf-8")
        return "好的，已生成，请查收。"

    # 先自证：这份内容确实触发内容嗅探（否则测试本身没有意义）。
    probe = root / "probe.txt"
    probe.write_text(secret_line + "\n", encoding="utf-8")
    assert wx._refuse_to_send(probe) is True

    client = _UploadClient()
    _run_handle(tmp_path, dispatch=dispatch, roots=[str(root)],
                client=client, monkeypatch=monkeypatch, poster=_Poster())

    assert _file_names(client) == []                     # 敏感内容 → 不发
    assert client.upload_calls == []
    assert _text_messages(client) == ["好的，已生成，请查收。"]   # 文本照发


# ============================================== (e) 名字黑名单

def test_e_never_send_names_are_not_delivered(tmp_path, monkeypatch):
    assert ".env" in WEIXIN_NEVER_SEND_NAMES
    root = _root(tmp_path)

    async def dispatch(a, c, t):
        (root / ".env").write_text("FOO=bar\n", encoding="utf-8")
        return "好的，已生成，请查收。"

    client = _UploadClient()
    _run_handle(tmp_path, dispatch=dispatch, roots=[str(root)],
                client=client, monkeypatch=monkeypatch, poster=_Poster())

    assert _file_names(client) == []
    assert client.upload_calls == []
    assert _text_messages(client) == ["好的，已生成，请查收。"]


# ============================================== (f) best-effort：不抛、不改文本

def test_f_no_candidates_text_unchanged(tmp_path, monkeypatch):
    root = _root(tmp_path)

    async def dispatch(a, c, t):
        return "没有任何产出，就是聊两句。"

    client = _UploadClient()
    reply = _run_handle(tmp_path, dispatch=dispatch, roots=[str(root)],
                        client=client, monkeypatch=monkeypatch, poster=_Poster())
    assert reply == "没有任何产出，就是聊两句。"
    assert client.upload_calls == []
    assert len(client.sent) == 1


def test_f_no_root_text_unchanged(tmp_path, monkeypatch):
    root = _root(tmp_path)

    async def dispatch(a, c, t):
        (root / "report.md").write_text("x", encoding="utf-8")
        return "好的，已生成，请查收。"

    client = _UploadClient()
    reply = _run_handle(tmp_path, dispatch=dispatch, roots=[],
                        client=client, monkeypatch=monkeypatch, poster=_Poster())
    assert reply == "好的，已生成，请查收。"
    assert _file_names(client) == []
    assert len(client.sent) == 1


def test_f_upload_failure_falls_back_to_text_once(tmp_path, monkeypatch):
    root = _root(tmp_path)

    async def dispatch(a, c, t):
        (root / "report.md").write_text("data", encoding="utf-8")
        return "好的，已生成，请查收。"

    client = _UploadClient(upload_error=RuntimeError("boom-getuploadurl"))
    _run_handle(tmp_path, dispatch=dispatch, roots=[str(root)],
                client=client, monkeypatch=monkeypatch, poster=_Poster())

    assert _file_names(client) == []
    assert _text_messages(client) == ["好的，已生成，请查收。"]   # 文本不丢不重


# ============================================== (g) 快照差集 / 去重 / fallback

def test_g_diff_detects_new_mtime_and_size_changes(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    f = root / "a.txt"
    f.write_text("one", encoding="utf-8")

    base = snapshot_tree_files([root])
    # 未变动 → 空差集
    assert diff_touched_files(base, snapshot_tree_files([root])) == []

    # 新增文件
    (root / "b.txt").write_text("new", encoding="utf-8")
    after = snapshot_tree_files([root])
    assert [p.name for p in diff_touched_files(base, after)] == ["b.txt"]

    # 仅 mtime 变化（用 os.utime 显式改，避开文件系统 mtime 粒度）
    before_m = after
    os.utime(f, (9999999999, 9999999999))
    after_m = snapshot_tree_files([root])
    assert [p.name for p in diff_touched_files(before_m, after_m)] == ["a.txt"]

    # 仅 size 变化（改内容后把 mtime 还原成上一拍的 mtime）
    prev_mtime = after_m[os.path.realpath(f)][0]
    before_s = after_m
    f.write_text("one and more text", encoding="utf-8")
    os.utime(f, (prev_mtime, prev_mtime))
    after_s = snapshot_tree_files([root])
    assert [p.name for p in diff_touched_files(before_s, after_s)] == ["a.txt"]


def test_g_snapshot_ignores_noisy_dirs_and_files(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("x", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "m.js").write_text("x", encoding="utf-8")
    (root / "attachments").mkdir()
    (root / "attachments" / "in.pdf").write_text("x", encoding="utf-8")
    (root / "runs").mkdir()
    (root / "runs" / "r.json").write_text("x", encoding="utf-8")
    (root / "report.tmp").write_text("x", encoding="utf-8")
    (root / ".kairos-lock").write_text("x", encoding="utf-8")
    (root / "good.md").write_text("x", encoding="utf-8")

    names = {Path(p).name for p in snapshot_tree_files([root])}
    assert names == {"good.md"}


def test_g_unchanged_old_file_not_touched_even_if_mentioned(tmp_path, monkeypatch):
    """旧文件（本轮没改）不该被当成「本轮产出」——但文本 fallback 仍会把它发出。"""
    root = _root(tmp_path)
    (root / "notes.md").write_text("旧笔记\n", encoding="utf-8")   # 本轮之前就存在

    async def dispatch(a, c, t):
        return "请参考 notes.md。"       # 只提到、没改动它

    client = _UploadClient()
    _run_handle(tmp_path, dispatch=dispatch, roots=[str(root)],
                client=client, monkeypatch=monkeypatch, poster=_Poster())

    # fallback（文本启发式）命中旧文件 → 仍然发出，证明保留了这条兜底
    assert _file_names(client) == ["notes.md"]


def test_g_touched_and_heuristic_merge_dedupes_with_stable_order(tmp_path,
                                                                 monkeypatch):
    root = _root(tmp_path)
    (root / "notes.md").write_text("旧笔记\n", encoding="utf-8")   # 本轮之前就存在

    async def dispatch(a, c, t):
        (root / "report.md").write_text("# 新报告\n结论：通过。\n", encoding="utf-8")
        # 文本里**同时**点名了新产出的 report.md 与旧文件 notes.md
        return "已生成 report.md，另外 notes.md 也参考一下。"

    client = _UploadClient()
    _run_handle(tmp_path, dispatch=dispatch, roots=[str(root)],
                client=client, monkeypatch=monkeypatch, poster=_Poster())

    names = _file_names(client)
    assert names[0] == "report.md"          # 真信号在前
    assert "notes.md" in names              # fallback 补旧文件
    assert names.count("report.md") == 1    # 与真信号去重，只发一次


def test_g_merge_candidates_caps_and_dedupes():
    a = [Path("/x/a"), Path("/x/b")]
    b = [Path("/x/b"), Path("/x/c"), Path("/x/d")]
    merged = merge_sendable_candidates(a, b, max_files=3)
    assert merged == [Path("/x/a"), Path("/x/b"), Path("/x/c")]


# ============================================== 可观测：候选 / 实发各一条日志

def test_candidate_and_sent_are_logged(tmp_path, monkeypatch, caplog):
    root = _root(tmp_path)

    async def dispatch(a, c, t):
        (root / "report.md").write_text("# ok\n", encoding="utf-8")
        return "好的，已生成，请查收。"

    client = _UploadClient()
    with caplog.at_level(logging.INFO, logger="kairos.weixin_ilink"):
        _run_handle(tmp_path, dispatch=dispatch, roots=[str(root)],
                    client=client, monkeypatch=monkeypatch, poster=_Poster())

    joined = "\n".join(r.message for r in caplog.records)
    assert "出站文件候选" in joined
    assert "出站文件实发" in joined


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
