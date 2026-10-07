"""The memory rules, on top of any `MemoryStore`.

- Confidence decays with age unless the user reconfirms the fact.
- A newer fact that contradicts an older one supersedes it, and the old row keeps a link to it.
- Stale facts come back flagged, so the caller can ask the user instead of using them silently.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from makan.memory.content import Claim, claim
from makan.memory.policy import MemoryPolicy
from makan.memory.store import MemoryStore, Owner
from makan.models import MemoryFact, MemoryKind, MemorySource

Outcome = Literal["created", "confirmed", "superseded"]
StaleReason = Literal["expired", "low_confidence"]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class RecalledFact:
    """A fact as it reads now: `confidence` is decayed, and `stale` says not to trust it."""

    fact: MemoryFact
    confidence: float
    stale: StaleReason | None = None


@dataclass(frozen=True)
class Remembered:
    outcome: Outcome
    fact: MemoryFact  # the fact now in force
    superseded: tuple[MemoryFact, ...] = ()  # the facts it replaced, as they were


class Memory:
    def __init__(
        self,
        store: MemoryStore,
        policy: MemoryPolicy | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store = store
        self.policy = policy or MemoryPolicy()
        self._clock = clock

    def now(self) -> datetime:
        return self._clock()

    def recall(
        self, owner: Owner, kinds: Collection[MemoryKind] | None = None
    ) -> list[RecalledFact]:
        """The owner's facts in force, trustworthy ones first and then by confidence."""
        now = self._clock()
        recalled = [self._read(f, now) for f in self.store.active(owner, kinds)]
        return sorted(recalled, key=lambda r: (r.stale is not None, -r.confidence))

    def remember(
        self,
        owner: Owner,
        kind: MemoryKind,
        content: object,
        *,
        source: MemorySource = "stated",
        expires_at: datetime | None = None,
    ) -> Remembered:
        """Record a fact. `content` is checked and normalized for `kind`; see `content.claim`.

        Saying a fact again reconfirms the one in force. A different value for the same
        subject supersedes what is in force, and the new fact starts at the confidence for
        its `source`. A fact whose expiry has passed is replaced, not reconfirmed.
        """
        new = claim(kind, content)
        now = self._clock()
        # Facts of any kind about the same subject, so a dislike can replace a like.
        related: list[MemoryFact] = []
        for fact in self.store.active(owner):
            old = _claim(fact)
            if old is None or old.subject != new.subject:
                continue
            if old.says_same_as(new) and not _expired(fact, now):
                return Remembered("confirmed", self._reconfirm(fact, source, now))
            related.append(fact)
        created = MemoryFact(
            id=uuid4(),
            kind=kind,
            content=new.content,
            source=source,
            confidence=self.policy.initial_confidence[source],
            observed_at=now,
            last_confirmed_at=now,
            created_at=now,
            user_id=owner.user_id,
            session_id=None if owner.writes_to_user else owner.session_id,
            expires_at=expires_at,
        )
        self.store.add(created, supersedes=[f.id for f in related])
        outcome: Outcome = "superseded" if related else "created"
        return Remembered(outcome, created, tuple(related))

    def confirm(self, fact_id: UUID, *, source: MemorySource = "stated") -> MemoryFact:
        """The user reconfirmed a fact, so its decay starts over."""
        fact = self.store.get(fact_id)
        if fact is None or fact.superseded_by is not None:
            raise ValueError("no such fact in force")
        return self._reconfirm(fact, source, self._clock())

    def forget(self, user_id: UUID, fact_id: UUID) -> bool:
        """The user takes a fact back. It and its history are deleted, not kept as superseded."""
        return self.store.forget(fact_id, user_id=user_id)

    def _reconfirm(self, fact: MemoryFact, source: MemorySource, now: datetime) -> MemoryFact:
        # Never lower what the fact is worth now, and never go below what this source earns.
        confidence = max(
            self.policy.confidence_at(fact, now), self.policy.initial_confidence[source]
        )
        updated = self.store.reconfirm(
            fact.id, confidence=confidence, at=max(now, fact.observed_at)
        )
        if updated is None:
            raise ValueError("no such fact in force")
        return updated

    def _read(self, fact: MemoryFact, now: datetime) -> RecalledFact:
        confidence = self.policy.confidence_at(fact, now)
        stale: StaleReason | None = None
        if _expired(fact, now):
            stale = "expired"
        elif confidence < self.policy.stale_below:
            stale = "low_confidence"
        return RecalledFact(fact, confidence, stale)


def _expired(fact: MemoryFact, now: datetime) -> bool:
    return fact.expires_at is not None and fact.expires_at <= now


def _claim(fact: MemoryFact) -> Claim | None:
    try:
        return claim(fact.kind, fact.content)
    except ValueError:  # a row some other writer made, which cannot be read as a claim
        return None
