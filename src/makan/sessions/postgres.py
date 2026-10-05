"""A `SessionStore` over the `sessions` and `participants` tables, using `psycopg`.

It takes an open connection and does not manage it, as `PostgresMemoryStore` does. Group link
access has no signed-in user, so the connection must be the backend's privileged one, which row
level security does not bind. One lock serializes use of the connection across threads.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from makan.models import Participant, Session
from makan.sessions.errors import SessionExpired, SessionFull, SessionNotFound, require_open

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover - only without the extra
    raise ImportError('psycopg is not installed; run: pip install -e ".[dev]"') from None

_SESSION = "id, user_id, link_token, context, shared_at, expires_at, closed_at, created_at"
_PARTICIPANT = (
    "id, session_id, user_id, display_name, is_host, "
    "constraints, preferences, joined_at, updated_at"
)


class PostgresSessionStore:
    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def create(self, session: Session, host: Participant) -> None:
        with self._lock, _conflicts(), self._conn.transaction():
            self._conn.execute(
                f"insert into sessions ({_SESSION}) values (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    session.id,
                    session.user_id,
                    session.link_token,
                    Jsonb(session.context),
                    session.shared_at,
                    session.expires_at,
                    session.closed_at,
                    session.created_at,
                ),
            )
            self._insert_participant(host)

    def get_by_token(self, link_token: UUID) -> Session | None:
        with self._lock:
            row = self._conn.execute(
                f"select {_SESSION} from sessions where link_token = %s", (link_token,)
            ).fetchone()
        return None if row is None else _session(row)

    def get_participant(self, participant_id: UUID) -> Participant | None:
        with self._lock:
            row = self._conn.execute(
                f"select {_PARTICIPANT} from participants where id = %s", (participant_id,)
            ).fetchone()
        return None if row is None else _participant(row)

    def participants(self, session_id: UUID) -> list[Participant]:
        with self._lock:
            rows = self._conn.execute(
                f"select {_PARTICIPANT} from participants where session_id = %s "
                "order by is_host desc, joined_at, id",
                (session_id,),
            ).fetchall()
        return [_participant(row) for row in rows]

    def add_participant(
        self, participant: Participant, *, limit: int, now: datetime
    ) -> Participant:
        with self._lock, _conflicts(), self._conn.transaction():
            # Locking the session row serializes this with every other join, update, and close
            # of the session, on any connection. The state check, the duplicate lookup, the count,
            # and the insert are then one step, so a close cannot land between them and two
            # people joining at once cannot both take the last place.
            row = self._conn.execute(
                "select closed_at, expires_at from sessions where id = %s for update",
                (participant.session_id,),
            ).fetchone()
            if row is None:
                raise SessionNotFound("This session does not exist.")
            require_open(row[0], row[1], now)
            if participant.user_id is not None:
                found = self._conn.execute(
                    f"select {_PARTICIPANT} from participants "
                    "where session_id = %s and user_id = %s",
                    (participant.session_id, participant.user_id),
                ).fetchone()
                if found is not None:
                    return _participant(found)
            (count,) = self._conn.execute(
                "select count(*) from participants where session_id = %s",
                (participant.session_id,),
            ).fetchone() or (0,)
            if count >= limit:
                raise SessionFull(f"This session already has {limit} people.")
            self._insert_participant(participant)
            return participant

    def update_participant(
        self,
        participant_id: UUID,
        *,
        display_name: str | None,
        constraints: dict[str, Any],
        preferences: dict[str, Any],
        now: datetime,
    ) -> Participant | None:
        # `updated_at` is set by the table's trigger, so `now` only gates the write here.
        with self._lock, self._conn.transaction():
            row = self._conn.execute(
                "select s.closed_at, s.expires_at from participants p "
                "join sessions s on s.id = p.session_id where p.id = %s for update of s",
                (participant_id,),
            ).fetchone()
            if row is None:
                return None
            require_open(row[0], row[1], now)
            updated = self._conn.execute(
                "update participants set display_name = %s, constraints = %s, preferences = %s "
                f"where id = %s returning {_PARTICIPANT}",
                (display_name, Jsonb(constraints), Jsonb(preferences), participant_id),
            ).fetchone()
        return None if updated is None else _participant(updated)

    def close(self, session_id: UUID, now: datetime) -> Session | None:
        with self._lock, self._conn.transaction():
            row = self._conn.execute(
                "select expires_at from sessions where id = %s for update", (session_id,)
            ).fetchone()
            if row is None:
                return None
            if row[0] is not None and row[0] <= now:
                raise SessionExpired("This session has expired.")
            closed = self._conn.execute(
                "update sessions set closed_at = coalesce(closed_at, %s) "
                f"where id = %s returning {_SESSION}",
                (now, session_id),
            ).fetchone()
        return None if closed is None else _session(closed)

    def purge_expired(self, now: datetime) -> int:
        # Participants, a guest's memory facts, and trace events go with the session by cascade.
        with self._lock:
            return self._conn.execute(
                "delete from sessions where expires_at is not null and expires_at <= %s", (now,)
            ).rowcount

    def _insert_participant(self, p: Participant) -> None:
        self._conn.execute(
            f"insert into participants ({_PARTICIPANT}) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                p.id,
                p.session_id,
                p.user_id,
                p.display_name,
                p.is_host,
                Jsonb(p.constraints),
                Jsonb(p.preferences),
                p.joined_at,
                p.updated_at,
            ),
        )


@contextmanager
def _conflicts() -> Iterator[None]:
    """Report a violated constraint as the `ValueError` every store raises."""
    try:
        yield
    except psycopg.errors.IntegrityError as exc:
        raise ValueError(exc.diag.constraint_name or "integrity error") from None


def _session(row: tuple[Any, ...]) -> Session:
    return Session(
        id=row[0],
        user_id=row[1],
        link_token=row[2],
        context=row[3],
        shared_at=row[4],
        expires_at=row[5],
        closed_at=row[6],
        created_at=row[7],
    )


def _participant(row: tuple[Any, ...]) -> Participant:
    return Participant(
        id=row[0],
        session_id=row[1],
        user_id=row[2],
        display_name=row[3],
        is_host=row[4],
        constraints=row[5],
        preferences=row[6],
        joined_at=row[7],
        updated_at=row[8],
    )
