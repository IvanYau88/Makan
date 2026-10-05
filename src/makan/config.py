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

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        model = env.get("MAKAN_MODEL", "").strip()
        if not model:
            raise ConfigError("MAKAN_MODEL is not set")
        defaults = Limits()
        graph_defaults = GraphLimits()
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
