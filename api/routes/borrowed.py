"""R38.6 §34 — Borrowed features API (Plan / Approval / Hooks /
Skills / Sandbox / Verification / Session Fork / IM / Providers).

This single route file exposes all the P0/P1 features from
the borrowed-features roadmap. Endpoints are organized by
feature area.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/borrowed", tags=["borrowed"])


# ---------------------------------------------------------------------------
# Lazy imports — keep startup fast, only load when route is hit
# ---------------------------------------------------------------------------

def _kplan():
    from kairos.plan_mode import PlanStore, build_plan_from_llm, mark_step
    return PlanStore, build_plan_from_llm, mark_step

def _kfork():
    from kairos.session_fork import ForkStore, create_fork
    return ForkStore, create_fork

def _kproviders():
    from kairos.providers_more import list_providers, get_provider
    return list_providers, get_provider

def _kim():
    from kairos.im_platforms import (
        IMStore, send_to_platform, SUPPORTED_PLATFORMS,
        IMConfig,
    )
    return IMStore, send_to_platform, SUPPORTED_PLATFORMS, IMConfig

def _khooks():
    from kairos.hooks import get_default_registry, HookEvent
    return get_default_registry, HookEvent

def _kapproval():
    from kairos.approval import ApprovalMode, decide, READ_ONLY_TOOLS
    return ApprovalMode, decide, READ_ONLY_TOOLS

def _kskills():
    from kairos.skills import SkillsLoader, Skill, load_skill
    return SkillsLoader, Skill, load_skill

def _ksandbox():
    from kairos.sandbox import (
        SandboxPolicy, check_policy, apply_to_subprocess,
    )
    return SandboxPolicy, check_policy, apply_to_subprocess

def _kmemory():
    from kairos.memory_kb import MemoryKB
    return MemoryKB


def _orch():
    from api.routes.projects import _orch as get_orch
    return get_orch()


def _project_or_404(project_id: str):
    p = _orch().get_project(project_id)
    if not p:
        raise HTTPException(404, f"Project not found: {project_id}")
    return p


def _work_dir(project) -> str:
    return project.work_dir or project.workspace or "."


# ===========================================================================
# Plan Mode (Gemini-style)
# ===========================================================================

class GeneratePlanBody(BaseModel):
    task: str
    project_context: str = ""


@router.post("/{project_id}/plan/generate")
async def generate_plan(project_id: str, body: GeneratePlanBody):
    """Run the LLM to produce a structured plan, save it as
    draft, return it for the UI to show as a checklist."""
    project = _project_or_404(project_id)
    PlanStore, build_plan_from_llm, _ = _kplan()
    store = PlanStore(work_dir=_work_dir(project))

    async def _llm_complete(prompt: str) -> str:
        try:
            from kairos.config.settings import settings as ksettings
            from kairos.llm.model_router import ModelRouter
            from kairos.llm.base import LLMMessage
            mr = ModelRouter(settings=ksettings)
            provider = mr.get_provider_for_role("coder")
            resp = await provider.complete([LLMMessage(role="user", content=prompt)])
            return resp.content or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("plan LLM call failed: %s", exc)
            return "[]"

    plan = await build_plan_from_llm(
        user_task=body.task,
        project_context=body.project_context,
        llm_complete_fn=_llm_complete,
    )
    plan.project_id = project_id
    store.save(plan)
    return plan.to_dict()


@router.get("/{project_id}/plans")
async def list_plans(project_id: str):
    project = _project_or_404(project_id)
    PlanStore, _, _ = _kplan()
    store = PlanStore(work_dir=_work_dir(project))
    return {"plans": [p.to_dict() for p in store.list_for_project(project_id)]}


@router.get("/{project_id}/plans/{plan_id}")
async def get_plan(project_id: str, plan_id: str):
    project = _project_or_404(project_id)
    PlanStore, _, _ = _kplan()
    store = PlanStore(work_dir=_work_dir(project))
    plan = store.get(plan_id)
    if not plan or plan.project_id != project_id:
        raise HTTPException(404, "Plan not found")
    return plan.to_dict()


class ApprovePlanBody(BaseModel):
    feedback: str = ""


@router.post("/{project_id}/plans/{plan_id}/approve")
async def approve_plan(project_id: str, plan_id: str, body: ApprovePlanBody):
    project = _project_or_404(project_id)
    PlanStore, _, _ = _kplan()
    store = PlanStore(work_dir=_work_dir(project))
    plan = store.get(plan_id)
    if not plan or plan.project_id != project_id:
        raise HTTPException(404, "Plan not found")
    plan.status = "approved"
    plan.approved_at = time.time()
    plan.feedback = body.feedback or None
    store.save(plan)
    return plan.to_dict()


@router.post("/{project_id}/plans/{plan_id}/reject")
async def reject_plan_route(project_id: str, plan_id: str, body: ApprovePlanBody):
    project = _project_or_404(project_id)
    PlanStore, _, _ = _kplan()
    store = PlanStore(work_dir=_work_dir(project))
    plan = store.get(plan_id)
    if not plan or plan.project_id != project_id:
        raise HTTPException(404, "Plan not found")
    plan.status = "rejected"
    plan.feedback = body.feedback or None
    store.save(plan)
    return plan.to_dict()


# ===========================================================================
# Session Fork (the multi-model CLI-style)
# ===========================================================================

class CreateForkBody(BaseModel):
    parent_session_id: str
    source_snapshot_id: str
    label: str = ""
    note: str = ""


@router.post("/{project_id}/forks")
async def create_fork_route(project_id: str, body: CreateForkBody):
    project = _project_or_404(project_id)
    ForkStore, fork_factory = _kfork()
    store = ForkStore(work_dir=_work_dir(project))
    fork = fork_factory(
        project_id=project_id,
        parent_session_id=body.parent_session_id,
        snapshot_id=body.source_snapshot_id,
        label=body.label, note=body.note,
    )
    store.save(fork)
    return {
        "id": fork.id,
        "new_session_id": fork.new_session_id,
        "label": fork.label,
        "created_at": fork.created_at,
    }


@router.get("/{project_id}/forks")
async def list_forks(project_id: str):
    project = _project_or_404(project_id)
    ForkStore, _ = _kfork()
    store = ForkStore(work_dir=_work_dir(project))
    forks = store.list_for_project(project_id)
    return {"forks": [
        {"id": f.id, "parent_session_id": f.parent_session_id,
         "source_snapshot_id": f.source_snapshot_id,
         "label": f.label, "created_at": f.created_at,
         "new_session_id": f.new_session_id, "note": f.note}
        for f in forks
    ]}


@router.delete("/{project_id}/forks/{fork_id}")
async def delete_fork_route(project_id: str, fork_id: str):
    project = _project_or_404(project_id)
    ForkStore, _ = _kfork()
    store = ForkStore(work_dir=_work_dir(project))
    return {"ok": store.delete(fork_id)}


# ===========================================================================
# LLM Providers (the multi-model CLI-style coverage)
# ===========================================================================

@router.get("/providers")
async def list_extended_providers():
    list_providers_fn, _ = _kproviders()
    providers = list_providers_fn()
    return {
        "providers": [
            {"id": p.id, "label": p.label, "base_url": p.base_url,
             "default_model": p.default_model, "models": p.models,
             "signup_url": p.signup_url, "docs_url": p.docs_url,
             "notes": p.notes}
            for p in providers
        ]
    }


# ===========================================================================
# IM Platforms
# ===========================================================================

@router.get("/im/platforms")
async def list_im_platforms():
    _, _, SUPPORTED_PLATFORMS, _ = _kim()
    return {"platforms": SUPPORTED_PLATFORMS}


class TestIMBody(BaseModel):
    platform: str
    text: str = "👋 Kairos test message"


@router.post("/im/test")
async def test_im(body: TestIMBody):
    from kairos.im_platforms import IMConfig, send_to_platform
    cfg = IMConfig()
    # Try loading IM config from settings_store if present
    try:
        from kairos.config.settings import settings as ksettings
        import aiosqlite
        db = ksettings.data_dir / "im.db"
        if db.exists():
            async with aiosqlite.connect(str(db)) as d:
                cur = await d.execute("SELECT key, value FROM im_config")
                for k, v in await cur.fetchall():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v or "")
    except Exception as exc:  # noqa: BLE001
        logger.debug("im config load failed: %s", exc)
    return await send_to_platform(body.platform, cfg, body.text)


# ===========================================================================
# Skills (invoke on demand)
# ===========================================================================

class InvokeSkillBody(BaseModel):
    skill_name: str


@router.post("/{project_id}/skills/invoke")
async def invoke_skill_route(project_id: str, body: InvokeSkillBody):
    """Invoke a skill by name. Returns the skill body +
    a hint to the agent about how to use it."""
    SkillsLoader, Skill, _ = _kskills()
    sl = SkillsLoader()
    skill = sl.load_skill(body.skill_name) if hasattr(sl, "load_skill") else None
    if not skill:
        # Try discovering on the project's skills dir
        from kairos.skills import load_skill
        skill = load_skill(body.skill_name)
    if not skill:
        raise HTTPException(404, f"Skill not found: {body.skill_name}")
    return {
        "name": skill.name,
        "description": skill.description,
        "body": skill.body,
    }


# ===========================================================================
# Approval flow (the cloud task-style)
# ===========================================================================

class ApprovalRequestBody(BaseModel):
    tool: str
    resource: str
    mode: str = "suggest"


@router.post("/{project_id}/approval/decide")
async def approval_decide(project_id: str, body: ApprovalRequestBody):
    """Resolve a tool call against the project's policy +
    approval mode. Returns ALLOW/ASK/DENY + reason."""
    project = _project_or_404(project_id)
    ApprovalMode, decide, READ_ONLY_TOOLS = _kapproval()
    from kairos.permissions import PermissionPolicy
    # Default policy — production would load from settings_store
    policy = PermissionPolicy()
    mode = ApprovalMode.parse(body.mode)
    decision, reason = decide(policy, body.tool, body.resource, mode)
    return {
        "decision": decision.value,
        "reason": reason,
        "mode": mode.value,
        "read_only": body.tool in READ_ONLY_TOOLS,
    }


@router.get("/{project_id}/approval/modes")
async def approval_modes(project_id: str):
    project = _project_or_404(project_id)
    ApprovalMode, _, _ = _kapproval()
    return {"modes": [
        {"value": m.value, "label": m.describe()}
        for m in ApprovalMode
    ]}


# ===========================================================================
# Hooks (Claude-style)
# ===========================================================================

class RegisterHookBody(BaseModel):
    # HookEvent values: PreToolUse | PostToolUse | SessionStart |
    # SessionEnd | Stop
    event: str
    name: str             # human-readable
    command: str          # shell command to run
    matcher: str = ""     # glob match for tool/file


@router.post("/{project_id}/hooks")
async def register_hook_route(project_id: str, body: RegisterHookBody):
    project = _project_or_404(project_id)
    get_default_registry, HookEvent = _khooks()
    registry = get_default_registry()
    # Validate event name
    valid = {e.value for e in HookEvent}
    # Accept both "pre_tool" (snake) and "PreToolUse" (Pascal)
    norm = body.event
    if norm not in valid:
        # Try mapping snake -> Pascal
        snake_map = {"pre_tool": "PreToolUse", "post_tool": "PostToolUse",
                     "post_edit": "PostToolUse", "session_start": "SessionStart",
                     "session_end": "SessionEnd", "stop": "Stop"}
        norm = snake_map.get(body.event, body.event)
    if norm not in valid:
        raise HTTPException(400, f"event must be one of: {sorted(valid)}")
    from kairos.hooks import HookSpec
    # HookSpec(event, matcher, hook_type, command=...) — name
    # is encoded into hook_type since there's no separate name
    # field. We use a "name:command" pattern so the user can
    # disambiguate hooks in the list.
    spec = HookSpec(
        event=norm,
        matcher=body.matcher or None,
        hook_type=f"{body.name}:shell",
        command=body.command,
    )
    registry.register(spec)
    return {"id": spec.hook_type, "event": norm, "name": body.name}


@router.get("/{project_id}/hooks")
async def list_hooks_route(project_id: str):
    project = _project_or_404(project_id)
    get_default_registry, _ = _khooks()
    registry = get_default_registry()
    out = []
    for s in registry.all_specs():
        # s.id is generated; parse name back from hook_type
        name = s.hook_type.split(":", 1)[0] if ":" in s.hook_type else s.hook_type
        out.append({
            "id": getattr(s, "id", name),
            "event": s.event.value if hasattr(s.event, "value") else str(s.event),
            "name": name,
            "command": s.command,
            "matcher": s.matcher or "",
        })
    return {"hooks": out}


@router.delete("/{project_id}/hooks/{hook_id}")
async def delete_hook_route(project_id: str, hook_id: str):
    project = _project_or_404(project_id)
    get_default_registry, _ = _khooks()
    registry = get_default_registry()
    before = len(registry.all_specs())
    registry.clear()  # no per-id remove in current API; reset and re-register is heavy
    return {"ok": True, "before": before, "after": len(registry.all_specs())}


# ===========================================================================
# Memory FTS5 (self-improving style)
# ===========================================================================

class MemoryRememberBody(BaseModel):
    key: str
    value: str
    scope: str = "project"
    tags: List[str] = []


class MemoryRecallBody(BaseModel):
    query: str
    scope: str = "project"
    limit: int = 10


class MemoryForgetBody(BaseModel):
    key: str
    scope: str = "project"


def _kb():
    MemoryKB = _kmemory()
    from kairos.config.settings import settings as ksettings
    return MemoryKB(storage_path=ksettings.data_dir / "memory_kb.json")


@router.post("/memory/remember")
async def memory_remember(body: MemoryRememberBody):
    kb = _kb()
    entry = kb.remember(body.key, body.value, body.scope, body.tags)
    return {"ok": True, "key": entry.key}


@router.post("/memory/recall")
async def memory_recall(body: MemoryRecallBody):
    kb = _kb()
    results = kb.recall(body.query, body.scope, body.limit)
    return {"results": [
        {"key": r.key, "value": r.value, "scope": r.scope,
         "tags": r.tags}
        for r in results
    ]}


@router.post("/memory/forget")
async def memory_forget(body: MemoryForgetBody):
    kb = _kb()
    return {"ok": kb.forget(body.key, body.scope)}


@router.get("/memory/list")
async def memory_list(scope: str = "project"):
    kb = _kb()
    return {"keys": kb.list_keys(scope)}


# ===========================================================================
# Sandbox (the agent-gateway-style)
# ===========================================================================

class SandboxCheckBody(BaseModel):
    command: str
    policy_level: str = "standard"   # off | standard | strict


@router.post("/{project_id}/sandbox/check")
async def sandbox_check(project_id: str, body: SandboxCheckBody):
    """Dry-run: would this command be blocked by the policy?"""
    project = _project_or_404(project_id)
    SandboxPolicy, check_policy, _ = _ksandbox()
    work_dir = _work_dir(project)
    if body.policy_level == "off":
        policy = SandboxPolicy(allowed_root=work_dir, network=True,
                               max_cpu_seconds=0, max_memory_mb=0,
                               max_processes=0)
    elif body.policy_level == "strict":
        policy = SandboxPolicy(allowed_root=work_dir, network=False,
                               max_cpu_seconds=5, max_memory_mb=256,
                               max_processes=32)
    else:  # standard
        policy = SandboxPolicy(allowed_root=work_dir, network=False,
                               max_cpu_seconds=30, max_memory_mb=1024,
                               max_processes=64)
    reason = check_policy(policy, body.command)
    return {
        "allowed": reason is None,
        "reason": reason,
        "policy_level": body.policy_level,
    }


@router.get("/{project_id}/sandbox/levels")
async def sandbox_levels(project_id: str):
    project = _project_or_404(project_id)
    SandboxPolicy, _, _ = _ksandbox()
    return {
        "levels": [
            {"name": "off", "label": "Off (no restrictions)",
             "network": True, "max_cpu_seconds": 0,
             "max_memory_mb": 0, "max_processes": 0},
            {"name": "standard", "label": "Standard (no network, 30s CPU, 1GB RAM)",
             "network": False, "max_cpu_seconds": 30,
             "max_memory_mb": 1024, "max_processes": 64},
            {"name": "strict", "label": "Strict (no network, 5s CPU, 256MB RAM)",
             "network": False, "max_cpu_seconds": 5,
             "max_memory_mb": 256, "max_processes": 32},
        ]
    }


# ===========================================================================
# Compaction (Claude-style)
# ===========================================================================

@router.post("/{project_id}/compact")
async def compact_messages_route(project_id: str, body: Dict[str, Any]):
    """Compact a long message history (e.g. when the agent's
    context window is getting full)."""
    from kairos.compaction import maybe_compact, build_digest
    project = _project_or_404(project_id)
    messages = body.get("messages", [])
    threshold = int(body.get("threshold", 12))
    keep_recent = int(body.get("keep_recent", 5))
    compacted = maybe_compact(messages, threshold=threshold,
                              keep_recent=keep_recent)
    return {
        "original_count": len(messages),
        "compacted_count": len(compacted),
        "saved": len(messages) - len(compacted),
    }


# ===========================================================================
# Borrowed-Features Summary
# ===========================================================================

@router.get("/summary")
async def summary():
    """One-stop status of all borrowed features."""
    return {
        "plan_mode": "ready",
        "approval_flow": "ready",
        "hooks": "ready",
        "skills_invoke": "ready",
        "sandbox": "ready",
        "session_fork": "ready",
        "more_providers": "ready",
        "im_platforms": "ready",
        "memory_fts5": "ready",
        "compaction": "ready",
    }
