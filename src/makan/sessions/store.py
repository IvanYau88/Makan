"""The storage interface for group sessions, and the in-memory store used in tests.

A store only keeps and finds rows. Expiry, closing, and who may do what are rules in
`makan.sessions.service`, so every store behaves the same.
"""

from __future__ import annotations

import copy
import dataclasses
import threading
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from makan.models import Participant, Session
from makan.sessions.errors import SessionExpired, SessionFull, SessionNotFound, require_open


class SessionStore(Protocol):
    def create(self, session: Session, host: Participant) -> None:
        """Insert the session and its host as one atomic change.

        Raises `ValueError` if the id or link token is taken, or the host is not the session's.
        """
        ...

    def get_by_token(self, link_token: UUID) -> Session | None: ...

    def get_participant(self, participant_id: UUID) -> Participant | None: ...

    def participants(self, session_id: UUID) -> list[Participant]:
        """Everyone in the session: the host first, then the rest in the order they joined."""
        ...

    def add_participant(
        self, participant: Participant, *, limit: int, now: datetime
    ) -> Participant:
        """Admit a participant, as one atomic step that also checks the session is open at `now`.

        Checking and writing together is what makes closing freeze joins. Raises `SessionNotFound`,
        `SessionExpired`, `SessionClosed`, or `SessionFull`, and `ValueError` for a second host.
        A participant with a `user_id` who is already in the session is not added twice, even when
        the session is full: the existing row is returned.
        """
        ...

    def update_participant(
        self,
        participant_id: UUID,
        *,
        display_name: str | None,
        constraints: dict[str, Any],
        preferences: dict[str, Any],
        now: datetime,
    ) -> Participant | None:
        """Replace the three fields and set `updated_at`, only while the session is open at `now`.

        The check and the write are one atomic step. Returns None if there is no such participant,
        and raises `SessionExpired` or `SessionClosed` otherwise.
        """
        ...

    def close(self, session_id: UUID, now: datetime) -> Session | None:
        """Set `closed_at` to `now` unless it is already set, and return the session.

        Returns None if there is no such session, and raises `SessionExpired` if it has expired.
        """
        ...

    def purge_expired(self, now: datetime) -> int:
        """Delete every session with an `expires_at` at or before `now`, and everything it owns.

        Return how many sessions went. A session with no `expires_at` never goes.
        """
        ...


class InMemorySessionStore:
    """A thread-safe store that keeps everything in dictionaries. It holds copies, never aliases."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[UUID, Session] = {}
        self._participants: dict[UUID, Participant] = {}

    def create(self, session: Session, host: Participant) -> None:
        with self._lock:
            if session.id in self._sessions or any(
                s.link_token == session.link_token for s in self._sessions.values()
            ):
                raise ValueError("session id or link token already exists")
            if host.session_id != session.id or not host.is_host:
                raise ValueError("the host must be a host participant of the session")
            self._sessions[session.id] = _copy(session)
            self._participants[host.id] = _copy(host)

    def get_by_token(self, link_token: UUID) -> Session | None:
        with self._lock:
            found = next((s for s in self._sessions.values() if s.link_token == link_token), None)
            return None if found is None else _copy(found)

    def get_participant(self, participant_id: UUID) -> Participant | None:
        with self._lock:
            found = self._participants.get(participant_id)
            return None if found is None else _copy(found)

    def participants(self, session_id: UUID) -> list[Participant]:
        with self._lock:
            members = [p for p in self._participants.values() if p.session_id == session_id]
            return [
                _copy(p) for p in sorted(members, key=lambda p: (not p.is_host, p.joined_at, p.id))
            ]

    def add_participant(
        self, participant: Participant, *, limit: int, now: datetime
    ) -> Participant:
        with self._lock:
            session = self._sessions.get(participant.session_id)
            if session is None:
                raise SessionNotFound("This session does not exist.")
            require_open(session.closed_at, session.expires_at, now)
            members = [
                p for p in self._participants.values() if p.session_id == participant.session_id
            ]
            if participant.user_id is not None:
                for existing in members:
                    if existing.user_id == participant.user_id:
                        return _copy(existing)
            if participant.is_host and any(p.is_host for p in members):
                raise ValueError("a session has one host")
            if participant.id in self._participants:
                raise ValueError("participant id already exists")
            if len(members) >= limit:
                raise SessionFull(f"This session already has {limit} people.")
            self._participants[participant.id] = _copy(participant)
            return _copy(participant)

    def update_participant(
        self,
        participant_id: UUID,
        *,
        display_name: str | None,
        constraints: dict[str, Any],
        preferences: dict[str, Any],
        now: datetime,
    ) -> Participant | None:
        with self._lock:
            current = self._participants.get(participant_id)
            if current is None:
                return None
            session = self._sessions[current.session_id]
            require_open(session.closed_at, session.expires_at, now)
            updated = dataclasses.replace(
                current,
                display_name=display_name,
                constraints=copy.deepcopy(constraints),
                preferences=copy.deepcopy(preferences),
                updated_at=now,
            )
            self._participants[participant_id] = updated
            return _copy(updated)

    def close(self, session_id: UUID, now: datetime) -> Session | None:
        with self._lock:
            current = self._sessions.get(session_id)
            if current is None:
                return None
            if current.expires_at is not None and current.expires_at <= now:
                raise SessionExpired("This session has expired.")
            if current.closed_at is None:
                current = dataclasses.replace(current, closed_at=now)
                self._sessions[session_id] = current
            return _copy(current)

    def purge_expired(self, now: datetime) -> int:
        with self._lock:
            gone = {
                s.id
                for s in self._sessions.values()
                if s.expires_at is not None and s.expires_at <= now
            }
            for session_id in gone:
                del self._sessions[session_id]
            self._participants = {
                pid: p for pid, p in self._participants.items() if p.session_id not in gone
            }
            return len(gone)


def _copy[T](row: T) -> T:
    return copy.deepcopy(row)
