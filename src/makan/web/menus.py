"""Dish facts from a venue's own website, read when a diner opens that place's detail.

Off unless `MAKAN_MENU_FACTS_ENABLED` is on. Then each place with a readable website carries a
`menu_ref`, an HMAC-signed copy of the one URL the server would read for it, and
`POST /api/places/menu` reads only a URL inside a ref it signed. A client can ask for the menu of
a place it was shown and for nothing else, so the endpoint is never a way to make the server
fetch an address someone chose, and no search list ever triggers a read.

The request names only the ref. Nothing about the diner (location, tastes, account) is in it or
reaches the venue, and the answer holds public facts about the venue only.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from typing import Annotated, Any

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints

from makan.places.facts import OsmFacts
from makan.places.links import MAX_URL_CHARS, web_link
from makan.places.menu_fetch import MenuFetcher, third_party
from makan.web.errors import ApiError

REF_VERSION = "v1"
_SIGNATURE_CHARS = 32  # 128 bits of HMAC-SHA256, hex
MAX_REF_CHARS = len(REF_VERSION) + 2 + (MAX_URL_CHARS * 4 + 2) // 3 + _SIGNATURE_CHARS


def menu_page(website: str | None, osm: OsmFacts | None) -> str | None:
    """The one page a menu read of a venue would fetch: its OpenStreetMap menu link when it has
    one off any platform, else its website. A platform link is never chosen."""
    for candidate in (osm.menu_url if osm else None, website):
        url = web_link(candidate)
        if url is not None and not third_party(url):
            return url
    return None


class MenuRefs:
    """Signs and checks the opaque refs that name a venue page the server may read."""

    def __init__(self, secret: str) -> None:
        self._secret = secret.encode()

    def issue(self, url: str) -> str:
        encoded = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
        return f"{REF_VERSION}.{encoded}.{self._sign(url)}"

    def read(self, ref: str) -> str | None:
        """The URL a ref names, or None unless this server signed it."""
        try:
            version, encoded, signature = ref.split(".")
            url = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        except (ValueError, binascii.Error, UnicodeDecodeError):
            return None
        if version != REF_VERSION or not hmac.compare_digest(
            signature.encode(), self._sign(url).encode()
        ):
            return None
        return url if web_link(url) == url else None

    def for_venue(self, website: str | None, osm: OsmFacts | None) -> str | None:
        url = menu_page(website, osm)
        return self.issue(url) if url else None

    def _sign(self, url: str) -> str:
        digest = hmac.new(self._secret, f"menu-ref-{REF_VERSION}:{url}".encode(), hashlib.sha256)
        return digest.hexdigest()[:_SIGNATURE_CHARS]


class MenuBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: Annotated[str, StringConstraints(min_length=1, max_length=MAX_REF_CHARS)]


def register_menu_routes(app: FastAPI, refs: MenuRefs, fetcher: MenuFetcher) -> None:
    @app.post("/api/places/menu")
    async def place_menu(body: MenuBody) -> JSONResponse:
        """Read the menu page a signed ref names, at most once a day per page."""
        url = refs.read(body.ref)
        if url is None:
            raise ApiError(422, "invalid_menu_ref", "This place has no menu Makan can read.")
        facts = await run_in_threadpool(fetcher.read, url)
        payload: dict[str, Any] = facts.to_json()
        return JSONResponse(payload, headers={"Cache-Control": "private, no-store"})
