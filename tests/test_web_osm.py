"""OpenStreetMap facts in the web API: soft hints with their own source, credit and licence."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from makan.config import Config
from makan.places import FakePlacesProvider, Place, PlaceQuery
from makan.places.facts import OSM_LICENCE, FactSource, facts_from_tags
from makan.places.osm import OsmEnrichedProvider, OsmExtract
from makan.places.osm_build import elements_from, write_extract
from makan.places.osm_format import Box
from makan.providers import FakeProvider, ToolCall
from makan.providers.fake import call
from makan.solo import SoloRequest, recommend
from makan.web import create_app
from makan.web.public import public_env
from tests.helpers import KLCC, place

CONFIG = Config(model="test/classifier")
SOURCE = FactSource("openstreetmap", "2026-11-08T09:30:00Z", OSM_LICENCE, "2026-11-08")
BROWSE: dict[str, Any] = {"mode": "browse", "latitude": 3.14812, "longitude": 101.69498}


def with_osm(venue: Place, **tags: str) -> Place:
    return replace(venue, osm=facts_from_tags("node/42", tags, SOURCE))


THAI = with_osm(
    place("Mid Thai", "thai_restaurant", 3.1485, 101.6951),
    cuisine="thai;noodle;bubble_tea",
    opening_hours="Mo-Fr 11:00-22:00; Sa,Su 12:00-23:00; PH off",
    takeaway="yes",
    delivery="no",
    outdoor_seating="yes",
    wheelchair="limited",
    check_date="2025-06",
    **{"diet:vegan": "only", "diet:gluten_free": "limited", "website:menu": "venue.example/m"},
)
ODD_HOURS = with_osm(
    place("Odd Hours", "cafe", 3.1482, 101.6951), opening_hours="sunrise-sunset", takeaway="no"
)
PLAIN = place("Plain Ramen", "ramen_restaurant", 3.1481, 101.6951)


def browse(*places: Place) -> dict[str, Any]:
    app = create_app(provider=FakeProvider([]), places=FakePlacesProvider(places), config=CONFIG)
    response = TestClient(app).post("/api/recommendations", json=BROWSE)
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def by_name(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in body["places"]}


def test_a_matched_place_carries_its_osm_facts_with_their_source() -> None:
    osm = by_name(browse(THAI, PLAIN))["Mid Thai"]["osm"]
    assert osm["ref"] == "node/42" and osm["url"] == "https://www.openstreetmap.org/node/42"
    assert osm["cuisines"] == [
        {"id": "thai", "label": "Thai"},
        {"id": "noodle", "label": "Noodle"},
        {"id": "bubble_tea", "label": "Bubble tea"},
    ]
    assert osm["diets"] == [
        {"id": "vegan", "label": "Vegan", "level": "only"},
        {"id": "gluten_free", "label": "Gluten-free", "level": "limited"},
    ]
    assert (osm["takeaway"], osm["delivery"], osm["outdoor_seating"]) == ("yes", "no", "yes")
    assert osm["wheelchair"] == "limited"
    assert osm["menu_url"] == "https://venue.example/m"
    assert osm["check_date"] == "2025-06"
    assert osm["source"] == {
        "source": "openstreetmap",
        "version": "2026-11-08T09:30:00Z",
        "licence": "ODbL-1.0",
        "retrieved": "2026-11-08",
        "credit": "© OpenStreetMap contributors",
        "licence_url": "https://www.openstreetmap.org/copyright",
    }


def test_hours_are_read_at_display() -> None:
    hours = by_name(browse(THAI))["Mid Thai"]["osm"]["hours"]
    assert hours["status"] == "listed" and hours["holidays_differ"] is True
    assert hours["days"][0] == [[660, 1320]] and hours["days"][6] == [[720, 1380]]


def test_unreadable_hours_come_back_as_text_never_as_closed() -> None:
    hours = by_name(browse(ODD_HOURS))["Odd Hours"]["osm"]["hours"]
    assert hours == {"status": "unreadable", "text": "sunrise-sunset"}


def test_a_result_with_osm_facts_credits_openstreetmap() -> None:
    body = browse(THAI, PLAIN)
    assert body["osm_credit"] == {
        "text": "© OpenStreetMap contributors",
        "url": "https://www.openstreetmap.org/copyright",
        "licence": "ODbL 1.0",
        "as_of": "2026-11-08T09:30:00Z",
    }
    assert by_name(body)["Plain Ramen"]["osm"] is None


def test_a_result_without_osm_facts_has_no_credit_and_the_same_shape() -> None:
    body = browse(PLAIN)
    assert body["osm_credit"] is None and body["places"][0]["osm"] is None
    assert any("Opening hours" in w for w in body["warnings"])


def test_soft_diet_tags_never_relax_a_stated_requirement() -> None:
    """A vegan-only tag is a hint: the requirement stays unverified and the order unchanged."""

    def run(places: list[Place]) -> Any:
        intent = {"cuisine": None, "category": None, "requirements": ["vegan", "peanut allergy"]}
        result = recommend(
            SoloRequest(KLCC[0], KLCC[1], "vegan please, no peanuts"),
            provider=FakeProvider([call(ToolCall.of("finish", answer=json.dumps(intent)))]),
            places=FakePlacesProvider(places),
            config=CONFIG,
        )
        assert result.recommendation is not None
        return result.recommendation

    vegan = with_osm(place("Green Bowl", "restaurant", 3.1490, 101.6955), **{"diet:vegan": "only"})
    tagged = run([PLAIN, vegan])
    untagged = run([PLAIN, replace(vegan, osm=None)])
    assert "Cannot verify requirement: vegan." in tagged.warnings
    assert "Cannot verify requirement: peanut allergy." in tagged.warnings
    assert tagged.warnings == untagged.warnings
    assert [r.place.name for r in tagged.ranked] == [r.place.name for r in untagged.ranked]
    assert [r.reasons for r in tagged.ranked] == [r.reasons for r in untagged.ranked]
    assert tagged.ranked[-1].place.osm is not None  # the facts did arrive


def test_trace_summaries_hold_no_osm_facts() -> None:
    from makan.solo import Candidate

    candidate = Candidate.from_tool(
        {
            "id": "x",
            "name": "X",
            "category": "cafe",
            "distance_m": 3,
            "osm": THAI.osm.to_json() if THAI.osm else None,
        }
    )
    assert candidate.osm == THAI.osm
    assert "osm" not in candidate.trace_summary()


def test_the_public_site_defaults_to_the_built_extract_path(tmp_path: Path) -> None:
    path = tmp_path / "osm" / "places.parquet"
    assert public_env({}, osm_extract=path)["MAKAN_OSM_EXTRACT"] == str(path)
    assert public_env({"MAKAN_OSM_EXTRACT": "other"}, osm_extract=path)["MAKAN_OSM_EXTRACT"] == (
        "other"
    )


def test_a_real_extract_end_to_end_through_the_api(tmp_path: Path) -> None:
    raw = [
        {
            "type": "node",
            "id": 7,
            "lat": 3.1485,
            "lon": 101.6951,
            "tags": {"amenity": "restaurant", "name": "Mid Thai", "opening_hours": "24/7"},
        }
    ]
    out = tmp_path / "places.parquet"
    box = Box(101.69, 3.14, 101.70, 3.15)
    write_extract(
        elements_from(raw),
        out,
        region="T",
        box=box,
        osm_base="2026-11-08T09:30:00Z",
        built=datetime(2026, 11, 8, tzinfo=UTC),
    )
    inner = FakePlacesProvider([replace(THAI, osm=None), PLAIN], name="overture:test")
    places = OsmEnrichedProvider(inner, OsmExtract(str(out)))
    app = create_app(provider=FakeProvider([]), places=places, config=CONFIG)
    body = TestClient(app).post("/api/recommendations", json=BROWSE).json()
    assert body["data_source"] == "overture:test+osm:2026-11-08T09:30:00Z"
    osm = by_name(body)["Mid Thai"]["osm"]
    assert osm["ref"] == "node/7" and osm["hours"]["always_open"] is True
    assert body["osm_credit"]["as_of"] == "2026-11-08T09:30:00Z"


@pytest.mark.parametrize("missing", ["", "does/not/exist.parquet"])
def test_without_an_extract_the_api_is_unchanged(missing: str) -> None:
    inner = FakePlacesProvider([PLAIN], name="overture:test")
    places = OsmEnrichedProvider(inner, OsmExtract(missing))
    app = create_app(provider=FakeProvider([]), places=places, config=CONFIG)
    body = TestClient(app).post("/api/recommendations", json=BROWSE).json()
    assert body["data_source"] == "overture:test" and body["osm_credit"] is None
    assert inner.queries == [PlaceQuery.near(3.14812, 101.69498, 1000, limit=20)]
