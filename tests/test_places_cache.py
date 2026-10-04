import pytest

from makan.places import CachedPlacesProvider, FakePlacesProvider, PlaceQuery, PlacesError
from tests.helpers import place

CAFE = place("Mid Cafe", "cafe", 0.0, 0.002, "cafe")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def q(radius_m: int = 1000, cuisine: str | None = None) -> PlaceQuery:
    return PlaceQuery.near(0.0, 0.0, radius_m, cuisine=cuisine)


def test_a_repeated_search_is_answered_from_memory() -> None:
    inner = FakePlacesProvider([CAFE])
    cached = CachedPlacesProvider(inner)
    first = cached.search_nearby(q())
    assert cached.search_nearby(q()) == first
    assert len(inner.queries) == 1


def test_different_queries_are_looked_up_separately() -> None:
    inner = FakePlacesProvider([CAFE])
    cached = CachedPlacesProvider(inner)
    cached.search_nearby(q())
    cached.search_nearby(q(2000))
    cached.search_nearby(q(cuisine="thai"))
    assert len(inner.queries) == 3


def test_entries_expire_after_the_ttl() -> None:
    clock = Clock()
    inner = FakePlacesProvider([CAFE])
    cached = CachedPlacesProvider(inner, ttl_seconds=60, clock=clock)
    cached.search_nearby(q())
    clock.now += 59
    cached.search_nearby(q())
    assert len(inner.queries) == 1
    clock.now += 1
    cached.search_nearby(q())
    assert len(inner.queries) == 2


def test_failures_are_not_cached() -> None:
    inner = FakePlacesProvider(error=PlacesError("down"))
    cached = CachedPlacesProvider(inner)
    for _ in range(2):
        with pytest.raises(PlacesError):
            cached.search_nearby(q())
    assert len(inner.queries) == 2


def test_the_oldest_entry_is_dropped_when_full() -> None:
    inner = FakePlacesProvider([CAFE])
    cached = CachedPlacesProvider(inner, max_entries=2)
    cached.search_nearby(q(100))
    cached.search_nearby(q(200))
    cached.search_nearby(q(300))  # evicts the 100 m search
    cached.search_nearby(q(300))
    cached.search_nearby(q(200))
    assert len(inner.queries) == 3
    cached.search_nearby(q(100))
    assert len(inner.queries) == 4


def test_callers_cannot_change_what_is_cached() -> None:
    cached = CachedPlacesProvider(FakePlacesProvider([CAFE]))
    cached.search_nearby(q()).clear()
    assert len(cached.search_nearby(q())) == 1


def test_a_new_data_version_does_not_serve_old_results() -> None:
    inner = FakePlacesProvider([CAFE], name="fake:1")
    cached = CachedPlacesProvider(inner)
    assert cached.name == "fake:1"
    cached.search_nearby(q())
    inner.name = "fake:2"  # the source moved to a new release
    cached.search_nearby(q())
    assert len(inner.queries) == 2
