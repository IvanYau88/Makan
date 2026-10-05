"""Overture Maps places (https://docs.overturemaps.org/guides/places/), read straight from S3.

Overture publishes each monthly release as GeoParquet.
DuckDB reads only the rows whose bounding box falls in the search area, so nothing is downloaded
up front and no API key is needed.
DuckDB is an optional dependency: `pip install "makan[overture]"`.

Overture has names, categories, addresses, and locations. It has no hours, ratings, or prices.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from makan.places.base import Place, PlaceQuery, PlacesError, rank_nearby

STAC_CATALOG = "https://stac.overturemaps.org/catalog.json"
PLACES_PATH = "s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
S3_REGION = "us-west-2"
# The bucket is public, so reads must be anonymous. With no secret, DuckDB signs requests with
# whatever AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_PROFILE, or credentials file the machine
# has, and S3 rejects a bad key with a 403 even on a public bucket. A config secret with an empty
# key pair takes precedence over all of them and sends unsigned requests.
ANONYMOUS_S3_SECRET = (
    "CREATE OR REPLACE SECRET makan_overture "
    f"(TYPE s3, PROVIDER config, KEY_ID '', SECRET '', REGION '{S3_REGION}')"
)
FOOD_ROOT = "food_and_drink"  # the top of Overture's category taxonomy for places to eat or drink
MIN_CONFIDENCE = 0.5  # Overture's own score that a place exists; lower scores are mostly junk
METERS_PER_DEGREE = 111_320

Row = dict[str, Any]

SQL = f"""
SELECT
    id,
    names.primary AS name,
    taxonomy.primary AS category,
    taxonomy.hierarchy AS hierarchy,
    taxonomy.alternates AS alternates,
    bbox.ymin AS lat,
    bbox.xmin AS lon,
    addresses[1].freeform AS street,
    addresses[1].locality AS locality
FROM read_parquet(?)
WHERE bbox.xmin BETWEEN ? AND ?
  AND bbox.ymin BETWEEN ? AND ?
  AND list_contains(taxonomy.hierarchy, '{FOOD_ROOT}')
  AND names.primary IS NOT NULL
  AND confidence >= {MIN_CONFIDENCE}
  AND coalesce(operating_status, 'open') = 'open'
"""  # a place is a point, so its bounding box is its location; that avoids the spatial extension


@dataclass(frozen=True)
class Bounds:
    west: float
    south: float
    east: float
    north: float

    @classmethod
    def around(cls, lat: float, lon: float, radius_m: int) -> Bounds:
        """A box that contains the whole circle. It does not wrap at the antimeridian."""
        dlat = radius_m / METERS_PER_DEGREE
        dlon = radius_m / (METERS_PER_DEGREE * max(math.cos(math.radians(lat)), 0.01))
        return cls(lon - dlon, lat - dlat, lon + dlon, lat + dlat)


class RowSource(Protocol):
    def fetch(self, release: str, bounds: Bounds) -> list[Row]:
        """Return candidate food places inside the box, as dicts with the keys `SQL` selects."""
        ...


class DuckDbSource:
    """Runs `SQL` over Overture's Parquet files. `path` may name a local file for offline use."""

    def __init__(self, path: str = PLACES_PATH) -> None:
        self._path = path
        self._connection: Any = None
        self._lock = threading.Lock()

    def fetch(self, release: str, bounds: Bounds) -> list[Row]:
        try:
            import duckdb
        except ImportError:
            raise PlacesError(
                'DuckDB is not installed; run: pip install "makan[overture]"'
            ) from None
        path = self._path.format(release=release)
        try:
            cursor = self._connect(duckdb, path).cursor()  # one cursor per call: thread safe
            cursor.execute(SQL, [path, bounds.west, bounds.east, bounds.south, bounds.north])
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]
        except duckdb.Error as exc:
            raise PlacesError(f"Overture query failed: {exc}") from exc

    def _connect(self, duckdb: Any, path: str) -> Any:
        with self._lock:
            if self._connection is None:
                connection = duckdb.connect()
                if path.startswith("s3://"):
                    connection.execute("INSTALL httpfs")
                    connection.execute("LOAD httpfs")
                    connection.execute(ANONYMOUS_S3_SECRET)
                self._connection = connection
            return self._connection


class OvertureProvider:
    """Nearby search over one Overture release.

    With no `release` it asks Overture's catalog for the latest, once, on first use,
    because old releases are removed from S3 after a couple of months.
    """

    def __init__(
        self,
        release: str | None = None,
        *,
        source: RowSource | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._release = release or None
        self._source = source or DuckDbSource()
        self._client = client or httpx.Client(timeout=15.0)

    @property
    def release(self) -> str:
        if self._release is None:
            self._release = self._latest_release()
        return self._release

    @property
    def name(self) -> str:
        return f"overture:{self.release}"

    def search_nearby(self, query: PlaceQuery) -> list[Place]:
        bounds = Bounds.around(query.lat, query.lon, query.radius_m)
        rows = self._source.fetch(self.release, bounds)
        return rank_nearby((_place(row) for row in rows), query)

    def _latest_release(self) -> str:
        try:
            response = self._client.get(STAC_CATALOG)
            response.raise_for_status()
            latest = response.json()["latest"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise PlacesError(
                f"could not find the latest Overture release ({type(exc).__name__}); "
                "set MAKAN_OVERTURE_RELEASE to pin one"
            ) from exc
        if not isinstance(latest, str) or not latest:
            raise PlacesError("the Overture catalog did not name a latest release")
        return latest


def _place(row: Row) -> Place:
    labels = [*(row["hierarchy"] or ()), *(row["alternates"] or ())]
    category = row["category"] or (labels[-1] if labels else FOOD_ROOT)
    return Place(
        id=row["id"],
        name=row["name"],
        category=category,
        categories=tuple(dict.fromkeys([*labels, category])),
        lat=row["lat"],
        lon=row["lon"],
        address=_address(row["street"], row["locality"]),
    )


def _address(street: str | None, locality: str | None) -> str | None:
    parts: Sequence[str] = [p.strip() for p in (street, locality) if p and p.strip()]
    if len(parts) == 2 and parts[1].lower() in parts[0].lower():
        parts = parts[:1]
    return ", ".join(parts) or None
