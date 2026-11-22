"""Voice for the advisor call, on Groq's free tier: Whisper for speech-to-text, Orpheus for
text-to-speech. If Orpheus isn't available (terms not accepted, or the free-tier limit of about 10
requests a minute), the browser runs the open Kokoro voice instead, so the call always works for free.
"""

import time

import groq
from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from vinsight.config import settings
from vinsight.keys import Keys

router = APIRouter(prefix="/api/voice")

# Whisper invents these on near-silence; a turn that is only one of them is dropped.
_HALLUCINATIONS = {"", "you", "thank you.", "thanks for watching!", "thank you for watching.", "bye.", ".", "uh", "um"}
_tts_unavailable: str | None = None  # remembered after a refusal, so we don't retry every sentence...
_tts_refused_at = 0.0
_TTS_RETRY_S = 120  # ...but retried every couple of minutes, e.g. after the terms are accepted


def _tts_blocked() -> str | None:
    global _tts_unavailable
    if _tts_unavailable and time.monotonic() - _tts_refused_at > _TTS_RETRY_S:
        _tts_unavailable = None
    return _tts_unavailable


def _client(request: Request) -> groq.AsyncGroq:
    key = Keys.from_request(request).groq
    if not key:
        raise HTTPException(503, "Voice needs a Groq API key (free at console.groq.com). Add one in Settings.")
    return groq.AsyncGroq(api_key=key, max_retries=1, timeout=20)


@router.get("/status")
async def status(request: Request) -> dict:
    has_key = bool(Keys.from_request(request).groq)
    return {
        "stt": has_key,
        "tts": "fallback" if (_tts_blocked() or not has_key) else "groq",
        "tts_note": _tts_blocked(),
    }


@router.post("/transcribe")
async def transcribe(audio: UploadFile, request: Request) -> dict:
    data = await audio.read(10_000_000)
    if len(data) < 2_000:  # well under half a second of audio
        return {"text": ""}
    try:
        result = await _client(request).audio.transcriptions.create(
            file=(audio.filename or "speech.wav", data), model=settings.groq_stt_model, language="en",
            prompt="A buyer talking to a car advisor about budget, models, mileage and years.",
        )
    except groq.APIError as e:
        raise HTTPException(502, f"transcription failed: {e}") from e
    text = (result.text or "").strip()
    return {"text": "" if text.lower() in _HALLUCINATIONS else text}


def fix_wav(data: bytes) -> bytes:
    """Groq streams WAV with placeholder sizes (0xFFFFFFFF); Web Audio's decoder can reject that.
    Rewrite the RIFF and data chunk sizes to the real ones."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    out = bytearray(data)
    out[4:8] = (len(data) - 8).to_bytes(4, "little")
    pos = 12
    while pos + 8 <= len(out):
        chunk, size = bytes(out[pos:pos + 4]), int.from_bytes(out[pos + 4:pos + 8], "little")
        if chunk == b"data":
            out[pos + 4:pos + 8] = (len(out) - pos - 8).to_bytes(4, "little")
            break
        pos += 8 + size + (size & 1)
    return bytes(out)


class SpeakRequest(BaseModel):
    text: str


@router.post("/speak")
async def speak(req: SpeakRequest, request: Request) -> Response:
    global _tts_unavailable, _tts_refused_at
    text = req.text.strip()[:600]
    if _tts_blocked() or not text:
        raise HTTPException(409, _tts_unavailable or "nothing to say")
    try:
        audio = await _client(request).audio.speech.create(
            model=settings.groq_tts_model, voice=settings.groq_tts_voice, input=text, response_format="wav"
        )
    except groq.BadRequestError as e:
        if "terms" in str(e).lower():
            _tts_unavailable = "Accept the Orpheus terms in the Groq console to use the Groq voice; using the in-browser voice."
            _tts_refused_at = time.monotonic()
        raise HTTPException(409, _tts_unavailable or f"speech failed: {e.message}") from e
    except groq.APIError as e:
        raise HTTPException(502, f"speech failed: {e}") from e
    return Response(content=fix_wav(await audio.read()), media_type="audio/wav")
