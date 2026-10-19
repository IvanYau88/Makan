"""Both transports decorate the same bounded candidates locally, without scorer calls."""

import json
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from makan.config import Config
from makan.fit import FitAssessment
from makan.memory import Owner
from makan.memory.hard import hard_content
from makan.places import FakePlacesProvider
from makan.providers import FakeProvider
from makan.providers.scoring import FakeScorer
from makan.web import create_app
from tests.test_web import BODY, FAR, NEAR, THAI, classify
from tests.test_web_accounts import PIZZA, Setup

CONFIG = Config(model="test/classifier", fit_enabled=True)
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
    for tastes in (
        {"likes": ["private allergy prose"]},
        {"allergies": ["peanut"]},
        {"likes": [True]},
        {"likes": ["thai"] * 9},
    ):
        response = client.post("/api/recommendations", json={**BROWSE, "tastes": tastes})
        assert response.status_code == 422
        assert "private allergy prose" not in response.text
    assert not provider.requests


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
