"""The SQL migrations and the typed models must describe the same tables.

The checks in the first half need no database.
The checks in the second half run the migrations on a real Postgres and skip unless
`MAKAN_TEST_DATABASE_URL` points at one, so they never need the network or a hosted project.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest

from makan import models
from makan.models import (
    MEMORY_KINDS,
    MEMORY_SOURCES,
    MemoryFact,
    Participant,
    Profile,
    Session,
    TraceEventRow,
    User,
)
from makan.trace import JsonlSink, TraceEvent, read_jsonl
from tests.helpers import migration_files

TABLE_MODELS: dict[str, type] = {
    "users": User,
    "profiles": Profile,
    "sessions": Session,
    "participants": Participant,
    "memory_facts": MemoryFact,
    "trace_events": TraceEventRow,
}

SQL_TO_PYTHON: dict[str, type] = {
    "uuid": UUID,
    "text": str,
    "timestamp with time zone": datetime,
    "boolean": bool,
    "integer": int,
    "smallint": int,
    "double precision": float,
}


def python_type(hint: Any) -> tuple[Any, bool]:
    """Split a type hint into its non-null part and whether it allows None."""
    if typing.get_origin(hint) in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        assert len(args) == 1
        return args[0], len(args) != len(typing.get_args(hint))
    return hint, False


def test_migrations_are_numbered_in_order() -> None:
    files = migration_files()
    assert files
    for index, path in enumerate(files, start=1):
        assert path.name.startswith(f"{index:04d}_"), path.name


def test_models_cover_every_table() -> None:
    dataclass_names = {
        n
        for n, o in vars(models).items()
        if dataclasses.is_dataclass(o) and o.__module__ == models.__name__
    }
    assert dataclass_names == {m.__name__ for m in TABLE_MODELS.values()}


def test_nullable_model_fields_default_to_none() -> None:
    for model in TABLE_MODELS.values():
        for field in dataclasses.fields(model):
            _, nullable = python_type(typing.get_type_hints(model)[field.name])
            if nullable:
                assert field.default is None, f"{model.__name__}.{field.name}"


def test_trace_row_normalizes_timestamps_to_utc() -> None:
    event = TraceEvent(run_id="r", seq=0, type="run_start", data={})
    row = TraceEventRow.from_event(event)
    local = dataclasses.replace(row, ts=row.ts.astimezone(timezone(timedelta(hours=8))))
    assert local.ts.utcoffset() == timedelta(hours=8)
    assert local.to_event() == event
    naive = TraceEventRow.from_dict(event.to_dict() | {"ts": "2026-01-02T03:04:05"})
    assert naive.ts == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert naive.to_event().ts == "2026-01-02T03:04:05+00:00"


def test_trace_event_survives_a_row_round_trip() -> None:
    event = TraceEvent(run_id=uuid4().hex, seq=3, type="tool_call", data={"name": "echo", "n": 1})
    session_id, user_id = uuid4(), uuid4()
    row = TraceEventRow.from_event(event, session_id=session_id, user_id=user_id)
    assert (row.session_id, row.user_id) == (session_id, user_id)
    assert row.to_event() == event


def test_jsonl_trace_lines_load_as_rows(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    sink = JsonlSink(path)
    events = [TraceEvent("run1", i, "model_request", {"i": i}) for i in range(3)]
    for event in events:
        sink.emit(event)
    rows = [TraceEventRow.from_dict(line) for line in read_jsonl(path)]
    assert [r.to_event() for r in rows] == events
    assert all(r.session_id is None and r.user_id is None for r in rows)


# Live Postgres checks. These skip unless MAKAN_TEST_DATABASE_URL is set.


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


def act_as(db: Any, role: str, user_id: UUID | None) -> None:
    claims = "" if user_id is None else f'{{"sub": "{user_id}"}}'
    db.execute("reset role")
    db.execute(f"set role {role}")
    db.execute("select set_config('request.jwt.claims', %s, false)", (claims,))


def count(db: Any, table: str) -> int:
    return int(db.execute(f"select count(*) from {table}").fetchone()[0])


def test_every_table_enforces_row_level_security_with_a_policy(db: Any) -> None:
    for table in TABLE_MODELS:
        enabled = db.execute(
            "select relrowsecurity from pg_class where oid = to_regclass(%s)", (table,)
        ).fetchone()[0]
        assert enabled, table
        policies = db.execute(
            "select count(*) from pg_policies where schemaname = current_schema() "
            "and tablename = %s",
            (table,),
        ).fetchone()[0]
        assert policies > 0, table


def test_session_expiry_has_no_default_retention(db: Any) -> None:
    default, is_nullable = db.execute(
        "select column_default, is_nullable from information_schema.columns "
        "where table_schema = current_schema() and table_name = 'sessions' "
        "and column_name = 'expires_at'"
    ).fetchone()
    assert default is None
    assert is_nullable == "YES"
    db.execute("insert into sessions default values")
    assert db.execute("select expires_at from sessions").fetchone()[0] is None


def test_every_memory_kind_and_source_is_accepted(db: Any) -> None:
    uid = uuid4()
    db.execute("insert into users (id) values (%s)", (uid,))
    for kind in MEMORY_KINDS:
        for source in MEMORY_SOURCES:
            db.execute(
                "insert into memory_facts (user_id, kind, content, source, confidence) "
                "values (%s, %s, '1', %s, 0.5)",
                (uid, kind, source),
            )
    assert count(db, "memory_facts") == len(MEMORY_KINDS) * len(MEMORY_SOURCES)


def test_database_columns_match_models(db: Any) -> None:
    tables = db.execute(
        "select table_name from information_schema.tables "
        "where table_schema = current_schema() and table_type = 'BASE TABLE'"
    ).fetchall()
    assert {name for (name,) in tables} == set(TABLE_MODELS)
    for table, model in TABLE_MODELS.items():
        rows = db.execute(
            "select column_name, data_type, is_nullable from information_schema.columns "
            "where table_schema = current_schema() and table_name = %s",
            (table,),
        ).fetchall()
        hints = typing.get_type_hints(model)
        assert {name for name, _, _ in rows} == set(hints), table
        for name, data_type, is_nullable in rows:
            base, nullable = python_type(hints[name])
            assert (is_nullable == "YES") == nullable, f"{table}.{name} nullability"
            if data_type == "jsonb":
                assert base is Any or typing.get_origin(base) is dict, f"{table}.{name}"
            elif typing.get_origin(base) is Literal:
                assert data_type == "text", f"{table}.{name}"
            else:
                assert base is SQL_TO_PYTHON[data_type], f"{table}.{name} type"


def test_trace_event_fields_are_columns_of_trace_events(db: Any) -> None:
    columns = db.execute(
        "select column_name from information_schema.columns "
        "where table_schema = current_schema() and table_name = 'trace_events'"
    ).fetchall()
    event = TraceEvent(run_id="r", seq=0, type="run_start", data={})
    assert set(event.to_dict()) <= {name for (name,) in columns}


def seed_two_users(db: Any) -> tuple[UUID, UUID, UUID]:
    """Two users with a session each, plus a guest session. Returns the three session ids."""
    a, b = uuid4(), uuid4()
    db.execute("insert into users (id) values (%s), (%s)", (a, b))
    ids = [uuid4(), uuid4(), uuid4()]
    for sid, uid in zip(ids, (a, b, None), strict=True):
        db.execute("insert into sessions (id, user_id) values (%s, %s)", (sid, uid))
        db.execute(
            "insert into participants (session_id, user_id, is_host) values (%s, %s, true)",
            (sid, uid),
        )
        db.execute(
            "insert into trace_events (run_id, seq, v, ts, type, session_id, user_id) "
            "values (%s, 0, 1, now(), 'run_start', %s, %s)",
            (uuid4().hex, sid, uid),
        )
    for uid in (a, b):
        db.execute(
            "insert into profiles (user_id) values (%s)",
            (uid,),
        )
        db.execute(
            "insert into memory_facts (user_id, kind, content, source, confidence) "
            "values (%s, 'cuisine_like', '\"thai\"', 'stated', 0.9)",
            (uid,),
        )
    db.execute(
        "insert into memory_facts (session_id, kind, content, source, confidence) "
        "values (%s, 'constraint', '\"halal\"', 'stated', 1)",
        (ids[2],),
    )
    return ids[0], ids[1], ids[2]


def test_each_user_reads_only_their_own_rows(db: Any, app_role: str) -> None:
    seed_two_users(db)
    a = db.execute("select id from users order by id limit 1").fetchone()[0]
    expected = {"users": 1, "profiles": 1, "sessions": 1, "participants": 1}
    expected |= {"memory_facts": 1, "trace_events": 1}
    act_as(db, app_role, a)
    for table, rows in expected.items():
        assert count(db, table) == rows, table


def test_guests_and_unset_connections_read_nothing(db: Any, app_role: str) -> None:
    seed_two_users(db)
    act_as(db, app_role, None)
    for table in TABLE_MODELS:
        assert count(db, table) == 0, table


def test_a_user_cannot_write_another_users_rows(db: Any, app_role: str) -> None:
    session_a, session_b, _ = seed_two_users(db)
    a = db.execute("select user_id from sessions where id = %s", (session_a,)).fetchone()[0]
    b = db.execute("select user_id from sessions where id = %s", (session_b,)).fetchone()[0]
    act_as(db, app_role, a)
    assert (
        db.execute("update memory_facts set confidence = 0 where user_id = %s", (b,)).rowcount == 0
    )
    with pytest.raises(Exception, match="row-level security"):
        db.execute("insert into participants (session_id) values (%s)", (session_b,))
    with pytest.raises(Exception, match="row-level security"):
        db.execute(
            "insert into trace_events (run_id, seq, v, ts, type, user_id) "
            "values ('x', 0, 1, now(), 't', %s)",
            (a,),
        )


def test_a_user_cannot_join_a_session_they_do_not_own(db: Any, app_role: str) -> None:
    session_a, session_b, guest_session = seed_two_users(db)
    a = db.execute("select user_id from sessions where id = %s", (session_a,)).fetchone()[0]
    db.execute("delete from participants where session_id in (%s, %s)", (session_b, guest_session))
    act_as(db, app_role, a)
    for sid in (session_b, guest_session):
        for is_host in (False, True):
            with pytest.raises(Exception, match="row-level security"):
                db.execute(
                    "insert into participants (session_id, user_id, is_host) values (%s, %s, %s)",
                    (sid, a, is_host),
                )
    db.execute("reset role")
    assert count(db, "participants") == 1


def test_a_session_owner_adds_guests_but_not_other_users(db: Any, app_role: str) -> None:
    session_a, session_b, _ = seed_two_users(db)
    a = db.execute("select user_id from sessions where id = %s", (session_a,)).fetchone()[0]
    b = db.execute("select user_id from sessions where id = %s", (session_b,)).fetchone()[0]
    act_as(db, app_role, a)
    db.execute(
        "insert into participants (session_id, display_name) values (%s, 'guest')", (session_a,)
    )
    with pytest.raises(Exception, match="row-level security"):
        db.execute("insert into participants (session_id, user_id) values (%s, %s)", (session_a, b))
    assert count(db, "participants") == 2


def test_a_participant_added_by_the_backend_reads_their_own_row(db: Any, app_role: str) -> None:
    session_a, session_b, _ = seed_two_users(db)
    b = db.execute("select user_id from sessions where id = %s", (session_b,)).fetchone()[0]
    db.execute("insert into participants (session_id, user_id) values (%s, %s)", (session_a, b))
    act_as(db, app_role, b)
    joined = db.execute(
        "select count(*) from participants where session_id = %s", (session_a,)
    ).fetchone()[0]
    assert joined == 1


def test_deleting_a_user_deletes_all_their_data(db: Any) -> None:
    session_a, _, _ = seed_two_users(db)
    a = db.execute("select user_id from sessions where id = %s", (session_a,)).fetchone()[0]
    db.execute("delete from users where id = %s", (a,))
    for table in ("profiles", "sessions", "participants", "memory_facts", "trace_events"):
        assert (
            db.execute(f"select count(*) from {table} where user_id = %s", (a,)).fetchone()[0] == 0
        ), table
    assert count(db, "sessions") == 2
    assert count(db, "memory_facts") == 2


def test_a_solo_guest_session_needs_no_account(db: Any) -> None:
    sid = uuid4()
    db.execute("insert into sessions (id) values (%s)", (sid,))
    db.execute("insert into participants (session_id, is_host) values (%s, true)", (sid,))
    row = db.execute("select user_id, shared_at, expires_at from sessions").fetchone()
    assert tuple(row) == (None, None, None)


def test_a_session_has_at_most_one_host(db: Any) -> None:
    sid = uuid4()
    db.execute("insert into sessions (id) values (%s)", (sid,))
    db.execute("insert into participants (session_id, is_host) values (%s, true)", (sid,))
    db.execute("insert into participants (session_id) values (%s)", (sid,))
    with pytest.raises(Exception, match="participants_one_host_idx"):
        db.execute("insert into participants (session_id, is_host) values (%s, true)", (sid,))


def test_memory_fact_rules(db: Any) -> None:
    uid = uuid4()
    db.execute("insert into users (id) values (%s)", (uid,))
    insert = (
        "insert into memory_facts (user_id, kind, content, source, confidence) "
        "values (%s, %s, '1', %s, %s)"
    )
    for args in (
        (uid, "mood", "stated", 0.5),
        (uid, "constraint", "guessed", 0.5),
        (uid, "constraint", "stated", 1.5),
        (None, "constraint", "stated", 0.5),
    ):
        with pytest.raises(Exception, match="violates check constraint"):
            db.execute(insert, args)
    with pytest.raises(Exception, match="violates check constraint"):
        db.execute(
            "insert into memory_facts (user_id, kind, content, source, confidence, "
            "observed_at, last_confirmed_at) "
            "values (%s, 'constraint', '1', 'stated', 1, now(), now() - interval '1 day')",
            (uid,),
        )
    db.execute(insert, (uid, "place_rating", "observed", 0.5))


def test_superseded_fact_keeps_its_link_when_the_newer_one_is_removed(db: Any) -> None:
    uid = uuid4()
    db.execute("insert into users (id) values (%s)", (uid,))
    old, new = uuid4(), uuid4()
    for fid in (old, new):
        db.execute(
            "insert into memory_facts (id, user_id, kind, content, source, confidence) "
            "values (%s, %s, 'cuisine_like', '\"thai\"', 'stated', 0.5)",
            (fid, uid),
        )
    db.execute("update memory_facts set superseded_by = %s where id = %s", (new, old))
    with pytest.raises(Exception, match="violates check constraint"):
        db.execute("update memory_facts set superseded_by = id where id = %s", (new,))
    db.execute("delete from memory_facts where id = %s", (new,))
    assert db.execute("select superseded_by from memory_facts").fetchone()[0] is None


def test_trace_row_round_trips_through_the_database(db: Any) -> None:
    from psycopg.types.json import Jsonb

    db.execute("set time zone 'Asia/Singapore'")
    event = TraceEvent(run_id=uuid4().hex, seq=0, type="tool_call", data={"name": "echo"})
    row = TraceEventRow.from_event(event)
    db.execute(
        "insert into trace_events (run_id, seq, v, ts, type, data) values (%s, %s, %s, %s, %s, %s)",
        (row.run_id, row.seq, row.v, row.ts, row.type, Jsonb(row.data)),
    )
    stored = db.execute("select run_id, seq, v, ts, type, data from trace_events").fetchone()
    loaded = TraceEventRow(*stored)
    assert loaded.ts.utcoffset() == timedelta(hours=8)
    assert loaded.ts == row.ts
    assert loaded.to_event() == event
