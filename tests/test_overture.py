"""The Overture provider with no network: fixture rows, a mock catalog, and a local Parquet file."""

import sys
import threading
import time
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest

from makan.places import OvertureProvider, PlaceQuery, PlacesError
from makan.places.overture import (
    ANONYMOUS_S3_SECRET,
    PLACES_PATH,
    Bounds,
    DuckDbLimits,
    DuckDbSource,
    Row,
)
from tests.helpers import KLCC, overture_rows

RELEASE = "2026-09-23.1"


class FixtureSource:
    """Returns the fixture rows and records how it was asked."""

    def __init__(self, rows: list[Row] | None = None) -> None:
        self.rows = overture_rows() if rows is None else rows
        self.calls: list[tuple[str, Bounds]] = []

    def fetch(self, release: str, bounds: Bounds) -> list[Row]:
        self.calls.append((release, bounds))
        return self.rows


def provider(source: FixtureSource | None = None) -> OvertureProvider:
    return OvertureProvider(RELEASE, source=source or FixtureSource())


def query(**kwargs: Any) -> PlaceQuery:
    return PlaceQuery.near(*KLCC, kwargs.pop("radius_m", 1000), **kwargs)


# Turning rows into places


def test_rows_become_ranked_places_with_distances() -> None:
    found = provider().search_nearby(query(limit=5))
    assert len(found) == 5
    distances = [p.distance_m for p in found]
    assert distances == sorted(d for d in distances if d is not None)
    assert all(p.id and p.name and p.category for p in found)


def test_a_cuisine_filter_matches_the_category_taxonomy() -> None:
    found = provider().search_nearby(query(cuisine="indian", limit=20))
    assert {p.name for p in found} == {"Shahira", "Pappadom Restaurant"}
    assert {p.category for p in found} == {"indian_restaurant"}


def test_a_category_filter_picks_the_kind_of_venue() -> None:
    found = provider().search_nearby(query(category="coffee", limit=20))
    assert [p.name for p in found] == ["Bake and Beans KL"]
    assert provider().search_nearby(query(category="cafe", limit=20))[0].category == "cafe"


def test_a_broad_label_from_the_hierarchy_matches_too() -> None:
    found = provider().search_nearby(query(limit=20, category="restaurant"))
    names = {p.name for p in found}
    assert {"Shahira", "Everest Kitchen"} <= names
    assert "Mountbatten Kuala Lumpur" not in names  # a cafe is not under "restaurant"
    assert all(any("restaurant" in label for label in p.categories) for p in found)


def test_the_radius_is_exact_not_just_the_bounding_box() -> None:
    found = provider().search_nearby(query(radius_m=100, limit=20))
    assert all((p.distance_m or 0) <= 100 for p in found)


def address_of(name: str) -> str | None:
    row = next(r for r in overture_rows() if r["name"] == name)
    (found,) = provider(FixtureSource([row])).search_nearby(
        PlaceQuery.near(row["lat"], row["lon"], 500)
    )
    return found.address


def test_addresses_are_tidied() -> None:
    assert address_of("Everest Kitchen") == "3, Jalan Yap Ah Loy, Bandar Kuala Lumpur"
    assert address_of("Special Recipe - Wisma Goldhill") == "Bandar Kuala Lumpur"  # no street
    assert address_of("Kedai Mamak Jalan Tar") == "Bandar Kuala Lumpur"  # blank street
    assert address_of("Mountbatten Kuala Lumpur") == "Jalan Tun Razak, Kuala Lumpur"


def test_a_locality_already_in_the_street_is_not_repeated() -> None:
    row = dict(overture_rows()[0], street="1 Jalan Satu, Petaling Jaya", locality="Petaling Jaya")
    (found,) = provider(FixtureSource([row])).search_nearby(
        PlaceQuery.near(row["lat"], row["lon"], 500)
    )
    assert found.address == "1 Jalan Satu, Petaling Jaya"


def test_missing_taxonomy_lists_do_not_break_a_row() -> None:
    row = dict(overture_rows()[0], hierarchy=None, alternates=None, category=None)
    (found,) = provider(FixtureSource([row])).search_nearby(
        PlaceQuery.near(row["lat"], row["lon"], 500)
    )
    assert found.category == "food_and_drink"


def test_the_source_is_asked_for_a_box_around_the_query_with_the_release() -> None:
    source = FixtureSource([])
    provider(source).search_nearby(query(radius_m=1000))
    ((release, bounds),) = source.calls
    assert release == RELEASE
    assert bounds.south < KLCC[0] < bounds.north
    assert bounds.west < KLCC[1] < bounds.east
    assert bounds.north - bounds.south == pytest.approx(2 * 1000 / 111_320)
    assert bounds.east - bounds.west > bounds.north - bounds.south  # longitude degrees are shorter


def test_the_provider_name_carries_the_release_for_the_cache() -> None:
    assert provider().name == f"overture:{RELEASE}"


# Finding the latest release


def catalog(handler: Any) -> OvertureProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OvertureProvider(source=FixtureSource([]), client=client)


def test_without_a_pinned_release_the_latest_is_looked_up_once() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"latest": "2026-09-23.1", "links": []})

    p = catalog(handler)
    assert p.name == "overture:2026-09-23.1"
    p.search_nearby(query())
    assert calls == ["https://stac.overturemaps.org/catalog.json"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"nothing": "here"}),
        httpx.Response(200, json={"latest": ""}),
        httpx.Response(200, json=["list"]),
    ],
)
def test_a_bad_catalog_is_a_places_error_that_says_how_to_pin(response: httpx.Response) -> None:
    with pytest.raises(PlacesError, match="Overture"):
        catalog(lambda request: response).search_nearby(query())


def test_a_network_failure_finding_the_release_is_a_places_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    with pytest.raises(PlacesError, match="MAKAN_OVERTURE_RELEASE"):
        catalog(handler).search_nearby(query())


# The SQL, against a small local file with Overture's schema


@pytest.fixture
def parquet(tmp_path: Path) -> str:
    duckdb = pytest.importorskip("duckdb")
    path = str(tmp_path / "places.parquet")
    connection = duckdb.connect()
    connection.execute(
        """CREATE TABLE places (
            id VARCHAR,
            names STRUCT("primary" VARCHAR),
            taxonomy STRUCT("primary" VARCHAR, hierarchy VARCHAR[], alternates VARCHAR[]),
            bbox STRUCT(xmin DOUBLE, xmax DOUBLE, ymin DOUBLE, ymax DOUBLE),
            addresses STRUCT(freeform VARCHAR, locality VARCHAR)[],
            confidence DOUBLE,
            operating_status VARCHAR
        )"""
    )

    def add(
        id: str,
        name: str | None,
        lat: float,
        lon: float,
        *,
        hierarchy: tuple[str, ...] = ("food_and_drink", "restaurant"),
        confidence: float = 0.9,
        status: str | None = None,
        street: str | None = "1 Jalan Satu",
    ) -> None:
        connection.execute(
            "INSERT INTO places VALUES (?, {'primary': ?}, {'primary': ?, 'hierarchy': ?,"
            " 'alternates': ?}, {'xmin': ?, 'xmax': ?, 'ymin': ?, 'ymax': ?},"
            " [{'freeform': ?, 'locality': 'Kuala Lumpur'}], ?, ?)",
            [
                *(id, name, hierarchy[-1], list(hierarchy), ["thai_restaurant"]),
                *(lon, lon, lat, lat, street, confidence, status),
            ],
        )

    lat, lon = KLCC
    add("good", "Good Place", lat, lon)
    add("no-address", "No Address", lat, lon + 0.001, street=None)
    add("closed", "Closed Place", lat, lon, status="permanently_closed")
    add("paused", "Paused Place", lat, lon, status="temporarily_closed")
    add("junk", "Junk Place", lat, lon, confidence=0.2)
    add("shop", "A Shop", lat, lon, hierarchy=("shopping", "clothing_store"))
    add("nameless", None, lat, lon)
    add("far", "Far Place", lat + 1, lon)
    connection.execute(f"COPY places TO '{path}' (FORMAT parquet)")
    return path


def test_the_sql_keeps_open_confident_named_food_places_in_the_box(parquet: str) -> None:
    rows = DuckDbSource(parquet).fetch(RELEASE, Bounds.around(*KLCC, 1000))
    assert sorted(r["id"] for r in rows) == ["good", "no-address"]
    good = next(r for r in rows if r["id"] == "good")
    assert good == {
        "id": "good",
        "name": "Good Place",
        "category": "restaurant",
        "hierarchy": ["food_and_drink", "restaurant"],
        "alternates": ["thai_restaurant"],
        "lat": KLCC[0],
        "lon": KLCC[1],
        "street": "1 Jalan Satu",
        "locality": "Kuala Lumpur",
    }


def test_the_provider_runs_end_to_end_over_a_local_file(parquet: str) -> None:
    p = OvertureProvider(RELEASE, source=DuckDbSource(parquet))
    found = p.search_nearby(query(cuisine="thai"))
    assert [(x.name, x.distance_m) for x in found] == [("Good Place", 0), ("No Address", 111)]
    assert found[1].address == "Kuala Lumpur"


def test_a_missing_file_is_a_places_error(tmp_path: Path) -> None:
    pytest.importorskip("duckdb")
    source = DuckDbSource(str(tmp_path / "missing.parquet"))
    with pytest.raises(PlacesError, match="Overture query failed"):
        source.fetch(RELEASE, Bounds.around(*KLCC, 1000))


def test_the_release_is_substituted_into_the_path(tmp_path: Path) -> None:
    pytest.importorskip("duckdb")
    source = DuckDbSource(str(tmp_path / "{release}.parquet"))
    with pytest.raises(PlacesError, match=RELEASE):
        source.fetch(RELEASE, Bounds.around(*KLCC, 1000))


class RecordingDuckDb:
    """Stands in for the `duckdb` module and records every statement run on its connection."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.config: dict[str, Any] | None = None

    Error = Exception

    def connect(self, config: dict[str, Any] | None = None) -> "RecordingDuckDb":
        self.config = config
        return self

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def test_the_s3_connection_is_anonymous_whatever_aws_credentials_the_machine_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A placeholder key in the environment made S3 answer 403 for this public bucket.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "YOUR_KEY_ID")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "YOUR_SECRET")
    monkeypatch.setenv("AWS_PROFILE", "placeholder")
    duckdb = RecordingDuckDb()
    DuckDbSource()._connect(duckdb, PLACES_PATH.format(release=RELEASE))
    assert duckdb.statements[-1] == ANONYMOUS_S3_SECRET
    assert "KEY_ID ''" in ANONYMOUS_S3_SECRET and "SECRET ''" in ANONYMOUS_S3_SECRET
    assert "credential_chain" not in ANONYMOUS_S3_SECRET.lower()
    assert "us-west-2" in ANONYMOUS_S3_SECRET


def test_a_local_file_needs_no_s3_setup(tmp_path: Path) -> None:
    duckdb = RecordingDuckDb()
    DuckDbSource()._connect(duckdb, str(tmp_path / "places.parquet"))
    assert duckdb.statements == []


def test_duckdb_picks_the_anonymous_secret_over_aws_environment_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duckdb = pytest.importorskip("duckdb")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "YOUR_KEY_ID")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "YOUR_SECRET")
    connection = duckdb.connect()
    try:
        connection.execute("LOAD httpfs")  # no INSTALL: that would download it
    except duckdb.Error:
        pytest.skip("the DuckDB httpfs extension is not installed here")
    connection.execute(ANONYMOUS_S3_SECRET)
    path = PLACES_PATH.format(release=RELEASE)
    chosen = connection.execute("SELECT name FROM which_secret(?, 's3')", [path]).fetchone()
    assert chosen == ("makan_overture",)
    secret = connection.execute(
        "SELECT secret_string FROM duckdb_secrets() WHERE name = 'makan_overture'"
    ).fetchone()
    assert secret is not None and "key_id=;" in secret[0]


# The bounds on one DuckDB connection


class FakeDuckError(Exception):
    pass


class FakeCursor:
    """A query that runs until it is interrupted, like a remote read that never answers."""

    description: ClassVar[list[tuple[str]]] = [("id",)]

    def __init__(self) -> None:
        self.interrupted = threading.Event()

    def interrupt(self) -> None:
        self.interrupted.set()

    def execute(self, sql: str, params: list[Any]) -> None:
        if not self.interrupted.wait(timeout=5):
            raise AssertionError("the deadline never interrupted the query")
        raise FakeDuckError("INTERRUPT Error: Interrupted!")


class FakeConnection:
    def __init__(self, config: dict[str, Any] | None, failing: tuple[str, ...] = ()) -> None:
        self.config = config
        self.statements: list[str] = []
        self.failing = failing

    def execute(self, statement: str) -> None:
        self.statements.append(statement)
        if statement in self.failing:
            raise FakeDuckError(f"{statement} failed")

    def cursor(self) -> FakeCursor:
        return FakeCursor()


class FakeDuckModule:
    Error = FakeDuckError

    def __init__(self, failing: tuple[str, ...] = ()) -> None:
        self.failing = failing
        self.connections: list[FakeConnection] = []

    def connect(self, config: dict[str, Any] | None = None) -> FakeConnection:
        self.connections.append(FakeConnection(config, self.failing))
        return self.connections[-1]


@pytest.fixture
def fake_duckdb(monkeypatch: pytest.MonkeyPatch) -> FakeDuckModule:
    module = FakeDuckModule()
    monkeypatch.setitem(sys.modules, "duckdb", module)
    return module


def test_a_query_past_its_deadline_is_interrupted_and_reported_as_late(
    fake_duckdb: FakeDuckModule, tmp_path: Path
) -> None:
    source = DuckDbSource(str(tmp_path / "places.parquet"), DuckDbLimits(deadline_s=0.05))
    with pytest.raises(PlacesError, match="deadline"):
        source.fetch(RELEASE, Bounds.around(*KLCC, 1000))


def test_the_connection_is_opened_with_the_limits(
    fake_duckdb: FakeDuckModule, tmp_path: Path
) -> None:
    limits = DuckDbLimits(threads=1, memory_mb=300, work_dir=str(tmp_path / "work"))
    DuckDbSource(limits=limits)._connect(fake_duckdb, PLACES_PATH.format(release=RELEASE))
    config = fake_duckdb.connections[0].config
    assert config is not None
    assert config["threads"] == 1 and config["memory_limit"] == "300MB"
    assert config["extension_directory"] == str(tmp_path / "work" / "extensions")
    assert config["temp_directory"] == str(tmp_path / "work" / "tmp")
    assert config["home_directory"] == str(tmp_path / "work" / "home")
    assert (tmp_path / "work" / "extensions").is_dir()
    assert str(config["max_temp_directory_size"]).endswith("MB")


def test_remote_reads_are_given_a_short_timeout_and_one_retry(
    fake_duckdb: FakeDuckModule,
) -> None:
    DuckDbSource()._connect(fake_duckdb, PLACES_PATH.format(release=RELEASE))
    statements = fake_duckdb.connections[0].statements
    assert "SET http_timeout = 10" in statements and "SET http_retries = 1" in statements
    assert statements[-1] == ANONYMOUS_S3_SECRET


def test_an_extension_that_is_already_there_is_loaded_without_a_download(
    fake_duckdb: FakeDuckModule,
) -> None:
    DuckDbSource()._connect(fake_duckdb, PLACES_PATH.format(release=RELEASE))
    statements = fake_duckdb.connections[0].statements
    assert statements[0] == "LOAD httpfs" and "INSTALL httpfs" not in statements


def test_a_missing_extension_is_installed_once_and_then_loaded() -> None:
    class Missing(FakeDuckModule):
        def connect(self, config: dict[str, Any] | None = None) -> FakeConnection:
            connection = super().connect(config)
            real = connection.execute

            def execute(statement: str) -> None:
                real(statement)
                if statement == "LOAD httpfs" and "INSTALL httpfs" not in connection.statements:
                    raise FakeDuckError("not installed")

            connection.execute = execute  # type: ignore[method-assign]
            return connection

    duckdb = Missing()
    DuckDbSource()._connect(duckdb, PLACES_PATH.format(release=RELEASE))
    assert duckdb.connections[0].statements[:3] == ["LOAD httpfs", "INSTALL httpfs", "LOAD httpfs"]


def test_an_extension_that_cannot_be_installed_is_a_named_places_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing = FakeDuckModule(failing=("LOAD httpfs", "INSTALL httpfs"))
    monkeypatch.setitem(sys.modules, "duckdb", failing)
    with pytest.raises(PlacesError, match="httpfs extension could not be installed"):
        DuckDbSource().fetch(RELEASE, Bounds.around(*KLCC, 1000))


@pytest.mark.parametrize("home", [None, ""], ids=["HOME unset", "HOME empty"])
def test_a_process_with_no_home_directory_can_still_install_an_extension(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, home: str | None
) -> None:
    # A serverless function has no HOME, and DuckDB's INSTALL then failed with "Can't find the
    # home directory at ''" even with `extension_directory` set. The repository here is an empty
    # local directory, so nothing is downloaded: the install gets past the home directory and
    # fails only because the repository holds no extension.
    duckdb = pytest.importorskip("duckdb")
    if home is None:
        monkeypatch.delenv("HOME", raising=False)
    else:
        monkeypatch.setenv("HOME", home)
    repository = tmp_path / "repository"
    repository.mkdir()
    connection = duckdb.connect(config=DuckDbLimits(work_dir=str(tmp_path / "work")).settings())
    with pytest.raises(duckdb.Error) as raised:
        connection.execute(f"INSTALL httpfs FROM '{repository}'")
    assert "home directory" not in str(raised.value)
    assert "Failed to install local extension" in str(raised.value)


class StalledInstall(FakeDuckModule):
    """`INSTALL httpfs` waits for `release`, like a download from a host that never answers."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()
        self.installs = 0

    def connect(self, config: dict[str, Any] | None = None) -> FakeConnection:
        connection = super().connect(config)
        run = connection.execute
        installed = threading.Event()

        def execute(statement: str) -> None:
            if statement == "INSTALL httpfs":
                self.installs += 1
                self.release.wait(timeout=5)
                installed.set()
            elif statement == "LOAD httpfs" and not (installed.is_set() or self.release.is_set()):
                raise FakeDuckError("not installed")
            run(statement)

        connection.execute = execute  # type: ignore[method-assign]
        return connection


def test_a_stalled_extension_download_is_a_places_error_within_its_deadline() -> None:
    duckdb = StalledInstall()
    source = DuckDbSource(limits=DuckDbLimits(install_deadline_s=0.05))
    started = time.monotonic()
    try:
        with pytest.raises(PlacesError, match=r"did not finish within 0\.05 seconds"):
            source._connect(duckdb, PLACES_PATH.format(release=RELEASE))
        assert time.monotonic() - started < 2
        assert source._connection is None  # nothing half set up is kept
    finally:
        duckdb.release.set()


def test_a_download_that_outlasts_its_deadline_is_not_started_twice_and_is_used_when_done() -> None:
    duckdb = StalledInstall()
    source = DuckDbSource(limits=DuckDbLimits(install_deadline_s=0.05))
    path = PLACES_PATH.format(release=RELEASE)
    for _ in range(2):
        with pytest.raises(PlacesError, match="did not finish"):
            source._connect(duckdb, path)
    assert duckdb.installs == 1
    duckdb.release.set()
    assert source._installer is not None
    source._installer.join(timeout=5)
    assert source._connect(duckdb, path) is duckdb.connections[-1]
    assert duckdb.installs == 1  # the finished download was loaded, not repeated


def test_an_unwritable_work_directory_is_a_places_error(
    fake_duckdb: FakeDuckModule, tmp_path: Path
) -> None:
    (tmp_path / "file").write_text("not a directory")
    source = DuckDbSource(limits=DuckDbLimits(work_dir=str(tmp_path / "file" / "inside")))
    with pytest.raises(PlacesError, match="work directory"):
        source.fetch(RELEASE, Bounds.around(*KLCC, 1000))


def test_the_limits_apply_to_a_real_connection(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    limits = DuckDbLimits(threads=1, memory_mb=300, work_dir=str(tmp_path))
    connection = duckdb.connect(config=limits.settings())
    row = connection.execute(
        "SELECT current_setting('threads'), current_setting('extension_directory')"
    ).fetchone()
    assert row == (1, str(tmp_path / "extensions"))
    assert "MiB" in connection.execute("SELECT current_setting('memory_limit')").fetchone()[0]
