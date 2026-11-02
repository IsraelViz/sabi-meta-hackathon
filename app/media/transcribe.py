"""Voice notes to text: Muse Spark Open on Baseten, Muse Voice Transcribe (Meta
API) or Whisper on Fireworks.

Baseten's Muse Spark Open takes audio in the chat request itself (an
`audio_url` part; WAV, MP3, FLAC, OGG or M4A, up to 25 MB and 5 minutes), so
a WhatsApp voice note goes up as it is. The demo page's WebM recordings are
converted to OGG with ffmpeg first.

Fireworks takes the voice note as a file upload in the OpenAI transcription
format, so WhatsApp's OGG/Opus goes up as it is, without ffmpeg.

Muse Voice Transcribe (Meta Model API): the voice note becomes a 16 kHz mono
WAV (ffmpeg) and goes to POST /v1/asr/transcribe as multipart `request` (JSON
settings) + `audio`. The answer is JSON with the `transcript`. The shop's
product and customer names go along as `keywords`, so "Indomie" and "Chioma"
come back spelled the way her books spell them.
"""
import asyncio
import base64
import json
import logging
import shutil
import subprocess

from app.config import settings

log = logging.getLogger("sabi.transcribe")
RATE = 16000


def to_wav(audio: bytes) -> bytes:
    """WhatsApp voice notes are OGG/Opus; convert anything ffmpeg reads to 16 kHz mono 16-bit WAV."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not on PATH")
    return subprocess.run([ffmpeg, "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le",
                           "-f", "wav", "pipe:1"], input=audio, capture_output=True, check=True).stdout


def shop_words(store):
    """Her product and customer names, so the transcript spells them the way her books do."""
    try:
        return [p.name for p in store.list_products()] + [c.name for c in store.list_contacts()]
    except Exception:
        return []


async def transcribe(audio: bytes, language=None, timeout=60, keywords=()) -> str:
    backend = settings.transcriber()
    if backend == "meta":
        try:
            return await asyncio.to_thread(transcribe_meta, audio, language, timeout, keywords)
        except Exception:
            if not settings.baseten_api_key:
                raise
            log.exception("Muse Voice Transcribe failed; Muse Spark Open listens instead")
            backend = "baseten"
    if backend == "fireworks":
        return await transcribe_fireworks(audio, language, timeout)
    return await asyncio.to_thread(transcribe_baseten, audio, language, timeout)


def _audio_name(audio: bytes) -> str:
    if audio[:4] == b"OggS":
        return "voice.ogg"
    if audio[:4] == bytes.fromhex("1a45dfa3"):
        return "voice.webm"      # browser MediaRecorder (the demo page)
    if audio[4:8] == b"ftyp":
        return "voice.m4a"
    if audio[:4] == b"RIFF":
        return "voice.wav"
    return "voice.mp3"


AUDIO_TYPES = {"voice.ogg": "audio/ogg", "voice.m4a": "audio/mp4", "voice.mp3": "audio/mpeg", "voice.wav": "audio/wav"}
LISTEN_PROMPT = ("Transcribe this voice note from a trader word for word, in the language spoken. It may be or mix "
                 "English, Pidgin, Yoruba, Igbo, Hausa, French, Portuguese, Swahili, Arabic or another language. Keep their words and amounts exactly as spoken; do not "
                 "translate, correct or summarise. Reply with the transcript only.")


def to_ogg(audio: bytes) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not on PATH")
    return subprocess.run([ffmpeg, "-v", "error", "-i", "pipe:0", "-c:a", "libopus", "-ac", "1", "-f", "ogg", "pipe:1"],
                          input=audio, capture_output=True, check=True).stdout


def transcribe_baseten(audio: bytes, language=None, timeout=60) -> str:
    from openai import OpenAI

    name = _audio_name(audio)
    if name not in AUDIO_TYPES:    # WebM from the browser
        audio, name = to_ogg(audio), "voice.ogg"
    url = f"data:{AUDIO_TYPES[name]};base64,{base64.b64encode(audio).decode()}"
    prompt = LISTEN_PROMPT + (f" The language is probably {language}." if language else "")
    client = OpenAI(base_url=settings.baseten_base_url, api_key=settings.baseten_api_key, timeout=timeout)
    resp = client.chat.completions.create(model=settings.baseten_model, temperature=0, messages=[
        {"role": "user", "content": [{"type": "text", "text": prompt},
                                     {"type": "audio_url", "audio_url": {"url": url}}]}])
    return (resp.choices[0].message.content or "").strip()


async def transcribe_fireworks(audio: bytes, language=None, timeout=60) -> str:
    import httpx

    data = {"model": settings.fireworks_transcribe_model, "temperature": "0", "vad_model": "silero"}
    if language:
        data["language"] = language
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(settings.fireworks_transcribe_url, data=data,
                              files={"file": (_audio_name(audio), audio)},
                              headers={"Authorization": f"Bearer {settings.fireworks_api_key}"})
    if r.status_code >= 400:
        raise RuntimeError(f"transcription error {r.status_code}: {r.text[:300]}")
    return (r.json().get("text") or "").strip()


def transcribe_meta(audio: bytes, language=None, timeout=60, keywords=()) -> str:
    import httpx

    wav = audio if _audio_name(audio) == "voice.wav" else to_wav(audio)
    request = {"model": settings.transcribe_model, "audioEncoding": "WAV"}
    if keywords:
        request["keywords"] = [k for k in dict.fromkeys(keywords) if k][:100]
    r = httpx.post(f"{settings.meta_base_url}/asr/transcribe", timeout=timeout,
                   headers={"Authorization": f"Bearer {settings.model_api_key}", "Accept": "application/json"},
                   files={"request": (None, json.dumps(request), "application/json"),
                          "audio": ("voice.wav", wav, "audio/wav")})
    if r.status_code >= 400:
        raise RuntimeError(f"transcription error {r.status_code}: {r.text[:300]}")
    return (r.json().get("transcript") or "").strip()
