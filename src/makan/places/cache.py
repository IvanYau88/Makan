"""An in-memory cache of places lookups.

It lives only as long as the process, so no record of where anyone searched is kept.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable

from makan.places.base import Place, PlaceQuery, PlacesProvider


class CachedPlacesProvider:
    """Wraps a provider and remembers answers for `ttl_seconds`, up to `max_entries` of them.

    Failures are never cached. The oldest entry is dropped when the cache is full.
    """

    def __init__(
        self,
        inner: PlacesProvider,
        *,
        ttl_seconds: float = 86_400,
        max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[tuple[str, PlaceQuery], tuple[float, list[Place]]] = (
            OrderedDict()
        )
        self._lock = threading.Lock()  # graph workflows will search from several threads

    @property
    def name(self) -> str:
        return self._inner.name

    def search_nearby(self, query: PlaceQuery) -> list[Place]:
        key = (self._inner.name, query)  # the name carries the data version
        now = self._clock()
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None and now - hit[0] < self._ttl:
                return list(hit[1])
        places = self._inner.search_nearby(query)
        with self._lock:
            self._entries[key] = (now, places)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return list(places)
