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
from pathlib import Path
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

HTTP_TIMEOUT_S = 10  # one remote read, so a stalled one cannot outlast the application deadline
MAX_TEMP_MB = 256  # the most DuckDB may spill to disk, under the 500 MB a serverless host allows

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


@dataclass(frozen=True)
class DuckDbLimits:
    """What bounds one DuckDB connection, so a places query cannot take a small host down.

    `work_dir` is where DuckDB keeps everything it writes: extensions, spilled data, and its home
    directory. It must be writable, and blank leaves DuckDB's own defaults, which are all under
    the process's home directory. A serverless host has none, so it needs a scratch directory such
    as `/tmp/makan-duckdb`.
    `deadline_s` is the application deadline for one query, enforced by interrupting it.
    `install_deadline_s` bounds the one-time download of the S3 extension, which cannot be
    interrupted, so it is waited on instead.
    """

    threads: int = 2
    memory_mb: int = 1024
    work_dir: str = ""
    deadline_s: float = 20.0
    install_deadline_s: float = 15.0

    def settings(self) -> dict[str, str | int]:
        """DuckDB's `config` for `duckdb.connect`."""
        settings: dict[str, str | int] = {
            "threads": self.threads,
            "memory_limit": f"{self.memory_mb}MB",
            "max_temp_directory_size": f"{MAX_TEMP_MB}MB",
        }
        if self.work_dir:
            work = Path(self.work_dir)
            for name in ("extensions", "tmp", "home"):
                (work / name).mkdir(parents=True, exist_ok=True)
            settings["extension_directory"] = str(work / "extensions")
            settings["temp_directory"] = str(work / "tmp")
            # `INSTALL` looks up the home directory even when `extension_directory` is set, and a
            # serverless process has no HOME, so DuckDB fails with "Can't find the home directory
            # at ''". It has to be in the connect config, before any INSTALL runs.
            settings["home_directory"] = str(work / "home")
        return settings


class DuckDbSource:
    """Runs `SQL` over Overture's Parquet files. `path` may name a local file for offline use."""

    def __init__(self, path: str = PLACES_PATH, limits: DuckDbLimits | None = None) -> None:
        self._path = path
        self._limits = limits or DuckDbLimits()
        self._connection: Any = None
        self._installer: _Installer | None = None
        self._lock = threading.Lock()

    def fetch(self, release: str, bounds: Bounds) -> list[Row]:
        try:
            import duckdb
        except ImportError:
            raise PlacesError(
                'DuckDB is not installed; run: pip install "makan[overture]"'
            ) from None
        path = self._path.format(release=release)
        overdue = threading.Event()
        timer: threading.Timer | None = None
        try:
            cursor = self._connect(duckdb, path).cursor()  # one cursor per call: thread safe
            # The graph's step timeout cannot stop a thread, so the query is interrupted instead.

            def expire() -> None:
                overdue.set()
                cursor.interrupt()

            timer = threading.Timer(self._limits.deadline_s, expire)
            timer.daemon = True
            timer.start()
            cursor.execute(SQL, [path, bounds.west, bounds.east, bounds.south, bounds.north])
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]
        except duckdb.Error as exc:
            if overdue.is_set():
                raise PlacesError(
                    f"Overture query passed its {self._limits.deadline_s:g} second deadline"
                ) from exc
            raise PlacesError(f"Overture query failed: {exc}") from exc
        finally:
            if timer is not None:
                timer.cancel()

    def _connect(self, duckdb: Any, path: str) -> Any:
        with self._lock:
            if self._connection is None:
                try:
                    settings = self._limits.settings()
                except OSError as exc:
                    raise PlacesError(
                        f"the DuckDB work directory is not writable ({type(exc).__name__})"
                    ) from exc
                connection = duckdb.connect(config=settings)
                if path.startswith("s3://"):
                    self._load_httpfs(duckdb, connection)
                    connection.execute(f"SET http_timeout = {HTTP_TIMEOUT_S}")
                    connection.execute("SET http_retries = 1")
                    connection.execute(ANONYMOUS_S3_SECRET)
                self._connection = connection
            return self._connection

    def _load_httpfs(self, duckdb: Any, connection: Any) -> None:
        """Load the extension that reads S3, downloading it only when it is not already there.

        A directory prepared ahead of time, see `DuckDbLimits.work_dir`, needs no download.
        """
        try:
            connection.execute("LOAD httpfs")
            return
        except duckdb.Error:
            pass
        failure = self._install_httpfs(connection)
        if failure is None:
            try:
                connection.execute("LOAD httpfs")
                return
            except duckdb.Error as exc:
                failure = exc
        raise PlacesError(
            "the DuckDB httpfs extension could not be installed or loaded, so Overture "
            f"cannot be read ({type(failure).__name__}): {failure}"
        ) from failure

    def _install_httpfs(self, connection: Any) -> Exception | None:
        """Download the extension within `install_deadline_s`, and return why it failed, if it did.

        DuckDB cannot interrupt a download, and it has no timeout setting for one before the
        extension that defines `http_timeout` is loaded, so it runs on a thread that is waited on.
        A download that outlasts the deadline keeps going and still lands in the extension
        directory, so a later request can load it, and no second one starts until it ends.
        """
        installer = self._installer
        if installer is None or not installer.is_alive():
            installer = self._installer = _Installer(connection)
            installer.start()
        installer.join(self._limits.install_deadline_s)
        if installer.is_alive():
            raise PlacesError(
                "the DuckDB httpfs extension download did not finish within "
                f"{self._limits.install_deadline_s:g} seconds, so Overture cannot be read yet"
            )
        return installer.failure


class _Installer(threading.Thread):
    """Runs `INSTALL httpfs` off the calling thread. `failure` is why it failed, if it did."""

    def __init__(self, connection: Any) -> None:
        super().__init__(name="makan-httpfs-install", daemon=True)
        self._connection = connection
        self.failure: Exception | None = None

    def run(self) -> None:
        try:
            self._connection.execute("INSTALL httpfs")
        except Exception as exc:  # handed to the caller, which raises PlacesError
            self.failure = exc


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
