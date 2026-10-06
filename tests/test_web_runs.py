"""The run view and the map data the web channel adds to a recommendation, with fakes only."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from makan.config import Config
from makan.places import FakePlacesProvider, Place, PlacesError
from makan.providers import FakeProvider, ProviderBusy, ToolCall
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
BODY: dict[str, Any] = {"latitude": 3.14812, "longitude": 101.69498, "request": "thai please"}


def classify(cuisine: str | None = "thai") -> FakeProvider:
    intent = {"cuisine": cuisine, "category": None, "requirements": ["open now"]}
    return FakeProvider([call(ToolCall.of("finish", answer=json.dumps(intent)), tokens=42)])


def client(
    provider: FakeProvider | None = None, places: FakePlacesProvider | None = None, **kwargs: Any
) -> TestClient:
    app = create_app(
        provider=provider or classify(),
        places=places or FakePlacesProvider([NEAR, THAI]),
        config=kwargs.pop("config", CONFIG),
        **kwargs,
    )
    return TestClient(app)


def stages(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["name"]: s for s in run["stages"]}


class OneSearchFails(FakePlacesProvider):
    def search_nearby(self, query: Any) -> list[Place]:
        if query.cuisine:
            raise PlacesError("Overture query failed: password=hunter2")
        return super().search_nearby(query)


def test_places_keep_their_coordinates_rank_and_match_evidence() -> None:
    body = client().post("/api/recommendations", json=BODY).json()
    assert body["query"] == {
        "latitude": 3.148,
        "longitude": 101.695,
        "radius_m": 1000,
        "request": "thai please",
    }
    assert body["intent"] == {"cuisine": "thai", "category": None}
    assert [p["name"] for p in body["places"]] == ["Mid Thai", "Near Ramen"]
    top, other = body["places"]
    assert (top["lat"], top["lon"]) == (THAI.lat, THAI.lon)
    assert (top["rank"], top["matched"]) == (1, True)
    assert (other["rank"], other["matched"]) == (2, False)
    assert body["pick"]["id"] == top["id"] and body["pick"]["lat"] == THAI.lat
    assert body["runners_up"][0]["rank"] == 2
    assert body["candidate_count"] == 2 and body["truncated"] is False
    assert "matches your request for thai" in top["reasons"]
    assert not any("filter(s)" in r for p in body["places"] for r in p["reasons"])


def test_a_full_search_says_the_list_is_bounded() -> None:
    many = [place(f"Place {i:02}", "cafe", 3.148 + i * 0.00001, 101.695) for i in range(25)]
    body = client(places=FakePlacesProvider(many)).post("/api/recommendations", json=BODY).json()
    assert body["truncated"] is True
    assert body["candidate_count"] == len(body["places"]) == 20


def test_the_run_has_the_eight_real_stages_and_their_evidence() -> None:
    run = client().post("/api/recommendations", json=BODY).json()["run"]
    assert run["graph"] == "solo_recommendation"
    assert (run["status"], run["graph_ok"], run["outcome"]) == ("ok", True, "complete")
    assert (run["mode"], run["data_source"], run["model"]) == ("live", "fake", "test/classifier")
    assert run["center"] == {"latitude": 3.148, "longitude": 101.695}
    assert run["radius_m"] == 1000 and run["request"] == "thai please"
    assert [s["name"] for s in run["stages"]] == STAGES
    by_name = stages(run)
    assert all(s["status"] == "ok" for s in run["stages"])
    assert by_name["merge"]["after"] == ["intent", "requested_places", "nearby_places", "memory"]
    assert by_name["classify"]["output"]["cuisine"] == "thai"
    assert by_name["classify"]["input"]["model"] == "test/classifier"
    (model,) = by_name["classify"]["calls"]
    assert model["kind"] == "model" and model["model_calls"][0]["tokens"] == 42
    assert by_name["intent"]["output"]["requirements"] == ["open now"]
    requested = by_name["requested_places"]
    assert requested["input"]["cuisine"] == "thai" and requested["input"]["limit"] == 20
    assert requested["input"]["tool"] == "search_nearby_places"
    assert requested["output"]["count"] == 1
    assert by_name["nearby_places"]["input"].get("cuisine") is None
    assert by_name["nearby_places"]["output"]["count"] == 2
    assert set(by_name["requested_places"]["parallel_with"]) <= {"nearby_places", "memory"}
    assert by_name["memory"]["output"] == {
        "lookup": False,
        "reason": "no user memory",
        "fact_count": 0,
    }
    assert by_name["merge"]["output"]["candidate_count"] == 2
    assert by_name["rank"]["output"]["top"][0]["name"] == "Mid Thai"
    assert by_name["explain"]["output"]["pick"] == "Mid Thai"
    assert all(s["duration_ms"] is not None and s["started_ms"] is not None for s in run["stages"])


def test_the_run_never_carries_session_secrets_or_raw_model_text() -> None:
    text = client().post("/api/recommendations", json=BODY).text
    for secret in ("link_token", "Session(", "session_id", "tool_calls", "messages"):
        assert secret not in text


def test_a_failed_search_is_a_partial_outcome_and_a_failed_graph() -> None:
    places = OneSearchFails([THAI, NEAR])
    run = client(places=places).post("/api/recommendations", json=BODY).json()["run"]
    assert (run["status"], run["graph_ok"], run["outcome"]) == ("failed", False, "partial")
    by_name = stages(run)
    assert by_name["requested_places"]["status"] == "error"
    assert by_name["nearby_places"]["status"] == "ok" and by_name["merge"]["status"] == "ok"
    assert by_name["requested_places"]["error"] == {
        "type": "PlacesError",
        "message": "Nearby places data was unavailable.",
    }
    assert "hunter2" not in json.dumps(run)
    assert by_name["requested_places"]["calls"][0]["ok"] is False
    warnings = by_name["merge"]["output"]["warnings"]
    assert "Part of the search failed, so these results may be incomplete." in warnings


def test_an_empty_search_is_a_no_result_outcome_with_an_ok_graph() -> None:
    run = (
        client(places=FakePlacesProvider([])).post("/api/recommendations", json=BODY).json()["run"]
    )
    assert (run["status"], run["outcome"]) == ("ok", "no_result")


def test_a_timed_out_stage_says_timed_out_not_cancelled() -> None:
    import threading

    from makan.graph import GraphLimits

    release = threading.Event()

    class Slow(FakePlacesProvider):
        def search_nearby(self, query: Any) -> list[Place]:
            if query.cuisine:
                release.wait(5)
            return super().search_nearby(query)

    config = Config(model="m", graph_limits=GraphLimits(step_timeout_s=0.2))
    try:
        run = (
            client(places=Slow([THAI]), config=config)
            .post("/api/recommendations", json=BODY)
            .json()["run"]
        )
    finally:
        release.set()
    timed_out = stages(run)["requested_places"]
    assert timed_out["status"] == "timeout"
    assert "not cancelled" in timed_out["error"]["message"]
    assert run["outcome"] == "partial"


def test_a_failed_model_names_its_child_run_even_though_it_raised() -> None:
    provider = FakeProvider([ProviderBusy("429 secret details")])
    response = client(provider=provider).post("/api/recommendations/stream", json=BODY)
    lines = [json.loads(line) for line in response.text.splitlines()]
    last = lines[-1]
    assert last["type"] == "error" and last["status"] == 503
    assert last["error"]["code"] == "model_busy"
    run = last["run"]
    assert run["outcome"] == "failed"
    classify_stage = stages(run)["classify"]
    assert classify_stage["status"] == "error"
    assert classify_stage["error"]["message"] == "The language model was busy."
    assert classify_stage["calls"][0]["kind"] == "model"
    assert "secret" not in response.text


def test_the_stream_reports_stages_as_they_progress_then_the_result() -> None:
    response = client().post("/api/recommendations/stream", json=BODY)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in response.text.splitlines()]
    assert {line["type"] for line in lines[:-1]} == {"run"}
    assert lines[-1]["type"] == "result"
    seen = [{s["name"]: s["status"] for s in line["run"]["stages"]} for line in lines[:-1]]
    assert (seen[0]["classify"], seen[0]["intent"], seen[0]["merge"]) == (
        "queued",
        "waiting",
        "waiting",
    )
    assert any(s["classify"] == "running" for s in seen)
    assert seen[-1]["explain"] == "ok"
    assert [line["run"]["status"] for line in lines[:-1]][-1] == "ok"
    assert lines[-1]["recommendation"]["pick"]["name"] == "Mid Thai"
    assert lines[-1]["recommendation"]["run"]["outcome"] == "complete"


def test_the_stream_rejects_bad_input_before_it_starts() -> None:
    response = client().post("/api/recommendations/stream", json={**BODY, "latitude": 100})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_a_crash_inside_the_stream_is_a_generic_error_line(monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("password=hunter2")

    monkeypatch.setattr("makan.web.app.recommend", crash)
    response = client().post("/api/recommendations/stream", json=BODY)
    (line,) = [json.loads(x) for x in response.text.splitlines()]
    assert line["type"] == "error" and line["status"] == 500
    assert "hunter2" not in response.text


def test_the_config_route_gives_the_map_tiles_and_their_attribution() -> None:
    body = client().get("/api/config").json()
    assert body["mode"] == "live"
    assert body["map"]["tile_url"] == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    assert body["map"]["attribution"] == "© OpenStreetMap contributors"
    assert body["map"]["attribution_url"] == "https://www.openstreetmap.org/copyright"

    custom = Config.from_env(
        {
            "MAKAN_MODEL": "m",
            "MAKAN_MAP_TILE_URL": "https://tiles.example/{z}/{x}/{y}.png",
            "MAKAN_MAP_ATTRIBUTION": "© Example Maps",
        }
    )
    body = client(config=custom, mode="demo").get("/api/config").json()
    assert body["mode"] == "demo"
    assert body["map"] == {
        "tile_url": "https://tiles.example/{z}/{x}/{y}.png",
        "attribution": "© Example Maps",
        "attribution_url": None,
    }


def test_a_stage_never_carries_unbounded_text_or_lists() -> None:
    huge = {
        "cuisine": "thai",
        "category": None,
        "requirements": [f"need {i} " + "x" * 500 for i in range(30)],
    }
    provider = FakeProvider([call(ToolCall.of("finish", answer=json.dumps(huge)))])
    run = client(provider=provider).post("/api/recommendations", json=BODY).json()["run"]
    requirements = stages(run)["intent"]["output"]["requirements"]
    assert len(requirements) == 11 and requirements[-1] == "… 20 more"
    assert all(len(r) <= 300 for r in requirements)
    assert len(json.dumps(run)) < 30_000
