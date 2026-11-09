"""The regional OpenStreetMap extract: how it is built, read, and joined to Overture places.

The Overpass answers here are synthetic, in Overpass's real JSON shape, and every HTTP call goes
to a mock transport, so nothing reaches the network.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from makan.config import Config
from makan.places import FakePlacesProvider, Place, PlaceQuery
from makan.places.cache import CachedPlacesProvider
from makan.places.factory import places_provider
from makan.places.facts import OSM_LICENCE
from makan.places.osm import (
    MATCH_RADIUS_M,
    OsmElement,
    OsmEnrichedProvider,
    OsmExtract,
    match,
    name_key,
)
from makan.places.osm_build import (
    MAX_TILES,
    OVERPASS_URL,
    USER_AGENT,
    Element,
    build,
    elements_from,
    main,
    overpass_query,
    tiles,
    write_extract,
)
from makan.places.osm_format import Box, ExtractError
from makan.places.tool import search_nearby_places
from tests.helpers import place

BASE = "2026-11-08T09:30:00Z"
BUILT = datetime(2026, 11, 8, 12, tzinfo=UTC)
BOX = Box(101.69, 3.14, 101.70, 3.15)
METER = 1 / 111_320  # about a meter of latitude, in degrees


def node(osm_id: int, lat: float, lon: float, **tags: str) -> dict[str, Any]:
    return {"type": "node", "id": osm_id, "lat": lat, "lon": lon, "tags": tags}


def overpass(*elements: dict[str, Any], base: str = BASE) -> dict[str, Any]:
    return {"version": 0.6, "osm3s": {"timestamp_osm_base": base}, "elements": list(elements)}


ELEMENTS = [
    node(
        1,
        3.1485,
        101.6951,
        amenity="restaurant",
        name="Mid Thai",
        cuisine="thai;noodle",
        opening_hours="Mo-Su 11:00-22:00",
        takeaway="yes",
        wheelchair="no",
        **{"diet:vegan": "yes", "phone": "+60 3 0000 0000"},
    ),
    {
        "type": "way",
        "id": 2,
        "center": {"lat": 3.1481, "lon": 101.6951},
        "tags": {"amenity": "fast_food", "name": "The Near Ramen", "delivery": "only"},
    },
    node(3, 3.1490, 101.6960, amenity="cafe", name="Quiet Cafe"),  # matches, but has no facts
]


def write(tmp_path: Path, *elements: dict[str, Any], box: Box = BOX) -> Path:
    out = tmp_path / "osm" / "places.parquet"
    found = [e for e in elements_from(elements) if box.south <= e.lat <= box.north]
    write_extract(found, out, region="Test", box=box, osm_base=BASE, built=BUILT)
    return out


def enriched(path: Path, *places: Place) -> OsmEnrichedProvider:
    return OsmEnrichedProvider(
        FakePlacesProvider(places, name="overture:test"), OsmExtract(str(path))
    )


QUERY = PlaceQuery.near(3.1483, 101.6951, 1000)


def plain(*places: Place, query: PlaceQuery = QUERY) -> list[Place]:
    """What the inner provider alone returns for the query."""
    return FakePlacesProvider(places).search_nearby(query)


MID_THAI = place("Mid Thai", "thai_restaurant", 3.1485, 101.6951)
NEAR_RAMEN = place("Near Ramen", "ramen_restaurant", 3.1481, 101.6951)
QUIET_CAFE = place("Quiet Cafe", "cafe", 3.1490, 101.6960)


# Names and the match rule


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Joe's Pizza", "JOES PIZZA"),
        ("Joe\u2019s Pizza", "Joes Pizza"),
        ("Café Lalo", "Cafe Lalo"),
        ("Ben & Jerry's", "Ben and Jerrys"),
        ("The Halal Guys", "Halal Guys"),
        ("Shake Shack", "ShakeShack"),
        ("Pret A Manger", "Pret-A-Manger"),
    ],
)
def test_spelling_noise_does_not_matter(a: str, b: str) -> None:
    assert name_key(a) == name_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Joe's", "Joe's Pizza"),
        ("Pad Thai", "Pad Thai Asian Kitchen"),
        ("Starbucks", "Starbucks Reserve"),
        ("Thai Basil", "Thai Basil 2"),
    ],
)
def test_different_words_never_match(a: str, b: str) -> None:
    assert name_key(a) != name_key(b)


def element(ref: str, lat: float, lon: float, *names: str) -> OsmElement:
    from makan.places.facts import FactSource, facts_from_tags

    source = FactSource("openstreetmap", BASE, OSM_LICENCE, "2026-11-08")
    facts = facts_from_tags(ref, {"takeaway": "yes"}, source)
    return OsmElement(ref, lat, lon, frozenset(name_key(n) for n in names), facts)


def test_a_same_named_element_within_the_radius_matches() -> None:
    venue = place("Mid Thai", "thai_restaurant", 3.0, 101.0)
    near = element("node/1", 3.0 + (MATCH_RADIUS_M - 2) * METER, 101.0, "Mid Thai")
    assert match(venue, [near]) == near


def test_a_same_named_element_past_the_radius_does_not() -> None:
    venue = place("Mid Thai", "thai_restaurant", 3.0, 101.0)
    far = element("node/1", 3.0 + (MATCH_RADIUS_M + 2) * METER, 101.0, "Mid Thai")
    assert match(venue, [far]) is None


def test_a_nearby_element_with_another_name_does_not() -> None:
    venue = place("Mid Thai", "thai_restaurant", 3.0, 101.0)
    assert match(venue, [element("node/1", 3.0, 101.0, "Thai Palace")]) is None


def test_any_of_an_elements_names_can_match() -> None:
    venue = place("Dunkin Donuts", "coffee_shop", 3.0, 101.0)
    dunkin = element("node/1", 3.0, 101.0, "Dunkin'", "Dunkin Donuts")
    assert match(venue, [dunkin]) == dunkin


def test_two_matching_elements_are_ambiguous_so_neither_matches() -> None:
    venue = place("Starbucks", "coffee_shop", 3.0, 101.0)
    one = element("node/1", 3.0, 101.0, "Starbucks")
    two = element("node/2", 3.0 + 20 * METER, 101.0, "Starbucks")
    assert match(venue, [one, two]) is None


def test_a_place_with_no_name_left_matches_nothing() -> None:
    venue = place("!!!", "restaurant", 3.0, 101.0)
    assert match(venue, [element("node/1", 3.0, 101.0, "!!!")]) is None


# Building the extract


def test_overpass_asks_for_named_food_places_with_tags_and_centers() -> None:
    query = overpass_query(Box(-74.0, 40.7, -73.9, 40.8))
    assert query.startswith("[out:json][timeout:180];")
    assert (
        '["amenity"~"^(restaurant|fast_food|cafe|bar|pub|food_court|ice_cream|biergarten)$"]'
        in (query)
    )
    assert '["name"]' in query and "(40.700000,-74.000000,40.800000,-73.900000)" in query
    assert query.endswith("out tags center;")


def test_elements_keep_only_food_places_with_a_name_a_point_and_the_listed_tags() -> None:
    found = elements_from(
        [
            *ELEMENTS,
            node(1, 3.1485, 101.6951, amenity="restaurant", name="Mid Thai"),  # a repeat
            node(4, 3.1, 101.6, amenity="bench", name="Not food"),
            node(5, 3.1, 101.6, amenity="cafe"),  # no name
            {"type": "way", "id": 6, "tags": {"amenity": "cafe", "name": "No center"}},
            node(7, 3.1, 101.6, amenity="cafe", name="Long", cuisine="x" * 300),
        ]
    )
    assert [(e.osm_type, e.osm_id) for e in found] == [
        ("node", 1),
        ("way", 2),
        ("node", 3),
        ("node", 7),
    ]
    assert "phone" not in found[0].tags and found[0].tags["diet:vegan"] == "yes"
    assert found[1].lat == 3.1481  # a way is placed at its center
    assert "cuisine" not in found[3].tags


def test_a_box_is_cut_into_capped_tiles() -> None:
    assert len(tiles(Box(0, 0, 0.25, 0.25))) == 1
    cut = tiles(Box(0, 0, 0.5, 0.3))
    assert len(cut) == 4
    assert (cut[0].west, cut[-1].east, cut[0].south, cut[-1].north) == (0, 0.5, 0, 0.3)
    with pytest.raises(ExtractError, match="cap"):
        tiles(Box(0, 0, 0.25 * (MAX_TILES + 1), 0.25))


def test_a_build_writes_parquet_with_its_source_in_the_metadata(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=overpass(*ELEMENTS))

    out = tmp_path / "places.parquet"
    client = httpx.Client(transport=httpx.MockTransport(answer))
    count = build(box=BOX, out=out, region="Test", client=client, now=lambda: BUILT)
    assert count == 3
    (request,) = requests
    assert str(request.url) == OVERPASS_URL and request.method == "POST"
    info = OsmExtract(str(out)).info()
    assert info is not None
    assert info.region == "Test" and info.box == Box.parse(BOX.text())
    assert info.source.to_json() == {
        "source": "openstreetmap",
        "version": BASE,
        "licence": "ODbL-1.0",
        "retrieved": "2026-11-08",
    }


def test_the_oldest_tile_dates_the_extract_and_tiles_are_paced(tmp_path: Path) -> None:
    stamps = iter(["2026-11-08T10:00:00Z", "2026-11-07T23:00:00Z"])
    pauses: list[float] = []

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=overpass(base=next(stamps)))

    out = tmp_path / "places.parquet"
    client = httpx.Client(transport=httpx.MockTransport(answer))
    build(box=Box(0, 0, 0.5, 0.25), out=out, region="T", client=client, pause=pauses.append)
    info = OsmExtract(str(out)).info()
    assert info is not None and info.source.version == "2026-11-07T23:00:00Z"
    assert pauses == [2.0]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, text="slow down"),
        httpx.Response(200, text="<html>not json"),
        httpx.Response(200, json={"elements": []}),  # no timestamp
        httpx.Response(200, json={**overpass(), "remark": "runtime error: timed out"}),
    ],
)
def test_any_failed_tile_fails_the_whole_build(tmp_path: Path, response: httpx.Response) -> None:
    answers = iter([httpx.Response(200, json=overpass(*ELEMENTS)), response])
    client = httpx.Client(transport=httpx.MockTransport(lambda request: next(answers)))
    out = tmp_path / "places.parquet"
    with pytest.raises(ExtractError):
        build(box=Box(0, 0, 0.5, 0.25), out=out, region="T", client=client, pause=lambda s: None)
    assert not out.exists() and not list(tmp_path.iterdir())


def test_the_builder_names_itself_to_overpass() -> None:
    assert "github.com/IvanYau88/Makan" in USER_AGENT and "@" not in USER_AGENT


def test_the_command_reads_saved_overpass_json(tmp_path: Path) -> None:
    saved = tmp_path / "overpass.json"
    saved.write_text(json.dumps(overpass(*ELEMENTS)), "utf-8")
    out = tmp_path / "places.parquet"
    assert main(["--bbox", BOX.text(), "--input", str(saved), "--out", str(out)], env={}) == 0
    assert OsmExtract(str(out)).info() is not None


def test_the_command_from_env_does_nothing_without_a_box(tmp_path: Path) -> None:
    out = tmp_path / "places.parquet"
    assert main(["--from-env", "--soft", "--out", str(out)], env={}) == 0
    assert not out.exists()


def test_a_soft_failure_exits_zero_and_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "places.parquet"
    args = ["--bbox", BOX.text(), "--input", str(tmp_path / "missing.json"), "--out", str(out)]
    assert main([*args, "--soft"], env={}) == 0
    assert main(args, env={}) == 1
    assert main(["--bbox", "0,0,10,10", "--out", str(out), "--soft"], env={}) == 0  # too big
    assert not out.exists()


# Reading it, and adding its facts to places


def test_matched_places_gain_facts_and_the_rest_are_unchanged(tmp_path: Path) -> None:
    provider = enriched(write(tmp_path, *ELEMENTS), MID_THAI, NEAR_RAMEN, QUIET_CAFE)
    found = {p.name: p for p in provider.search_nearby(QUERY)}
    thai = found["Mid Thai"].osm
    assert thai is not None
    assert thai.ref == "node/1" and thai.cuisines == ("thai", "noodle")
    assert thai.opening_hours == "Mo-Su 11:00-22:00" and thai.takeaway == "yes"
    assert thai.diets == (("vegan", "yes"),) and thai.wheelchair == "no"
    assert thai.source.version == BASE and thai.source.licence == "ODbL-1.0"
    ramen = found["Near Ramen"].osm
    assert ramen is not None and ramen.ref == "way/2" and ramen.delivery == "only"
    assert (
        found["Quiet Cafe"] == plain(QUIET_CAFE)[0]
    )  # matched, but OpenStreetMap says nothing useful
    # The Overture facts and the order are untouched.
    assert [p.name for p in provider.search_nearby(QUERY)] == [
        "Near Ramen",
        "Mid Thai",
        "Quiet Cafe",
    ]
    assert found["Mid Thai"].cuisines == MID_THAI.cuisines


def test_the_name_carries_the_extract_version_so_a_cache_never_mixes_them(tmp_path: Path) -> None:
    provider = enriched(write(tmp_path, *ELEMENTS), MID_THAI)
    assert provider.name == f"overture:test+osm:{BASE}"
    cached = CachedPlacesProvider(provider)
    assert cached.search_nearby(QUERY)[0].osm is not None


def test_with_no_extract_file_places_pass_through(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    provider = enriched(tmp_path / "missing.parquet", MID_THAI)
    with caplog.at_level(logging.WARNING, logger="makan.places.osm"):
        assert provider.search_nearby(QUERY) == plain(MID_THAI)
        assert provider.search_nearby(QUERY) == plain(MID_THAI)
    assert provider.name == "overture:test"
    assert len(caplog.records) == 1  # said once, not on every search
    assert "no extract file" in caplog.records[0].getMessage()


@pytest.mark.parametrize("content", [b"", b"not parquet at all"])
def test_a_broken_file_passes_through(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "places.parquet"
    path.write_bytes(content)
    assert enriched(path, MID_THAI).search_nearby(QUERY) == plain(MID_THAI)


def test_a_parquet_file_in_another_format_is_not_read(tmp_path: Path) -> None:
    import duckdb

    path = tmp_path / "places.parquet"
    duckdb.connect().execute(f"COPY (SELECT 1 AS a) TO '{path}' (FORMAT parquet)")
    provider = enriched(path, MID_THAI)
    assert provider.search_nearby(QUERY) == plain(MID_THAI) and provider.name == "overture:test"


def test_a_failed_read_passes_through(tmp_path: Path) -> None:
    path = write(tmp_path, *ELEMENTS)
    provider = enriched(path, MID_THAI)
    assert provider.name.endswith(BASE)  # opened while the file was good
    path.write_bytes(b"truncated")
    assert provider.search_nearby(QUERY) == plain(MID_THAI)


def test_places_outside_the_extract_are_not_looked_up(tmp_path: Path) -> None:
    far_away = place("Mid Thai", "thai_restaurant", 40.75, -73.98)
    provider = enriched(write(tmp_path, *ELEMENTS), far_away)
    query = PlaceQuery.near(40.75, -73.98, 500)
    assert provider.search_nearby(query) == plain(far_away, query=query)


def test_an_element_just_past_the_result_edge_still_matches(tmp_path: Path) -> None:
    edge = node(9, 3.1485 + 40 * METER, 101.6951, amenity="cafe", name="Edge", takeaway="no")
    venue = place("Edge", "cafe", 3.1485, 101.6951)
    found = enriched(write(tmp_path, edge), venue).search_nearby(QUERY)
    assert found[0].osm is not None and found[0].osm.takeaway == "no"


def test_the_factory_adds_osm_only_when_a_path_is_set(tmp_path: Path) -> None:
    plain = places_provider(Config(model="m", overture_release="2026-10-21.0"))
    assert plain.name == "overture:2026-10-21.0"
    path = write(tmp_path, *ELEMENTS)
    config = Config(model="m", overture_release="2026-10-21.0", osm_extract=str(path))
    assert places_provider(config).name == f"overture:2026-10-21.0+osm:{BASE}"
    missing = Config(model="m", overture_release="2026-10-21.0", osm_extract=str(tmp_path / "x"))
    assert places_provider(missing).name == "overture:2026-10-21.0"


def test_the_setting_is_read_from_the_environment() -> None:
    env = {"MAKAN_MODEL": "m", "MAKAN_OSM_EXTRACT": " osm/places.parquet "}
    assert Config.from_env(env).osm_extract == "osm/places.parquet"
    assert Config.from_env({"MAKAN_MODEL": "m"}).osm_extract == ""


def test_the_places_tool_gives_the_model_the_facts_and_says_what_they_are(tmp_path: Path) -> None:
    tool = search_nearby_places(enriched(write(tmp_path, *ELEMENTS), MID_THAI))
    result = json.loads(tool.run({"latitude": 3.1483, "longitude": 101.6951}))
    osm = result["places"][0]["osm"]
    assert osm["opening_hours"] == "Mo-Su 11:00-22:00" and osm["diets"] == {"vegan": "yes"}
    assert osm["source"]["licence"] == "ODbL-1.0"
    assert "never verify a dietary or access need" in tool.description


def test_write_needs_a_valid_timestamp(tmp_path: Path) -> None:
    with pytest.raises(ExtractError):
        write_extract(
            [Element("node", 1, 0, 0, {"name": "x"})],
            tmp_path / "x.parquet",
            region="T",
            box=BOX,
            osm_base="yesterday",
            built=BUILT,
        )
