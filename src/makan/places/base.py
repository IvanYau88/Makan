"""Place types and the provider interface.

Nothing here depends on a particular places data source.
The tool, the cache, and every provider speak only these types.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Protocol

EARTH_RADIUS_M = 6_371_000
COORD_DECIMALS = 3  # about 110 m: the finest location the harness keeps or sends on
MIN_RADIUS_M = 100
MAX_RADIUS_M = 5_000
MAX_LIMIT = 20


class PlacesError(Exception):
    """The provider could not answer the search."""


@dataclass(frozen=True)
class Place:
    id: str  # stable within one provider
    name: str
    category: str  # the most specific category, as a slug such as "thai_restaurant"
    categories: tuple[str, ...]  # every label the source gives, broad to specific
    lat: float
    lon: float
    address: str | None = None
    distance_m: int | None = None  # from the query point, set by `rank_nearby`

    def matches(self, term: str) -> bool:
        """True if every word of `term` appears in one category label.

        "thai" matches `thai_restaurant`, "fast food" matches `fast_food_restaurant`,
        and "coffee" matches `coffee_shop`.
        """
        wanted = set(_words(term))
        return bool(wanted) and any(wanted <= set(_words(label)) for label in self.categories)


@dataclass(frozen=True)
class PlaceQuery:
    lat: float
    lon: float
    radius_m: int
    cuisine: str | None = None
    category: str | None = None
    limit: int = 10

    def __post_init__(self) -> None:
        if not -90 <= self.lat <= 90:
            raise ValueError(f"latitude must be between -90 and 90, got {self.lat}")
        if not -180 <= self.lon <= 180:
            raise ValueError(f"longitude must be between -180 and 180, got {self.lon}")
        if not MIN_RADIUS_M <= self.radius_m <= MAX_RADIUS_M:
            raise ValueError(f"radius_m must be between {MIN_RADIUS_M} and {MAX_RADIUS_M}")
        if not 1 <= self.limit <= MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")

    @classmethod
    def near(
        cls,
        lat: float,
        lon: float,
        radius_m: int,
        *,
        cuisine: str | None = None,
        category: str | None = None,
        limit: int = 10,
    ) -> PlaceQuery:
        """Build a query with the location rounded to `COORD_DECIMALS`, so it stays approximate."""
        return cls(
            round(lat, COORD_DECIMALS),
            round(lon, COORD_DECIMALS),
            radius_m,
            cuisine=cuisine,
            category=category,
            limit=limit,
        )


class PlacesProvider(Protocol):
    @property
    def name(self) -> str:
        """Identifies the data source and its version, such as "overture:2026-09-23.1".

        The cache keys on it, so a new data version never serves old results.
        """
        ...

    def search_nearby(self, query: PlaceQuery) -> list[Place]:
        """Return places within the radius that match the filters, nearest first.

        Every place carries `distance_m`. Raise PlacesError on failure.
        """
        ...


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Great-circle distance in meters, rounded."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return round(2 * EARTH_RADIUS_M * math.asin(math.sqrt(a)))


def rank_nearby(places: Iterable[Place], query: PlaceQuery) -> list[Place]:
    """Apply the query to candidate places: radius, filters, nearest first, limit.

    Ties on distance break on name then id, so results are deterministic.
    """
    found: list[Place] = []
    for place in places:
        meters = distance_m(query.lat, query.lon, place.lat, place.lon)
        if meters > query.radius_m:
            continue
        if query.cuisine and not place.matches(query.cuisine):
            continue
        if query.category and not place.matches(query.category):
            continue
        found.append(replace(place, distance_m=meters))
    found.sort(key=lambda p: (p.distance_m or 0, p.name, p.id))
    return found[: query.limit]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())
