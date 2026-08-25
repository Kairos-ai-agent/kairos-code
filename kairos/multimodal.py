"""Multimodal input — image attachments for LLM prompts.

Lets a user attach an image (screenshot, diagram, photo) to a chat
message. The image is loaded from disk, base64-encoded, and emitted
in the correct content-part shape for the target provider:

* OpenAI:       {"type": "image_url", "image_url": {"url": "data:..."}}
* Anthropic:    {"type": "image", "source": {"type": "base64",
                                              "media_type": "...",
                                              "data": "..."}}

Design:

* Pure stdlib (`base64`, `mimetypes`, `pathlib`). No Pillow — we
  don't need to *render* images, just transport them faithfully.
* `ImageAttachment` is a small dataclass; the heavy work is in
  `to_openai_part()` and `to_anthropic_part()`.
* `load_image()` detects mime by extension (with a magic-byte fallback
  for `.png`/`.jpg`/`.gif`/`.webp` so a misnamed file still works).
* Size cap: 20 MB by default. Larger files raise `MultimodalError`
  rather than silently OOMing the LLM client.
"""
from __future__ import annotations

import base64
import binascii
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


# Provider-supported mime types. We accept anything that's a
# recognized image type; others raise MultimodalError.
SUPPORTED_IMAGE_MIMES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
}

# Default size cap. Anthropic accepts up to ~5MB per image; OpenAI
# 20MB. 20MB is a safe upper bound across both.
DEFAULT_MAX_BYTES = 20 * 1024 * 1024


class MultimodalError(ValueError):
    """Raised when an image attachment cannot be loaded or converted."""


@dataclass
class ImageAttachment:
    """A single image attachment.

    Attributes:
        path: absolute path to the image file (or None if the
              attachment was already base64-encoded by the caller)
        mime_type: detected mime (e.g. "image/png")
        base64_data: base64-encoded image bytes (no data: prefix)
        size_bytes: original file size
        filename: original filename for display
    """
    mime_type: str
    base64_data: str
    size_bytes: int
    filename: str = ""
    path: Optional[str] = None

    def to_data_url(self) -> str:
        """Return a data: URL for HTML <img src=...> embedding."""
        return f"data:{self.mime_type};base64,{self.base64_data}"

    def to_openai_part(self) -> Dict[str, Any]:
        """Convert to OpenAI's image_url content part shape.

        https://platform.openai.com/docs/guides/vision
        """
        if self.mime_type not in SUPPORTED_IMAGE_MIMES:
            raise MultimodalError(
                f"OpenAI does not accept {self.mime_type} as image input")
        return {
            "type": "image_url",
            "image_url": {"url": self.to_data_url()},
        }

    def to_anthropic_part(self) -> Dict[str, Any]:
        """Convert to Anthropic's image content block shape.

        https://docs.anthropic.com/claude/docs/vision
        """
        if self.mime_type not in SUPPORTED_IMAGE_MIMES:
            raise MultimodalError(
                f"Anthropic does not accept {self.mime_type} as image input")
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": self.mime_type,
                "data": self.base64_data,
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            # base64_data is large; callers can drop it if they only
            # want metadata. We include it for completeness.
            "base64_data": self.base64_data,
            "path": self.path,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ImageAttachment":
        return cls(
            mime_type=d["mime_type"],
            base64_data=d["base64_data"],
            size_bytes=int(d["size_bytes"]),
            filename=d.get("filename", ""),
            path=d.get("path"),
        )


def _detect_mime(path: Path) -> str:
    """Detect mime. Magic bytes take priority over extension so a
    misnamed file (e.g. "lie.png" with jpeg content) is classified
    by its real format. Extension is the fallback when magic bytes
    don't match a known format.
    """
    # Read 12 bytes — enough for the largest magic header we check
    # (RIFF....WEBP is 12 bytes).
    try:
        with path.open("rb") as f:
            head = f.read(12)
    except OSError as e:
        raise MultimodalError(f"cannot read {path}: {e}") from e
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    # Fall back to extension-based detection.
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def load_image(path: Union[str, Path],
               max_bytes: int = DEFAULT_MAX_BYTES) -> ImageAttachment:
    """Load an image file from disk and return an ImageAttachment.

    Raises:
        MultimodalError: if the file is missing, too large, or not
                         a supported image format.
    """
    p = Path(path)
    if not p.exists():
        raise MultimodalError(f"image not found: {p}")
    if not p.is_file():
        raise MultimodalError(f"not a file: {p}")
    size = p.stat().st_size
    if size == 0:
        raise MultimodalError(f"image is empty: {p}")
    if size > max_bytes:
        raise MultimodalError(
            f"image too large: {size} bytes (max {max_bytes})")
    mime = _detect_mime(p)
    if mime not in SUPPORTED_IMAGE_MIMES:
        raise MultimodalError(
            f"unsupported image type {mime!r} for {p.name}")
    try:
        raw = p.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
    except (OSError, binascii.Error) as e:
        raise MultimodalError(f"failed to encode {p}: {e}") from e
    return ImageAttachment(
        mime_type=mime,
        base64_data=b64,
        size_bytes=size,
        filename=p.name,
        path=str(p.resolve()),
    )


def load_images(paths: List[Union[str, Path]],
                max_bytes: int = DEFAULT_MAX_BYTES) -> List[ImageAttachment]:
    """Load multiple images. Fails fast on the first error."""
    return [load_image(p, max_bytes=max_bytes) for p in paths]


def build_multimodal_message(text: str,
                             images: List[ImageAttachment],
                             provider: str = "openai") -> Dict[str, Any]:
    """Build a single chat message that contains text + image parts.

    Returns a dict like:
        {"role": "user",
         "content": [
             {"type": "text", "text": "..."},
             {"type": "image_url"|"image", ...},
             ...
         ]}
    """
    if not images:
        return {"role": "user", "content": text}
    parts: List[Dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    if provider == "anthropic":
        for img in images:
            parts.append(img.to_anthropic_part())
    else:
        # Default to OpenAI shape.
        for img in images:
            parts.append(img.to_openai_part())
    return {"role": "user", "content": parts}


def total_attachment_size(images: List[ImageAttachment]) -> int:
    """Return the sum of `size_bytes` across all images."""
    return sum(img.size_bytes for img in images)


def estimate_openai_tokens(image: ImageAttachment) -> int:
    """Approximate the token cost of one image for OpenAI.

    OpenAI's image pricing model (as of 2024): images are tiled into
    512px squares; each tile costs ~170 tokens, plus a base of 85
    tokens. We can't compute the exact tile count without rendering,
    so we return a conservative estimate based on file size:
    ~750 tokens per MB. This is good enough for budget checks; the
    real cost depends on resolution, not file size.
    """
    mb = image.size_bytes / (1024 * 1024)
    return int(85 + mb * 750)
