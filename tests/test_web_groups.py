"""The group endpoints through FastAPI's test client, with fakes and no network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from makan.config import Config, ConfigError
from makan.places import FakePlacesProvider, PlacesError
from makan.providers import FakeProvider, ProviderBusy, ToolCall
from makan.providers.fake import call
from makan.sessions import GroupSessions, InMemorySessionStore
from makan.web import create_app, create_app_from_env
from tests.helpers import KLCC, InterleavingStore, place, synthetic_database_url

CONFIG = Config(model="test/classifier")
THAI = place("Mid Thai", "thai_restaurant", 3.1485, 101.6951)
RAMEN = place("Near Ramen", "ramen_restaurant", 3.1481, 101.6951)
SEAFOOD = place("Sea Palace", "seafood_restaurant", 3.1482, 101.695)
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
CREATE: dict[str, Any] = {
    "latitude": KLCC[0],
    "longitude": KLCC[1],
    "request": "dinner for four",
    "display_name": "Alex",
}


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def classify(cuisine: str | None = None) -> FakeProvider:
    intent: dict[str, Any] = {"cuisine": cuisine, "category": None, "requirements": []}
    return FakeProvider([call(ToolCall.of("finish", answer=json.dumps(intent)))])


def make(
    clock: Clock | None = None,
    provider: FakeProvider | None = None,
    places: FakePlacesProvider | None = None,
    sessions: GroupSessions | None = None,
) -> TestClient:
    sessions = sessions or GroupSessions(
        InMemorySessionStore(),
        retention=timedelta(hours=2),
        max_participants=3,
        clock=clock or Clock(),
    )
    app = create_app(
        provider=provider or classify(),
        places=places or FakePlacesProvider([THAI, RAMEN, SEAFOOD]),
        config=CONFIG,
        sessions=sessions,
    )
    return TestClient(app)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def host_session(client: TestClient) -> tuple[str, str]:
    response = client.post("/api/groups", json=CREATE)
    assert response.status_code == 201
    body = response.json()
    return body["link_token"], body["participant_token"]


def join(client: TestClient, link: str, name: str | None = "Sam") -> str:
    response = client.post(f"/api/groups/{link}/participants", json={"display_name": name})
    assert response.status_code == 201
    token: str = response.json()["participant_token"]
    return token


def error_code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


# The whole flow


def test_a_group_is_created_joined_filled_in_and_resolved() -> None:
    client = make()
    created = client.post("/api/groups", json=CREATE)
    assert created.status_code == 201
    body = created.json()
    link, host = body["link_token"], body["participant_token"]
    assert body["session"]["request"] == "dinner for four"
    assert body["session"]["latitude"] == KLCC[0]
    assert body["session"]["closed"] is False
    assert body["session"]["created_at"] == T0.isoformat()
    assert body["session"]["expires_at"] == (T0 + timedelta(hours=2)).isoformat()
    assert body["session"]["participants"] == [
        {"name": "Alex", "is_host": True, "submitted": False}
    ]
    assert body["you"]["name"] == "Alex" and body["you"]["is_host"] is True

    sam = join(client, link)
    shared = client.put(
        f"/api/groups/{link}/me",
        headers=auth(sam),
        json={
            "constraints": {"refuses": ["seafood"], "allergies": ["peanut"]},
            "preferences": {"likes": ["ramen"], "dislikes": ["thai"]},
        },
    )
    assert shared.status_code == 200
    assert shared.json()["you"]["constraints"]["refuses"] == ["seafood"]
    assert shared.json()["you"]["submitted"] is True

    client.put(
        f"/api/groups/{link}/me",
        headers=auth(host),
        json={"preferences": {"likes": ["thai"]}},
    )
    result = client.post(f"/api/groups/{link}/result", headers=auth(host))
    assert result.status_code == 200
    out = result.json()
    assert out["pick"]["name"] == "Near Ramen"
    assert [p["name"] for p in out["runners_up"]] == ["Mid Thai"]
    assert out["excluded"] == [
        {
            "id": "sea-palace",
            "name": "Sea Palace",
            "category": "seafood_restaurant",
            "refusals": [{"person": "Sam", "term": "seafood"}],
        }
    ]
    assert out["pick"]["lowest_scorers"] == ["Alex"]
    assert out["pick"]["lowest_score"] <= out["pick"]["average_score"]
    assert "Alex is least happy with it" in out["explanation"]
    assert "nobody scored it below" in out["explanation"]
    for option in (out["pick"], *out["runners_up"]):
        assert option["warnings"] == ["Cannot verify Sam's allergy to peanut."]
    assert out["participant_count"] == 2 and out["pending"] == []
    assert out["partial"] is False and out["mode"] == "live"
    assert out["data_source"] == "fake" and out["attribution"] is None


def test_no_place_left_after_hard_constraints_is_a_clear_empty_result() -> None:
    client = make(places=FakePlacesProvider([SEAFOOD]))
    link, host = host_session(client)
    client.put(
        f"/api/groups/{link}/me", headers=auth(host), json={"constraints": {"refuses": ["seafood"]}}
    )
    response = client.post(f"/api/groups/{link}/result", headers=auth(host))
    assert response.status_code == 200
    body = response.json()
    assert body["pick"] is None and body["runners_up"] == []
    assert "No place is left after hard constraints" in body["explanation"]
    assert [e["name"] for e in body["excluded"]] == ["Sea Palace"]


# Privacy


def test_a_reader_sees_who_is_in_but_never_what_anyone_shared() -> None:
    client = make()
    link, host = host_session(client)
    sam = join(client, link)
    client.put(
        f"/api/groups/{link}/me",
        headers=auth(sam),
        json={
            "constraints": {"allergies": ["peanut"], "budget": "RM5"},
            "preferences": {"likes": ["thai"]},
        },
    )
    anonymous = client.get(f"/api/groups/{link}")
    as_host = client.get(f"/api/groups/{link}", headers=auth(host))
    for response in (anonymous, as_host):
        assert response.status_code == 200
        text = response.text
        for secret in ("peanut", "RM5", "thai", sam):
            assert secret not in text
        assert response.json()["session"]["participants"] == [
            {"name": "Alex", "is_host": True, "submitted": False},
            {"name": "Sam", "is_host": False, "submitted": True},
        ]
    assert anonymous.json()["you"] is None
    assert host not in client.get(f"/api/groups/{link}", headers=auth(sam)).text
    # A participant sees their own inputs, and only theirs.
    mine = client.get(f"/api/groups/{link}", headers=auth(sam)).json()["you"]
    assert mine["constraints"]["allergies"] == ["peanut"] and mine["name"] == "Sam"


def test_participant_tokens_are_never_listed_to_anyone_else() -> None:
    client = make()
    link, host = host_session(client)
    sam = join(client, link)
    everything = [
        client.get(f"/api/groups/{link}").text,
        client.get(f"/api/groups/{link}", headers=auth(sam)).text,
        client.post(f"/api/groups/{link}/result", headers=auth(host)).text,
    ]
    assert host not in everything[0] and host not in everything[1]
    assert all(sam not in text for text in everything)


def test_the_result_names_unverified_constraints_only_for_the_host() -> None:
    client = make()
    link, host = host_session(client)
    sam = join(client, link)
    client.put(
        f"/api/groups/{link}/me", headers=auth(sam), json={"constraints": {"diets": ["halal"]}}
    )
    assert client.post(f"/api/groups/{link}/result", headers=auth(sam)).status_code == 403
    assert client.post(f"/api/groups/{link}/result").status_code == 401
    ok = client.post(f"/api/groups/{link}/result", headers=auth(host))
    assert ok.json()["pick"]["warnings"] == ["Cannot verify Sam's diet: halal."]


# Access rules


def test_only_the_host_can_close_and_a_closed_session_stops_changes() -> None:
    client = make()
    link, host = host_session(client)
    sam = join(client, link)
    assert client.post(f"/api/groups/{link}/close", headers=auth(sam)).status_code == 403
    assert client.post(f"/api/groups/{link}/close").status_code == 401
    closed = client.post(f"/api/groups/{link}/close", headers=auth(host))
    assert closed.status_code == 200 and closed.json()["session"]["closed"] is True
    assert client.get(f"/api/groups/{link}").json()["session"]["closed"] is True
    join_again = client.post(f"/api/groups/{link}/participants", json={})
    assert join_again.status_code == 409 and error_code(join_again) == "session_closed"
    share = client.put(f"/api/groups/{link}/me", headers=auth(sam), json={})
    assert share.status_code == 409 and error_code(share) == "session_closed"
    assert client.post(f"/api/groups/{link}/result", headers=auth(host)).status_code == 200


def test_missing_and_wrong_tokens_are_told_apart() -> None:
    client = make()
    link, _ = host_session(client)
    _, other_host = host_session(client)
    missing = client.put(f"/api/groups/{link}/me", json={})
    assert missing.status_code == 401 and error_code(missing) == "participant_required"
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    not_bearer = client.put(
        f"/api/groups/{link}/me", headers={"Authorization": "Basic abc"}, json={}
    )
    assert not_bearer.status_code == 401
    # A token that is garbage, or belongs to another session, is not a participant here.
    for token in ("nonsense", other_host):
        response = client.put(f"/api/groups/{link}/me", headers=auth(token), json={})
        assert response.status_code == 403 and error_code(response) == "not_a_participant"
        assert client.get(f"/api/groups/{link}", headers=auth(token)).status_code == 403


def test_an_unknown_or_malformed_link_is_a_404_everywhere() -> None:
    client = make()
    _, host = host_session(client)
    for link in ("not-a-uuid", "00000000-0000-4000-8000-000000000000"):
        for method, path, kwargs in (
            ("get", "", {}),
            ("post", "/participants", {"json": {}}),
            ("put", "/me", {"json": {}, "headers": auth(host)}),
            ("post", "/close", {"headers": auth(host)}),
            ("post", "/result", {"headers": auth(host)}),
        ):
            response = client.request(method, f"/api/groups/{link}{path}", **kwargs)
            assert response.status_code == 404, (link, path)
            assert error_code(response) == "session_not_found"


def test_an_expired_session_is_410_for_every_call() -> None:
    clock = Clock()
    client = make(clock)
    link, host = host_session(client)
    clock.now += timedelta(hours=2)
    for method, path, kwargs in (
        ("get", "", {}),
        ("post", "/participants", {"json": {}}),
        ("put", "/me", {"json": {}, "headers": auth(host)}),
        ("post", "/close", {"headers": auth(host)}),
        ("post", "/result", {"headers": auth(host)}),
    ):
        response = client.request(method, f"/api/groups/{link}{path}", **kwargs)
        assert response.status_code == 410, path
        assert error_code(response) == "session_expired"
    assert (
        client.get(f"/api/groups/{link}").json()["error"]["message"] == "This session has expired."
    )


def test_a_full_session_is_a_409() -> None:
    client = make()
    link, _ = host_session(client)
    join(client, link)
    join(client, link)
    full = client.post(f"/api/groups/{link}/participants", json={})
    assert full.status_code == 409 and error_code(full) == "session_full"


# Validation


@pytest.mark.parametrize(
    "body",
    [
        {**CREATE, "user_id": "6f1c0a4e-1111-4111-8111-111111111111"},  # guests only, as in solo
        {**CREATE, "latitude": 91},
        {**CREATE, "request": "   "},
        {**CREATE, "request": "x" * 501},
        {**CREATE, "radius_m": 50},
        {**CREATE, "display_name": ""},
        {**CREATE, "display_name": "x" * 41},
        {"latitude": 1},
    ],
)
def test_bad_create_bodies_are_422(body: dict[str, Any]) -> None:
    response = make().post("/api/groups", json=body)
    assert response.status_code == 422 and error_code(response) == "invalid_request"


@pytest.mark.parametrize(
    "body",
    [
        {"constraints": {"allergy": ["peanut"]}},  # a misspelled field is never ignored
        {"constraints": {"refuses": "seafood"}},
        {"constraints": {"refuses": ["!!!"]}},
        {"constraints": {"refuses": ["thai"] * 21}},
        {"constraints": {"allergies": ["x" * 81]}},
        {"constraints": {"budget": 30}},
        {"preferences": {"likes": [1]}},
        {"preferences": {"love": ["thai"]}},
        {"display_name": " "},
        {"user_id": "x"},
    ],
)
def test_bad_inputs_are_422_with_a_message_and_change_nothing(body: dict[str, Any]) -> None:
    client = make()
    link, host = host_session(client)
    response = client.put(f"/api/groups/{link}/me", headers=auth(host), json=body)
    assert response.status_code == 422 and error_code(response) == "invalid_request"
    assert response.json()["error"]["message"]
    assert client.get(f"/api/groups/{link}", headers=auth(host)).json()["you"]["submitted"] is False


def test_semantic_input_errors_explain_themselves() -> None:
    client = make()
    link, host = host_session(client)
    response = client.put(
        f"/api/groups/{link}/me", headers=auth(host), json={"constraints": {"refuses": ["!!!"]}}
    )
    assert response.status_code == 422
    assert "refuses" in response.json()["error"]["message"]


def test_a_blank_join_makes_a_numbered_guest_with_or_without_a_body() -> None:
    clock = Clock()
    client = make(clock)
    link, _ = host_session(client)
    clock.now += timedelta(seconds=1)
    assert client.post(f"/api/groups/{link}/participants").status_code == 201
    clock.now += timedelta(seconds=1)
    response = client.post(f"/api/groups/{link}/participants", json={})
    assert response.status_code == 201
    assert [p["name"] for p in response.json()["session"]["participants"]] == [
        "Alex",
        "Guest 2",
        "Guest 3",
    ]
    assert response.json()["you"]["name"] == "Guest 3"


# Failures of the workflow


def test_a_busy_model_is_a_503_and_leaks_nothing() -> None:
    provider = FakeProvider([ProviderBusy("secret upstream detail")])
    client = make(provider=provider)
    link, host = host_session(client)
    response = client.post(f"/api/groups/{link}/result", headers=auth(host))
    assert response.status_code == 503 and error_code(response) == "model_busy"
    assert response.headers["Retry-After"] == "60"
    assert "secret" not in response.text


def test_both_searches_failing_is_a_places_error() -> None:
    client = make(places=FakePlacesProvider([THAI], error=PlacesError("db password is hunter2")))
    link, host = host_session(client)
    response = client.post(f"/api/groups/{link}/result", headers=auth(host))
    assert response.status_code == 502 and error_code(response) == "places_error"
    assert "hunter2" not in response.text


def test_one_failed_search_still_answers_and_hides_internals() -> None:
    class Flaky(FakePlacesProvider):
        def search_nearby(self, query: Any) -> Any:
            if query.cuisine:
                raise PlacesError("internal detail")
            return super().search_nearby(query)

    intent: dict[str, Any] = {"cuisine": "thai", "category": None, "requirements": []}
    provider = FakeProvider([call(ToolCall.of("finish", answer=json.dumps(intent)))])
    client = make(provider=provider, places=Flaky([THAI]))
    link, host = host_session(client)
    response = client.post(f"/api/groups/{link}/result", headers=auth(host))
    body = response.json()
    assert response.status_code == 200 and body["partial"] is True
    assert "Part of the search failed" in " ".join(body["warnings"])
    assert "internal detail" not in response.text and "requested_places" not in response.text


def test_a_crash_is_a_generic_500(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("kaboom")

    monkeypatch.setattr("makan.web.groups.recommend_group", explode)
    client = make()
    link, host = host_session(client)
    response = client.post(f"/api/groups/{link}/result", headers=auth(host))
    assert response.status_code == 500 and error_code(response) == "server_error"
    assert "kaboom" not in response.text


# The app from the environment


def test_demo_mode_serves_groups_with_no_database_or_network() -> None:
    client = TestClient(create_app_from_env({"MAKAN_DEMO": "1", "MAKAN_DATABASE_URL": "ignored"}))
    created = client.post("/api/groups", json={**CREATE, "request": "thai please"}).json()
    link, host = created["link_token"], created["participant_token"]
    join(client, link)
    result = client.post(f"/api/groups/{link}/result", headers=auth(host))
    assert result.status_code == 200
    assert result.json()["mode"] == "demo" and result.json()["pick"]["name"].startswith("Sample")


def test_retention_comes_from_the_environment() -> None:
    env = {"MAKAN_DEMO": "1", "MAKAN_SESSION_RETENTION_HOURS": "5"}
    session = (
        TestClient(create_app_from_env(env)).post("/api/groups", json=CREATE).json()["session"]
    )
    created = datetime.fromisoformat(session["created_at"])
    assert datetime.fromisoformat(session["expires_at"]) - created == timedelta(hours=5)
    default = TestClient(create_app_from_env({"MAKAN_DEMO": "1"}))
    assert default.post("/api/groups", json=CREATE).json()["session"]["expires_at"]


def test_a_database_that_cannot_be_reached_fails_at_startup_without_echoing_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    password = "throwaway-" + uuid4().hex
    url = synthetic_database_url(password)
    attempts: list[str] = []

    def refuse(target: str, **kwargs: Any) -> Any:
        attempts.append(target)  # no socket is ever opened
        raise psycopg.OperationalError(f"connection to {target} failed: bad password {password}")

    monkeypatch.setattr(psycopg, "connect", refuse)
    env = {"MAKAN_MODEL": "m", "OPENROUTER_API_KEY": "k", "MAKAN_DATABASE_URL": url}
    with pytest.raises(ConfigError) as caught:
        create_app_from_env(env)
    assert attempts == [url]
    message = str(caught.value)
    assert password not in message and "db.invalid" not in message
    assert "MAKAN_DATABASE_URL" in message and "OperationalError" in message
    assert caught.value.__cause__ is None  # the original error, which holds the secret, is dropped


# Postgres


def test_the_flow_works_over_postgres(db: Any) -> None:
    from makan.sessions.postgres import PostgresSessionStore

    clock = Clock()
    sessions = GroupSessions(PostgresSessionStore(db), retention=timedelta(hours=2), clock=clock)
    client = make(sessions=sessions)
    link, host = host_session(client)
    sam = join(client, link)
    client.put(
        f"/api/groups/{link}/me",
        headers=auth(sam),
        json={"constraints": {"refuses": ["seafood"]}, "preferences": {"likes": ["ramen"]}},
    )
    result = client.post(f"/api/groups/{link}/result", headers=auth(host))
    assert result.status_code == 200
    assert result.json()["pick"]["name"] == "Near Ramen"
    assert [e["name"] for e in result.json()["excluded"]] == ["Sea Palace"]
    assert client.post(f"/api/groups/{link}/close", headers=auth(host)).status_code == 200
    clock.now += timedelta(hours=3)
    assert client.get(f"/api/groups/{link}").status_code == 410
    assert sessions.purge_expired() == 1
    assert client.get(f"/api/groups/{link}").status_code == 404


# Closing and expiry against a write that was already admitted, through HTTP on both stores


def interleaved(store: Any, clock: Clock) -> tuple[TestClient, InterleavingStore, GroupSessions]:
    hooked = InterleavingStore(store)
    sessions = GroupSessions(hooked, retention=timedelta(hours=2), max_participants=3, clock=clock)
    return make(sessions=sessions), hooked, sessions


def test_a_close_landing_after_the_check_keeps_a_join_out(store: Any) -> None:
    clock = Clock()
    client, hooked, sessions = interleaved(store, clock)
    link, host = host_session(client)
    hooked.before["add_participant"] = lambda: sessions.close(link, host)
    late = client.post(f"/api/groups/{link}/participants", json={"display_name": "Late"})
    assert late.status_code == 409 and error_code(late) == "session_closed"
    assert [p.display_name for p in sessions.view(link).participants] == ["Alex"]
    assert client.get(f"/api/groups/{link}").json()["session"]["closed"] is True


def test_a_close_landing_after_the_check_leaves_the_frozen_inputs_alone(store: Any) -> None:
    clock = Clock()
    client, hooked, sessions = interleaved(store, clock)
    link, host = host_session(client)
    sam = join(client, link)
    refuse = {"constraints": {"refuses": ["seafood"]}}
    assert client.put(f"/api/groups/{link}/me", headers=auth(sam), json=refuse).status_code == 200
    hooked.before["update_participant"] = lambda: sessions.close(link, host)
    undo = client.put(f"/api/groups/{link}/me", headers=auth(sam), json={"constraints": {}})
    assert undo.status_code == 409 and error_code(undo) == "session_closed"
    # The host's result still honors Sam's refusal, which a late update would have cleared.
    result = client.post(f"/api/groups/{link}/result", headers=auth(host)).json()
    assert [e["name"] for e in result["excluded"]] == ["Sea Palace"]
    assert result["pick"]["name"] != "Sea Palace"


def test_the_clock_reaching_expiry_after_the_check_keeps_every_write_out(store: Any) -> None:
    clock = Clock()
    client, hooked, sessions = interleaved(store, clock)
    link, host = host_session(client)
    sam = join(client, link)

    def expire() -> None:
        clock.now = T0 + timedelta(hours=2)  # exactly expires_at

    hooked.before["after_get_by_token"] = expire
    late = client.post(f"/api/groups/{link}/participants", json={})
    assert late.status_code == 410 and error_code(late) == "session_expired"
    clock.now = T0
    hooked.before["after_get_participant"] = expire
    update = client.put(f"/api/groups/{link}/me", headers=auth(sam), json={"preferences": {}})
    assert update.status_code == 410
    clock.now = T0
    hooked.before["after_get_participant"] = expire
    closing = client.post(f"/api/groups/{link}/close", headers=auth(host))
    assert closing.status_code == 410
    clock.now = T0
    assert [p.display_name for p in sessions.view(link).participants] == ["Alex", "Sam"]
    assert sessions.view(link).session.closed_at is None
    you = sessions.view(link, sam).you
    assert you is not None and you.constraints == {}


# A store that fails must give the documented JSON error


@pytest.mark.parametrize(
    ("method", "path", "hook", "needs_token", "body"),
    [
        ("post", "", "create", False, CREATE),
        ("get", "/LINK", "get_by_token", False, None),
        ("get", "/LINK", "participants", False, None),
        ("get", "/LINK", "after_get_by_token", True, None),
        ("post", "/LINK/participants", "get_by_token", False, {}),
        ("post", "/LINK/participants", "add_participant", False, {}),
        ("put", "/LINK/me", "get_participant", True, {}),
        ("put", "/LINK/me", "update_participant", True, {}),
        ("post", "/LINK/close", "get_participant", True, None),
        ("post", "/LINK/close", "close", True, None),
        ("post", "/LINK/result", "get_by_token", True, None),
        ("post", "/LINK/result", "participants", True, None),
    ],
)
def test_a_store_failure_in_any_route_is_the_json_server_error(
    method: str,
    path: str,
    hook: str,
    needs_token: bool,
    body: dict[str, Any] | None,
    store: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, hooked, _ = interleaved(store, Clock())
    link, host = host_session(client)

    def fail() -> None:
        raise RuntimeError("connection to db.internal dropped: password hunter2")

    hooked.before[hook] = fail
    kwargs: dict[str, Any] = {"headers": auth(host)} if needs_token else {}
    if body is not None:
        kwargs["json"] = body
    with caplog.at_level("ERROR", logger="makan.web"):
        response = client.request(method, f"/api/groups{path.replace('LINK', link)}", **kwargs)
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "error": {
            "code": "server_error",
            "message": "Something went wrong on our side. Try again.",
        }
    }
    assert "hunter2" not in response.text and "db.internal" not in response.text
    logged = [r for r in caplog.records if r.name == "makan.web" and r.exc_info]
    assert logged and "hunter2" in str(logged[0].exc_info)  # the detail is in the log, not the body


def test_rule_failures_and_bad_input_keep_their_own_errors_not_the_generic_500() -> None:
    client = make()
    link, host = host_session(client)
    assert client.get("/api/groups/00000000-0000-4000-8000-000000000000").status_code == 404
    bad = client.put(f"/api/groups/{link}/me", headers=auth(host), json={"constraints": {"x": 1}})
    assert bad.status_code == 422
    semantic = client.put(
        f"/api/groups/{link}/me", headers=auth(host), json={"constraints": {"refuses": ["!!!"]}}
    )
    assert semantic.status_code == 422 and error_code(semantic) == "invalid_request"


# Control characters in a category term


@pytest.mark.parametrize("term", ["sea\u0000food", "sea\tfood", "sea\u200bfood", "sea\x07food"])
@pytest.mark.parametrize("field", ["refuses", "likes", "dislikes"])
def test_a_term_with_a_control_character_is_422_and_changes_nothing(
    term: str, field: str, store: Any
) -> None:
    client, _, sessions = interleaved(store, Clock())
    link, host = host_session(client)
    first = {"constraints": {"refuses": ["seafood"]}, "preferences": {"likes": ["thai"]}}
    assert client.put(f"/api/groups/{link}/me", headers=auth(host), json=first).status_code == 200
    section = "constraints" if field == "refuses" else "preferences"
    response = client.put(
        f"/api/groups/{link}/me", headers=auth(host), json={section: {field: [term]}}
    )
    assert response.status_code == 422 and error_code(response) == "invalid_request"
    assert "control characters" in response.json()["error"]["message"]
    you = sessions.view(link, host).you
    assert you is not None
    assert you.constraints == {
        "refuses": ["seafood"],
        "allergies": [],
        "diets": [],
        "budget": None,
    }
    assert you.preferences == {"likes": ["thai"], "dislikes": []}


# The consensus contract, end to end


def test_a_requested_category_that_one_person_dislikes_loses_to_an_alternative() -> None:
    client = make(provider=classify("thai"), places=FakePlacesProvider([THAI, RAMEN]))
    link, host = host_session(client)
    sam = join(client, link)
    client.put(
        f"/api/groups/{link}/me", headers=auth(host), json={"preferences": {"likes": ["thai"]}}
    )
    client.put(
        f"/api/groups/{link}/me", headers=auth(sam), json={"preferences": {"dislikes": ["thai"]}}
    )
    body = client.post(f"/api/groups/{link}/result", headers=auth(host)).json()
    assert body["pick"]["name"] == "Near Ramen"
    assert [p["name"] for p in body["runners_up"]] == ["Mid Thai"]
    assert body["pick"]["lowest_score"] - body["runners_up"][0]["lowest_score"] > 0.05
    assert body["runners_up"][0]["lowest_scorers"] == ["Sam"]


def test_a_blank_join_does_not_change_the_result_but_is_reported() -> None:
    far_thai = place("Far Thai", "thai_restaurant", 3.1565, 101.695)  # about 940 m away
    near_ramen = place("Near Ramen", "ramen_restaurant", 3.148, 101.695)
    intent: dict[str, Any] = {"cuisine": None, "category": None, "requirements": []}
    twice = FakeProvider([call(ToolCall.of("finish", answer=json.dumps(intent)))] * 2)
    client = make(provider=twice, places=FakePlacesProvider([far_thai, near_ramen]))
    link, host = host_session(client)
    client.put(
        f"/api/groups/{link}/me", headers=auth(host), json={"preferences": {"likes": ["thai"]}}
    )
    before = client.post(f"/api/groups/{link}/result", headers=auth(host)).json()
    join(client, link, None)
    after = client.post(f"/api/groups/{link}/result", headers=auth(host)).json()
    assert before["pick"]["name"] == after["pick"]["name"] == "Far Thai"
    assert after["pick"]["lowest_score"] == before["pick"]["lowest_score"]
    assert after["participant_count"] == 2
    assert after["pending"] == ["Guest 2"] and after["uncounted"] == ["Guest 2"]
    assert "Guest 2 has not shared anything yet." in after["explanation"]
