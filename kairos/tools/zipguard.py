"""Shared zip-bomb guard for the stdlib Office readers (``xlsx_read``/``doc_read``).

Both readers open an untrusted ``.xlsx`` / ``.docx`` / ``.pptx`` -- which is a
ZIP container -- with :mod:`zipfile` and then decompress named parts. A hostile
file can declare, in its central directory, parts that expand to gigabytes or
that compress at an absurd ratio, so a plain ``zf.read(part)`` would blow up
memory *before* any XML parsing happens. This module refuses such a file up
front.

The check reads **only the central directory** (:meth:`zipfile.ZipFile.infolist`),
which is metadata: nothing is decompressed to decide, so a declared-huge part is
rejected without ever being expanded into memory.

The limits are deliberately generous for real Office documents (mostly XML text
that compresses well) and leave headroom for a large but honest workbook, while
still stopping a bomb:

* one part may not expand past :data:`MAX_ENTRY_BYTES` (32 MiB);
* the whole archive may not expand past :data:`MAX_TOTAL_BYTES` (128 MiB);
* no non-empty part may exceed a compression ratio of :data:`MAX_RATIO` (100:1).
"""
from __future__ import annotations

import zipfile
from typing import Any

#: Largest single part we will decompress, in bytes (32 MiB).
MAX_ENTRY_BYTES = 32 * 1024 * 1024
#: Largest total expansion we will decompress, in bytes (128 MiB).
MAX_TOTAL_BYTES = 128 * 1024 * 1024
#: Largest tolerated ``file_size / compress_size`` ratio for a part (100:1).
MAX_RATIO = 100


class ZipBombError(ValueError):
    """The archive's declared sizes exceed a limit; refuse it, readably."""


def _mib(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MiB"


def guard_zip(zf: zipfile.ZipFile, path: Any = "") -> None:
    """Refuse a zip whose *declared* (central-directory) sizes are a bomb.

    Reads only ``zf.infolist()`` -- never ``zf.read`` -- so a huge declared
    entry is rejected without being decompressed. Raises :class:`ZipBombError`
    naming the limit and the actual value; returns ``None`` when the archive is
    within budget.
    """
    where = f"{path}: " if path else ""
    total = 0
    for info in zf.infolist():
        size = int(info.file_size)
        if size > MAX_ENTRY_BYTES:
            raise ZipBombError(
                f"{where}a part ({info.filename!r}) declares an uncompressed "
                f"size of {_mib(size)}, over the {_mib(MAX_ENTRY_BYTES)} "
                f"per-part limit; refusing to open it. (This is a ZIP-bomb "
                f"guard -- the size comes from the archive's central "
                f"directory, so nothing was decompressed.)")
        comp = int(info.compress_size)
        if comp > 0 and size // comp > MAX_RATIO:
            raise ZipBombError(
                f"{where}a part ({info.filename!r}) declares a compression "
                f"ratio of {size // comp}:1, over the {MAX_RATIO}:1 limit; "
                f"refusing to open it. (This is a ZIP-bomb guard.)")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ZipBombError(
                f"{where}the archive declares a total uncompressed size over "
                f"{_mib(MAX_TOTAL_BYTES)} (reached {_mib(total)}); refusing to "
                f"open it. (This is a ZIP-bomb guard.)")
