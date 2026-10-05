"""The ways a session rule can fail. Stores and the service raise these, and web maps them.

Each has a stable `code`, and its message is safe to show a person.
"""

from __future__ import annotations

from datetime import datetime


class SessionError(Exception):
    code = "session_error"


class SessionNotFound(SessionError):
    code = "session_not_found"


class SessionExpired(SessionError):
    code = "session_expired"


class SessionClosed(SessionError):
    code = "session_closed"


class SessionFull(SessionError):
    code = "session_full"


class ParticipantRequired(SessionError):
    code = "participant_required"


class NotAParticipant(SessionError):
    code = "not_a_participant"


class NotHost(SessionError):
    code = "host_only"


def require_open(closed_at: datetime | None, expires_at: datetime | None, now: datetime) -> None:
    """Raise unless a session is open at `now`. A store calls this under the lock of its write.

    The check and the write must be one step, or a close or an expiry could land between them and
    the write would change a session that is meant to be frozen.
    """
    if expires_at is not None and expires_at <= now:
        raise SessionExpired("This session has expired.")
    if closed_at is not None:
        raise SessionClosed("The host has closed this session.")
