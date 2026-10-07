"""Command-line interface for Kairos Code.

`kairos exec "..."` is the non-interactive mode that mirrors
`codex exec "..."` (the cloud task) and `claude -p "..."` (the agentic CLI). It:

  1. Creates a throwaway project (or `--persist` for debugging).
  2. Runs the Team Leader loop on the supplied task.
  3. Streams progress to stderr (unless `--quiet`).
  4. Writes the final result to stdout.
  5. Cleans up: deletes the project (and optionally the work dir)
     unless `--persist`.
  6. Exits with a meaningful code:
     0 = success
     1 = agent error / task failed
     2 = timeout
     3 = bad input (e.g. empty task)

Designed to be CI-friendly:

    kairos exec "Add docstrings to kairos/cli.py" \
        --work-dir /tmp/kairos-build \
        --json --timeout 300 | jq '.result'

    kairos exec --persist "Refactor ..."    # leave the project around
                                             # so you can inspect it

    kairos gate report --project my-app --out gate.html
        # the receipt for a loop: rounds, scores, verdicts, cost

    kairos demo
        # watch the gate work end-to-end — no API key, no network

This module deliberately uses only the standard library (argparse
+ asyncio) so the CLI works in any environment where the package
is installed. The agent core (Orchestrator, MessageBus) is reused
unchanged.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_TIMEOUT = 2
EXIT_BAD_INPUT = 3
# The work is understood but not safe to start unattended (dangerous subject
# matter, or nothing to verify against). A caller that dispatches work must be
# able to tell this apart from success and from failure.
EXIT_NEEDS_CONFIRMATION = 4


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level parser. Returns the parser so callers
    can unit-test argument handling without invoking main()."""
    parser = argparse.ArgumentParser(
        prog="kairos",
        description=(
            "Kairos Code — multi-agent collaboration platform. "
            "Run `kairos serve` to launch the long-lived HTTP server, "
            "or `kairos exec` for a one-shot CI-style invocation."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    # ---- serve (default uvicorn entry) ----------------------------------
    p_serve = sub.add_parser(
        "serve", help="Start the Kairos HTTP/WebSocket server (default)."
    )
    p_serve.add_argument(
        "--host", default=os.environ.get("KAIROS_HOST", "127.0.0.1"),
    )
    p_serve.add_argument(
        "--port", type=int,
        default=int(os.environ.get("KAIROS_PORT", "8900")),
    )

    # ---- exec (one-shot) ------------------------------------------------
    p_exec = sub.add_parser(
        "exec", help="Run a single task non-interactively and exit.",
    )
    p_exec.add_argument(
        "task", help="Task description in natural language (quoted).",
    )
    p_exec.add_argument(
        "--work-dir", default=None,
        help="Where agents write files. Default: a temp dir cleaned up "
             "after the run (use --persist to keep it).",
    )
    p_exec.add_argument(
        "--persist", action="store_true",
        help="Keep the project (and work_dir) after the run.",
    )
    p_exec.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Emit a single JSON document on stdout instead of a "
             "human-readable result.",
    )
    p_exec.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress progress logs on stderr.",
    )
    p_exec.add_argument(
        "--model", default=None,
        help="Override the model name for this run.",
    )
    p_exec.add_argument(
        "--timeout", type=int, default=600,
        help="Hard wall-clock cap in seconds (default: 600).",
    )
    p_exec.add_argument(
        "--no-cleanup", action="store_true",
        help="Skip removing the work_dir on success. (Always kept on "
             "failure so you can inspect what went wrong.)",
    )

    # ---- gate (Gate Report: the receipt for a loop) ---------------------
    p_gate = sub.add_parser(
        "gate",
        help="Gate Report — who reviewed, what it scored, how many rounds "
             "were rejected, what it cost.",
    )
    gate_sub = p_gate.add_subparsers(dest="gate_command")
    p_gate_report = gate_sub.add_parser(
        "report", help="Generate a Gate Report for one project.",
    )
    p_gate_report.add_argument(
        "--project", required=True, help="Project id, id prefix or name.",
    )
    p_gate_report.add_argument(
        "--out", default="gate-report",
        help="Output path (extension optional; default gate-report.html).",
    )
    p_gate_report.add_argument(
        "--format", choices=["html", "md", "json"], default="html",
        help="html = one self-contained file (default); md = paste into a "
             "PR; json = stable keys for CI.",
    )
    p_gate_report.add_argument(
        "--lang", choices=["en", "zh"], default="en",
        help="Report language (the HTML also carries an in-place toggle).",
    )
    p_gate_report.add_argument(
        "--session", default=None, help="Restrict to one loop session.",
    )
    p_gate_report.add_argument(
        "--db", default=None,
        help="SQLite path (default: the app's data dir).",
    )
    p_gate_report.add_argument("--quiet", action="store_true")

    # ---- worker (identity: one repo is one worker) ----------------------
    p_worker = sub.add_parser(
        "worker",
        help="Bind a repository to a single long-lived worker session, so "
             "each dispatched task does not start over from scratch.",
    )
    worker_sub = p_worker.add_subparsers(dest="worker_command")
    for name, helptext in (
        ("attach", "Bind this repository (creating the binding once)."),
        ("status", "Show the worker bound to a repository."),
        ("forget", "Detach the worker from a repository (history is kept)."),
    ):
        sub_parser = worker_sub.add_parser(name, help=helptext)
        sub_parser.add_argument(
            "--repo", default=".",
            help="Repository the worker is attached to (default: cwd).",
        )
        sub_parser.add_argument(
            "--name", default="", help="Display name (attach only).",
        )
        sub_parser.add_argument(
            "--json", action="store_true", dest="json_output",
            help="Emit JSON instead of readable text.",
        )

    # ---- accept (read a task document of any shape) ----------------------
    p_accept = sub.add_parser(
        "accept",
        help="Read a task document and report what it asks for. Accepts any "
             "format — no template, no frontmatter, no keywords.",
    )
    p_accept.add_argument(
        "file", help="The task document (Markdown, prose, anything).",
    )
    p_accept.add_argument(
        "--repo", default=None,
        help="Repository the task belongs to (default: the document's dir).",
    )
    p_accept.add_argument(
        "--task-id", default="",
        help="Override the task id (default: derived from the filename).",
    )
    p_accept.add_argument(
        "--out", default=None,
        help="Where to write our record (default: <repo>/.kairos/outbox).",
    )
    p_accept.add_argument(
        "--no-model", action="store_true",
        help="Read it structurally, with no model call at all.",
    )
    p_accept.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Emit the task record as JSON.",
    )

    # ---- demo (see the gate with no API key) ----------------------------
    p_demo = sub.add_parser(
        "demo",
        help="Run the review gate end-to-end with a scripted model — no API "
             "key, no network, about a minute.",
    )
    p_demo.add_argument(
        "--out", default=None,
        help="Where to build the demo workspace (default: a temp dir that is "
             "cleaned up; the Gate Report is copied to the current directory).",
    )
    p_demo.add_argument(
        "--keep", action="store_true",
        help="Keep the temporary workspace (default: cleaned up).",
    )
    p_demo.add_argument(
        "--open", action="store_true", dest="open_report",
        help="Open the Gate Report in the browser when done.",
    )
    p_demo.add_argument(
        "--format", choices=["html", "md", "json"], default="html",
        help="Gate Report format (default: html).",
    )
    p_demo.add_argument("--lang", choices=["en", "zh"], default="en")
    p_demo.add_argument(
        "--timeout", type=float, default=90.0,
        help="Wall-clock cap for the loop (default: 90s).",
    )
    p_demo.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Emit a single JSON document on stdout.",
    )
    p_demo.add_argument("--quiet", "-q", action="store_true")

    # ---- skeleton (domain-neutral worker -> verifier, no code loop) ------
    p_skeleton = sub.add_parser(
        "skeleton",
        help="Run a domain-neutral task (workspace -> worker -> verifier) "
             "without the code loop: e.g. read documents and produce a report.",
    )
    skeleton_sub = p_skeleton.add_subparsers(dest="skeleton_command")
    p_sk_run = skeleton_sub.add_parser(
        "run", help="Run one task through the skeleton and verify it.",
    )
    p_sk_run.add_argument(
        "--task", required=True,
        help="The task: literal text, or a path to a file containing it.",
    )
    p_sk_run.add_argument(
        "--workspace", default=".",
        help="Workspace directory the worker reads/writes (default: cwd).",
    )
    p_sk_run.add_argument(
        "--kind", choices=["docs", "repo"], default="docs",
        help="docs = a document set (default); repo = a git working tree.",
    )
    p_sk_run.add_argument(
        "--verifier", choices=["assertion", "citations", "tests", "rubric", "human"],
        default="assertion",
        help="How to judge the deliverable (default: assertion). "
             "`citations` cross-checks a self-reported `citations=N/M` line "
             "against the workspace's real resource count (a report admitting "
             "it read nothing fails); `tests` runs the workspace's test "
             "command; `human` persists the run as a human-gated run for "
             "`skeleton resume`.",
    )
    p_sk_run.add_argument(
        "--check", default="",
        help="assertion verifier: a trusted string expression, e.g. "
             "\"'SELF_CHECK' in output\" (callables preferred in code).",
    )
    p_sk_run.add_argument(
        "--criterion", action="append", default=[], metavar="SUBSTRING",
        help="rubric verifier: a substring the deliverable must contain "
             "(repeatable).",
    )
    p_sk_run.add_argument(
        "--threshold", type=float, default=1.0,
        help="rubric threshold (default: 1.0 = every criterion).",
    )
    p_sk_run.add_argument(
        "--out-name", default="report.md",
        help="Name of the deliverable artifact (default: report.md).",
    )
    p_sk_run.add_argument(
        "--generator", choices=["auto", "offline", "model"], default="auto",
        help="auto = model if configured else offline (default); offline = a "
             "deterministic no-network generator; model = require a provider.",
    )
    p_sk_run.add_argument(
        "--run-dir", default=None,
        help="Where to persist the run JSON "
             "(default: <workspace>/.kairos/skeleton-runs).",
    )
    p_sk_run.add_argument("--json", action="store_true", dest="json_output")
    p_sk_run.add_argument("--quiet", "-q", action="store_true")

    p_sk_resume = skeleton_sub.add_parser(
        "resume", help="Answer a human-gated skeleton run and continue it.",
    )
    p_sk_resume.add_argument(
        "--run", required=True, help="Path to a persisted skeleton-run JSON.",
    )
    grp = p_sk_resume.add_mutually_exclusive_group(required=True)
    grp.add_argument("--approve", action="store_true",
                     help="Accept the deliverable; the run is marked passed.")
    grp.add_argument("--reject", action="store_true",
                     help="Reject the deliverable; the run is marked failed.")
    p_sk_resume.add_argument("--reason", default="",
                             help="Why (recorded on the run).")
    p_sk_resume.add_argument("--json", action="store_true", dest="json_output")

    return parser


# ---------------------------------------------------------------------------
# Core exec logic
# ---------------------------------------------------------------------------


async def run_exec(args: argparse.Namespace) -> int:
    """Execute one task in headless mode. Returns the exit code."""
    if not args.task or not args.task.strip():
        print("error: empty task", file=sys.stderr)
        return EXIT_BAD_INPUT

    # Lazy import the heavy agent core. Keeps the CLI import-cheap
    # so `--help` is instant and so this module can be imported by
    # tests without paying the full uvicorn/agent stack cost.
    from kairos.core.orchestrator import Orchestrator
    from kairos.core.persistence import Persistence
    from kairos.llm.model_router import ModelRouter
    from kairos.config.settings import settings

    # 1. Set up a work directory. Either user-provided or a tempdir
    #    that we own.
    if args.work_dir:
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        owned_workdir = False
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="kairos_exec_"))
        owned_workdir = True

    started_at = time.time()
    pid = f"exec-{uuid.uuid4().hex[:8]}"

    if not args.quiet:
        print(f"[kairos] project={pid} work_dir={work_dir}", file=sys.stderr)

    # 2. Spin up an in-process Orchestrator backed by a *scratch*
    #    SQLite database so the run leaves no global state behind.
    scratch_db = tempfile.NamedTemporaryFile(
        suffix=".sqlite", delete=False, prefix="kairos_exec_db_"
    )
    scratch_db.close()
    try:
        db_path = Path(scratch_db.name)
        try:
            persistence = Persistence(db_path=db_path)
            # settings is a Pydantic model. It has data_dir /
            # workspace_dir but not config_dir — config files
            # (models_config.yaml, agents_config.yaml) live in the
            # package itself.
            from kairos.config.settings import settings as _settings
            models_cfg = (
                Path(_settings.data_dir).resolve() / ".." / "kairos" / "config" / "models_config.yaml"
            )
            # Fall back to the package's bundled config if the
            # data-dir-relative lookup didn't resolve.
            if not models_cfg.exists():
                from kairos import config as _pkg_config
                models_cfg = Path(_pkg_config.__file__).parent / "models_config.yaml"
            router = ModelRouter(config_path=models_cfg.resolve())
            orch = Orchestrator(
                model_router=router,
                workspace_base=work_dir,
                db=persistence,
            )
        except Exception as exc:
            import traceback
            print(f"error: failed to init orchestrator: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return EXIT_FAILED

        try:
            project = orch.create_project(
                name=f"exec-{started_at:.0f}",
                description="One-shot task from kairos exec",
                work_dir=str(work_dir),
            )
            pid = project.id  # actual generated id may differ

            if not args.quiet:
                print(f"[kairos] created project {pid}", file=sys.stderr)

            # 3. Optional: override the model for this run.
            if args.model:
                try:
                    router.assign_role_model("team_leader", args.model)
                    if not args.quiet:
                        print(f"[kairos] model override: {args.model}", file=sys.stderr)
                except Exception as exc:
                    print(f"[kairos] model override failed: {exc}", file=sys.stderr)

            # 4. Start the loop. start_project dispatches sub-tasks
            #    and the orchestrator stores the final plan in
            #    project.state. We poll for completion.
            await orch.start_project(pid, args.task)

            # Poll for completion with a hard timeout.
            deadline = time.time() + args.timeout
            while time.time() < deadline:
                # status can be "active" / "working" / "completed"
                # / "failed". The orchestrator transitions to
                # "completed" once the loop reports back.
                if project.status in ("completed", "failed"):
                    break
                await asyncio.sleep(1.0)
            else:
                # Timed out.
                payload = {
                    "project_id": pid,
                    "status": "timeout",
                    "elapsed_s": time.time() - started_at,
                    "work_dir": str(work_dir),
                    "result": None,
                    "error": f"timed out after {args.timeout}s",
                }
                _emit(args, payload)
                return EXIT_TIMEOUT

            elapsed = time.time() - started_at
            # 5. Pull the latest plan / result from the orchestrator.
            plan_text = ""
            try:
                # start_project's return value isn't kept; pull
                # from the latest "project.plan" message.
                msgs = await orch.message_bus.get_history(
                    limit=50, topic_filter="project.plan"
                )
                if msgs:
                    plan_text = msgs[-1].get("content", "") or ""
            except Exception:
                pass

            result = {
                "project_id": pid,
                "status": project.status,
                "elapsed_s": round(elapsed, 2),
                "work_dir": str(work_dir),
                "result": plan_text,
                "task_count": project.task_count,
            }
            _emit(args, result)
            exit_code = EXIT_OK if project.status == "completed" else EXIT_FAILED
            return exit_code

        finally:
            # 6. Cleanup. If we own the workdir and --no-cleanup wasn't
            #    passed, remove it on success; always keep on failure
            #    so the user can inspect.
            try:
                await orch.close_all_clients()
            except Exception:
                pass
            if owned_workdir and not args.persist and not args.no_cleanup:
                if exit_code == 0:  # type: ignore[name-defined]
                    shutil.rmtree(work_dir, ignore_errors=True)
            if not args.persist:
                # The DB is always temp.
                db_path.unlink(missing_ok=True)
    finally:
        if not args.persist:
            scratch_db_path = Path(scratch_db.name)
            # Best-effort: on Windows the SQLite file may still be
            # mmap'd for a moment after the loop closes, which
            # makes unlink() raise PermissionError. Swallow that.
            try:
                scratch_db_path.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _emit(args: argparse.Namespace, payload: Dict[str, Any]) -> None:
    """Write the result to stdout in the requested format."""
    if getattr(args, "json_output", False):
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        sys.stdout.flush()
    else:
        status = payload.get("status", "unknown")
        sys.stdout.write(f"=== kairos exec {status} ===\n")
        elapsed = payload.get("elapsed_s")
        if elapsed is not None:
            sys.stdout.write(f"elapsed: {elapsed}s\n")
        wd = payload.get("work_dir")
        if wd:
            sys.stdout.write(f"work_dir: {wd}\n")
        result = payload.get("result")
        if result:
            sys.stdout.write("\n--- plan ---\n")
            sys.stdout.write(result if isinstance(result, str) else
                             json.dumps(result, ensure_ascii=False, indent=2))
            sys.stdout.write("\n")
        err = payload.get("error")
        if err:
            sys.stdout.write(f"\n--- error ---\n{err}\n")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    """Top-level entry. Dispatches to `serve` or `exec` based on argv.

    Defaults to `serve` for backward compatibility (so `python -m
    kairos` still launches the HTTP server)."""
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    # No-arg call → serve (legacy default)
    if argv and argv[0] in ("--version", "-V", "version"):
        from kairos import __version__

        print(f"kairos-code {__version__}")
        return 0
    if not argv or argv[0] not in ("serve", "exec", "gate", "demo", "worker",
                                   "accept", "skeleton", "-h", "--help"):
        # Bare command (or unknown) → legacy server mode
        return _serve_legacy()

    args = parser.parse_args(argv)

    # Set up logging once. CLI runs are usually not too chatty; we
    # honor --quiet to suppress progress on stderr.
    logging.basicConfig(
        level=logging.WARNING if (hasattr(args, "quiet") and args.quiet)
        else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "serve":
        return _serve_legacy(host=args.host, port=args.port)
    if args.command == "exec":
        try:
            return asyncio.run(run_exec(args))
        except KeyboardInterrupt:
            print("\n[kairos] interrupted", file=sys.stderr)
            return 130  # POSIX SIGINT
    if args.command == "gate":
        return _run_gate(args)
    if args.command == "demo":
        return _run_demo(args)
    if args.command == "worker":
        return _run_worker(args)
    if args.command == "accept":
        try:
            return asyncio.run(run_accept(args))
        except KeyboardInterrupt:
            print("\n[kairos] interrupted", file=sys.stderr)
            return 130
    if args.command == "skeleton":
        return _run_skeleton(args)
    return EXIT_BAD_INPUT


def _run_demo(args: argparse.Namespace) -> int:
    """Dispatch `kairos demo` to :mod:`kairos.demo`."""
    from kairos import demo as demo_mod
    return demo_mod.main([
        "--lang", args.lang,
        "--format", args.format,
        "--timeout", str(args.timeout),
        *(["--out", args.out] if args.out else []),
        *(["--keep"] if args.keep else []),
        *(["--open"] if args.open_report else []),
        *(["--json"] if args.json_output else []),
        *(["--quiet"] if args.quiet else []),
    ])


def _run_worker(args: argparse.Namespace) -> int:
    """Dispatch ``kairos worker <attach|status|forget>``.

    Identity only: this reads and writes ``<repo>/.kairos/worker.json`` and
    never constructs the agent stack, so it is instant and works with no key
    configured. The project row itself is created on the first real dispatch.
    """
    from pathlib import Path as _Path

    from kairos import worker_identity

    repo = _Path(args.repo).expanduser().resolve()
    command = getattr(args, "worker_command", None) or "status"

    if command in ("attach", "status") and not repo.exists():
        print(f"error: no such directory: {repo}", file=sys.stderr)
        return EXIT_BAD_INPUT

    if command == "forget":
        removed = worker_identity.forget(repo)
        payload = {"repo": str(repo), "detached": removed}
        _emit_worker(args, payload, (
            f"[kairos] worker detached from {repo}" if removed
            else f"[kairos] no worker was bound to {repo}"
        ))
        return EXIT_OK

    if command == "attach":
        binding = worker_identity.bind(repo, name=args.name or "")
        payload = {
            "repo": binding.repo, "project_id": binding.project_id,
            "name": binding.name, "dispatches": binding.dispatches,
            "binding": binding.path, "created": not binding.is_persisted,
        }
        _emit_worker(args, payload, (
            f"[kairos] worker bound to {repo}\n"
            f"  project id : {binding.project_id}\n"
            f"  binding    : {binding.path or '(not persisted)'}\n"
            f"  dispatches : {binding.dispatches}\n"
            f"  → 这个仓库的每次派发都会落到同一个会话（不会每来一个任务就新建）。"
        ))
        return EXIT_OK

    if command == "status":
        binding = worker_identity.load(repo)
        if binding is None:
            payload = {"repo": str(repo), "bound": False}
            _emit_worker(args, payload,
                         f"[kairos] no worker bound to {repo} "
                         f"(run `kairos worker attach --repo {repo}`)")
            return EXIT_OK
        payload = {
            "repo": binding.repo, "bound": True,
            "project_id": binding.project_id, "name": binding.name,
            "dispatches": binding.dispatches,
            "first_task_id": binding.first_task_id,
            "last_task_id": binding.last_task_id,
            "binding": binding.path,
        }
        _emit_worker(args, payload, (
            f"[kairos] worker for {repo}\n"
            f"  project id : {binding.project_id}\n"
            f"  dispatches : {binding.dispatches}\n"
            f"  tasks      : {binding.first_task_id or '(none)'} … "
            f"{binding.last_task_id or '(none)'}"
        ))
        return EXIT_OK

    print(f"error: unknown worker command {command!r}", file=sys.stderr)
    return EXIT_BAD_INPUT


def _emit_worker(args: argparse.Namespace, payload: dict, text: str) -> None:
    if getattr(args, "json_output", False):
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _intake_llm():
    """Best-effort provider for reading the task document.

    Returns ``None`` when no model is configured — the intake then reads the
    document structurally, which is the whole point of the fallback. Never
    raises: a missing key must not stop a dispatch.
    """
    try:
        from pathlib import Path

        from kairos import config as _pkg_config
        from kairos.llm.model_router import ModelRouter

        cfg = Path(_pkg_config.__file__).parent / "models_config.yaml"
        router = ModelRouter(config_path=cfg.resolve())
        return router.get_provider_for_role("team_leader")
    except Exception as exc:  # noqa: BLE001 - degrade to structural reading
        logger.info("no model for intake (%s); reading the document "
                    "structurally", exc)
        return None


async def run_accept(args: argparse.Namespace) -> int:
    """Read a task document and report what it asks for.

    Writes our own record next to the repository (``.kairos/outbox``), so the
    understanding is auditable and the answers we still need are a file the
    caller can reply to in prose. Returns:

      0  understood
      3  nothing actionable in the document
      4  understood, but not safe to start unattended — see the questions
    """
    from pathlib import Path as _Path

    from kairos.intake import (
        Intake,
        RepoFacts,
        render_questions,
        render_understanding,
    )

    document = _Path(args.file).expanduser()
    if not document.exists() or not document.is_file():
        print(f"error: no such task document: {document}", file=sys.stderr)
        return EXIT_BAD_INPUT

    try:
        text = document.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"error: cannot read {document}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT

    repo = _Path(args.repo).expanduser().resolve() if args.repo \
        else document.resolve().parent
    facts = RepoFacts.gather(repo)
    llm = None if args.no_model else _intake_llm()
    cache_dir = repo / ".kairos" / "intake"
    intake = Intake(llm, cache_dir=cache_dir)

    result = await intake.accept(
        text, source_file=document.name, facts=facts,
        task_id=args.task_id or "",
    )

    out_dir = _Path(args.out).expanduser() if args.out \
        else repo / ".kairos" / "outbox"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{result.task_id}.intake.json").write_text(
            result.model_dump_json(indent=2), encoding="utf-8")
        (out_dir / f"{result.task_id}.understanding.md").write_text(
            render_understanding(result), encoding="utf-8")
        if result.questions:
            (out_dir / f"{result.task_id}.questions.md").write_text(
                render_questions(result), encoding="utf-8")
    except OSError as exc:
        print(f"[kairos] could not write the record ({exc})", file=sys.stderr)

    if getattr(args, "json_output", False):
        sys.stdout.write(result.model_dump_json(indent=2))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_understanding(result))
        if result.questions:
            sys.stdout.write("\n### 需要你确认\n")
            for index, question in enumerate(result.questions, 1):
                sys.stdout.write(f"{index}. {question}\n")
        sys.stdout.write(f"\n→ 记录已写入 {out_dir}\n")
    sys.stdout.flush()

    if result.blocked:
        return EXIT_BAD_INPUT
    if result.needs_confirmation:
        return EXIT_NEEDS_CONFIRMATION
    return EXIT_OK


def _run_gate(args: argparse.Namespace) -> int:
    """Dispatch ``kairos gate <subcommand>`` to :mod:`kairos.gate_report`.

    The report module owns its own argument parser (so it also works as
    ``python -m kairos.gate_report``); we translate the parsed namespace
    into that parser's argv instead of duplicating the logic here.
    """
    from kairos import gate_report as gate_mod
    if getattr(args, "gate_command", None) != "report":
        gate_mod.build_parser().print_help()
        return EXIT_BAD_INPUT
    argv = ["report", "--project", args.project, "--out", args.out,
            "--format", args.format, "--lang", args.lang]
    if args.session:
        argv += ["--session", args.session]
    if args.db:
        argv += ["--db", args.db]
    if args.quiet:
        argv += ["--quiet"]
    return gate_mod.main(argv)


# ---------------------------------------------------------------------------
# skeleton (domain-neutral worker -> verifier, without the code loop)
# ---------------------------------------------------------------------------


class _SkeletonInputError(ValueError):
    """Bad ``kairos skeleton`` input; maps to EXIT_BAD_INPUT."""


def _skeleton_provider_generator(provider):
    """Adapt a model provider into an async ``generate(prompt) -> str``."""
    from kairos.llm.base import LLMMessage

    async def _generate(prompt: str) -> str:
        response = await provider.complete([LLMMessage(role="user", content=prompt)])
        return getattr(response, "content", "") or ""

    return _generate


def _skeleton_substring_predicate(needle: str):
    """A rubric row: the deliverable (artifact, else worker output) contains it."""
    def _pred(workspace, task, result):
        text = ""
        try:
            text = workspace.read_output(task.output_name) or ""
        except Exception:
            text = ""
        if not text:
            text = result.output or ""
        return needle in text
    return _pred


def _prepare_skeleton_run(args: argparse.Namespace):
    """Build (workspace, worker, verifier, task, run_dir) from parsed args."""
    from kairos.skeleton import (
        AssertionVerifier,
        CitationConsistencyVerifier,
        DocSetWorkspace,
        OfflineGenerator,
        PromptWorker,
        ProjectTestsVerifier,
        RepoWorkspace,
        RubricVerifier,
        Task,
    )

    ws_root = Path(args.workspace).expanduser().resolve()
    if not ws_root.is_dir():
        raise _SkeletonInputError(f"no such workspace directory: {ws_root}")

    workspace = RepoWorkspace(ws_root) if args.kind == "repo" else DocSetWorkspace(ws_root)

    if args.generator == "offline":
        worker = PromptWorker(generate=OfflineGenerator())
    else:
        provider = _intake_llm()
        if provider is None:
            if args.generator == "model":
                raise _SkeletonInputError(
                    "--generator model: no model provider is configured"
                )
            worker = PromptWorker(generate=OfflineGenerator())
        else:
            worker = PromptWorker(generate=_skeleton_provider_generator(provider))

    if args.verifier == "tests":
        verifier = ProjectTestsVerifier()
    elif args.verifier == "citations":
        verifier = CitationConsistencyVerifier()
    elif args.verifier == "human":
        from kairos.skeleton import HumanVerifier
        verifier = HumanVerifier()
    elif args.verifier == "rubric":
        if not args.criterion:
            raise _SkeletonInputError(
                "--verifier rubric needs at least one --criterion SUBSTRING"
            )
        verifier = RubricVerifier(
            criteria=[(c, _skeleton_substring_predicate(c)) for c in args.criterion],
            threshold=args.threshold,
        )
    else:  # assertion
        if not args.check.strip():
            raise _SkeletonInputError(
                "--verifier assertion needs --check '<expression>'"
            )
        verifier = AssertionVerifier(check=args.check)

    task_text = args.task
    candidate = Path(args.task).expanduser()
    if candidate.is_file():
        try:
            task_text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise _SkeletonInputError(f"cannot read task file {candidate}: {exc}")
    if not task_text.strip():
        raise _SkeletonInputError("empty task")

    task = Task(instruction=task_text, output_name=args.out_name)
    run_dir = Path(args.run_dir).expanduser() if args.run_dir \
        else ws_root / ".kairos" / "skeleton-runs"
    return workspace, worker, verifier, task, run_dir


async def _execute_skeleton_run(worker, workspace, task, verifier, run_dir):
    from kairos.core.message_bus import MessageBus
    from kairos.skeleton import run_task

    return await run_task(
        worker, workspace, task, verifier, bus=MessageBus(), run_dir=run_dir,
    )


def _emit_skeleton_run(args: argparse.Namespace, run, workspace, run_dir) -> None:
    run_file = str(Path(run_dir) / f"skeleton-run-{run.run_id}.json")
    payload = {**run.to_dict(), "artifacts": list(workspace.outputs()),
               "run_file": run_file}
    if getattr(args, "json_output", False):
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        sys.stdout.flush()
        return
    sys.stdout.write(f"=== kairos skeleton run [{run.outcome}] ===\n")
    sys.stdout.write(f"attempts : {run.attempts}\n")
    sys.stdout.write(f"verifier : {run.verdict.verifier}\n")
    sys.stdout.write(f"reason   : {run.verdict.reason}\n")
    for ref in workspace.outputs():
        sys.stdout.write(f"artifact : {ref}\n")
    sys.stdout.write(f"run file : {run_file}\n")
    if run.blocked_on_human:
        sys.stdout.write(
            "→ 需要人工审批：kairos skeleton resume --run <file> --approve|--reject\n"
        )
    elif run.undecided:
        sys.stdout.write("→ 没有验证器能判定（verifier abstained）。\n")
    sys.stdout.flush()


def _run_skeleton(args: argparse.Namespace) -> int:
    """Dispatch ``kairos skeleton <run|resume>``."""
    command = getattr(args, "skeleton_command", None)
    if command == "resume":
        return _run_skeleton_resume(args)
    if command != "run":
        print("error: `kairos skeleton` needs a subcommand (run|resume)",
              file=sys.stderr)
        return EXIT_BAD_INPUT

    try:
        workspace, worker, verifier, task, run_dir = _prepare_skeleton_run(args)
    except _SkeletonInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT

    try:
        run = asyncio.run(_execute_skeleton_run(worker, workspace, task, verifier, run_dir))
    except KeyboardInterrupt:
        print("\n[kairos] interrupted", file=sys.stderr)
        return 130

    _emit_skeleton_run(args, run, workspace, run_dir)
    if run.passed:
        return EXIT_OK
    if run.blocked_on_human or run.undecided:
        return EXIT_NEEDS_CONFIRMATION
    return EXIT_FAILED


def _run_skeleton_resume(args: argparse.Namespace) -> int:
    """Answer a human-gated skeleton run, then persist/print the result."""
    from kairos.skeleton import SkeletonRun, resume_task

    path = Path(args.run).expanduser()
    if not path.is_file():
        print(f"error: no such run file: {path}", file=sys.stderr)
        return EXIT_BAD_INPUT
    try:
        prior = SkeletonRun.load(path)
    except Exception as exc:
        print(f"error: cannot read run file {path}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT

    approved = bool(getattr(args, "approve", False))

    async def _do():
        return await resume_task(prior, approved=approved, reason=args.reason,
                                 run_dir=path.parent)

    try:
        run = asyncio.run(_do())
    except KeyboardInterrupt:
        print("\n[kairos] interrupted", file=sys.stderr)
        return 130

    payload = {**run.to_dict(), "run_file": str(path)}
    if getattr(args, "json_output", False):
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(f"=== kairos skeleton resume [{run.outcome}] ===\n")
        sys.stdout.write(f"reason   : {run.verdict.reason}\n")
        sys.stdout.write(f"run file : {path}\n")
    sys.stdout.flush()
    return EXIT_OK if run.passed else EXIT_FAILED


def _serve_legacy(host: Optional[str] = None, port: Optional[int] = None) -> int:
    """Backwards-compatible server entry. Preserves the old
    `python -m kairos` behaviour so existing scripts keep working."""
    import uvicorn
    from kairos import __version__
    from kairos.config.settings import settings

    print(f"""
╔══════════════════════════════════════════════╗
║           Kairos Code v{__version__}                 ║
║   Multi-Agent Collaboration Platform         ║
╚══════════════════════════════════════════════╝
""")
    uvicorn.run(
        "api.app:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=settings.debug,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
