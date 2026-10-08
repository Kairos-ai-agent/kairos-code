"""Mixin OrchReferenceMixin — split from kairos/core/orchestrator.py."""
from __future__ import annotations
import asyncio
import dataclasses
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
import importlib as _il
from kairos.core.message_bus import Message, MessageBus
from kairos.core.persistence import Persistence
from kairos.llm.base import LLMConfig
from kairos.llm.model_router import ModelRouter
from kairos.tools.base import ToolResult
from kairos.tools.code_search import CodeSearchTool
from kairos.tools.file_edit import FileEditReplaceTool, FileEditTool, MultiEditTool
from kairos.tools.file_read import FileReadTool
from kairos.tools.find import FindTool
from kairos.tools.git_tool import GitTool
from kairos.tools.grep_tool import GrepTool
from kairos.tools.subagent import SubagentTool
from kairos.tools.terminal import TerminalTool
from kairos.tools.webfetch import WebFetchTool, WebSearchTool

logger = logging.getLogger(__name__)


class OrchReferenceMixin:
    def save_requirements(self, project_id: str, requirements: str):
        project = self._projects.get(project_id)
        if not project:
            return
        project.requirements = requirements
        self._db.save_project(project)

    def add_reference_file(self, project_id: str, name: str, mime: str,
                            content) -> str:
        """Persist a reference file. `content` may be bytes (from the
        HTTP upload endpoint) or a str (already-decoded text). We
        coerce to UTF-8 text so the schema column stays TEXT, and
        generate the row id + size here so the caller can return
        the file_id without knowing the SQLite schema.
        """
        if isinstance(content, (bytes, bytearray)):
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                import base64 as _b64
                text = _b64.b64encode(bytes(content)).decode("ascii")
        else:
            text = str(content or "")
        file_id = uuid.uuid4().hex
        size = len(text)
        self._db.add_file(file_id, project_id, name, mime or "application/octet-stream", size, text)
        return file_id

    def list_reference_files(self, project_id: str) -> list:
        return self._db.list_files(project_id)

    def get_reference_file(self, file_id: str) -> Optional[dict]:
        return self._db.get_file(file_id)

    def delete_reference_file(self, file_id: str) -> bool:
        return self._db.delete_file(file_id)

    def build_reference_digest(self, project_id: str, query: str = "") -> str:
        """Build a prompt-friendly digest of all uploaded reference
        files. Small files are inlined verbatim so the Coder can
        read them; large files are truncated to a preview with a
        `… truncated` marker. Returns "" when no files exist so
        the caller can splice it in without a sentinel check.

        ``query`` (the requirement) is optional. When the project has more
        reference files than the digest can inline, they are ordered by
        relevance to ``query`` using the file-content RAG index
        (``Persistence.search_files_content``) so the *relevant* files make the
        cut instead of whichever happened to be uploaded first. A project at or
        under the inline limit, or a call with no query, keeps the old
        newest-first order exactly.
        """
        files = self._db.list_files(project_id)
        if not files:
            return ""
        inline_limit = 8
        ranked = False
        if query and query.strip() and len(files) > inline_limit:
            try:
                hit_result = self._db.search_files_content(
                    query, k=len(files), project_id=project_id)
                hits = hit_result.get("hits") or []
                if hits:
                    rank = {h.get("file_id"): i for i, h in enumerate(hits)}
                    files = sorted(files, key=lambda f: rank.get(f["id"], len(files)))
                    ranked = True
            except Exception:
                logger.debug("reference digest: relevance ranking failed (non-fatal)",
                             exc_info=True)
                files = self._db.list_files(project_id)
        chunks = ["## 参考资料"]
        if ranked:
            chunks.append("(ranked by relevance to the task; most relevant first)")
        preview_bytes = 3000
        for meta in files[:inline_limit]:
            payload = self._db.get_file(meta["id"])
            if not payload:
                continue
            raw = payload.get("content", "")
            if isinstance(raw, (bytes, bytearray)):
                body = raw.decode("utf-8", errors="replace")
            else:
                body = str(raw or "")
            name = meta.get("name", "?")
            size = int(meta.get("size") or len(body))
            if size <= preview_bytes:
                chunks.append("### " + name + "\n```\n" + body + "\n```")
            else:
                head = body[:preview_bytes]
                chunks.append(
                    "### " + name
                    + "\n```\n" + head
                    + "\n\n… truncated (showing first " + str(preview_bytes)
                    + " of " + str(size) + " bytes)\n```"
                )
        return "\n\n".join(chunks)
