"""The terminal tool must decode cmd / PowerShell output the way the console does.

A failed Windows command printed 找不到文件 as cp936 bytes; decoding as UTF-8
with errors="replace" turned it into replacement characters, so both the user's
tool summary and the model saw garbage instead of the error.
"""
from __future__ import annotations

from kairos.tools.terminal import _console_encodings, _decode_console_output


def test_cp936_error_message_survives():
    # Encoding is injected so the test proves the *order* of attempts rather
    # than depending on the language of the machine running the suite.
    raw = "系统找不到指定的文件。".encode("cp936")
    assert _decode_console_output(raw, ["cp936"]) == "系统找不到指定的文件。"


def test_real_console_code_pages_are_probed():
    import os

    encs = _console_encodings()
    assert encs, "there must always be a fallback"
    if os.name == "nt":
        # UTF-8-itis is exactly the bug this guards: a Windows box answering
        # "utf-8" here means the fallback would never fire.
        assert any(e.startswith("cp") for e in encs), encs


def test_utf8_still_wins():
    raw = "hello 世界".encode("utf-8")
    assert _decode_console_output(raw) == "hello 世界"


def test_ascii_is_byte_identical():
    assert _decode_console_output(b"ok\n") == "ok\n"


def test_empty_input():
    assert _decode_console_output(b"") == ""


def test_undecodable_bytes_never_raise():
    # Neither UTF-8 nor cp936 can decode this; it must degrade, not explode.
    assert _decode_console_output(b"\xff\xfe\x00\x81")
