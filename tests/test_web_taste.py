"""The own taste model over HTTP: who sees a personal score, when, and what it never changes.

Accounts use the fake Supabase from `tests.auth_helpers`, so nothing touches the network.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from makan.config import Config
from makan.memory import Owner
from makan.memory.hard import hard_content
from makan.places import FakePlacesProvider
from makan.places.base import Place
from makan.providers import FakeProvider
from makan.taste_model import MIN_RATINGS, SETTLED_RATINGS, TasteModels
from makan.visits import PlaceFacts, parse_place
from makan.web import create_app
from tests.helpers import place
from tests.test_web_accounts import BODY, Setup, classify
from tests.test_web_fit import BROWSE, result

CONFIG = Config(model="test/classifier", taste_model_enabled=True)
SUSHI = place(
    "Sushi Bar", "sushi_restaurant", 3.1483, 101.6951, "restaurant", "japanese_restaurant"
)
KEBAB = place("Kebab House", "turkish_restaurant", 3.1484, 101.6951, "restaurant")
TACOS = place("Taco Stand", "taco_restaurant", 3.1486, 101.6951, "mexican_restaurant")
VENUE = place("Corner Bistro", "restaurant", 3.1487, 101.6951)
PLACES = [SUSHI, KEBAB, TACOS, VENUE]
SOURCE = "fake"


def facts(p: Place) -> PlaceFacts:
    return PlaceFacts(p.cuisines, p.diets, (), False)


def rate(setup: Setup, user: UUID, p: Place, rating: float, times: int = 1) -> None:
    for _ in range(times):
        setup.services.visits.log_visit(
            user,
            place=parse_place(SOURCE, f"{p.id}-{uuid4().hex}", p.name, None, facts(p)),
            rating=rating,
            party="solo",
        )


def fan_of_sushi(setup: Setup, user: UUID, ratings: int) -> None:
    """`ratings` ratings: sushi high, kebab low, the rest in between."""
    half, rest = divmod(ratings, 2)
    rate(setup, user, SUSHI, 9.5, half)
    rate(setup, user, KEBAB, 3.0, half)
    rate(setup, user, VENUE, 6.0, rest)


def site(
    setup: Setup,
    config: Config = CONFIG,
    *,
    models: TasteModels | None = None,
    mode: str = "places",
    places: list[Place] | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            provider=FakeProvider([]) if mode == "places" else classify(),
            places=FakePlacesProvider(places or PLACES),
            config=config,
            accounts=setup.services,
            mode=mode,  # type: ignore[arg-type]
            taste_models=models,
        )
    )


def without_run(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload minus the run trace, which carries its own ids and times."""
    return {k: v for k, v in payload.items() if k != "run"}


def tastes(payload: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
    return {p["name"]: p.get("taste") for p in payload["places"]}


@pytest.mark.parametrize("condition", ["flag_off", "guest", "too_few", "no_accounts"])
def test_without_the_flag_an_account_or_ten_ratings_the_response_is_exactly_today_s(
    condition: str,
) -> None:
    setup = Setup()
    alice, headers = setup.person("Alice")
    fan_of_sushi(setup, alice, MIN_RATINGS - 1 if condition == "too_few" else SETTLED_RATINGS)
    if condition == "guest":
        headers = {}
    config = replace(CONFIG, taste_model_enabled=condition != "flag_off")
    if condition == "no_accounts":
        client = TestClient(
            create_app(
                provider=FakeProvider([]),
                places=FakePlacesProvider(PLACES),
                config=config,
                mode="places",
            )
        )
    else:
        client = site(setup, config)
    today = site(setup, replace(CONFIG, taste_model_enabled=False))
    for stream in (False, True):
        payload = result(client, BROWSE, stream, headers=headers)
        assert all("taste" not in p for p in payload["places"])
        if condition != "no_accounts":
            assert without_run(payload) == without_run(
                result(today, BROWSE, stream, headers=headers)
            )


def test_ten_ratings_show_an_estimate_and_thirty_a_settled_score() -> None:
    setup = Setup()
    alice, headers = setup.person("Alice")
    client = site(setup)
    fan_of_sushi(setup, alice, MIN_RATINGS)
    first = tastes(result(client, BROWSE, False, headers=headers))
    assert all(t is not None for t in first.values())
    sushi = first["Sushi Bar"]
    assert sushi is not None
    assert sushi["estimate"] is True and sushi["ratings"] == MIN_RATINGS
    assert sushi["scale"] == 10.0 and sushi["policy_version"] == "own-taste-ridge-v2"
    assert sushi["score"] * 2 == int(sushi["score"] * 2)  # half steps
    assert set(sushi) == {"score", "scale", "estimate", "ratings", "why", "policy_version"}

    fan_of_sushi(setup, alice, SETTLED_RATINGS - MIN_RATINGS)
    settled = tastes(result(client, BROWSE, True, headers=headers))
    assert settled["Sushi Bar"] is not None
    assert settled["Sushi Bar"]["estimate"] is False
    assert settled["Sushi Bar"]["ratings"] == SETTLED_RATINGS
    assert settled["Sushi Bar"]["why"] == "You tend to like Sushi."
    assert settled["Kebab House"] is not None
    assert settled["Kebab House"]["why"] == "You tend not to like Turkish."
    assert settled["Sushi Bar"]["score"] > settled["Taco Stand"]["score"]  # type: ignore[index]
    assert settled["Taco Stand"]["score"] > settled["Kebab House"]["score"]  # type: ignore[index]


def test_two_owners_each_see_only_their_own_taste_and_a_guest_none() -> None:
    setup = Setup()
    alice, alice_headers = setup.person("Alice")
    bob, bob_headers = setup.person("Bob")
    carol, carol_headers = setup.person("Carol")
    fan_of_sushi(setup, alice, 20)
    rate(setup, bob, SUSHI, 2.0, 10)
    rate(setup, bob, KEBAB, 9.0, 10)
    rate(setup, carol, SUSHI, 10.0, 4)
    client = site(setup)
    a = tastes(result(client, BROWSE, False, headers=alice_headers))
    b = tastes(result(client, BROWSE, False, headers=bob_headers))
    c = tastes(result(client, BROWSE, False, headers=carol_headers))
    guest = tastes(result(client, BROWSE, False))
    assert a["Sushi Bar"]["score"] > a["Kebab House"]["score"]  # type: ignore[index]
    assert b["Sushi Bar"]["score"] < b["Kebab House"]["score"]  # type: ignore[index]
    assert a["Sushi Bar"]["ratings"] == 20 and b["Sushi Bar"]["ratings"] == 20  # type: ignore[index]
    assert set(c.values()) == {None} and set(guest.values()) == {None}
    # Asking again as Alice gives Alice's answer, never Bob's from the cache.
    assert tastes(result(client, BROWSE, False, headers=alice_headers)) == a


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode", ["places", "demo"])
def test_scores_never_change_the_order_ranks_or_pick(stream: bool, mode: str) -> None:
    setup = Setup()
    alice, headers = setup.person("Alice")
    # Alice rates the nearest place lowest, so a ranking by taste would move it.
    rate(setup, alice, SUSHI, 0.0, 10)
    rate(setup, alice, VENUE, 10.0, 10)
    body = BROWSE if mode == "places" else BODY
    on = result(site(setup, mode=mode), body, stream, headers=headers)
    off = result(
        site(setup, replace(CONFIG, taste_model_enabled=False), mode=mode),
        body,
        stream,
        headers=headers,
    )

    def order(payload: dict[str, Any]) -> list[tuple[str, int]]:
        return [(p["id"], p["rank"]) for p in payload["places"]]

    assert order(on) == order(off)
    assert all(p["taste"] is not None for p in on["places"])
    if mode == "demo":
        assert on["pick"]["id"] == off["pick"]["id"]
        # The same value reaches the pick, the runners-up and the list.
        by_id = {p["id"]: p["taste"] for p in on["places"]}
        assert on["pick"]["taste"] == by_id[on["pick"]["id"]]
        for r in on["runners_up"]:
            assert r["taste"] == by_id[r["id"]]


def test_hard_rules_stay_in_plain_code_whatever_the_score() -> None:
    setup = Setup()
    alice, headers = setup.person("Alice")
    memory = setup.services.accounts.memory
    owner = Owner(user_id=alice)
    memory.remember(owner, "constraint", hard_content("never_place", "Sushi Bar"))
    memory.remember(owner, "constraint", hard_content("allergy", "peanut"))
    memory.remember(owner, "constraint", hard_content("diet", "vegan"))
    # Alice loved the sushi place and rates every listed-vegan place top marks.
    rate(setup, alice, SUSHI, 10.0, 10)
    vegan = replace(
        place("Green Bowl", "vegan_restaurant", 3.1482, 101.6951, "restaurant"), id="green"
    )
    rate(setup, alice, vegan, 10.0, 10)
    client = site(setup, places=[*PLACES, vegan])
    payload = result(client, BROWSE, False, headers=headers)
    names = [p["name"] for p in payload["places"]]
    assert "Sushi Bar" not in names  # the never-go rule wins over a predicted 10
    warnings = " ".join(payload["warnings"])
    assert "peanut" in warnings and "vegan" in warnings
    green = next(p for p in payload["places"] if p["name"] == "Green Bowl")
    assert green["taste"]["score"] >= 9.0
    assert "vegan" not in green["taste"]["why"].lower()
    plain = result(
        site(setup, replace(CONFIG, taste_model_enabled=False), places=[*PLACES, vegan]),
        BROWSE,
        False,
        headers=headers,
    )
    assert names == [p["name"] for p in plain["places"]]
    assert payload["warnings"] == plain["warnings"]


def test_a_rating_change_through_the_api_refits_on_the_next_search() -> None:
    setup = Setup()
    alice, headers = setup.person("Alice")
    models = TasteModels(setup.services.visits.taste_ratings)
    client = site(setup, models=models)

    def body(n: int) -> dict[str, Any]:
        # Five distinct sushi places, since repeat visits to one place count once.
        return {
            "place": {
                "data_source": SOURCE,
                "id": f"sushi-bar-{n}",
                "name": f"Sushi Bar {n}",
                "facts": {"cuisines": ["sushi", "japanese"], "diets": [], "hours_known": False},
            },
            "rating": 9.5,
            "party": "solo",
        }

    created = [client.post("/api/me/visits", json=body(n), headers=headers) for n in range(5)]
    assert all(r.status_code == 201 for r in created)
    rate(setup, alice, KEBAB, 3.0, 5)
    before = tastes(result(client, BROWSE, False, headers=headers))["Sushi Bar"]
    assert before is not None and models.cached(alice)
    for response in created:
        visit_id = response.json()["visit"]["id"]
        edited = client.patch(f"/api/me/visits/{visit_id}", json={"rating": 1}, headers=headers)
        assert edited.status_code == 200
    after = tastes(result(client, BROWSE, False, headers=headers))["Sushi Bar"]
    assert after is not None and after["score"] < before["score"]
    # Deleting a rating below the threshold removes the score and the cached model.
    client.delete(f"/api/me/visits/{created[0].json()['visit']['id']}", headers=headers)
    assert tastes(result(client, BROWSE, False, headers=headers))["Sushi Bar"] is None
    assert not models.cached(alice)


def test_the_visit_api_keeps_checked_place_facts() -> None:
    setup = Setup()
    _, headers = setup.person("Alice")
    client = site(setup)
    place_body = {"data_source": SOURCE, "id": "taco-stand", "name": "Taco Stand"}
    body: dict[str, Any] = {"rating": 8, "party": "solo"}
    plain = client.post("/api/me/visits", json={**body, "place": place_body}, headers=headers)
    assert plain.status_code == 201
    assert plain.json()["visit"]["place"]["facts"] is None
    given = {
        "cuisines": ["tacos", "mexican", "tacos"],
        "diets": ["vegan"],
        "dishes": ["Al pastor", " "],
        "hours_known": True,
    }
    kept = client.post(
        "/api/me/visits", json={**body, "place": {**place_body, "facts": given}}, headers=headers
    )
    assert kept.status_code == 201
    assert kept.json()["visit"]["place"]["facts"] == {
        "cuisines": ["tacos", "mexican"],
        "diets": ["vegan"],
        "dishes": ["Al pastor"],
        "hours_known": True,
    }
    for bad in (
        {**given, "cuisines": ["street food"]},
        {**given, "diets": ["paleo"]},
        {**given, "hours_known": "yes"},
        {k: v for k, v in given.items() if k != "hours_known"},
        {**given, "extra": 1},
    ):
        refused = client.post(
            "/api/me/visits", json={**body, "place": {**place_body, "facts": bad}}, headers=headers
        )
        assert refused.status_code == 422, bad
        assert refused.json()["error"]["code"] == "invalid_request"


def test_a_failing_model_leaves_the_search_as_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    setup = Setup()
    alice, headers = setup.person("Alice")
    fan_of_sushi(setup, alice, 12)

    def broken(user_id: UUID) -> Any:
        raise RuntimeError("secret database exception")

    client = site(setup, models=TasteModels(broken))
    payload = result(client, BROWSE, False, headers=headers)
    assert all("taste" not in p for p in payload["places"])
    assert "secret database exception" not in json.dumps(payload)


def test_the_trace_never_carries_a_personal_score() -> None:
    setup = Setup()
    alice, headers = setup.person("Alice")
    fan_of_sushi(setup, alice, 30)
    payload = result(site(setup), BROWSE, True, headers=headers)
    assert payload["places"][0]["taste"] is not None
    run = json.dumps(payload["run"])
    assert "taste" not in run and "You tend" not in run and "ratings" not in run


def test_deleting_the_account_forgets_its_model() -> None:
    setup = Setup()
    alice, headers = setup.person("Alice")
    fan_of_sushi(setup, alice, 12)
    models = TasteModels(setup.services.visits.taste_ratings)
    client = site(setup, models=models)
    result(client, BROWSE, False, headers=headers)
    assert models.cached(alice)
    assert client.delete("/api/me", headers=headers).status_code < 400
    assert not models.cached(alice)
