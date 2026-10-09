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


@pytest.fixture(params=["memory", "postgres"])
def store(request: pytest.FixtureRequest) -> Any:
    """A group session store: in memory, and on Postgres when `MAKAN_TEST_DATABASE_URL` is set."""
    if request.param == "memory":
        from makan.sessions import InMemorySessionStore

        return InMemorySessionStore()
    from makan.sessions.postgres import PostgresSessionStore

    return PostgresSessionStore(request.getfixturevalue("db"))  # skips without a database


@pytest.fixture(params=["memory", "postgres"])
def account_store(request: pytest.FixtureRequest) -> Any:
    """An account store with its memory store: in memory, and on Postgres when a database is set."""
    if request.param == "memory":
        from makan.accounts import InMemoryAccountStore

        return InMemoryAccountStore()
    from makan.accounts.postgres import PostgresAccountStore
    from makan.memory.postgres import PostgresMemoryStore

    conn = request.getfixturevalue("db")  # skips without a database
    store = PostgresAccountStore(conn)
    store.memory = PostgresMemoryStore(conn)  # type: ignore[attr-defined]
    return store


@pytest.fixture(params=["memory", "postgres"])
def world(request: pytest.FixtureRequest) -> Any:
    """The visit rules over an in-memory store, and over Postgres when a database is set."""
    from tests.visit_helpers import World

    if request.param == "memory":
        return World.in_memory()
    return World.on_postgres(request.getfixturevalue("db"))  # skips without a database


@pytest.fixture
def app_role(db: Any) -> Iterator[str]:
    """An ordinary role, so row-level security applies, as it does for a signed-in user."""
    role = f"makan_test_{uuid4().hex[:8]}"
    schema = db.execute("select current_schema()").fetchone()[0]
    try:
        db.execute(f"create role {role} nologin")
    except Exception:
        pytest.skip("cannot create a role on this database")
    try:
        db.execute(f"grant usage on schema {schema} to {role}")
        db.execute(f"grant all on all tables in schema {schema} to {role}")
        db.execute(f"grant execute on all functions in schema {schema} to {role}")
        yield role
    finally:
        db.execute("reset role")
        db.execute(f"drop owned by {role}")
        db.execute(f"drop role {role}")
