"""Typed models that mirror the tables in `migrations/`, one dataclass per table.

Field names are column names. A column that allows null is `... | None`, and
`tests/test_schema.py` fails if a model and a migration drift apart. The models hold
rows and carry no behavior beyond converting trace events, so memory rules such as
decay and superseding belong to the memory component, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Self, get_args
from uuid import UUID

from makan.trace import SCHEMA_VERSION, TraceEvent

MemoryKind = Literal["cuisine_like", "cuisine_dislike", "constraint", "place_rating"]
MemorySource = Literal["stated", "observed"]

MEMORY_KINDS: tuple[str, ...] = get_args(MemoryKind)
MEMORY_SOURCES: tuple[str, ...] = get_args(MemorySource)


def _utc(value: datetime) -> datetime:
    """A naive timestamp is taken as UTC, and an aware one is converted to UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class User:
    id: UUID
    created_at: datetime


@dataclass(frozen=True)
class Profile:
    user_id: UUID
    location_history_opt_in: bool
    created_at: datetime
    updated_at: datetime
    display_name: str | None = None


@dataclass(frozen=True)
class Session:
    id: UUID
    link_token: UUID
    context: dict[str, Any]
    created_at: datetime
    user_id: UUID | None = None
    shared_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class Participant:
    id: UUID
    session_id: UUID
    is_host: bool
    constraints: dict[str, Any]
    preferences: dict[str, Any]
    joined_at: datetime
    updated_at: datetime
    user_id: UUID | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class MemoryFact:
    id: UUID
    kind: MemoryKind
    content: Any
    source: MemorySource
    confidence: float
    observed_at: datetime
    last_confirmed_at: datetime
    created_at: datetime
    user_id: UUID | None = None
    session_id: UUID | None = None
    expires_at: datetime | None = None
    superseded_by: UUID | None = None


@dataclass(frozen=True)
class TraceEventRow:
    """A persisted trace event: the fields of `makan.trace.TraceEvent` plus its owner."""

    run_id: str
    seq: int
    v: int
    ts: datetime
    type: str
    data: dict[str, Any]
    session_id: UUID | None = None
    user_id: UUID | None = None

    @classmethod
    def from_event(
        cls, event: TraceEvent, *, session_id: UUID | None = None, user_id: UUID | None = None
    ) -> Self:
        return cls(
            run_id=event.run_id,
            seq=event.seq,
            v=SCHEMA_VERSION,
            ts=_utc(datetime.fromisoformat(event.ts)),
            type=event.type,
            data=event.data,
            session_id=session_id,
            user_id=user_id,
        )

    @classmethod
    def from_dict(
        cls,
        event: dict[str, Any],
        *,
        session_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> Self:
        """Build a row from one decoded JSONL line, as `makan.trace.read_jsonl` returns."""
        return cls(
            run_id=event["run_id"],
            seq=event["seq"],
            v=event["v"],
            ts=_utc(datetime.fromisoformat(event["ts"])),
            type=event["type"],
            data=event["data"],
            session_id=session_id,
            user_id=user_id,
        )

    def to_event(self) -> TraceEvent:
        return TraceEvent(
            run_id=self.run_id,
            seq=self.seq,
            type=self.type,
            data=self.data,
            ts=_utc(self.ts).isoformat(),
        )
