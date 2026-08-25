"""Tests for the voice mode module (P2-5)."""
from __future__ import annotations

import asyncio
import hashlib
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.voice import (
    MockSTTProvider,
    MockTTSProvider,
    VoiceError,
    VoiceSession,
    VoiceSessionConfig,
    VoiceState,
    VoiceTurn,
    WhisperSTTProvider,
)


# ---------------------------------------------------------------------------
# MockSTTProvider
# ---------------------------------------------------------------------------


def test_mock_stt_deterministic():
    stt = MockSTTProvider()
    audio = b"hello world" * 100
    a = stt.transcribe(audio)
    b = stt.transcribe(audio)
    assert a == b
    # The transcript mentions the SHA-256 prefix and the byte count.
    assert "sha256:" in a
    assert str(len(audio)) in a


def test_mock_stt_empty_audio_raises():
    stt = MockSTTProvider()
    with pytest.raises(VoiceError, match="empty"):
        stt.transcribe(b"")


def test_mock_stt_different_audio_different_transcript():
    stt = MockSTTProvider()
    a = stt.transcribe(b"foo")
    b = stt.transcribe(b"bar")
    assert a != b


def test_mock_stt_name():
    assert MockSTTProvider().name == "mock-stt"


# ---------------------------------------------------------------------------
# MockTTSProvider
# ---------------------------------------------------------------------------


def test_mock_tts_returns_valid_wav():
    tts = MockTTSProvider()
    audio = tts.synthesize("hello")
    assert len(audio) > 0
    # RIFF header is at the start.
    assert audio[:4] == b"RIFF"
    assert audio[8:12] == b"WAVE"


def test_mock_tts_wav_is_parseable():
    """The silent WAV should be a real, parseable file."""
    tts = MockTTSProvider()
    audio = tts.synthesize("test", voice="alice")
    import io
    with wave.open(io.BytesIO(audio), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2  # 16-bit
        assert wf.getframerate() == 8000


def test_mock_tts_empty_text_raises():
    tts = MockTTSProvider()
    with pytest.raises(VoiceError, match="empty"):
        tts.synthesize("")


def test_mock_tts_voice_param_does_not_crash():
    """The mock ignores voice; the real engines use it. We just
    verify the parameter is accepted."""
    tts = MockTTSProvider()
    a = tts.synthesize("hi", voice="alice")
    b = tts.synthesize("hi", voice="bob")
    # Mock returns identical bytes regardless of voice.
    assert a == b


def test_mock_tts_name():
    assert MockTTSProvider().name == "mock-tts"


# ---------------------------------------------------------------------------
# WhisperSTTProvider (lazy load — only error path tested)
# ---------------------------------------------------------------------------


def test_whisper_stt_raises_when_no_engine(monkeypatch):
    """If neither faster_whisper nor openai-whisper is installed,
    `transcribe` should raise VoiceError, not crash with ImportError."""
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *args, **kwargs):
        if name in ("faster_whisper", "whisper"):
            raise ImportError(f"no {name}")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    p = WhisperSTTProvider()
    with pytest.raises(VoiceError, match="neither"):
        p.transcribe(b"fake-audio")


# ---------------------------------------------------------------------------
# VoiceSession
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_run_turn_full_flow(tmp_path):
    stt = MockSTTProvider()
    tts = MockTTSProvider()
    sess = VoiceSession(stt=stt, tts=tts,
                         config=VoiceSessionConfig(audio_dir=str(tmp_path)))
    audio = b"sample audio bytes"
    turn = await sess.run_turn(audio)
    assert turn.transcript  # not empty
    assert turn.response.startswith("[echo] ")
    assert turn.response_audio_path is not None
    assert Path(turn.response_audio_path).exists()
    # WAV file actually contains data.
    wav_data = Path(turn.response_audio_path).read_bytes()
    assert wav_data[:4] == b"RIFF"
    # User audio was saved.
    assert turn.user_audio_path is not None
    assert Path(turn.user_audio_path).exists()
    # State returned to idle.
    assert turn.state == VoiceState.IDLE
    assert sess.state == VoiceState.IDLE
    assert len(sess.turns) == 1


@pytest.mark.asyncio
async def test_session_run_turn_with_custom_responder(tmp_path):
    stt = MockSTTProvider()
    tts = MockTTSProvider()

    def responder(text):
        return f"answer to: {text}"

    sess = VoiceSession(stt=stt, tts=tts, responder=responder,
                         config=VoiceSessionConfig(audio_dir=str(tmp_path)))
    turn = await sess.run_turn(b"user audio")
    assert turn.transcript
    assert turn.response == f"answer to: {turn.transcript}"


@pytest.mark.asyncio
async def test_session_collects_all_turns(tmp_path):
    stt = MockSTTProvider()
    tts = MockTTSProvider()
    sess = VoiceSession(stt=stt, tts=tts,
                         config=VoiceSessionConfig(audio_dir=str(tmp_path)))
    for i in range(3):
        await sess.run_turn(f"audio {i}".encode())
    assert len(sess.turns) == 3
    assert all(t.state == VoiceState.IDLE for t in sess.turns)


@pytest.mark.asyncio
async def test_session_run_turn_stt_failure_marks_error(tmp_path):
    class FailingSTT:
        name = "failing-stt"
        def transcribe(self, audio, **kwargs):
            raise RuntimeError("whisper oom")

    sess = VoiceSession(stt=FailingSTT(), tts=MockTTSProvider(),
                         config=VoiceSessionConfig(audio_dir=str(tmp_path)))
    with pytest.raises(RuntimeError, match="whisper oom"):
        await sess.run_turn(b"audio")
    # The turn is still recorded with state=ERROR.
    assert len(sess.turns) == 1
    assert sess.turns[0].state == VoiceState.ERROR
    assert "stt:" in sess.turns[0].error
    assert sess.state == VoiceState.ERROR
    assert "whisper oom" in sess.last_error


@pytest.mark.asyncio
async def test_session_run_turn_tts_failure_marks_error(tmp_path):
    class FailingTTS:
        name = "failing-tts"
        def synthesize(self, text, **kwargs):
            raise RuntimeError("espeak crashed")

    stt = MockSTTProvider()
    sess = VoiceSession(stt=stt, tts=FailingTTS(),
                         config=VoiceSessionConfig(audio_dir=str(tmp_path)))
    with pytest.raises(RuntimeError, match="espeak crashed"):
        await sess.run_turn(b"audio")
    assert sess.turns[0].state == VoiceState.ERROR
    assert "tts:" in sess.turns[0].error


@pytest.mark.asyncio
async def test_session_can_skip_user_audio_save(tmp_path):
    sess = VoiceSession(stt=MockSTTProvider(), tts=MockTTSProvider(),
                         config=VoiceSessionConfig(audio_dir=str(tmp_path)))
    turn = await sess.run_turn(b"audio", save_user_audio=False)
    assert turn.user_audio_path is None


def test_session_synthesize_only(tmp_path):
    """synthesize_only skips STT and just turns text into audio."""
    sess = VoiceSession(stt=MockSTTProvider(), tts=MockTTSProvider(),
                         config=VoiceSessionConfig(audio_dir=str(tmp_path)))
    path = sess.synthesize_only("hello world")
    assert path.exists()
    assert path.read_bytes()[:4] == b"RIFF"


def test_session_synthesize_only_empty_text_raises(tmp_path):
    sess = VoiceSession(stt=MockSTTProvider(), tts=MockTTSProvider(),
                         config=VoiceSessionConfig(audio_dir=str(tmp_path)))
    with pytest.raises(VoiceError, match="text is required"):
        sess.synthesize_only("")


def test_session_to_dict_includes_state_and_turns(tmp_path):
    sess = VoiceSession(stt=MockSTTProvider(), tts=MockTTSProvider(),
                         config=VoiceSessionConfig(audio_dir=str(tmp_path)))
    d = sess.to_dict()
    assert d["state"] == "idle"
    assert d["stt"] == "mock-stt"
    assert d["tts"] == "mock-tts"
    assert d["turn_count"] == 0
    assert d["turns"] == []


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_mock_stt_satisfies_protocol():
    """MockSTTProvider should be usable wherever STTProvider is expected."""
    from kairos.voice import STTProvider
    p: STTProvider = MockSTTProvider()
    # The Protocol's only method is transcribe; calling it works.
    out = p.transcribe(b"audio")
    assert out


def test_mock_tts_satisfies_protocol():
    from kairos.voice import TTSProvider
    p: TTSProvider = MockTTSProvider()
    out = p.synthesize("hi")
    assert out[:4] == b"RIFF"


# ---------------------------------------------------------------------------
# VoiceTurn
# ---------------------------------------------------------------------------


def test_voice_turn_to_dict_shape():
    t = VoiceTurn(id="abc", transcript="hi", response="hello",
                   state=VoiceState.IDLE)
    d = t.to_dict()
    assert d["id"] == "abc"
    assert d["transcript"] == "hi"
    assert d["state"] == "idle"
