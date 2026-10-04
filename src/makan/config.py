"""Runtime configuration, read from the environment.

Model names and loop limits live here, never in the loop itself.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from makan.loop import Limits


class ConfigError(Exception):
    """A required setting is missing or malformed."""


@dataclass(frozen=True)
class Config:
    model: str
    openrouter_api_key: str = field(default="", repr=False)
    limits: Limits = field(default_factory=Limits)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        model = env.get("MAKAN_MODEL", "").strip()
        if not model:
            raise ConfigError("MAKAN_MODEL is not set")
        defaults = Limits()
        return cls(
            model=model,
            openrouter_api_key=env.get("OPENROUTER_API_KEY", "").strip(),
            limits=Limits(
                max_iterations=_int(env, "MAKAN_MAX_ITERATIONS", defaults.max_iterations),
                token_budget=_int(env, "MAKAN_TOKEN_BUDGET", defaults.token_budget),
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
