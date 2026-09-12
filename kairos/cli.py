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
    if not argv or argv[0] not in ("serve", "exec", "gate", "demo", "-h", "--help"):
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
