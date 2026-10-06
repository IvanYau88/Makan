"""Runtime configuration, read from the environment.

Model names and loop limits live here, never in the loop itself.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from makan.graph import GraphLimits
from makan.loop import Limits


class ConfigError(Exception):
    """A required setting is missing or malformed."""


# OpenStreetMap's own tiles, a best effort service for light use that needs visible attribution.
# A real deployment should point MAKAN_MAP_TILE_URL at a provider it has an agreement with.
DEFAULT_MAP_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
DEFAULT_MAP_ATTRIBUTION = "© OpenStreetMap contributors"
DEFAULT_MAP_ATTRIBUTION_URL = "https://www.openstreetmap.org/copyright"
SCORER_BACKENDS = ("none", "logprob", "structured", "jev", "fake")
# These call a hosted model, so they need a configured model name and an API key.
HOSTED_SCORER_BACKENDS = ("logprob", "structured", "jev")


@dataclass(frozen=True)
class ScorerConfig:
    """Which fixed-answer scorer backs the soft decision points. `none` leaves them off."""

    backend: str = "none"
    model: str = ""  # a hosted backend's model, such as a logprob-capable chat model or Jev
    timeout_s: float = 15.0

    def __post_init__(self) -> None:
        if self.backend not in SCORER_BACKENDS:
            raise ConfigError(
                f"MAKAN_SCORER_BACKEND must be one of {', '.join(SCORER_BACKENDS)}, "
                f"got {self.backend!r}"
            )
        if self.backend in HOSTED_SCORER_BACKENDS and not self.model:
            raise ConfigError(f"MAKAN_SCORER_MODEL is not set for the {self.backend} scorer")


@dataclass(frozen=True)
class Config:
    model: str
    openrouter_api_key: str = field(default="", repr=False)
    limits: Limits = field(default_factory=Limits)
    graph_limits: GraphLimits = field(default_factory=GraphLimits)
    overture_release: str = ""  # empty means the latest release
    places_cache_ttl_seconds: int = 86_400
    session_retention_hours: int = 24  # how long a group session lives after it is created
    database_url: str = field(default="", repr=False)  # empty keeps group sessions in memory
    map_tile_url: str = DEFAULT_MAP_TILE_URL  # a raster tile template with {z}, {x} and {y}
    map_attribution: str = DEFAULT_MAP_ATTRIBUTION  # shown beside the map whenever it is
    map_attribution_url: str = DEFAULT_MAP_ATTRIBUTION_URL  # where that attribution links, or ""
    scorer: ScorerConfig = field(default_factory=ScorerConfig)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        model = env.get("MAKAN_MODEL", "").strip()
        if not model:
            raise ConfigError("MAKAN_MODEL is not set")
        defaults = Limits()
        graph_defaults = GraphLimits()
        tile_url = env.get("MAKAN_MAP_TILE_URL", "").strip()
        attribution = env.get("MAKAN_MAP_ATTRIBUTION", "").strip()
        attribution_url = env.get("MAKAN_MAP_ATTRIBUTION_URL", "").strip()
        if tile_url:
            if not all(f"{{{axis}}}" in tile_url for axis in "zxy"):
                raise ConfigError("MAKAN_MAP_TILE_URL must contain {z}, {x} and {y}")
            if not attribution:
                raise ConfigError("MAKAN_MAP_ATTRIBUTION is required with MAKAN_MAP_TILE_URL")
        else:
            tile_url = DEFAULT_MAP_TILE_URL
            attribution = attribution or DEFAULT_MAP_ATTRIBUTION
            attribution_url = attribution_url or DEFAULT_MAP_ATTRIBUTION_URL
        return cls(
            model=model,
            openrouter_api_key=env.get("OPENROUTER_API_KEY", "").strip(),
            limits=Limits(
                max_iterations=_int(env, "MAKAN_MAX_ITERATIONS", defaults.max_iterations),
                token_budget=_int(env, "MAKAN_TOKEN_BUDGET", defaults.token_budget),
            ),
            graph_limits=GraphLimits(
                max_concurrency=_int(
                    env, "MAKAN_GRAPH_MAX_CONCURRENCY", graph_defaults.max_concurrency
                ),
                step_timeout_s=_int(
                    env, "MAKAN_GRAPH_STEP_TIMEOUT_SECONDS", int(graph_defaults.step_timeout_s)
                ),
            ),
            overture_release=env.get("MAKAN_OVERTURE_RELEASE", "").strip(),
            places_cache_ttl_seconds=_int(
                env, "MAKAN_PLACES_CACHE_TTL_SECONDS", cls.places_cache_ttl_seconds
            ),
            session_retention_hours=_int(
                env, "MAKAN_SESSION_RETENTION_HOURS", cls.session_retention_hours
            ),
            database_url=env.get("MAKAN_DATABASE_URL", "").strip(),
            map_tile_url=tile_url,
            map_attribution=attribution,
            map_attribution_url=attribution_url,
            scorer=ScorerConfig(
                backend=env.get("MAKAN_SCORER_BACKEND", "").strip().lower() or "none",
                model=env.get("MAKAN_SCORER_MODEL", "").strip(),
                timeout_s=float(_int(env, "MAKAN_SCORER_TIMEOUT_SECONDS", 15)),
            ),
        )


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if value < 1:
        raise ConfigError(f"{name} must be at least 1, got {value}")
    return value
