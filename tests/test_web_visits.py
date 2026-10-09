"""Visit logging over HTTP: signed-in only, scoped to the token, and private between people.

Supabase is a mock (`tests.auth_helpers.FakeSupabase`), so nothing here touches the network.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from makan.config import Config
from makan.places import FakePlacesProvider
from makan.visits import Visits
from makan.web import create_app
from tests.auth_helpers import services
from tests.test_web_accounts import BODY, THAI, Setup, classify
from tests.visit_helpers import Clock

CONFIG = Config(model="test/classifier")
PLACE = {
    "data_source": "overture:2026-09-23.1",
    "id": "g-thai",
    "name": "Mid Thai",
    "address": "1 Jalan Satu",
}
SOUP = {"name": "Tom Yum", "rating": 7.5, "tags": ["spicy", "not spicy", "lunch"], "comment": "ok"}
Headers = dict[str, str]


class Api:
    """The app with accounts, a clock the test controls, and people who can sign in."""

    def __init__(self) -> None:
        self.services, self.fake, self.store = services()
        self.clock = Clock()
        self.services = dataclasses.replace(
            self.services, visits=Visits(self.store.visits, self.store, clock=self.clock)
        )
        self.client = TestClient(
            create_app(
                provider=classify(),
                places=FakePlacesProvider([THAI]),
                config=CONFIG,
                accounts=self.services,
            )
        )

    def person(self, name: str) -> tuple[UUID, Headers]:
        user = self.fake.sign_up()
        self.services.accounts.save_profile(user, display_name=name)
        return user, {"Authorization": f"Bearer {self.fake.key.token(user)}"}

    def visit(self, headers: Headers, **body: Any) -> dict[str, Any]:
        sent = {"place": PLACE, "rating": 8.5, "party": "with_others"} | body
        response = self.client.post("/api/me/visits", json=sent, headers=headers)
        assert response.status_code == 201, response.text
        visit: dict[str, Any] = response.json()["visit"]
        return visit


def code(response: Any) -> str:
    value: str = response.json()["error"]["code"]
    return value


ID = str(uuid4())
ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("post", "/api/me/going-here", {"place": PLACE}),
    ("get", "/api/me/going-here", None),
    ("get", "/api/me/going-here/due", None),
    ("post", f"/api/me/going-here/{ID}/reminded", None),
    ("delete", f"/api/me/going-here/{ID}", None),
    ("post", "/api/me/visits", {"place": PLACE, "rating": 5, "party": "solo"}),
    ("get", "/api/me/visits", None),
    ("get", f"/api/me/visits/{ID}", None),
    ("patch", f"/api/me/visits/{ID}", {"rating": 5}),
    ("delete", f"/api/me/visits/{ID}", None),
    ("put", f"/api/me/visits/{ID}/confirmation", {}),
    ("post", f"/api/me/visits/{ID}/tags", {"user_id": ID, "acknowledged_sharing": True}),
    ("delete", f"/api/me/visits/{ID}/tags/{ID}", None),
    ("get", "/api/me/tag-requests", None),
    ("post", f"/api/me/tag-requests/{ID}/accept", None),
    ("post", f"/api/me/tag-requests/{ID}/decline", None),
]


# Guests


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_a_guest_is_refused_clearly(method: str, path: str, body: dict[str, Any] | None) -> None:
    api = Api()
    response = api.client.request(method, path, json=body)
    assert response.status_code == 401
    assert code(response) == "sign_in_required"
    assert "Sign in" in response.json()["error"]["message"]
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_a_bad_token_is_the_usual_401(method: str, path: str, body: dict[str, Any] | None) -> None:
    api = Api()
    response = api.client.request(
        method, path, json=body, headers={"Authorization": "Bearer not-a-token"}
    )
    assert (response.status_code, code(response)) == (401, "invalid_token")


def test_a_participant_token_is_not_an_account() -> None:
    api = Api()
    response = api.client.get(
        "/api/me/visits", headers={"Authorization": f"Bearer {uuid4()}"}
    )  # group sessions use these
    assert (response.status_code, code(response)) == (401, "invalid_token")


def test_guests_search_exactly_as_before() -> None:
    api = Api()
    response = api.client.post("/api/recommendations", json=BODY)
    assert response.status_code == 200
    assert response.json()["greeting"] is None
    assert api.client.get("/api/health").json()["status"] == "ok"
    assert api.client.get("/api/config").json()["auth"] is not None


def test_without_accounts_there_are_no_visit_routes() -> None:
    plain = Setup()
    client = plain.build(None)
    for method, path, body in ROUTES:
        response = client.request(method, path, json=body)
        assert response.status_code == 404, path


def test_the_owner_cannot_come_from_the_body() -> None:
    api = Api()
    _, headers = api.person("Alice")
    other = str(uuid4())
    response = api.client.post(
        "/api/me/visits",
        json={"place": PLACE, "rating": 5, "party": "solo", "user_id": other},
        headers=headers,
    )
    assert (response.status_code, code(response)) == (422, "invalid_request")


# Going here


def test_going_here_and_the_reminder_that_comes_due() -> None:
    api = Api()
    _, headers = api.person("Alice")
    response = api.client.post("/api/me/going-here", json={"place": PLACE}, headers=headers)
    assert response.status_code == 200
    marker = response.json()["going_here"]
    assert marker["place"] == {
        "source": "overture",
        "id": "g-thai",
        "name": "Mid Thai",
        "address": "1 Jalan Satu",
    }
    assert marker["planned_on"] == "2026-10-08"
    assert marker["remind_at"] == "2026-10-10T12:00:00+00:00"

    def due() -> list[dict[str, Any]]:
        listing: list[dict[str, Any]] = api.client.get(
            "/api/me/going-here/due", headers=headers
        ).json()["due"]
        return listing

    assert due() == []
    api.clock.advance(timedelta(hours=48))
    assert [m["id"] for m in due()] == [marker["id"]]
    assert [m["id"] for m in due()] == [marker["id"]]  # asking changes nothing

    sent = api.client.post(f"/api/me/going-here/{marker['id']}/reminded", headers=headers)
    assert sent.json()["going_here"]["reminded_at"] is not None
    assert due() == []
    assert api.client.get("/api/me/going-here", headers=headers).json() == {"going_here": []}


def test_a_visit_logged_from_a_marker_defaults_to_its_date() -> None:
    api = Api()
    _, headers = api.person("Alice")
    marker = api.client.post(
        "/api/me/going-here", json={"place": PLACE, "planned_on": "2026-10-06"}, headers=headers
    ).json()["going_here"]
    visit = api.visit(headers, going_here_id=marker["id"])
    assert visit["visited_on"] == "2026-10-06"
    api.clock.advance(timedelta(days=3))
    assert api.client.get("/api/me/going-here/due", headers=headers).json() == {"due": []}
    again = api.client.post(
        "/api/me/visits",
        json={"place": PLACE, "rating": 5, "party": "solo", "going_here_id": marker["id"]},
        headers=headers,
    )
    assert (again.status_code, code(again)) == (409, "conflict")


def test_cancelling_a_marker() -> None:
    api = Api()
    _, headers = api.person("Alice")
    marker = api.client.post("/api/me/going-here", json={"place": PLACE}, headers=headers).json()
    cancelled = api.client.delete(
        f"/api/me/going-here/{marker['going_here']['id']}", headers=headers
    )
    assert cancelled.json()["going_here"]["cancelled_at"] is not None
    api.clock.advance(timedelta(days=3))
    assert api.client.get("/api/me/going-here/due", headers=headers).json() == {"due": []}


def test_another_persons_marker_is_not_found() -> None:
    api = Api()
    _, alice = api.person("Alice")
    _, bob = api.person("Bob")
    marker = api.client.post("/api/me/going-here", json={"place": PLACE}, headers=alice).json()
    marker_id = marker["going_here"]["id"]
    for method, path in (
        ("post", f"/api/me/going-here/{marker_id}/reminded"),
        ("delete", f"/api/me/going-here/{marker_id}"),
    ):
        response = api.client.request(method, path, headers=bob)
        assert (response.status_code, code(response)) == (404, "not_found")
    assert api.client.get("/api/me/going-here", headers=bob).json() == {"going_here": []}


# Visits


def test_logging_a_visit_with_dishes_round_trips_exactly() -> None:
    api = Api()
    _, headers = api.person("Alice")
    visit = api.visit(
        headers,
        rating="9.25",
        description="Loud but good",
        visited_on="2026-10-02",
        dishes=[SOUP, {"name": "Pad Thai", "rating": "5.7"}],
    )
    assert visit["rating"] == 9.3 and visit["rating_origin"] == "fresh"
    assert visit["visited_on"] == "2026-10-02"
    assert [d["name"] for d in visit["dishes"]] == ["Tom Yum", "Pad Thai"]
    assert visit["dishes"][0]["tags"] == ["spicy", "not spicy", "lunch"]
    assert visit["dishes"][1]["rating"] == 5.7
    assert visit["dish_count"] == 2
    assert visit["tagged_by"] is None and visit["shared"] is None

    fetched = api.client.get(f"/api/me/visits/{visit['id']}", headers=headers).json()["visit"]
    assert fetched == visit
    listing = api.client.get("/api/me/visits", headers=headers).json()
    assert [v["id"] for v in listing["visits"]] == [visit["id"]]
    assert listing["visits"][0]["dish_count"] == 2
    assert "ratings, dishes and notes" in listing["sharing_notice"]


def test_a_visit_with_no_dishes_is_listed_as_a_visit() -> None:
    api = Api()
    _, headers = api.person("Alice")
    visit = api.visit(headers, party="solo")
    assert (visit["dishes"], visit["dish_count"]) == ([], 0)
    [row] = api.client.get("/api/me/visits", headers=headers).json()["visits"]
    assert (row["id"], row["dish_count"]) == (visit["id"], 0)


def test_editing_and_deleting_a_visit() -> None:
    api = Api()
    _, headers = api.person("Alice")
    visit = api.visit(headers, dishes=[SOUP])
    dish_id = visit["dishes"][0]["id"]

    patched = api.client.patch(
        f"/api/me/visits/{visit['id']}",
        json={
            "rating": 3.04,
            "description": None,
            "dishes": [
                {"id": dish_id, "name": "Tom Yum", "rating": 7.5},
                {"name": "Satay", "rating": 9},
            ],
        },
        headers=headers,
    ).json()["visit"]
    assert patched["rating"] == 3.0 and patched["description"] is None
    assert patched["dishes"][0]["id"] == dish_id
    assert patched["dishes"][0]["tags"] == []  # the list replaces the dish, tags included

    untouched = api.client.patch(f"/api/me/visits/{visit['id']}", json={}, headers=headers)
    assert untouched.json()["visit"]["rating"] == 3.0

    nulls = api.client.patch(f"/api/me/visits/{visit['id']}", json={"party": None}, headers=headers)
    assert (nulls.status_code, code(nulls)) == (422, "invalid_request")

    gone = api.client.delete(f"/api/me/visits/{visit['id']}", headers=headers)
    assert gone.status_code == 204
    assert api.client.get(f"/api/me/visits/{visit['id']}", headers=headers).status_code == 404
    assert api.client.delete(f"/api/me/visits/{visit['id']}", headers=headers).status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        {"rating": 10.5},
        {"rating": -1},
        {"rating": "ten"},
        {"rating": None},
        {"party": "alone"},
        {"visited_on": "2099-01-01"},
        {"visited_on": "not a date"},
        {"place": {**PLACE, "id": ""}},
        {"place": {**PLACE, "data_source": ""}},
        {"place": {"id": "x"}},
        {"dishes": [{"name": "", "rating": 5}]},
        {"dishes": [{"name": "Soup", "rating": 11}]},
        {"dishes": [{"name": "Soup", "rating": 5, "tags": ["a", ""]}]},
        {"dishes": [{"name": "Soup", "rating": 5, "extra": 1}]},
        {"dishes": [{"name": "Soup"}]},
        {"description": "x" * 2001},
        {"acknowledged_sharing": "maybe"},
    ],
)
def test_bad_input_is_a_422_that_stores_nothing(change: dict[str, Any]) -> None:
    api = Api()
    _, headers = api.person("Alice")
    body = {"place": PLACE, "rating": 5, "party": "solo"} | change
    response = api.client.post("/api/me/visits", json=body, headers=headers)
    assert (response.status_code, code(response)) == (422, "invalid_request")
    assert api.client.get("/api/me/visits", headers=headers).json()["visits"] == []


def test_bad_ids_are_422_and_not_a_crash() -> None:
    api = Api()
    _, headers = api.person("Alice")
    for path in ("/api/me/visits/not-a-uuid", "/api/me/going-here/not-a-uuid"):
        response = api.client.get(path, headers=headers)
        assert response.status_code in (405, 422)
    assert api.client.get("/api/me/visits/not-a-uuid", headers=headers).status_code == 422


def test_one_person_cannot_touch_anothers_visit_over_http() -> None:
    api = Api()
    alice_id, alice = api.person("Alice")
    _, bob = api.person("Bob")
    visit = api.visit(alice, dishes=[SOUP])
    path = f"/api/me/visits/{visit['id']}"

    attempts = [
        api.client.get(path, headers=bob),
        api.client.patch(path, json={"rating": 1}, headers=bob),
        api.client.delete(path, headers=bob),
        api.client.put(f"{path}/confirmation", json={"rating": {"action": "same"}}, headers=bob),
        api.client.post(
            f"{path}/tags",
            json={"user_id": str(alice_id), "acknowledged_sharing": True},
            headers=bob,
        ),
    ]
    for response in attempts:
        assert (response.status_code, code(response)) == (404, "not_found"), response.request.url
    assert api.client.get("/api/me/visits", headers=bob).json()["visits"] == []
    assert api.client.get(path, headers=alice).json()["visit"] == visit


def test_an_unexpected_failure_is_the_documented_500_without_its_text() -> None:
    api = Api()
    _, headers = api.person("Alice")

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("secret database detail")

    api.services.visits.history = boom  # type: ignore[method-assign]
    response = api.client.get("/api/me/visits", headers=headers)
    assert (response.status_code, code(response)) == (500, "server_error")
    assert "secret" not in response.text


# Tags


def tag(api: Api, headers: Headers, visit: dict[str, Any], friend: UUID, **extra: Any) -> Any:
    body = {"user_id": str(friend), "acknowledged_sharing": True} | extra
    return api.client.post(f"/api/me/visits/{visit['id']}/tags", json=body, headers=headers)


def test_tagging_tells_the_tagger_what_is_shared_and_needs_a_platform_user() -> None:
    api = Api()
    _, alice = api.person("Alice")
    bob_id, _ = api.person("Bob")
    visit = api.visit(alice)

    refused = tag(api, alice, visit, bob_id, acknowledged_sharing=False)
    assert (refused.status_code, code(refused)) == (422, "invalid_request")
    assert "will see your ratings, dishes and notes" in refused.json()["error"]["message"]
    stranger = tag(api, alice, visit, uuid4())
    assert (stranger.status_code, code(stranger)) == (422, "invalid_request")
    assert "on Makan" in stranger.json()["error"]["message"]
    solo = api.visit(alice, party="solo")
    assert tag(api, alice, solo, bob_id).status_code == 409

    made = tag(api, alice, visit, bob_id)
    assert made.status_code == 200
    assert made.json()["tag"]["status"] == "pending"
    assert made.json()["tag"]["display_name"] == "Bob"
    assert tag(api, alice, visit, bob_id).json()["tag"]["id"] == made.json()["tag"]["id"]


def test_a_request_shows_only_the_restaurant_the_date_and_who() -> None:
    api = Api()
    alice_id, alice = api.person("Alice")
    bob_id, bob = api.person("Bob")
    visit = api.visit(alice, dishes=[SOUP], description="my secret note", rating=9.9)
    tag(api, alice, visit, bob_id)

    [request] = api.client.get("/api/me/tag-requests", headers=bob).json()["requests"]
    assert request["place"] == {"name": "Mid Thai", "address": "1 Jalan Satu"}
    assert request["visited_on"] == "2026-10-08"
    assert request["tagged_by"] == {"display_name": "Alice"}
    assert request["status"] == "pending" and request["visit_id"] is None
    text = str(request)
    for private in ("my secret note", "Tom Yum", "9.9", "spicy", str(alice_id), visit["id"]):
        assert private not in text

    # Nothing else of Alice's is reachable while it is pending.
    assert api.client.get(f"/api/me/visits/{visit['id']}", headers=bob).status_code == 404
    assert api.client.get("/api/me/visits", headers=bob).json()["visits"] == []
    assert api.client.get("/api/me/export", headers=bob).text.count("my secret note") == 0


def test_a_declined_request_leaves_no_trace_for_either_side() -> None:
    api = Api()
    _, alice = api.person("Alice")
    bob_id, bob = api.person("Bob")
    visit = api.visit(alice)
    tag(api, alice, visit, bob_id)
    [request] = api.client.get("/api/me/tag-requests", headers=bob).json()["requests"]

    declined = api.client.post(f"/api/me/tag-requests/{request['id']}/decline", headers=bob)
    assert declined.status_code == 200
    assert declined.json()["request"]["status"] == "declined" and declined.json()["visit"] is None
    assert (
        api.client.post(f"/api/me/tag-requests/{request['id']}/decline", headers=bob).status_code
        == 200
    )
    accept = api.client.post(f"/api/me/tag-requests/{request['id']}/accept", headers=bob)
    assert (accept.status_code, code(accept)) == (409, "conflict")
    assert api.client.get("/api/me/tag-requests", headers=bob).json()["requests"] == []
    assert api.client.get("/api/me/visits", headers=bob).json()["visits"] == []
    mine = api.client.get(f"/api/me/visits/{visit['id']}", headers=alice).json()["visit"]
    assert [t["status"] for t in mine["tags"]] == ["declined"]
    [row] = api.client.get("/api/me/visits", headers=alice).json()["visits"]
    assert row["companions"] == []


def test_the_whole_tagged_visit_flow() -> None:
    api = Api()
    _, alice = api.person("Alice")
    bob_id, bob = api.person("Bob")
    visit = api.visit(
        alice, rating=9, description="great night", dishes=[SOUP, {"name": "Curry", "rating": 6}]
    )
    soup, curry = visit["dishes"]
    tag(api, alice, visit, bob_id)
    [request] = api.client.get("/api/me/tag-requests", headers=bob).json()["requests"]

    accepted = api.client.post(f"/api/me/tag-requests/{request['id']}/accept", headers=bob)
    assert accepted.status_code == 200
    mine = accepted.json()["visit"]
    assert accepted.json()["request"]["status"] == "accepted"
    assert accepted.json()["request"]["visit_id"] == mine["id"]
    assert (mine["rating"], mine["rating_origin"], mine["dishes"]) == (None, None, [])
    assert mine["tagged_by"] == {"display_name": "Alice"}
    assert mine["shared"]["rating"] == 9.0
    assert mine["shared"]["description"] == "great night"
    assert [d["name"] for d in mine["shared"]["dishes"]] == ["Tom Yum", "Curry"]
    assert (
        str(mine["shared"]).count(visit["id"]) == 0
    )  # the shared view carries no id of Alice's visit
    again = api.client.post(f"/api/me/tag-requests/{request['id']}/accept", headers=bob)
    assert again.json()["visit"]["id"] == mine["id"]
    [summary] = api.client.get("/api/me/visits", headers=bob).json()["visits"]
    assert summary["tagged_by"] == {"display_name": "Alice"}

    confirmed = api.client.put(
        f"/api/me/visits/{mine['id']}/confirmation",
        json={
            "rating": {"action": "change", "value": 4.4},
            "dishes": [
                {"source_dish_id": soup["id"], "action": "same"},
                {"source_dish_id": curry["id"], "action": "change", "rating": 2, "tags": ["dry"]},
            ],
        },
        headers=bob,
    )
    assert confirmed.status_code == 200, confirmed.text
    done = confirmed.json()["visit"]
    assert (done["rating"], done["rating_origin"]) == (4.4, "fresh")
    assert [(d["name"], d["rating"], d["rating_origin"]) for d in done["dishes"]] == [
        ("Tom Yum", 7.5, "confirmed"),
        ("Curry", 2.0, "fresh"),
    ]
    assert done["dishes"][1]["tags"] == ["dry"]
    assert done["dishes"][0]["answers_dish_id"] == soup["id"]

    # Alice's side is untouched, and she sees Bob as a companion.
    hers = api.client.get(f"/api/me/visits/{visit['id']}", headers=alice).json()["visit"]
    assert hers["rating"] == 9.0 and [d["rating"] for d in hers["dishes"]] == [7.5, 6.0]
    assert [t["status"] for t in hers["tags"]] == ["accepted"]
    [alice_row] = api.client.get("/api/me/visits", headers=alice).json()["visits"]
    assert alice_row["companions"] == ["Bob"]

    # She deletes her visit later, and his entry stays his.
    assert api.client.delete(f"/api/me/visits/{visit['id']}", headers=alice).status_code == 204
    left = api.client.get(f"/api/me/visits/{mine['id']}", headers=bob).json()["visit"]
    assert left["shared"] is None and left["rating"] == 4.4
    assert [d["rating"] for d in left["dishes"]] == [7.5, 2.0]
    stuck = api.client.put(
        f"/api/me/visits/{mine['id']}/confirmation",
        json={"rating": {"action": "same"}},
        headers=bob,
    )
    assert (stuck.status_code, code(stuck)) == (409, "conflict")


def test_only_the_tagged_person_can_answer_a_request() -> None:
    api = Api()
    alice_id, alice = api.person("Alice")
    bob_id, bob = api.person("Bob")
    _, cy = api.person("Cy")
    visit = api.visit(alice)
    tag(api, alice, visit, bob_id)
    [request] = api.client.get("/api/me/tag-requests", headers=bob).json()["requests"]
    for who in (alice, cy):
        for verb in ("accept", "decline"):
            r = api.client.post(f"/api/me/tag-requests/{request['id']}/{verb}", headers=who)
            assert (r.status_code, code(r)) == (404, "not_found")
    assert api.client.get("/api/me/tag-requests", headers=cy).json()["requests"] == []
    assert api.client.get("/api/me/tag-requests?status=all", headers=alice).json()["requests"] == []
    assert alice_id != bob_id


def test_the_request_list_filters_by_state() -> None:
    api = Api()
    _, alice = api.person("Alice")
    bob_id, bob = api.person("Bob")
    for _ in range(2):
        visit = api.visit(alice)
        tag(api, alice, visit, bob_id)
    first, second = api.client.get("/api/me/tag-requests", headers=bob).json()["requests"]
    api.client.post(f"/api/me/tag-requests/{first['id']}/accept", headers=bob)
    api.client.post(f"/api/me/tag-requests/{second['id']}/decline", headers=bob)

    def ids(status: str) -> list[str]:
        r = api.client.get(f"/api/me/tag-requests?status={status}", headers=bob)
        return [x["id"] for x in r.json()["requests"]]

    assert ids("pending") == []
    assert ids("accepted") == [first["id"]]
    assert ids("declined") == [second["id"]]
    assert sorted(ids("all")) == sorted([first["id"], second["id"]])
    assert api.client.get("/api/me/tag-requests?status=bogus", headers=bob).status_code == 422


def test_a_pending_request_can_be_withdrawn() -> None:
    api = Api()
    _, alice = api.person("Alice")
    bob_id, bob = api.person("Bob")
    visit = api.visit(alice)
    made = tag(api, alice, visit, bob_id).json()["tag"]
    path = f"/api/me/visits/{visit['id']}/tags/{made['id']}"
    assert api.client.delete(path, headers=bob).status_code == 404
    assert api.client.delete(path, headers=alice).status_code == 204
    assert api.client.get("/api/me/tag-requests", headers=bob).json()["requests"] == []


# Data rights


def test_export_and_deletion_cover_visits_over_http() -> None:
    api = Api()
    alice_id, alice = api.person("Alice")
    bob_id, bob = api.person("Bob")
    api.client.post("/api/me/going-here", json={"place": PLACE}, headers=alice)
    visit = api.visit(alice, dishes=[SOUP], description="alice note")
    tag(api, alice, visit, bob_id)
    api.visit(bob, party="solo", description="bob note")

    exported = api.client.get("/api/me/export", headers=alice).json()
    assert len(exported["going_here"]) == 1
    assert [v["description"] for v in exported["visits"]] == ["alice note"]
    assert [d["name"] for d in exported["visit_dishes"]] == ["Tom Yum"]
    assert exported["visit_dishes"][0]["tags"] == ["spicy", "not spicy", "lunch"]
    assert [t["status"] for t in exported["visit_tags"]] == ["pending"]
    assert "bob note" not in str(exported)

    theirs = api.client.get("/api/me/export", headers=bob).json()
    assert [r["status"] for r in theirs["tag_requests"]] == ["pending"]
    assert "alice note" not in str(theirs)

    assert api.client.delete("/api/me", headers=alice).status_code == 204
    assert api.store.visits.export(alice_id) == {
        "going_here": [],
        "visits": [],
        "visit_dishes": [],
        "visit_tags": [],
        "tag_requests": [],
    }
    assert api.client.get("/api/me/tag-requests", headers=bob).json()["requests"] == []
