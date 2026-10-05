"""The approval-mode switch must reach the gate that actually decides.

``approval_mode`` used to be written by the settings endpoint and read by
nobody: ``get_sentinel()`` always built a SUGGEST gate, so choosing "full-auto"
in the UI changed nothing about what the agent asked for. These tests pin the
wire from both ends — the stored value reaches the gate, and the gate still
refuses what it must refuse.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.routes.projects as projects_route
from api.routes.p2_features import SetApprovalBody, get_approval_mode, set_approval_mode
from kairos.approval import ApprovalMode, MODE_ORDER
from kairos.core.orchestrator import ProjectRuntime
from kairos.permissions import Decision, PermissionPolicy, PermissionRule
from kairos.sentinel import Sentinel, get_sentinel, reset_sentinel

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_gate():
    reset_sentinel()
    yield
    reset_sentinel()


def _gate(mode: str = "suggest", policy: PermissionPolicy | None = None) -> Sentinel:
    return Sentinel(policy=policy, mode=ApprovalMode.parse(mode),
                    enabled=True, strict=False)


# ------------------------------------------------------------- the gate itself

def test_the_default_gate_still_asks():
    """Zero change for anyone who never touches the setting."""
    gate = _gate()
    assert gate.mode is ApprovalMode.SUGGEST
    assert gate._ladder("terminal", "ls -la")[0] is Decision.ASK


def test_setting_the_mode_reaches_the_decision():
    gate = _gate()
    gate.set_mode("full-auto")

    decision, reason = gate._ladder("terminal", "ls -la")

    assert decision is Decision.ALLOW
    assert "full-auto" in reason


def test_an_explicit_deny_survives_full_auto():
    """`full-auto` means "nothing asks", not "nothing is refused"."""
    policy = PermissionPolicy(rules=[
        PermissionRule("terminal", "*", Decision.DENY),
    ])
    gate = _gate("full-auto", policy=policy)

    decision, reason = gate._ladder("terminal", "rm -rf /")

    assert decision is Decision.DENY
    assert "denied" in reason


def test_edit_mode_still_asks_before_writing():
    gate = _gate("edit")
    assert gate._ladder("file_read", "README.md")[0] is Decision.ALLOW
    assert gate._ladder("file_write", "README.md")[0] is Decision.ASK


def test_an_unknown_value_falls_back_to_the_safest_mode():
    gate = _gate("full-auto")
    gate.set_mode("definitely-not-a-mode")
    assert gate.mode is ApprovalMode.SUGGEST


def test_the_documented_alias_still_works():
    gate = _gate()
    gate.set_mode("yolo")
    assert gate.mode is ApprovalMode.FULL_AUTO


# ------------------------------------------------------- the process ceiling

def test_a_project_cannot_loosen_the_process_ceiling(monkeypatch):
    monkeypatch.setenv("KAIROS_APPROVAL_MODE", "suggest")

    gate = _gate("full-auto")            # as if the UI said full-auto
    gate.set_mode("full-auto")

    assert gate.mode is ApprovalMode.SUGGEST


def test_a_stricter_project_is_left_alone(monkeypatch):
    monkeypatch.setenv("KAIROS_APPROVAL_MODE", "full-auto")

    gate = _gate("suggest")
    gate.set_mode("suggest")

    assert gate.mode is ApprovalMode.SUGGEST
    assert gate._ladder("terminal", "ls")[0] is Decision.ASK


def test_no_ceiling_means_the_project_decides(monkeypatch):
    monkeypatch.delenv("KAIROS_APPROVAL_MODE", raising=False)
    gate = _gate("suggest")
    gate.set_mode("full-auto")
    assert gate.mode is ApprovalMode.FULL_AUTO


def test_the_order_is_strictest_first():
    assert MODE_ORDER == (ApprovalMode.SUGGEST, ApprovalMode.EDIT,
                          ApprovalMode.FULL_AUTO)


# ------------------------------------------------------------------ the API

class _FakeOrch:
    def __init__(self):
        self.project = SimpleNamespace(id="p1", metadata={},
                                       runtime=ProjectRuntime())

    def get_project(self, project_id):
        return self.project if project_id == "p1" else None


@pytest.fixture
def fake_orch(monkeypatch):
    fake = _FakeOrch()
    monkeypatch.setattr(projects_route, "_orch", lambda: fake)
    return fake


def test_the_endpoint_pushes_the_mode_into_the_running_gate(fake_orch):
    out = asyncio.run(set_approval_mode("p1", SetApprovalBody(mode="full-auto")))

    assert out["effective"] == "full-auto"
    assert get_sentinel().mode is ApprovalMode.FULL_AUTO
    # …and the choice has a home that survives a restart.
    assert fake_orch.project.metadata["approval_mode"] == "full-auto"


def test_the_endpoint_reports_the_record_not_the_mirror(fake_orch):
    """Before attach the runtime mirror is empty; the record still answers."""
    fake_orch.project.metadata["approval_mode"] = "edit"

    out = asyncio.run(get_approval_mode("p1"))

    assert out["mode"] == "edit"
    assert "Edit" in out["label"]


def test_the_default_project_reads_as_suggest(fake_orch):
    out = asyncio.run(get_approval_mode("p1"))
    assert out["mode"] == "suggest"


def test_the_attach_path_applies_the_stored_mode():
    """Source check: attach is the only place that knows which project this
    process is serving, so that is where the gate has to learn the mode."""
    src = (ROOT / "kairos" / "core" / "orchestrator.py").read_text(
        encoding="utf-8", errors="replace")
    assert "set_mode(stored)" in src
    assert 'metadata.get("approval_mode")' in src
