"""Tests for the multimodal (image attachment) module."""
from __future__ import annotations

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.multimodal import (
    DEFAULT_MAX_BYTES,
    ImageAttachment,
    MultimodalError,
    SUPPORTED_IMAGE_MIMES,
    build_multimodal_message,
    estimate_openai_tokens,
    load_image,
    load_images,
    total_attachment_size,
)


# Tiny valid PNG header + payload. Just enough to look like a real
# file to the magic-byte sniffer.
PNG_HEADER = b"\x89PNG\r\n\x1a\n"
JPEG_HEADER = b"\xff\xd8\xff\xe0"
GIF_HEADER = b"GIF89a"
WEBP_HEADER = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP"


# ---------------------------------------------------------------------------
# load_image
# ---------------------------------------------------------------------------


def test_load_png_by_extension(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(PNG_HEADER + b"fake-png-data")
    img = load_image(p)
    assert img.mime_type == "image/png"
    assert img.filename == "a.png"
    assert img.size_bytes == len(PNG_HEADER) + len(b"fake-png-data")
    decoded = base64.b64decode(img.base64_data)
    assert decoded.startswith(PNG_HEADER)


def test_load_jpeg_by_magic_bytes(tmp_path):
    """Even without a .jpg extension, magic bytes should detect jpeg."""
    p = tmp_path / "mystery.bin"
    p.write_bytes(JPEG_HEADER + b"more")
    img = load_image(p)
    assert img.mime_type == "image/jpeg"


def test_load_gif_by_magic_bytes(tmp_path):
    p = tmp_path / "g.bin"
    p.write_bytes(GIF_HEADER + b"abc")
    img = load_image(p)
    assert img.mime_type == "image/gif"


def test_load_webp_by_magic_bytes(tmp_path):
    p = tmp_path / "w.bin"
    p.write_bytes(WEBP_HEADER + b"abc")
    img = load_image(p)
    assert img.mime_type == "image/webp"


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(MultimodalError, match="not found"):
        load_image(tmp_path / "nope.png")


def test_load_directory_raises(tmp_path):
    d = tmp_path / "subdir"
    d.mkdir()
    with pytest.raises(MultimodalError, match="not a file"):
        load_image(d)


def test_load_empty_file_raises(tmp_path):
    p = tmp_path / "empty.png"
    p.write_bytes(b"")
    with pytest.raises(MultimodalError, match="empty"):
        load_image(p)


def test_load_oversized_file_raises(tmp_path):
    p = tmp_path / "big.png"
    p.write_bytes(PNG_HEADER + b"x" * 100)
    with pytest.raises(MultimodalError, match="too large"):
        load_image(p, max_bytes=50)


def test_load_unsupported_format_raises(tmp_path):
    """A bmp file (not in SUPPORTED_IMAGE_MIMES) should be rejected."""
    p = tmp_path / "a.bmp"
    p.write_bytes(b"BM" + b"x" * 100)
    with pytest.raises(MultimodalError, match="unsupported"):
        load_image(p)


def test_load_corrupt_image_extension_uses_magic(tmp_path):
    """A file with .png extension but a jpeg magic byte should be
    classified as jpeg (the magic byte wins)."""
    p = tmp_path / "lie.png"
    p.write_bytes(JPEG_HEADER + b"abc")
    img = load_image(p)
    assert img.mime_type == "image/jpeg"


# ---------------------------------------------------------------------------
# ImageAttachment conversions
# ---------------------------------------------------------------------------


def _mk(mime: str = "image/png", data: bytes = b"abc") -> ImageAttachment:
    return ImageAttachment(
        mime_type=mime,
        base64_data=base64.b64encode(data).decode("ascii"),
        size_bytes=len(data),
        filename="x.png",
    )


def test_data_url_format():
    img = _mk("image/png", b"abc")
    url = img.to_data_url()
    assert url.startswith("data:image/png;base64,")
    # round-trip
    payload = url.split(",", 1)[1]
    assert base64.b64decode(payload) == b"abc"


def test_to_openai_part_shape():
    img = _mk("image/png", b"abc")
    part = img.to_openai_part()
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/png;base64,")


def test_to_openai_part_rejects_unsupported():
    img = _mk("image/bmp", b"abc")
    with pytest.raises(MultimodalError, match="OpenAI does not accept"):
        img.to_openai_part()


def test_to_anthropic_part_shape():
    img = _mk("image/jpeg", b"abc")
    part = img.to_anthropic_part()
    assert part["type"] == "image"
    assert part["source"]["type"] == "base64"
    assert part["source"]["media_type"] == "image/jpeg"
    assert part["source"]["data"] == img.base64_data


def test_to_anthropic_part_rejects_unsupported():
    img = _mk("image/bmp", b"abc")
    with pytest.raises(MultimodalError, match="Anthropic does not accept"):
        img.to_anthropic_part()


def test_to_dict_and_from_dict_round_trip():
    img = _mk("image/png", b"abc")
    d = img.to_dict()
    again = ImageAttachment.from_dict(d)
    assert again.mime_type == "image/png"
    assert again.base64_data == img.base64_data
    assert again.size_bytes == 3
    assert again.filename == "x.png"


# ---------------------------------------------------------------------------
# build_multimodal_message
# ---------------------------------------------------------------------------


def test_message_text_only():
    msg = build_multimodal_message("hello", [], provider="openai")
    assert msg == {"role": "user", "content": "hello"}


def test_message_text_plus_openai_image():
    img = _mk("image/png", b"abc")
    msg = build_multimodal_message("what is this?", [img], provider="openai")
    assert msg["role"] == "user"
    parts = msg["content"]
    assert isinstance(parts, list)
    assert parts[0] == {"type": "text", "text": "what is this?"}
    assert parts[1]["type"] == "image_url"
    assert "data:image/png" in parts[1]["image_url"]["url"]


def test_message_text_plus_anthropic_image():
    img = _mk("image/jpeg", b"abc")
    msg = build_multimodal_message("describe", [img], provider="anthropic")
    parts = msg["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image"
    assert parts[1]["source"]["media_type"] == "image/jpeg"


def test_message_multiple_images():
    imgs = [_mk("image/png", b"a"), _mk("image/jpeg", b"b")]
    msg = build_multimodal_message("compare", imgs)
    parts = msg["content"]
    # text + 2 images
    assert len(parts) == 3
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    assert parts[2]["type"] == "image_url"


def test_message_no_text_with_images():
    img = _mk("image/png", b"abc")
    msg = build_multimodal_message("", [img])
    parts = msg["content"]
    # No text part when text is empty.
    assert len(parts) == 1
    assert parts[0]["type"] == "image_url"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_load_images_batch(tmp_path):
    p1 = tmp_path / "a.png"
    p1.write_bytes(PNG_HEADER + b"1")
    p2 = tmp_path / "b.jpg"
    p2.write_bytes(JPEG_HEADER + b"22")
    imgs = load_images([p1, p2])
    assert len(imgs) == 2
    assert imgs[0].mime_type == "image/png"
    assert imgs[1].mime_type == "image/jpeg"


def test_total_attachment_size():
    imgs = [_mk("image/png", b"x" * 100), _mk("image/png", b"x" * 200)]
    assert total_attachment_size(imgs) == 300


def test_estimate_openai_tokens_scales_with_size():
    small = _mk("image/png", b"x" * 1024)            # 1 KB
    big = _mk("image/png", b"x" * 1024 * 1024)       # 1 MB
    assert estimate_openai_tokens(small) < estimate_openai_tokens(big)
    # 1 MB should give a meaningful token estimate.
    assert estimate_openai_tokens(big) > 100


def test_default_max_bytes_is_at_least_5mb():
    """Both Anthropic (~5MB) and OpenAI (~20MB) cap per image. The
    default must clear Anthropic's limit so we never silently drop
    images the user attached."""
    assert DEFAULT_MAX_BYTES >= 5 * 1024 * 1024
