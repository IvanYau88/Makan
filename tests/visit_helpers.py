"""Fixtures for the visit logging tests: a clock, a world of people, and places."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from makan.accounts import InMemoryAccountStore
from makan.visits import DishInput, PlaceRef, Visits, VisitView, parse_place
from makan.visits.store import InMemoryVisitStore

START = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
THAI = parse_place("overture:2026-09-23.1", "g-thai", "Mid Thai", "1 Jalan Satu")
RAMEN = parse_place("overture:2026-09-23.1", "g-ramen", "Near Ramen")


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, by: timedelta) -> None:
        self.now += by


class World:
    """The visit rules over one backend, with people who exist on the platform."""

    def __init__(self, people: Any, store: Any, db: Any = None) -> None:
        self.clock = Clock()
        self.people = people
        self.store = store
        self.db = db
        self.visits = Visits(store, people, clock=self.clock)

    @classmethod
    def in_memory(cls) -> World:
        people = InMemoryAccountStore()
        assert isinstance(people.visits, InMemoryVisitStore)
        return cls(people, people.visits)

    @classmethod
    def on_postgres(cls, db: Any) -> World:
        from makan.accounts.postgres import PostgresAccountStore
        from makan.visits.postgres import PostgresVisitStore

        return cls(PostgresAccountStore(db), PostgresVisitStore(db), db)

    def user(self, name: str | None = "Alice") -> UUID:
        user_id = uuid4()
        self.people.create_user(user_id)
        if name is not None:
            self.people.save_profile(user_id, display_name=name, location_history_opt_in=None)
        return user_id

    def visit(
        self,
        owner: UUID,
        *,
        place: PlaceRef = THAI,
        rating: object = 8.5,
        party: str = "with_others",
        dishes: tuple[DishInput, ...] = (),
        **kwargs: Any,
    ) -> VisitView:
        return self.visits.log_visit(
            owner, place=place, rating=rating, party=party, dishes=dishes, **kwargs
        )

    def tagged(
        self, tagger: UUID, friend: UUID, *, dishes: tuple[DishInput, ...] = (), **kwargs: Any
    ) -> tuple[VisitView, UUID]:
        """The tagger's visit with the friend tagged, and the id of the request."""
        view = self.visit(tagger, dishes=dishes, description="great night", **kwargs)
        tag = self.visits.tag(tagger, view.visit.id, friend, acknowledged_sharing=True)
        return self.visits.visit(tagger, view.visit.id), tag.tag.id

    def count(self, table: str, where: str = "true", *args: Any) -> int:
        """A row count straight from Postgres. Only for the Postgres variants."""
        row = self.db.execute(f"select count(*) from {table} where {where}", args).fetchone()
        return int(row[0])


SOUP = DishInput("Tom Yum", 7.5, ["spicy", "not spicy", "lunch"], "too sour")
CURRY = DishInput("Green Curry", "6.0", [], None)
