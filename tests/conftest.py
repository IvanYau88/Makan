import os
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest

from makan.trace import ListSink
from tests.helpers import migration_files


@pytest.fixture
def sink() -> ListSink:
    return ListSink()


@pytest.fixture
def db() -> Iterator[Any]:
    """A connection to a throwaway schema with the migrations applied.

    Skips unless `MAKAN_TEST_DATABASE_URL` points at a Postgres, so nothing here needs the
    network or a hosted project.
    """
    url = os.environ.get("MAKAN_TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("MAKAN_TEST_DATABASE_URL is not set")
    psycopg = pytest.importorskip("psycopg")
    schema = f"makan_test_{uuid4().hex[:8]}"
    conn = psycopg.connect(url, autocommit=True)
    try:
        conn.execute(f"create schema {schema}")
        conn.execute(f"set search_path to {schema}")
        for path in migration_files():
            conn.execute(path.read_text(encoding="utf-8"))
        yield conn
    finally:
        conn.execute("reset role")
        conn.execute(f"drop schema {schema} cascade")
        conn.close()
