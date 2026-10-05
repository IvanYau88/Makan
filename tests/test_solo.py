"""Exercise the actual solo product path with scripted classification and offline places."""

import json
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from makan.config import Config
from makan.memory.gate import GateDecision
from makan.memory.service import Memory
from makan.memory.store import InMemoryStore, Owner
from makan.places import FakePlacesProvider, Place, PlaceQuery, PlacesError
from makan.providers import Completion, FakeProvider, ProviderError, ToolCall
from makan.providers.fake import call
from makan.solo import SoloRequest, recommend
from makan.trace import ListSink
from tests.helpers import KLCC, place

NEAR = place("Near Ramen", "ramen_restaurant", 3.1481, 101.6951)
THAI = place("Mid Thai", "thai_restaurant", 3.1485, 101.6951)
FAR = place("Far Thai", "thai_restaurant", 3.152, 101.695)
CONFIG = Config(model="test/classifier")


def classifier(cuisine: str | None = "thai", **kwargs: object) -> FakeProvider:
    intent = {"cuisine": cuisine, "category": None, "requirements": [], **kwargs}
    return FakeProvider([call(ToolCall.of("finish", answer=json.dumps(intent)))])


def test_guest_gets_pick_runners_up_and_one_unshared_participant() -> None:
    sink = ListSink()
    provider = classifier()
    places = FakePlacesProvider([NEAR, THAI, FAR])
    result = recommend(
        SoloRequest(KLCC[0] + 0.00001, KLCC[1], "thai please"),
        provider=provider,
        places=places,
        config=CONFIG,
        sink=sink,
    )
    assert result.graph.ok
    assert result.session.user_id is None
    assert result.session.shared_at is None
    assert result.participant.session_id == result.session.id
    assert result.participant.is_host and result.participant.user_id is None
    assert result.session.context["latitude"] == KLCC[0]
    answer = result.recommendation
    assert answer and answer.pick
    assert answer.pick.place.name == "Mid Thai"
    assert [r.place.name for r in answer.runners_up] == ["Far Thai", "Near Ramen"]
    assert "nearby alternative" in answer.runners_up[-1].reasons[-1]
    assert "Runners-up: Far Thai" in answer.explanation
    assert "Opening hours" in answer.explanation
    assert len(provider.requests) == 1
    assert provider.requests[0].model == CONFIG.model
    assert {q.cuisine for q in places.queries} == {None, "thai"}
    assert all(q.lat == KLCC[0] for q in places.queries)
    (event,) = [e for e in sink.events if e.type == "step_finish" and e.data["step"] == "classify"]
    (child,) = event.data["child_runs"]
    assert child != result.graph.run_id
    assert any(e.type == "run_end" and e.run_id == child for e in sink.events)
    assert sink.events[-1].type == "graph_end"


def test_real_search_branches_overlap() -> None:
    barrier = threading.Barrier(2)

    class ParallelPlaces(FakePlacesProvider):
        def search_nearby(self, query: PlaceQuery) -> list[Place]:
            barrier.wait(timeout=2)
            return super().search_nearby(query)

    sink = ListSink()
    result = recommend(
        SoloRequest(*KLCC, "thai"),
        provider=classifier(),
        places=ParallelPlaces([THAI]),
        config=CONFIG,
        sink=sink,
    )
    assert result.graph.ok
    for e in sink.events:
        if e.type == "step_finish" and e.data["step"] == "requested_places":
            assert "nearby_places" in e.data["parallel_with"]


def test_no_taste_ranks_by_distance_and_breaks_ties_stably() -> None:
    tied = place("A Ramen", "ramen_restaurant", NEAR.lat, NEAR.lon)
    result = recommend(
        SoloRequest(*KLCC, "food"),
        provider=classifier(None),
        places=FakePlacesProvider([NEAR, tied, THAI]),
        config=CONFIG,
    )
    assert result.recommendation and result.recommendation.pick
    assert result.recommendation.pick.place.name == "A Ramen"
    assert len(result.recommendation.runners_up) == 2  # overlapping searches deduplicated


def test_memory_is_gated_scoped_and_stale_facts_are_returned_without_ranking() -> None:
    now = datetime(2026, 10, 5, tzinfo=UTC)
    memory = Memory(InMemoryStore(), clock=lambda: now)
    user = uuid4()
    memory.remember(Owner(user_id=user), "cuisine_like", {"cuisine": "thai"})
    memory.remember(Owner(user_id=uuid4()), "cuisine_dislike", {"cuisine": "thai"})
    stale = memory.remember(
        Owner(user_id=user),
        "place_rating",
        {"place_id": NEAR.id, "rating": 5},
        expires_at=now - timedelta(days=1),
    ).fact
    memory.remember(Owner(user_id=user), "constraint", {"key": "diet", "value": "vegan"})
    result = recommend(
        SoloRequest(*KLCC, "food", user_id=user),
        provider=classifier(None),
        places=FakePlacesProvider([NEAR, THAI]),
        config=CONFIG,
        memory=memory,
    )
    answer = result.recommendation
    assert answer and answer.pick
    assert answer.pick.place.id == THAI.id
    assert [r.fact.id for r in answer.stale_facts] == [stale.id]
    assert any("Confirm stale memory" in w for w in answer.warnings)
    assert any("Cannot verify stored constraint" in w for w in answer.warnings)
    assert answer.runners_up[0].memory_score == 0

    class SkipGate:
        def decide(self, message: str) -> GateDecision:
            return GateDecision(False, "test skip")

    skipped = recommend(
        SoloRequest(*KLCC, "food", user_id=user),
        provider=classifier(None),
        places=FakePlacesProvider([NEAR, THAI]),
        config=CONFIG,
        memory=memory,
        gate=SkipGate(),
    )
    assert skipped.recommendation and skipped.recommendation.pick
    assert skipped.recommendation.pick.place.id == NEAR.id
    guest = recommend(
        SoloRequest(*KLCC, "food"),
        provider=classifier(None),
        places=FakePlacesProvider([NEAR, THAI]),
        config=CONFIG,
        memory=memory,
    )
    assert guest.recommendation and guest.recommendation.pick
    assert guest.recommendation.pick.place.id == NEAR.id


def test_current_request_fit_takes_priority_over_stored_taste() -> None:
    user = uuid4()
    memory = Memory(InMemoryStore())
    memory.remember(Owner(user_id=user), "cuisine_like", {"cuisine": "ramen"})
    memory.remember(Owner(user_id=user), "cuisine_dislike", {"cuisine": "thai"})
    result = recommend(
        SoloRequest(*KLCC, "thai", user_id=user),
        provider=classifier(),
        places=FakePlacesProvider([NEAR, THAI]),
        config=CONFIG,
        memory=memory,
    )
    assert result.recommendation and result.recommendation.pick
    assert result.recommendation.pick.place.id == THAI.id


def test_unsupported_requirements_are_visible_and_never_claimed_satisfied() -> None:
    result = recommend(
        SoloRequest(*KLCC, "thai, peanut free and open now"),
        provider=classifier(requirements=["peanut free", "open now"]),
        places=FakePlacesProvider([THAI]),
        config=CONFIG,
    )
    assert result.recommendation
    assert "Cannot verify requirement: peanut free." in result.recommendation.warnings
    assert "Cannot verify requirement: open now." in result.recommendation.warnings


def test_empty_results_are_a_valid_no_pick_outcome() -> None:
    result = recommend(
        SoloRequest(*KLCC, "food"),
        provider=classifier(),
        places=FakePlacesProvider([]),
        config=CONFIG,
    )
    assert result.graph.ok
    assert result.recommendation and result.recommendation.pick is None
    assert "No places found" in result.recommendation.explanation


@pytest.mark.parametrize("all_fail", [False, True])
def test_search_failure_is_preserved_even_when_other_branch_can_recommend(all_fail: bool) -> None:
    class FailingPlaces(FakePlacesProvider):
        def search_nearby(self, query: PlaceQuery) -> list[Place]:
            if all_fail or query.cuisine:
                raise PlacesError("offline")
            return super().search_nearby(query)

    result = recommend(
        SoloRequest(*KLCC, "thai"),
        provider=classifier(),
        places=FailingPlaces([THAI]),
        config=CONFIG,
    )
    assert not result.graph.ok
    if all_fail:
        assert result.recommendation is None
    else:
        assert result.recommendation and result.recommendation.pick
        assert any("requested_places error" in w for w in result.recommendation.warnings)


@pytest.mark.parametrize(
    "script",
    [
        [ProviderError("offline")],
        [call(ToolCall.of("finish", answer="thai"))],
        [call(ToolCall.of("finish", answer='{"cuisine":12,"category":null,"requirements":[]}'))],
    ],
)
def test_failed_or_malformed_classification_never_searches(
    script: list[Completion | ProviderError],
) -> None:
    places = FakePlacesProvider([THAI])
    result = recommend(
        SoloRequest(*KLCC, "thai"), provider=FakeProvider(script), places=places, config=CONFIG
    )
    assert not result.graph.ok
    assert result.recommendation is None
    assert places.queries == []


@pytest.mark.parametrize("lat,lon,text", [(91, 0, "food"), (0, 181, "food"), (0, 0, " ")])
def test_invalid_request_fails_before_running(lat: float, lon: float, text: str) -> None:
    with pytest.raises(ValueError):
        SoloRequest(lat, lon, text)


def test_full_taxonomy_filter_match_survives_compact_tool_results() -> None:
    labelled = place("Thai Cafe", "cafe", 3.1485, 101.6951, "thai_restaurant")
    result = recommend(
        SoloRequest(*KLCC, "thai cafe"),
        provider=classifier(category="cafe"),
        places=FakePlacesProvider([NEAR, labelled]),
        config=CONFIG,
    )
    assert result.recommendation and result.recommendation.pick
    assert result.recommendation.pick.place.id == labelled.id
    assert result.recommendation.pick.place.request_fit == 2


def test_timed_out_search_reports_partial_recommendation_and_trace() -> None:
    from makan.graph import GraphLimits

    release = threading.Event()

    class SlowPlaces(FakePlacesProvider):
        def search_nearby(self, query: PlaceQuery) -> list[Place]:
            if query.cuisine:
                release.wait(2)
            return super().search_nearby(query)

    sink = ListSink()
    try:
        result = recommend(
            SoloRequest(*KLCC, "thai"),
            provider=classifier(),
            places=SlowPlaces([THAI]),
            config=Config(model=CONFIG.model, graph_limits=GraphLimits(step_timeout_s=0.1)),
            sink=sink,
        )
    finally:
        release.set()
    assert result.graph.results["requested_places"].status == "timeout"
    assert result.recommendation and result.recommendation.pick
    assert any("timeout" in w for w in result.recommendation.warnings)
    assert any(e.type == "step_error" and e.data["status"] == "timeout" for e in sink.events)


def test_memory_failure_degrades_to_guest_ranking_with_warning() -> None:
    class BrokenGate:
        def decide(self, message: str) -> GateDecision:
            raise RuntimeError("memory unavailable")

    result = recommend(
        SoloRequest(*KLCC, "food", user_id=uuid4()),
        provider=classifier(None),
        places=FakePlacesProvider([NEAR, THAI]),
        config=CONFIG,
        memory=Memory(InMemoryStore()),
        gate=BrokenGate(),
    )
    assert not result.graph.ok
    assert result.recommendation and result.recommendation.pick
    assert result.recommendation.pick.place.id == NEAR.id
    assert any("Memory error" in w for w in result.recommendation.warnings)


def test_overture_results_include_source_attribution() -> None:
    result = recommend(
        SoloRequest(*KLCC, "food"),
        provider=classifier(None),
        places=FakePlacesProvider([THAI], name="overture:fixture"),
        config=CONFIG,
    )
    assert result.recommendation
    assert result.recommendation.data_source == "overture:fixture"
    assert "Overture Maps Foundation (CDLA Permissive 2.0)" in result.recommendation.explanation
