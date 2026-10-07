"""Saved group sessions: the store interface, the rules over it, and the in-memory store.

`PostgresSessionStore` is in `makan.sessions.postgres`, so this package never needs psycopg.
"""

from makan.sessions.errors import (
    NotAParticipant,
    NotHost,
    ParticipantRequired,
    SessionClosed,
    SessionError,
    SessionExpired,
    SessionFull,
    SessionNotFound,
    SessionOpen,
)
from makan.sessions.service import (
    DEFAULT_RETENTION,
    MAX_PARTICIPANTS,
    MAX_REQUEST_CHARS,
    GroupSessions,
    SessionView,
    purge_expired_sessions,
)
from makan.sessions.store import InMemorySessionStore, SessionStore

__all__ = [
    "DEFAULT_RETENTION",
    "MAX_PARTICIPANTS",
    "MAX_REQUEST_CHARS",
    "GroupSessions",
    "InMemorySessionStore",
    "NotAParticipant",
    "NotHost",
    "ParticipantRequired",
    "SessionClosed",
    "SessionError",
    "SessionExpired",
    "SessionFull",
    "SessionNotFound",
    "SessionOpen",
    "SessionStore",
    "SessionView",
    "purge_expired_sessions",
]
