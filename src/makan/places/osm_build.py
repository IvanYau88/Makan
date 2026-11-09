"""Builds the regional OpenStreetMap extract that `makan.places.osm` reads.

This runs at build time, never on a request: it asks an Overpass server once per tile for the
named food places in a box, keeps only the tags Makan uses, and writes one Parquet file sorted by
location, with the OpenStreetMap data timestamp and the licence in its metadata.

    python -m makan.places.osm_build --bbox -74.03,40.68,-73.90,40.82 --out osm/places.parquet

`--input` reads saved Overpass JSON instead of querying. With `--soft`, every failure is a
printed message and exit status 0, so a deployment build without OpenStreetMap still ships, and
the site then runs with no OpenStreetMap facts. A failed tile fails the whole build, so a partial
extract never ships with silently uneven coverage.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from makan.places.facts import DIET_KEYS, OSM_LICENCE, SERVICE_VALUES
from makan.places.osm_format import EXTRACT_FORMAT, NAME_KEYS, TIMESTAMP, Box, ExtractError

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "Makan-OSM-extract/1 (+https://github.com/IvanYau88/Makan)"
FOOD_AMENITIES = (
    "restaurant",
    "fast_food",
    "cafe",
    "bar",
    "pub",
    "food_court",
    "ice_cream",
    "biergarten",
)
FACT_KEYS = (
    "cuisine",
    "opening_hours",
    *(f"diet:{diet}" for diet in DIET_KEYS),
    *SERVICE_VALUES,
    "wheelchair",
    "check_date",
    "website:menu",
)
KEPT_KEYS = frozenset((*NAME_KEYS, "amenity", *FACT_KEYS))
MAX_TAG_CHARS = 255  # OpenStreetMap's own limit on a tag value
TILE_DEGREES = 0.25  # one Overpass query covers at most this much of a degree each way
MAX_TILES = 16  # about 4 square degrees: a metro area, not a country
QUERY_TIMEOUT_S = 180  # what the query asks the server for
HTTP_TIMEOUT_S = QUERY_TIMEOUT_S + 30
PAUSE_S = 2.0  # between tiles, to be gentle with a shared community server


@dataclass(frozen=True)
class Element:
    osm_type: str
    osm_id: int
    lat: float
    lon: float
    tags: dict[str, str]


def tiles(box: Box) -> list[Box]:
    """The box cut into equal tiles of at most `TILE_DEGREES`, or an error past `MAX_TILES`."""
    columns = math.ceil(round((box.east - box.west) / TILE_DEGREES, 9))
    rows = math.ceil(round((box.north - box.south) / TILE_DEGREES, 9))
    if columns * rows > MAX_TILES:
        raise ExtractError(
            f"the box needs {columns * rows} Overpass queries, over the cap of {MAX_TILES}; "
            "an extract is for one metro area"
        )
    width = (box.east - box.west) / columns
    height = (box.north - box.south) / rows
    return [
        Box(
            box.west + c * width,
            box.south + r * height,
            box.west + (c + 1) * width,
            box.south + (r + 1) * height,
        )
        for r in range(rows)
        for c in range(columns)
    ]


def overpass_query(box: Box) -> str:
    """Named food places in the box, tags only, with a center point for ways and relations."""
    amenities = "|".join(FOOD_AMENITIES)
    return (
        f"[out:json][timeout:{QUERY_TIMEOUT_S}];"
        f'nwr["amenity"~"^({amenities})$"]["name"]'
        f"({box.south:.6f},{box.west:.6f},{box.north:.6f},{box.east:.6f});"
        "out tags center;"
    )


def fetch(
    box: Box,
    client: httpx.Client,
    *,
    url: str = OVERPASS_URL,
    pause: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], str]:
    """Every tile's elements and the oldest OpenStreetMap timestamp among the answers."""
    elements: list[dict[str, Any]] = []
    stamps: list[str] = []
    for index, tile in enumerate(tiles(box)):
        if index:
            pause(PAUSE_S)
        try:
            response = client.post(url, data={"data": overpass_query(tile)})
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ExtractError(
                f"Overpass did not answer tile {index + 1} ({type(exc).__name__})"
            ) from exc
        tile_elements, stamp = _overpass_body(body)
        if (remark := body.get("remark")) and isinstance(remark, str):
            # Overpass reports a timeout or memory stop here with a 200 and partial data.
            raise ExtractError(f"Overpass stopped tile {index + 1} early: {remark[:200]}")
        elements.extend(tile_elements)
        stamps.append(stamp)
    return elements, min(stamps)


def _overpass_body(body: object) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(body, Mapping):
        raise ExtractError("the Overpass answer is not a JSON object")
    elements = body.get("elements")
    stamp = (body.get("osm3s") or {}).get("timestamp_osm_base")
    if not isinstance(elements, list):
        raise ExtractError("the Overpass answer has no elements list")
    if not isinstance(stamp, str) or not TIMESTAMP.fullmatch(stamp):
        raise ExtractError("the Overpass answer has no OpenStreetMap timestamp")
    return [e for e in elements if isinstance(e, dict)], stamp


def elements_from(raw: Iterable[Mapping[str, Any]]) -> list[Element]:
    """Food places with a name and a point, with only the kept tags, one row per element.

    Ways and relations use the center Overpass computed. Elements repeated across tile edges
    are kept once.
    """
    found: dict[tuple[str, int], Element] = {}
    for item in raw:
        osm_type, osm_id = item.get("type"), item.get("id")
        if osm_type not in ("node", "way", "relation") or not isinstance(osm_id, int):
            continue
        point = item if osm_type == "node" else item.get("center") or {}
        lat, lon = point.get("lat"), point.get("lon")
        tags = item.get("tags")
        if not isinstance(lat, int | float) or not isinstance(lon, int | float):
            continue
        if not isinstance(tags, Mapping) or tags.get("amenity") not in FOOD_AMENITIES:
            continue
        kept = {
            key: value
            for key, value in tags.items()
            if key in KEPT_KEYS and isinstance(value, str) and 0 < len(value) <= MAX_TAG_CHARS
        }
        if not kept.get("name", "").strip():
            continue
        found.setdefault(
            (osm_type, osm_id), Element(osm_type, osm_id, float(lat), float(lon), kept)
        )
    return list(found.values())


def write_extract(
    elements: Sequence[Element],
    out: Path,
    *,
    region: str,
    box: Box,
    osm_base: str,
    built: datetime,
) -> int:
    """Write the Parquet file through a temporary name, so a failed write leaves no file."""
    try:
        import duckdb
    except ImportError:
        raise ExtractError('DuckDB is not installed; run: pip install "makan[overture]"') from None
    if not TIMESTAMP.fullmatch(osm_base):
        raise ExtractError(f"the OpenStreetMap timestamp {osm_base!r} is malformed")
    metadata = {
        "makan_format": EXTRACT_FORMAT,
        "region": region,
        "bbox": box.text(),
        "osm_base": osm_base,
        "built": built.astimezone(UTC).strftime("%Y-%m-%d"),
        "licence": OSM_LICENCE,
        "count": str(len(elements)),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".partial")
    rows = sorted(
        (
            (e.osm_type, e.osm_id, e.lat, e.lon, json.dumps(e.tags, ensure_ascii=False))
            for e in elements
        ),
        key=lambda row: (row[2], row[3]),  # by latitude, so a box read skips row groups
    )
    connection = duckdb.connect()
    try:
        connection.execute(
            "CREATE TABLE osm (osm_type VARCHAR, osm_id BIGINT, lat DOUBLE, lon DOUBLE, "
            "tags VARCHAR)"
        )
        if rows:
            connection.executemany("INSERT INTO osm VALUES (?, ?, ?, ?, ?)", rows)
        kv = ", ".join(f"{key}: {_sql_text(value)}" for key, value in metadata.items())
        connection.execute(
            f"COPY (SELECT * FROM osm) TO {_sql_text(str(partial))} "
            f"(FORMAT parquet, ROW_GROUP_SIZE 4096, KV_METADATA {{{kv}}})"
        )
    except duckdb.Error as exc:
        partial.unlink(missing_ok=True)
        raise ExtractError(f"could not write the extract: {exc}") from exc
    finally:
        connection.close()
    partial.replace(out)
    return len(rows)


def _sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build(
    *,
    box: Box,
    out: Path,
    region: str,
    client: httpx.Client | None = None,
    input_path: Path | None = None,
    url: str = OVERPASS_URL,
    pause: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Build one extract and return how many places it holds."""
    if input_path is not None:
        try:
            body = json.loads(input_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ExtractError(f"could not read {input_path} ({type(exc).__name__})") from exc
        raw, osm_base = _overpass_body(body)
    else:
        tiles(box)  # refuse an oversized box before any query
        owned = client is None
        client = client or httpx.Client(timeout=HTTP_TIMEOUT_S, headers={"User-Agent": USER_AGENT})
        try:
            raw, osm_base = fetch(box, client, url=url, pause=pause)
        finally:
            if owned:
                client.close()
    elements = [e for e in elements_from(raw) if _inside(e, box)]
    return write_extract(elements, out, region=region, box=box, osm_base=osm_base, built=now())


def _inside(element: Element, box: Box) -> bool:
    return box.south <= element.lat <= box.north and box.west <= element.lon <= box.east


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(prog="python -m makan.places.osm_build", description=__doc__)
    parser.add_argument("--bbox", help="west,south,east,north in degrees")
    parser.add_argument("--region", default="", help="a short name for the area, kept as metadata")
    parser.add_argument("--out", required=True, type=Path, help="the Parquet file to write")
    parser.add_argument("--input", type=Path, help="saved Overpass JSON to read instead")
    parser.add_argument(
        "--from-env",
        action="store_true",
        help="take the box and region from MAKAN_OSM_BUILD_BBOX and MAKAN_OSM_BUILD_REGION, "
        "and do nothing when the box is unset",
    )
    parser.add_argument("--soft", action="store_true", help="report failures and exit 0")
    args = parser.parse_args(argv)
    bbox_text = args.bbox
    region = args.region
    if args.from_env:
        bbox_text = bbox_text or env.get("MAKAN_OSM_BUILD_BBOX", "").strip()
        region = region or env.get("MAKAN_OSM_BUILD_REGION", "").strip()
        if not bbox_text:
            print("OpenStreetMap extract skipped: MAKAN_OSM_BUILD_BBOX is not set.")
            return 0
    try:
        if not bbox_text:
            raise ExtractError("--bbox is required")
        count = build(
            box=Box.parse(bbox_text),
            out=args.out,
            region=region or "custom",
            input_path=args.input,
            url=env.get("MAKAN_OSM_OVERPASS_URL", "").strip() or OVERPASS_URL,
        )
    except (ExtractError, OSError) as exc:
        print(f"OpenStreetMap extract not built: {exc}", file=sys.stderr)
        if args.soft:
            print("Continuing without OpenStreetMap facts.", file=sys.stderr)
            return 0
        return 1
    print(f"OpenStreetMap extract: {count} places written to {args.out}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
