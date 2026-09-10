"""Tests for chat attachments (any file format) — R38.7.

The composer's 📎 button was disabled with an "Attach file (coming soon)"
tooltip. It now uploads through ``POST /api/projects/{id}/attachments``
(any format, 50 MB cap, written into ``<project root>/attachments/``) and
the chat/task routes fold the uploaded paths into the user message as an
``[附件]`` block so the Coder can open them with its own file tools.

We exercise the route functions directly with a fake orchestrator, like
``test_checkpoints_api.py`` does — no HTTP server, no real DB.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api.deps as _api_deps  # noqa: E402
import api.routes.projects as _projects  # noqa: E402
from api.routes.projects import (  # noqa: E402
    ATTACHMENTS_DIRNAME,
    MAX_ATTACHMENT_BYTES,
    attachment_prompt_block,
    chat,
    delete_attachment,
    list_attachments,
    upload_attachments,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeCoder:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def chat(self, text: str) -> str:
        self.seen.append(text)
        return "ok"


class _FakeProject:
    def __init__(self, work_dir: Path) -> None:
        self.id = "p1"
        self.name = "Test"
        self.description = ""
        self.work_dir = str(work_dir)
        self.workspace = work_dir
        self.coder = _FakeCoder()
        self.runtime = None
        self.loop_task = None


class _FakeOrchestrator:
    def __init__(self, project: _FakeProject) -> None:
        self._project = project
        self._db = None            # route tolerates a missing DB

    def get_project(self, project_id: str):  # noqa: D401
        return self._project if project_id == self._project.id else None


def _upload(name: str, data: bytes, mime: str | None = None) -> UploadFile:
    headers = Headers({"content-type": mime}) if mime else None
    return UploadFile(file=io.BytesIO(data), filename=name, headers=headers)


@pytest.fixture
def project(tmp_path):
    """Fake orchestrator + a project whose work_dir is a temp directory."""
    work_dir = tmp_path / "proj"
    work_dir.mkdir(parents=True, exist_ok=True)
    proj = _FakeProject(work_dir)
    real = _api_deps.orchestrator
    _api_deps.orchestrator = _FakeOrchestrator(proj)
    try:
        yield proj
    finally:
        _api_deps.orchestrator = real


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


async def test_upload_accepts_any_format_and_writes_it_verbatim(project):
    """A binary file (PNG header) survives byte-for-byte, any extension."""
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
    resp = await upload_attachments("p1", files=[_upload("小猫截图.png", png,
                                                         "image/png")])

    assert resp["status"] == "ok"
    item = resp["attachments"][0]
    assert item["name"] == "小猫截图.png"
    assert item["rel_path"] == f"{ATTACHMENTS_DIRNAME}/小猫截图.png"
    assert item["size"] == len(png)
    on_disk = Path(project.work_dir) / item["rel_path"]
    assert on_disk.read_bytes() == png


async def test_upload_keeps_multiple_files_and_dedupes_names(project):
    resp = await upload_attachments("p1", files=[
        _upload("a.txt", b"one"),
        _upload("a.txt", b"two"),
        _upload("b.zip", b"PK\x03\x04binary"),
    ])
    names = [a["name"] for a in resp["attachments"]]
    assert names == ["a.txt", "a-1.txt", "b.zip"]
    assert (Path(project.work_dir) / "attachments" / "a.txt").read_bytes() == b"one"
    assert (Path(project.work_dir) / "attachments" / "a-1.txt").read_bytes() == b"two"


async def test_upload_strips_path_traversal_from_filename(project):
    resp = await upload_attachments(
        "p1", files=[_upload("../../evil.txt", b"x")])
    item = resp["attachments"][0]
    assert item["name"] == "evil.txt"
    # stayed inside the project
    assert (Path(project.work_dir) / item["rel_path"]).is_file()
    assert not (Path(project.work_dir).parent.parent / "evil.txt").exists()


async def test_upload_repairs_latin1_mojibake_filename(project):
    """A client that lets the multipart header decode as latin-1 hands us
    mojibake; the stored name must still be the real Chinese filename."""
    mojibake = "界面截图.png".encode("utf-8").decode("latin-1")
    resp = await upload_attachments("p1", files=[_upload(mojibake, b"x")])
    assert resp["attachments"][0]["name"] == "界面截图.png"


async def test_upload_guesses_mime_for_generic_part_content_type(project):
    """curl often sends application/octet-stream for the part; the mime shown
    in the [附件] block should still be the real type."""
    resp = await upload_attachments(
        "p1", files=[_upload("清单.csv", b"a,b", "application/octet-stream")])
    assert resp["attachments"][0]["mime"] == "text/csv"


async def test_upload_rejects_oversized_file(project):
    big = b"0" * (MAX_ATTACHMENT_BYTES + 1)
    with pytest.raises(HTTPException) as exc:
        await upload_attachments("p1", files=[_upload("huge.bin", big)])
    assert exc.value.status_code == 413


async def test_upload_unknown_project_404(project):
    with pytest.raises(HTTPException) as exc:
        await upload_attachments("nope", files=[_upload("a.txt", b"x")])
    assert exc.value.status_code == 404


async def test_list_and_delete_attachment(project):
    await upload_attachments("p1", files=[_upload("note.md", b"# hi")])
    listed = await list_attachments("p1")
    assert [a["name"] for a in listed["attachments"]] == ["note.md"]

    await delete_attachment("p1", f"{ATTACHMENTS_DIRNAME}/note.md")
    assert (await list_attachments("p1"))["attachments"] == []
    assert not (Path(project.work_dir) / "attachments" / "note.md").exists()


# ---------------------------------------------------------------------------
# The [附件] block that the model sees
# ---------------------------------------------------------------------------


def test_attachment_block_lists_path_size_and_mime(project):
    out_dir = Path(project.work_dir) / ATTACHMENTS_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "spec.md").write_bytes(b"x" * 2048)

    block = attachment_prompt_block(Path(project.work_dir),
                                    ["attachments/spec.md"])
    assert "[附件 / attachments]" in block
    assert "- attachments/spec.md (2.0 KB, text/markdown)" in block
    assert "file_read" in block


def test_attachment_block_is_empty_without_attachments(project):
    assert attachment_prompt_block(Path(project.work_dir), []) == ""
    assert attachment_prompt_block(Path(project.work_dir), None) == ""


def test_attachment_block_rejects_paths_outside_the_project(project, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        attachment_prompt_block(Path(project.work_dir),
                               ["../outside.txt", str(outside)])
    assert exc.value.status_code == 400


def test_attachment_block_rejects_missing_file(project):
    with pytest.raises(HTTPException) as exc:
        attachment_prompt_block(Path(project.work_dir), ["attachments/nope.bin"])
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# /chat + /start fold the attachments into the message
# ---------------------------------------------------------------------------


async def test_chat_appends_attachment_block_and_echoes_message(project):
    await upload_attachments("p1", files=[_upload("data.csv", b"a,b\n1,2\n",
                                                  "text/csv")])
    body = _projects.ChatRequest(message="看看这个表",
                                 attachments=[f"{ATTACHMENTS_DIRNAME}/data.csv"])

    resp = await chat("p1", body)

    assert resp["reply"] == "ok"
    sent = project.coder.seen[-1]
    assert sent.startswith("看看这个表")
    assert f"- {ATTACHMENTS_DIRNAME}/data.csv" in sent
    # the echoed message is what the thread should render / what got persisted
    assert resp["message"] == sent


async def test_chat_without_attachments_is_unchanged(project):
    body = _projects.ChatRequest(message="你好")
    resp = await chat("p1", body)
    assert project.coder.seen[-1] == "你好"
    assert resp["message"] == "你好"


async def test_chat_allows_attachment_only_message(project):
    """Dropping a file with no text at all still sends (no 400)."""
    await upload_attachments("p1", files=[_upload("shot.png", b"\x89PNG")])
    body = _projects.ChatRequest(message="",
                                 attachments=[f"{ATTACHMENTS_DIRNAME}/shot.png"])
    resp = await chat("p1", body)
    assert "shot.png" in project.coder.seen[-1]
    assert resp["message"].startswith("[附件")


async def test_chat_rejects_missing_attachment_with_404(project):
    body = _projects.ChatRequest(message="hi",
                                 attachments=[f"{ATTACHMENTS_DIRNAME}/ghost.png"])
    with pytest.raises(HTTPException) as exc:
        await chat("p1", body)
    assert exc.value.status_code == 404
