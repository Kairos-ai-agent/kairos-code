"""Two Workspace implementations: a git repo, and a pile of documents.

``RepoWorkspace``  -- today's behaviour: a working tree; supports git + tests.
``DocSetWorkspace`` -- the non-code case: a directory of documents, no git, no
tests. Reading three docs and writing a report is a first-class task here.
"""
from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import List, Optional

from kairos.skeleton.contracts import Workspace


def _safe_join(base: Path, ref: str) -> Path:
    """Join ``ref`` under ``base``, refusing to escape it.

    ``ref`` is a workspace-relative name from ``resources()`` / a task, but a
    hostile or buggy caller could pass ``../../etc/passwd``; keep every read
    and write inside the workspace root.
    """
    candidate = (base / ref).resolve()
    base_resolved = base.resolve()
    if candidate != base_resolved and base_resolved not in candidate.parents:
        raise ValueError(f"ref escapes workspace root: {ref!r}")
    return candidate


class FileWorkspace(Workspace):
    """Shared read/emit/as_context plumbing over a source dir + an output dir."""

    kind = "files"
    capabilities = frozenset({"read", "write"})

    def __init__(self, root, source_dir: str = ".", output_dir: str = "outputs"):
        self.root = Path(root)
        if source_dir in ("", "."):
            self.source_dir = self.root
        else:
            self.source_dir = self.root / source_dir
        self.output_dir = self.root / output_dir
        self._emitted: List[str] = []

    # -- reads ------------------------------------------------------------
    def read(self, ref: str) -> str:
        path = _safe_join(self.source_dir, ref)
        return path.read_text(encoding="utf-8", errors="replace")

    def _list_source_files(self) -> List[str]:
        if not self.source_dir.exists():
            return []
        files = []
        for p in sorted(self.source_dir.rglob("*")):
            if not p.is_file():
                continue
            # A resource listing is about *inputs*. Two kinds of thing are the
            # workspace's own byproduct, not an input, and handing them to the
            # worker as context is noise:
            #   * the deliverable directory (under the source dir whenever the
            #     two share a root, i.e. ``--workspace <docs-dir>``);
            #   * the run-record directory ``.kairos`` the skeleton writes.
            if self.output_dir == p or self.output_dir in p.parents:
                continue
            rel = p.relative_to(self.source_dir)
            if any(part == ".kairos" for part in rel.parts):
                continue
            files.append(str(rel).replace("\\", "/"))
        return files

    def resources(self) -> List[str]:
        return self._list_source_files()

    # -- writes -----------------------------------------------------------
    def emit(self, name: str, content: str) -> str:
        path = _safe_join(self.output_dir, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        ref = str(path.relative_to(self.root)).replace("\\", "/")
        if ref not in self._emitted:
            self._emitted.append(ref)
        return ref

    def read_output(self, name: str) -> Optional[str]:
        path = _safe_join(self.output_dir, name)
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
        return None

    def outputs(self) -> List[str]:
        return list(self._emitted)


class RepoWorkspace(FileWorkspace):
    """The code case: a git working tree that supports git and running tests.

    ``resources()`` prefers the git index (tracked files); it falls back to a
    directory walk when git is unavailable so the workspace still works in a
    plain directory or a shallow checkout.
    """

    kind = "repo"
    capabilities = frozenset({"read", "write", "git", "tests"})

    def __init__(self, root, output_dir: Optional[str] = None, *, run_id: str = ""):
        # A repo workspace used to write artifacts into the repository root
        # (``output_dir="."``), which polluted the working tree. Default to a
        # run-scoped directory instead, so many runs never collide and the
        # repo root stays clean; pass ``output_dir`` explicitly to override.
        if output_dir is None:
            run_id = run_id or uuid.uuid4().hex[:8]
            output_dir = f".kairos/skeleton-runs/{run_id}"
        super().__init__(root, source_dir=".", output_dir=output_dir)

    @property
    def work_dir(self) -> str:
        """Native path string, for adapters that speak to the Coder."""
        return str(self.root)

    def _git_tracked(self) -> List[str]:
        try:
            from kairos.platform_flags import hidden_kwargs
            proc = subprocess.run(
                ["git", "ls-files"],
                cwd=str(self.root), capture_output=True, text=True,
                timeout=10, check=False, **hidden_kwargs(),
            )
            if proc.returncode == 0:
                return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        except Exception:
            pass
        return []

    def resources(self) -> List[str]:
        tracked = self._git_tracked()
        if tracked:
            return tracked
        return self._list_source_files()

    def as_context(self, refs: Optional[List[str]] = None) -> str:
        # A repo can have thousands of files; dumping them into a prompt is
        # useless. Render a bounded listing instead of file bodies.
        chosen = list(refs) if refs is not None else self.resources()
        head = chosen[:200]
        listing = "\n".join(head)
        more = f"\n(... {len(chosen) - len(head)} more files)" if len(chosen) > len(head) else ""
        return f"REPO FILES ({len(chosen)}):\n{listing}{more}"

    def as_prompt_context(
        self, refs: Optional[List[str]] = None, *, max_chars: Optional[int] = None,
        query: Optional[str] = None,
    ) -> str:
        # Deliberately NOT the base body-inlining behaviour: a repository's
        # files are read through the Coder's tools, not crammed into a prompt.
        # Keep the same bounded listing ``as_context`` has always produced, so
        # adding the capped renderer to the interface does not change what a
        # repo worker sees. (``max_chars`` and ``query`` are accepted and
        # ignored -- a listing is already bounded and already task-agnostic by
        # design; a repo worker greps for what it needs.)
        return self.as_context(refs)


class DocSetWorkspace(FileWorkspace):
    """The non-code case: a set of documents. No git, no tests.

    Inputs live in ``source_dir`` (default ``docs/``); deliverables are written
    to ``output_dir`` (default ``outputs/``). Nothing here shells out to git or
    pytest -- that is the whole point.
    """

    kind = "docs"
    # Deliberately NO "git" and NO "tests": this is a document workspace.
    capabilities = frozenset({"read", "write"})

    def __init__(self, root, source_dir: str = "docs", output_dir: str = "outputs"):
        super().__init__(root, source_dir=source_dir, output_dir=output_dir)
        # ``kairos skeleton run --kind docs --workspace <dir>`` may be pointed
        # at a project root (inputs live in ``<dir>/docs``) *or* straight at the
        # document folder itself. When the conventional subdir is absent but the
        # root holds files, treat the root as the document set -- otherwise
        # ``resources()`` comes back empty and the worker is handed a blank
        # context (the real-model failure this guards against: the model then
        # honestly answers "no documents were provided").
        if not self.source_dir.exists() and source_dir not in ("", "."):
            try:
                if self.root.is_dir() and any(p.is_file() for p in self.root.rglob("*")):
                    self.source_dir = self.root
            except OSError:
                pass
