"""The web channel's HTTP backend: a thin FastAPI app over `makan.solo.recommend`."""

from makan.web.app import create_app, create_app_from_env

__all__ = ["create_app", "create_app_from_env"]
