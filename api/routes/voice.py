"""Voice mode over HTTP: pick a voice, hear a reply.

The synthesis engine already existed in :mod:`kairos.voice` and nothing called
it. This router is the wiring:

* ``GET  /api/voice/voices``  — the voices the user can pick from
* ``POST /api/voice/speak``   — text in, audio bytes out
* ``POST /api/voice/distill`` — text in, the *spoken* form out (the browser's
  own speech engine needs the short version too, and it must be the same one
  the server would speak)
* ``GET  /api/voice/status``  — which engine is live, and why it might not be

Design notes:

* Edge's endpoint is the online engine, so the route is ``async`` and hands the
  blocking call to a thread — a frozen desktop app that stops answering
  ``/api/health`` while it speaks is worse than one that cannot speak.
* The engine degrades instead of failing: without ``edge-tts`` or without a
  network, the route still answers, says which engine produced the audio, and
  the interface falls back to the browser's own voices.
* Nothing here logs the text. Lengths only — the transcript is where secrets
  collect.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from kairos.voice import EdgeTTSProvider, MockTTSProvider, VoiceError
from kairos.voice_text import DEFAULT_MAX_CHARS, distill_for_speech

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

#: Voice list cache: the endpoint is a network call and the list changes about
#: never. Six hours is short enough to pick up new voices, long enough that
#: opening the settings drawer twice is one call.
_VOICES_TTL_S = 6 * 3600
_voices_cache: Dict[str, Any] = {"at": 0.0, "voices": [], "source": ""}
_voices_lock = asyncio.Lock()

#: A usable set of Edge voices, shipped with the code so the picker is never
#: empty: offline, behind a proxy, or with the service having a bad day, the
#: user still gets a real choice instead of an empty dropdown.
BUNDLED_VOICES: List[Dict[str, str]] = [
    {"name": "zh-CN-XiaoxiaoNeural", "locale": "zh-CN", "gender": "Female", "friendly": "晓晓 · 温柔女声"},
    {"name": "zh-CN-XiaoyiNeural", "locale": "zh-CN", "gender": "Female", "friendly": "晓伊 · 活泼女声"},
    {"name": "zh-CN-YunxiNeural", "locale": "zh-CN", "gender": "Male", "friendly": "云希 · 少年男声"},
    {"name": "zh-CN-YunyangNeural", "locale": "zh-CN", "gender": "Male", "friendly": "云扬 · 播音男声"},
    {"name": "zh-CN-YunjianNeural", "locale": "zh-CN", "gender": "Male", "friendly": "云健 · 沉稳男声"},
    {"name": "zh-CN-liaoning-XiaobeiNeural", "locale": "zh-CN", "gender": "Female", "friendly": "晓北 · 东北女声"},
    {"name": "zh-TW-HsiaoChenNeural", "locale": "zh-TW", "gender": "Female", "friendly": "曉臻 · 台湾女声"},
    {"name": "zh-HK-HiuMaanNeural", "locale": "zh-HK", "gender": "Female", "friendly": "曉曼 · 粤语女声"},
    {"name": "en-US-AriaNeural", "locale": "en-US", "gender": "Female", "friendly": "Aria · warm"},
    {"name": "en-US-JennyNeural", "locale": "en-US", "gender": "Female", "friendly": "Jenny · friendly"},
    {"name": "en-US-GuyNeural", "locale": "en-US", "gender": "Male", "friendly": "Guy · newscast"},
    {"name": "en-US-DavisNeural", "locale": "en-US", "gender": "Male", "friendly": "Davis · calm"},
    {"name": "en-GB-SoniaNeural", "locale": "en-GB", "gender": "Female", "friendly": "Sonia · British"},
    {"name": "en-GB-RyanNeural", "locale": "en-GB", "gender": "Male", "friendly": "Ryan · British"},
    {"name": "ja-JP-NanamiNeural", "locale": "ja-JP", "gender": "Female", "friendly": "ナナミ"},
    {"name": "ja-JP-KeitaNeural", "locale": "ja-JP", "gender": "Male", "friendly": "ケイタ"},
    {"name": "ko-KR-SunHiNeural", "locale": "ko-KR", "gender": "Female", "friendly": "선히"},
    {"name": "ko-KR-InJoonNeural", "locale": "ko-KR", "gender": "Male", "friendly": "인준"},
    {"name": "de-DE-KatjaNeural", "locale": "de-DE", "gender": "Female", "friendly": "Katja"},
    {"name": "de-DE-ConradNeural", "locale": "de-DE", "gender": "Male", "friendly": "Conrad"},
    {"name": "fr-FR-DeniseNeural", "locale": "fr-FR", "gender": "Female", "friendly": "Denise"},
    {"name": "fr-FR-HenriNeural", "locale": "fr-FR", "gender": "Male", "friendly": "Henri"},
    {"name": "es-ES-ElviraNeural", "locale": "es-ES", "gender": "Female", "friendly": "Elvira"},
    {"name": "es-MX-DaliaNeural", "locale": "es-MX", "gender": "Female", "friendly": "Dalia"},
    {"name": "ru-RU-SvetlanaNeural", "locale": "ru-RU", "gender": "Female", "friendly": "Светлана"},
    {"name": "pt-BR-FranciscaNeural", "locale": "pt-BR", "gender": "Female", "friendly": "Francisca"},
    {"name": "it-IT-ElsaNeural", "locale": "it-IT", "gender": "Female", "friendly": "Elsa"},
    {"name": "ar-EG-SalmaNeural", "locale": "ar-EG", "gender": "Female", "friendly": "سلمى"},
    {"name": "hi-IN-SwaraNeural", "locale": "hi-IN", "gender": "Female", "friendly": "स्वरा"},
    {"name": "th-TH-PremwadeeNeural", "locale": "th-TH", "gender": "Female", "friendly": "เปรมวดี"},
    {"name": "vi-VN-HoaiMyNeural", "locale": "vi-VN", "gender": "Female", "friendly": "Hoài My"},
    {"name": "id-ID-GadisNeural", "locale": "id-ID", "gender": "Female", "friendly": "Gadis"},
]


class SpeakRequest(BaseModel):
    text: str = Field(..., max_length=20000)
    voice: str = Field("", max_length=120)
    #: Percent, matching how the UI labels the sliders. 0 is the engine default.
    rate: int = Field(0, ge=-90, le=200)
    volume: int = Field(0, ge=-100, le=100)
    #: Hertz offset; Edge uses Hz for pitch, so the UI says Hz too.
    pitch: int = Field(0, ge=-100, le=100)
    max_chars: int = Field(DEFAULT_MAX_CHARS, ge=0, le=5000)


class DistillRequest(BaseModel):
    text: str = Field(..., max_length=20000)
    max_chars: int = Field(DEFAULT_MAX_CHARS, ge=0, le=5000)


def engine_name() -> str:
    """Which engine this process will use.

    ``KAIROS_TTS_ENGINE=mock`` forces the offline placeholder (tests, demos,
    a locked-down network); otherwise Edge when its package is importable.
    """
    forced = (os.environ.get("KAIROS_TTS_ENGINE") or "").strip().lower()
    if forced in ("mock", "edge"):
        return forced
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return "mock"
    return "edge"


def _make_provider(engine: str, voice: str, rate: int, volume: int, pitch: int):
    if engine == "edge":
        return EdgeTTSProvider(
            voice=voice or EdgeTTSProvider.DEFAULT_VOICE,
            rate=f"{rate:+d}%",
            volume=f"{volume:+d}%",
            pitch=f"{pitch:+d}Hz",
        )
    return MockTTSProvider()


def _media_type(engine: str) -> str:
    return "audio/mpeg" if engine == "edge" else "audio/wav"


async def _fetch_voices() -> tuple[List[Dict[str, str]], str]:
    """Live voice list, or the bundled set. Never raises, never empty."""
    try:
        live = await EdgeTTSProvider.list_voices_async()
    except Exception as exc:  # noqa: BLE001 - offline is an expected state
        logger.info("voice list: live lookup unavailable (%s)", type(exc).__name__)
        return list(BUNDLED_VOICES), "bundled"
    cleaned = [
        {
            "name": str(v.get("name") or ""),
            "locale": str(v.get("locale") or ""),
            "gender": str(v.get("gender") or ""),
            "friendly": str(v.get("friendly") or v.get("name") or ""),
        }
        for v in live
        if v.get("name")
    ]
    if not cleaned:
        return list(BUNDLED_VOICES), "bundled"
    return cleaned, "live"


@router.get("/status")
async def voice_status() -> Dict[str, Any]:
    """What the interface needs to decide whether to speak at all."""
    engine = engine_name()
    cached = bool(_voices_cache["voices"])
    return {
        "engine": engine,
        "available": engine == "edge",
        "online": engine == "edge",
        "voice_count": len(_voices_cache["voices"]) or len(BUNDLED_VOICES),
        "voices_source": _voices_cache["source"] or "bundled",
        "cached": cached,
        "detail": (
            "Microsoft Edge neural voices"
            if engine == "edge"
            else "edge-tts is not installed; falling back to the browser's own voices"
        ),
    }


@router.get("/voices")
async def list_voices(locale: Optional[str] = None,
                      refresh: bool = False) -> Dict[str, Any]:
    """Voices to choose from. Falls back to the bundled set, never empty."""
    engine = engine_name()
    now = time.time()
    stale = (now - float(_voices_cache["at"] or 0.0)) > _VOICES_TTL_S
    if engine == "edge" and (refresh or stale or not _voices_cache["voices"]):
        async with _voices_lock:
            if refresh or stale or not _voices_cache["voices"]:
                voices_cache, source = await _fetch_voices()
                _voices_cache.update({
                    "at": time.time(),
                    "voices": voices_cache,
                    "source": source,
                })
    voices = _voices_cache["voices"] or list(BUNDLED_VOICES)
    if locale:
        prefix = locale.lower()
        voices = [v for v in voices if (v.get("locale") or "").lower().startswith(prefix)]
    return {
        "engine": engine,
        "source": _voices_cache["source"] or "bundled",
        "count": len(voices),
        "voices": voices,
    }


@router.post("/distill")
async def distill(request: DistillRequest) -> Dict[str, Any]:
    """The spoken form of a reply, for clients that speak it themselves."""
    spoken = distill_for_speech(request.text, max_chars=request.max_chars)
    return {
        "spoken": spoken,
        "original_chars": len(request.text or ""),
        "spoken_chars": len(spoken),
    }


@router.post("/speak")
async def speak(request: SpeakRequest) -> Response:
    """Synthesize the spoken form of ``text`` and return audio bytes."""
    if not (request.text or "").strip():
        raise HTTPException(status_code=400, detail="text is required")
    spoken = distill_for_speech(request.text, max_chars=request.max_chars)
    engine = engine_name()
    if not spoken:
        # Nothing sayable (a reply that was all code, or all table). 204 tells
        # the caller "this is fine, there is just nothing to play".
        logger.info("speak: nothing speakable in %d chars", len(request.text or ""))
        return Response(status_code=204, headers={"X-Voice-Engine": engine,
                                                  "X-Spoken-Chars": "0"})

    provider = _make_provider(engine, request.voice, request.rate,
                              request.volume, request.pitch)
    try:
        # Edge synthesis is a blocking network call; keep the event loop free
        # so /api/health keeps answering while the app talks.
        audio = await asyncio.to_thread(provider.synthesize, spoken)
    except VoiceError as exc:
        logger.warning("speak failed (%s): %s", engine, exc)
        raise HTTPException(status_code=502,
                            detail=f"Speech synthesis failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("speak crashed")
        raise HTTPException(status_code=502,
                            detail=f"Speech synthesis failed: {type(exc).__name__}") from exc

    return Response(
        content=audio,
        media_type=_media_type(engine),
        headers={
            "X-Voice-Engine": engine,
            "X-Voice-Name": provider.name,
            "X-Spoken-Chars": str(len(spoken)),
            "X-Original-Chars": str(len(request.text or "")),
            "Cache-Control": "no-store",
        },
    )
