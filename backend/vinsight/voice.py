"""Voice for the advisor call, on Groq's free tier: Whisper for speech-to-text, Orpheus for
text-to-speech. If Orpheus isn't available (its terms must be accepted once in the Groq console),
the browser's built-in speech synthesis takes over, so the call always works for free.
"""

import groq
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from vinsight.config import settings

router = APIRouter(prefix="/api/voice")

# Whisper invents these on near-silence; a turn that is only one of them is dropped.
_HALLUCINATIONS = {"", "you", "thank you.", "thanks for watching!", "thank you for watching.", "bye.", ".", "uh", "um"}
_tts_unavailable: str | None = None  # remembered after the first refusal, so we don't retry every sentence


def _client() -> groq.AsyncGroq:
    if not settings.groq_api_key:
        raise HTTPException(503, "Voice needs GROQ_API_KEY (free at console.groq.com).")
    return groq.AsyncGroq(api_key=settings.groq_api_key, max_retries=1, timeout=20)


@router.get("/status")
async def status() -> dict:
    return {
        "stt": bool(settings.groq_api_key),
        "tts": "browser" if (_tts_unavailable or not settings.groq_api_key) else "groq",
        "tts_note": _tts_unavailable,
    }


@router.post("/transcribe")
async def transcribe(audio: UploadFile) -> dict:
    data = await audio.read(10_000_000)
    if len(data) < 2_000:  # well under half a second of audio
        return {"text": ""}
    try:
        result = await _client().audio.transcriptions.create(
            file=(audio.filename or "speech.wav", data), model=settings.groq_stt_model, language="en",
            prompt="A buyer talking to a car advisor about budget, models, mileage and years.",
        )
    except groq.APIError as e:
        raise HTTPException(502, f"transcription failed: {e}") from e
    text = (result.text or "").strip()
    return {"text": "" if text.lower() in _HALLUCINATIONS else text}


class SpeakRequest(BaseModel):
    text: str


@router.post("/speak")
async def speak(req: SpeakRequest) -> Response:
    global _tts_unavailable
    text = req.text.strip()[:600]
    if _tts_unavailable or not text:
        raise HTTPException(409, _tts_unavailable or "nothing to say")
    try:
        audio = await _client().audio.speech.create(
            model=settings.groq_tts_model, voice=settings.groq_tts_voice, input=text, response_format="wav"
        )
    except groq.BadRequestError as e:
        if "terms" in str(e).lower():
            _tts_unavailable = "Accept the Orpheus terms in the Groq console for a natural voice; using the browser voice."
        raise HTTPException(409, _tts_unavailable or f"speech failed: {e.message}") from e
    except groq.APIError as e:
        raise HTTPException(502, f"speech failed: {e}") from e
    return Response(content=await audio.aread() if hasattr(audio, "aread") else audio.content, media_type="audio/wav")
