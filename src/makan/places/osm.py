"""OpenStreetMap facts for Overture places, read from a local regional extract.

`makan.places.osm_build` writes the extract ahead of time. Here it is only read, from a local
Parquet file through DuckDB, which needs no extension and no network, so a search never calls
Overpass or any OpenStreetMap API.

A place gains facts only from an element that is clearly the same venue: see `match`. Without an
extract, with a broken one, or when a read fails, places come back exactly as the inner provider
gave them, so OpenStreetMap can add facts but never take a search down.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from makan.places.base import Place, PlaceQuery, PlacesProvider, distance_m
from makan.places.facts import (
    OSM_LICENCE,
    OSM_SOURCE,
    FactSource,
    OsmFacts,
    facts_from_tags,
)
from makan.places.osm_format import (
    EXTRACT_FORMAT,
    HOUSE_NUMBER_KEY,
    NAME_KEYS,
    TIMESTAMP,
    Box,
    ExtractError,
)

log = logging.getLogger(__name__)

MATCH_RADIUS_M = 50  # an Overture point and an OpenStreetMap point for one venue rarely differ more
METERS_PER_DEGREE = 111_320
READ_DEADLINE_S = 5.0  # a local file read; past this the search goes on without OpenStreetMap

SQL = """
SELECT osm_type, osm_id, lat, lon, tags
FROM read_parquet(?)
WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
"""
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class OsmUnavailable(Exception):
    """The extract could not be read for this search."""


@dataclass(frozen=True)
class ExtractInfo:
    region: str
    box: Box
    source: FactSource


@dataclass(frozen=True)
class OsmElement:
    ref: str
    lat: float
    lon: float
    names: frozenset[str]  # every name the element carries, as `name_key` gives them
    facts: OsmFacts
    house_number: str | None = None  # `addr:housenumber`, as `house_number` gives it


def name_key(text: str) -> str:
    """A name reduced so spelling noise does not matter, and nothing else is forgiven.

    Accents, case, punctuation, spacing, "&" against "and", and a leading "The" are ignored, so
    "Joe's Pizza" and "JOES PIZZA" agree. Different words never do: "Joe's" is not "Joe's Pizza".
    """
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    plain = re.sub(r"['\u2019`]", "", plain.replace("&", " and "))
    words = re.findall(r"[^\W_]+", plain)
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    return "".join(words)


def house_number(text: str | None) -> str | None:
    """The house number an address starts with, such as "265" or "12a", reduced like a name."""
    if not text:
        return None
    found = re.match(r"\s*(\d+[a-z]?)\b", text.casefold())
    return found.group(1) if found else None


def match(place: Place, elements: Sequence[OsmElement]) -> OsmElement | None:
    """The one element that is this venue, or None.

    The rule is deliberately strict, because a wrong match shows another venue's hours:
    an element matches when it is within `MATCH_RADIUS_M` of the place, one of its names has the
    same `name_key` as the place's name, and, when both give a house number, the numbers agree, so
    a chain's branch at another address never lends its hours. When two different elements match,
    nothing does.
    """
    key = name_key(place.name)
    if not key:
        return None
    number = house_number(place.address)
    hits = {
        e.ref: e
        for e in elements
        if key in e.names
        and distance_m(place.lat, place.lon, e.lat, e.lon) <= MATCH_RADIUS_M
        and (number is None or e.house_number is None or number == e.house_number)
    }
    return next(iter(hits.values())) if len(hits) == 1 else None


def match_all(places: Sequence[Place], elements: Sequence[OsmElement]) -> list[OsmElement | None]:
    """`match` for each place, except that an element two places at different addresses both
    match is no one's: one of them is another branch whose source point is off."""
    found = [match(place, elements) for place in places]
    addresses: dict[str, set[str]] = {}
    for place, element in zip(places, found, strict=True):
        if element is not None:
            addresses.setdefault(element.ref, set()).add(name_key(place.address or ""))
    return [
        element if element is not None and len(addresses[element.ref]) == 1 else None
        for element in found
    ]


class OsmExtract:
    """One extract file. It is opened on first use, and a missing or invalid one is logged once
    and then treated as absent until the process restarts."""

    def __init__(self, path: str, *, deadline_s: float = READ_DEADLINE_S) -> None:
        self._path = path
        self._deadline_s = deadline_s
        self._lock = threading.Lock()
        self._opened = False
        self._info: ExtractInfo | None = None
        self._connection: Any = None

    @property
    def path(self) -> str:
        return self._path

    def info(self) -> ExtractInfo | None:
        with self._lock:
            if not self._opened:
                self._opened = True
                try:
                    self._info = self._open()
                except OsmUnavailable as exc:
                    log.warning("OpenStreetMap facts are off: %s", exc)
            return self._info

    def nearby(self, box: Box) -> list[OsmElement]:
        info = self.info()
        if info is None:
            return []
        import duckdb

        cursor = self._connection.cursor()
        timer = threading.Timer(self._deadline_s, cursor.interrupt)
        timer.daemon = True
        timer.start()
        try:
            cursor.execute(SQL, [self._path, box.south, box.north, box.west, box.east])
            rows = cursor.fetchall()
        except duckdb.Error as exc:
            raise OsmUnavailable(f"the extract could not be read ({type(exc).__name__})") from exc
        finally:
            timer.cancel()
        return [e for row in rows if (e := _element(row, info.source)) is not None]

    def _open(self) -> ExtractInfo:
        if not Path(self._path).is_file():
            raise OsmUnavailable(f"no extract file at {self._path}")
        try:
            import duckdb
        except ImportError:
            raise OsmUnavailable("DuckDB is not installed") from None
        try:
            connection = duckdb.connect(config={"threads": 1, "memory_limit": "256MB"})
            rows = connection.execute(
                "SELECT decode(key), decode(value) FROM parquet_kv_metadata(?)", [self._path]
            ).fetchall()
        except duckdb.Error as exc:
            raise OsmUnavailable(f"the extract is not a readable Parquet file ({exc})") from exc
        meta = {str(k): str(v) for k, v in rows}
        if meta.get("makan_format") != EXTRACT_FORMAT:
            raise OsmUnavailable(f"the extract is not in the {EXTRACT_FORMAT} format")
        if meta.get("licence") != OSM_LICENCE:
            raise OsmUnavailable("the extract does not name the ODbL licence")
        base, built = meta.get("osm_base", ""), meta.get("built", "")
        if not TIMESTAMP.fullmatch(base) or not _DATE.fullmatch(built):
            raise OsmUnavailable("the extract has no valid OpenStreetMap timestamp or build date")
        try:
            box = Box.parse(meta.get("bbox", ""))
        except ExtractError as exc:
            raise OsmUnavailable(f"the extract has no valid box ({exc})") from None
        self._connection = connection
        return ExtractInfo(
            region=meta.get("region", ""),
            box=box,
            source=FactSource(OSM_SOURCE, base, OSM_LICENCE, built),
        )


def _element(row: tuple[Any, ...], source: FactSource) -> OsmElement | None:
    osm_type, osm_id, lat, lon, tags_text = row
    try:
        tags = json.loads(tags_text)
    except (TypeError, ValueError):
        return None
    if not isinstance(tags, dict):
        return None
    tags = {k: v for k, v in tags.items() if isinstance(k, str) and isinstance(v, str)}
    names = set()
    for key in NAME_KEYS:
        for name in tags.get(key, "").split(";"):
            if found := name_key(name):
                names.add(found)
    if not names:
        return None
    ref = f"{osm_type}/{osm_id}"
    return OsmElement(
        ref,
        float(lat),
        float(lon),
        frozenset(names),
        facts_from_tags(ref, tags, source),
        house_number(tags.get(HOUSE_NUMBER_KEY)),
    )


class OsmEnrichedProvider:
    """Adds OpenStreetMap facts to another provider's places, after its own ranking.

    Its name carries the extract's data timestamp, so a cache never serves facts from an older
    extract. The order, the filters and the places themselves are the inner provider's.
    """

    def __init__(self, inner: PlacesProvider, extract: OsmExtract) -> None:
        self._inner = inner
        self._extract = extract

    @property
    def name(self) -> str:
        info = self._extract.info()
        return self._inner.name if info is None else f"{self._inner.name}+osm:{info.source.version}"

    def search_nearby(self, query: PlaceQuery) -> list[Place]:
        places = self._inner.search_nearby(query)
        info = self._extract.info()
        if not places or info is None:
            return places
        box = _around(places)
        if not _overlaps(box, info.box):
            return places
        try:
            elements = self._extract.nearby(box)
        except OsmUnavailable as exc:
            log.warning("searching without OpenStreetMap facts: %s", exc)
            return places
        enriched = []
        for place, found in zip(places, match_all(places, elements), strict=True):
            facts = found.facts if found is not None else None
            enriched.append(replace(place, osm=facts) if facts and facts.has_facts else place)
        return enriched


def _around(places: Sequence[Place]) -> Box:
    """The places' extent, widened by the match radius so an element just outside still counts."""
    south = min(p.lat for p in places)
    north = max(p.lat for p in places)
    dlat = MATCH_RADIUS_M / METERS_PER_DEGREE
    widest = max(abs(south), abs(north))
    dlon = MATCH_RADIUS_M / (METERS_PER_DEGREE * max(math.cos(math.radians(widest)), 0.01))
    return Box(
        min(p.lon for p in places) - dlon,
        south - dlat,
        max(p.lon for p in places) + dlon,
        north + dlat,
    )


def _overlaps(a: Box, b: Box) -> bool:
    return a.west <= b.east and b.west <= a.east and a.south <= b.north and b.south <= a.north
