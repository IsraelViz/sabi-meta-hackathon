"""On a host that wipes its disk, saved pictures and voice notes come back from Postgres."""
import pytest

from app.media import durable
from app.media.evidence_store import PrivateMedia
from app.promo.publish import LocalMedia


@pytest.fixture
def mirror(pg_url, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    m = durable.start(pg_url)
    m.conn.execute("delete from saved_files")
    yield m
    durable._mirror = None
    m.conn.close()


def test_files_come_back_after_the_disk_is_wiped(mirror, tmp_path):
    photo = LocalMedia("media_cache", "https://sabi.example").save(b"peak milk", "jpg")
    voice = PrivateMedia("private_media").save(b"ogg voice", "audio/ogg").removeprefix("private:")
    for f in (tmp_path / "media_cache" / photo["name"], tmp_path / "private_media" / voice):
        f.unlink()                                        # the restart
    assert mirror.restore() == 2
    assert (tmp_path / "media_cache" / photo["name"]).read_bytes() == b"peak milk"
    assert (tmp_path / "private_media" / voice).read_bytes() == b"ogg voice"
    assert mirror.restore() == 0                          # nothing missing now


def test_without_a_database_files_stay_on_disk_only(tmp_path):
    assert durable._mirror is None
    saved = LocalMedia(tmp_path, "").save(b"x", "png")
    assert (tmp_path / saved["name"]).read_bytes() == b"x"
