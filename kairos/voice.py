"""Voice mode — STT (speech-to-text) and TTS (text-to-speech) integration.

Lets a user speak to the agent and have it speak back. The design is
provider-pluggable: the actual STT/TTS engines (whisper.cpp, piper,
ElevenLabs, OpenAI TTS, Azure Speech, etc.) are user-supplied. This
module defines the abstract interfaces, a couple of safe placeholder
implementations for tests and offline mode, and a `VoiceSession`
orchestrator that ties everything together.

Design:

* `STTProvider.transcribe(audio_bytes) -> str` is the only contract.
  The default `MockSTTProvider` echoes the audio's SHA-256 prefix as
  a placeholder so we can exercise the flow without a real model.
* `TTSProvider.synthesize(text) -> bytes` returns raw audio bytes
  (WAV, MP3, whatever the engine produces). The default
  `MockTTSProvider` returns a tiny silent WAV so file outputs are
  valid and tests can `len(audio) > 0`.
* `VoiceSession` wraps both providers and adds a small
  push-to-talk state machine (idle → recording → transcribing →
  thinking → speaking → idle). The session is the unit the API
  routes are bound to.
* No new pip dependencies — providers are abstract; concrete engines
  are user responsibility. The mock providers use stdlib only.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import struct
import time
import uuid
import wave
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class VoiceError(RuntimeError):
    """Raised when a voice operation fails (provider error, bad audio, etc)."""


class VoiceState(str, Enum):
    """State of a VoiceSession.

    IDLE         — ready for input
    RECORDING    — user is recording (push-to-talk active)
    TRANSCRIBING — STT is processing the captured audio
    THINKING     — LLM is generating a response
    SPEAKING     — TTS is playing the response
    ERROR        — last operation failed; see `last_error`
    """
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


@dataclass
class VoiceTurn:
    """One user-assistant voice exchange.

    Attributes:
        id: short identifier
        user_audio_path: path to the captured user audio (or None)
        transcript: STT output (the user's text)
        response: LLM response text
        response_audio_path: path to the synthesized response audio
        started_at / finished_at: unix timestamps
        state: final VoiceState
    """
    id: str
    transcript: str = ""
    response: str = ""
    state: VoiceState = VoiceState.IDLE
    user_audio_path: Optional[str] = None
    response_audio_path: Optional[str] = None
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        return d


@runtime_checkable
class STTProvider(Protocol):
    """Speech-to-text provider interface."""

    name: str

    def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav",
                   language: str = "en") -> str: ...


@runtime_checkable
class TTSProvider(Protocol):
    """Text-to-speech provider interface."""

    name: str

    def synthesize(self, text: str, *, voice: str = "default",
                   mime_type: str = "audio/wav") -> bytes: ...


# ---------------------------------------------------------------------------
# Mock providers (default; safe for tests + offline mode)
# ---------------------------------------------------------------------------


class MockSTTProvider:
    """Deterministic placeholder STT.

    Returns a short "transcript" derived from the audio's SHA-256 so
    tests can assert on it without depending on a real engine. The
    transcript is *clearly* synthetic ("[mock-stt sha256:abcdef…]") so
    nothing in production mistakes it for a real STT output.
    """

    name = "mock-stt"

    def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav",
                   language: str = "en") -> str:
        if not audio:
            raise VoiceError("empty audio")
        digest = hashlib.sha256(audio).hexdigest()[:8]
        return f"[mock-stt sha256:{digest} bytes={len(audio)}]"


class MockTTSProvider:
    """Placeholder TTS that returns a minimal silent WAV.

    The WAV is a real, playable 0.1-second 8kHz mono silence file
    (about 1.6 KB) so the audio path can be saved to disk and the
    UI can render `<audio src="...">` without the engine actually
    being installed.
    """

    name = "mock-tts"

    # WAV header for: 8kHz, 16-bit, mono, 0.1 sec silence.
    _WAV_HEADER = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + 1600,   # file size - 8
        b"WAVE",
        b"fmt ",
        16,          # fmt chunk size
        1,           # PCM
        1,           # mono
        8000,        # sample rate
        16000,       # byte rate
        2,           # block align
        16,          # bits per sample
        b"data",
        1600,        # data chunk size (0.1s * 8000 * 2)
    )

    def synthesize(self, text: str, *, voice: str = "default",
                   mime_type: str = "audio/wav") -> bytes:
        if not text:
            raise VoiceError("empty text")
        # 0.1 sec of silence; the real engine would emit the actual
        # speech. We always include the header + 1600 bytes of zeros
        # so the file is a valid playable WAV.
        silence_block = b"\x00" * 1600
        return self._WAV_HEADER + silence_block


# ---------------------------------------------------------------------------
# Real-provider fallbacks (whisper + pyttsx3) — only used if installed
# ---------------------------------------------------------------------------


class WhisperSTTProvider:
    """Whisper-backed STT. Lazy-imports faster_whisper (the lighter
    CTranslate2 build) so we don't force the dependency on projects
    that don't need voice. Falls back to openai-whisper if the
    faster-whisper package isn't installed.

    Usage:
        stt = WhisperSTTProvider(model="base", device="cpu")
    """

    def __init__(self, model: str = "base", device: str = "cpu",
                 language: str = "en"):
        self.model_name = model
        self.device = device
        self.language = language
        self._model = None
        self.name = f"whisper-{model}"

    def _load(self):
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel  # type: ignore
            self._model = WhisperModel(self.model_name, device=self.device)
            self._impl = "faster_whisper"
        except ImportError:
            try:
                import whisper  # type: ignore
                self._model = whisper.load_model(self.model_name,
                                                  device=self.device)
                self._impl = "openai_whisper"
            except ImportError as e:
                raise VoiceError(
                    "neither faster_whisper nor openai-whisper is installed"
                ) from e

    def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav",
                   language: str = "en") -> str:
        self._load()
        # Write to a temp file because both backends want a path.
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio)
            tmp = f.name
        try:
            if self._impl == "faster_whisper":
                segments, _ = self._model.transcribe(tmp, language=language)
                return " ".join(seg.text for seg in segments).strip()
            else:
                result = self._model.transcribe(tmp, language=language)
                return str(result.get("text", "")).strip()
        finally:
            try:
                Path(tmp).unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Real TTS: edge-tts (Microsoft Edge's online TTS, free, no key needed)
# ---------------------------------------------------------------------------


class EdgeTTSProvider:
    """Real TTS backed by Microsoft Edge's read-aloud endpoint.

    Wraps the official ``edge-tts`` package (already in the project's
    runtime dependencies). Synthesizes text to MP3 in-memory and
    returns raw bytes. Voice selection uses the standard Edge short
    names like ``en-US-AriaNeural`` or ``zh-CN-XiaoxiaoNeural``.

    This is the **online** path — the project gets real, high-quality
    speech for free. For fully-offline use, swap in a local engine
    (Piper, pyttsx3, Coqui) and pass it to :class:`VoiceSession`.

    Usage::

        tts = EdgeTTSProvider(voice="zh-CN-XiaoxiaoNeural")
        mp3_bytes = tts.synthesize("你好世界")
    """

    DEFAULT_VOICE = "en-US-AriaNeural"
    SUPPORTED_VOICES_HINT = (
        "Common: en-US-AriaNeural, en-US-GuyNeural, "
        "zh-CN-XiaoxiaoNeural, zh-CN-YunxiNeural, "
        "ja-JP-NanamiNeural, de-DE-KatjaNeural"
    )

    def __init__(self, voice: str = DEFAULT_VOICE,
                 rate: str = "+0%",
                 volume: str = "+0%",
                 pitch: str = "+0Hz"):
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.pitch = pitch
        self.name = f"edge-tts:{voice}"

    def synthesize(self, text: str, *, voice: str = "default",
                   mime_type: str = "audio/mpeg") -> bytes:
        if not text or not text.strip():
            raise VoiceError("empty text")
        try:
            import edge_tts  # type: ignore
        except ImportError as exc:  # pragma: no cover - dep missing
            raise VoiceError(
                "edge-tts is not installed; pip install edge-tts"
            ) from exc

        chosen_voice = self.voice if voice in (None, "", "default") else voice

        async def _run() -> bytes:
            comm = edge_tts.Communicate(
                text,
                voice=chosen_voice,
                rate=self.rate,
                volume=self.volume,
                pitch=self.pitch,
            )
            buf = bytearray()
            try:
                async for chunk in comm.stream():
                    # Edge also emits metadata events (WordBoundary etc).
                    # They are dicts without "data"; only audio chunks carry bytes.
                    if not isinstance(chunk, dict):
                        continue
                    if chunk.get("type") == "audio":
                        data = chunk.get("data")
                        if data:
                            buf.extend(data)
            except Exception as exc:
                raise VoiceError(f"edge-tts stream failed: {exc}") from exc
            return bytes(buf)

        try:
            # Re-use a running loop if one exists, else spin one up.
            asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(lambda: asyncio.run(_run())).result()
        except RuntimeError:
            # No running loop — safe to call asyncio.run directly.
            return asyncio.run(_run())

    @staticmethod
    async def list_voices_async(locale_filter: str = "") -> list:
        """Return the list of available Edge voices.

        Hits the public voices.list endpoint, caches nothing. Pass
        ``locale_filter="zh-"`` to keep only Chinese voices, etc.
        """
        try:
            import edge_tts  # type: ignore
        except ImportError as exc:  # pragma: no cover - dep missing
            raise VoiceError(
                "edge-tts is not installed; pip install edge-tts"
            ) from exc
        raw = await edge_tts.list_voices()
        if locale_filter:
            raw = [v for v in raw if v.get("Locale", "").startswith(locale_filter)]
        return [
            {
                "name": v.get("ShortName"),
                "gender": v.get("Gender"),
                "locale": v.get("Locale"),
                "friendly": v.get("FriendlyName"),
            }
            for v in raw
        ]


# ---------------------------------------------------------------------------
# VoiceSession — orchestrator
# ---------------------------------------------------------------------------


@dataclass
class VoiceSessionConfig:
    """Configuration for a VoiceSession."""
    audio_dir: str = "data/voice"
    stt_language: str = "en"
    tts_voice: str = "default"
    auto_play: bool = False  # whether to auto-play synthesized audio


class VoiceSession:
    """A single voice-mode session.

    Lifecycle:
        sess = VoiceSession(stt=..., tts=..., config=...)
        turn = await sess.run_turn(audio_bytes)
        # turn.transcript  — what the user said
        # turn.response     — what the agent said back
        # turn.response_audio_path — where the WAV was saved
    """

    def __init__(self,
                 stt: STTProvider,
                 tts: TTSProvider,
                 config: Optional[VoiceSessionConfig] = None,
                 responder: Optional[Any] = None):
        self.stt = stt
        self.tts = tts
        self.config = config or VoiceSessionConfig()
        self.responder = responder  # callable(text) -> str; defaults to echo
        self._turns: List[VoiceTurn] = []
        self._state: VoiceState = VoiceState.IDLE
        self._last_error: str = ""

    @property
    def state(self) -> VoiceState:
        return self._state

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def turns(self) -> List[VoiceTurn]:
        return list(self._turns)

    def _set_state(self, new_state: VoiceState, error: str = "") -> None:
        self._state = new_state
        if error:
            self._last_error = error

    def _save_audio(self, audio: bytes, suffix: str) -> Path:
        out_dir = Path(self.config.audio_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{uuid.uuid4().hex[:8]}.{suffix}"
        path.write_bytes(audio)
        return path

    def _default_responder(self, text: str) -> str:
        return f"[echo] {text}"

    async def run_turn(self, audio: bytes,
                       save_user_audio: bool = True) -> VoiceTurn:
        """Run one full voice turn: transcribe → respond → synthesize.

        Synchronous underlying providers are wrapped so callers can
        `await` consistently even if the engines are sync (e.g.
        MockSTTProvider). If a real engine is async, the caller
        should wrap it; this method does not assume async engines.
        """
        turn = VoiceTurn(id=uuid.uuid4().hex[:8],
                          started_at=time.time())
        user_path: Optional[Path] = None
        if save_user_audio:
            user_path = self._save_audio(audio, "wav")
            turn.user_audio_path = str(user_path)
        # 1) STT
        self._set_state(VoiceState.TRANSCRIBING)
        try:
            transcript = self.stt.transcribe(
                audio, language=self.config.stt_language)
        except Exception as e:  # noqa: BLE001
            self._set_state(VoiceState.ERROR, str(e))
            turn.error = f"stt: {e}"
            turn.finished_at = time.time()
            turn.state = VoiceState.ERROR
            self._turns.append(turn)
            raise
        turn.transcript = transcript
        # 2) LLM (responder)
        self._set_state(VoiceState.THINKING)
        try:
            responder = self.responder or self._default_responder
            response = responder(transcript)
        except Exception as e:  # noqa: BLE001
            self._set_state(VoiceState.ERROR, str(e))
            turn.error = f"llm: {e}"
            turn.finished_at = time.time()
            turn.state = VoiceState.ERROR
            self._turns.append(turn)
            raise
        turn.response = response
        # 3) TTS
        self._set_state(VoiceState.SPEAKING)
        try:
            audio_out = self.tts.synthesize(
                response, voice=self.config.tts_voice)
        except Exception as e:  # noqa: BLE001
            self._set_state(VoiceState.ERROR, str(e))
            turn.error = f"tts: {e}"
            turn.finished_at = time.time()
            turn.state = VoiceState.ERROR
            self._turns.append(turn)
            raise
        out_path = self._save_audio(audio_out, "wav")
        turn.response_audio_path = str(out_path)
        turn.finished_at = time.time()
        turn.state = VoiceState.IDLE
        self._set_state(VoiceState.IDLE)
        self._turns.append(turn)
        return turn

    def synthesize_only(self, text: str) -> Path:
        """Skip the STT step — just synthesize text to audio. Useful
        when the user types a response instead of speaking it."""
        if not text:
            raise VoiceError("text is required")
        audio = self.tts.synthesize(text, voice=self.config.tts_voice)
        return self._save_audio(audio, "wav")

    def to_dict(self) -> dict:
        return {
            "state": self._state.value,
            "last_error": self._last_error,
            "stt": getattr(self.stt, "name", "unknown"),
            "tts": getattr(self.tts, "name", "unknown"),
            "turn_count": len(self._turns),
            "turns": [t.to_dict() for t in self._turns],
        }
