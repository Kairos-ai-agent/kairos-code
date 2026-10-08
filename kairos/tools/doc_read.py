"""``doc_read`` — extract the text of a Word ``.docx`` or PowerPoint ``.pptx``.

Both formats are ZIP containers of XML. The text lives in ``<w:t>`` runs inside
``<w:p>`` paragraphs (Word) and in ``<a:t>`` runs inside ``<a:p>`` paragraphs
(PowerPoint). Pulling that out needs :mod:`zipfile` + :mod:`xml.etree` and
nothing else, so Office *reading* adds no dependency (``python-docx`` /
``python-pptx`` are not declared and not required).

Read-only and confined to the tool's root — it declares only
:data:`Capability.READ_FILE`. A corrupt file or a legacy ``.doc``/``.ppt``
reports a readable line instead of a traceback.
"""
from __future__ import annotations

import zipfile
from typing import Any, List, Tuple
from xml.etree import ElementTree as ET

from kairos.capabilities import Capability
from kairos.tools.base import BaseTool, ToolResult
from kairos.tools.zipguard import ZipBombError, guard_zip

MAX_OUTPUT = 50_000
DEFAULT_MAX_CHARS = 50_000
MAX_CHARS_LIMIT = 400_000

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"


class DocReadError(Exception):
    """A problem worth showing to the user, not a traceback."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _paragraph_text(node: ET.Element) -> str:
    """Concatenate a paragraph's text runs, honouring tabs and breaks."""
    parts: List[str] = []
    for child in node.iter():
        tag = _local(child.tag)
        if tag == "t":
            parts.append(child.text or "")
        elif tag == "tab":
            parts.append("\t")
        elif tag in ("br", "cr"):
            parts.append("\n")
    return "".join(parts)


def _docx_text(zf: zipfile.ZipFile, names: List[str]) -> str:
    if "word/document.xml" not in names:
        raise DocReadError("no word/document.xml part (not a Word .docx)")
    root = ET.fromstring(zf.read("word/document.xml"))
    lines: List[str] = []
    for node in root.iter():
        if _local(node.tag) == "p":
            lines.append(_paragraph_text(node))
    return "\n".join(lines)


def _pptx_text(zf: zipfile.ZipFile, names: List[str]) -> str:
    def slide_no(part: str) -> int:
        tail = part.rsplit("/", 1)[-1].replace("slide", "").replace(".xml", "")
        return int(tail) if tail.isdigit() else 0

    slides = sorted((n for n in names
                     if n.startswith("ppt/slides/slide") and n.endswith(".xml")),
                    key=slide_no)
    if not slides:
        raise DocReadError("no ppt/slides/slideN.xml parts (not a PowerPoint "
                           ".pptx)")
    blocks: List[str] = []
    for i, part in enumerate(slides, start=1):
        root = ET.fromstring(zf.read(part))
        lines = [_paragraph_text(node) for node in root.iter()
                 if _local(node.tag) == "p"]
        body = "\n".join(ln for ln in lines if ln.strip())
        blocks.append(f"--- slide {i} ---\n{body if body else '(no text)'}")
    return "\n\n".join(blocks)


def _truncate(text: str, limit: int) -> Tuple[str, bool, int]:
    if len(text) <= limit:
        return text, False, 0
    cut = text.rfind("\n", 0, limit)
    if cut <= 0:
        cut = limit
    omitted = len(text) - cut
    shown = text[:cut] + (
        f"\n\n[truncated: text capped at {limit} characters; {omitted} "
        f"characters omitted. Raise max_chars, or read a narrower document.]"
    )
    return shown, True, omitted


class DocReadTool(BaseTool):
    """Extract the text of a Word ``.docx`` or PowerPoint ``.pptx`` document.

    Word documents are returned as their paragraphs; PowerPoint decks as one
    text block per slide. Only the text is extracted — formatting, images and
    layout are not reported.

    Example usage:
        - Word: {"path": "spec.docx"}
        - Deck: {"path": "pitch.pptx"}
        - Bounded: {"path": "long.docx", "max_chars": 8000}
    """

    name = "doc_read"
    description = (
        "Extract the plain text of a Word .docx or PowerPoint .pptx document "
        "(read-only, no Office needed). Word is returned paragraph by "
        "paragraph, a deck slide by slide. Use it to read a document's text; "
        "for spreadsheets use xlsx_read instead."
    )

    capabilities = frozenset({Capability.READ_FILE})

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": ("Path to the .docx/.pptx file (relative "
                                        "to the project directory)."),
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": (f"Character budget (default "
                                        f"{DEFAULT_MAX_CHARS})."),
                    },
                },
                "required": ["path"],
            },
        }

    async def execute(self, path: str = "", max_chars: Any = DEFAULT_MAX_CHARS,
                      **kwargs) -> ToolResult:
        if not path:
            return ToolResult(success=False, output="",
                              error="doc_read needs a path")
        try:
            file_path = self._resolve_safe(path)
        except PermissionError as exc:
            return ToolResult(success=False, output="", error=str(exc))
        if not file_path.exists():
            return ToolResult(success=False, output="",
                              error=f"File not found: {path}")
        if file_path.is_dir():
            return ToolResult(success=False, output="",
                              error=f"{path} is a directory, not a document")

        try:
            limit = int(max_chars)
        except (TypeError, ValueError):
            limit = DEFAULT_MAX_CHARS
        limit = max(200, min(MAX_CHARS_LIMIT, limit))

        try:
            # Sniff only the first 8 bytes -- read exactly that many, never the
            # whole file (``read_bytes()[:8]`` pulled the entire document into
            # memory just to look at its magic number).
            with open(file_path, "rb") as _fh:
                head = _fh.read(8)
        except OSError as exc:
            return ToolResult(success=False, output="",
                              error=f"Could not read {path}: {exc}")
        if head.startswith(_OLE_MAGIC):
            return ToolResult(
                success=False, output="",
                error=(f"{path} is a legacy binary Office file (.doc/.ppt). "
                       f"doc_read only understands the .docx/.pptx format; "
                       f"re-save it as .docx or .pptx first."))

        try:
            with zipfile.ZipFile(file_path) as zf:
                # Refuse a ZIP bomb *before* decompressing anything: the guard
                # reads the central directory only (kairos.tools.zipguard).
                guard_zip(zf, path)
                names = zf.namelist()
                if "word/document.xml" in names:
                    kind, text = "docx", _docx_text(zf, names)
                elif any(n.startswith("ppt/slides/slide") for n in names):
                    kind, text = "pptx", _pptx_text(zf, names)
                else:
                    raise DocReadError(
                        "not a Word .docx or PowerPoint .pptx document "
                        "(no recognised Office part inside the ZIP)")
        except zipfile.BadZipFile:
            return ToolResult(
                success=False, output="",
                error=(f"{path} is not a readable .docx/.pptx document "
                       f"(the ZIP container is corrupt or the file is not "
                       f"actually an Office file)."))
        except ET.ParseError as exc:
            return ToolResult(
                success=False, output="",
                error=(f"{path} contains malformed XML inside the document "
                       f"({exc}); the file is corrupt."))
        except ZipBombError as exc:
            # A declared-size guard hit: the file looks like (or is) a bomb.
            # Reported as-is -- the message names the limit and the value.
            return ToolResult(success=False, output="", error=str(exc))
        except DocReadError as exc:
            return ToolResult(success=False, output="", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - one readable line, no stack
            return ToolResult(
                success=False, output="",
                error=(f"Could not read {path}: {type(exc).__name__}: {exc}"))

        output, truncated, omitted = _truncate(text, limit)
        header = f"# doc_read: {path} ({kind})"
        return ToolResult(
            success=True,
            output=f"{header}\n\n{output}",
            metadata={
                "path": str(file_path),
                "kind": kind,
                "chars": len(text),
                "truncated": truncated,
                "omitted_chars": omitted,
            },
        )
