"""Saved group sessions: the service rules over every store, and the Postgres store itself.

The `store` fixture runs each test against the in-memory store and, when `MAKAN_TEST_DATABASE_URL`
is set, against Postgres too. The Postgres runs skip cleanly without it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from makan.consensus import InvalidInputs
from makan.models import Participant, Session
from makan.sessions import (
    DEFAULT_RETENTION,
    GroupSessions,
    InMemorySessionStore,
    NotAParticipant,
    NotHost,
    ParticipantRequired,
    SessionClosed,
    SessionExpired,
    SessionFull,
    SessionNotFound,
    SessionStore,
    purge_expired_sessions,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def groups(store: SessionStore, clock: Clock) -> GroupSessions:
    return GroupSessions(store, retention=timedelta(hours=2), max_participants=4, clock=clock)


def start(groups: GroupSessions, **kwargs: Any) -> tuple[Session, Participant]:
    args: dict[str, Any] = {
        "latitude": 3.1481,
        "longitude": 101.6951,
        "request": "dinner",
        "host_name": "Alex",
    }
    return groups.create(**{**args, **kwargs})


def token(session: Session) -> str:
    return str(session.link_token)


# Creating and reading


def test_a_session_has_a_random_link_a_host_and_a_retention_period(
    groups: GroupSessions, clock: Clock
) -> None:
    session, host = start(groups, latitude=3.14812, longitude=101.69512, request="  thai   please ")
    other, _ = start(groups)
    assert session.link_token != other.link_token and session.link_token != session.id
    assert session.link_token.version == 4
    assert session.user_id is None and host.user_id is None
    assert session.shared_at == clock.now
    assert session.expires_at == clock.now + timedelta(hours=2)
    assert session.closed_at is None
    assert session.context == {
        "latitude": 3.148,
        "longitude": 101.695,
        "radius_m": 1000,
        "request": "thai please",
    }
    assert host.is_host and host.display_name == "Alex"
    assert host.session_id == session.id
    assert host.constraints == {} and host.preferences == {}


def test_no_retention_means_the_session_never_expires(store: SessionStore, clock: Clock) -> None:
    groups = GroupSessions(store, retention=None, clock=clock)
    session, host = start(groups)
    assert session.expires_at is None
    clock.advance(days=3650)
    assert groups.view(token(session), str(host.id)).session.expires_at is None
    assert groups.purge_expired() == 0
    assert groups.view(token(session)).session.id == session.id


def test_the_default_retention_is_a_day(store: SessionStore, clock: Clock) -> None:
    assert timedelta(hours=24) == DEFAULT_RETENTION
    session, _ = start(GroupSessions(store, clock=clock))
    assert session.expires_at == T0 + timedelta(hours=24)


def test_view_returns_the_session_and_the_callers_own_row_only_with_a_token(
    groups: GroupSessions,
) -> None:
    session, host = start(groups)
    _, friend = groups.join(token(session), display_name="Sam")
    public = groups.view(token(session))
    assert public.you is None
    assert [p.display_name for p in public.participants] == ["Alex", "Sam"]
    assert groups.view(token(session), str(friend.id)).you == friend
    assert groups.view(token(session), str(host.id)).you == host


@pytest.mark.parametrize("bad", ["not-a-token", "", str(uuid4()), "12345"])
def test_an_unknown_or_malformed_link_is_not_found(groups: GroupSessions, bad: str) -> None:
    with pytest.raises(SessionNotFound):
        groups.view(bad)
    with pytest.raises(SessionNotFound):
        groups.join(bad)


def test_a_bad_location_or_request_is_rejected(groups: GroupSessions) -> None:
    for kwargs in (
        {"latitude": 91.0},
        {"longitude": -181.0},
        {"radius_m": 5},
        {"request": "   "},
        {"request": "x" * 501},
        {"host_name": "  "},
    ):
        with pytest.raises(InvalidInputs):
            start(groups, **kwargs)


# Joining and sharing


def test_a_friend_joins_with_the_link_as_a_guest(groups: GroupSessions) -> None:
    session, host = start(groups)
    _, friend = groups.join(token(session), display_name="  Sam ")
    assert not friend.is_host and friend.user_id is None
    assert friend.display_name == "Sam"
    assert friend.id != host.id
    assert groups.join(token(session))[1].display_name is None


def test_a_session_holds_at_most_the_configured_number_of_people(groups: GroupSessions) -> None:
    session, _ = start(groups)
    for i in range(3):
        groups.join(token(session), display_name=f"P{i}")
    with pytest.raises(SessionFull):
        groups.join(token(session))
    assert len(groups.view(token(session)).participants) == 4


def test_joins_at_the_same_moment_cannot_exceed_the_limit(
    store: SessionStore, clock: Clock
) -> None:
    groups = GroupSessions(store, retention=None, max_participants=4, clock=clock)
    session, _ = start(groups)
    outcomes: list[str] = []

    def join() -> None:
        try:
            groups.join(token(session))
            outcomes.append("joined")
        except SessionFull:
            outcomes.append("full")

    threads = [threading.Thread(target=join) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("joined") == 3 and outcomes.count("full") == 9
    assert len(groups.view(token(session)).participants) == 4


def test_a_participant_shares_and_updates_their_own_inputs(groups: GroupSessions) -> None:
    session, _ = start(groups)
    _, friend = groups.join(token(session), display_name="Sam")
    saved = groups.submit(
        token(session),
        str(friend.id),
        constraints={"refuses": [" Seafood "], "allergies": ["peanut"], "budget": "cheap"},
        preferences={"likes": ["Thai"]},
    )
    assert saved.id == friend.id and saved.display_name == "Sam"
    assert saved.constraints == {
        "refuses": ["seafood"],
        "allergies": ["peanut"],
        "diets": [],
        "budget": "cheap",
    }
    assert saved.preferences == {"likes": ["thai"], "dislikes": []}
    again = groups.submit(
        token(session),
        str(friend.id),
        constraints={},
        preferences={"dislikes": ["burger"]},
        display_name="Samantha",
    )
    assert again.constraints["refuses"] == [] and again.preferences["dislikes"] == ["burger"]
    assert again.display_name == "Samantha"
    assert again.updated_at >= saved.updated_at
    # Sharing an empty form still counts as having shared, because the stored shape is full.
    empty = groups.submit(token(session), str(friend.id), constraints={}, preferences={})
    assert empty.constraints and empty.preferences


def test_invalid_inputs_are_rejected_and_change_nothing(groups: GroupSessions) -> None:
    session, host = start(groups)
    groups.submit(token(session), str(host.id), constraints={}, preferences={"likes": ["thai"]})
    with pytest.raises(InvalidInputs):
        groups.submit(token(session), str(host.id), constraints={"allergy": ["x"]}, preferences={})
    with pytest.raises(InvalidInputs):
        groups.submit(
            token(session), str(host.id), constraints={}, preferences={}, display_name=" "
        )
    assert groups.view(token(session), str(host.id)).you is not None
    (stored,) = groups.view(token(session)).participants
    assert stored.preferences["likes"] == ["thai"]


def test_nobody_can_change_someone_elses_inputs_or_use_another_sessions_token(
    groups: GroupSessions,
) -> None:
    one, host_one = start(groups)
    two, host_two = start(groups)
    with pytest.raises(ParticipantRequired):
        groups.submit(token(one), None, constraints={}, preferences={})
    for bad in ("nonsense", str(uuid4()), str(host_two.id)):
        with pytest.raises(NotAParticipant):
            groups.submit(token(one), bad, constraints={}, preferences={})
        with pytest.raises(NotAParticipant):
            groups.view(token(one), bad)
    assert host_one.id != host_two.id and two.id != one.id


def test_a_signed_in_user_joining_twice_gets_the_same_participant(
    store: SessionStore, clock: Clock, request: pytest.FixtureRequest
) -> None:
    user = uuid4()
    if not isinstance(store, InMemorySessionStore):
        request.getfixturevalue("db").execute("insert into users (id) values (%s)", (user,))
    groups = GroupSessions(store, clock=clock)
    session, _ = start(groups)
    _, first = groups.join(token(session), user_id=user)
    _, second = groups.join(token(session), user_id=user)
    assert first == second and first.user_id == user
    assert len(groups.view(token(session)).participants) == 2


# Closing and the result


def test_only_the_host_can_close_the_session_or_ask_for_the_result(
    groups: GroupSessions,
) -> None:
    session, host = start(groups)
    _, friend = groups.join(token(session), display_name="Sam")
    with pytest.raises(NotHost):
        groups.close(token(session), str(friend.id))
    with pytest.raises(NotHost):
        groups.host_inputs(token(session), str(friend.id))
    with pytest.raises(ParticipantRequired):
        groups.close(token(session), None)
    with pytest.raises(ParticipantRequired):
        groups.host_inputs(token(session), None)
    with pytest.raises(NotAParticipant):
        groups.host_inputs(token(session), str(uuid4()))
    found, people = groups.host_inputs(token(session), str(host.id))
    assert found.id == session.id
    assert [p.display_name for p in people] == ["Alex", "Sam"]


def test_a_closed_session_reads_but_takes_no_more_people_or_changes(
    groups: GroupSessions, clock: Clock
) -> None:
    session, host = start(groups)
    _, friend = groups.join(token(session), display_name="Sam")
    closed = groups.close(token(session), str(host.id))
    assert closed.closed_at == clock.now
    clock.advance(minutes=5)
    assert groups.close(token(session), str(host.id)).closed_at == closed.closed_at
    assert groups.view(token(session)).session.closed_at == closed.closed_at
    with pytest.raises(SessionClosed):
        groups.join(token(session))
    with pytest.raises(SessionClosed):
        groups.submit(token(session), str(friend.id), constraints={}, preferences={})
    # The inputs are frozen, so the host can still get the result.
    assert groups.host_inputs(token(session), str(host.id))[0].id == session.id


# Expiry


def test_an_expired_session_is_gone_for_everything_even_before_the_purge(
    groups: GroupSessions, clock: Clock
) -> None:
    session, host = start(groups)
    _, friend = groups.join(token(session), display_name="Sam")
    clock.advance(hours=1, minutes=59)
    assert groups.view(token(session)).session.id == session.id
    clock.advance(minutes=1)  # exactly at expires_at
    calls: list[Callable[[], Any]] = [
        lambda: groups.view(token(session)),
        lambda: groups.view(token(session), str(host.id)),
        lambda: groups.join(token(session)),
        lambda: groups.submit(token(session), str(friend.id), constraints={}, preferences={}),
        lambda: groups.close(token(session), str(host.id)),
        lambda: groups.host_inputs(token(session), str(host.id)),
    ]
    for action in calls:
        with pytest.raises(SessionExpired):
            action()


def test_a_session_expires_even_when_closed(groups: GroupSessions, clock: Clock) -> None:
    session, host = start(groups)
    groups.close(token(session), str(host.id))
    clock.advance(hours=3)
    with pytest.raises(SessionExpired):
        groups.view(token(session))


def test_the_purge_deletes_expired_sessions_and_what_they_own(
    store: SessionStore, clock: Clock
) -> None:
    short = GroupSessions(store, retention=timedelta(hours=1), clock=clock)
    long = GroupSessions(store, retention=timedelta(hours=5), clock=clock)
    never = GroupSessions(store, retention=None, clock=clock)
    gone, gone_host = start(short)
    short.join(token(gone), display_name="Sam")
    kept, _ = start(long)
    forever, _ = start(never)
    assert short.purge_expired() == 0  # nothing has expired yet
    clock.advance(hours=1)
    assert short.purge_expired() == 1
    assert store.get_by_token(gone.link_token) is None
    assert store.participants(gone.id) == []
    assert store.get_participant(gone_host.id) is None
    assert store.get_by_token(kept.link_token) is not None
    assert store.get_by_token(forever.link_token) is not None
    clock.advance(hours=4)
    assert purge_expired_sessions(store, clock()) == 1  # the module function, with an explicit time
    assert purge_expired_sessions(store, clock()) == 0
    assert store.get_by_token(forever.link_token) is not None


def test_purge_takes_the_current_time_when_given_none(store: SessionStore) -> None:
    session = Session(
        id=uuid4(),
        link_token=uuid4(),
        context={},
        created_at=T0,
        expires_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    host = Participant(uuid4(), session.id, True, {}, {}, T0, T0)
    store.create(session, host)
    assert purge_expired_sessions(store) == 1


# The store contract


def test_the_store_lists_the_host_first_then_by_join_time_then_id(store: SessionStore) -> None:
    session = Session(id=uuid4(), link_token=uuid4(), context={}, created_at=T0)
    host = Participant(uuid4(), session.id, True, {}, {}, T0, T0)
    store.create(session, host)
    ids = sorted(uuid4() for _ in range(3))
    later = [Participant(i, session.id, False, {}, {}, T0 + timedelta(minutes=1), T0) for i in ids]
    for p in reversed(later):
        assert store.add_participant(p, limit=10, now=T0) == p
    assert [p.id for p in store.participants(session.id)] == [host.id, *ids]


def test_the_store_refuses_a_second_host_and_a_duplicate_link(store: SessionStore) -> None:
    session = Session(id=uuid4(), link_token=uuid4(), context={}, created_at=T0)
    host = Participant(uuid4(), session.id, True, {}, {}, T0, T0)
    store.create(session, host)
    with pytest.raises(ValueError):
        store.add_participant(
            Participant(uuid4(), session.id, True, {}, {}, T0, T0), limit=10, now=T0
        )
    clash = Session(id=uuid4(), link_token=session.link_token, context={}, created_at=T0)
    with pytest.raises(ValueError):
        store.create(clash, Participant(uuid4(), clash.id, True, {}, {}, T0, T0))
    assert [p.id for p in store.participants(session.id)] == [host.id]
    found = store.get_by_token(session.link_token)
    assert found is not None and found.id == session.id


def test_a_failed_create_leaves_no_session_behind(store: SessionStore) -> None:
    session = Session(id=uuid4(), link_token=uuid4(), context={}, created_at=T0)
    wrong_host = Participant(uuid4(), uuid4(), True, {}, {}, T0, T0)  # another session's host
    with pytest.raises(ValueError):
        store.create(session, wrong_host)
    assert store.get_by_token(session.link_token) is None


def test_store_writes_to_a_missing_row_are_reported(store: SessionStore) -> None:
    missing: UUID = uuid4()
    assert store.get_participant(missing) is None
    assert store.close(missing, T0) is None
    assert (
        store.update_participant(missing, display_name=None, constraints={}, preferences={}, now=T0)
        is None
    )
    with pytest.raises(SessionNotFound):
        store.add_participant(Participant(uuid4(), missing, False, {}, {}, T0, T0), limit=5, now=T0)
    assert store.participants(missing) == []


def test_the_store_round_trips_json_without_aliasing(store: SessionStore) -> None:
    context: dict[str, Any] = {"latitude": 3.148, "nested": {"a": [1, 2]}}
    session = Session(id=uuid4(), link_token=uuid4(), context=context, created_at=T0)
    store.create(session, Participant(uuid4(), session.id, True, {}, {}, T0, T0))
    context["nested"]["a"].append(3)  # the caller's dictionary must not reach the store
    found = store.get_by_token(session.link_token)
    assert found is not None and found.context == {"latitude": 3.148, "nested": {"a": [1, 2]}}
    found.context["nested"]["a"].append(9)
    again = store.get_by_token(session.link_token)
    assert again is not None and again.context["nested"]["a"] == [1, 2]


# Postgres only


def test_the_purge_cascades_to_guest_memory_and_traces_in_postgres(db: Any) -> None:
    from makan.sessions.postgres import PostgresSessionStore

    store = PostgresSessionStore(db)
    clock = Clock()
    short = GroupSessions(store, retention=timedelta(hours=1), clock=clock)
    long = GroupSessions(store, retention=timedelta(hours=5), clock=clock)
    gone, _ = start(short)
    kept, _ = start(long)
    for session in (gone, kept):
        db.execute(
            "insert into memory_facts (session_id, kind, content, source, confidence) "
            "values (%s, 'constraint', '\"halal\"', 'stated', 1)",
            (session.id,),
        )
        db.execute(
            "insert into trace_events (run_id, seq, v, ts, type, session_id) "
            "values (%s, 0, 1, now(), 'run_start', %s)",
            (session.id.hex, session.id),
        )
    assert purge_expired_sessions(store, T0 + timedelta(hours=2)) == 1
    for table in ("sessions", "participants", "memory_facts", "trace_events"):
        assert db.execute(f"select count(*) from {table}").fetchone()[0] == 1, table
        column = "id" if table == "sessions" else "session_id"
        assert db.execute(f"select {column} from {table}").fetchone()[0] == kept.id, table


def test_postgres_rows_match_what_the_service_stored(db: Any) -> None:
    from makan.sessions.postgres import PostgresSessionStore

    groups = GroupSessions(PostgresSessionStore(db), clock=Clock())
    session, host = start(groups)
    row = db.execute(
        "select link_token, shared_at, expires_at, closed_at, context ->> 'request' "
        "from sessions where id = %s",
        (session.id,),
    ).fetchone()
    assert row == (session.link_token, T0, T0 + DEFAULT_RETENTION, None, "dinner")
    groups.close(str(session.link_token), str(host.id))
    assert db.execute("select closed_at from sessions").fetchone()[0] == T0


def test_every_store_write_rechecks_closing_and_expiry_itself(store: SessionStore) -> None:
    """The check and the write are one step in the store, so an earlier check is not enough."""
    expires = T0 + timedelta(hours=1)
    session = Session(id=uuid4(), link_token=uuid4(), context={}, created_at=T0, expires_at=expires)
    host = Participant(uuid4(), session.id, True, {}, {}, T0, T0)
    store.create(session, host)
    sam = Participant(uuid4(), session.id, False, {}, {}, T0, T0)
    just_before = expires - timedelta(microseconds=1)
    store.add_participant(sam, limit=9, now=just_before)
    inputs: dict[str, Any] = {"constraints": {"refuses": ["x"]}, "preferences": {}}
    assert store.update_participant(sam.id, display_name="S", now=just_before, **inputs)

    late = Participant(uuid4(), session.id, False, {}, {}, T0, T0)
    with pytest.raises(SessionExpired):
        store.add_participant(late, limit=9, now=expires)  # exactly expires_at is expired
    with pytest.raises(SessionExpired):
        store.update_participant(sam.id, display_name="Z", now=expires, **inputs)
    with pytest.raises(SessionExpired):
        store.close(session.id, expires)
    found = store.get_by_token(session.link_token)
    assert found is not None and found.closed_at is None

    closed = store.close(session.id, just_before)
    assert closed is not None and closed.closed_at == just_before
    with pytest.raises(SessionClosed):
        store.add_participant(late, limit=9, now=just_before)
    with pytest.raises(SessionClosed):
        store.update_participant(sam.id, display_name="Z", now=just_before, **inputs)
    again = store.close(session.id, T0)
    assert again is not None and again.closed_at == just_before  # the first closing time stays
    assert [p.display_name for p in store.participants(session.id)] == [None, "S"]
