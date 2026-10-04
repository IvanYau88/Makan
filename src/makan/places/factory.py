"""Build the places provider the configuration asks for."""

from __future__ import annotations

from makan.config import Config
from makan.places.base import PlacesProvider
from makan.places.cache import CachedPlacesProvider
from makan.places.overture import OvertureProvider


def places_provider(config: Config) -> PlacesProvider:
    """Overture behind the cache. A paid provider would be picked here, and only here."""
    return CachedPlacesProvider(
        OvertureProvider(config.overture_release or None),
        ttl_seconds=config.places_cache_ttl_seconds,
    )
