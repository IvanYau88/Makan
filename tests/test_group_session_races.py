"""Closing and expiry must freeze a session even against a write that was already admitted.

The service checks a session before it writes, so something can land between the check and the
write. `InterleavingStore` forces that moment deterministically on every store, and the Postgres
tests at the end prove the row lock across two separate connections. Duplicate joins by one
signed-in user must also resolve to one participant in both stores.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from makan.models import Participant, Session
from makan.sessions import (
    GroupSessions,
    InMemorySessionStore,
    SessionClosed,
    SessionError,
    SessionExpired,
    SessionStore,
)
from tests.helpers import InterleavingStore

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
RETENTION = timedelta(hours=2)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def hooked(store: SessionStore) -> InterleavingStore:
    return InterleavingStore(store)


@pytest.fixture
def groups(hooked: InterleavingStore, clock: Clock) -> GroupSessions:
    return GroupSessions(hooked, retention=RETENTION, max_participants=3, clock=clock)


def start(groups: GroupSessions) -> tuple[Session, Participant]:
    return groups.create(latitude=3.148, longitude=101.695, request="dinner", host_name="Alex")


def refusing(groups: GroupSessions, session: Session) -> Participant:
    """Sam joins and refuses seafood, which is what a late update would silently undo."""
    _, sam = groups.join(str(session.link_token), display_name="Sam")
    groups.submit(
        str(session.link_token),
        str(sam.id),
        constraints={"refuses": ["seafood"]},
        preferences={},
    )
    return sam


def refusals(store: SessionStore, sam: Participant) -> list[str]:
    stored = store.get_participant(sam.id)
    assert stored is not None
    refuses: list[str] = stored.constraints["refuses"]
    return refuses


# Close lands between the check and the write


def test_a_close_between_the_check_and_a_join_keeps_the_joiner_out(
    groups: GroupSessions, hooked: InterleavingStore, store: SessionStore
) -> None:
    session, host = start(groups)
    hooked.before["add_participant"] = lambda: groups.close(str(session.link_token), str(host.id))
    with pytest.raises(SessionClosed):
        groups.join(str(session.link_token), display_name="Late")
    assert [p.display_name for p in store.participants(session.id)] == ["Alex"]
    found = store.get_by_token(session.link_token)
    assert found is not None and found.closed_at == T0


def test_a_close_between_the_check_and_an_update_leaves_the_inputs_frozen(
    groups: GroupSessions, hooked: InterleavingStore, store: SessionStore
) -> None:
    session, host = start(groups)
    sam = refusing(groups, session)
    hooked.before["update_participant"] = lambda: groups.close(
        str(session.link_token), str(host.id)
    )
    with pytest.raises(SessionClosed):
        groups.submit(str(session.link_token), str(sam.id), constraints={}, preferences={})
    assert refusals(store, sam) == ["seafood"]


# The clock reaches expires_at between the check and the write


def advance_to_expiry(clock: Clock) -> Callable[[], object]:
    def hook() -> None:
        clock.now = T0 + RETENTION  # exactly expires_at, which counts as expired

    return hook


def test_expiry_between_the_check_and_a_join_keeps_the_joiner_out(
    groups: GroupSessions, hooked: InterleavingStore, store: SessionStore, clock: Clock
) -> None:
    session, _ = start(groups)
    hooked.before["after_get_by_token"] = advance_to_expiry(clock)
    with pytest.raises(SessionExpired):
        groups.join(str(session.link_token), display_name="Late")
    assert len(store.participants(session.id)) == 1


def test_expiry_between_the_check_and_an_update_leaves_the_inputs_alone(
    groups: GroupSessions, hooked: InterleavingStore, store: SessionStore, clock: Clock
) -> None:
    session, _ = start(groups)
    sam = refusing(groups, session)
    hooked.before["after_get_participant"] = advance_to_expiry(clock)
    with pytest.raises(SessionExpired):
        groups.submit(str(session.link_token), str(sam.id), constraints={}, preferences={})
    assert refusals(store, sam) == ["seafood"]


def test_expiry_between_the_check_and_a_close_does_not_close_the_session(
    groups: GroupSessions, hooked: InterleavingStore, store: SessionStore, clock: Clock
) -> None:
    session, host = start(groups)
    hooked.before["after_get_participant"] = advance_to_expiry(clock)
    with pytest.raises(SessionExpired):
        groups.close(str(session.link_token), str(host.id))
    found = store.get_by_token(session.link_token)
    assert found is not None and found.closed_at is None


def test_one_microsecond_before_expiry_a_write_still_lands(
    groups: GroupSessions, hooked: InterleavingStore, store: SessionStore, clock: Clock
) -> None:
    session, _ = start(groups)

    def almost() -> None:
        clock.now = T0 + RETENTION - timedelta(microseconds=1)

    hooked.before["after_get_by_token"] = almost
    groups.join(str(session.link_token), display_name="Just in time")
    assert len(store.participants(session.id)) == 2


# One signed-in user joining twice at once


def add_user(store: SessionStore, user: Any, request: pytest.FixtureRequest) -> None:
    """Postgres needs a users row for a signed-in participant, and memory needs nothing."""
    if not isinstance(store, InMemorySessionStore):
        request.getfixturevalue("db").execute("insert into users (id) values (%s)", (user,))


def test_simultaneous_joins_by_one_user_give_one_participant(
    store: SessionStore, clock: Clock, request: pytest.FixtureRequest
) -> None:
    user = uuid4()
    add_user(store, user, request)
    groups = GroupSessions(store, retention=None, max_participants=2, clock=clock)  # one slot left
    session, _ = start(groups)
    joined = run_together([lambda: groups.join(str(session.link_token), user_id=user)] * 6)
    assert all(isinstance(r, tuple) for r in joined), joined
    assert len({r[1].id for r in joined}) == 1
    people = store.participants(session.id)
    assert len(people) == 2 and sum(p.user_id == user for p in people) == 1


def test_a_second_user_still_finds_the_full_session_full(
    store: SessionStore, clock: Clock, request: pytest.FixtureRequest
) -> None:
    first, second = uuid4(), uuid4()
    add_user(store, first, request)
    add_user(store, second, request)
    groups = GroupSessions(store, retention=None, max_participants=2, clock=clock)
    session, _ = start(groups)
    groups.join(str(session.link_token), user_id=first)
    # The one already in gets themself back from a full session, and the newcomer is turned away.
    assert groups.join(str(session.link_token), user_id=first)[1].user_id == first
    with pytest.raises(SessionError) as full:
        groups.join(str(session.link_token), user_id=second)
    assert full.value.code == "session_full"


def run_together(jobs: list[Callable[[], Any]]) -> list[Any]:
    """Run the jobs on threads that start at the same moment, and return each result or error."""
    barrier = threading.Barrier(len(jobs))
    results: list[Any] = [None] * len(jobs)

    def work(index: int) -> None:
        barrier.wait()
        try:
            results[index] = jobs[index]()
        except Exception as exc:
            results[index] = exc

    threads = [threading.Thread(target=work, args=(i,)) for i in range(len(jobs))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    return results


# Postgres, on separate connections


@pytest.fixture
def second_connection(db: Any) -> Any:
    """A second connection to the same throwaway schema, as another process would have."""
    psycopg = pytest.importorskip("psycopg")
    schema = db.execute("select current_schema()").fetchone()[0]
    conn = psycopg.connect(os.environ["MAKAN_TEST_DATABASE_URL"], autocommit=True)
    conn.execute(f"set search_path to {schema}")
    yield conn
    conn.close()


def wait_until_blocked(db: Any) -> None:
    """Wait until some other connection is waiting on a lock."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        (waiting,) = db.execute(
            "select count(*) from pg_stat_activity "
            "where wait_event_type = 'Lock' and datname = current_database()"
        ).fetchone()
        if waiting:
            return
        time.sleep(0.02)
    raise AssertionError("no connection ever waited on the session lock")


def blocked_then_closed(db: Any, session: Session, attempt: Callable[[], Any]) -> list[Any]:
    """Hold the session's row lock, let `attempt` block on it, then close and release."""
    outcome: list[Any] = []

    def run() -> None:
        try:
            outcome.append(attempt())
        except Exception as exc:
            outcome.append(exc)

    worker = threading.Thread(target=run)
    with db.transaction():
        db.execute("select 1 from sessions where id = %s for update", (session.id,))
        worker.start()
        wait_until_blocked(db)
        assert outcome == []  # still waiting, so it has not written yet
        db.execute("update sessions set closed_at = %s where id = %s", (T0, session.id))
    worker.join(20)
    return outcome


def test_a_join_waits_for_a_closing_transaction_on_another_connection(
    db: Any, second_connection: Any, clock: Clock
) -> None:
    from makan.sessions.postgres import PostgresSessionStore

    groups = GroupSessions(PostgresSessionStore(db), retention=None, clock=clock)
    other = PostgresSessionStore(second_connection)
    session, _ = start(groups)
    # The service's own check has passed by the time the store is asked to write.
    outcome = blocked_then_closed(
        db,
        session,
        lambda: other.add_participant(
            Participant(uuid4(), session.id, False, {}, {}, T0, T0), limit=5, now=T0
        ),
    )
    assert len(outcome) == 1 and isinstance(outcome[0], SessionClosed)
    assert db.execute("select count(*) from participants").fetchone()[0] == 1


def test_an_update_waits_for_a_closing_transaction_on_another_connection(
    db: Any, second_connection: Any, clock: Clock
) -> None:
    from makan.sessions.postgres import PostgresSessionStore

    groups = GroupSessions(PostgresSessionStore(db), retention=None, clock=clock)
    other = PostgresSessionStore(second_connection)
    session, _ = start(groups)
    sam = refusing(groups, session)
    outcome = blocked_then_closed(
        db,
        session,
        lambda: other.update_participant(
            sam.id, display_name="Sam", constraints={"refuses": []}, preferences={}, now=T0
        ),
    )
    assert len(outcome) == 1 and isinstance(outcome[0], SessionClosed)
    assert refusals(PostgresSessionStore(db), sam) == ["seafood"]


def test_a_close_waits_for_a_joining_transaction_and_then_the_join_is_counted(
    db: Any, second_connection: Any, clock: Clock
) -> None:
    from makan.sessions.postgres import PostgresSessionStore

    store = PostgresSessionStore(db)
    groups = GroupSessions(store, retention=None, max_participants=2, clock=clock)
    session, _ = start(groups)
    closer = PostgresSessionStore(second_connection)
    outcome: list[Any] = []
    worker = threading.Thread(target=lambda: outcome.append(closer.close(session.id, T0)))
    with db.transaction():
        db.execute("select 1 from sessions where id = %s for update", (session.id,))
        worker.start()
        wait_until_blocked(db)
        db.execute(
            "insert into participants (session_id, display_name) values (%s, 'First')",
            (session.id,),
        )
    worker.join(20)
    assert len(outcome) == 1 and outcome[0] is not None and outcome[0].closed_at == T0
    assert db.execute("select count(*) from participants").fetchone()[0] == 2


def test_simultaneous_joins_by_one_user_on_two_connections_give_one_participant(
    db: Any, second_connection: Any, clock: Clock
) -> None:
    from makan.sessions.postgres import PostgresSessionStore

    user = uuid4()
    db.execute("insert into users (id) values (%s)", (user,))
    one = GroupSessions(PostgresSessionStore(db), retention=None, max_participants=2, clock=clock)
    two = GroupSessions(
        PostgresSessionStore(second_connection), retention=None, max_participants=2, clock=clock
    )
    session, _ = start(one)
    link = str(session.link_token)
    results = run_together(
        [
            lambda: one.join(link, user_id=user),
            lambda: two.join(link, user_id=user),
            lambda: one.join(link, user_id=user),
            lambda: two.join(link, user_id=user),
        ]
    )
    assert all(isinstance(r, tuple) for r in results), results
    assert len({r[1].id for r in results}) == 1
    assert (
        db.execute("select count(*) from participants where user_id = %s", (user,)).fetchone()[0]
        == 1
    )
