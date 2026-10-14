"""The public deployment: the same app with a public configuration as its defaults.

A public site is a guest-only site. Groups are kept in one process's memory, so a second
instance never sees them, and accounts need an email path that does not exist yet, so both are
off unless the environment says otherwise. The places are real, from Overture, and nothing needs
a model or a key: Browse nearby is the one search. `MAKAN_DEMO=1` swaps in the sample places.

`create_public_app` reads the environment it is given and never `.env`, never connects to
anything, and never reads Overture, so importing the entrypoint during a build needs no
credentials and no network.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

from fastapi import FastAPI

from makan.web.app import DEFAULT_WEB_DIST, create_app_from_env

log = logging.getLogger("makan.web")

# Used when the variable is unset or blank. A value set to anything, even "0", is kept.
PUBLIC_DEFAULTS = {
    "MAKAN_GROUPS_ENABLED": "0",
    "MAKAN_ACCOUNTS_ENABLED": "0",
    "MAKAN_PLACES_ONLY": "1",
    # The one writable place on a serverless host, for DuckDB's extension and spill files.
    "MAKAN_DUCKDB_DIR": "/tmp/makan-duckdb",
}


def public_env(env: Mapping[str, str], web_dist: Path | None = None) -> dict[str, str]:
    """`env` with the public defaults filled in for whatever it leaves unset or blank."""
    merged = dict(env)
    defaults = dict(PUBLIC_DEFAULTS)
    if web_dist is not None:
        defaults["MAKAN_WEB_DIST"] = str(web_dist)
    for name, value in defaults.items():
        if not merged.get(name, "").strip():
            merged[name] = value
    return merged


def create_public_app(
    env: Mapping[str, str] | None = None, *, web_dist: Path | None = None
) -> FastAPI:
    """Build the app for a public host. `web_dist` is where the built page is, by default."""
    merged = public_env(os.environ if env is None else env, web_dist)
    dist = Path(merged.get("MAKAN_WEB_DIST", "") or DEFAULT_WEB_DIST)
    if not (dist / "index.html").is_file():
        log.warning("no built page at %s, so this serves the API only", dist)
    return create_app_from_env(merged)
