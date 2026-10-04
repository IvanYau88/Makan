"""A fixed-list places provider for tests. It never touches the network."""

from __future__ import annotations

from collections.abc import Iterable

from makan.places.base import Place, PlaceQuery, PlacesError, rank_nearby


class FakePlacesProvider:
    """Serves a fixed list of places through the real ranking, and records every query."""

    def __init__(
        self,
        places: Iterable[Place] = (),
        *,
        name: str = "fake",
        error: PlacesError | None = None,
    ) -> None:
        self.name = name
        self._places = list(places)
        self._error = error
        self.queries: list[PlaceQuery] = []

    def search_nearby(self, query: PlaceQuery) -> list[Place]:
        self.queries.append(query)
        if self._error is not None:
            raise self._error
        return rank_nearby(self._places, query)
