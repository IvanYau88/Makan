import pytest

from makan.config import Config, ConfigError
from makan.loop import Limits


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
