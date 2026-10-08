"""``xlsx_read`` — read Excel ``.xlsx`` / ``.xlsm`` sheets, standard library only.

An ``.xlsx`` file is a ZIP of XML parts. Reading it needs no third-party
package: :mod:`zipfile` opens the container and :mod:`xml.etree` parses three
small parts — the shared-string table, the workbook's sheet index and the sheet
itself. This module implements exactly that, so Excel support needs **no new
dependency** and survives the frozen build (``openpyxl`` is neither declared in
``pyproject.toml`` nor guaranteed to be present).

It is read-only and confined to the tool's root: it declares only
:data:`Capability.READ_FILE`. A corrupt or non-Excel file is reported as one
readable line, never a traceback.
"""
from __future__ import annotations

import zipfile
from typing import Any, Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

from kairos.capabilities import Capability
from kairos.tools.base import BaseTool, ToolResult
from kairos.tools.zipguard import ZipBombError, guard_zip

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

#: Character budget for the rendered preview.
MAX_OUTPUT = 8000
DEFAULT_MAX_ROWS = 200
DEFAULT_MAX_COLS = 50
MAX_ROWS_LIMIT = 20_000
MAX_COLS_LIMIT = 1_000

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class XlsxError(Exception):
    """A problem that should be shown to the user, not a traceback."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _col_index(ref: str) -> int:
    """``"B7"`` -> ``1`` (0-based column index)."""
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return max(n - 1, 0)


def _truncate(text: str, limit: int = MAX_OUTPUT) -> Tuple[str, bool, int]:
    if len(text) <= limit:
        return text, False, 0
    cut = text.rfind("\n", 0, limit)
    if cut <= 0:
        cut = limit
    omitted = len(text) - cut
    shown = text[:cut] + (
        f"\n\n[truncated: preview capped at {limit} characters; "
        f"{omitted} characters omitted. Lower max_rows / max_cols to see the "
        f"rest.]"
    )
    return shown, True, omitted


def _shared_strings(zf: zipfile.ZipFile, names: Sequence[str]) -> List[str]:
    if "xl/sharedStrings.xml" not in names:
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out: List[str] = []
    for si in root:
        if _local(si.tag) != "si":
            continue
        out.append("".join(node.text or "" for node in si.iter()
                           if _local(node.tag) == "t"))
    return out


def _sheet_map(zf: zipfile.ZipFile,
               names: Sequence[str]) -> List[Tuple[str, str]]:
    """``[(sheet_name, zip_part), ...]`` in workbook order."""
    rels: Dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in names:
        rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        for rel in rel_root:
            rid = rel.get("Id")
            target = rel.get("Target") or ""
            if not rid or not target:
                continue
            if target.startswith("/"):
                rels[rid] = target.lstrip("/")
            else:
                rels[rid] = "xl/" + target
    sheets: List[Tuple[str, str]] = []
    if "xl/workbook.xml" in names:
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        for node in wb.iter():
            if _local(node.tag) != "sheet":
                continue
            name = node.get("name") or f"Sheet{len(sheets) + 1}"
            rid = node.get(f"{{{REL_NS}}}id") or node.get("id")
            part = rels.get(rid or "")
            if not part or part not in names:
                continue
            sheets.append((name, part))
    if not sheets:
        # No workbook index: fall back to whatever worksheets exist.
        alnum = sorted(n for n in names
                       if n.startswith("xl/worksheets/") and n.endswith(".xml"))
        sheets = [(f"Sheet{i + 1}", n) for i, n in enumerate(alnum)]
    if not sheets:
        raise XlsxError("no worksheets found inside the workbook")
    return sheets


def _cell_value(cell: ET.Element, shared: List[str]) -> str:
    ctype = cell.get("t") or "n"
    raw: Optional[str] = None
    inline: Optional[str] = None
    for child in cell:
        tag = _local(child.tag)
        if tag == "v":
            raw = child.text
        elif tag == "is":
            inline = "".join(node.text or "" for node in child.iter()
                             if _local(node.tag) == "t")
    if inline is not None:
        return inline
    if raw is None:
        return ""
    if ctype == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return raw
    if ctype == "b":
        return "TRUE" if raw == "1" else "FALSE"
    return raw


def read_sheet(zf: zipfile.ZipFile, names: Sequence[str], part: str,
               shared: List[str], max_rows: int,
               max_cols: int) -> Tuple[List[List[str]], int, int]:
    """Return ``(rows, total_rows_seen, max_col_seen)`` for one worksheet."""
    root = ET.fromstring(zf.read(part))
    rows: List[List[str]] = []
    total = 0
    widest = 0
    for row in root.iter():
        if _local(row.tag) != "row":
            continue
        total += 1
        if len(rows) >= max_rows:
            continue
        cells: Dict[int, str] = {}
        auto = 0
        for cell in row:
            if _local(cell.tag) != "c":
                continue
            ref = cell.get("r") or ""
            idx = _col_index(ref) if ref else auto
            auto = idx + 1
            if idx >= max_cols:
                continue
            cells[idx] = _cell_value(cell, shared)
        width = (max(cells) + 1) if cells else 0
        widest = max(widest, width)
        rows.append([cells.get(i, "") for i in range(width)])
    return rows, total, max(widest, max((len(r) for r in rows), default=0))


class XlsxReadTool(BaseTool):
    """Read a sheet from an Excel ``.xlsx`` / ``.xlsm`` workbook.

    Returns the sheet as rows of text. A workbook usually has several sheets;
    pick one by name or by 1-based index. Large sheets are previewed with an
    explicit row/column cap rather than dumped whole.

    Example usage:
        - First sheet: {"path": "report.xlsx"}
        - By name: {"path": "report.xlsx", "sheet": "Q3"}
        - By index: {"path": "report.xlsx", "sheet": 2, "max_rows": 50}
    """

    name = "xlsx_read"
    description = (
        "Read a sheet from an Excel .xlsx / .xlsm workbook (read-only, no "
        "Excel needed). Lists the sheets and returns one as rows of text, "
        "capped by max_rows/max_cols. Use it to inspect a spreadsheet's "
        "contents; for delimited text files use data_analyze instead."
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
                        "description": ("Path to the .xlsx/.xlsm file (relative "
                                        "to the project directory)."),
                    },
                    "sheet": {
                        "type": ["string", "integer"],
                        "description": ("Sheet to read: a name or a 1-based "
                                        "index. Omit for the first sheet."),
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": f"Rows to return (default {DEFAULT_MAX_ROWS}).",
                    },
                    "max_cols": {
                        "type": "integer",
                        "description": f"Columns to return (default {DEFAULT_MAX_COLS}).",
                    },
                },
                "required": ["path"],
            },
        }

    @staticmethod
    def _coerce_int(value: Any, default: int, low: int, high: int) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return max(low, min(high, n))

    def _pick(self, sheets: List[Tuple[str, str]],
              requested: Any) -> Tuple[str, str]:
        if requested in (None, ""):
            return sheets[0]
        want = str(requested).strip()
        for name, part in sheets:
            if name.lower() == want.lower():
                return name, part
        if want.isdigit():
            idx = int(want) - 1
            if 0 <= idx < len(sheets):
                return sheets[idx]
        available = ", ".join(n for n, _p in sheets)
        raise XlsxError(f"no sheet named {requested!r}; available: {available}")

    async def execute(self, path: str = "", sheet: Any = None,
                      max_rows: Any = DEFAULT_MAX_ROWS,
                      max_cols: Any = DEFAULT_MAX_COLS, **kwargs) -> ToolResult:
        if not path:
            return ToolResult(success=False, output="",
                              error="xlsx_read needs a path")
        try:
            file_path = self._resolve_safe(path)
        except PermissionError as exc:
            return ToolResult(success=False, output="", error=str(exc))
        if not file_path.exists():
            return ToolResult(success=False, output="",
                              error=f"File not found: {path}")
        if file_path.is_dir():
            return ToolResult(success=False, output="",
                              error=f"{path} is a directory, not a workbook")

        rows_cap = self._coerce_int(max_rows, DEFAULT_MAX_ROWS, 1, MAX_ROWS_LIMIT)
        cols_cap = self._coerce_int(max_cols, DEFAULT_MAX_COLS, 1, MAX_COLS_LIMIT)

        try:
            # Sniff only the first 8 bytes -- read exactly that many, never the
            # whole file (``read_bytes()[:8]`` pulled the entire workbook into
            # memory just to look at its magic number).
            with open(file_path, "rb") as _fh:
                head = _fh.read(8)
        except OSError as exc:
            return ToolResult(success=False, output="",
                              error=f"Could not read {path}: {exc}")
        if head.startswith(_OLE_MAGIC):
            return ToolResult(
                success=False, output="",
                error=(f"{path} is a legacy binary Excel file (.xls). "
                       f"xlsx_read only understands the .xlsx/.xlsm format; "
                       f"re-save it as .xlsx first."))

        try:
            with zipfile.ZipFile(file_path) as zf:
                # Refuse a ZIP bomb *before* decompressing anything: the guard
                # reads the central directory only (kairos.tools.zipguard).
                guard_zip(zf, path)
                names = zf.namelist()
                shared = _shared_strings(zf, names)
                sheets = _sheet_map(zf, names)
                name, part = self._pick(sheets, sheet)
                rows, total_rows, widest = read_sheet(
                    zf, names, part, shared, rows_cap, cols_cap)
        except zipfile.BadZipFile:
            return ToolResult(
                success=False, output="",
                error=(f"{path} is not a readable .xlsx workbook "
                       f"(the ZIP container is corrupt or the file is not "
                       f"actually an Office file)."))
        except ET.ParseError as exc:
            return ToolResult(
                success=False, output="",
                error=(f"{path} contains malformed XML inside the workbook "
                       f"({exc}); the file is corrupt."))
        except ZipBombError as exc:
            # A declared-size guard hit: the file looks like (or is) a bomb.
            # Reported as-is -- the message names the limit and the value.
            return ToolResult(success=False, output="", error=str(exc))
        except XlsxError as exc:
            return ToolResult(success=False, output="", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - one readable line, no stack
            return ToolResult(
                success=False, output="",
                error=(f"Could not read {path}: {type(exc).__name__}: {exc}"))

        lines = [f"# xlsx_read: {path}",
                 f"sheets: {', '.join(n for n, _p in sheets)}",
                 f"sheet: {name!r}" ]
        shown_rows = len(rows)
        row_capped = total_rows > shown_rows
        shape = f"rows: {shown_rows}"
        if row_capped:
            shape += f" (capped from {total_rows})"
        shape += f" | columns: {widest}"
        if widest > cols_cap:
            shape += f" (capped from {widest} to {cols_cap})"
        lines.append(shape)
        lines.append("")
        for row in rows:
            cells = [str(c)[:60] for c in row[:cols_cap]]
            lines.append(" | ".join(cells))
        if not rows:
            lines.append("(sheet is empty)")

        output, truncated, omitted = _truncate("\n".join(lines))
        return ToolResult(
            success=True,
            output=output,
            metadata={
                "path": str(file_path),
                "sheet": name,
                "sheets": [n for n, _p in sheets],
                "rows": shown_rows,
                "total_rows": total_rows,
                "columns": min(widest, cols_cap),
                "row_capped": row_capped,
                "truncated": truncated,
                "omitted_chars": omitted,
            },
        )
