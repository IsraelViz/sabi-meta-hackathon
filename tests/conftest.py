"""Run every store-backed test against both stores.

Postgres runs on an embedded server (pgserver) when it is installed, so the
real triggers in schema.sql are exercised. Set SABI_TEST_PG_URL to use your
own database instead (its public schema is wiped for every test).
"""
import os
import tempfile

import pytest

from app.ledger.seed import demo_store


@pytest.fixture(scope="session")
def pg_url():
    url = os.environ.get("SABI_TEST_PG_URL")
    if url:
        yield url
        return
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(tempfile.mkdtemp(prefix="sabi-pg-"), cleanup_mode="stop")
    yield server.get_uri()


@pytest.fixture(params=["memory", "postgres"])
def store(request):
    if request.param == "memory":
        yield demo_store()
        return
    from app.ledger.pg_store import PostgresStore

    pg = PostgresStore(request.getfixturevalue("pg_url"))
    pg.conn.execute("drop schema if exists public cascade; create schema public;")
    pg.apply_schema()
    yield demo_store(store=pg)
    pg.conn.close()
