"""Run the web backend: `python -m makan.web`.

    python -m makan.web --demo      sample places, no API key and no network
    python -m makan.web --reload    restart on code changes, for development
    python -m makan.web             live providers, with `.env` or the environment as the settings

The server also serves the built page from `web/dist` (or `MAKAN_WEB_DIST`) when it exists. For
front end work run `npm run dev` in `web/` next to it, which proxies `/api` to this server.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from makan.config import ConfigError
from makan.env import load_dotenv
from makan.web.app import DEFAULT_WEB_DIST, demo_requested

# Matches the proxy in web/vite.config.ts.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
PACKAGE_DIR = Path(__file__).resolve().parents[1]  # src/makan, what --reload watches
FACTORY = "makan.web:create_app_from_env"
INSTALL = 'pip install -e ".[web,overture]"'


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m makan.web",
        description="Start the Makan web backend, which also serves the built page when it exists.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run with sample places, no API key and no network (same as MAKAN_DEMO=1)",
    )
    parser.add_argument(
        "--reload", action="store_true", help="restart when the Python code changes (development)"
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="default: %(default)s")
    parser.add_argument("--port", type=_port, default=DEFAULT_PORT, help="default: %(default)s")
    return parser.parse_args(argv)


def _port(text: str) -> int:
    try:
        port = int(text)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"{text!r} is not a port between 0 and 65535")
    return port


def missing_packages(*, demo: bool) -> list[str]:
    """The optional packages this run needs and the environment lacks."""
    needed = ["fastapi", "uvicorn", "jwt"] if demo else ["fastapi", "uvicorn", "jwt", "duckdb"]
    return [name for name in needed if importlib.util.find_spec(name) is None]


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        load_dotenv()  # so MAKAN_DEMO and MAKAN_WEB_DIST in `.env` count here too
    except ConfigError as exc:
        print(f"Makan could not start: {exc}.", file=sys.stderr)
        return 1
    if args.demo:
        os.environ["MAKAN_DEMO"] = "1"  # the reload worker inherits it
    missing = missing_packages(demo=demo_requested(os.environ))
    if missing:
        print(
            f"Makan could not start: {', '.join(missing)} not installed.\nRun: {INSTALL}",
            file=sys.stderr,
        )
        return 1

    import uvicorn

    dist = Path(os.environ.get("MAKAN_WEB_DIST", "").strip() or DEFAULT_WEB_DIST)
    if not dist.is_dir():
        print(
            f"No built page at {dist}, so this serves the API only. Run `npm run dev` in web/ "
            "for the page, or `npm run build` there to serve it from here.",
            file=sys.stderr,
        )
    uvicorn.run(
        FACTORY,
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(PACKAGE_DIR)] if args.reload else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
