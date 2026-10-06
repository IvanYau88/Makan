from uuid import uuid4

import pytest

from makan.config import Config, ConfigError
from makan.graph import GraphLimits
from makan.loop import Limits
from tests.helpers import synthetic_database_url


def test_model_is_required() -> None:
    with pytest.raises(ConfigError, match="MAKAN_MODEL"):
        Config.from_env({})


def test_defaults_and_overrides() -> None:
    config = Config.from_env({"MAKAN_MODEL": "a/b"})
    assert (config.model, config.openrouter_api_key, config.limits) == ("a/b", "", Limits())

    config = Config.from_env(
        {
            "MAKAN_MODEL": "a/b",
            "OPENROUTER_API_KEY": "sk-secret",
            "MAKAN_MAX_ITERATIONS": "4",
            "MAKAN_TOKEN_BUDGET": "1000",
        }
    )
    assert config.openrouter_api_key == "sk-secret"
    assert config.limits == Limits(max_iterations=4, token_budget=1000)


def test_api_key_is_not_leaked_by_repr() -> None:
    assert "sk-secret" not in repr(
        Config.from_env({"MAKAN_MODEL": "m", "OPENROUTER_API_KEY": "sk-secret"})
    )


@pytest.mark.parametrize("value", ["many", "0", "-3"])
def test_bad_limits_are_rejected(value: str) -> None:
    with pytest.raises(ConfigError, match="MAKAN_MAX_ITERATIONS"):
        Config.from_env({"MAKAN_MODEL": "m", "MAKAN_MAX_ITERATIONS": value})


def test_places_settings_have_defaults_and_overrides() -> None:
    config = Config.from_env({"MAKAN_MODEL": "m"})
    assert (config.overture_release, config.places_cache_ttl_seconds) == ("", 86_400)

    config = Config.from_env(
        {
            "MAKAN_MODEL": "m",
            "MAKAN_OVERTURE_RELEASE": " 2026-09-23.1 ",
            "MAKAN_PLACES_CACHE_TTL_SECONDS": "60",
        }
    )
    assert (config.overture_release, config.places_cache_ttl_seconds) == ("2026-09-23.1", 60)


def test_a_bad_cache_ttl_is_rejected() -> None:
    with pytest.raises(ConfigError, match="MAKAN_PLACES_CACHE_TTL_SECONDS"):
        Config.from_env({"MAKAN_MODEL": "m", "MAKAN_PLACES_CACHE_TTL_SECONDS": "soon"})


def test_graph_limits_have_defaults_and_overrides() -> None:
    assert Config.from_env({"MAKAN_MODEL": "m"}).graph_limits == GraphLimits(4, 30.0)

    config = Config.from_env(
        {
            "MAKAN_MODEL": "m",
            "MAKAN_GRAPH_MAX_CONCURRENCY": "2",
            "MAKAN_GRAPH_STEP_TIMEOUT_SECONDS": "90",
        }
    )
    assert config.graph_limits == GraphLimits(max_concurrency=2, step_timeout_s=90.0)


def test_a_bad_graph_limit_is_rejected() -> None:
    with pytest.raises(ConfigError, match="MAKAN_GRAPH_STEP_TIMEOUT_SECONDS"):
        Config.from_env({"MAKAN_MODEL": "m", "MAKAN_GRAPH_STEP_TIMEOUT_SECONDS": "0"})


def test_group_session_settings_have_defaults_and_overrides() -> None:
    default = Config.from_env({"MAKAN_MODEL": "m"})
    assert (default.session_retention_hours, default.database_url) == (24, "")

    password = "throwaway-" + uuid4().hex
    url = synthetic_database_url(password)
    config = Config.from_env(
        {
            "MAKAN_MODEL": "m",
            "MAKAN_SESSION_RETENTION_HOURS": "6",
            "MAKAN_DATABASE_URL": f" {url} ",
        }
    )
    assert config.session_retention_hours == 6
    assert config.database_url == url
    assert password not in repr(config)


def test_a_bad_session_retention_is_rejected() -> None:
    for value in ("a day", "0", "-1"):
        with pytest.raises(ConfigError, match="MAKAN_SESSION_RETENTION_HOURS"):
            Config.from_env({"MAKAN_MODEL": "m", "MAKAN_SESSION_RETENTION_HOURS": value})


def test_map_tiles_default_to_openstreetmap_with_attribution() -> None:
    config = Config.from_env({"MAKAN_MODEL": "m"})
    assert config.map_tile_url == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    assert config.map_attribution == "© OpenStreetMap contributors"
    assert config.map_attribution_url.startswith("https://www.openstreetmap.org/")


def test_a_custom_tile_provider_must_be_a_template_and_credit_itself() -> None:
    env = {"MAKAN_MODEL": "m", "MAKAN_MAP_TILE_URL": "https://tiles.example/{z}/{x}/{y}.png"}
    with pytest.raises(ConfigError, match="MAKAN_MAP_ATTRIBUTION"):
        Config.from_env(env)
    config = Config.from_env({**env, "MAKAN_MAP_ATTRIBUTION": "© Example"})
    assert (config.map_tile_url, config.map_attribution) == (env["MAKAN_MAP_TILE_URL"], "© Example")
    assert config.map_attribution_url == ""
    with pytest.raises(ConfigError, match=r"\{z\}"):
        Config.from_env({**env, "MAKAN_MAP_TILE_URL": "https://tiles.example/map.png"})
