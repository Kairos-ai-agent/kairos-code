"""Round 29: Long-running harness — the .har/ contract + resume runtime.

A developer kicks off a multi-day refactor: ``kairos.har init "migrate
all 47 endpoints to FastAPI dependency injection"``. The harness writes
``.har/`` to disk. They run ``kairos.har resume`` a few times, close
the laptop, and resume the next morning by running it again. Each
``resume`` invocation picks up from the last saved round.

This module is the **contract + state + lock + history** layer. The
actual loop logic is injected by the caller via a ``tick_fn`` callback
so the framework is decoupled from ``kairos.loop.loop_runner`` (which
is async and heavyweight). For tests and dry-runs, a synthetic tick
is provided.

File layout::

    .har/
      contract.json     # immutable: goal, har_id, created_at, cwd
      state.json        # mutable: round, last_score, last_approve, plan
      plan.md           # human-readable, synced from state.plan_text
      history.jsonl     # one line per round (newest last)
      .gitignore        # "lock" — lock file should not be committed
      lock              # PID + ts (only present while resume is running)

Why JSONL for history? Same reason as ``data/alerts.jsonl`` (R28) and
``data/cost.jsonl`` (R14) — append-only, crash-safe, easy to tail.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_HAR_DIRNAME = ".har"
STALE_LOCK_S = 3600        # 1 h — auto-steal a lock that old


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class HarContract:
    """What the user wants. Immutable after init.

    ``cwd`` is the workspace the loop is supposed to operate on. We
    capture it at init time so a later ``resume`` from a different
    CWD still finds the right workspace.
    """
    goal: str
    har_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: float = field(default_factory=time.time)
    cwd: str = ""
    rounds_planned: int = 10
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "HarContract":
        return cls(
            goal=d.get("goal", ""),
            har_id=d.get("har_id", uuid.uuid4().hex[:8]),
            created_at=float(d.get("created_at", 0.0)),
            cwd=d.get("cwd", ""),
            rounds_planned=int(d.get("rounds_planned", 10)),
            meta=dict(d.get("meta") or {}),
        )


@dataclass
class HarState:
    """Mutable state for a running harness.

    Saved after every round (atomic rename). Mirrors a subset of
    ``LoopSession`` that's safe to serialize as JSON (no agents, no
    asyncio.Event, no file snapshots).
    """
    round: int = 0
    last_score: int = 0
    last_approve: bool = False
    last_summary: str = ""
    last_signature: str = ""
    no_progress_count: int = 0
    plan_text: str = ""
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "HarState":
        return cls(
            round=int(d.get("round", 0)),
            last_score=int(d.get("last_score", 0)),
            last_approve=bool(d.get("last_approve", False)),
            last_summary=str(d.get("last_summary", "")),
            last_signature=str(d.get("last_signature", "")),
            no_progress_count=int(d.get("no_progress_count", 0)),
            plan_text=str(d.get("plan_text", "")),
            updated_at=float(d.get("updated_at", 0.0)),
        )


# ---------------------------------------------------------------------------
# File I/O — atomic + safe
# ---------------------------------------------------------------------------


def _har_dir(root: Path) -> Path:
    return Path(root) / DEFAULT_HAR_DIRNAME


def init_har(root: Path, contract: HarContract) -> Path:
    """Create a new ``.har/`` directory. Fails if it already exists."""
    h = _har_dir(root)
    if h.exists():
        raise FileExistsError(
            f"{h} already exists; refusing to overwrite "
            f"(delete it manually or run `har resume` instead)"
        )
    h.mkdir(parents=True)
    (h / "contract.json").write_text(
        json.dumps(contract.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (h / "state.json").write_text(
        json.dumps(HarState().to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (h / "plan.md").write_text(
        f"# Plan for harness {contract.har_id}\n\n"
        f"Goal: {contract.goal}\n\n"
        f"_Plan will appear here after the first round._\n",
        encoding="utf-8",
    )
    (h / "history.jsonl").write_text("", encoding="utf-8")
    (h / ".gitignore").write_text("lock\n", encoding="utf-8")
    return h


def load_har(root: Path) -> Tuple[HarContract, HarState, Path]:
    """Load an existing ``.har/`` directory."""
    h = _har_dir(root)
    if not h.exists():
        raise FileNotFoundError(f"no {DEFAULT_HAR_DIRNAME}/ at {h}")
    c = HarContract.from_dict(json.loads(
        (h / "contract.json").read_text(encoding="utf-8")))
    s = HarState.from_dict(json.loads(
        (h / "state.json").read_text(encoding="utf-8")))
    return c, s, h


def save_state(h: Path, state: HarState) -> None:
    """Atomic write of state.json (tmp + rename).

    Same pattern as the R10 settings store — crash mid-write
    preserves the old file.
    """
    state.updated_at = time.time()
    tmp = h / "state.json.tmp"
    tmp.write_text(
        json.dumps(state.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, h / "state.json")


def save_plan(h: Path, plan_text: str) -> None:
    """Sync plan.md from state.plan_text."""
    (h / "plan.md").write_text(
        plan_text or "(empty plan)\n",
        encoding="utf-8",
    )


def append_history(h: Path, entry: Dict[str, Any]) -> None:
    """Append one history line to history.jsonl (best-effort, no exceptions)."""
    p = h / "history.jsonl"
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
    except OSError as exc:
        logger.debug("history append failed: %s", exc)


def read_history(h: Path, limit: int = 50) -> List[Dict[str, Any]]:
    """Return the most recent ``limit`` history entries (newest first)."""
    p = h / "history.jsonl"
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = [l for l in text.splitlines() if l]
        out: List[Dict[str, Any]] = []
        for line in reversed(lines[-limit:]):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
    except OSError as exc:
        logger.debug("history read failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Lock — prevent concurrent resume
# ---------------------------------------------------------------------------


def acquire_lock(h: Path) -> Optional[int]:
    """Try to acquire ``.har/lock``.

    Returns our PID on success, ``None`` if the lock is held by another
    live process. Stale locks older than ``STALE_LOCK_S`` are
    auto-stolen (handles the "laptop suspended for 3 hours" case).
    """
    lock = h / "lock"
    payload = f"{os.getpid()}\n{time.time()}\n".encode("utf-8")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        return os.getpid()
    except FileExistsError:
        # Maybe stale?
        try:
            text = lock.read_text(encoding="utf-8")
            ts = float(text.strip().splitlines()[-1])
            if time.time() - ts > STALE_LOCK_S:
                lock.unlink(missing_ok=True)
                return acquire_lock(h)
        except (OSError, ValueError, IndexError):
            pass
        return None


def release_lock(h: Path) -> None:
    """Release the lock if (and only if) we own it."""
    lock = h / "lock"
    if not lock.exists():
        return
    try:
        text = lock.read_text(encoding="utf-8")
        pid = int(text.strip().splitlines()[0])
        if pid == os.getpid():
            lock.unlink(missing_ok=True)
    except (OSError, ValueError, IndexError):
        pass


# ---------------------------------------------------------------------------
# Tick — the actual round logic (decoupled from loop_runner)
# ---------------------------------------------------------------------------


TickFn = Callable[[HarState, HarContract],
                  Tuple[HarState, Dict[str, Any]]]


def _synthetic_tick(state: HarState, contract: HarContract
                    ) -> Tuple[HarState, Dict[str, Any]]:
    """A no-op tick for tests and ``--dry-run``.

    Bumps the round, marks approved on round 3, and records a
    fake entry. Production callers pass a real ``tick_fn`` that
    invokes ``kairos.loop.loop_runner`` (or a stub thereof).
    """
    new_round = state.round + 1
    approved = new_round >= 3
    new_state = HarState(**{
        **state.to_dict(),
        "round": new_round,
        "last_score": 80 if approved else 60,
        "last_approve": approved,
        "last_summary": f"round {new_round} synthetic (goal={contract.goal[:30]})",
    })
    entry = {
        "round": new_round,
        "ts": time.time(),
        "score": new_state.last_score,
        "approved": approved,
        "summary": new_state.last_summary,
    }
    return new_state, entry


def resume(
    root: Path,
    *,
    max_rounds: int = 1,
    tick_fn: Optional[TickFn] = None,
    stop_on_approve: bool = True,
) -> Tuple[int, str]:
    """Run up to ``max_rounds`` rounds via ``tick_fn``.

    Returns ``(returncode, message)``:
      - ``(0, "ok")`` — at least one round ran (or 0 rounds requested)
      - ``(2, "lock held")`` — another process owns the lock
      - ``(3, "no .har/")`` — the harness directory is missing
      - ``(4, "approved, stopping")`` — ``stop_on_approve`` and we got a YES
      - ``(5, "no progress, stopping")`` — ``no_progress_count`` exceeded

    Each round:
      1. ``tick_fn(state, contract) -> (new_state, history_entry)``
      2. ``save_state(h, new_state)``
      3. ``append_history(h, history_entry)``
      4. ``save_plan(h, new_state.plan_text)`` if changed
    """
    try:
        contract, state, h = load_har(root)
    except FileNotFoundError as e:
        return 3, str(e)

    pid = acquire_lock(h)
    if pid is None:
        return 2, "lock held by another process; refusing to resume"

    fn = tick_fn or _synthetic_tick
    final_msg = "ok"
    rc = 0
    try:
        for i in range(max_rounds):
            new_state, entry = fn(state, contract)
            save_state(h, new_state)
            append_history(h, entry)
            if new_state.plan_text and new_state.plan_text != state.plan_text:
                save_plan(h, new_state.plan_text)
            state = new_state
            if stop_on_approve and entry.get("approved"):
                final_msg = f"approved at round {state.round}; stopping"
                rc = 4
                break
            if state.no_progress_count >= 3:
                final_msg = (f"no progress for {state.no_progress_count} "
                             f"rounds; stopping")
                rc = 5
                break
        else:
            final_msg = f"ran {max_rounds} round(s) without approval"
        return rc, final_msg
    finally:
        release_lock(h)


# ---------------------------------------------------------------------------
# Status / formatting helpers
# ---------------------------------------------------------------------------


def status_text(c: HarContract, s: HarState) -> str:
    return (
        f"Harness {c.har_id}  goal: {c.goal[:60]}\n"
        f"  cwd: {c.cwd}\n"
        f"  round={s.round}  last_score={s.last_score}  "
        f"approved={s.last_approve}  no_progress={s.no_progress_count}\n"
        f"  updated_at={s.updated_at:.0f}  "
        f"plan_chars={len(s.plan_text)}  "
        f"rounds_planned={c.rounds_planned}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_history_row(e: Dict[str, Any]) -> str:
    ts = e.get("ts", 0)
    verdict = "OK" if e.get("approved") else "X"
    return (f"  R{e.get('round', 0):>3}  "
            f"score={e.get('score', 0):>3}  "
            f"[{verdict}]  "
            f"{e.get('summary', '')[:60]}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kairos.har",
        description="Long-running harness — .har/ contract + resume runtime",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Create a new .har/ in CWD")
    p_init.add_argument("goal", help="The user's goal / requirement")
    p_init.add_argument("--cwd", default=".",
                        help="Workspace the loop operates on")
    p_init.add_argument("--rounds", type=int, default=10,
                        help="Max rounds planned")
    p_init.add_argument("--meta", help="Optional JSON meta to attach to the contract")

    p_status = sub.add_parser("status", help="Show current state")
    p_status.add_argument("--cwd", default=".")

    p_chk = sub.add_parser("checkpoints",
                           help="List rounds from history.jsonl")
    p_chk.add_argument("--cwd", default=".")
    p_chk.add_argument("--limit", type=int, default=20)
    p_chk.add_argument("--json", action="store_true")

    p_resume = sub.add_parser("resume", help="Run N more rounds (bounded)")
    p_resume.add_argument("--cwd", default=".")
    p_resume.add_argument("--rounds", type=int, default=1,
                          help="Max rounds to add this invocation")
    p_resume.add_argument("--no-stop-on-approve", action="store_true",
                          help="Continue even if a round is approved")

    args = parser.parse_args(argv)
    root = Path(args.cwd).resolve()

    if args.cmd == "init":
        meta: Dict[str, Any] = {}
        if args.meta:
            try:
                meta = json.loads(args.meta)
            except json.JSONDecodeError as e:
                print(f"invalid --meta JSON: {e}", file=sys.stderr)
                return 1
        contract = HarContract(
            goal=args.goal, cwd=str(root),
            rounds_planned=args.rounds, meta=meta,
        )
        try:
            h = init_har(root, contract)
        except FileExistsError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f"Initialized {h}")
        print(f"  har_id    = {contract.har_id}")
        print(f"  goal      = {contract.goal}")
        print(f"  rounds    = {contract.rounds_planned}")
        print(f"  cwd       = {contract.cwd}")
        return 0

    # --- Status / checkpoints / resume: need an existing .har/ ---
    try:
        contract, state, h = load_har(root)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.cmd == "status":
        print(status_text(contract, state))
        plan_path = h / "plan.md"
        if plan_path.exists():
            plan = plan_path.read_text(encoding="utf-8")
            if plan.strip() and "Plan will appear here" not in plan:
                print("\n--- plan.md ---")
                print(plan)
        return 0

    if args.cmd == "checkpoints":
        entries = read_history(h, limit=args.limit)
        if args.json:
            print(json.dumps(entries, indent=2))
            return 0
        if not entries:
            print("No rounds recorded yet.")
            return 0
        print(f"Recent rounds (newest first, max {args.limit}):")
        for e in entries:
            print(_format_history_row(e))
        return 0

    if args.cmd == "resume":
        rc, msg = resume(
            root, max_rounds=args.rounds,
            stop_on_approve=not args.no_stop_on_approve,
        )
        print(msg)
        return rc

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
