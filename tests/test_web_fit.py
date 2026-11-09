"""Both transports decorate the same bounded candidates locally, without scorer calls."""

import json
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from makan.config import Config
from makan.cuisines import CUISINE_IDS, VOCABULARY_VERSION
from makan.fit import FitAssessment
from makan.memory import Owner
from makan.memory.hard import hard_content
from makan.places import FakePlacesProvider
from makan.providers import FakeProvider
from makan.providers.scoring import FakeScorer
from makan.web import create_app
from tests.helpers import place
from tests.test_web import BODY, FAR, NEAR, THAI, classify
from tests.test_web_accounts import PIZZA, Setup

CONFIG = Config(model="test/classifier", fit_enabled=True)
MEAT = ("food_and_drink", "restaurant", "meat_restaurant")
SPECIAL_DIET = ("food_and_drink", "restaurant", "special_diet_restaurant")
ITALIAN = ("food_and_drink", "restaurant", "european_restaurant", "italian_restaurant")
PIZZERIA = place("Corner Pizza", "pizza_restaurant", 3.1489, 101.6951, *ITALIAN)
BURGER = place("Corner Burgers", "burger_restaurant", 3.1490, 101.6951, *MEAT)
VENUE = place("Corner Bistro", "restaurant", 3.1491, 101.6951, "food_and_drink")
VEGAN = replace(
    place("Green Bowl", "vegan_restaurant", 3.1481, 101.6951, *SPECIAL_DIET),
    website="https://greenbowl.example/",
    brand="Green Bowl Co",
)
BROWSE = {k: v for k, v in {**BODY, "mode": "browse"}.items() if k != "request"}


def result(client: TestClient, body: dict[str, Any], stream: bool, **kwargs: Any) -> dict[str, Any]:
    response = client.post(
        "/api/recommendations" + ("/stream" if stream else ""), json=body, **kwargs
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    if not stream:
        return response.json()  # type: ignore[no-any-return]
    final = json.loads(response.text.splitlines()[-1])
    assert final["type"] == "result"
    return final["recommendation"]  # type: ignore[no-any-return]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("browsing", [False, True])
def test_every_candidate_has_one_assessment_across_transports_and_views(
    stream: bool,
    browsing: bool,
) -> None:
    provider = FakeProvider([]) if browsing else classify()
    scorer = FakeScorer()
    client = TestClient(
        create_app(
            provider=provider,
            places=FakePlacesProvider([NEAR, THAI, FAR]),
            config=CONFIG,
            mode="places" if browsing else "live",
            scorer=scorer if browsing else None,
        )
    )
    body = {**(BROWSE if browsing else BODY), "tastes": {"likes": ["thai"]}}
    payload = result(client, body, stream)
    assert len(payload["places"]) == 3
    by_id = {p["id"]: p for p in payload["places"]}
    for p in payload["places"]:
        assert p["fit"]["score"] == (100 if p["category"] == "thai_restaurant" else None)
        assert set(p["fit"]) == set(FitAssessment().to_json())
    for p in [payload["pick"], *payload["runners_up"]]:
        if p:
            assert p == by_id[p["id"]]
    assert by_id[THAI.id]["fit"] == by_id[FAR.id]["fit"]
    assert [p["name"] for p in payload["places"]] == (
        ["Near Ramen", "Mid Thai", "Far Thai"]
        if browsing
        else ["Mid Thai", "Far Thai", "Near Ramen"]
    )
    assert not scorer.questions
    if browsing:
        assert not provider.requests


@pytest.mark.parametrize("stream", [False, True])
def test_blank_guest_request_words_and_clear_do_not_supply_tastes(stream: bool) -> None:
    client = TestClient(
        create_app(
            provider=FakeProvider([]),
            places=FakePlacesProvider([THAI]),
            config=CONFIG,
            mode="places",
        )
    )
    assert result(client, BROWSE, stream)["places"][0]["fit"]["status"] == "no_taste"
    rated = result(client, {**BROWSE, "tastes": {"dislikes": ["thai"]}}, stream)
    assert rated["places"][0]["fit"]["score"] == 0
    cleared = result(client, {**BROWSE, "tastes": {"likes": [], "dislikes": []}}, stream)
    assert cleared["places"][0]["fit"]["status"] == "no_taste"
    # A request match is still not a lasting taste.
    rec = TestClient(
        create_app(provider=classify(), places=FakePlacesProvider([THAI]), config=CONFIG)
    )
    assert result(rec, BODY, stream)["pick"]["fit"]["status"] == "no_taste"


def test_default_off_and_guest_vocabulary_validation() -> None:
    provider = FakeProvider([])
    client = TestClient(
        create_app(
            provider=provider,
            places=FakePlacesProvider([THAI]),
            config=replace(CONFIG, fit_enabled=False),
        )
    )
    payload = result(client, {**BROWSE, "tastes": {"likes": ["thai"]}}, False)
    assert payload["places"][0]["fit"] == FitAssessment().to_json()
    assert client.get("/api/config").json()["features"]["fit"] is False
    assert client.get("/api/config").json()["cuisines"] is None
    for tastes in (
        {"likes": ["private allergy prose"]},
        {"allergies": ["peanut"]},
        {"likes": [True]},
        {"likes": ["thai"] * (len(CUISINE_IDS) + 1)},
        {"likes": ["Thai"]},
        {"likes": ["thai food"]},
        {"dislikes": ["a_cuisine_the_vocabulary_lacks"]},
        {"likes": ["vegan"]},
    ):
        response = client.post("/api/recommendations", json={**BROWSE, "tastes": tastes})
        assert response.status_code == 422
        assert "private allergy prose" not in response.text
    assert not provider.requests


def test_the_config_serves_the_vocabulary_for_the_picker_when_fit_is_on() -> None:
    client = TestClient(
        create_app(provider=FakeProvider([]), places=FakePlacesProvider(), config=CONFIG)
    )
    vocabulary = client.get("/api/config").json()["cuisines"]
    assert vocabulary["version"] == VOCABULARY_VERSION
    assert {c["id"] for c in vocabulary["cuisines"]} == CUISINE_IDS
    assert {"id": "thai", "label": "Thai", "group": "Southeast Asian", "aliases": []} in (
        vocabulary["cuisines"]
    )


@pytest.mark.parametrize("stream", [False, True])
def test_guest_tastes_from_the_wider_vocabulary_rate_wider_place_cuisines(stream: bool) -> None:
    client = TestClient(
        create_app(
            provider=FakeProvider([]),
            places=FakePlacesProvider([PIZZERIA, NEAR, BURGER, VENUE]),
            config=CONFIG,
            mode="places",
        )
    )
    tastes = {"likes": ["burgers", "italian"], "dislikes": ["ramen"]}
    payload = result(client, {**BROWSE, "radius_m": 2000, "tastes": tastes}, stream)
    fits = {p["name"]: (p["fit"]["score"], p["fit"]["reason"]) for p in payload["places"]}
    assert fits == {
        "Corner Pizza": (100, "exact_like"),  # its pizza category is filed under Italian
        "Near Ramen": (0, "exact_dislike"),
        "Corner Burgers": (100, "exact_like"),
        "Corner Bistro": (None, "no_listed_cuisine"),
    }
    by_name = {p["name"]: p for p in payload["places"]}
    assert by_name["Corner Pizza"]["cuisines"] == [
        {"id": "pizza", "label": "Pizza"},
        {"id": "italian", "label": "Italian"},
    ]
    assert by_name["Corner Bistro"]["cuisines"] == []
    mixed = result(
        client,
        {**BROWSE, "radius_m": 2000, "tastes": {"likes": ["pizza"], "dislikes": ["italian"]}},
        stream,
    )
    pizza = next(p for p in mixed["places"] if p["name"] == "Corner Pizza")
    assert (pizza["fit"]["score"], pizza["fit"]["reason"]) == (None, "mixed_cuisine_match")


def test_places_carry_soft_diets_a_safe_website_and_brand() -> None:
    unsafe = replace(VEGAN, id="unsafe", name="Unsafe Link", website="javascript:alert(1)")
    client = TestClient(
        create_app(
            provider=FakeProvider([]),
            places=FakePlacesProvider([VEGAN, unsafe]),
            config=replace(CONFIG, fit_enabled=False),
            mode="places",
        )
    )
    payload = result(client, BROWSE, False)
    by_name = {p["name"]: p for p in payload["places"]}
    green = by_name["Green Bowl"]
    assert green["diets"] == [{"id": "vegan", "label": "Vegan"}] and green["cuisines"] == []
    assert green["website"] == "https://greenbowl.example/"
    assert green["brand"] == "Green Bowl Co"
    assert by_name["Unsafe Link"]["website"] is None
    # A listed diet never decides fit, eligibility or order, with fit off as in production.
    assert green["fit"] == FitAssessment().to_json()
    assert [p["name"] for p in payload["places"]] == ["Green Bowl", "Unsafe Link"]


def test_conflicting_guest_choices_abstain_and_do_not_hide_venues() -> None:
    client = TestClient(
        create_app(
            provider=FakeProvider([]),
            places=FakePlacesProvider([THAI]),
            config=CONFIG,
        )
    )
    payload = result(client, {**BROWSE, "tastes": {"likes": ["thai"], "dislikes": ["thai"]}}, False)
    assert payload["places"][0]["fit"]["reason"] == "conflicting_tastes"
    assert payload["places"][0]["fit"]["score"] is None


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_account_browse_reads_only_owner_and_never_accepts_guest_tastes_for_an_owner(
    stream: bool,
    enabled: bool,
) -> None:
    setup = Setup()
    first, first_header = setup.person("First")
    second, second_header = setup.person("Second")
    memory = setup.services.accounts.memory
    memory.remember(Owner(user_id=first), "cuisine_like", {"cuisine": "thai"})
    memory.remember(Owner(user_id=second), "cuisine_dislike", {"cuisine": "thai"})
    memory.remember(Owner(user_id=first), "constraint", hard_content("never_place", "Pizza Hut"))
    memory.remember(Owner(user_id=first), "constraint", hard_content("allergy", "peanut"))
    # Use the same two candidates for all owners. Names are never cuisine evidence.
    client = TestClient(
        create_app(
            provider=FakeProvider([]),
            places=FakePlacesProvider([THAI, PIZZA]),
            config=replace(CONFIG, fit_enabled=enabled),
            accounts=setup.services,
        )
    )
    first_result = result(client, BROWSE, stream, headers=first_header)
    assert [p["id"] for p in first_result["places"]] == [THAI.id]
    assert first_result["places"][0]["fit"]["score"] == (100 if enabled else None)
    assert any("peanut" in w and "does not establish safety" in w for w in first_result["warnings"])
    second_result = result(
        client, {**BROWSE, "tastes": {"likes": ["thai"]}}, stream, headers=second_header
    )
    assert len(second_result["places"]) == 2
    assert second_result["places"][0]["fit"]["score"] == (0 if enabled else None)
    assert not any("peanut" in w for w in second_result["warnings"])
    guest = result(client, BROWSE, stream)
    assert len(guest["places"]) == 2
    assert guest["places"][0]["fit"]["status"] == ("no_taste" if enabled else "disabled")
    # An unsupported real profile remains local and makes even the exact match Unrated.
    memory.remember(Owner(user_id=first), "cuisine_like", {"cuisine": "private free text"})
    unsupported = result(client, BROWSE, stream, headers=first_header)
    assert unsupported["places"][0]["fit"]["reason"] == (
        "unsupported_tastes" if enabled else "disabled"
    )
    assert "private free text" not in json.dumps(unsupported)


def test_stale_owner_tastes_do_not_bypass_hard_exclusions_or_leak_on_read_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = Setup()
    owner_id, headers = setup.person()
    memory = setup.services.accounts.memory
    owner = Owner(user_id=owner_id)
    memory.remember(owner, "cuisine_like", {"cuisine": "thai"})
    memory.remember(owner, "constraint", hard_content("never_place", "Pizza Hut"))
    future = memory.now() + timedelta(days=400)
    monkeypatch.setattr(memory, "_clock", lambda: future)
    client = TestClient(
        create_app(
            provider=FakeProvider([]),
            places=FakePlacesProvider([THAI, PIZZA]),
            config=CONFIG,
            accounts=setup.services,
        )
    )
    stale = result(client, BROWSE, False, headers=headers)
    assert len(stale["places"]) == 1
    assert stale["places"][0]["fit"]["reason"] == "stale_tastes"

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret database exception")

    monkeypatch.setattr(memory, "recall", broken)
    unavailable = result(client, BROWSE, True, headers=headers)
    assert all(p["fit"]["status"] == "unavailable" for p in unavailable["places"])
    assert any("exclusions are unavailable" in w for w in unavailable["warnings"])
    assert "secret database exception" not in json.dumps(unavailable)


@pytest.mark.parametrize("enabled", [False, True])
def test_a_listed_diet_never_relaxes_hard_rules_or_their_warnings(enabled: bool) -> None:
    setup = Setup()
    owner_id, headers = setup.person()
    owner = Owner(user_id=owner_id)
    memory = setup.services.accounts.memory
    memory.remember(owner, "cuisine_like", {"cuisine": "burgers"})
    memory.remember(owner, "constraint", hard_content("diet", "vegan"))
    memory.remember(owner, "constraint", hard_content("allergy", "peanut"))
    memory.remember(owner, "constraint", hard_content("never_place", "Green Bowl"))
    halal = replace(VEGAN, id="halal", name="Halal Corner", diets=("halal",), website=None)
    client = TestClient(
        create_app(
            provider=FakeProvider([]),
            places=FakePlacesProvider([VEGAN, halal, BURGER]),
            config=replace(CONFIG, fit_enabled=enabled),
            accounts=setup.services,
            mode="places",
        )
    )
    payload = result(client, BROWSE, False, headers=headers)
    # The never-go rule removes the vegan-listed place, whatever its diet label says.
    assert [p["name"] for p in payload["places"]] == ["Halal Corner", "Corner Burgers"]
    warnings = " ".join(payload["warnings"])
    assert "vegan" in warnings and "peanut" in warnings
    assert "does not establish safety" in warnings
    by_name = {p["name"]: p for p in payload["places"]}
    assert by_name["Halal Corner"]["diets"] == [{"id": "halal", "label": "Halal"}]
    assert by_name["Halal Corner"]["fit"]["score"] is None
    assert by_name["Corner Burgers"]["fit"]["score"] == (100 if enabled else None)
