"""GrepTool — regex search across the project (sandboxed).

Modeled on ripgrep's most common flags. Returns matched lines with file
path + line number + content, capped to avoid flooding the LLM context.

Safety boundaries (all preserved / made explicit):

* build/vendor dirs in ``SKIP_DIRS`` are never descended into;
* ``max_files`` bounds how many files are read at all;
* ``max_file_bytes`` skips oversized files;
* files that look binary (a NUL byte in the first few KB) are skipped;
* shown paths are project-relative when the file lives under the anchor,
  and the absolute path otherwise (full-access search root).

Truncation contract (mirrors file_read / find):

* ``max_results`` caps how many match *lines* are shown.
* counting does **not** stop at ``max_results`` — the scan keeps going but
  stops *retaining* line strings, so ``total_matches`` is real instead of
  "how many we happened to return". To bound the extra CPU on a
  match-heavy tree the count itself is capped (see ``_COUNT_CEILING``);
  past it the total is a lower bound (``>=N``).
* whenever anything is withheld the output ends with an explicit marker
  naming shown/total/files; when nothing is withheld the output is exactly
  the match lines, with no marker.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Optional

from kairos.tools.base import BaseTool, ToolResult

# How far the scan keeps going after ``max_results`` lines have been
# collected. Past the cap we still match lines but only bump a counter — no
# line string is built — so the extra cost is regex CPU, not memory. This
# ceiling bounds that CPU on a match-heavy repo: once this many matches have
# been counted we stop scanning and report the total as a lower bound
# (">=N"). The earlier code stopped the whole scan at ``max_results``, so a
# real total was never known; the extra work here is the price of a truthful
# count, and the ceiling keeps that price bounded.
_COUNT_CEILING = 5_000

# Boundary defaults. A repo at or under these limits sees no behaviour change.
_DEFAULT_MAX_FILES = 5_000
_DEFAULT_MAX_FILE_BYTES = 5_000_000
_BINARY_SNIFF_BYTES = 8_192
_MAX_OUTPUT_CHARS = 50_000


class GrepTool(BaseTool):
    name = "grep"
    description = (
        "Search for a regex pattern across files in the project. "
        "Similar to ripgrep but simpler. Skips .git, node_modules, __pycache__, "
        "binary files, and other common build dirs. Returns up to max_results "
        "matches with file path, line number, and content. When the match cap "
        "is hit the output ends with an explicit [truncated: ...] marker "
        "giving the true number of matches and the number of files, and "
        "metadata carries truncated / total_matches / returned. An uncapped "
        "run carries no marker."
    )

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string",
                                "description": "Regex pattern to search for"},
                    "path": {"type": "string",
                             "description": "Directory to search in (relative to project root). Defaults to '.'."},
                    "glob": {"type": "string",
                              "description": "Optional glob filter, e.g. '*.py' or 'src/**/*.ts'."},
                    "ignore_case": {"type": "boolean", "default": False},
                    "max_results": {"type": "integer", "default": 100,
                                     "description": "Cap on shown match lines (keeps LLM "
                                                    "context sane). The true match count is "
                                                    "still reported; a capped result is never "
                                                    "silently mistaken for the whole set."},
                    "max_files": {"type": "integer", "default": _DEFAULT_MAX_FILES,
                                  "description": "Cap on how many files are read."},
                    "max_file_bytes": {"type": "integer", "default": _DEFAULT_MAX_FILE_BYTES,
                                       "description": "Files larger than this are skipped."},
                },
                "required": ["pattern"],
            },
        }

    @staticmethod
    def _coerce_int(value: Any, default: int, minimum: int = 1) -> int:
        """Best-effort int coercion for a value arriving from a JSON call."""
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return n if n >= minimum else minimum

    async def execute(self, pattern: str = "", path: str = ".",
                      glob: Optional[str] = None,
                      ignore_case: bool = False,
                      max_results: int = 100,
                      max_files: int = _DEFAULT_MAX_FILES,
                      max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES,
                      **kwargs) -> ToolResult:
        if not pattern:
            return ToolResult(success=False, output="", error="pattern is required")
        try:
            root = self._resolve_safe(path)
        except PermissionError as e:
            return ToolResult(success=False, output="", error=str(e))

        flags = re.IGNORECASE if ignore_case else 0
        try:
            compiled = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(success=False, output="", error=f"invalid regex: {e}")

        cap = self._coerce_int(max_results, 100)
        file_cap = self._coerce_int(max_files, _DEFAULT_MAX_FILES)
        byte_cap = self._coerce_int(max_file_bytes, _DEFAULT_MAX_FILE_BYTES)
        ceiling = max(cap + 1, _COUNT_CEILING)

        # Common binary / heavy dirs to skip — saves wall time and keeps
        # noise out of results.
        SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv",
                     "dist", "build", "target", ".next", ".pytest_cache"}

        def iter_files(p: Path):
            if p.is_file():
                yield p
                return
            for child in p.rglob("*") if not glob else p.glob(glob):
                if child.is_dir():
                    continue
                if any(part in SKIP_DIRS for part in child.parts):
                    continue
                yield child

        def display_path(f: Path) -> str:
            try:
                return str(f.relative_to(self._allowed_root))
            except ValueError:
                # Under full access the searched root can sit outside the
                # worktree this tool is anchored to (an absolute path into
                # the real work_dir), and the unguarded relative_to escaped
                # execute() as a raw "is not in the subpath of ..." error --
                # ValueError is not an OSError, so a handler around the file
                # read never saw it. FindTool has always kept the absolute
                # path in exactly this case.
                return str(f)

        matches: list[str] = []
        matched_files: set[str] = set()
        total = 0
        total_exact = True
        files_scanned = 0
        files_truncated = False
        skipped_large = 0
        skipped_binary = 0

        for f in iter_files(root):
            if files_scanned >= file_cap:
                # More files exist than we were allowed to read.
                files_truncated = True
                break
            files_scanned += 1
            try:
                if f.stat().st_size > byte_cap:
                    skipped_large += 1
                    continue
                data = f.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:_BINARY_SNIFF_BYTES]:
                skipped_binary += 1
                continue
            text = data.decode("utf-8", errors="ignore")
            try:
                # StringIO reproduces the original text-mode iteration
                # (universal newlines, no phantom trailing line) while
                # letting us sniff the bytes once for binary content.
                for lineno, line in enumerate(io.StringIO(text), start=1):
                    if not compiled.search(line):
                        continue
                    total += 1
                    rel = display_path(f)
                    if len(matches) < cap:
                        matches.append(f"{rel}:{lineno}:{line.rstrip()}")
                    matched_files.add(rel)
                    if total >= ceiling:
                        total_exact = False
                        break
            except (OSError, UnicodeError):
                continue
            if total >= ceiling:
                break

        # ---- no matches -----------------------------------------------------
        if total == 0:
            meta = {
                "files_scanned": files_scanned,
                "files_with_matches": 0,
                "matches": 0,
                "returned": 0,
                "total_matches": 0,
                "total_is_lower_bound": False,
                "truncated": files_truncated,
                "files_truncated": files_truncated,
                "skipped_large_files": skipped_large,
                "skipped_binary_files": skipped_binary,
            }
            if files_truncated:
                return ToolResult(
                    success=True,
                    output=(f"(no matches in the first {files_scanned} files; "
                            f"max_files={file_cap} stopped the scan early — "
                            f"raise max_files or narrow the path to search the "
                            f"rest)"),
                    metadata=meta,
                )
            return ToolResult(success=True,
                              output=f"(no matches in {files_scanned} files)",
                              metadata=meta)

        # ---- trim to the character budget (line-aligned) --------------------
        shown = matches
        out = "\n".join(shown)
        if len(out) > _MAX_OUTPUT_CHARS:
            kept: list[str] = []
            used = 0
            for ln in shown:
                add = len(ln) + (1 if kept else 0)
                if used + add > _MAX_OUTPUT_CHARS:
                    break
                kept.append(ln)
                used += add
            shown = kept
            out = "\n".join(shown)

        returned = len(shown)
        truncated = returned < total or files_truncated

        if truncated:
            if returned < total:
                shown_total = str(total) if total_exact else f">={total}"
                out += (f"\n\n[truncated: showing {returned} of {shown_total} "
                        f"matches across {len(matched_files)} files; narrow the "
                        f"pattern/path or raise max_results]")
            else:
                # Every match we found was shown, but the scan stopped before
                # covering the whole tree, so the set may be incomplete.
                out += (f"\n\n[truncated: scanned only the first "
                        f"{files_scanned} files (max_files={file_cap}); more "
                        f"matches may exist — narrow the path or raise "
                        f"max_files]")

        return ToolResult(
            success=True,
            output=out,
            metadata={
                "files_scanned": files_scanned,
                "files_with_matches": len(matched_files),
                # `matches` is kept for backward compatibility and equals the
                # number of lines actually shown; `total_matches` is the true
                # number of matches (a lower bound when total_is_lower_bound).
                "matches": returned,
                "returned": returned,
                "total_matches": total,
                "total_is_lower_bound": not total_exact,
                "truncated": truncated,
                "files_truncated": files_truncated,
                "skipped_large_files": skipped_large,
                "skipped_binary_files": skipped_binary,
            },
        )
