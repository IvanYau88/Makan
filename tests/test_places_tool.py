import json
from typing import Any

import pytest

from makan.loop import run
from makan.places import FakePlacesProvider, PlacesError, search_nearby_places
from makan.providers import FakeProvider, ToolCall
from makan.providers.fake import call
from makan.trace import ListSink
from tests.helpers import place

NEAR = place("Near Noodles", "ramen_restaurant", 3.1481, 101.6951, "food_and_drink", "restaurant")
CAFE = place("Mid Cafe", "cafe", 3.1485, 101.6951, "food_and_drink", "casual_eatery", "cafe")
FAR = place("Far Thai", "thai_restaurant", 3.18, 101.695, "food_and_drink", "restaurant")


def search(provider: FakePlacesProvider, **arguments: Any) -> dict[str, Any]:
    tool = search_nearby_places(provider)
    result: dict[str, Any] = json.loads(tool.run(arguments))
    return result


def test_results_are_compact_and_nearest_first() -> None:
    provider = FakePlacesProvider([CAFE, NEAR, FAR])
    result = search(provider, latitude=3.148, longitude=101.695)
    assert result["searched"] == {"latitude": 3.148, "longitude": 101.695, "radius_m": 1000}
    assert result["count"] == 2
    assert result["places"][0] == {
        "id": "near-noodles",
        "name": "Near Noodles",
        "category": "ramen_restaurant",
        "distance_m": 16,
        "lat": NEAR.lat,
        "lon": NEAR.lon,
    }
    assert [p["name"] for p in result["places"]] == ["Near Noodles", "Mid Cafe"]


def test_filters_radius_and_limit_reach_the_provider() -> None:
    provider = FakePlacesProvider([CAFE, NEAR, FAR])
    result = search(
        provider, latitude=3.148, longitude=101.695, radius_m=5000, cuisine="thai", limit=1
    )
    assert [p["name"] for p in result["places"]] == ["Far Thai"]
    assert result["searched"]["cuisine"] == "thai"
    (query,) = provider.queries
    assert (query.radius_m, query.cuisine, query.category, query.limit) == (5000, "thai", None, 1)


def test_the_location_is_rounded_before_it_reaches_the_provider() -> None:
    provider = FakePlacesProvider([NEAR])
    result = search(provider, latitude=3.14812345, longitude=101.69498765)
    (query,) = provider.queries
    assert (query.lat, query.lon) == (3.148, 101.695)
    assert result["searched"]["latitude"] == 3.148


def test_an_empty_search_says_so_and_stays_valid_json() -> None:
    result = search(FakePlacesProvider([FAR]), latitude=3.148, longitude=101.695)
    assert result["count"] == 0
    assert result["places"] == []


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"latitude": "north", "longitude": 1}, "latitude must be a number"),
        ({"latitude": True, "longitude": 1}, "latitude must be a number"),
        ({"latitude": 95, "longitude": 1}, "latitude must be between"),
        ({"latitude": 1, "longitude": 1, "radius_m": 50}, "radius_m must be between"),
        ({"latitude": 1, "longitude": 1, "radius_m": 1.5}, "radius_m must be a whole number"),
        ({"latitude": 1, "longitude": 1, "limit": 99}, "limit must be between"),
        ({"latitude": 1, "longitude": 1, "cuisine": 7}, "cuisine must be a string"),
    ],
)
def test_bad_arguments_raise_a_message_the_model_can_act_on(
    arguments: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        search(FakePlacesProvider([NEAR]), **arguments)


def test_blank_filters_are_ignored() -> None:
    provider = FakePlacesProvider([NEAR])
    search(provider, latitude=3.148, longitude=101.695, cuisine="  ", category="")
    assert provider.queries[0].cuisine is None
    assert provider.queries[0].category is None


def test_the_tool_is_described_for_the_model() -> None:
    spec = search_nearby_places(FakePlacesProvider()).spec()
    assert spec.name == "search_nearby_places"
    assert spec.parameters["required"] == ["latitude", "longitude"]
    assert set(spec.parameters["properties"]) == {
        "latitude",
        "longitude",
        "radius_m",
        "cuisine",
        "category",
        "limit",
    }
    assert "opening hours" in spec.description  # tells the model what this data cannot answer


def test_inside_the_loop_a_search_reaches_the_model_and_the_trace(sink: ListSink) -> None:
    provider = FakePlacesProvider([NEAR])
    model = FakeProvider(
        [
            call(ToolCall.of("search_nearby_places", "c1", latitude=3.148, longitude=101.695)),
            call(ToolCall.of("finish", "c2", answer="Near Noodles")),
        ]
    )
    result = run(
        "ramen near me",
        provider=model,
        model="test/model",
        tools=[search_nearby_places(provider)],
        sink=sink,
    )
    assert result.status == "finished"
    shown = model.requests[1].messages[-1].content or ""
    assert json.loads(shown)["places"][0]["name"] == "Near Noodles"
    tool_result = next(e for e in sink.events if e.type == "tool_result")
    assert tool_result.data["ok"] is True


def test_a_provider_failure_is_a_tool_error_not_a_crashed_run(sink: ListSink) -> None:
    provider = FakePlacesProvider(error=PlacesError("Overture is unreachable"))
    model = FakeProvider(
        [
            call(ToolCall.of("search_nearby_places", "c1", latitude=3.148, longitude=101.695)),
            call(ToolCall.of("finish", "c2", answer="sorry")),
        ]
    )
    result = run(
        "ramen",
        provider=model,
        model="test/model",
        tools=[search_nearby_places(provider)],
        sink=sink,
    )
    assert result.status == "finished"
    assert model.requests[1].messages[-1].content == "PlacesError: Overture is unreachable"
    tool_result = next(e for e in sink.events if e.type == "tool_result")
    assert tool_result.data["ok"] is False
