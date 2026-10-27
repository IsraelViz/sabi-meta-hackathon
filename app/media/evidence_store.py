"""Voice notes and photos the owner sent, kept as private evidence.

Stored under PRIVATE_MEDIA_DIR and referenced as "private:<name>" in the
evidence row. They are only served through the owner's authenticated API,
never at a public URL.
"""
import mimetypes
import secrets
from pathlib import Path

EXTENSIONS = {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/aac": ".aac",
              "audio/amr": ".amr", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


class PrivateMedia:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, data: bytes, mime: str) -> str:
        base = (mime or "").split(";")[0].strip().lower()
        ext = EXTENSIONS.get(base) or mimetypes.guess_extension(base) or ".bin"
        name = f"{secrets.token_hex(12)}{ext}"
        (self.dir / name).write_bytes(data)
        return f"private:{name}"
