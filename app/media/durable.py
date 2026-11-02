"""Saved pictures and voice notes that survive a host wiping its disk.

Render's free tier starts every deploy and restart with an empty disk. With the
books in Postgres, each file Sabi saves (product photos, flyers, voice notes) is
also written to a `saved_files` table, and put back on disk when the app starts.
The app keeps serving files from disk as before.
"""
import logging
import threading
from pathlib import Path

import psycopg

log = logging.getLogger("sabi.files")

_mirror = None


class FileMirror:
    def __init__(self, dsn):
        self.conn = psycopg.connect(dsn, autocommit=True)
        self.lock = threading.Lock()
        self.conn.execute("create table if not exists saved_files (path text primary key, data bytea not null, "
                          "saved_at timestamptz not null default now())")

    def keep(self, path, data):
        with self.lock:
            self.conn.execute("insert into saved_files (path, data) values (%s, %s) "
                              "on conflict (path) do update set data = excluded.data, saved_at = now()",
                              (_key(path), data))

    def restore(self):
        """Write back every kept file the disk is missing. Returns how many."""
        with self.lock:
            missing = [p for (p,) in self.conn.execute("select path from saved_files") if not Path(p).exists()]
            for p in missing:
                (data,) = self.conn.execute("select data from saved_files where path = %s", (p,)).fetchone()
                Path(p).parent.mkdir(parents=True, exist_ok=True)
                Path(p).write_bytes(data)
        return len(missing)


def _key(path):
    """Paths relative to the app folder, so they mean the same on every machine."""
    p = Path(path)
    try:
        return p.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def start(dsn):
    global _mirror
    _mirror = FileMirror(dsn)
    restored = _mirror.restore()
    if restored:
        log.info("put back %d saved files from the database", restored)
    return _mirror


def keep(path, data):
    """Called after a file is written to disk; a no-op unless start() ran."""
    if _mirror is None:
        return
    try:
        _mirror.keep(path, data)
    except Exception:
        log.exception("couldn't keep %s in the database; it is on disk only", path)
