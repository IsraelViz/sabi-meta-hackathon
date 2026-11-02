"""Check the configured model backend for real: a tool call, a photo, and (if ffmpeg is here) a voice note.

    python scripts/check_models.py

Costs a fraction of a cent. Prints what worked and what didn't, and why.
"""
import asyncio
import base64
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from app.config import settings  # noqa: E402
from app.models.llm import LLMClient  # noqa: E402

TOOL = {"type": "function", "function": {
    "name": "record_sale", "description": "Record a sale in the shop's books.",
    "parameters": {"type": "object", "required": ["product", "qty", "unit_price_naira"], "properties": {
        "product": {"type": "string"}, "qty": {"type": "number"}, "unit_price_naira": {"type": "number"}}}}}


def check(name, fn):
    try:
        print(f"OK   {name}: {fn()}")
        return True
    except Exception as e:   # report and keep going
        print(f"FAIL {name}: {type(e).__name__}: {str(e)[:300]}")
        return False


def tool_call(llm):
    r = llm.chat([{"role": "user", "content": "I sell 3 carton Indomie, 12,500 each"}], tools=[TOOL])
    if not r.tool_calls:
        raise RuntimeError(f"no tool call; replied: {r.content!r}")
    c = r.tool_calls[0]
    return f"{c.name}({json.loads(c.arguments)}) · {r.tokens} tokens"


def photo(llm):
    img = Image.new("RGB", (480, 200), "white")
    ImageDraw.Draw(img).text((20, 80), "RECEIPT  Peak milk x6  N4,200", fill="black")
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    r = llm.chat([{"role": "user", "content": [
        {"type": "text", "text": "What product and total are on this receipt? One short line."},
        {"type": "image_url", "image_url": {"url": url}}]}])
    return (r.content or "").strip()[:120]


def voice():
    from app.media.transcribe import transcribe

    if not settings.can_transcribe():
        return f"skipped (no key for TRANSCRIBE_BACKEND={settings.transcriber()})"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return "skipped (no ffmpeg to make a test clip)"
    # A 1 s tone: Whisper should return little or nothing, but the request shape is proven.
    ogg = subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                          "-c:a", "libopus", "-f", "ogg", "pipe:1"], capture_output=True, check=True).stdout
    return f"transcribed {len(ogg)} bytes of OGG/Opus → {asyncio.run(transcribe(ogg))!r} ({settings.transcriber()})"


def main():
    if not settings.has_llm():
        sys.exit(f"No API key for LLM_BACKEND={settings.llm_backend}. Set it in .env first.")
    llm = LLMClient()
    print(f"Backend {settings.llm_backend}, model {llm.model}")
    ok = [check("tool call", lambda: tool_call(llm)), check("photo", lambda: photo(llm)), check("voice note", voice)]
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
