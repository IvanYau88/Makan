"""Build the places provider the configuration asks for."""

from __future__ import annotations

import httpx

from makan.config import Config
from makan.places.base import PlacesProvider
from makan.places.cache import CachedPlacesProvider
from makan.places.overture import DuckDbLimits, DuckDbSource, OvertureProvider

CATALOG_TIMEOUT_S = 5.0  # the latest-release lookup, kept short so it fits inside the deadline


def places_provider(config: Config) -> PlacesProvider:
    """Overture behind the cache. A paid provider would be picked here, and only here.

    Nothing is opened here: DuckDB, its extension, and the release lookup wait for the first
    search, so building the provider needs no network and no writable disk.
    """
    limits = DuckDbLimits(
        threads=config.duckdb.threads,
        memory_mb=config.duckdb.memory_mb,
        work_dir=config.duckdb.work_dir,
        deadline_s=config.duckdb.deadline_s,
    )
    return CachedPlacesProvider(
        OvertureProvider(
            config.overture_release or None,
            source=DuckDbSource(limits=limits),
            client=httpx.Client(timeout=CATALOG_TIMEOUT_S),
        ),
        ttl_seconds=config.places_cache_ttl_seconds,
    )
