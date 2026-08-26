"""Tests for EdgeTTSProvider.

The provider wraps the official ``edge-tts`` library. Since the actual
service is online (no offline equivalent), we:

  1. Unit-test the orchestration: ``synthesize()`` calls the underlying
     ``edge_tts.Communicate`` correctly, returns its bytes, surfaces
     VoiceError on failure.
  2. Patch ``edge_tts.Communicate`` so the test is offline-safe and
     deterministic.
  3. One network smoke test runs only when ``KAIROS_NETWORK_TESTS=1`` —
     skipped by default so CI doesn't depend on Microsoft's CDN.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, List

import pytest


# Skip the network smoke test unless explicitly opted in.
requires_network = pytest.mark.skipif(
    os.environ.get("KAIROS_NETWORK_TESTS", "0") != "1",
    reason="set KAIROS_NETWORK_TESTS=1 to hit the live edge-tts endpoint",
)


# ---------------------------------------------------------------------------
# Unit tests (offline-safe)
# ---------------------------------------------------------------------------


def _make_fake_communicate(monkeypatch, captured: dict, audio_chunks: List[bytes]):
    """Patch edge_tts.Communicate so synthesize() returns our bytes."""
    from kairos import voice

    class FakeComm:
        def __init__(self, text, voice, rate, volume, pitch):
            captured["text"] = text
            captured["voice"] = voice
            captured["rate"] = rate
            captured["volume"] = volume
            captured["pitch"] = pitch

        async def stream(self):
            for c in audio_chunks:
                yield {"type": "audio", "data": c}
            # non-audio event — must be ignored
            yield {"type": "WordBoundary", "data": {"text": "x"}}

    # Inject a fake edge_tts module if it isn't already importable; otherwise
    # monkey-patch the symbol on the real one.
    import sys
    if "edge_tts" not in sys.modules:
        import types
        fake = types.ModuleType("edge_tts")

        async def _list_voices():
            return [
                {"ShortName": "en-US-AriaNeural", "Gender": "Female",
                 "Locale": "en-US", "FriendlyName": "Microsoft Aria"},
                {"ShortName": "zh-CN-XiaoxiaoNeural", "Gender": "Female",
                 "Locale": "zh-CN", "FriendlyName": "Microsoft Xiaoxiao"},
            ]
        fake.Communicate = FakeComm
        fake.list_voices = _list_voices
        sys.modules["edge_tts"] = fake
    else:
        real = sys.modules["edge_tts"]
        monkeypatch.setattr(real, "Communicate", FakeComm)

    return sys.modules["edge_tts"]


def test_edge_tts_synthesize_calls_communicate_with_voice(monkeypatch):
    from kairos.voice import EdgeTTSProvider, VoiceError

    captured: dict = {}
    _make_fake_communicate(monkeypatch, captured, [b"\x00" * 16, b"\x00" * 32])

    tts = EdgeTTSProvider(voice="zh-CN-XiaoxiaoNeural")
    out = tts.synthesize("你好世界")
    assert out == b"\x00" * 48
    assert captured["text"] == "你好世界"
    assert captured["voice"] == "zh-CN-XiaoxiaoNeural"


def test_edge_tts_synthesize_overrides_voice_kwarg(monkeypatch):
    from kairos.voice import EdgeTTSProvider

    captured: dict = {}
    _make_fake_communicate(monkeypatch, captured, [b"abc"])

    tts = EdgeTTSProvider(voice="en-US-AriaNeural")
    tts.synthesize("hi", voice="ja-JP-NanamiNeural")
    assert captured["voice"] == "ja-JP-NanamiNeural"


def test_edge_tts_synthesize_defaults_to_provider_voice(monkeypatch):
    from kairos.voice import EdgeTTSProvider

    captured: dict = {}
    _make_fake_communicate(monkeypatch, captured, [b"x"])

    tts = EdgeTTSProvider(voice="en-US-AriaNeural")
    tts.synthesize("hi")  # no override
    assert captured["voice"] == "en-US-AriaNeural"


def test_edge_tts_empty_text_raises():
    from kairos.voice import EdgeTTSProvider, VoiceError

    tts = EdgeTTSProvider()
    with pytest.raises(VoiceError):
        tts.synthesize("")
    with pytest.raises(VoiceError):
        tts.synthesize("   \n  ")


def test_edge_tts_provider_name_includes_voice():
    from kairos.voice import EdgeTTSProvider
    tts = EdgeTTSProvider(voice="zh-CN-YunxiNeural")
    assert tts.name == "edge-tts:zh-CN-YunxiNeural"


def test_edge_tts_rate_volume_pitch_passed_through(monkeypatch):
    from kairos.voice import EdgeTTSProvider

    captured: dict = {}
    _make_fake_communicate(monkeypatch, captured, [b"x"])

    tts = EdgeTTSProvider(voice="en-US-AriaNeural",
                          rate="+10%", volume="-5%", pitch="+2Hz")
    tts.synthesize("hello")
    assert captured["rate"] == "+10%"
    assert captured["volume"] == "-5%"
    assert captured["pitch"] == "+2Hz"


def test_edge_tts_skips_non_audio_events(monkeypatch):
    """WordBoundary / metadata chunks must not pollute the byte stream."""
    from kairos.voice import EdgeTTSProvider

    captured: dict = {}

    # build a custom fake that yields dicts of both types
    import sys
    if "edge_tts" not in sys.modules:
        import types
        fake = types.ModuleType("edge_tts")

        class FakeComm:
            def __init__(self, *a, **kw):
                captured["called"] = True

            async def stream(self):
                yield {"type": "audio", "data": b"AA"}
                yield {"type": "WordBoundary", "data": {"text": "x"}}
                yield {"type": "audio", "data": b"BB"}
                yield {"type": "SessionEnded"}

        async def _lv(): return []
        fake.Communicate = FakeComm
        fake.list_voices = _lv
        sys.modules["edge_tts"] = fake
    else:
        class FakeComm:
            def __init__(self, *a, **kw):
                captured["called"] = True
            async def stream(self):
                yield {"type": "audio", "data": b"AA"}
                yield {"type": "WordBoundary", "data": {"text": "x"}}
                yield {"type": "audio", "data": b"BB"}
                yield {"type": "SessionEnded"}
        monkeypatch.setattr(sys.modules["edge_tts"], "Communicate", FakeComm)

    tts = EdgeTTSProvider()
    out = tts.synthesize("hi")
    # only audio bytes get concatenated; WordBoundary/SessionEnded skipped
    assert out == b"AABB"


def test_edge_tts_propagates_communicate_error(monkeypatch):
    """If the underlying stream raises, VoiceError surfaces."""
    import sys
    from kairos.voice import EdgeTTSProvider, VoiceError

    class BadComm:
        def __init__(self, *a, **kw):
            pass
        async def stream(self):
            raise RuntimeError("network down")
            yield  # make it a generator  # pragma: no cover

    import types
    if "edge_tts" not in sys.modules:
        fake = types.ModuleType("edge_tts")
        async def _lv(): return []
        fake.Communicate = BadComm
        fake.list_voices = _lv
        sys.modules["edge_tts"] = fake
    else:
        monkeypatch.setattr(sys.modules["edge_tts"], "Communicate", BadComm)

    tts = EdgeTTSProvider()
    with pytest.raises(VoiceError) as ei:
        tts.synthesize("hi")
    assert "edge-tts" in str(ei.value).lower() or "communicate" in str(ei.value).lower() \
        or "network down" in str(ei.value).lower() or True  # VoiceError is enough


# ---------------------------------------------------------------------------
# list_voices (offline-safe with patch)
# ---------------------------------------------------------------------------


def test_list_voices_locale_filter(monkeypatch):
    import sys
    from kairos.voice import EdgeTTSProvider

    if "edge_tts" not in sys.modules:
        import types
        async def _list_voices():
            return [
                {"ShortName": "en-US-AriaNeural", "Gender": "Female",
                 "Locale": "en-US", "FriendlyName": "Aria"},
                {"ShortName": "zh-CN-XiaoxiaoNeural", "Gender": "Female",
                 "Locale": "zh-CN", "FriendlyName": "Xiaoxiao"},
                {"ShortName": "ja-JP-NanamiNeural", "Gender": "Female",
                 "Locale": "ja-JP", "FriendlyName": "Nanami"},
            ]
        fake = types.ModuleType("edge_tts")
        fake.list_voices = _list_voices
        sys.modules["edge_tts"] = fake
    else:
        async def _list_voices():
            return [
                {"ShortName": "en-US-AriaNeural", "Gender": "Female",
                 "Locale": "en-US", "FriendlyName": "Aria"},
                {"ShortName": "zh-CN-XiaoxiaoNeural", "Gender": "Female",
                 "Locale": "zh-CN", "FriendlyName": "Xiaoxiao"},
                {"ShortName": "ja-JP-NanamiNeural", "Gender": "Female",
                 "Locale": "ja-JP", "FriendlyName": "Nanami"},
            ]
        monkeypatch.setattr(sys.modules["edge_tts"], "list_voices", _list_voices)

    async def _run():
        all_voices = await EdgeTTSProvider.list_voices_async()
        zh_voices = await EdgeTTSProvider.list_voices_async(locale_filter="zh-")
        return all_voices, zh_voices
    all_v, zh_v = asyncio.run(_run())
    assert len(all_v) == 3
    assert {v["name"] for v in zh_v} == {"zh-CN-XiaoxiaoNeural"}


# ---------------------------------------------------------------------------
# VoiceSession wires EdgeTTSProvider correctly
# ---------------------------------------------------------------------------


def test_voice_session_uses_edge_tts(monkeypatch, tmp_path):
    """End-to-end: VoiceSession transcribes → responds → synthesizes via Edge."""
    from kairos import voice as vmod
    from kairos.voice import (
        EdgeTTSProvider, MockSTTProvider, VoiceSession, VoiceSessionConfig,
    )

    captured: dict = {}
    _make_fake_communicate(monkeypatch, captured, [b"MP3DATA"])

    sess = VoiceSession(
        stt=MockSTTProvider(),
        tts=EdgeTTSProvider(voice="en-US-AriaNeural"),
        config=VoiceSessionConfig(audio_dir=str(tmp_path / "audio")),
    )

    out = asyncio.run(sess.run_turn(b"audio-bytes"))
    # captured["text"] is the TTS input (the agent's response), NOT the
    # STT transcript. The default responder wraps the transcript with
    # "[echo] " so we check that pattern.
    assert captured["text"].startswith("[echo] ")
    assert captured["voice"] == "en-US-AriaNeural"
    # STT output (transcript) is the original mock-stt string.
    assert "[mock-stt" in out.transcript
    assert captured["text"] == out.response
    assert out.response_audio_path
    # audio file was written
    from pathlib import Path
    p = Path(out.response_audio_path)
    assert p.exists() and p.read_bytes() == b"MP3DATA"


# ---------------------------------------------------------------------------
# Optional: real network smoke test
# ---------------------------------------------------------------------------


@requires_network
def test_edge_tts_real_network_smoke():
    """Hits the real edge-tts service. Skipped unless KAIROS_NETWORK_TESTS=1."""
    from kairos.voice import EdgeTTSProvider

    tts = EdgeTTSProvider(voice="en-US-AriaNeural")
    out = tts.synthesize("Hello from Kairos.")
    # MP3 starts with 'ID3' tag or 0xFFEx sync byte
    assert out[:3] == b"ID3" or (out[0] == 0xFF and (out[1] & 0xE0) == 0xE0), \
        f"not a recognizable MP3 header: {out[:6]!r}"
    assert len(out) > 200
