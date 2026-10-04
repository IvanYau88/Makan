from makan.config import Config
from makan.places import CachedPlacesProvider, places_provider


def test_the_configured_provider_is_overture_behind_the_cache() -> None:
    provider = places_provider(Config(model="m", overture_release="2026-09-23.1"))
    assert isinstance(provider, CachedPlacesProvider)
    assert provider.name == "overture:2026-09-23.1"  # pinned, so no catalog lookup
