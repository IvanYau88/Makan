"""The HTTP API, exercised through FastAPI's test client with fakes and no network."""

import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from makan.config import Config, ConfigError
from makan.places import FakePlacesProvider, Place, PlacesError
from makan.providers import FakeProvider, ProviderBusy, ProviderError, ToolCall
from makan.providers.fake import call
from makan.providers.scoring import Distribution, FakeScorer, Question, ScoreResult, Status
from makan.web import create_app, create_app_from_env
from tests.helpers import KLCC, place

CONFIG = Config(model="test/classifier")
THAI = place("Mid Thai", "thai_restaurant", 3.1485, 101.6951)
NEAR = place("Near Ramen", "ramen_restaurant", 3.1481, 101.6951)
FAR = place("Far Thai", "thai_restaurant", 3.152, 101.695)
BODY: dict[str, Any] = {
    "mode": "recommend",
    "latitude": KLCC[0],
    "longitude": KLCC[1],
    "request": "thai please",
}


def classify(cuisine: str | None = "thai") -> FakeProvider:
    intent: dict[str, Any] = {"cuisine": cuisine, "category": None, "requirements": []}
    return FakeProvider([call(ToolCall.of("finish", answer=json.dumps(intent)))])


def client(
    provider: FakeProvider | None = None,
    places: FakePlacesProvider | None = None,
    **kwargs: Any,
) -> TestClient:
    app = create_app(
        provider=provider or classify(),
        places=places or FakePlacesProvider([NEAR, THAI, FAR]),
        config=CONFIG,
        **kwargs,
    )
    return TestClient(app)


def test_guest_gets_a_pick_runners_up_and_warnings() -> None:
    response = client().post("/api/recommendations", json=BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["pick"]["name"] == "Mid Thai"
    assert body["pick"]["distance_m"] > 0
    assert body["pick"]["reasons"]
    assert [p["name"] for p in body["runners_up"]] == ["Far Thai", "Near Ramen"]
    assert any("Opening hours" in w for w in body["warnings"])
    assert body["stale_facts"] == []
    assert body["partial"] is False
    assert body["data_source"] == "fake"
    assert body["attribution"] is None
    assert body["mode"] == "live"


def test_the_request_is_trimmed_and_radius_defaults() -> None:
    places = FakePlacesProvider([THAI])
    client(places=places).post("/api/recommendations", json={**BODY, "request": "  thai  "})
    assert {q.radius_m for q in places.queries} == {1000}
    provider = classify()
    client(provider=provider).post("/api/recommendations", json={**BODY, "request": "  thai  "})
    assert provider.requests[0].messages[-1].content == "thai"


def test_overture_results_carry_attribution() -> None:
    places = FakePlacesProvider([THAI], name="overture:2026-09-23.1")
    body = client(places=places).post("/api/recommendations", json=BODY).json()
    assert body["attribution"] == "Places data: Overture Maps Foundation (CDLA Permissive 2.0)."


def test_no_results_is_a_successful_empty_answer() -> None:
    response = client(places=FakePlacesProvider([])).post("/api/recommendations", json=BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["pick"] is None and body["runners_up"] == []
    assert "No places found" in body["explanation"]


@pytest.mark.parametrize(
    "change",
    [
        {"latitude": 91},
        {"latitude": "north"},
        {"longitude": -181},
        {"request": ""},
        {"request": "   \n\t"},
        {"request": "x" * 501},
        {"radius_m": 50},
        {"radius_m": 5001},
        {"user_id": "5a5b1f1e-0000-4000-8000-000000000000"},
    ],
)
def test_bad_input_is_a_clear_422_that_never_reaches_the_workflow(change: dict[str, Any]) -> None:
    provider = classify()
    response = client(provider=provider).post("/api/recommendations", json={**BODY, **change})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["message"].startswith("Check these fields:")
    assert not provider.requests


def test_missing_body_and_unknown_routes_use_the_same_error_shape() -> None:
    app = client()
    assert app.post("/api/recommendations").json()["error"]["code"] == "invalid_request"
    missing = app.get("/api/nope")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "http_error"
    assert app.get("/api/recommendations").status_code == 405


def test_a_model_failure_is_a_502() -> None:
    provider = FakeProvider([ProviderError("OpenRouter returned 500: secret details")])
    response = client(provider=provider).post("/api/recommendations", json=BODY)
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "provider_error"
    assert "secret" not in error["message"]
    assert "could not read" not in error["message"]
    assert "Retry-After" not in response.headers


def test_a_rate_limited_model_is_a_503_that_says_it_is_busy() -> None:
    provider = FakeProvider([ProviderBusy("OpenRouter returned 429: secret upstream details")])
    response = client(provider=provider).post("/api/recommendations", json=BODY)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "60"
    error = response.json()["error"]
    assert error["code"] == "model_busy"
    assert error["message"] == "The language model is busy right now. Try again in a minute."
    assert "secret" not in response.text


def test_an_unusable_model_answer_is_a_provider_error_that_blames_the_answer() -> None:
    bad = FakeProvider([call(ToolCall.of("finish", answer="not json"))])
    response = client(provider=bad).post("/api/recommendations", json=BODY)
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "provider_error"
    assert "answer Makan could not use" in error["message"]


def test_places_failure_is_a_502_and_one_failed_search_is_still_a_partial_answer() -> None:
    down = FakePlacesProvider(error=PlacesError("Overture query failed: boom"))
    response = client(places=down).post("/api/recommendations", json=BODY)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "places_error"
    assert "boom" not in response.text

    class OneFails(FakePlacesProvider):
        def search_nearby(self, query: Any) -> list[Place]:
            if query.cuisine:
                raise PlacesError("filtered search failed")
            return super().search_nearby(query)

    response = client(places=OneFails([THAI, NEAR])).post("/api/recommendations", json=BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["partial"] is True
    assert body["pick"] is not None
    assert "Part of the search failed, so these results may be incomplete." in body["warnings"]
    assert "filtered search failed" not in response.text
    assert "requested_places" not in " ".join([*body["warnings"], body["explanation"]])


def test_an_unexpected_crash_is_a_500_without_details(monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("password=hunter2")

    monkeypatch.setattr("makan.web.app.recommend", crash)
    response = client().post("/api/recommendations", json=BODY)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "server_error"
    assert "hunter2" not in response.text


def test_blocking_work_runs_off_the_event_loop() -> None:
    seen: list[str] = []

    class Spy(FakePlacesProvider):
        def search_nearby(self, query: Any) -> list[Place]:
            seen.append(threading.current_thread().name)
            return super().search_nearby(query)

    client(places=Spy([THAI])).post("/api/recommendations", json=BODY)
    assert seen and threading.main_thread().name not in seen


def test_health_reports_the_mode() -> None:
    assert client().get("/api/health").json() == {"status": "ok", "mode": "live"}
    assert client(mode="demo").get("/api/health").json()["mode"] == "demo"


# The built front end is served by the same app when it exists.


def test_static_front_end_is_served_after_the_api(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>Makan</h1>", encoding="utf-8")
    app = client(static_dir=tmp_path)
    assert "<h1>Makan</h1>" in app.get("/").text
    assert app.get("/api/health").status_code == 200


def test_a_shared_group_link_opens_the_page_and_is_not_cached(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>Makan</h1>", encoding="utf-8")
    app = client(static_dir=tmp_path)
    page = app.get("/g/2d6c1b8e-0000-4000-8000-000000000000")
    assert page.status_code == 200 and "<h1>Makan</h1>" in page.text
    assert page.headers["Cache-Control"] == "no-store"
    assert app.get("/g/2d6c1b8e-0000-4000-8000-000000000000/").status_code == 200
    assert app.get("/api/groups/2d6c1b8e-0000-4000-8000-000000000000").status_code == 404


def test_a_missing_front_end_build_leaves_only_the_api(tmp_path: Path) -> None:
    app = client(static_dir=tmp_path / "missing")
    assert app.get("/").status_code == 404
    assert app.get("/api/health").status_code == 200


# Run modes, chosen from the environment.


@pytest.fixture
def no_dist(tmp_path: Path) -> Iterator[dict[str, str]]:
    yield {"MAKAN_WEB_DIST": str(tmp_path / "none")}


def test_demo_mode_needs_no_key_and_makes_no_network_call(no_dist: dict[str, str]) -> None:
    app = TestClient(create_app_from_env({**no_dist, "MAKAN_DEMO": "1"}))
    assert app.get("/api/health").json()["mode"] == "demo"
    response = app.post(
        "/api/recommendations",
        json={**BODY, "latitude": 51.5, "longitude": -0.12, "request": "thai"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pick"]["name"] == "Sample Thai Garden"
    assert body["data_source"] == "demo" and body["mode"] == "demo"
    assert len(body["runners_up"]) == 3


def test_demo_mode_finds_nothing_at_the_poles(no_dist: dict[str, str]) -> None:
    app = TestClient(create_app_from_env({**no_dist, "MAKAN_DEMO": "true"}))
    body = app.post(
        "/api/recommendations", json={**BODY, "latitude": 90, "longitude": 0, "request": "lunch"}
    ).json()
    assert body["pick"] is None


def test_live_mode_builds_real_providers_from_the_environment(no_dist: dict[str, str]) -> None:
    env = {**no_dist, "MAKAN_MODEL": "some/model", "OPENROUTER_API_KEY": "sk-test"}
    app = TestClient(create_app_from_env(env))
    assert app.get("/api/health").json()["mode"] == "live"


def test_a_configured_scorer_adds_estimated_signals_to_the_explanation() -> None:
    plain = client().post("/api/recommendations", json=BODY).json()
    scored = client(scorer=_leaning_scorer()).post("/api/recommendations", json=BODY).json()

    assert "estimate" not in plain["explanation"]
    assert "budget band: cheap" in scored["explanation"]
    assert scored["pick"] == plain["pick"]  # signals never change the ranking


def test_live_mode_builds_the_configured_scorer_and_rejects_a_half_set_one(
    no_dist: dict[str, str],
) -> None:
    live = {**no_dist, "MAKAN_MODEL": "some/model", "OPENROUTER_API_KEY": "sk-test"}
    assert create_app_from_env({**live, "MAKAN_SCORER_BACKEND": "fake"})
    assert create_app_from_env(
        {**live, "MAKAN_SCORER_BACKEND": "logprob", "MAKAN_SCORER_MODEL": "m"}
    )
    with pytest.raises(ConfigError, match="MAKAN_SCORER_MODEL"):
        create_app_from_env({**live, "MAKAN_SCORER_BACKEND": "jev"})


def _leaning_scorer() -> FakeScorer:
    def policy(question: Question) -> ScoreResult:
        choice = {"budget_band": "cheap"}.get(question.decision, "not_stated")
        ids = question.ids
        rest = 0.1 / (len(ids) - 1)
        d = Distribution.of({i: 0.9 if i == choice else rest for i in ids}, ids)
        return ScoreResult(Status.OK, "fake", "", choice=choice, distribution=d)

    return FakeScorer(policy)


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        ({}, "MAKAN_MODEL"),
        ({"MAKAN_MODEL": "some/model"}, "OPENROUTER_API_KEY"),
    ],
)
def test_live_mode_fails_at_startup_and_points_at_demo_mode(
    env: dict[str, str], missing: str
) -> None:
    with pytest.raises(ConfigError, match=f"{missing}.*MAKAN_DEMO=1"):
        create_app_from_env(env)


def test_the_uvicorn_factory_loads_dot_env_from_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_bytes(
        b"MAKAN_DEMO=1\r\nMAKAN_WEB_DIST=" + str(tmp_path / "none").encode() + b"\r\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", {})  # the factory writes here, so keep it off the real one
    app = TestClient(create_app_from_env())
    assert app.get("/api/health").json()["mode"] == "demo"


def test_a_variable_in_the_real_environment_beats_dot_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("MAKAN_DEMO=1\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAKAN_DEMO", "0")
    monkeypatch.setenv("MAKAN_WEB_DIST", str(tmp_path / "none"))
    monkeypatch.delenv("MAKAN_MODEL", raising=False)
    with pytest.raises(ConfigError, match="MAKAN_MODEL"):
        create_app_from_env()
