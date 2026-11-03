"""Sabi's voice.

First choice: a Nigerian English neural voice (Microsoft's en-NG Ezinne or Abeo, through the open-source
edge-tts package; TTS_VOICE picks one). It sounds like a real Nigerian speaker and reads amounts and
dates itself, so it only gets light cleaning (`for_speaking`).

Fallback, and TTS_ENGINE=mms: Meta's MMS text-to-speech (facebook/mms-tts-eng, the ONNX export at
Xenova/mms-tts-eng), run on our own server with onnxruntime.

MMS reads only lowercase letters, a few marks and digits it can't say as numbers, so code turns
what Sabi writes into words first: "₦42,000" -> "forty two thousand naira", "JSS1" -> "j s s
one", "11 January 2027" -> "eleven january twenty twenty seven". Emoji and WhatsApp markup are
dropped. The model is loaded on first use; without the model files (TTS_DIR) voice is off and
Sabi just writes.
"""
import io
import json
import logging
import re
import subprocess
import threading
import wave
from pathlib import Path

from app.config import settings

log = logging.getLogger("sabi.speech")
RATE = 16000
GAP = 0.25            # seconds of silence between sentences
MAX_CHARS = 1200      # a voice note is for a short answer; longer text is cut at a sentence

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
        "seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_words(n):
    """Whole numbers the way Nigerians say amounts: 42500 -> 'forty two thousand five hundred'."""
    if n < 0:
        return "minus " + number_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return _ONES[n // 100] + " hundred" + ("" if not rest else " and " + number_words(rest))
    for size, word in ((10**9, "billion"), (10**6, "million"), (1000, "thousand")):
        if n >= size:
            rest = n % size
            head = number_words(n // size) + " " + word
            if not rest:
                return head
            return head + (" and " if rest < 100 else " ") + number_words(rest)
    return str(n)


def _year(n):
    """2027 -> 'twenty twenty seven', 2005 -> 'two thousand and five', 1960 -> 'nineteen sixty'."""
    if 2000 <= n <= 2009:
        return number_words(n)
    hi, lo = divmod(n, 100)
    return number_words(hi) + " " + (number_words(lo) if lo >= 10 else f"o {_ONES[lo]}" if lo else "hundred")


_MONEY = re.compile(r"(₦|\bN(?=\d)|\bNGN\s?)(\d+(?:,\d{3})*)(?:\.(\d{1,2}))?(k\b)?", re.I)
_ORDINAL = re.compile(r"\b(\d{1,2})(st|nd|rd|th)\b", re.I)
_IRREGULAR = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth",
              "twelve": "twelfth"}


def ordinal_words(n):
    """1 -> 'first', 22 -> 'twenty second', 30 -> 'thirtieth'."""
    words = number_words(n).split()
    last = words[-1]
    words[-1] = _IRREGULAR.get(last) or (last[:-1] + "ieth" if last.endswith("y") else last + "th")
    return " ".join(words)


_THOUSANDS = re.compile(r"\b(\d+(?:\.\d+)?)k\b", re.I)
_LEVEL = re.compile(r"\b(JSS|SSS|SS|JS|P(?:ry)?)\s?(\d)\b", re.I)
_YEAR = re.compile(r"\b(19\d\d|20\d\d)\b")
_NUM = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def _amount(m):
    whole = int(m.group(2).replace(",", ""))
    if m.group(4):   # "₦12.5k"
        return number_words(round(float(f"{whole}.{m.group(3) or 0}") * 1000)) + " naira"
    text = number_words(whole) + " naira"
    if m.group(3) and int(m.group(3)):
        text += " " + number_words(int(m.group(3).ljust(2, "0"))) + " kobo"
    return text


def _plain_number(m):
    s = m.group(0).replace(",", "")
    if "," not in m.group(0) and "." not in s and len(s) >= 6:   # order refs, account numbers: digit by digit
        return " ".join(_ONES[int(d)] for d in s)
    if "." in s:
        whole, frac = s.split(".", 1)
        return number_words(int(whole)) + " point " + " ".join(_ONES[int(d)] for d in frac)
    return number_words(int(s))


def to_words(text):
    t = _EMOJI.sub(" ", text or "")
    t = re.sub(r"https?://\S+", " a link ", t)
    t = re.sub(r"[*_~`]", "", t)
    t = _MONEY.sub(_amount, t)
    t = _THOUSANDS.sub(lambda m: number_words(round(float(m.group(1)) * 1000)), t)   # "3.5k" -> three thousand five hundred
    t = _LEVEL.sub(lambda m: " ".join(m.group(1).lower()) + " " + _ONES[int(m.group(2))], t)
    t = _ORDINAL.sub(lambda m: ordinal_words(int(m.group(1))), t)
    t = _YEAR.sub(lambda m: _year(int(m.group(1))), t)
    t = re.sub(r"(\d)\s*x\b|\bx\s*(\d)", lambda m: f"{m.group(1) or ''} times {m.group(2) or ''}", t)
    t = t.replace("%", " percent").replace("&", " and ").replace("+", " plus ")
    t = _NUM.sub(_plain_number, t)
    t = re.sub(r"[\n•·:;]+", ". ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


class Voice:
    def __init__(self, folder):
        self.folder = Path(folder)
        self._session = None
        self._lock = threading.Lock()

    @property
    def model_file(self):
        return self.folder / "model_quantized.onnx"

    def available(self):
        return self.model_file.exists() and (self.folder / "vocab.json").exists()

    def _load(self):
        if self._session is None:
            import onnxruntime as ort
            self.vocab = json.loads((self.folder / "vocab.json").read_text(encoding="utf-8"))
            self._session = ort.InferenceSession(str(self.model_file), providers=["CPUExecutionProvider"])
        return self._session

    def _ids(self, words):
        ids = [self.vocab[c] for c in words if c in self.vocab]
        out = [0] * (2 * len(ids) + 1)   # VITS reads the text with a blank between every letter
        out[1::2] = ids
        return out

    def waveform(self, text):
        """float32 samples at 16 kHz for the text, sentence by sentence."""
        import numpy as np
        words = to_words(text)[:MAX_CHARS]
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", words) if p.strip(" .")]
        with self._lock:     # one ONNX session; keep calls one at a time
            session = self._load()
            chunks = []
            for p in parts:
                ids = np.array([self._ids(p)], dtype=np.int64)
                if ids.shape[1] < 3:
                    continue
                chunks += [session.run(None, {"input_ids": ids, "attention_mask": np.ones_like(ids)})[0].squeeze(),
                           np.zeros(int(RATE * GAP), dtype=np.float32)]
        return np.concatenate(chunks) if chunks else np.zeros(RATE // 2, dtype=np.float32)

    def wav(self, text):
        import numpy as np
        pcm = (np.clip(self.waveform(text), -1, 1) * 32767).astype("<i2").tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
        return buf.getvalue()

    def voice_note(self, text):
        return to_ogg(self.wav(text))


def to_ogg(audio):
    """OGG/Opus, the format WhatsApp plays as a voice note."""
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", "-ac", "1",
                        "-f", "ogg", "pipe:1"], input=audio, capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg: {r.stderr.decode()[:200]}")
    return r.stdout


def for_speaking(text):
    """For the neural voice: no emoji, markup or links; money said the Nigerian way; refs digit by digit."""
    t = _EMOJI.sub(" ", text or "")
    t = re.sub(r"https?://\S+", "the link", t)
    t = re.sub(r"[*_~`•]", "", t)
    t = _MONEY.sub(lambda m: f"{_naira_figure(m)} naira", t)
    t = _THOUSANDS.sub(lambda m: f"{round(float(m.group(1)) * 1000):,}", t)
    t = re.sub(r"\b\d{6,}\b", lambda m: " ".join(m.group(0)), t)
    t = re.sub(r"\n+", ". ", t)
    t = re.sub(r"\s+([.,!?])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()[:MAX_CHARS * 2]


def _naira_figure(m):
    whole = int(m.group(2).replace(",", ""))
    if m.group(4):   # "₦12.5k"
        return f"{round(float(f'{whole}.{m.group(3) or 0}') * 1000):,}"
    return f"{whole:,}" + (f".{m.group(3)}" if m.group(3) and int(m.group(3)) else "")


class NaturalVoice:
    """A Nigerian English neural voice through edge-tts (Microsoft's read-aloud service)."""

    def __init__(self, name):
        self.name = name

    def available(self):
        if settings.tts_engine == "mms":
            return False
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    def mp3(self, text):
        import asyncio

        import edge_tts

        async def make():
            out = bytearray()
            async for chunk in edge_tts.Communicate(for_speaking(text), self.name, rate="-4%").stream():
                if chunk["type"] == "audio":
                    out += chunk["data"]
            return bytes(out)
        data = asyncio.run(asyncio.wait_for(make(), timeout=30))
        if not data:
            raise RuntimeError("the voice service sent no audio")
        return data


mms = Voice(settings.tts_dir)
natural = NaturalVoice(settings.tts_voice)


def available():
    return natural.available() or mms.available()


def audio(text):
    """(bytes, media type) to play in a browser: the Nigerian voice, or MMS if it can't be reached."""
    if natural.available():
        try:
            return natural.mp3(text), "audio/mpeg"
        except Exception as e:
            log.warning("Nigerian voice failed, using MMS: %s", e)
    return mms.wav(text), "audio/wav"


def voice_note(text):
    """A WhatsApp voice note: the Nigerian voice, or MMS if it can't be reached."""
    if natural.available():
        try:
            return to_ogg(natural.mp3(text))
        except Exception as e:
            log.warning("Nigerian voice failed, using MMS: %s", e)
    return mms.voice_note(text)


def wants_text_too(body):
    """Account numbers, order refs and long answers are easier to read than to hear."""
    return len(body) > 400 or bool(re.search(r"\d{6,}|\bref\b", body, re.I))
