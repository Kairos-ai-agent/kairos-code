"""``data_analyze`` — CSV / TSV analysis with the standard library only (P0-5).

A model reading a CSV with ``file_read`` sees raw rows and has to count them
itself; a 200k-row file is windowed and it never sees the tail at all. This
tool reads a delimited text file *inside the tool's root* and reports what a
reader actually wants: the shape (rows x columns), each column's inferred type
and how many values are missing, per-column summary statistics for the numeric
columns, the leading rows, and an optional group-by count.

Deliberately **stdlib-only** (``csv`` + ``statistics``): it adds no dependency,
so it works in a clean install and in the frozen build. It writes nothing and
reaches no network — it declares only :data:`Capability.READ_FILE`.
"""
from __future__ import annotations

import csv
import io
import re
import statistics
from typing import Any, Dict, List, Optional, Tuple

from kairos.capabilities import Capability
from kairos.tools.base import BaseTool, ToolResult

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?$")
_BOOLS = {"true", "false", "yes", "no"}

#: Character budget for the rendered report. Cut at a line boundary with an
#: explicit marker, never silently.
MAX_OUTPUT = 8000
#: Hard cap on how many data rows are read into memory for analysis. The file
#: may be far larger; the report always says how many rows were analysed.
MAX_ROWS_LIMIT = 200_000
DEFAULT_MAX_ROWS = 20_000
MAX_GROUPS = 30


def _truncate(text: str, limit: int = MAX_OUTPUT) -> Tuple[str, bool, int]:
    """Cut ``text`` to ``limit`` chars at a line boundary.

    Returns ``(shown, truncated, omitted_chars)``. The marker names the cap
    and the number of omitted characters so the loss is never silent.
    """
    if len(text) <= limit:
        return text, False, 0
    cut = text.rfind("\n", 0, limit)
    if cut <= 0:
        cut = limit
    omitted = len(text) - cut
    shown = text[:cut] + (
        f"\n\n[truncated: report capped at {limit} characters; "
        f"{omitted} characters omitted. Narrow it with max_rows / "
        f"preview_rows, or pick a single column.]"
    )
    return shown, True, omitted


def _infer_type(values: List[str]) -> str:
    """Classify a column from its non-empty string values."""
    if not values:
        return "empty"
    if all(_INT_RE.match(v) for v in values):
        return "integer"
    if all(_FLOAT_RE.match(v) for v in values):
        return "number"
    if all(_DATE_RE.match(v) for v in values):
        return "date"
    if all(v.strip().lower() in _BOOLS for v in values):
        return "boolean"
    return "text"


def _to_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.4g}"


class DataAnalyzeTool(BaseTool):
    """Analyse a CSV / TSV / delimited text file inside the project directory.

    Reports row and column counts, each column's inferred type, per-column
    missing-value counts, summary statistics for numeric columns, the leading
    rows, and an optional group-by count. Use it before answering questions
    about a data file's contents, shape or quality.

    Example usage:
        - Profile it: {"path": "sales.csv"}
        - Group counts: {"path": "sales.csv", "group_by": "region"}
        - Semicolon-separated: {"path": "data.txt", "delimiter": ";"}
    """

    name = "data_analyze"
    description = (
        "Profile a CSV / TSV / delimited text file: rows, columns, inferred "
        "column types, missing-value counts, numeric summary statistics "
        "(min/max/mean/median), the leading rows, and a group-by count. Use "
        "this instead of reading a whole data file to answer questions about "
        "its shape, quality or aggregate values."
    )

    #: Reading inside the root is the only side effect.
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
                        "description": ("Path to the data file (relative to the "
                                        "project directory)."),
                    },
                    "delimiter": {
                        "type": "string",
                        "description": ("Field delimiter, e.g. ',' or ';' or a "
                                        "tab. Omit to auto-detect."),
                    },
                    "has_header": {
                        "type": "boolean",
                        "description": ("First row holds column names. Default "
                                        "true."),
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": ("Maximum data rows to read into the "
                                        f"analysis (default {DEFAULT_MAX_ROWS})."),
                    },
                    "preview_rows": {
                        "type": "integer",
                        "description": "Leading rows to show (default 5).",
                    },
                    "group_by": {
                        "type": "string",
                        "description": ("A column name or 1-based index to count "
                                        "occurrences per distinct value."),
                    },
                },
                "required": ["path"],
            },
        }

    # -- internals --------------------------------------------------------

    @staticmethod
    def _coerce_int(value: Any, default: int, *, low: int = 0,
                    high: Optional[int] = None) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        if n < low:
            return low
        if high is not None and n > high:
            return high
        return n

    def _resolve_group_index(self, group_by: str, header: List[str],
                             ncols: int) -> Optional[int]:
        if group_by in header:
            return header.index(group_by)
        if _INT_RE.match(group_by):
            idx = int(group_by) - 1
            if 0 <= idx < ncols:
                return idx
        return None

    def _read_rows(self, file_path) -> Tuple[List[List[str]], str, List[str]]:
        """Return ``(rows, encoding_notice, warnings)`` for the data file."""
        warnings: List[str] = []
        raw = file_path.read_bytes()
        notice = ""
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            replaced = text.count("\ufffd")
            notice = (f"file is not valid UTF-8; {replaced} byte(s) shown as the "
                      f"replacement character")
        reader = csv.reader(io.StringIO(text))
        rows: List[List[str]] = []
        try:
            for row in reader:
                rows.append(row)
        except csv.Error as exc:
            warnings.append(f"stopped at a malformed CSV row: {exc}")
        return rows, notice, warnings

    # -- the tool ---------------------------------------------------------

    async def execute(self, path: str = "", delimiter: Optional[str] = None,
                      has_header: bool = True, max_rows: Any = DEFAULT_MAX_ROWS,
                      preview_rows: Any = 5, group_by: Optional[str] = None,
                      **kwargs) -> ToolResult:
        if not path:
            return ToolResult(success=False, output="",
                              error="data_analyze needs a path")
        try:
            file_path = self._resolve_safe(path)
        except PermissionError as exc:
            return ToolResult(success=False, output="", error=str(exc))
        if not file_path.exists():
            return ToolResult(success=False, output="",
                              error=f"File not found: {path}")
        if file_path.is_dir():
            return ToolResult(success=False, output="",
                              error=f"{path} is a directory, not a data file")

        limit = self._coerce_int(max_rows, DEFAULT_MAX_ROWS, low=1,
                                 high=MAX_ROWS_LIMIT)
        try:
            rows, notice, warnings = self._read_rows(file_path)
        except Exception as exc:  # noqa: BLE001 - one readable error, no stack
            return ToolResult(success=False, output="",
                              error=f"Could not read {path}: {exc}")

        if not rows:
            return ToolResult(success=True,
                              output=f"# data_analyze: {path}\n(empty file)",
                              metadata={"path": str(file_path), "rows": 0})

        # Auto-detect the delimiter from the head of the file.
        if not delimiter:
            sample = "\n".join(",".join(r) for r in rows[:20])
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|"
                                                ).delimiter
            except csv.Error:
                delimiter = ","
        if delimiter == "\\t":
            delimiter = "\t"

        header: List[str]
        body: List[List[str]]
        if has_header:
            header = [str(c) for c in rows[0]]
            body = rows[1:]
        else:
            header = [f"col{i + 1}" for i in range(len(rows[0]))]
            body = rows

        total_data_rows = len(body)
        analysed = body[:limit]
        row_capped = total_data_rows > len(analysed)

        ncols = max([len(header)] + [len(r) for r in analysed]) if analysed else len(header)
        while len(header) < ncols:
            header.append(f"col{len(header) + 1}")

        ragged = sum(1 for r in analysed if len(r) != len(header))
        columns: List[List[str]] = [[] for _ in range(ncols)]
        empty_counts = [0] * ncols
        for r in analysed:
            for i in range(ncols):
                v = r[i] if i < len(r) else ""
                if v == "":
                    empty_counts[i] += 1
                else:
                    columns[i].append(v)

        types = [_infer_type(col) for col in columns]
        preview_n = self._coerce_int(preview_rows, 5, low=0, high=100)

        lines: List[str] = [f"# data_analyze: {path}"]
        shape = f"rows: {total_data_rows}"
        if row_capped:
            shape += f" (analysed the first {len(analysed)})"
        shape += (f" | columns: {ncols} "
                  f"({'header row present' if has_header else 'no header'})")
        lines += [shape, f"delimiter: {delimiter!r}"]
        if notice:
            lines.append(f"encoding notice: {notice}")
        warnings += [f"ragged rows (column count differs): {ragged}"] if ragged else []
        for w in warnings:
            lines.append(f"warning: {w}")

        lines.append("")
        lines.append("## Columns")
        lines.append("  #  name                 type     non-empty  empty")
        for i in range(ncols):
            lines.append(f"  {i + 1:<2} {header[i][:20]:<20} "
                         f"{types[i]:<8} {len(columns[i]):>9}  {empty_counts[i]:>5}")

        numeric_cols = [(i, columns[i]) for i in range(ncols)
                        if types[i] in ("integer", "number") and columns[i]]
        if numeric_cols:
            lines.append("")
            lines.append("## Numeric summary")
            for i, values in numeric_cols:
                nums = [n for n in (_to_float(v) for v in values) if n is not None]
                if not nums:
                    continue
                stats = (f"n={len(nums)} min={_fmt(min(nums))} "
                         f"max={_fmt(max(nums))} mean={_fmt(statistics.fmean(nums))} "
                         f"median={_fmt(statistics.median(nums))} sum={_fmt(sum(nums))}")
                if len(nums) > 1:
                    stats += f" stdev={_fmt(statistics.pstdev(nums))}"
                lines.append(f"  {header[i]}: {stats}")

        missing = [i for i in range(ncols) if empty_counts[i]]
        if missing:
            lines.append("")
            lines.append("## Missing values")
            denom = len(analysed) or 1
            for i in missing:
                pct = 100.0 * empty_counts[i] / denom
                lines.append(f"  {header[i]}: {empty_counts[i]} missing "
                             f"({pct:.1f}% of analysed rows)")

        if preview_n and analysed:
            lines.append("")
            lines.append(f"## Preview (first {min(preview_n, len(analysed))} rows)")
            lines.append("  " + " | ".join(header))
            for r in analysed[:preview_n]:
                cells = [str(c)[:40] for c in r[:ncols]]
                lines.append("  " + " | ".join(cells))

        if group_by:
            idx = self._resolve_group_index(str(group_by), header, ncols)
            if idx is None:
                lines.append("")
                lines.append(f"## Group by {group_by!r}: no such column "
                             f"(columns: {', '.join(header)})")
            else:
                counts: Dict[str, int] = {}
                for r in analysed:
                    key = r[idx] if idx < len(r) and r[idx] != "" else "(empty)"
                    counts[key] = counts.get(key, 0) + 1
                ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
                lines.append("")
                lines.append(f"## Group by {header[idx]!r} "
                             f"({len(ordered)} distinct values)")
                for key, count in ordered[:MAX_GROUPS]:
                    lines.append(f"  {key}: {count}")
                if len(ordered) > MAX_GROUPS:
                    rest = sum(c for _k, c in ordered[MAX_GROUPS:])
                    lines.append(f"  (+{len(ordered) - MAX_GROUPS} more groups, "
                                 f"{rest} rows)")
                if row_capped:
                    lines.append("  [counts cover the analysed rows only]")

        output, truncated, omitted = _truncate("\n".join(lines))
        return ToolResult(
            success=True,
            output=output,
            metadata={
                "path": str(file_path),
                "delimiter": delimiter,
                "data_rows": total_data_rows,
                "analysed_rows": len(analysed),
                "columns": ncols,
                "row_capped": row_capped,
                "types": {header[i]: types[i] for i in range(ncols)},
                "truncated": truncated,
                "omitted_chars": omitted,
            },
        )
