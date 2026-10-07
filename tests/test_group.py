"""The group consensus workflow through the real graph, with scripted classification and places."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from makan.config import Config
from makan.consensus import CLOSE_SCORE_MARGIN, Constraints, Preferences, floor_score, score
from makan.group import GroupInput, labelled_members, recommend_group
from makan.models import Participant, Session
from makan.places import FakePlacesProvider, PlacesError
from makan.providers import FakeProvider, ToolCall
from makan.providers.fake import call
from makan.solo import SoloRequest, recommend
from makan.trace import ListSink
from tests.helpers import KLCC, place

CONFIG = Config(model="test/classifier")
T0 = datetime(2026, 1, 1, tzinfo=UTC)

# Distances from KLCC grow with the latitude offset: about 55 m, 110 m, and 165 m per 0.0005.
THAI = place("Mid Thai", "thai_restaurant", 3.1485, 101.6951)
NEAR_RAMEN = place("Near Ramen", "ramen_restaurant", 3.1481, 101.6951)
FAR_THAI = place("Far Thai", "thai_restaurant", 3.152, 101.695)
SEAFOOD = place("Sea Palace", "seafood_restaurant", 3.1482, 101.695)
BURGER = place("Burger Shack", "burger_restaurant", 3.1490, 101.696)


def classifier(cuisine: str | None = None, **extra: Any) -> FakeProvider:
    intent = {"cuisine": cuisine, "category": None, "requirements": [], **extra}
    return FakeProvider([call(ToolCall.of("finish", answer=json.dumps(intent)))])


def session(request: str = "dinner") -> Session:
    return Session(
        id=uuid4(),
        link_token=uuid4(),
        created_at=T0,
        shared_at=T0,
        context={
            "latitude": KLCC[0],
            "longitude": KLCC[1],
            "radius_m": 1000,
            "request": request,
        },
    )


def who(
    s: Session,
    name: str | None,
    *,
    host: bool = False,
    likes: tuple[str, ...] = (),
    dislikes: tuple[str, ...] = (),
    refuses: tuple[str, ...] = (),
    allergies: tuple[str, ...] = (),
    diets: tuple[str, ...] = (),
    budget: str | None = None,
    submitted: bool = True,
    minutes: int = 0,
) -> Participant:
    return Participant(
        id=uuid4(),
        session_id=s.id,
        is_host=host,
        display_name=name,
        constraints=Constraints(refuses, allergies, diets, budget).to_json() if submitted else {},
        preferences=Preferences(likes, dislikes).to_json() if submitted else {},
        joined_at=T0 + timedelta(minutes=minutes),
        updated_at=T0,
    )


def run(
    s: Session,
    people: list[Participant],
    places: list[Any],
    provider: FakeProvider | None = None,
    sink: ListSink | None = None,
) -> Any:
    return recommend_group(
        s,
        people,
        provider=provider or classifier(),
        places=FakePlacesProvider(places),
        config=CONFIG,
        sink=sink,
    )


def test_a_group_picks_the_place_nobody_is_unhappy_with_and_explains_it() -> None:
    s = session()
    people = [
        who(s, "Alex", host=True, likes=("thai",)),
        who(s, "Sam", dislikes=("thai",), likes=("ramen",), minutes=1),
        who(s, "Kim", likes=("ramen",), minutes=2),
    ]
    result = run(s, people, [THAI, NEAR_RAMEN, FAR_THAI, BURGER])
    assert result.graph.ok
    rec = result.recommendation
    assert rec.pick.place.name == "Near Ramen"
    assert [o.place.name for o in rec.runners_up] == ["Burger Shack", "Mid Thai", "Far Thai"]
    assert rec.participant_count == 3 and rec.pending == ()
    # The least happy person is named, and the stated lowest score is a floor for everyone.
    assert rec.pick.least_happy == ("Alex",)
    low = f"{floor_score(rec.pick.minimum):.2f}"  # floored, so the claim holds
    assert "Alex is least happy with it" in rec.explanation
    assert f"nobody scored it below {low}" in rec.explanation
    assert "Runners-up: Burger Shack" in rec.explanation
    # The stated lowest score is a floor for everyone, recomputed apart from the workflow.
    members = [m for _, m in labelled_members(people) if m.has_taste]
    assert min(score(rec.pick.place, m, 1000) for m in members) == rec.pick.minimum
    assert all(score(rec.pick.place, m, 1000) >= rec.pick.minimum for m in members)


def test_hard_constraints_apply_first_and_the_refused_place_is_listed_as_excluded() -> None:
    s = session()
    people = [
        who(s, "Alex", host=True, refuses=("seafood",)),
        who(s, "Sam", likes=("seafood",), minutes=1),
    ]
    rec = run(s, people, [SEAFOOD, THAI]).recommendation
    assert rec.pick.place.name == "Mid Thai"
    assert [e.place.name for e in rec.excluded] == ["Sea Palace"]
    assert "Excluded before scoring: Sea Palace (Alex refuses seafood)" in rec.explanation
    assert "primary category only" in " ".join(rec.warnings)
    assert all("Sea Palace" not in o.place.name for o in (rec.pick, *rec.runners_up))


def test_unverifiable_constraints_are_warnings_on_the_pick_and_every_runner_up() -> None:
    s = session()
    people = [
        who(s, "Alex", host=True, allergies=("peanut",), diets=("halal",)),
        who(s, "Sam", budget="under RM30", minutes=1),
    ]
    rec = run(s, people, [THAI, NEAR_RAMEN, FAR_THAI]).recommendation
    expected = (
        "Cannot verify Alex's allergy to peanut.",
        "Cannot verify Alex's diet: halal.",
        "Cannot verify Sam's budget: under RM30.",
    )
    assert len(rec.runners_up) == 2
    for option in (rec.pick, *rec.runners_up):
        assert option.warnings == expected
    for warning in expected:
        assert warning in rec.explanation
    # None of them excluded anything, and nothing claims an option is safe.
    assert rec.excluded == ()
    assert "safe" not in rec.explanation.lower()
    assert "Opening hours, menus, prices" in rec.explanation


def test_requirements_from_the_request_the_data_cannot_check_stay_visible() -> None:
    s = session("halal dinner under RM20")
    provider = classifier(requirements=["halal", "under RM20"])
    rec = run(s, [who(s, "Alex", host=True)], [THAI], provider).recommendation
    assert "Cannot verify requirement: halal." in rec.warnings
    assert "Cannot verify requirement: under RM20." in rec.warnings


def test_all_options_excluded_is_explained_and_never_an_arbitrary_pick() -> None:
    s = session()
    people = [who(s, "Alex", host=True, refuses=("seafood", "thai")), who(s, "Sam", minutes=1)]
    rec = run(s, people, [SEAFOOD, THAI]).recommendation
    assert rec.pick is None and rec.runners_up == ()
    assert len(rec.excluded) == 2
    assert "No place is left after hard constraints" in rec.explanation
    assert "all 2 nearby places" in rec.explanation
    assert "Alex refuses seafood" in rec.explanation
    assert "Try " not in rec.explanation


def test_no_places_found_is_a_valid_empty_answer() -> None:
    s = session()
    result = run(s, [who(s, "Alex", host=True)], [])
    assert result.graph.ok
    assert result.recommendation.pick is None
    assert "No places found within this search radius." in result.recommendation.explanation
    assert "No place is left after hard constraints" not in result.recommendation.explanation


def test_a_group_of_one_matches_the_solo_recommendation() -> None:
    places = [NEAR_RAMEN, THAI, FAR_THAI, BURGER]
    for cuisine in (None, "thai"):
        s = session("thai please" if cuisine else "dinner")
        group = run(s, [who(s, "Only", host=True)], places, classifier(cuisine)).recommendation
        solo = recommend(
            SoloRequest(KLCC[0], KLCC[1], "thai please" if cuisine else "dinner"),
            provider=classifier(cuisine),
            places=FakePlacesProvider(places),
            config=CONFIG,
        ).recommendation
        assert solo and solo.pick
        assert [o.place.name for o in (group.pick, *group.runners_up)] == [
            r.place.name for r in (solo.pick, *solo.runners_up)
        ]
        assert group.pick.minimum == group.pick.average


def test_a_requested_category_cannot_override_what_makes_a_participant_unhappy() -> None:
    # "thai please": the host likes thai and Sam dislikes it, and nobody refuses either place.
    # Thai answers the request but leaves Sam at the bottom, so the alternative has the best
    # lowest score and must win, with the request still counted in everyone's scores.
    s = session("thai please")
    people = [
        who(s, "Alex", host=True, likes=("thai",)),
        who(s, "Sam", dislikes=("thai",), minutes=1),
    ]
    rec = run(s, people, [NEAR_RAMEN, THAI], classifier("thai")).recommendation
    assert rec.pick.place.name == "Near Ramen"
    assert [o.place.name for o in rec.runners_up] == ["Mid Thai"]
    thai = rec.runners_up[0]
    assert rec.pick.minimum - thai.minimum > CLOSE_SCORE_MARGIN  # far beyond the tie margin
    assert thai.least_happy == ("Sam",)
    assert "matches 1 requested category filter(s)" in thai.reasons
    assert re.fullmatch(r"(\d+ ft|\d+\.\d mi) from the search point", thai.reasons[0])
    assert "Alex and Sam are least happy with it" in rec.explanation
    # With nobody disliking it, the request does count and the matching place wins.
    liking = [who(s, "Alex", host=True, likes=("thai",)), who(s, "Sam", likes=("thai",), minutes=1)]
    rec = run(s, liking, [NEAR_RAMEN, THAI], classifier("thai")).recommendation
    assert rec.pick.place.name == "Mid Thai"


def test_an_unfilled_participant_does_not_change_the_pick_and_is_still_reported() -> None:
    # Far Thai is far out and Near Ramen is at the search point. The host likes thai, so thai wins.
    # Joining someone who has shared nothing must not hand the pick to the nearer ramen place.
    s = session()
    far_thai = place("Far Thai", "thai_restaurant", 3.1565, 101.695)  # about 900 m north
    near_ramen = place("Near Ramen", "ramen_restaurant", 3.148, 101.695)
    host = who(s, "Alex", host=True, likes=("thai",))
    alone = run(s, [host], [far_thai, near_ramen]).recommendation
    assert alone.pick.place.name == "Far Thai"
    assert alone.pick.place.distance_m > 800  # more than half the 1000 m radius from ramen
    for blank in (
        who(s, None, submitted=False, minutes=1),  # joined, shared nothing
        who(s, "Kim", minutes=1),  # shared an empty form
    ):
        rec = run(s, [host, blank], [far_thai, near_ramen]).recommendation
        assert rec.pick.place.name == "Far Thai"
        assert rec.pick.minimum == alone.pick.minimum
        assert rec.participant_count == 2
        assert rec.uncounted == (blank.display_name or "Guest 2",)
        assert "Not counted in the scores" in rec.explanation
    pending = run(s, [host, who(s, None, submitted=False, minutes=1)], [far_thai]).recommendation
    assert pending.pending == ("Guest 2",)
    assert "Guest 2 has not shared anything yet." in pending.explanation


def test_hard_constraints_of_someone_with_no_taste_still_apply() -> None:
    s = session()
    people = [
        who(s, "Alex", host=True, likes=("seafood",)),
        who(s, "Kim", refuses=("seafood",), allergies=("peanut",), minutes=1),  # no taste at all
    ]
    rec = run(s, people, [SEAFOOD, THAI]).recommendation
    assert rec.pick.place.name == "Mid Thai"
    assert [e.place.name for e in rec.excluded] == ["Sea Palace"]
    assert rec.uncounted == ("Kim",)
    assert "Cannot verify Kim's allergy to peanut." in rec.pick.warnings


def test_when_nobody_shared_a_taste_the_pick_is_the_solo_ranking_and_says_so() -> None:
    s = session("thai please")
    people = [who(s, "Alex", host=True), who(s, "Sam", minutes=1)]
    rec = run(s, people, [NEAR_RAMEN, THAI, FAR_THAI], classifier("thai")).recommendation
    solo = recommend(
        SoloRequest(KLCC[0], KLCC[1], "thai please"),
        provider=classifier("thai"),
        places=FakePlacesProvider([NEAR_RAMEN, THAI, FAR_THAI]),
        config=CONFIG,
    ).recommendation
    assert solo and solo.pick
    assert [o.place.name for o in (rec.pick, *rec.runners_up)] == [
        r.place.name for r in (solo.pick, *solo.runners_up)
    ]
    assert rec.pick.minimum is None and rec.pick.average is None and rec.pick.least_happy == ()
    assert "Nobody shared a taste preference" in rec.explanation
    assert "least happy" not in rec.explanation


def test_a_participant_who_has_shared_nothing_is_neutral_and_flagged() -> None:
    s = session()
    people = [
        who(s, "Alex", host=True, likes=("thai",)),
        who(s, None, submitted=False, minutes=1),
    ]
    rec = run(s, people, [THAI, NEAR_RAMEN]).recommendation
    assert rec.pick.place.name == "Mid Thai"
    assert rec.pending == ("Guest 2",)
    assert "Guest 2 has not shared anything yet" in rec.explanation


def test_names_are_made_unique_and_guests_are_numbered_by_join_order() -> None:
    s = session()
    people = [
        who(s, "Sam", minutes=5),
        who(s, None, host=True, minutes=0),
        who(s, "Sam", minutes=3),
        who(s, None, minutes=4),
    ]
    labelled = labelled_members(people)
    assert [m.name for _, m in labelled] == ["Guest 1", "Sam", "Guest 3", "Sam (2)"]
    assert [p.is_host for p, _ in labelled] == [True, False, False, False]
    assert [m.name for _, m in labelled_members(people[::-1])] == [
        "Guest 1",
        "Sam",
        "Guest 3",
        "Sam (2)",
    ]


def test_the_result_does_not_depend_on_the_order_participants_are_given() -> None:
    s = session()
    people = [
        who(s, "Alex", host=True, likes=("thai",), refuses=("burger",)),
        who(s, "Sam", dislikes=("thai",), minutes=1),
        who(s, "Kim", likes=("ramen",), minutes=2),
    ]
    places = [THAI, NEAR_RAMEN, FAR_THAI, BURGER, SEAFOOD]
    first = run(s, people, places).recommendation
    second = run(s, people[::-1], places[::-1]).recommendation
    assert first == second


def test_the_graph_runs_the_documented_steps_and_collects_people_in_parallel_with_research() -> (
    None
):
    sink = ListSink()
    s = session()
    result = run(s, [who(s, "Alex", host=True)], [THAI], sink=sink)
    assert list(result.graph.results) == [
        "classify",
        "intent",
        "requested_places",
        "nearby_places",
        "memory",
        "merge",
        "participants",
        "constraints",
        "score",
        "pick",
        "explain",
    ]
    starts = {e.data["step"]: e.data for e in sink.events if e.type == "step_start"}
    assert starts["participants"]["after"] == []
    assert starts["constraints"]["after"] == ["merge", "participants"]


def test_traces_never_hold_a_participant_id_or_stored_memory() -> None:
    sink = ListSink()
    s = session()
    people = [who(s, "Alex", host=True), who(s, "Sam", minutes=1)]
    run(s, people, [THAI], sink=sink)
    dump = json.dumps([e.to_dict() for e in sink.events])
    for p in people:
        assert str(p.id) not in dump
    assert repr(GroupInput(s, tuple(people))) == f"GroupInput(session={s.id}, participants=2)"


def test_one_failed_search_gives_a_partial_recommendation_with_warnings() -> None:
    s = session()

    class FlakyPlaces(FakePlacesProvider):
        def search_nearby(self, query: Any) -> Any:
            if query.cuisine:
                raise PlacesError("boom")
            return super().search_nearby(query)

    result = recommend_group(
        s,
        [who(s, "Alex", host=True)],
        provider=classifier("thai"),
        places=FlakyPlaces([THAI]),
        config=CONFIG,
    )
    assert not result.graph.ok
    assert result.recommendation and result.recommendation.pick
    assert any("requested_places" in w for w in result.recommendation.warnings)


def test_both_searches_failing_gives_no_recommendation() -> None:
    s = session()
    result = recommend_group(
        s,
        [who(s, "Alex", host=True)],
        provider=classifier(),
        places=FakePlacesProvider([THAI], error=PlacesError("down")),
        config=CONFIG,
    )
    assert result.recommendation is None
    assert not result.graph.results["merge"].ok


def test_no_user_memory_is_read_for_a_group() -> None:
    s = session()
    result = run(s, [who(s, "Alex", host=True)], [THAI])
    assert result.graph.results["memory"].value.decision.lookup is False


def test_the_shared_explanation_says_what_was_shared_and_never_who_shared_it() -> None:
    s = session()
    people = [
        who(s, "Alex", host=True, likes=("thai",)),
        who(s, "Sam", dislikes=("thai",), likes=("ramen",), allergies=("peanut",), minutes=1),
        who(s, "Kim", refuses=("seafood",), minutes=2),  # no taste, so left out of the scores
    ]
    rec = run(s, people, [THAI, NEAR_RAMEN, SEAFOOD]).recommendation
    assert rec.pick.place.name == "Near Ramen"
    assert "Alex is least happy with it" in rec.explanation
    assert "Excluded before scoring: Sea Palace (Kim refuses seafood)" in rec.explanation
    text = rec.shared_explanation
    for name in ("Alex", "Sam", "Kim"):
        assert name not in text
        assert name not in " ".join(rec.pick.shared_warnings)
    assert "Try Near Ramen" in text and "Runners-up: Mid Thai (lowest score" in text
    assert "Nobody scored it below" in text and "The group average is" in text
    assert "Excluded before scoring: Sea Palace (refused by someone in the group: seafood)" in text
    assert "1 person who shared no taste preferences" in text
    assert "Cannot verify an allergy to peanut." in text
    assert rec.pick.shared_warnings == ("Cannot verify an allergy to peanut.",)
    assert "safe" not in text.lower()


def test_the_shared_explanation_holds_the_same_pick_and_scores_as_the_hosts() -> None:
    s = session("thai please")
    people = [who(s, "Alex", host=True), who(s, "Sam", minutes=1)]
    rec = run(s, people, [NEAR_RAMEN, THAI], classifier("thai")).recommendation
    assert "Nobody shared a taste preference" in rec.shared_explanation
    assert rec.shared_explanation.startswith("Try Mid Thai")
