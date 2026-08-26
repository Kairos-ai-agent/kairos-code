"""Tests for the offline TTS providers (pyttsx3, espeak)."""
from __future__ import annotations

import os
import shutil
import struct
import sys
from unittest.mock import MagicMock, patch

import pytest

from kairos.voice_offline import (
    EspeakTTSProvider,
    Pyttsx3TTSProvider,
    TTSProviderError,
    available_providers,
    make_offline_provider,
)


# ---------------------------------------------------------------------------
# pyttsx3
# ---------------------------------------------------------------------------


def test_pyttsx3_missing_dependency_raises_clean_error():
    """If pyttsx3 is not installed, the constructor raises
    TTSProviderError with a clear install hint."""
    with patch.dict(sys.modules, {"pyttsx3": None}):
        with pytest.raises(TTSProviderError) as ei:
            Pyttsx3TTSProvider()
    assert "pyttsx3" in str(ei.value)


def test_pyttsx3_init_failure_raises_clean_error():
    """If pyttsx3 init() raises (no speech engine on the host),
    the constructor surfaces a TTSProviderError."""
    fake_module = MagicMock()
    fake_module.init.side_effect = RuntimeError("no engine")
    with patch.dict(sys.modules, {"pyttsx3": fake_module}):
        with pytest.raises(TTSProviderError) as ei:
            Pyttsx3TTSProvider()
    assert "no engine" in str(ei.value).lower() or "speech engine" in str(ei.value).lower()


def test_pyttsx3_synthesize_with_mocked_engine():
    """Drive pyttsx3 via a fully mocked engine; assert it routes
    the call to save_to_file + runAndWait and reads the file back."""
    fake_engine = MagicMock()
    fake_engine.getProperty.return_value = []  # no voices
    fake_module = MagicMock()
    fake_module.init.return_value = fake_engine

    # write a tiny valid WAV to the temp path the engine "saved"
    def _save_and_emit(text, path):
        # minimal 100-byte WAV: 44 header + 56 zero data
        import wave
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\x00" * 100)

    fake_engine.save_to_file.side_effect = _save_and_emit

    with patch.dict(sys.modules, {"pyttsx3": fake_module}):
        p = Pyttsx3TTSProvider()
        out = p.synthesize("hello world")
    # Output is a valid WAV
    assert out[:4] == b"RIFF"
    assert b"WAVE" in out[:12]


def test_pyttsx3_empty_text_raises():
    fake_module = MagicMock()
    fake_module.init.return_value = MagicMock()
    with patch.dict(sys.modules, {"pyttsx3": fake_module}):
        p = Pyttsx3TTSProvider()
    with pytest.raises(TTSProviderError):
        p.synthesize("")
    with pytest.raises(TTSProviderError):
        p.synthesize("   \n  ")


# ---------------------------------------------------------------------------
# espeak
# ---------------------------------------------------------------------------


def test_espeak_missing_binary_raises():
    with patch("shutil.which", return_value=None):
        with pytest.raises(TTSProviderError) as ei:
            EspeakTTSProvider()
    assert "espeak" in str(ei.value)


def test_espeak_synthesize_invokes_binary(tmp_path: Path):
    """Drive espeak via a fake subprocess; assert the right args
    and that the function returns the WAV that was written."""
    from pathlib import Path as _P
    fake_binary = "/usr/bin/espeak"
    with patch("shutil.which", return_value=fake_binary):
        p = EspeakTTSProvider(voice="en", pitch=50, speed=175)
    fake_binary = "/usr/bin/espeak"
    with patch("shutil.which", return_value=fake_binary):
        p = EspeakTTSProvider(voice="en", pitch=50, speed=175)

    wav_bytes = b"RIFF" + b"\x00" * 100 + b"WAVE"

    # Replace subprocess.run with a fake that writes wav_bytes
    # to the path passed via -w, then returns success.
    def fake_run(cmd, **kwargs):
        out_path = None
        for i, arg in enumerate(cmd):
            if arg == "-w":
                out_path = cmd[i + 1]
                break
        if out_path:
            _P(out_path).write_bytes(wav_bytes)
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        return proc

    with patch("subprocess.run", side_effect=fake_run) as mock_run:
        out = p.synthesize("hello world")

    assert out == wav_bytes
    args = mock_run.call_args[0][0]
    assert args[0] == fake_binary
    assert "-v" in args and "en" in args
    assert "-w" in args
    assert "hello world" in args


def test_espeak_synthesize_returns_error_on_nonzero_exit():
    fake_binary = "/usr/bin/espeak"
    with patch("shutil.which", return_value=fake_binary):
        p = EspeakTTSProvider()
    fake_proc = MagicMock()
    fake_proc.returncode = 1
    fake_proc.stderr = "espeak: error message"
    with patch("subprocess.run", return_value=fake_proc):
        with pytest.raises(TTSProviderError) as ei:
            p.synthesize("hi")
    assert "exit 1" in str(ei.value)


# ---------------------------------------------------------------------------
# Discovery + factory
# ---------------------------------------------------------------------------


def test_available_providers_returns_empty_when_nothing_installed():
    with patch.dict(sys.modules, {"pyttsx3": None}), \
         patch("shutil.which", return_value=None):
        assert available_providers() == []


def test_available_providers_with_espeak():
    with patch.dict(sys.modules, {"pyttsx3": None}), \
         patch("shutil.which", return_value="/usr/bin/espeak"):
        assert available_providers() == ["espeak"]


def test_available_providers_with_pyttsx3():
    fake = MagicMock()
    fake.init.return_value = MagicMock()
    with patch.dict(sys.modules, {"pyttsx3": fake}), \
         patch("shutil.which", return_value=None):
        assert available_providers() == ["pyttsx3"]


def test_make_offline_provider_auto_prefers_pyttsx3():
    fake_engine = MagicMock()
    fake_module = MagicMock()
    fake_module.init.return_value = fake_engine
    with patch.dict(sys.modules, {"pyttsx3": fake_module}), \
         patch("shutil.which", return_value=None):
        p = make_offline_provider()
    assert isinstance(p, Pyttsx3TTSProvider)


def test_make_offline_provider_falls_back_to_espeak():
    fake_module = MagicMock()
    fake_module.init.side_effect = RuntimeError("no engine")
    with patch.dict(sys.modules, {"pyttsx3": fake_module}), \
         patch("shutil.which", return_value="/usr/bin/espeak"):
        p = make_offline_provider()
    assert isinstance(p, EspeakTTSProvider)


def test_make_offline_provider_raises_when_nothing_available():
    with patch.dict(sys.modules, {"pyttsx3": None}), \
         patch("shutil.which", return_value=None):
        with pytest.raises(TTSProviderError):
            make_offline_provider()


def test_make_offline_provider_explicit_espeak():
    with patch("shutil.which", return_value="/usr/bin/espeak"):
        p = make_offline_provider("espeak")
    assert isinstance(p, EspeakTTSProvider)


def test_make_offline_provider_unknown_raises():
    with pytest.raises(TTSProviderError):
        make_offline_provider("nonexistent")


# ---------------------------------------------------------------------------
# Sanity: the pyttsx3 module exposes the same synthesize() interface
# ---------------------------------------------------------------------------


def test_pyttsx3_name_attribute():
    assert Pyttsx3TTSProvider.name == "pyttsx3"


def test_espeak_name_attribute():
    assert EspeakTTSProvider.name == "espeak"
