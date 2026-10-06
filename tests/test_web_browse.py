"""Browse nearby: places only, nearest first, no request, no model call, no pick."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from makan.browse import BrowseRequest
from makan.config import Config
from makan.places import FakePlacesProvider, PlacesError
from makan.providers import FakeProvider, ToolCall
from makan.providers.fake import call
from makan.web import create_app
from tests.helpers import place

CONFIG = Config(model="test/classifier")
STAGES = [
    "classify",
    "intent",
    "requested_places",
    "nearby_places",
    "memory",
    "merge",
    "rank",
    "explain",
]
THAI = place("Mid Thai", "thai_restaurant", 3.1485, 101.6951)
NEAR = place("Near Ramen", "ramen_restaurant", 3.1481, 101.6951)
FAR = place("Far Thai", "thai_restaurant", 3.152, 101.695)
BROWSE: dict[str, Any] = {"mode": "browse", "latitude": 3.14812, "longitude": 101.69498}


def client(
    places: FakePlacesProvider | None = None, provider: FakeProvider | None = None
) -> TestClient:
    return TestClient(
        create_app(
            # An empty script fails the test if anything asks the model for an answer.
            provider=provider or FakeProvider([]),
            places=places or FakePlacesProvider([FAR, THAI, NEAR]),
            config=CONFIG,
        )
    )


def test_browse_lists_the_places_nearest_first_with_no_pick() -> None:
    provider = FakeProvider([])
    response = client(provider=provider).post("/api/recommendations", json=BROWSE)
    assert response.status_code == 200
    body = response.json()
    assert provider.requests == []
    assert [p["name"] for p in body["places"]] == ["Near Ramen", "Mid Thai", "Far Thai"]
    assert [p["rank"] for p in body["places"]] == [1, 2, 3]
    assert body["places"] == sorted(body["places"], key=lambda p: p["distance_m"])
    assert body["pick"] is None and body["runners_up"] == [] and body["intent"] is None
    assert not any(p["matched"] for p in body["places"])
    assert body["query"] == {
        "mode": "browse",
        "latitude": 3.148,
        "longitude": 101.695,
        "radius_m": 1000,
        "request": None,
    }
    assert body["candidate_count"] == 3 and body["truncated"] is False and body["partial"] is False
    assert any("Opening hours" in w for w in body["warnings"])
    assert "recommend" in body["explanation"] and "Try " not in body["explanation"]
    assert all("from your approximate location" in p["reasons"][0] for p in body["places"])


def test_browse_searches_once_with_no_filters() -> None:
    places = FakePlacesProvider([NEAR])
    client(places=places).post("/api/recommendations", json={**BROWSE, "radius_m": 400})
    (query,) = places.queries
    assert (query.radius_m, query.cuisine, query.category, query.limit) == (400, None, None, 20)


def test_a_full_browse_says_the_list_is_bounded() -> None:
    many = [place(f"Place {i:02}", "cafe", 3.148 + i * 0.00001, 101.695) for i in range(25)]
    body = client(places=FakePlacesProvider(many)).post("/api/recommendations", json=BROWSE).json()
    assert body["truncated"] is True
    assert body["candidate_count"] == len(body["places"]) == 20


def test_browse_with_nothing_nearby_is_a_successful_empty_answer() -> None:
    response = client(places=FakePlacesProvider([])).post("/api/recommendations", json=BROWSE)
    assert response.status_code == 200
    body = response.json()
    assert body["places"] == [] and body["run"]["outcome"] == "no_result"


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"request": "thai please"}, "request"),
        ({"request": ""}, "request"),
        ({"request": None, "latitude": 91}, "latitude"),
        ({"radius_m": 50}, "radius_m"),
        ({"user_id": "5a5b1f1e-0000-4000-8000-000000000000"}, "user_id"),
    ],
)
def test_browse_rejects_a_request_and_unknown_fields(change: dict[str, Any], field: str) -> None:
    response = client().post("/api/recommendations", json={**BROWSE, **change})
    assert response.status_code == 422
    assert response.json()["error"]["message"] == f"Check these fields: {field}."


@pytest.mark.parametrize(
    "body",
    [
        {"latitude": 3.1, "longitude": 101.7, "request": "thai"},  # no mode
        {"mode": "surprise", "latitude": 3.1, "longitude": 101.7},
        {"mode": "recommend", "latitude": 3.1, "longitude": 101.7},  # no request
        {"mode": "recommend", "latitude": 3.1, "longitude": 101.7, "request": None},
        {"mode": "recommend", "latitude": 3.1, "longitude": 101.7, "request": "  \n"},
    ],
)
def test_a_recommendation_still_needs_a_mode_and_a_request(body: dict[str, Any]) -> None:
    provider = FakeProvider([])
    for route in ("/api/recommendations", "/api/recommendations/stream"):
        response = client(provider=provider).post(route, json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
    assert provider.requests == []


def test_a_browse_run_runs_one_stage_and_skips_the_rest() -> None:
    run = client().post("/api/recommendations", json=BROWSE).json()["run"]
    assert run["graph"] == "browse_nearby" and run["search_mode"] == "browse"
    assert (run["status"], run["graph_ok"], run["outcome"]) == ("ok", True, "complete")
    assert run["request"] is None
    assert [s["name"] for s in run["stages"]] == STAGES
    by_name = {s["name"]: s for s in run["stages"]}
    searched = by_name["nearby_places"]
    assert searched["status"] == "ok" and searched["output"]["count"] == 3
    assert searched["input"]["tool"] == "search_nearby_places"
    assert "cuisine" not in searched["input"]
    assert [c["kind"] for c in searched["calls"]] == ["tool"]
    skipped = [s for s in run["stages"] if s["name"] != "nearby_places"]
    assert {s["status"] for s in skipped} == {"skipped"}
    assert all(s["input"] is None and s["output"] is None and s["calls"] == [] for s in skipped)
    assert all(s["started_ms"] is None and s["duration_ms"] is None for s in skipped)
    # The skipped stages keep the wiring they have in the solo graph.
    assert by_name["merge"]["after"] == ["intent", "requested_places", "nearby_places", "memory"]
    assert by_name["rank"]["after"] == ["merge"]


def test_a_recommendation_run_has_no_skipped_stage() -> None:
    intent: dict[str, Any] = {"cuisine": None, "category": None, "requirements": []}
    provider = FakeProvider([call(ToolCall.of("finish", answer=json.dumps(intent)))])
    body = {"mode": "recommend", "latitude": 3.14812, "longitude": 101.69498, "request": "food"}
    run = client(provider=provider).post("/api/recommendations", json=body).json()["run"]
    assert run["search_mode"] == "recommend"
    assert {s["status"] for s in run["stages"]} == {"ok"}


def test_the_stream_follows_a_browse_run_to_its_result() -> None:
    response = client().post("/api/recommendations/stream", json=BROWSE)
    lines = [json.loads(line) for line in response.text.splitlines()]
    assert {line["type"] for line in lines[:-1]} == {"run"}
    assert lines[-1]["type"] == "result"
    assert lines[-1]["recommendation"]["pick"] is None
    started = [line["run"] for line in lines[:-1] if line["run"]["stages"]]
    assert all(
        {s["name"]: s["status"] for s in run["stages"]}["classify"] == "skipped" for run in started
    )
    assert lines[-1]["recommendation"]["run"]["outcome"] == "complete"


def test_a_failed_browse_search_is_a_places_error_with_its_run() -> None:
    places = FakePlacesProvider([], error=PlacesError("Overture query failed: password=hunter2"))
    response = client(places=places).post("/api/recommendations", json=BROWSE)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "places_error"
    assert "hunter2" not in response.text
    streamed = client(places=places).post("/api/recommendations/stream", json=BROWSE)
    last = json.loads(streamed.text.splitlines()[-1])
    assert last["type"] == "error" and last["status"] == 502
    assert last["error"]["code"] == "places_error"
    assert last["run"]["outcome"] == "failed"
    by_name = {s["name"]: s for s in last["run"]["stages"]}
    assert by_name["nearby_places"]["status"] == "error"
    assert by_name["classify"]["status"] == "skipped"
    assert "hunter2" not in streamed.text


def test_a_browse_trace_holds_only_the_rounded_search() -> None:
    assert BrowseRequest(3.14812, 101.69498, 500).trace_summary() == {
        "latitude": 3.148,
        "longitude": 101.695,
        "radius_m": 500,
    }
