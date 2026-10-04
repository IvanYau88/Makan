"""The Overture provider with no network: fixture rows, a mock catalog, and a local Parquet file."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from makan.places import OvertureProvider, PlaceQuery, PlacesError
from makan.places.overture import Bounds, DuckDbSource, Row
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
