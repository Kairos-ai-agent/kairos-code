"""Offline TTS providers.

Companion to :mod:`kairos.voice`, which currently has
``EdgeTTSProvider`` (online, Microsoft read-aloud) and
``MockTTSProvider`` (silent placeholder). When the user is
offline or doesn't want to hit Microsoft, we provide two more
options that are best-effort:

  - :class:`Pyttsx3TTSProvider` — wraps ``pyttsx3`` (a wrapper
    around the OS's native speech engine: SAPI5 on Windows,
    NSSpeechSynthesizer on macOS, eSpeak on Linux). Truly local.

  - :class:`EspeakTTSProvider` — wraps the ``espeak`` CLI
    directly (no Python wrapper needed). Saves WAVs to disk
    so callers can ``Audio(data)`` them. Works on every
    platform with espeak installed.

Both providers degrade gracefully: if the underlying engine
isn't installed, :class:`TTSProviderError` is raised with a
clear "install X to use this provider" message. The
:func:`available_providers` helper returns the list of
offline providers that would work *right now* on this host.
"""
from __future__ import annotations

import logging
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class TTSProviderError(RuntimeError):
    """Raised when the underlying TTS engine isn't available."""


# ---------------------------------------------------------------------------
# pyttsx3 (OS native TTS)
# ---------------------------------------------------------------------------


class Pyttsx3TTSProvider:
    """Offline TTS via the OS's native speech engine (pyttsx3)."""

    name = "pyttsx3"

    def __init__(self, voice: str = "default", rate: int = 200) -> None:
        self.voice = voice
        self.rate = rate
        self._engine = None
        try:
            import pyttsx3  # type: ignore
        except ImportError as exc:
            raise TTSProviderError(
                "pyttsx3 is not installed. `pip install pyttsx3` to use "
                "this offline TTS provider."
            ) from exc
        try:
            self._engine = pyttsx3.init()
        except Exception as exc:  # noqa: BLE001
            raise TTSProviderError(
                f"pyttsx3 failed to initialize (no speech engine?): {exc}"
            ) from exc
        if voice and voice != "default":
            try:
                voices = self._engine.getProperty("voices")
                # Match by name or id substring
                for v in voices or []:
                    if voice.lower() in (v.name or "").lower() or voice == v.id:
                        self._engine.setProperty("voice", v.id)
                        break
            except Exception:  # noqa: BLE001
                logger.debug("pyttsx3 voice selection failed", exc_info=True)
        try:
            self._engine.setProperty("rate", rate)
        except Exception:  # noqa: BLE001
            pass

    def synthesize(self, text: str, *, voice: str = "default",
                   mime_type: str = "audio/wav") -> bytes:
        if not text or not text.strip():
            raise TTSProviderError("empty text")
        if self._engine is None:
            raise TTSProviderError("pyttsx3 not initialized")

        # pyttsx3 has no "synthesize to bytes" API in v2 — we route
        # through a temp WAV file. Some engines write synchronously
        # when runAndWait() returns; others need a busy-wait.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            self._engine.save_to_file(text, tmp_path)
            self._engine.runAndWait()
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# espeak (direct CLI)
# ---------------------------------------------------------------------------


class EspeakTTSProvider:
    """Offline TTS via the ``espeak`` CLI (saves WAV directly)."""

    name = "espeak"

    def __init__(self, voice: str = "en", pitch: int = 50,
                 speed: int = 175) -> None:
        self.voice = voice
        self.pitch = pitch
        self.speed = speed
        self._binary = shutil.which("espeak") or shutil.which("espeak-ng")
        if not self._binary:
            raise TTSProviderError(
                "espeak not found on PATH. `apt install espeak-ng` (Linux), "
                "`brew install espeak` (macOS), or use a different provider."
            )

    def synthesize(self, text: str, *, voice: str = "default",
                   mime_type: str = "audio/wav") -> bytes:
        if not text or not text.strip():
            raise TTSProviderError("empty text")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            cmd = [
                self._binary,
                "-v", voice if voice and voice != "default" else self.voice,
                "-p", str(self.pitch),
                "-s", str(self.speed),
                "-w", tmp_path,
                text,
            ]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )
            if proc.returncode != 0:
                raise TTSProviderError(
                    f"espeak failed (exit {proc.returncode}): {proc.stderr[:200]}"
                )
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def available_providers() -> List[str]:
    """Return the names of offline TTS providers that would work
    on this host right now (no install)."""
    out: List[str] = []
    try:
        import pyttsx3  # type: ignore  # noqa: F401
        # pyttsx3 imports OK, but the engine init might still fail.
        # We do a quick init to be sure.
        try:
            import pyttsx3
            eng = pyttsx3.init()
            eng.stop()
            del eng
            out.append("pyttsx3")
        except Exception:
            pass
    except ImportError:
        pass
    if shutil.which("espeak") or shutil.which("espeak-ng"):
        out.append("espeak")
    return out


# ---------------------------------------------------------------------------
# Factory: pick the best offline provider given user prefs
# ---------------------------------------------------------------------------


def make_offline_provider(preferred: str = "auto"):
    """Construct an offline TTS provider.

    ``preferred`` may be "pyttsx3" / "espeak" / "auto". With
    "auto" the order is: pyttsx3 (better voice quality) →
    espeak → raise TTSProviderError if neither is available.
    """
    if preferred == "auto":
        for cand in ("pyttsx3", "espeak"):
            try:
                return make_offline_provider(cand)
            except TTSProviderError:
                continue
        raise TTSProviderError(
            "no offline TTS provider available; install pyttsx3 or espeak"
        )
    if preferred == "pyttsx3":
        return Pyttsx3TTSProvider()
    if preferred == "espeak":
        return EspeakTTSProvider()
    raise TTSProviderError(f"unknown offline TTS provider: {preferred!r}")
