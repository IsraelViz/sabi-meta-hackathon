"""Muse Voice Transcribe.

LiteLLM documents the model as realtime-only over
wss://api.meta.ai/v1/asr/realtime: 16 kHz mono PCM16 in base64 chunks of at
most 4 s, then a commit, then a `...transcription.completed` event. The exact
session.update shape is not confirmed in Meta's public docs yet; if the first
call fails, compare it with dev.meta.ai and adjust `_session_update`.
"""
import asyncio
import base64
import json
import shutil
import subprocess

from app.config import settings

RATE = 16000
CHUNK_BYTES = RATE * 2 * 2   # 2 seconds of 16-bit mono


def to_pcm16(audio: bytes) -> bytes:
    """WhatsApp voice notes are OGG/Opus; convert anything ffmpeg reads to raw PCM16."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not on PATH")
    return subprocess.run([ffmpeg, "-v", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(RATE), "pipe:1"],
                          input=audio, capture_output=True, check=True).stdout


def _session_update(language=None):
    transcription = {"model": settings.transcribe_model}
    if language:
        transcription["language"] = language
    return {"type": "session.update", "session": {
        "type": "transcription",
        "audio": {"input": {"format": {"type": "audio/pcm", "rate": RATE, "channels": 1},
                            "transcription": transcription, "turn_detection": None}}}}


async def transcribe(audio: bytes, language=None, timeout=60) -> str:
    import websockets

    pcm = await asyncio.to_thread(to_pcm16, audio)
    base = settings.meta_base_url.replace("https://", "wss://")
    url = f"{base}/asr/realtime?model={settings.transcribe_model}&intent=transcription"
    headers = {"Authorization": f"Bearer {settings.model_api_key}"}
    async with websockets.connect(url, additional_headers=headers, max_size=None) as ws:
        await ws.send(json.dumps(_session_update(language)))
        for i in range(0, len(pcm), CHUNK_BYTES):
            await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                      "audio": base64.b64encode(pcm[i:i + CHUNK_BYTES]).decode()}))
        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            kind = msg.get("type", "")
            if kind == "conversation.item.input_audio_transcription.completed":
                return (msg.get("transcript") or "").strip()
            if kind == "error":
                raise RuntimeError(f"transcription error: {msg.get('error')}")
