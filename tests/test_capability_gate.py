"""P0-6 -- the capability gate, proved by the ways out.

Six families of reverse tests, each a way a tool could do something it never
declared: escape its root with a path, reach an internal network, arrive
undeclared, run a capability it needs approval for without approval -- plus a
proof that the tools that already exist behave exactly as they did before.

The gate is fail-closed: an undeclared tool is refused, not waved through.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kairos.approval import ApprovalMode
from kairos.capabilities import (
    Capability,
    assess,
    audit,
    CapabilityVerdict,
    fence_path,
    register_tool_capabilities,
    requires_approval,
    resolve_capabilities,
    runtime_capabilities,
    unregister_tool_capabilities,
)
from kairos.sentinel import Sentinel, SentinelAudit
from kairos.tools.base import BaseTool, ToolResult, resolve_within_root
from kairos.tools.browser_tool import BrowserTool
from kairos.tools.file_edit import FileEditTool
from kairos.tools.file_read import FileReadTool
from kairos.tools.terminal import TerminalTool
from kairos.tools.webfetch import WebFetchTool


AUDIT_ENV = "KAIROS_CAPABILITY_AUDIT_DIR"


@pytest.fixture(autouse=True)
def _no_env_ceiling(monkeypatch):
    """Tests set the mode explicitly; the process ceiling must not interfere."""
    monkeypatch.delenv("KAIROS_APPROVAL_MODE", raising=False)


@pytest.fixture
def cap_audit(tmp_path, monkeypatch):
    """Point the capability audit trail at a throwaway file."""
    d = tmp_path / "cap-audit"
    monkeypatch.setenv(AUDIT_ENV, str(d))
    return d / "capabilities.jsonl"


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "b.txt").write_text("nested", encoding="utf-8")
    return root


def _link_out_of_root(ws: Path, name: str, outside: Path) -> Path:
    """Create a real on-disk link inside ``ws`` that resolves to ``outside``.

    Tries a symlink first (POSIX, and Windows with developer mode / the
    create-symlink privilege). Windows *without* that privilege still allows a
    directory **junction** -- a genuine reparse point that ``Path.resolve``
    follows exactly like a symlink -- so the "link out of the root" escape is
    exercised on either platform, not skipped.
    """
    link = ws / name
    try:
        link.symlink_to(outside, target_is_directory=outside.is_dir())
        return link
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt" and outside.is_dir():
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True)
        if proc.returncode == 0 and link.exists():
            return link
    pytest.skip("no symlink/junction could be created on this platform")


# ===========================================================================
# (1) Path fence -- a file tool must not leave its root
# ===========================================================================


async def test_parent_traversal_out_of_root_is_refused(ws, cap_audit):
    (ws.parent / "outside.txt").write_text("secret", encoding="utf-8")
    tool = FileReadTool(allowed_root=ws)

    res = await tool.execute(path="../outside.txt")

    assert res.success is False
    assert "Path outside project directory" in (res.error or "")


async def test_absolute_path_out_of_root_is_refused(ws, cap_audit):
    outside = ws.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    tool = FileEditTool(allowed_root=ws)

    res = await tool.execute(path=str(outside), content="pwn")

    assert res.success is False
    assert "Path outside project directory" in (res.error or "")


async def test_symlink_out_of_root_is_refused(ws, cap_audit):
    # A real on-disk link inside the root that resolves outside it. On Windows
    # without the create-symlink privilege this is a directory junction -- a
    # genuine reparse point ``Path.resolve`` follows exactly like a symlink.
    outside_dir = ws.parent / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("secret", encoding="utf-8")
    _link_out_of_root(ws, "linked", outside_dir)     # symlink, else junction
    tool = FileReadTool(allowed_root=ws)

    res = await tool.execute(path="linked/secret.txt")

    assert res.success is False
    assert "Path outside project directory" in (res.error or "")
    assert "secret" not in (res.output or "")


def test_fence_resolves_through_dotdot_and_stays_inside(ws):
    # A path that dips out and back in is fine -- and is judged on the
    # *resolved* target, which is why a symlink cannot slip past it.
    assert fence_path("sub/../a.txt", ws) == (ws / "a.txt").resolve()
    with pytest.raises(PermissionError):
        fence_path("sub/../../outside.txt", ws)


def test_shared_path_policy_is_one_implementation(ws, tmp_path):
    # The gate and the tools call the same helper, so they can never disagree.
    from kairos.tools import base as base_mod
    assert base_mod.resolve_within_root is resolve_within_root
    tool = FileReadTool(allowed_root=ws)
    assert tool._resolve_safe("a.txt") == resolve_within_root("a.txt", ws.resolve())


# ===========================================================================
# (2) Network guard -- a network tool must not reach the inside
# ===========================================================================


INTERNAL_URLS = [
    "http://127.0.0.1/",
    "http://127.0.0.1:8080/health",
    "http://10.1.2.3/",
    "http://192.168.0.10/",
    "http://172.16.5.4/",
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://[::1]/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://internal.corp/",
]


@pytest.mark.parametrize("url", INTERNAL_URLS)
async def test_webfetch_refuses_internal_targets(url, cap_audit):
    res = await WebFetchTool().execute(url=url)

    assert res.success is False
    assert "public http(s) URL" in (res.error or "")


@pytest.mark.parametrize("url", INTERNAL_URLS)
def test_gate_refuses_internal_targets_before_the_tool(url, cap_audit):
    # The gate itself -- not merely the tool's own guard -- refuses the target,
    # delegating to the repository's SSRF guard (kairos.netsec) as the single
    # source of truth. Covers loopback, RFC1918 (10/172.16/192.168), the
    # link-local cloud-metadata address, and internal hostnames.
    verdict = assess("webfetch", {"url": url})
    assert verdict.allowed is False
    assert verdict.rule == "network-guard"


async def test_the_cloud_metadata_address_is_refused_before_the_tool(cap_audit):
    # The gate refuses the same call the tool does, with the same message.
    verdict = assess("webfetch", {"url": "http://169.254.169.254/"})
    assert verdict.allowed is False
    assert verdict.rule == "network-guard"


async def test_the_browser_keeps_the_lenient_guard(ws, cap_audit):
    """The browser's own rule survives: metadata is refused, loopback is not."""
    tool = BrowserTool(allowed_root=ws, manager=None)

    res = await tool.execute(action="navigate",
                             url="http://169.254.169.254/latest/meta-data/")

    assert res.success is False
    assert "link-local" in (res.error or "").lower()


async def test_network_guard_has_no_fallback_when_the_guard_is_absent(monkeypatch):
    # No guard available => fail closed, not "let it through".
    import builtins

    import kairos.capabilities as cap
    real_import = builtins.__import__

    def _boom(name, *a, **k):
        if name == "kairos.netsec":
            raise ImportError("netsec unavailable")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    verdict = cap.assess("webfetch", {"url": "https://example.com/"})

    assert verdict.allowed is False
    assert verdict.rule == "network-guard-unavailable"


# ===========================================================================
# (3) Undeclared capability -- fail closed
# ===========================================================================


class _UndeclaredTool(BaseTool):
    """A brand-new tool that declares nothing and is not registered."""

    name = "probe_undeclared_tool"

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, output="ran")


async def test_a_tool_that_declares_nothing_is_refused(cap_audit):
    res = await _UndeclaredTool().execute(anything=1)

    assert res.success is False
    assert res.metadata["capability"]["rule"] == "undeclared-capability"
    assert "declares no capability set" in (res.error or "")


def test_assess_refuses_an_unknown_name():
    verdict = assess("probe_never_registered", {"path": "x"})
    assert verdict.allowed is False
    assert verdict.rule == "undeclared-capability"


def test_registering_a_tool_makes_it_allowed():
    register_tool_capabilities("probe_registered_read", {Capability.READ_FILE})
    try:
        verdict = assess("probe_registered_read", {})
        assert verdict.allowed is True
        assert verdict.rule == "capability-allow"
    finally:
        unregister_tool_capabilities("probe_registered_read")


# ===========================================================================
# (4) Approval -- a capability that needs approval is refused without it
# ===========================================================================


def test_requires_approval_mirrors_the_approval_ladder():
    ext = {Capability.EXTERNAL_WRITE}
    read = {Capability.READ_FILE}
    write = {Capability.WRITE_FILE}
    exec_ = {Capability.EXEC_PROCESS}

    # Reading is always silent.
    assert requires_approval(read, ApprovalMode.SUGGEST)[0] is False
    # A write/external act asks in SUGGEST.
    assert requires_approval(write, ApprovalMode.SUGGEST)[0] is True
    assert requires_approval(ext, ApprovalMode.SUGGEST)[0] is True
    # EDIT auto-allows writes and network reads, still asks for exec/external.
    assert requires_approval(write, ApprovalMode.EDIT)[0] is False
    assert requires_approval(exec_, ApprovalMode.EDIT)[0] is True
    assert requires_approval(ext, ApprovalMode.EDIT)[0] is True
    # FULL_AUTO silences everything that is not explicitly denied.
    assert requires_approval(ext, ApprovalMode.FULL_AUTO)[0] is False


def test_sentinel_asks_for_a_declared_capability_the_ladder_waved_through(
        tmp_path, cap_audit):
    """The name-based ladder says allow; the capability says "ask".

    A tool whose *name* the ladder trusts (here a read-only name) but which
    declares an external write must still get the user's OK -- the name is not
    a permission.
    """
    register_tool_capabilities("file_read", {Capability.EXTERNAL_WRITE})
    try:
        gate = Sentinel(
            mode=ApprovalMode.EDIT, strict=True,
            audit=SentinelAudit(directory=tmp_path / "audit"),
        )

        ruling = gate.authorize("file_read", {"path": "x"})

        assert ruling.denied, "an unapproved external write was allowed"
        assert "capability" in ruling.reason
    finally:
        unregister_tool_capabilities("file_read")


def test_sentinel_allows_the_same_capability_in_full_auto(tmp_path, cap_audit):
    register_tool_capabilities("probe_ext_send_fa", {Capability.EXTERNAL_WRITE})
    try:
        gate = Sentinel(
            mode=ApprovalMode.FULL_AUTO, strict=True,
            audit=SentinelAudit(directory=tmp_path / "audit"),
        )
        ruling = gate.authorize("probe_ext_send_fa", {"to": "x@example.com"})
        assert ruling.allowed
    finally:
        unregister_tool_capabilities("probe_ext_send_fa")


def test_kairos_approval_mode_is_a_ceiling_not_a_default(monkeypatch):
    """Exporting FULL_AUTO must not loosen a project set to SUGGEST."""
    monkeypatch.setenv("KAIROS_APPROVAL_MODE", "full-auto")
    gate = Sentinel(mode=ApprovalMode.SUGGEST)
    assert gate.mode == ApprovalMode.SUGGEST


# ===========================================================================
# (5) Existing tools -- behaviour unchanged, byte for byte
# ===========================================================================


async def test_existing_read_tool_still_reads(ws, cap_audit):
    res = await FileReadTool(allowed_root=ws).execute(path="a.txt")
    assert res.success is True
    assert res.output == "hello"


async def test_existing_write_tool_still_writes(ws, cap_audit):
    res = await FileEditTool(allowed_root=ws).execute(path="new.txt", content="x=1")
    assert res.success is True
    assert (ws / "new.txt").read_text(encoding="utf-8") == "x=1"


async def test_existing_terminal_still_runs(ws, cap_audit):
    res = await TerminalTool(allowed_cwd=str(ws)).execute("echo capability-gate-ok")
    assert res.success is True
    assert "capability-gate-ok" in res.output


async def test_gate_is_a_no_op_on_the_allow_path(ws, cap_audit):
    # When the gate allows a call it returns ``None``, so the tool's own
    # (pre-gate) ``execute`` runs and its result is passed through untouched.
    # ``functools.wraps`` left the original coroutine on ``__wrapped__``.
    tool = FileReadTool(allowed_root=ws)

    gated = await tool.execute(path="a.txt")
    raw = await tool.execute.__wrapped__(tool, path="a.txt")

    assert gated == raw


@pytest.mark.parametrize("path", ["a.txt", "sub/b.txt", "./a.txt", "sub/../a.txt"])
async def test_gated_results_equal_unwrapped_for_a_read_tool(ws, cap_audit, path):
    tool = FileReadTool(allowed_root=ws)
    assert await tool.execute(path=path) == \
        await tool.execute.__wrapped__(tool, path=path)


async def test_gated_results_equal_unwrapped_for_write_and_exec(ws, cap_audit):
    write = FileEditTool(allowed_root=ws)
    assert await write.execute(path="new.txt", content="x=1") == \
        await write.execute.__wrapped__(write, path="new.txt", content="x=1")

    term = TerminalTool(allowed_cwd=str(ws))
    gated = await term.execute("echo hi")
    raw = await term.execute.__wrapped__(term, "echo hi")
    # Compare what a caller observes; ``metadata['duration_s']`` is a timing
    # value and is not part of the behaviour.
    assert (gated.success, gated.output, gated.error) == \
        (raw.success, raw.output, raw.error)


def test_head_resolve_safe_behaviour_is_unchanged_by_the_refactor(tmp_path):
    """The path policy in HEAD and the extracted helper agree, byte for byte.

    Loads the *HEAD* revision of ``kairos/tools/base.py`` in isolation and
    compares its ``_resolve_safe`` against today's shared
    ``resolve_within_root`` across every shape of path -- relative, ``..``,
    absolute-inside, absolute-outside, workspace-prefixed -- proving the
    refactor that extracted the helper preserved behaviour exactly.
    """
    repo_root = Path(__file__).resolve().parents[1]
    try:
        src = subprocess.run(
            ["git", "show", "HEAD:kairos/tools/base.py"],
            cwd=str(repo_root), capture_output=True, check=True,
        ).stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("no git / HEAD revision available")
    mod_path = tmp_path / "_head_base.py"
    mod_path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_head_capability_base", mod_path)
    head = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = head
    spec.loader.exec_module(head)

    class _Concrete(head.BaseTool):
        name = "concrete"
        async def execute(self, **kw):  # pragma: no cover - never called
            return head.ToolResult(success=True, output="")

    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("hi", encoding="utf-8")
    obj = _Concrete(allowed_root=root)
    resolved_root = root.resolve()

    cases = [
        "a.txt", "./a.txt", "sub/../a.txt", "ws/a.txt",
        "../outside.txt", "sub/../../outside.txt",
        str(tmp_path / "outside.txt"), str(root / "a.txt"),
    ]
    for case in cases:
        try:
            old = ("ok", obj._resolve_safe(case))
        except PermissionError as exc:
            old = ("deny", str(exc))
        try:
            new = ("ok", resolve_within_root(case, resolved_root))
        except PermissionError as exc:
            new = ("deny", str(exc))
        assert old == new, f"path policy changed for {case!r}: {old} != {new}"
        # The gate's fence agrees with both.
        if old[0] == "deny":
            with pytest.raises(PermissionError):
                fence_path(case, resolved_root)


async def test_existing_webfetch_scheme_error_is_unchanged(ws, cap_audit):
    # Not an SSRF refusal -- the tool's own "must be http(s)" message survives.
    res = await WebFetchTool().execute(url="ftp://example.com/x")
    assert res.success is False
    assert res.error == "url must be http(s)"


async def test_legacy_tools_are_never_in_the_runtime_registry(ws):
    # Nothing existing declares new capabilities, so the sentinel's ladder for
    # every one of them is exactly what it was.
    for name in ("file_read", "file_write", "terminal", "git", "webfetch",
                 "browser", "computer_use", "checkpoint", "history_search"):
        assert runtime_capabilities(name) is None


def test_legacy_names_resolve_from_the_registry_only():
    assert resolve_capabilities("terminal") == frozenset({Capability.EXEC_PROCESS})
    assert resolve_capabilities("webfetch") == frozenset({Capability.NETWORK})
    assert resolve_capabilities("mcp_github__search") == \
        frozenset({Capability.EXTERNAL_WRITE})
    assert resolve_capabilities("totally_unknown_tool") is None


async def test_the_gate_refusal_matches_the_tools_own_message(ws, cap_audit):
    """The gate refuses with the tool's message, so the change is invisible."""
    outside = ws.parent / "outside.txt"
    outside.write_text("s", encoding="utf-8")
    tool = FileReadTool(allowed_root=ws)
    # What the tool itself would say (its own path resolver).
    try:
        tool._resolve_safe("../outside.txt")
        own = None
    except PermissionError as exc:
        own = str(exc)
    res = await tool.execute(path="../outside.txt")
    assert own is not None
    assert res.error == own


# ===========================================================================
# (6) Audit -- one readable line per allow/deny, with secrets redacted
# ===========================================================================


async def test_every_ruling_is_audited(ws, cap_audit):
    tool = FileReadTool(allowed_root=ws)
    await tool.execute(path="a.txt")            # allow
    await tool.execute(path="../outside.txt")   # deny

    lines = [json.loads(x) for x in cap_audit.read_text(encoding="utf-8").splitlines()]
    results = {ln["result"] for ln in lines}
    assert "allow" in results and "deny" in results
    for ln in lines:
        assert set(("at", "actor", "tool", "capability", "target",
                    "result", "rule", "reason")) <= set(ln)


def test_audit_redacts_credential_shaped_values(cap_audit):
    verdict = CapabilityVerdict(
        tool="webfetch",
        capabilities=frozenset({Capability.NETWORK}),
        target="https://x.test/?token=sk-ABCDEFGHIJKLMNOPQRSTUV",
        allowed=False,
        reason="password=hunter2 was refused",
        rule="network-guard",
    )
    audit(verdict)

    text = cap_audit.read_text(encoding="utf-8")
    assert "sk-ABCDEFGHIJKLMNOPQRSTUV" not in text
    assert "hunter2" not in text
    assert "<redacted>" in text


# ===========================================================================
# (7) The gate is wired at the tool boundary for every tool
# ===========================================================================


def test_every_base_tool_subclass_has_a_wrapped_execute():
    """A new tool cannot reach the world without passing the gate."""
    import kairos.mcp_client  # noqa: F401 - ensure the adapter is imported
    import kairos.tools  # noqa: F401
    from kairos.tools.base import BaseTool as _B

    seen = 0
    stack = [_B]
    while stack:
        cls = stack.pop()
        for sub in cls.__subclasses__():
            stack.append(sub)
            execute = sub.__dict__.get("execute")
            if execute is not None:
                assert getattr(execute, "__capability_wrapped__", False), \
                    f"{sub.__name__}.execute is not routed through the gate"
                seen += 1
    assert seen >= 15


async def test_read_cache_is_scoped_to_the_project_root(tmp_path):
    """同一相对路径在两个项目里是**不同文件**。

    ToolResult 缓存（kairos/tools/cache.py）是**进程级单例**：循环每轮清一次，闲聊
    车道以前从不清。而 ``file_read`` 的键只用传入的 ``path``（不含根）⇒ 项目 A 读
    ``a.txt`` 之后，项目 B 读 ``a.txt`` 会拿到 **A 的内容**（跨项目读取泄漏）。这正是
    本地全量能复现、而 CI 逐文件分片跑所以从来没红过的那类问题。
    """
    from kairos.tools.file_read import FileReadTool

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "a.txt").write_text("AAA-content", encoding="utf-8")
    (root_b / "a.txt").write_text("BBB-content", encoding="utf-8")

    from_a = await FileReadTool(allowed_root=root_a).execute(path="a.txt")
    from_b = await FileReadTool(allowed_root=root_b).execute(path="a.txt")
    assert from_a.output == "AAA-content"
    assert from_b.output == "BBB-content", (
        "同一个相对路径在不同项目里读到了同一份内容 —— 缓存键丢了根"
    )


def test_the_general_lane_starts_its_turn_with_a_clean_cache():
    """车道每一轮（= 一次聊天）开始时必须清缓存，别把上一轮/别的项目的读带进来。"""
    import inspect

    from kairos.skeleton import read_tools

    src = inspect.getsource(read_tools.run_read_tool_loop)
    assert "clear_round" in src, "通用车道的读工具环没有清进程级缓存"
