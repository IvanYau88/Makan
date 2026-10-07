"""The rules of a group session: who may do what, and when a session is over.

A group session is a `Session` with one host and any number of friends who join through its
random link token, with no account. The token is the shared secret for reading and joining. Each
person also gets a participant token, which is their own participant id: a random UUID that is
returned only to them, and that the host's actions and everyone's own updates require. No endpoint
ever lists another participant's id.

Expiry is enforced here and not left to the purge. A session whose `expires_at` has passed is
treated as gone for everything, and a null `expires_at` never expires. `retention` is how long a
new session lives, and None creates sessions that never expire. A closed session still reads, but
nobody can join it or change their inputs, and only the host can close it. The host can ask for the
result at any time, and once the session is closed every participant can.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from makan.consensus import InvalidInputs, clean_name, parse_constraints, parse_preferences
from makan.memory.service import utc_now
from makan.models import Participant, Session
from makan.places.base import PlaceQuery
from makan.sessions.errors import (
    NotAParticipant,
    NotHost,
    ParticipantRequired,
    SessionClosed,
    SessionExpired,
    SessionNotFound,
    SessionOpen,
)
from makan.sessions.store import SessionStore

DEFAULT_RETENTION = timedelta(hours=24)
MAX_PARTICIPANTS = 20
MAX_REQUEST_CHARS = 500


@dataclass(frozen=True)
class SessionView:
    """What anyone holding the link may see, plus the caller's own row when they sent a token."""

    session: Session
    participants: tuple[Participant, ...]
    you: Participant | None


class GroupSessions:
    def __init__(
        self,
        store: SessionStore,
        *,
        retention: timedelta | None = DEFAULT_RETENTION,
        max_participants: int = MAX_PARTICIPANTS,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if retention is not None and retention <= timedelta(0):
            raise ValueError("retention must be positive, or None for no expiry")
        if max_participants < 1:
            raise ValueError("max_participants must be at least 1")
        self._store = store
        self._retention = retention
        self._max = max_participants
        self._clock = clock

    def create(
        self,
        *,
        latitude: float,
        longitude: float,
        request: str,
        radius_m: int = 1000,
        host_name: str | None = None,
        user_id: UUID | None = None,
    ) -> tuple[Session, Participant]:
        """Start a shared session. The location is rounded as the solo workflow rounds it."""
        try:
            query = PlaceQuery.near(latitude, longitude, radius_m)
        except ValueError as exc:
            raise InvalidInputs(str(exc)) from None
        request = " ".join(request.split())
        if not request:
            raise InvalidInputs("request must be non-empty text")
        if len(request) > MAX_REQUEST_CHARS:
            raise InvalidInputs(f"request must be at most {MAX_REQUEST_CHARS} characters")
        now = self._clock()
        session = Session(
            id=uuid4(),
            link_token=uuid4(),
            created_at=now,
            user_id=user_id,
            shared_at=now,  # creating a group session is sharing its link
            expires_at=None if self._retention is None else now + self._retention,
            context={
                "latitude": query.lat,
                "longitude": query.lon,
                "radius_m": query.radius_m,
                "request": request,
            },
        )
        host = self._new_participant(session, is_host=True, name=host_name, user_id=user_id)
        self._store.create(session, host)
        return session, host

    def now(self) -> datetime:
        """The time as this service reads it, so what is kept beside a session expires with it."""
        return self._clock()

    def view(self, link_token: str, participant_token: str | None = None) -> SessionView:
        """Read a session by its link. An open or closed session reads, an expired one does not."""
        session = self._live(link_token)
        you = None if participant_token is None else self._member(session, participant_token)
        return SessionView(session, tuple(self._store.participants(session.id)), you)

    def join(
        self, link_token: str, *, display_name: str | None = None, user_id: UUID | None = None
    ) -> tuple[Session, Participant]:
        """Add a friend as a guest. A signed-in user joining twice gets their first row back."""
        session = self._open(link_token)
        participant = self._new_participant(
            session, is_host=False, name=display_name, user_id=user_id
        )
        # The store checks that the session is still open as part of the write, so a close or an
        # expiry that lands after the check above still keeps this person out. For a signed-in
        # user it also returns the row they already have, and that lookup is atomic too.
        admitted = self._store.add_participant(participant, limit=self._max, now=self._clock())
        return session, admitted

    def submit(
        self,
        link_token: str,
        participant_token: str | None,
        *,
        constraints: Mapping[str, Any],
        preferences: Mapping[str, Any],
        display_name: str | None = None,
    ) -> Participant:
        """Replace the caller's constraints and preferences, and their name when one is given.

        Both are validated and stored in full shape, so a participant who has submitted is told
        from one who has not by the stored constraints and preferences being non-empty.
        """
        session = self._open(link_token)
        participant = self._member(session, _required(participant_token))
        stored = self._store.update_participant(
            participant.id,
            display_name=(
                participant.display_name if display_name is None else clean_name(display_name)
            ),
            constraints=parse_constraints(constraints).to_json(),
            preferences=parse_preferences(preferences).to_json(),
            now=self._clock(),
        )
        if stored is None:
            raise NotAParticipant("This participant is no longer in the session.")
        return stored

    def close(self, link_token: str, participant_token: str | None) -> Session:
        """Close the session, host only. Closing twice keeps the first closing time."""
        session = self._live(link_token)
        self._host(session, participant_token)
        closed = self._store.close(session.id, self._clock())
        if closed is None:
            raise SessionNotFound("This session does not exist.")
        return closed

    def host_inputs(
        self, link_token: str, participant_token: str | None
    ) -> tuple[Session, list[Participant]]:
        """The session and everyone in it, for the host asking for the group result.

        A closed session still gives its result, since closing freezes the inputs.
        """
        session = self._live(link_token)
        self._host(session, participant_token)
        return session, self._store.participants(session.id)

    def closed_inputs(
        self, link_token: str, participant_token: str | None
    ) -> tuple[Session, list[Participant], Participant]:
        """The session, everyone in it, and the caller, for anyone in a closed session's result.

        The group's result is only given once the host has closed the session, because until then
        the inputs can still change. The caller must be a participant, host or not.
        """
        session = self._live(link_token)
        you = self._member(session, _required(participant_token))
        if session.closed_at is None:
            raise SessionOpen("The host has not closed this group yet.")
        return session, self._store.participants(session.id), you

    def purge_expired(self) -> int:
        """Delete every expired session. The web app runs this on a schedule (makan.web.purge)."""
        return purge_expired_sessions(self._store, self._clock())

    def _live(self, link_token: str) -> Session:
        session = self._store.get_by_token(_parse_token(link_token))
        if session is None:
            raise SessionNotFound("This session does not exist. Check the link.")
        if session.expires_at is not None and session.expires_at <= self._clock():
            raise SessionExpired("This session has expired.")
        return session

    def _open(self, link_token: str) -> Session:
        session = self._live(link_token)
        if session.closed_at is not None:
            raise SessionClosed("The host has closed this session.")
        return session

    def _member(self, session: Session, participant_token: str) -> Participant:
        try:
            participant = self._store.get_participant(UUID(participant_token))
        except ValueError:
            participant = None
        if participant is None or participant.session_id != session.id:
            raise NotAParticipant("This is not a participant of the session.")
        return participant

    def _host(self, session: Session, participant_token: str | None) -> Participant:
        participant = self._member(session, _required(participant_token))
        if not participant.is_host:
            raise NotHost("Only the host can do this.")
        return participant

    def _new_participant(
        self, session: Session, *, is_host: bool, name: str | None, user_id: UUID | None
    ) -> Participant:
        now = self._clock()
        return Participant(
            id=uuid4(),
            session_id=session.id,
            is_host=is_host,
            constraints={},
            preferences={},
            joined_at=now,
            updated_at=now,
            user_id=user_id,
            display_name=None if name is None else clean_name(name),
        )


def purge_expired_sessions(store: SessionStore, now: datetime | None = None) -> int:
    """Delete the expired sessions in `store` and return how many went.

    This is the purge the schema leaves to the application. It is a plain function so a scheduler,
    a CLI, or a test can call it. The web app runs it at startup and on an interval.
    """
    return store.purge_expired(utc_now() if now is None else now)


def _parse_token(token: str) -> UUID:
    try:
        return UUID(token)
    except ValueError:
        raise SessionNotFound("This session does not exist. Check the link.") from None


def _required(participant_token: str | None) -> str:
    if not participant_token:
        raise ParticipantRequired("This needs your participant token.")
    return participant_token
