"""P0-5 — the first batch of general, credential-free tools.

Four tools, each wired through the P0-6 capability gate:

* ``data_analyze`` — CSV / delimited-file profiling (stdlib ``csv``).
* ``xlsx_read``    — Excel ``.xlsx`` sheets (stdlib ``zipfile`` + ``xml``).
* ``doc_read``     — Word ``.docx`` / PowerPoint ``.pptx`` text (stdlib).
* ``python_run``   — a bounded Python snippet in a child interpreter.

The tests follow ``tests/test_capability_gate.py``: one positive path per tool,
then the ways out — a path that leaves the root, a variant that declares
nothing, a timeout, an over-long output, a corrupt file. Every test writes only
under ``tmp_path``; the capability audit is redirected there too.
"""
from __future__ import annotations

import asyncio
import os
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import pytest

from kairos.approval import ApprovalMode
from kairos.capabilities import (
    Capability,
    assess,
    requires_approval,
    resolve_capabilities,
    runtime_capabilities,
    unregister_tool_capabilities,
)
from kairos.platform_flags import CREATE_NO_WINDOW
from kairos.sentinel import Sentinel, SentinelAudit
from kairos.tools.base import BaseTool, ToolResult
from kairos.tools.data_analyze import DataAnalyzeTool
from kairos.tools.doc_read import DocReadTool
from kairos.tools.python_run import PythonRunTool
from kairos.tools.xlsx_read import XlsxReadTool

WINDOWS = sys.platform.startswith("win")
NEW_TOOLS = ("data_analyze", "xlsx_read", "doc_read", "python_run")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Keep the mode, the audit trail and the runtime registry local."""
    monkeypatch.delenv("KAIROS_APPROVAL_MODE", raising=False)
    monkeypatch.setenv("KAIROS_CAPABILITY_AUDIT_DIR", str(tmp_path / "cap-audit"))
    yield
    # Drop only the throwaway probe names; the real tools' class-level
    # declarations must survive the test that read them.
    for name in ("probe_p05_undeclared", "probe_p05_variant"):
        unregister_tool_capabilities(name)


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "sub").mkdir()
    return root


# ---------------------------------------------------------------------------
# stdlib fixture builders (no third-party writer needed)
# ---------------------------------------------------------------------------


def write_csv(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def write_xlsx(path: Path, rows, *, sheet_name: str = "Sheet1") -> Path:
    """A minimal but valid single-sheet .xlsx, built with zipfile + XML."""
    cells = []
    for r, row in enumerate(rows, start=1):
        parts = []
        for c, value in enumerate(row):
            col = chr(ord("A") + c)
            if isinstance(value, bool):
                parts.append(f'<c r="{col}{r}" t="b"><v>{1 if value else 0}</v></c>')
            elif isinstance(value, (int, float)):
                parts.append(f'<c r="{col}{r}"><v>{value}</v></c>')
            else:
                parts.append(f'<c r="{col}{r}" t="inlineStr"><is><t>'
                             f'{xml_escape(str(value))}</t></is></c>')
        cells.append(f'<row r="{r}">{"".join(parts)}</row>')
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    parts = {
        "[Content_Types].xml":
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"/>',
        "_rels/.rels":
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxml'
            'formats.org/package/2006/relationships"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":
            f'<?xml version="1.0"?><workbook xmlns="{ns}" xmlns:r="{rns}"><sheets>'
            f'<sheet name="{xml_escape(sheet_name)}" sheetId="1" r:id="rId1"/>'
            f'</sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxml'
            'formats.org/package/2006/relationships"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml":
            f'<?xml version="1.0"?><worksheet xmlns="{ns}"><sheetData>'
            f'{"".join(cells)}</sheetData></worksheet>',
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in parts.items():
            z.writestr(name, content)
    return path


def write_docx(path: Path, paragraphs) -> Path:
    body = "".join(
        "<w:p>" + "".join(f"<w:r><w:t>{xml_escape(p)}</w:t></w:r>" for p in [para])
        + "</w:p>"
        for para in paragraphs
    )
    parts = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types/>',
        "word/document.xml":
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxml'
            'formats.org/wordprocessingml/2006/main"><w:body>'
            f'{body}</w:body></w:document>',
    }
    with zipfile.ZipFile(path, "w") as z:
        for name, content in parts.items():
            z.writestr(name, content)
    return path


def write_pptx(path: Path, slides) -> Path:
    parts = {"[Content_Types].xml": '<?xml version="1.0"?><Types/>'}
    for i, texts in enumerate(slides, start=1):
        runs = "".join(f"<a:p><a:r><a:t>{xml_escape(t)}</a:t></a:r></a:p>"
                       for t in texts)
        parts[f"ppt/slides/slide{i}.xml"] = (
            '<?xml version="1.0"?><p:sld xmlns:p="http://schemas.openxmlformats'
            '.org/presentationml/2006/main" xmlns:a="http://schemas.openxml'
            'formats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp>'
            f'<p:txBody>{runs}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
    with zipfile.ZipFile(path, "w") as z:
        for name, content in parts.items():
            z.writestr(name, content)
    return path


# ===========================================================================
# (1) Registration + the gate — declared, and actually gated
# ===========================================================================


def test_every_new_tool_declares_its_capabilities():
    assert resolve_capabilities("data_analyze") == frozenset({Capability.READ_FILE})
    assert resolve_capabilities("xlsx_read") == frozenset({Capability.READ_FILE})
    assert resolve_capabilities("doc_read") == frozenset({Capability.READ_FILE})
    assert resolve_capabilities("python_run") == frozenset({Capability.EXEC_PROCESS})
    for name in NEW_TOOLS:
        assert runtime_capabilities(name) is not None, (
            f"{name} did not register declare-by-class capabilities")


def test_a_declared_tool_is_allowed_but_its_target_is_fenced(ws):
    ok = assess("data_analyze", {"path": "data.csv"}, root=ws)
    assert ok.allowed is True and ok.rule == "capability-allow"

    bad = assess("data_analyze", {"path": "../secret.csv"}, root=ws)
    assert bad.allowed is False
    assert bad.rule == "path-fence"


def test_an_undeclared_variant_is_refused():
    class _Undeclared(BaseTool):
        name = "probe_p05_undeclared"

        async def execute(self, **kwargs) -> ToolResult:  # pragma: no cover
            return ToolResult(success=True, output="ran")

    verdict = assess("probe_p05_undeclared", {"path": "x.csv"}, root=Path("."))
    assert verdict.allowed is False
    assert verdict.rule == "undeclared-capability"

    async def go():
        return await _Undeclared().execute(path="x.csv")

    res = asyncio.run(go())
    assert res.success is False
    assert "declares no capability set" in (res.error or "")


def test_every_new_tool_execute_is_routed_through_the_gate():
    for cls in (DataAnalyzeTool, XlsxReadTool, DocReadTool, PythonRunTool):
        execute = cls.__dict__.get("execute")
        assert getattr(execute, "__capability_wrapped__", False), (
            f"{cls.__name__}.execute is not routed through the capability gate")


def test_legacy_tools_are_untouched_by_the_new_declarations():
    # Adding declared tools must not leak into the legacy ladder.
    for name in ("file_read", "terminal", "grep", "webfetch"):
        assert runtime_capabilities(name) is None


def test_python_run_execution_needs_approval(tmp_path):
    assert requires_approval({Capability.EXEC_PROCESS}, ApprovalMode.SUGGEST)[0] is True
    assert requires_approval({Capability.EXEC_PROCESS}, ApprovalMode.EDIT)[0] is True
    # A strict gate must NOT wave a declared exec capability through in EDIT.
    gate = Sentinel(mode=ApprovalMode.EDIT, strict=True,
                    audit=SentinelAudit(directory=tmp_path / "audit"))
    ruling = gate.authorize("python_run", {"code": "print(1)"})
    assert ruling.denied, "an unapproved EXEC capability was allowed"


def test_read_only_tools_stay_silent_for_approval():
    assert requires_approval({Capability.READ_FILE}, ApprovalMode.EDIT)[0] is False
    assert requires_approval({Capability.READ_FILE}, ApprovalMode.SUGGEST)[0] is False


# ===========================================================================
# (2) data_analyze
# ===========================================================================


CSV = (
    "date,region,units,price,note\n"
    "2024-01-01,North,10,1.5,ok\n"
    "2024-01-02,South,3,2.0,\n"
    "2024-01-03,North,,2.5,late\n"
    "2024-01-04,East,7,3.25,\n"
)


async def test_data_analyze_profiles_a_csv(ws):
    write_csv(ws / "sales.csv", CSV)
    res = await DataAnalyzeTool(allowed_root=ws).execute(path="sales.csv")

    assert res.success is True, res.error
    out = res.output
    assert "rows: 4" in out and "columns: 5" in out
    assert "integer" in out and "date" in out and "number" in out
    assert "missing" in out.lower()
    assert res.metadata["data_rows"] == 4
    assert res.metadata["types"]["units"] == "integer"


async def test_data_analyze_group_by_counts(ws):
    write_csv(ws / "sales.csv", CSV)
    res = await DataAnalyzeTool(allowed_root=ws).execute(
        path="sales.csv", group_by="region")

    assert res.success is True, res.error
    assert "North: 2" in res.output
    assert "East: 1" in res.output


async def test_data_analyze_refuses_a_path_out_of_root(ws):
    (ws.parent / "outside.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    res = await DataAnalyzeTool(allowed_root=ws).execute(path="../outside.csv")

    assert res.success is False
    assert "outside" in (res.error or "").lower()
    assert "\n" not in (res.error or ""), "an error leaked a traceback"


async def test_data_analyze_output_is_capped(ws):
    header = ",".join(f"col{i}" for i in range(6))
    rows = [header] + [",".join("x" * 60 for _ in range(6)) for _ in range(120)]
    write_csv(ws / "wide.csv", "\n".join(rows) + "\n")

    res = await DataAnalyzeTool(allowed_root=ws).execute(
        path="wide.csv", preview_rows=100)

    assert res.success is True, res.error
    assert res.metadata["truncated"] is True
    assert "[truncated: report capped at" in res.output
    assert len(res.output) < 9_000


async def test_data_analyze_never_raises_on_garbage(ws):
    (ws / "blob.csv").write_bytes(b"\x00\x01\x02binary\x00junk\n\x03\x04")

    res = await DataAnalyzeTool(allowed_root=ws).execute(path="blob.csv")

    # Whatever it decides, it must be a ToolResult with readable text.
    assert isinstance(res, ToolResult)
    if not res.success:
        assert "\n" not in (res.error or "")


# ===========================================================================
# (3) xlsx_read (stdlib reader)
# ===========================================================================


async def test_xlsx_read_reads_values_by_sheet(ws):
    write_xlsx(ws / "book.xlsx",
               [["region", "units", "active"],
                ["North", 10, True],
                ["South", 3, False]],
               sheet_name="Q3")
    tool = XlsxReadTool(allowed_root=ws)

    res = await tool.execute(path="book.xlsx")
    assert res.success is True, res.error
    assert "Q3" in res.output
    assert "North | 10 | TRUE" in res.output
    assert "South | 3 | FALSE" in res.output
    assert res.metadata["rows"] == 3

    by_index = await tool.execute(path="book.xlsx", sheet=1)
    assert by_index.success is True, by_index.error
    assert "North" in by_index.output


async def test_xlsx_read_rejects_an_unknown_sheet(ws):
    write_xlsx(ws / "book.xlsx", [["a"]], sheet_name="Only")
    res = await XlsxReadTool(allowed_root=ws).execute(path="book.xlsx",
                                                      sheet="Nope")

    assert res.success is False
    assert "Only" in (res.error or "")  # names what is available
    assert "\n" not in (res.error or "")


async def test_xlsx_read_refuses_a_path_out_of_root(ws):
    write_xlsx(ws.parent / "outside.xlsx", [["x"]])
    res = await XlsxReadTool(allowed_root=ws).execute(path="../outside.xlsx")

    assert res.success is False
    assert "outside" in (res.error or "").lower()


async def test_xlsx_read_reports_a_corrupt_zip_cleanly(ws):
    (ws / "broken.xlsx").write_bytes(b"this is definitely not a zip archive")

    res = await XlsxReadTool(allowed_root=ws).execute(path="broken.xlsx")

    assert res.success is False
    assert "corrupt" in (res.error or "").lower() or "not a readable" in (res.error or "")
    assert "\n" not in (res.error or "")
    assert "Traceback" not in (res.error or "")


async def test_xlsx_read_reports_malformed_xml_cleanly(ws):
    with zipfile.ZipFile(ws / "badxml.xlsx", "w") as z:
        z.writestr("xl/workbook.xml", "<workbook><unclosed>")

    res = await XlsxReadTool(allowed_root=ws).execute(path="badxml.xlsx")

    assert res.success is False
    assert "malformed" in (res.error or "").lower() or "corrupt" in (res.error or "").lower()
    assert "\n" not in (res.error or "")


async def test_xlsx_read_explains_a_legacy_xls(ws):
    (ws / "old.xls").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32)

    res = await XlsxReadTool(allowed_root=ws).execute(path="old.xls")

    assert res.success is False
    assert ".xlsx" in (res.error or "")
    assert "\n" not in (res.error or "")


async def test_xlsx_read_caps_a_large_sheet(ws):
    rows = [["h1", "h2", "h3", "h4", "h5"]]
    rows += [["y" * 60] * 5 for _ in range(300)]
    write_xlsx(ws / "big.xlsx", rows)

    res = await XlsxReadTool(allowed_root=ws).execute(path="big.xlsx")

    assert res.success is True, res.error
    assert res.metadata["row_capped"] is True
    assert res.metadata["truncated"] is True
    assert "[truncated: preview capped at" in res.output


# ===========================================================================
# (4) doc_read (stdlib reader)
# ===========================================================================


async def test_doc_read_extracts_docx_paragraphs(ws):
    write_docx(ws / "spec.docx", ["Hello world", "Second paragraph"])

    res = await DocReadTool(allowed_root=ws).execute(path="spec.docx")

    assert res.success is True, res.error
    assert "Hello world" in res.output
    assert "Second paragraph" in res.output
    assert res.metadata["kind"] == "docx"


async def test_doc_read_extracts_pptx_slides(ws):
    write_pptx(ws / "deck.pptx", [["Title slide", "subtitle"], ["Slide two body"]])

    res = await DocReadTool(allowed_root=ws).execute(path="deck.pptx")

    assert res.success is True, res.error
    assert "Title slide" in res.output
    assert "--- slide 2 ---" in res.output
    assert "Slide two body" in res.output
    assert res.metadata["kind"] == "pptx"


async def test_doc_read_refuses_a_path_out_of_root(ws):
    write_docx(ws.parent / "outside.docx", ["secret"])
    res = await DocReadTool(allowed_root=ws).execute(path="../outside.docx")

    assert res.success is False
    assert "outside" in (res.error or "").lower()


async def test_doc_read_reports_a_corrupt_document_cleanly(ws):
    (ws / "broken.docx").write_bytes(b"not a zip at all")

    res = await DocReadTool(allowed_root=ws).execute(path="broken.docx")

    assert res.success is False
    assert "\n" not in (res.error or "")
    assert "Traceback" not in (res.error or "")


async def test_doc_read_explains_a_legacy_doc(ws):
    (ws / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32)

    res = await DocReadTool(allowed_root=ws).execute(path="old.doc")

    assert res.success is False
    assert ".docx" in (res.error or "")
    assert "\n" not in (res.error or "")


async def test_doc_read_caps_the_text(ws):
    write_docx(ws / "long.docx", ["z" * 500 for _ in range(20)])

    res = await DocReadTool(allowed_root=ws).execute(path="long.docx",
                                                     max_chars=300)

    assert res.success is True, res.error
    assert res.metadata["truncated"] is True
    assert "[truncated: text capped at 300 characters" in res.output


# ===========================================================================
# (5) python_run — the interpreter
# ===========================================================================


class _SpawnRecorder:
    """Replaces asyncio.create_subprocess_* and records the kwargs."""

    def __init__(self) -> None:
        self.calls: list = []

    def install(self, monkeypatch) -> None:
        async def fake(*args, **kwargs):
            self.calls.append((args, kwargs))
            raise OSError("inspecting the spawn call is enough")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
        monkeypatch.setattr(asyncio, "create_subprocess_shell", fake)


async def test_python_run_runs_a_snippet(ws):
    res = await PythonRunTool(allowed_root=ws).execute(code="print(1 + 1)")

    assert res.success is True, res.error
    assert "2" in res.output
    assert res.metadata["returncode"] == 0


async def test_python_run_cwd_is_inside_the_root(ws):
    res = await PythonRunTool(allowed_root=ws).execute(
        code="import os; print(os.getcwd())")

    assert res.success is True, res.error
    assert str(ws.resolve()).lower() in res.output.lower()


async def test_python_run_uses_the_working_directory_and_stays_in_it(ws):
    res = await PythonRunTool(allowed_root=ws).execute(
        code="import os; print(os.path.basename(os.getcwd()))",
        cwd="sub")

    assert res.success is True, res.error
    assert "sub" in res.output


async def test_python_run_refuses_a_cwd_out_of_root(ws):
    res = await PythonRunTool(allowed_root=ws).execute(code="print(1)", cwd="..")

    assert res.success is False
    assert "outside" in (res.error or "").lower()


async def test_python_run_times_out_and_says_so(ws):
    res = await PythonRunTool(allowed_root=ws).execute(
        code="import time; time.sleep(10)", timeout_s=0.5)

    assert res.success is False
    assert res.metadata["timed_out"] is True
    assert "timed out" in (res.error or "").lower()


async def test_python_run_caps_a_flood_of_output(ws):
    res = await PythonRunTool(allowed_root=ws).execute(code="print('x' * 50000)",
                                                       timeout_s=30)

    assert res.success is True, res.error
    assert res.metadata["stdout_truncated"] is True
    assert "truncated: stream capped at" in res.output
    assert len(res.output) < 21_000


async def test_python_run_reports_a_nonzero_exit(ws):
    res = await PythonRunTool(allowed_root=ws).execute(
        code="print('before'); import sys; sys.exit(3)")

    assert res.success is False
    assert res.metadata["returncode"] == 3
    assert "before" in res.output


async def test_python_run_needs_code(ws):
    res = await PythonRunTool(allowed_root=ws).execute(code="   ")

    assert res.success is False
    assert "code" in (res.error or "")


async def test_python_run_never_leaks_a_traceback_on_a_bad_snippet(ws):
    res = await PythonRunTool(allowed_root=ws).execute(code="raise ValueError('boom')")

    # A snippet that raises exits non-zero: reported, not thrown.
    assert res.success is False
    assert res.metadata["returncode"] != 0
    assert "ValueError" in res.output  # stderr is surfaced, readable


async def test_python_run_spawn_is_hidden_and_does_not_touch_the_pipes(
        ws, monkeypatch):
    rec = _SpawnRecorder()
    rec.install(monkeypatch)

    res = await PythonRunTool(allowed_root=ws).execute(code="print(1)")

    assert res.success is False          # the fake aborts the spawn
    assert rec.calls, "python_run never reached a spawn call"
    args, kwargs = rec.calls[0]
    # The snippet is a file, never argv, so it is not in the process list.
    assert args[0] == sys.executable or args[0].lower().startswith(
        sys.executable.lower())
    assert "-I" in args
    assert any(str(a).endswith(".py") for a in args)
    assert not any("print(1)" in str(a) for a in args)
    # CREATE_NO_WINDOW is OR-ed in on Windows and absent off it.
    if WINDOWS:
        assert int(kwargs.get("creationflags", 0)) & CREATE_NO_WINDOW
    else:
        assert "creationflags" not in kwargs
    # The flag must not have been implemented by touching the child's pipes.
    assert kwargs["stdout"] is asyncio.subprocess.PIPE
    assert kwargs["stderr"] is asyncio.subprocess.PIPE
    # cwd confined to the root.
    assert Path(kwargs["cwd"]).resolve() == ws.resolve()
    # The staged snippet is cleaned up afterwards.
    script = [a for a in args if str(a).endswith(".py")][0]
    assert not os.path.exists(script), "the staged snippet was left behind"


# ===========================================================================
# (6) Discovery — the tools are listed, and wired to the roles
# ===========================================================================


def test_native_tools_listing_includes_the_new_tools():
    from api.routes.extensions_helpers import _native_tools

    listed = _native_tools()
    for name in NEW_TOOLS:
        assert name in listed, f"{name} is not in the built-in tools listing"


def test_tools_package_exports_the_new_classes():
    import kairos.tools as tools_mod

    for cls in ("DataAnalyzeTool", "XlsxReadTool", "DocReadTool", "PythonRunTool"):
        assert cls in tools_mod.__all__
        assert getattr(tools_mod, cls).name in NEW_TOOLS


def test_the_orchestrator_gives_the_new_tools_to_the_roles(tmp_path, monkeypatch):
    """They must reach the agent, or the prompt can never mention them."""
    monkeypatch.setenv("KAIROS_NO_BUNDLED_MCP", "1")
    from kairos.core.orchestrator import Orchestrator, Project
    from kairos.core.persistence import Persistence

    class _Router:
        def get_provider_for_role(self, role):
            return None

    orch = Orchestrator(model_router=_Router(), workspace_base=tmp_path / "ws",
                        db=Persistence(tmp_path / "kairos.db"))
    project = Project("p1", "n", "d", tmp_path / "ws" / "p1", db=orch._db)
    project.workspace.mkdir(parents=True, exist_ok=True)

    seen: dict = {}

    def fake_make_agent(project_id, role, role_cls, provider, tools, bus, prompts):
        seen[role] = list(tools)
        return type("A", (), {"agent_id": project_id + "." + role})()

    monkeypatch.setattr(orch, "_make_agent", fake_make_agent)
    orch._create_agents(project)

    coder = {t.name for t in seen["coder"]}
    reviewer = {t.name for t in seen["reviewer"]}
    for name in NEW_TOOLS:
        assert name in coder, f"{name} missing from the Coder's tools"
    for name in ("data_analyze", "xlsx_read", "doc_read"):
        assert name in reviewer, f"{name} missing from the Reviewer's tools"
    # The interpreter is the Coder's, not the Reviewer's.
    assert "python_run" not in reviewer


def test_read_only_mode_keeps_the_readers_and_drops_the_interpreter():
    from kairos.coder_modes import CoderMode, apply_mode

    tools = [DataAnalyzeTool(allowed_root="."), XlsxReadTool(allowed_root="."),
             DocReadTool(allowed_root="."), PythonRunTool(allowed_root=".")]
    out, policy = apply_mode(tools, CoderMode.READ_ONLY)
    names = {t.name for t in out}

    assert {"data_analyze", "xlsx_read", "doc_read"} <= names
    assert "python_run" not in names
    assert any("python_run" in n for n, _r in policy.blocked)
