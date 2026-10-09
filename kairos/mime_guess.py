"""A filename -> mime-type guess that does not depend on the host registry.

Windows' ``mimetypes`` module only knows what the registry maps, so common
dev/text formats (``.md``, ``.py``, ``.csv`` ...) come back as ``None`` (and
``.csv`` maps to ``application/vnd.ms-excel``, useless to a model or a
browser). This table fills the gaps explicitly and is the **single** source of
truth: the ``[附件]`` block, the artifact entries a chat reports, and the
download endpoint's ``Content-Type`` all resolve a name through here, so they
can never disagree.

Kept in a neutral module (not under ``api/``) so importing it never drags the
web app in -- importing the API layer from inside a running event loop
deadlocks, which is exactly the trap a shared helper call must avoid.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

#: Formats whose suffix is not (usefully) in the host registry's table.
EXTRA_MIME_TYPES = {
    ".md": "text/markdown", ".markdown": "text/markdown",
    ".txt": "text/plain", ".log": "text/plain", ".env": "text/plain",
    ".csv": "text/csv", ".tsv": "text/tab-separated-values",
    ".json": "application/json", ".jsonl": "application/json",
    ".yaml": "application/yaml", ".yml": "application/yaml",
    ".toml": "application/toml", ".ini": "text/plain",
    ".py": "text/x-python", ".pyi": "text/x-python",
    ".js": "text/javascript", ".jsx": "text/jsx",
    ".ts": "text/typescript", ".tsx": "text/tsx",
    ".sh": "application/x-sh", ".bat": "application/x-batch",
    ".ps1": "application/x-powershell",
    ".c": "text/x-c", ".h": "text/x-c", ".hpp": "text/x-c++",
    ".cpp": "text/x-c++", ".cc": "text/x-c++",
    ".rs": "text/x-rust", ".go": "text/x-go", ".java": "text/x-java",
    ".kt": "text/x-kotlin", ".rb": "text/x-ruby", ".php": "text/x-php",
    ".sql": "application/sql", ".ipynb": "application/json",
    ".html": "text/html", ".htm": "text/html", ".css": "text/css",
    ".scss": "text/scss", ".xml": "application/xml",
    ".svg": "image/svg+xml", ".pdf": "application/pdf",
}


def guess_mime(name: str) -> str:
    """Mime type for a filename, with the fallback table above.

    The table wins over ``mimetypes`` so the answer does not depend on the
    host's registry. Unknown suffixes fall back to ``mimetypes`` and then to
    ``application/octet-stream``.
    """
    suffix = Path(str(name or "")).suffix.lower()
    if suffix in EXTRA_MIME_TYPES:
        return EXTRA_MIME_TYPES[suffix]
    return (mimetypes.guess_type(str(name or ""))[0]
            or "application/octet-stream")


__all__ = ["EXTRA_MIME_TYPES", "guess_mime"]
