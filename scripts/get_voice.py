"""Download Sabi's voice: Meta's MMS English text-to-speech, ONNX export (Xenova/mms-tts-eng).

Pinned to one commit of the model repo, and the model file is checked against its SHA-256, so
the build gets exactly the voice that was tested. The Docker build runs this; locally run it once:

    python scripts/get_voice.py            # into models/mms-tts-eng (or TTS_DIR)
"""
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

REPO = "https://huggingface.co/Xenova/mms-tts-eng/resolve/3f8955a2adbd6487ce57420620d4916de22b9dac"
FILES = {"vocab.json": None, "config.json": None, "tokenizer_config.json": None,
         "onnx/model_quantized.onnx": "13cf610a20d9bec7d8948801ab073a65d85bf65b793927e3243f6fbf367cd0a5"}


def main():
    out = Path(os.environ.get("TTS_DIR") or Path(__file__).resolve().parents[1] / "models" / "mms-tts-eng")
    out.mkdir(parents=True, exist_ok=True)
    for name, sha in FILES.items():
        target = out / Path(name).name
        if target.exists() and (sha is None or hashlib.sha256(target.read_bytes()).hexdigest() == sha):
            continue
        with urllib.request.urlopen(f"{REPO}/{name}", timeout=300) as r:
            data = r.read()
        if sha and hashlib.sha256(data).hexdigest() != sha:
            sys.exit(f"{name}: checksum does not match; not saving it")
        target.write_bytes(data)
        print(f"{target.name}: {len(data):,} bytes")
    print(f"voice ready in {out}")


if __name__ == "__main__":
    main()
