"""The web channel's HTTP backend: a thin FastAPI app over `makan.solo.recommend`."""

try:
    from makan.web.app import create_app, create_app_from_env
except ImportError as exc:
    raise ImportError(
        f"The web backend needs its optional packages ({exc}).\n"
        'Run: pip install -e ".[web,overture]"'
    ) from exc

__all__ = ["create_app", "create_app_from_env"]
