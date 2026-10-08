"""CI 守卫：仓库里每个「把用户消息交给 coder.chat()」的调用点都必须能解释清楚。

这个测试**不 import FastAPI 应用、也不 import 被测模块**——纯源码扫描
（``ast``），所以既慢不动、也不会因为应用结构变化而碎掉；同时它用的是
``ast`` 而不是正则，所以只有**真正的调用**会被算进来，注释/文档字符串里
的 ``coder.chat()`` 不会误报。

判定规则（每个 ``.chat(`` 调用点必须命中两类之一）:

1. **已过路由**：调用点所在的**最内层函数**（含其上方 import/判定/回退）里
   出现过 ``route_task(...)``。即这条入口已经过 :mod:`kairos.task_router`
   判定车道——例如 ``api/routes/projects.py:chat``、``api/routes/im.py:_answer_inbound``、
   ``api/routes/weixin.py:_answer_message``、``api/routes/wecom.py:_answer_message``。
   （这些函数里既有「走 Coder」的分支，也有「走通用车道」的分支和「通用车道
   失败回退 Coder」的分支，路由判定都在同一函数内，故整体算「已过路由」。）
2. **豁免白名单**：否则必须登记在下面的 ``ROUTE_EXEMPT`` 里，且**每条必须写
   一行理由**（无理由的条目由 ``test_every_whitelist_entry_has_a_reason`` 判失败）。

出现新的、既没路由也不在名单里的 ``.chat(`` 调用点，本测试失败并指名 文件:行。
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("api", "kairos")

#: 非聊天入口：这里的 ``.chat()`` 是别的 SDK 客户端方法，不是 Coder 车道。
#: 每条必须带一行理由（``test_every_excluded_entry_has_a_reason`` 会检查）。
EXCLUDED = {
    "kairos/providers/ollama_provider.py": (
        "这是 ollama 客户端 SDK 的 client.chat(...)——直接打本地模型端点，"
        "与「把用户消息交给 Coder」无关，不是本守卫要管的入口。"
    ),
}

#: 故意直连 Coder、**不**走闲聊路由的调用点白名单。
#: 每条必须带一行 ``reason``，否则测试失败；``count`` 是该 (路径, 函数) 下
#: 允许出现的直连 ``.chat(`` 个数，多一个就会被指名报错。
ROUTE_EXEMPT: List[dict] = [
    {
        "path": "api/routes/p2_features.py",
        "func": "coder_harness",
        "count": 1,
        "reason": (
            "把一条排队的 agent task（长任务 / harness 基准）交给 Coder 执行，"
            "语义上本就属于 Coder 车道；路由器把「编码意图 / 长任务」判到 Coder "
            "正是为服务这类工作，再路由一次只是多一次 workspace 扫描、不改变结果。"
            "理由与调用点上方的注释一致。"
        ),
    },
]


# ------------------------------------------------------------------- 数据结构

@dataclass(frozen=True)
class CallSite:
    path: str          # 相对仓库根的 posix 路径
    line: int
    func: str          # 最内层函数名（模块级为 "<module>"）
    routed: bool       # 所在函数是否出现 route_task(...)
    text: str          # 该行源码（给人看的）


# ------------------------------------------------------------------- 源码扫描

def _iter_py_files(root: Path = REPO_ROOT) -> List[Path]:
    files: List[Path] = []
    for d in SCAN_DIRS:
        base = Path(root) / d
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    return files


def _func_routes(fn_src: str) -> bool:
    """函数源码里是否调用了 ``route_task(...)``。"""
    return re.search(r"\broute_task\s*\(", fn_src) is not None


def _find_chat_calls(
    tree: ast.AST, lines: Sequence[str]
) -> List[tuple]:
    """返回 ``[(lineno, funcname, routed, source_line), ...]``（只含真实调用）。"""
    results: List[tuple] = []

    def _fn_source(fn: ast.AST) -> str:
        start = fn.lineno - 1
        end = getattr(fn, "end_lineno", fn.lineno)
        return "\n".join(lines[start:end])

    def _visit(node: ast.AST, stack: List[ast.AST]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _visit(child, stack + [child])
                continue
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "chat"
            ):
                fn = stack[-1] if stack else None
                funcname = getattr(fn, "name", "<module>") if fn else "<module>"
                routed = _func_routes(_fn_source(fn)) if fn else False
                text = ""
                if 0 < child.lineno <= len(lines):
                    text = lines[child.lineno - 1].strip()
                results.append((child.lineno, funcname, routed, text))
            _visit(child, stack)

    _visit(tree, [])
    return results


def scan_call_sites(files: Optional[Iterable[Path]] = None,
                    root: Path = REPO_ROOT) -> List[CallSite]:
    """扫描 ``.chat(`` 调用点。``files``/``root`` 可注入，便于测试注入假文件。"""
    root = Path(root).resolve()
    targets = list(files) if files is not None else _iter_py_files(root)
    out: List[CallSite] = []
    for py in targets:
        py = Path(py)
        try:
            rel = py.resolve().relative_to(root).as_posix()
            source = py.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel)
        except (OSError, SyntaxError, ValueError):
            continue
        lines = source.splitlines()
        for lineno, funcname, routed, text in _find_chat_calls(tree, lines):
            out.append(CallSite(rel, lineno, funcname, routed, text))
    return out


def collect_violations(
    sites: Sequence[CallSite],
    exempt: Sequence[dict] = ROUTE_EXEMPT,
    excluded: dict = EXCLUDED,
    check_stale: bool = True,
) -> List[str]:
    """返回人可读的失败信息列表（空 = 通过）。

    ``check_stale=False`` 供注入假文件的自验证用例使用：那时扫描集里本来
    就不含真实白名单对应的调用点，不该被当成「陈旧条目」。
    """
    problems: List[str] = []
    whitelist = {(e["path"], e["func"]): e for e in exempt}

    grouped: "defaultdict[tuple, list]" = defaultdict(list)
    for s in sites:
        if s.path in excluded:
            continue
        if s.routed:
            continue
        grouped[(s.path, s.func)].append(s)

    for (path, func), group in sorted(grouped.items()):
        group = sorted(group, key=lambda x: x.line)
        lines = ", ".join(str(s.line) for s in group)
        entry = whitelist.get((path, func))
        if entry is None:
            for s in group:
                problems.append(
                    f"{s.path}:{s.line} —— 函数 {s.func}() 直连 coder.chat()，"
                    f"既没有在同一函数里调用 route_task()，也不在 ROUTE_EXEMPT "
                    f"白名单里。请让这条入口先过路由，或把它登记进白名单并写明理由。"
                )
        elif int(entry.get("count", -1)) != len(group):
            problems.append(
                f"{path} —— 函数 {func}() 白名单登记 count={entry.get('count')}，"
                f"实际发现 {len(group)} 个直连 .chat()（行 {lines}）：多出来的入口"
                f"必须补 route_task() 或更新白名单并写明理由。"
            )

    seen = set(grouped.keys())
    if check_stale:
        for (path, func) in sorted(whitelist.keys()):
            if (path, func) not in seen:
                problems.append(
                    f"{path} —— 白名单条目 {func}() 之下已不存在直连 .chat()，"
                    f"请删除该条（它要么已被路由、要么代码已挪走）。"
                )
    return problems


# ----------------------------------------------------------------------- 测试

def test_no_unrouted_chat_entrances():
    """核心守卫：任何未过路由、又不在带理由白名单里的 .chat() 入口 → 失败。"""
    sites = scan_call_sites()
    problems = collect_violations(sites)
    assert not problems, (
        "发现未解释的 coder.chat() 入口（判定规则见本文件顶部）:\n  "
        + "\n  ".join(problems)
    )


def test_the_scan_actually_sees_the_known_entrances():
    """守卫非空转：至少要给到已知的几个入口，否则扫描逻辑本身坏了。"""
    sites = scan_call_sites()
    assert sites, "扫描一个 .chat() 都没抓到——扫描逻辑坏了"
    routed = {(s.path, s.func) for s in sites if s.routed}
    for expected in (
        ("api/routes/projects.py", "chat"),
        ("api/routes/im.py", "_answer_inbound"),
        ("api/routes/weixin.py", "_answer_message"),
        ("api/routes/wecom.py", "_answer_message"),
    ):
        assert expected in routed, f"没把 {expected[0]}:{expected[1]}() 认成已过路由入口"
    assert any(s.path == "api/routes/p2_features.py"
               and s.func == "coder_harness" for s in sites), \
        "没抓到 p2_features.coder_harness() 的直连 .chat()"


def test_every_whitelist_entry_has_a_reason():
    """白名单条目必须写明理由；无理由（或太敷衍）的条目 → 失败。"""
    for entry in ROUTE_EXEMPT:
        reason = str(entry.get("reason") or "").strip()
        where = f"{entry.get('path')}:{entry.get('func')}"
        assert reason, f"ROUTE_EXEMPT 条目缺 reason: {where}"
        assert len(reason) >= 12, f"ROUTE_EXEMPT 条目的 reason 太短、看不出理由: {where}"
        assert "count" in entry, f"ROUTE_EXEMPT 条目缺 count: {where}"


def test_every_excluded_entry_has_a_reason():
    for path, reason in EXCLUDED.items():
        assert str(reason or "").strip(), f"EXCLUDED 条目缺理由: {path}"


def test_the_guard_flags_a_new_direct_coder_call(tmp_path):
    """自验证：往临时目录塞一个直连 .chat() 的新入口，守卫必须报警并指名 文件:行。"""
    probe = tmp_path / "api" / "routes" / "probe.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "async def sneaky_handler(project, text):\n"
        "    return await project.coder.chat(text)\n",
        encoding="utf-8",
    )
    sites = scan_call_sites([probe], root=tmp_path)
    assert len(sites) == 1, sites
    assert sites[0].routed is False
    problems = collect_violations(sites, check_stale=False)
    assert problems, "守卫没有报出这个新的直连入口"
    assert "api/routes/probe.py:2" in problems[0], problems


def test_the_guard_accepts_a_routed_call(tmp_path):
    """正面对照：同一形状、但函数里过了 route_task 的入口，不该被报。"""
    probe = tmp_path / "api" / "routes" / "probe.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "async def good_handler(project, text):\n"
        "    from kairos.task_router import route_task\n"
        "    decision = route_task(requirement=text, workspace=None)\n"
        "    if decision.uses_skeleton:\n"
        "        return 'general lane'\n"
        "    return await project.coder.chat(text)\n",
        encoding="utf-8",
    )
    sites = scan_call_sites([probe], root=tmp_path)
    assert sites and sites[0].routed is True
    assert collect_violations(sites, check_stale=False) == []


def test_comments_and_docstrings_do_not_count(tmp_path):
    """ast 扫描只认真实调用：注释/字符串里的 coder.chat() 不算入口。"""
    probe = tmp_path / "api" / "routes" / "probe.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        '"""doc mentions coder.chat() and client.chat()."""\n'
        "# a comment with coder.chat()\n"
        "NOTE = 'text also says coder.chat()'\n",
        encoding="utf-8",
    )
    assert scan_call_sites([probe], root=tmp_path) == []


if __name__ == "__main__":  # pragma: no cover - 手跑时给人看的报告
    _problems = collect_violations(scan_call_sites())
    print("\n".join(_problems) if _problems else "OK: 所有 .chat() 入口都已解释")
    raise SystemExit(1 if _problems else 0)
