"""The consensus logic on its own: pure functions, no graph, no network."""

from __future__ import annotations

import random
from typing import Any

import pytest

from makan.consensus import (
    CLOSE_SCORE_MARGIN,
    NEUTRAL_TASTE,
    PROXIMITY_WEIGHT,
    REQUEST_WEIGHT,
    TASTE_WEIGHT,
    Constraints,
    InvalidInputs,
    Member,
    Preferences,
    Refusal,
    Scored,
    apply_constraints,
    clean_name,
    consensus,
    floor_score,
    order_options,
    parse_constraints,
    parse_preferences,
    request_match,
    score,
    score_options,
    shared_unverified_warnings,
    taste,
    unverified_warnings,
)
from makan.solo import Candidate

RADIUS = 1000


def cand(
    name: str,
    category: str = "restaurant",
    distance: int = 500,
    fit: int = 0,
    id: str | None = None,
) -> Candidate:
    return Candidate(id or name.lower().replace(" ", "-"), name, category, distance, None, fit)


def person(
    name: str,
    *,
    likes: tuple[str, ...] = (),
    dislikes: tuple[str, ...] = (),
    refuses: tuple[str, ...] = (),
    allergies: tuple[str, ...] = (),
    diets: tuple[str, ...] = (),
    budget: str | None = None,
) -> Member:
    return Member(
        name,
        Constraints(refuses, allergies, diets, budget),
        Preferences(likes, dislikes),
    )


def with_scores(place: Candidate, **scores: float) -> Scored:
    """An option with chosen scores, so a test can set up a tie exactly."""
    return Scored(place, tuple(sorted(scores.items())))


def names(ranked: Any) -> list[str]:
    return [r.place.name for r in ranked]


# Parsing


def test_empty_inputs_mean_no_constraints_and_no_preferences() -> None:
    assert parse_constraints({}) == Constraints()
    assert parse_preferences({}) == Preferences()


def test_inputs_are_normalized_and_round_trip_through_json() -> None:
    constraints = parse_constraints(
        {
            "refuses": ["  Seafood ", "seafood", "Thai   Food"],
            "allergies": ["peanut", " peanut "],
            "diets": ["Halal"],
            "budget": "under  RM30",
        }
    )
    assert constraints.refuses == ("seafood", "thai food")
    assert constraints.allergies == ("peanut",)
    assert constraints.diets == ("Halal",)  # free text keeps its case
    assert constraints.budget == "under RM30"
    assert parse_constraints(constraints.to_json()) == constraints
    prefs = parse_preferences({"likes": ["Thai"], "dislikes": ["burger"]})
    assert parse_preferences(prefs.to_json()) == prefs


@pytest.mark.parametrize(
    "raw",
    [
        {"allergy": ["peanut"]},  # a typo must never silently drop a constraint
        {"refuses": "seafood"},
        {"refuses": [3]},
        {"refuses": ["!!!"]},  # nothing to match a category with, so it would be inert
        {"refuses": ["sea\x00food"]},  # a control character could split a word and hide a refusal
        {"refuses": ["sea\tfood"]},
        {"refuses": ["sea\u200bfood"]},  # zero-width space
        {"refuses": ["x" * 41]},
        {"refuses": ["thai"] * 21},
        {"allergies": ["   "]},
        {"allergies": ["x" * 81]},
        {"allergies": ["pea\x00nut"]},
        {"budget": 30},
        {"budget": ""},
    ],
)
def test_malformed_constraints_are_rejected(raw: dict[str, Any]) -> None:
    with pytest.raises(InvalidInputs):
        parse_constraints(raw)


def test_malformed_preferences_are_rejected() -> None:
    bad = (
        {"like": ["thai"]},
        {"likes": "thai"},
        {"dislikes": [""]},
        {"likes": ["th\x00ai"]},
        {"dislikes": ["bur\x07ger"]},
    )
    for raw in bad:
        with pytest.raises(InvalidInputs):
            parse_preferences(raw)


def test_display_names_are_cleaned_or_rejected() -> None:
    assert clean_name("  Alex   Tan ") == "Alex Tan"
    for bad in ("   ", "x" * 41, "a\x07b"):
        with pytest.raises(InvalidInputs):
            clean_name(bad)


# Hard constraints


def test_a_refused_category_excludes_the_option_for_everyone() -> None:
    sea = cand("Sea Palace", "seafood_restaurant")
    thai = cand("Thai Garden", "thai_restaurant")
    kept, excluded = apply_constraints(
        [sea, thai], [person("Alex", refuses=("seafood",)), person("Sam")]
    )
    assert kept == (thai,)
    assert excluded[0].place == sea
    assert excluded[0].refusals == (Refusal("Alex", "seafood"),)


def test_every_refusal_of_an_excluded_option_is_recorded() -> None:
    sea = cand("Sea Palace", "seafood_restaurant")
    _, (exclusion,) = apply_constraints(
        [sea],
        [
            person("Sam", refuses=("seafood", "restaurant")),
            person("Alex", refuses=("seafood",)),
            person("Kim", refuses=("thai",)),
        ],
    )
    assert exclusion.refusals == (
        Refusal("Alex", "seafood"),
        Refusal("Sam", "restaurant"),
        Refusal("Sam", "seafood"),
    )


def test_unverifiable_constraints_never_exclude_anything() -> None:
    places = [cand("Peanut Hut", "thai_restaurant"), cand("Halal Grill", "grill")]
    members = [person("Alex", allergies=("peanut",), diets=("halal", "vegan"), budget="under RM10")]
    kept, excluded = apply_constraints(places, members)
    assert kept == tuple(places)
    assert excluded == ()


def test_exclusions_are_listed_nearest_first_whatever_the_input_order() -> None:
    far = cand("Far Sea", "seafood_restaurant", distance=900)
    near = cand("Near Sea", "seafood_restaurant", distance=100)
    members = [person("Alex", refuses=("seafood",))]
    for order in ([far, near], [near, far]):
        assert [e.place.name for e in apply_constraints(order, members)[1]] == [
            "Near Sea",
            "Far Sea",
        ]


def test_unverified_constraints_are_warnings_by_person_and_never_claim_safety() -> None:
    warnings = unverified_warnings(
        [
            person("Sam", diets=("halal",)),
            person("Alex", allergies=("peanut", "shellfish"), budget="under RM30"),
            person("Kim", refuses=("seafood",)),  # checkable, so no warning
        ]
    )
    assert warnings == (
        "Cannot verify Alex's allergy to peanut.",
        "Cannot verify Alex's allergy to shellfish.",
        "Cannot verify Alex's budget: under RM30.",
        "Cannot verify Sam's diet: halal.",
    )
    assert not any("safe" in w.lower() for w in warnings)
    assert unverified_warnings([person("Kim")]) == ()


def test_shared_warnings_say_what_was_shared_and_never_by_whom() -> None:
    warnings = shared_unverified_warnings(
        [
            person("Sam", diets=("halal",), allergies=("peanut",)),
            person("Alex", allergies=("peanut", "shellfish"), budget="under RM30"),
            person("Kim", refuses=("seafood",)),
        ]
    )
    assert warnings == (
        "Cannot verify an allergy to peanut.",
        "Cannot verify an allergy to shellfish.",
        "Cannot verify a diet: halal.",
        "Cannot verify a budget: under RM30.",
    )
    assert not any(name in " ".join(warnings) for name in ("Sam", "Alex", "Kim"))
    assert shared_unverified_warnings([person("Kim")]) == ()


# Scoring


def test_taste_is_liked_disliked_or_neutral() -> None:
    thai = cand("Thai Garden", "thai_restaurant")
    assert taste(thai, Preferences(likes=("thai",))) == 1.0
    assert taste(thai, Preferences(dislikes=("thai",))) == 0.0
    assert taste(thai, Preferences()) == NEUTRAL_TASTE
    assert taste(thai, Preferences(likes=("ramen",), dislikes=("burger",))) == NEUTRAL_TASTE
    # Liking and disliking the same place cancel out.
    assert taste(thai, Preferences(likes=("restaurant",), dislikes=("thai",))) == NEUTRAL_TASTE


def test_the_weights_make_a_taste_step_outweigh_the_request_and_the_request_outweigh_distance() -> (
    None
):
    assert TASTE_WEIGHT + REQUEST_WEIGHT + PROXIMITY_WEIGHT == 1.0
    assert 0.5 * TASTE_WEIGHT > REQUEST_WEIGHT + PROXIMITY_WEIGHT  # one step of taste
    assert REQUEST_WEIGHT / 2 > PROXIMITY_WEIGHT  # even half a request beats all of proximity
    assert CLOSE_SCORE_MARGIN == PROXIMITY_WEIGHT


def test_scores_stay_between_zero_and_one_and_nearer_and_a_better_match_are_higher() -> None:
    thai = cand("Thai Garden", "thai_restaurant", distance=0, fit=1)
    far = cand("Far Thai", "thai_restaurant", distance=RADIUS)
    fan = person("Fan", likes=("thai",))
    hater = person("Hater", dislikes=("thai",))
    assert score(thai, fan, RADIUS, requested=1) == 1.0
    assert score(far, hater, RADIUS) == 0.0
    assert score(thai, fan, RADIUS, requested=1) > score(far, fan, RADIUS, requested=1)
    near_unmatched = cand("Near", "thai_restaurant", distance=0)
    matched_far = cand("Matched Far", "thai_restaurant", distance=RADIUS, fit=1)
    assert score(matched_far, fan, RADIUS, requested=1) > score(near_unmatched, fan, RADIUS, 1)
    assert score(matched_far, fan, RADIUS, requested=0) < score(near_unmatched, fan, RADIUS, 0)
    for requested in (0, 1, 2):
        assert 0.0 <= score(far, person("Nobody", likes=("x",)), RADIUS, requested) <= 1.0
    assert request_match(cand("Half", fit=1), 2) == 0.5
    assert request_match(cand("None", fit=0), 0) == 0.0


def test_taste_outweighs_any_difference_in_distance() -> None:
    liked_far = cand("Liked Far", "thai_restaurant", distance=RADIUS)
    neutral_near = cand("Neutral Near", "ramen_restaurant", distance=0)
    fan = person("Fan", likes=("thai",))
    assert score(liked_far, fan, RADIUS) > score(neutral_near, fan, RADIUS)


def test_a_person_with_no_taste_is_left_out_of_the_scores_but_keeps_their_refusals() -> None:
    thai = cand("Thai Garden", "thai_restaurant")
    sea = cand("Sea Palace", "seafood_restaurant")
    fan = person("Fan", likes=("thai",))
    blank = person("Blank")
    refuser = person("Refuser", refuses=("seafood",))  # no taste, but a hard constraint
    assert not blank.has_taste and not refuser.has_taste and fan.has_taste
    scored = score_options([thai], [fan, blank, refuser], RADIUS)
    assert [name for name, _ in scored[0].scores] == ["Fan"]
    result = consensus([thai, sea], [fan, blank, refuser], RADIUS)
    assert names(result.ranked) == ["Thai Garden"]
    assert [e.place.name for e in result.excluded] == ["Sea Palace"]


def test_an_unfilled_participant_cannot_reverse_the_pick_whatever_the_distances() -> None:
    # Far Thai is 900 m out and Near Ramen is at the search point, so they differ by far more
    # than half the radius. A blank person must leave Fan's like in charge, as it was alone.
    far_thai = cand("Far Thai", "thai_restaurant", distance=900)
    near_ramen = cand("Near Ramen", "ramen_restaurant", distance=0)
    fan = person("Fan", likes=("thai",))
    alone = consensus([near_ramen, far_thai], [fan], RADIUS)
    assert names(alone.ranked) == ["Far Thai", "Near Ramen"]
    for blank in (person("Blank"), Member("Blank", submitted=False), person("B", refuses=("x",))):
        both = consensus([near_ramen, far_thai], [fan, blank], RADIUS)
        assert names(both.ranked) == ["Far Thai", "Near Ramen"]
        assert both.ranked[0].minimum == alone.ranked[0].minimum


def test_an_option_everyone_who_has_a_taste_likes_wins_even_with_blanks_and_distance() -> None:
    far_thai = cand("Far Thai", "thai_restaurant", distance=950)
    near_other = cand("Near Other", "ramen_restaurant", distance=10)
    members = [
        person("A", likes=("thai",)),
        person("B", likes=("thai",)),
        person("Blank 1"),
        person("Blank 2"),
    ]
    assert names(consensus([near_other, far_thai], members, RADIUS).ranked)[0] == "Far Thai"


def test_when_everyone_is_indifferent_the_order_is_request_then_distance() -> None:
    places = [
        cand("Far Match", "thai_restaurant", 700, fit=1),
        cand("Near Match", "thai_restaurant", 200, fit=1),
        cand("Near Other", "ramen_restaurant", 100),
        cand("Far Other", "ramen_restaurant", 600),
    ]
    solo = ["Near Match", "Far Match", "Near Other", "Far Other"]
    for members in ([person("A")], [person("A"), person("B")], [Member("A", submitted=False)]):
        result = consensus(places, members, RADIUS, requested=1)
        assert names(result.ranked) == solo
        assert all(r.scores == () and r.least_happy == () for r in result.ranked)
        assert names(consensus(places[::-1], members, RADIUS, requested=1).ranked) == solo
    # Ties on request and distance fall to the name, then the id.
    same = [
        cand("B", distance=5, id="2"),
        cand("A", distance=5, id="9"),
        cand("A", distance=5, id="1"),
    ]
    assert [r.place.id for r in consensus(same, [person("A")], RADIUS).ranked] == ["1", "9", "2"]


def test_scoring_needs_people_with_distinct_names() -> None:
    with pytest.raises(ValueError, match="at least one"):
        score_options([cand("A")], [], RADIUS)
    with pytest.raises(ValueError, match="distinct"):
        score_options([cand("A")], [person("Sam"), person("Sam")], RADIUS)
    with pytest.raises(ValueError, match="radius"):
        score_options([cand("A")], [person("Sam")], 0)


def test_like_and_dislike_counts_are_recorded_for_the_explanation() -> None:
    thai = cand("Thai Garden", "thai_restaurant")
    members = [
        person("A", likes=("thai",)),
        person("B", likes=("thai",)),
        person("C", dislikes=("thai",)),
    ]
    (scored,) = score_options([thai], members, RADIUS)
    assert (scored.likes, scored.dislikes) == (2, 1)
    assert [n for n, _ in scored.scores] == ["A", "B", "C"]


# The least-misery pick


def test_the_minimum_score_decides_not_the_average() -> None:
    # "Divisive" has the better average, but one person scores it far lower, so it loses.
    divisive = with_scores(cand("Divisive"), a=1.0, b=0.3)
    steady = with_scores(cand("Steady"), a=0.5, b=0.5)
    assert divisive.average > steady.average
    assert names(order_options([divisive, steady])) == ["Steady", "Divisive"]
    assert names(order_options([steady, divisive])) == ["Steady", "Divisive"]


def test_options_close_on_the_minimum_are_decided_by_the_average() -> None:
    lukewarm = with_scores(cand("Lukewarm"), a=0.50, b=0.50)
    happier = with_scores(cand("Happier"), a=0.48, b=0.95)  # lower minimum, by less than the margin
    assert lukewarm.minimum - happier.minimum < CLOSE_SCORE_MARGIN
    assert names(order_options([lukewarm, happier])) == ["Happier", "Lukewarm"]


def test_the_margin_is_inclusive_and_exact_despite_float_noise() -> None:
    best = with_scores(cand("Best"), a=0.80, b=0.80)
    edge = with_scores(cand("Edge"), a=0.75, b=0.99)  # 0.80 - 0.75 is 0.05000000000000004 in floats
    assert names(order_options([best, edge])) == ["Edge", "Best"]
    just_past = with_scores(cand("Past"), a=0.749, b=0.99)
    assert names(order_options([best, just_past])) == ["Best", "Past"]


def test_the_window_is_anchored_at_the_best_minimum_so_chains_do_not_merge() -> None:
    # A and B are close, B and C are close, but A and C are not. C must not get a say.
    a = with_scores(cand("A"), x=0.60, y=0.62)
    b = with_scores(cand("B"), x=0.56, y=0.80)
    c = with_scores(cand("C"), x=0.52, y=1.00)  # the best average, but 0.08 below the best minimum
    assert c.average > b.average > a.average
    assert names(order_options([a, b, c])) == ["B", "A", "C"]
    assert names(order_options([c, b, a])) == ["B", "A", "C"]


def test_exact_ties_fall_to_nearest_then_name_then_id() -> None:
    same = {"a": 0.6, "b": 0.6}
    near = with_scores(cand("Zed", distance=100), **same)
    far = with_scores(cand("Alpha", distance=900), **same)
    assert names(order_options([far, near])) == ["Zed", "Alpha"]
    one = with_scores(cand("Aaa", distance=100, id="2"), **same)
    two = with_scores(cand("Bbb", distance=100, id="1"), **same)
    assert names(order_options([two, one])) == ["Aaa", "Bbb"]
    p = with_scores(cand("Same", distance=100, id="b"), **same)
    q = with_scores(cand("Same", distance=100, id="a"), **same)
    assert [o.place.id for o in order_options([p, q])] == ["a", "b"]


def test_the_request_nudges_each_persons_score_but_never_outweighs_a_taste() -> None:
    # With the same taste for everyone, the option that answers the request wins on the minimum.
    asked = cand("Asked", "thai_restaurant", distance=900, fit=1)
    unasked = cand("Unasked", "thai_restaurant", distance=0)
    members = [person("A", likes=("thai",)), person("B", likes=("thai",))]
    assert names(consensus([unasked, asked], members, RADIUS, requested=1).ranked)[0] == "Asked"
    # But the request gives way to a strong dislike: matching it cannot make B happy.
    asked = cand("Asked", "thai_restaurant", fit=1)
    other = cand("Other", "ramen_restaurant")
    members = [person("A", likes=("thai",)), person("B", dislikes=("thai",))]
    result = consensus([asked, other], members, RADIUS, requested=1)
    assert names(result.ranked) == ["Other", "Asked"]
    assert result.ranked[1].minimum < result.ranked[0].minimum - CLOSE_SCORE_MARGIN
    # Two matched filters beat one for the same people.
    both = cand("Both", "thai_restaurant", fit=2)
    one = cand("One", "thai_restaurant", fit=1)
    assert names(consensus([one, both], members[:1], RADIUS, requested=2).ranked)[0] == "Both"


def test_everyone_who_gave_the_lowest_score_is_named() -> None:
    option = with_scores(cand("A"), x=0.4, y=0.4, z=0.9)
    assert option.minimum == 0.4
    assert option.least_happy == ("x", "y")
    assert option.average == round((0.4 + 0.4 + 0.9) / 3, 9)


def test_an_option_nobody_scored_has_no_minimum_and_no_least_happy_person() -> None:
    unscored = Scored(cand("A"), ())
    assert unscored.least_happy == ()
    with pytest.raises(ValueError):
        _ = unscored.minimum


def test_ranking_nothing_is_empty() -> None:
    assert order_options([]) == ()


# The whole consensus


def test_a_group_of_one_ranks_like_the_solo_ranking() -> None:
    # Solo order is request fit, then distance, name, and id, which is what a person who has
    # shared no taste gets, because there is nothing of theirs to weigh.
    places = [
        cand("Far Match", "thai_restaurant", 700, fit=1),
        cand("Near Match", "thai_restaurant", 200, fit=1),
        cand("Near Other", "ramen_restaurant", 100),
        cand("Far Other", "ramen_restaurant", 600),
    ]
    solo = ["Near Match", "Far Match", "Near Other", "Far Other"]
    result = consensus(places, [person("Only")], RADIUS, requested=1)
    assert names(result.ranked) == solo
    assert names(consensus(places[::-1], [person("Only")], RADIUS, requested=1).ranked) == solo
    # With a taste, it goes first, then the request, then distance, and the lowest score and the
    # average are the same number.
    liked = consensus(places, [person("Only", likes=("ramen",))], RADIUS, requested=1)
    assert names(liked.ranked) == ["Near Other", "Far Other", "Near Match", "Far Match"]
    for r in liked.ranked:
        assert r.minimum == r.average
    # Stated likes among equal matches break the same way for one person as for solo's memory.
    pair = [cand("Near Thai", "thai_restaurant", 100), cand("Far Ramen", "ramen_restaurant", 900)]
    assert (
        names(consensus(pair, [person("Only", likes=("ramen",))], RADIUS).ranked)[0] == "Far Ramen"
    )


def test_a_refused_category_is_never_picked_even_if_everyone_else_loves_it() -> None:
    sea = cand("Sea Palace", "seafood_restaurant", distance=50)
    ramen = cand("Ramen House", "ramen_restaurant", distance=800)
    members = [
        person("Alex", refuses=("seafood",)),
        person("Sam", likes=("seafood",)),
        person("Kim", likes=("seafood",)),
    ]
    result = consensus([sea, ramen], members, RADIUS)
    assert names(result.ranked) == ["Ramen House"]
    assert [e.place.name for e in result.excluded] == ["Sea Palace"]


def test_every_option_excluded_gives_no_pick_and_the_reasons() -> None:
    result = consensus(
        [cand("Sea Palace", "seafood_restaurant"), cand("Fish Hut", "seafood_restaurant")],
        [person("Alex", refuses=("seafood",))],
        RADIUS,
    )
    assert result.ranked == ()
    assert len(result.excluded) == 2


def test_no_candidates_at_all_is_an_empty_consensus() -> None:
    result = consensus([], [person("Alex")], RADIUS)
    assert result.ranked == () and result.excluded == ()


def test_least_misery_across_a_real_group() -> None:
    # Alex loves thai, Sam hates it. Ramen is fine for both, so it beats thai on the minimum.
    thai = cand("Thai Garden", "thai_restaurant", 100)
    ramen = cand("Ramen House", "ramen_restaurant", 100)
    members = [person("Alex", likes=("thai",)), person("Sam", dislikes=("thai",))]
    result = consensus([thai, ramen], members, RADIUS)
    assert names(result.ranked) == ["Ramen House", "Thai Garden"]
    assert result.ranked[0].least_happy == ("Alex", "Sam")


def test_the_better_average_breaks_a_tie_on_the_lowest_score_in_a_real_group() -> None:
    # B scores thai neutral and A scores ramen neutral, so both options have the same lowest
    # score. C likes only thai, which gives thai the better average.
    thai = cand("Thai Garden", "thai_restaurant", 100)
    ramen = cand("Ramen House", "ramen_restaurant", 100)
    members = [
        person("A", likes=("thai",)),
        person("B", likes=("ramen",)),
        person("C", likes=("thai",)),
    ]
    scored = score_options([thai, ramen], members, RADIUS)
    assert scored[0].minimum == scored[1].minimum and scored[0].average > scored[1].average
    assert names(consensus([ramen, thai], members, RADIUS).ranked) == ["Thai Garden", "Ramen House"]


def test_the_result_does_not_depend_on_the_order_of_options_or_people() -> None:
    rng = random.Random(7)
    categories = ["thai_restaurant", "ramen_restaurant", "burger_restaurant", "cafe", "bakery"]
    places = [
        cand(f"Place {i}", rng.choice(categories), rng.randrange(50, 950), fit=rng.randrange(2))
        for i in range(14)
    ]
    members = [
        person("Ann", likes=("thai", "cafe"), dislikes=("burger",)),
        person("Bob", likes=("ramen",), refuses=("bakery",)),
        person("Cat", dislikes=("thai",)),
        person("Dan"),
    ]
    expected = consensus(places, members, RADIUS)
    for _ in range(20):
        shuffled_places, shuffled_members = places[:], members[:]
        rng.shuffle(shuffled_places)
        rng.shuffle(shuffled_members)
        assert consensus(shuffled_places, shuffled_members, RADIUS) == expected


def test_floor_score_never_overstates_what_nobody_fell_below() -> None:
    assert floor_score(0.57) == 0.57  # 0.57 * 100 is 56.99999999999999 in floats
    assert floor_score(0.6449) == 0.64
    assert floor_score(0.645) == 0.64
    assert floor_score(0.6499999999) == 0.65  # within the rounding that scores already have
    assert floor_score(0.65) == 0.65
    assert floor_score(0.0) == 0.0
    assert floor_score(1.0) == 1.0
    # Whatever the minimum, nobody scored below the floored value shown.
    for value in (0.5, 0.5499, 0.5999, 0.675, 0.9001):
        assert floor_score(value) <= value
