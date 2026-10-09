"""Typed models that mirror the tables in `migrations/`, one dataclass per table.

Field names are column names. A column that allows null is `... | None`, and
`tests/test_schema.py` fails against a live Postgres if a model and the migrated tables
drift apart. The models hold rows and carry no behavior beyond converting trace events,
so memory rules such as decay and superseding belong to the memory component, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal, Self, get_args
from uuid import UUID

from makan.trace import SCHEMA_VERSION, TraceEvent

MemoryKind = Literal["cuisine_like", "cuisine_dislike", "constraint", "place_rating"]
MemorySource = Literal["stated", "observed"]

Party = Literal["solo", "with_others"]
RatingOrigin = Literal["fresh", "confirmed"]
TagStatus = Literal["pending", "accepted", "declined"]

MEMORY_KINDS: tuple[str, ...] = get_args(MemoryKind)
MEMORY_SOURCES: tuple[str, ...] = get_args(MemorySource)
PARTIES: tuple[str, ...] = get_args(Party)
RATING_ORIGINS: tuple[str, ...] = get_args(RatingOrigin)
TAG_STATUSES: tuple[str, ...] = get_args(TagStatus)


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
    closed_at: datetime | None = None


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
class GoingHere:
    """A marker that the user is about to eat at a place, with the window that follows it."""

    id: UUID
    user_id: UUID
    place_source: str
    place_id: str
    place_name: str
    planned_on: date
    started_at: datetime
    remind_at: datetime
    place_address: str | None = None
    reminded_at: datetime | None = None
    visit_id: UUID | None = None
    cancelled_at: datetime | None = None


@dataclass(frozen=True)
class Visit:
    """One meal at one restaurant, owned by the person who ate it.

    Ratings are exact decimals with one decimal place. `rating` is None only on a visit that
    came from accepting a tag and has not been rated by its owner yet.
    """

    id: UUID
    user_id: UUID
    place_source: str
    place_id: str
    place_name: str
    visited_on: date
    party: Party
    created_at: datetime
    updated_at: datetime
    place_address: str | None = None
    rating: Decimal | None = None
    rating_origin: RatingOrigin | None = None
    description: str | None = None
    tagged_by_user_id: UUID | None = None


@dataclass(frozen=True)
class VisitDish:
    id: UUID
    visit_id: UUID
    user_id: UUID
    position: int
    name: str
    rating: Decimal
    rating_origin: RatingOrigin
    tags: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    comment: str | None = None
    source_dish_id: UUID | None = None


@dataclass(frozen=True)
class VisitTag:
    """A request from the owner of `visit_id` to `tagged_user_id` to confirm they ate together."""

    id: UUID
    visit_id: UUID
    tagger_id: UUID
    tagged_user_id: UUID
    status: TagStatus
    created_at: datetime
    accepted_visit_id: UUID | None = None
    responded_at: datetime | None = None


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
