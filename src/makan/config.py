"""Runtime configuration, read from the environment.

Model names and loop limits live here, never in the loop itself.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

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
CLEF_FIT_MODEL = "@cf/cloudflare/clef-flash"


def clef_selector(model: str) -> str:
    """Only the selected, frozen model is supported; never substitute another endpoint."""
    if model != CLEF_FIT_MODEL:
        raise ConfigError("MAKAN_FIT_MODEL must explicitly select @cf/cloudflare/clef-flash")
    return model.rsplit("/", 1)[1]


@dataclass(frozen=True)
class FitConfig:
    """Separate from request-signal scoring; selecting Clef does not wire it into the app."""

    backend: str = "local"
    model: str = ""
    cloudflare_account_id: str = field(default="", repr=False)
    cloudflare_api_token: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if self.backend not in ("local", "clef"):
            raise ConfigError("MAKAN_FIT_BACKEND must be local or clef")
        if self.backend == "clef":
            clef_selector(self.model)
        elif self.model:
            raise ConfigError("MAKAN_FIT_MODEL requires MAKAN_FIT_BACKEND=clef")


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
class DuckDbConfig:
    """The bounds on the Overture places query: threads, memory, a scratch directory, a deadline.

    The defaults suit one small serverless function. `work_dir` blank uses DuckDB's own
    directories, which a read-only host cannot write.
    """

    threads: int = 2
    memory_mb: int = 1024
    work_dir: str = ""
    deadline_s: float = 20.0


@dataclass(frozen=True)
class SupabaseConfig:
    """The Supabase project that holds accounts. Present only when `SUPABASE_URL` is set.

    `anon_key` is the project's public key and is sent to the browser. `service_role_key` is
    privileged, stays on the server, and is hidden from `repr` so it cannot reach a log.
    """

    url: str  # the project URL without a trailing slash
    anon_key: str
    service_role_key: str = field(repr=False)
    redirect_url: str = ""  # where the confirmation email sends the person, or "" for this origin

    @property
    def issuer(self) -> str:
        return f"{self.url}/auth/v1"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"


@dataclass(frozen=True)
class Config:
    model: str
    openrouter_api_key: str = field(default="", repr=False)
    limits: Limits = field(default_factory=Limits)
    graph_limits: GraphLimits = field(default_factory=GraphLimits)
    overture_release: str = ""  # empty means the latest release
    places_cache_ttl_seconds: int = 86_400
    session_retention_hours: int = 24  # how long a group session lives after it is created
    session_purge_interval_minutes: int = 15  # how often the server deletes expired sessions
    database_url: str = field(default="", repr=False)  # empty keeps group sessions in memory
    map_tile_url: str = DEFAULT_MAP_TILE_URL  # a raster tile template with {z}, {x} and {y}
    map_attribution: str = DEFAULT_MAP_ATTRIBUTION  # shown beside the map whenever it is
    map_attribution_url: str = DEFAULT_MAP_ATTRIBUTION_URL  # where that attribution links, or ""
    scorer: ScorerConfig = field(default_factory=ScorerConfig)
    supabase: SupabaseConfig | None = None  # None leaves accounts off, so everyone is a guest
    duckdb: DuckDbConfig = field(default_factory=DuckDbConfig)
    groups_enabled: bool = True  # False removes the group page and its endpoints
    accounts_enabled: bool = True  # False leaves everyone a guest, whatever SUPABASE_URL says
    fit_enabled: bool = False  # opt-in local cuisine estimates; never enables a model
    fit: FitConfig = field(default_factory=FitConfig)

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
        accounts_enabled = _flag(env, "MAKAN_ACCOUNTS_ENABLED", True)
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
            session_purge_interval_minutes=_int(
                env, "MAKAN_SESSION_PURGE_INTERVAL_MINUTES", cls.session_purge_interval_minutes
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
            supabase=_supabase(env) if accounts_enabled else None,
            duckdb=DuckDbConfig(
                threads=_int(env, "MAKAN_DUCKDB_THREADS", DuckDbConfig.threads),
                memory_mb=_int(env, "MAKAN_DUCKDB_MEMORY_MB", DuckDbConfig.memory_mb),
                work_dir=env.get("MAKAN_DUCKDB_DIR", "").strip(),
                deadline_s=float(
                    _int(env, "MAKAN_PLACES_DEADLINE_SECONDS", int(DuckDbConfig.deadline_s))
                ),
            ),
            groups_enabled=_flag(env, "MAKAN_GROUPS_ENABLED", True),
            accounts_enabled=accounts_enabled,
            fit_enabled=_flag(env, "MAKAN_FIT_ENABLED", False),
            fit=FitConfig(
                backend=env.get("MAKAN_FIT_BACKEND", "").strip().lower() or "local",
                model=env.get("MAKAN_FIT_MODEL", "").strip(),
                cloudflare_account_id=env.get("CLOUDFLARE_ACCOUNT_ID", "").strip(),
                cloudflare_api_token=env.get("CLOUDFLARE_API_TOKEN", "").strip(),
            ),
        )


def _supabase(env: Mapping[str, str]) -> SupabaseConfig | None:
    """Accounts are on when `SUPABASE_URL` is set, and then every setting they need must be."""
    url = env.get("SUPABASE_URL", "").strip().rstrip("/")
    if not url:
        return None
    if not _is_http_url(url):
        raise ConfigError("SUPABASE_URL must be an http or https URL")
    redirect = env.get("MAKAN_AUTH_REDIRECT_URL", "").strip()
    if redirect and not _is_http_url(redirect):
        raise ConfigError("MAKAN_AUTH_REDIRECT_URL must be an http or https URL")
    anon = env.get("SUPABASE_ANON_KEY", "").strip()
    service = env.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    missing = [
        name
        for name, value in (
            ("SUPABASE_ANON_KEY", anon),
            ("SUPABASE_SERVICE_ROLE_KEY", service),
            ("MAKAN_DATABASE_URL", env.get("MAKAN_DATABASE_URL", "").strip()),
        )
        if not value
    ]
    if missing:
        raise ConfigError(f"accounts need {', '.join(missing)} when SUPABASE_URL is set")
    return SupabaseConfig(url, anon, service, redirect)


def _is_http_url(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    """A switch: blank is the default, and anything but a clear yes or no is an error, so a typo
    cannot silently leave a feature on that was meant to be off."""
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"{name} must be one of 1, 0, true, false, yes, no, on, off, got {raw!r}")


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
