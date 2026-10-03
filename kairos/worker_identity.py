"""Worker identity — one repository is one worker, not one task.

Why this module exists
----------------------
``Orchestrator.create_project`` mints ``uuid4().hex[:8]``. That is right for a
human clicking "new project" and wrong for a teammate: a main agent that
dispatches five tasks in a row would get five projects, five memories, five
histories — a colleague with no memory of yesterday. The requirement is the
opposite: *do not start a new session for every task.*

So the identity of a worker is derived from the **repository**, and it is
written down once::

    <repo>/.kairos/worker.json
    {"repo": "...", "project_id": "w3f9a1c2", "dispatches": 4, ...}

Every dispatch into that repo reads the file and lands on the same
``project_id``. Because the orchestrator rehydrates projects from the database
on startup (``_load_projects``), and because per-project memory, notes,
preferences, checkpoints and artifacts are all keyed by that id, "same
project id" *is* "same session" — across tasks, and across restarts.

Design notes
------------
* **No file needed to be stable.** ``project_id`` is derived from the repo path
  when the file is missing, so a lost ``.kairos/`` (fresh clone, cleaned tree)
  still resolves to the same id on the same machine. The file exists to be
  explicit, not to be load-bearing.
* **Our state must never show up in the main agent's diff.** The binding lives
  under ``.kairos/``, which we mark ignored with a ``.gitignore`` holding ``*``.
  A worker that pollutes the diff it is asking to be reviewed has failed.
* **Read-only checkouts still work.** If the repo cannot be written to, the
  binding goes to the app data directory instead, keyed by the repo path.
* **Never raises.** Identity problems degrade to "derive an id", because
  refusing to work over a corrupt JSON file helps nobody.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
KAIROS_DIR = ".kairos"
BINDING_NAME = "worker.json"
_GITIGNORE_BODY = "# Kairos worker state. Not part of the project.\n*\n"


@dataclass
class WorkerBinding:
    """The durable identity of one worker attached to one repository."""

    repo: str
    project_id: str
    name: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    dispatches: int = 0
    first_task_id: str = ""
    last_task_id: str = ""
    schema_version: int = SCHEMA_VERSION
    path: str = field(default="", compare=False)

    @property
    def is_persisted(self) -> bool:
        """True when this binding came off disk (rather than being derived)."""
        return bool(self.path)

    def summary(self) -> str:
        return (f"{self.project_id} · {self.dispatches} 次派发 · "
                f"最近任务 {self.last_task_id or '(无)'}")


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def repo_key(repo: str | Path) -> str:
    """A stable key for a repository path.

    Resolved and case-folded, because ``D:\\Work\\Proj`` and ``d:/work/proj``
    are the same working copy on Windows and must not become two workers."""
    try:
        resolved = Path(repo).expanduser().resolve()
    except OSError:
        resolved = Path(repo).expanduser().absolute()
    return os.path.normcase(str(resolved))


def derive_project_id(repo: str | Path) -> str:
    """A stable id for a repo, used when no binding file exists yet.

    Shaped like the ids the orchestrator already generates (8 characters) so
    nothing downstream — UI, logs, filters — needs to know the difference."""
    digest = hashlib.sha256(repo_key(repo).encode("utf-8")).hexdigest()
    return "w" + digest[:7]


def binding_path(repo: str | Path) -> Path:
    return Path(repo).expanduser() / KAIROS_DIR / BINDING_NAME


def fallback_binding_path(repo: str | Path,
                          fallback_dir: Path | None = None) -> Path | None:
    """Where to keep the binding when the repo itself is not writable."""
    if fallback_dir is None:
        try:
            from kairos.config.settings import settings
            fallback_dir = Path(settings.data_dir) / "workers"
        except Exception:  # noqa: BLE001 - settings is optional here
            return None
    digest = hashlib.sha256(repo_key(repo).encode("utf-8")).hexdigest()[:16]
    return Path(fallback_dir) / f"{digest}.json"


def ensure_kairos_dir(repo: str | Path) -> Path | None:
    """Create ``.kairos/`` and keep it out of the repository's diffs."""
    directory = Path(repo).expanduser() / KAIROS_DIR
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug("cannot create %s: %s", directory, exc)
        return None
    ignore = directory / ".gitignore"
    try:
        if not ignore.exists():
            ignore.write_text(_GITIGNORE_BODY, encoding="utf-8")
    except OSError as exc:
        logger.debug("cannot write %s: %s", ignore, exc)
    return directory


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def _read(path: Path) -> WorkerBinding | None:
    try:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("unreadable worker binding at %s (%s); re-deriving",
                       path, exc)
        return None
    if not isinstance(payload, dict) or not payload.get("project_id"):
        return None
    binding = WorkerBinding(
        repo=str(payload.get("repo") or ""),
        project_id=str(payload["project_id"]),
        name=str(payload.get("name") or ""),
        created_at=float(payload.get("created_at") or 0.0),
        updated_at=float(payload.get("updated_at") or 0.0),
        dispatches=int(payload.get("dispatches") or 0),
        first_task_id=str(payload.get("first_task_id") or ""),
        last_task_id=str(payload.get("last_task_id") or ""),
        schema_version=int(payload.get("schema_version") or SCHEMA_VERSION),
        path=str(path),
    )
    return binding


def _write(binding: WorkerBinding, path: Path) -> bool:
    """Write atomically: a half-written binding would be worse than none."""
    payload = asdict(binding)
    payload.pop("path", None)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        logger.warning("cannot persist worker binding to %s: %s", path, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def load(repo: str | Path,
         fallback_dir: Path | None = None) -> WorkerBinding | None:
    """Read the binding if one exists. Does not create anything."""
    primary = _read(binding_path(repo))
    if primary is not None:
        return primary
    fallback = fallback_binding_path(repo, fallback_dir)
    if fallback is not None:
        return _read(fallback)
    return None


def bind(repo: str | Path, *, name: str = "", persist: bool = True,
         fallback_dir: Path | None = None) -> WorkerBinding:
    """Find the binding for a repo, creating it once if there is none.

    This is the only thing a caller needs: call it on every dispatch and the
    same repo keeps resolving to the same worker."""
    root = Path(repo).expanduser()
    existing = load(root, fallback_dir)
    if existing is not None and existing.project_id:
        if name and not existing.name:
            existing.name = name
        return existing

    now = time.time()
    binding = WorkerBinding(
        repo=str(root),
        project_id=derive_project_id(root),
        name=name or root.name,
        created_at=now,
        updated_at=now,
    )
    if not persist:
        return binding

    directory = ensure_kairos_dir(root)
    if directory is not None and _write(binding, directory / BINDING_NAME):
        binding.path = str(directory / BINDING_NAME)
        return binding

    fallback = fallback_binding_path(root, fallback_dir)
    if fallback is not None and _write(binding, fallback):
        binding.path = str(fallback)
        logger.info("worker binding for %s kept outside the repo (read-only)",
                    root)
        return binding

    logger.warning("worker binding for %s could not be persisted; the id is "
                   "derived and stable, so behaviour still holds", root)
    return binding


def touch(binding: WorkerBinding, task_id: str = "",
          fallback_dir: Path | None = None) -> WorkerBinding:
    """Record that a task was dispatched. Best effort, never raises."""
    binding.dispatches += 1
    if task_id:
        if not binding.first_task_id:
            binding.first_task_id = task_id
        binding.last_task_id = task_id
    binding.updated_at = time.time()
    if binding.path:
        _write(binding, Path(binding.path))
    return binding


def forget(repo: str | Path, fallback_dir: Path | None = None) -> bool:
    """Detach a worker from a repo. The project itself is left alone — this
    only removes the pointer, so the history stays recoverable."""
    removed = False
    for path in (binding_path(repo), fallback_binding_path(repo, fallback_dir)):
        if path is None:
            continue
        try:
            if path.exists():
                path.unlink()
                removed = True
        except OSError as exc:
            logger.debug("cannot remove %s: %s", path, exc)
    return removed
