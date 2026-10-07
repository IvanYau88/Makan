"""The storage interface for memory facts, and the in-memory store used in tests.

A store only keeps and finds rows. Decay, superseding, and staleness are rules in
`makan.memory.service`, so every store behaves the same.
"""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from makan.models import MemoryFact, MemoryKind


@dataclass(frozen=True)
class Owner:
    """Whose facts these are: a signed-in user, a guest's session, or both.

    A fact belongs to a user, or to a session when there is no user, because a session
    fact goes when the session is purged and a user's facts must outlive their sessions.
    Reading returns both a user's facts and the facts of the session.
    """

    user_id: UUID | None = None
    session_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.user_id is None and self.session_id is None:
            raise ValueError("an owner needs a user id or a session id")

    @property
    def writes_to_user(self) -> bool:
        return self.user_id is not None


class MemoryStore(Protocol):
    def add(self, fact: MemoryFact, *, supersedes: Sequence[UUID] = ()) -> None:
        """Insert `fact`, and point each fact in `supersedes` at it, as one atomic change."""
        ...

    def get(self, fact_id: UUID) -> MemoryFact | None: ...

    def active(self, owner: Owner, kinds: Collection[MemoryKind] | None = None) -> list[MemoryFact]:
        """The owner's facts that nothing has superseded, newest observation first.

        Decayed and expired facts are included. Flagging them is the service's job.
        """
        ...

    def reconfirm(self, fact_id: UUID, *, confidence: float, at: datetime) -> MemoryFact | None:
        """Set the confidence and `last_confirmed_at` of a fact in force.

        None if there is no such fact or something has superseded it.
        """
        ...

    def forget(self, fact_id: UUID, *, user_id: UUID) -> bool:
        """Delete a user's fact and the history behind it, which is every fact it superseded.

        The history goes too: deleting only the newer fact would set the older one's link to
        null and put it back in force. False if the user has no such fact.
        """
        ...


class InMemoryStore:
    """A store in a dict, with the same behavior as the Postgres one. Thread safe."""

    def __init__(self) -> None:
        self._facts: dict[UUID, MemoryFact] = {}
        self._lock = threading.Lock()

    def add(self, fact: MemoryFact, *, supersedes: Sequence[UUID] = ()) -> None:
        with self._lock:
            if fact.id in self._facts:
                raise ValueError(f"fact {fact.id} already exists")
            missing = [i for i in supersedes if i not in self._facts]
            if missing:
                raise ValueError(f"cannot supersede unknown facts {missing}")
            if fact.id in supersedes:
                raise ValueError("a fact cannot supersede itself")
            self._facts[fact.id] = fact
            for old_id in supersedes:
                self._facts[old_id] = dataclasses.replace(
                    self._facts[old_id], superseded_by=fact.id
                )

    def get(self, fact_id: UUID) -> MemoryFact | None:
        with self._lock:
            return self._facts.get(fact_id)

    def active(self, owner: Owner, kinds: Collection[MemoryKind] | None = None) -> list[MemoryFact]:
        with self._lock:
            facts = [
                f
                for f in self._facts.values()
                if f.superseded_by is None
                and (kinds is None or f.kind in kinds)
                and (
                    (owner.user_id is not None and f.user_id == owner.user_id)
                    or (owner.session_id is not None and f.session_id == owner.session_id)
                )
            ]
        return sorted(facts, key=lambda f: f.observed_at, reverse=True)

    def reconfirm(self, fact_id: UUID, *, confidence: float, at: datetime) -> MemoryFact | None:
        with self._lock:
            fact = self._facts.get(fact_id)
            if fact is None or fact.superseded_by is not None:
                return None
            updated = dataclasses.replace(fact, confidence=confidence, last_confirmed_at=at)
            self._facts[fact_id] = updated
            return updated

    def forget(self, fact_id: UUID, *, user_id: UUID) -> bool:
        with self._lock:
            fact = self._facts.get(fact_id)
            if fact is None or fact.user_id != user_id:
                return False
            gone = {fact_id}
            grew = True
            while grew:
                older = {
                    f.id
                    for f in self._facts.values()
                    if f.user_id == user_id and f.superseded_by in gone and f.id not in gone
                }
                grew = bool(older)
                gone |= older
            for fact_id_ in gone:
                del self._facts[fact_id_]
            return True

    def all_for_user(self, user_id: UUID) -> list[MemoryFact]:
        """Every fact a user owns, superseded ones included, oldest first. For export."""
        with self._lock:
            facts = [f for f in self._facts.values() if f.user_id == user_id]
        return sorted(facts, key=lambda f: (f.created_at, str(f.id)))

    def delete_user(self, user_id: UUID) -> None:
        """Delete every fact a user owns, as deleting the user does in the database."""
        with self._lock:
            for fact in [f for f in self._facts.values() if f.user_id == user_id]:
                del self._facts[fact.id]
