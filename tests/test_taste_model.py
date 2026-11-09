"""The own taste model: features, the ridge fit, thresholds, the reason line, the per-owner cache,
the place facts kept on a rated visit, and the offline headline on the invented places.

Nothing here touches the network. Store-level tests run on the in-memory store and, when
`MAKAN_TEST_DATABASE_URL` is set, on Postgres too.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from makan import taste_model
from makan.evals import taste as taste_eval
from makan.taste_model import (
    MIN_RATINGS,
    POLICY_VERSION,
    SETTLED_RATINGS,
    TasteModel,
    TasteModels,
    features,
    fit,
    place_facts,
)
from makan.visits import (
    InvalidVisit,
    PlaceFacts,
    PlaceRef,
    RatedPlace,
    RatingAnswer,
    SignInRequired,
    parse_place,
    parse_place_facts,
)
from tests.visit_helpers import World

SOURCE = "overture:2026-09-23.1"
JAPANESE = PlaceFacts(("japanese", "sushi"), (), ("Salmon nigiri", "Dragon roll"), True)
TURKISH = PlaceFacts(("turkish",), ("halal",), ("Lahmacun",), False)
PLAIN = PlaceFacts((), (), (), False)


def places() -> list[taste_eval.LabelledPlace]:
    return taste_eval.load_places()


def ratings_of(labelled: Sequence[taste_eval.LabelledPlace]) -> list[tuple[PlaceFacts, float]]:
    return [(p.facts, 2.0 * p.level) for p in labelled]


# Features


def test_features_are_only_what_a_search_shows_of_the_place() -> None:
    facts = PlaceFacts(
        ("japanese", "not-a-term"), ("halal", "paleo"), ("Salmon Nigiri", "salmon roll"), True
    )
    assert features(facts) == {"cuisine:japanese", "diet:halal", "hours_known"}
    assert features(PLAIN) == frozenset()


def test_dish_names_saved_on_a_rating_do_not_shift_a_searched_place_s_score() -> None:
    with_dishes = [(JAPANESE, 9.0)] * 6 + [(TURKISH, 3.0)] * 6
    without = [(replace(f, dishes=()), r) for f, r in with_dishes]
    searched = place_facts(("japanese", "sushi"), (), "Mo-Su 11:00-22:00")
    assert fit(with_dishes).predict(searched) == fit(without).predict(searched)


def test_a_searched_place_lists_no_dishes_and_knows_hours_only_when_readable() -> None:
    assert place_facts(("thai",), ("vegan",), "Mo-Su 11:00-22:00") == PlaceFacts(
        ("thai",), ("vegan",), (), True
    )
    assert not place_facts((), (), "by appointment, call ahead").hours_known
    assert not place_facts((), (), None).hours_known


# Fitting


def _dual_ridge(
    rows: Sequence[frozenset[str]], y: Sequence[float], strength: float
) -> Callable[[frozenset[str]], float]:
    """The report's kernel form: alpha = (K + strength I)^-1 (y - mean), by Gaussian elimination."""
    n = len(rows)
    mean = sum(y) / n
    m = [
        [float(len(rows[i] & rows[j])) + (strength if i == j else 0.0) for j in range(n)]
        + [y[i] - mean]
        for i in range(n)
    ]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            for k in range(c, n + 1):
                m[r][k] -= f * m[c][k]
    alpha = [0.0] * n
    for r in range(n - 1, -1, -1):
        alpha[r] = (m[r][n] - sum(m[r][k] * alpha[k] for k in range(r + 1, n))) / m[r][r]

    def predict(row: frozenset[str]) -> float:
        return mean + sum(a * len(row & f) for a, f in zip(alpha, rows, strict=True))

    return predict


def test_the_fit_is_the_ridge_solution_the_report_evaluated() -> None:
    data = ratings_of(places())
    model = fit(data)
    rows = [features(f) for f, _ in data]
    reference = _dual_ridge(rows, [r for _, r in data], taste_model.RIDGE_STRENGTH)
    for facts in [f for f, _ in data] + [JAPANESE, TURKISH, PLAIN]:
        expected = min(10.0, max(0.0, reference(features(facts))))
        assert model.predict(facts) == pytest.approx(expected, abs=1e-9)
    assert model.mean == pytest.approx(sum(r for _, r in data) / len(data))
    assert model.ratings == len(data)


def test_the_fit_is_deterministic_and_additive() -> None:
    data = ratings_of(places()[:40])
    first, second = fit(data), fit(data)
    assert dict(first.weights) == dict(second.weights)
    assert dict(first.leans) == dict(second.leans)
    facts = data[0][0]
    nudge = sum(first.weights.get(f, 0.0) for f in features(facts))
    assert first.predict(facts) == pytest.approx(min(10.0, max(0.0, first.mean + nudge)))


def test_a_place_with_nothing_listed_or_never_seen_predicts_the_owner_s_average() -> None:
    model = fit(ratings_of(places()[:30]))
    assert model.predict(PLAIN) == pytest.approx(model.mean)
    unseen = PlaceFacts(("uzbek",), (), ("Plov",), False)
    assert model.predict(unseen) == pytest.approx(model.mean)


def test_a_fit_needs_ratings_and_a_positive_strength() -> None:
    with pytest.raises(ValueError):
        fit([])
    with pytest.raises(ValueError):
        fit([(PLAIN, 5.0)], strength=0.0)


def test_predictions_stay_on_the_rating_scale() -> None:
    model = fit([(JAPANESE, 10.0)] * 12 + [(TURKISH, 0.0)] * 12)
    assert 0.0 <= model.predict(TURKISH) < model.mean < model.predict(JAPANESE) <= 10.0
    extreme = replace(model, weights={"cuisine:japanese": 50.0, "cuisine:turkish": -50.0})
    assert extreme.predict(JAPANESE) == 10.0
    assert extreme.predict(TURKISH) == 0.0


def test_on_the_invented_places_it_learns_the_rater_s_taste() -> None:
    model = fit(ratings_of(places()))
    for liked in ("japanese", "chinese", "sushi"):
        assert model.predict(PlaceFacts((liked,), (), (), False)) > model.mean + 0.4
    for disliked in ("ethiopian", "lebanese"):
        assert model.predict(PlaceFacts((disliked,), (), (), False)) < model.mean - 1.0
    # Its dish words carry part of a cuisine's lean, as the report found for pide and lahmacun.
    assert model.predict(TURKISH) < model.mean - 0.5
    likes = {c for c, lean in model.leans.items() if lean >= taste_model.LEAN_MARGIN}
    dislikes = {c for c, lean in model.leans.items() if lean <= -taste_model.LEAN_MARGIN}
    assert {"japanese", "chinese", "sushi", "fusion", "sichuan"} <= likes
    assert {"turkish", "ethiopian", "lebanese"} <= dislikes
    assert not likes & dislikes


# Estimates and the reason shown


def test_an_estimate_is_on_the_owner_s_own_scale_in_half_steps() -> None:
    model = TasteModel(7.0, {"cuisine:japanese": 0.8, "cuisine:turkish": -2.26}, 12)
    assert model.estimate(JAPANESE).score == 8.0  # 7.8 rounds up to 8.0
    assert model.estimate(TURKISH).score == 4.5  # 4.74 rounds down to 4.5
    assert model.estimate(PLAIN).score == 7.0
    payload = model.estimate(JAPANESE).to_json()
    assert payload == {
        "score": 8.0,
        "scale": 10.0,
        "estimate": True,
        "ratings": 12,
        "why": "From your 12 ratings.",
        "policy_version": POLICY_VERSION,
    }


@pytest.mark.parametrize(
    ("ratings", "estimate"),
    [(MIN_RATINGS, True), (SETTLED_RATINGS - 1, True), (SETTLED_RATINGS, False), (200, False)],
)
def test_an_estimate_is_marked_as_one_below_thirty_ratings(ratings: int, estimate: bool) -> None:
    model = TasteModel(6.0, {}, ratings)
    assert model.estimate(PLAIN).estimate is estimate
    assert model.estimate(PLAIN).to_json()["estimate"] is estimate


def test_the_reason_is_the_free_cuisine_match_over_the_owner_s_leans() -> None:
    leans = {"japanese": 1.4, "sushi": 2.0, "turkish": -1.6, "italian": 0.4, "thai": 1.2}
    weights = {f"cuisine:{c}": lean / 2 for c, lean in leans.items()} | {"hours_known": 0.1}
    model = TasteModel(7.0, weights, 14, leans)
    assert model.why(JAPANESE) == "You tend to like Sushi."  # the stronger of the two likes
    assert model.why(TURKISH) == "You tend not to like Turkish."
    assert model.why(PlaceFacts(("italian",), (), (), False)) == "From your 14 ratings."
    mixed = PlaceFacts(("thai", "turkish"), (), (), False)
    assert model.why(mixed) == "From your 14 ratings."
    assert model.why(PlaceFacts((), (), (), True)) == "From your 14 ratings."
    # Nothing it lists was on a rated place, so the score is just the owner's average.
    usual = "Your usual rating. You have not rated a place like this yet."
    assert model.why(PLAIN) == usual
    assert model.why(PlaceFacts(("uzbek",), ("vegan",), ("Plov",), False)) == usual
    assert model.predict(PLAIN) == 7.0


def test_a_listed_diet_never_becomes_the_reason() -> None:
    model = fit([(PlaceFacts((), ("vegan",), (), False), 10.0)] * 10 + [(PLAIN, 2.0)] * 10)
    assert model.predict(PlaceFacts((), ("vegan",), (), False)) > model.mean
    assert model.why(PlaceFacts((), ("vegan",), (), False)) == "From your 20 ratings."
    assert "vegan" not in str(model.estimate(PlaceFacts((), ("vegan",), (), False)).to_json())


def test_a_lean_needs_more_than_one_rating_to_count() -> None:
    data = [(PlaceFacts(("greek",), (), (), False), 4.0)] + [(PLAIN, 6.0)] * 11
    model = fit(data)
    assert model.leans["greek"] == pytest.approx(0.5 * (4.0 - model.mean))
    assert model.why(PlaceFacts(("greek",), (), (), False)) == "From your 12 ratings."


# The per-owner cache


class Ratings:
    """A stand-in for `Visits.taste_ratings` that counts reads."""

    def __init__(self) -> None:
        self.by_owner: dict[UUID, list[RatedPlace]] = {}
        self.reads = 0

    def __call__(self, user_id: UUID) -> list[RatedPlace]:
        self.reads += 1
        return list(self.by_owner.get(user_id, []))

    def give(
        self,
        user_id: UUID,
        data: Sequence[tuple[PlaceFacts, float]],
        place: Callable[[int], str] = lambda i: f"p{i}",
    ) -> None:
        self.by_owner[user_id] = [
            RatedPlace(uuid4(), ("overture", place(i)), facts, Decimal(str(rating)))
            for i, (facts, rating) in enumerate(data)
        ]


def test_fewer_than_ten_ratings_has_no_model_and_ten_has_one() -> None:
    ratings = Ratings()
    owner = uuid4()
    ratings.give(owner, [(JAPANESE, 9.0)] * (MIN_RATINGS - 1))
    models = TasteModels(ratings)
    assert models.for_owner(owner) is None
    ratings.give(owner, [(JAPANESE, 9.0)] * MIN_RATINGS)
    model = models.for_owner(owner)
    assert model is not None and model.ratings == MIN_RATINGS


def test_repeat_visits_to_one_place_count_as_one_rated_place() -> None:
    ratings = Ratings()
    owner = uuid4()
    ratings.give(owner, [(JAPANESE, 9.0)] * (MIN_RATINGS + 5), place=lambda _: "ramen")
    models = TasteModels(ratings)
    assert models.for_owner(owner) is None
    repeats = [(JAPANESE, 9.0)] * 5
    others = [(TURKISH, 3.0)] * (MIN_RATINGS - 2)
    ratings.give(owner, repeats + others, place=lambda i: "ramen" if i < 5 else f"p{i}")
    assert models.for_owner(owner) is None
    ratings.give(
        owner, repeats + others + [(PLAIN, 6.0)], place=lambda i: "ramen" if i < 5 else f"p{i}"
    )
    model = models.for_owner(owner)
    assert model is not None and model.ratings == MIN_RATINGS
    assert model.estimate(TURKISH).to_json()["ratings"] == MIN_RATINGS


def test_the_newest_rating_of_a_place_is_the_one_that_counts() -> None:
    ratings = Ratings()
    owner = uuid4()
    # Newest first, as `Visits.taste_ratings` returns them: the ramen shop was 2 lately, 10 before.
    data = [(JAPANESE, 2.0), (JAPANESE, 10.0), (JAPANESE, 10.0)] + [(TURKISH, 6.0)] * MIN_RATINGS
    ratings.give(owner, data, place=lambda i: "ramen" if i < 3 else f"p{i}")
    model = TasteModels(ratings).for_owner(owner)
    expected = fit([(JAPANESE, 2.0)] + [(TURKISH, 6.0)] * MIN_RATINGS)
    assert model is not None and model.ratings == MIN_RATINGS + 1
    assert model.mean == pytest.approx(expected.mean)
    assert dict(model.weights) == pytest.approx(dict(expected.weights))
    assert model.predict(JAPANESE) < model.predict(TURKISH)


def test_a_model_is_fitted_once_and_refitted_when_the_owner_s_ratings_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fits: list[int] = []
    real = taste_model.fit

    def counting(data: Sequence[tuple[PlaceFacts, float]], *args: object) -> TasteModel:
        fits.append(len(data))
        return real(data)

    monkeypatch.setattr(taste_model, "fit", counting)
    ratings = Ratings()
    owner = uuid4()
    ratings.give(owner, [(JAPANESE, 9.0)] * 6 + [(TURKISH, 3.0)] * 6)
    models = TasteModels(ratings)
    first = models.for_owner(owner)
    assert models.for_owner(owner) is first
    assert fits == [12]
    # A changed rating, a changed fact and a new rating each refit.
    changed = list(ratings.by_owner[owner])
    changed[0] = replace(changed[0], rating=Decimal("2.0"))
    ratings.by_owner[owner] = changed
    second = models.for_owner(owner)
    assert second is not first and second is not None and fits == [12, 12]
    changed[1] = replace(changed[1], facts=TURKISH)
    third = models.for_owner(owner)
    assert third is not second and fits == [12, 12, 12]
    ratings.by_owner[owner] = [*changed, replace(changed[0], visit_id=uuid4(), place=("o", "new"))]
    assert models.for_owner(owner) is not third and fits == [12, 12, 12, 13]
    # Dropping below the threshold drops the cached model too.
    ratings.by_owner[owner] = changed[:5]
    assert models.for_owner(owner) is None
    assert not models.cached(owner)


def test_each_owner_has_their_own_model_and_a_guest_or_stranger_none() -> None:
    ratings = Ratings()
    alice, bob, carol = uuid4(), uuid4(), uuid4()
    ratings.give(alice, [(JAPANESE, 10.0)] * 6 + [(TURKISH, 2.0)] * 6)
    ratings.give(bob, [(JAPANESE, 2.0)] * 6 + [(TURKISH, 10.0)] * 6)
    ratings.give(carol, [(JAPANESE, 10.0)] * 3)
    models = TasteModels(ratings)
    a, b = models.for_owner(alice), models.for_owner(bob)
    assert a is not None and b is not None
    assert a.predict(JAPANESE) > a.predict(TURKISH)
    assert b.predict(JAPANESE) < b.predict(TURKISH)
    assert models.for_owner(carol) is None
    assert models.for_owner(uuid4()) is None
    models.forget(alice)
    assert not models.cached(alice) and models.cached(bob)


def test_the_cache_holds_a_bounded_number_of_owners() -> None:
    ratings = Ratings()
    owners = [uuid4() for _ in range(3)]
    for owner in owners:
        ratings.give(owner, [(JAPANESE, 8.0)] * MIN_RATINGS)
    models = TasteModels(ratings, capacity=2)
    for owner in owners:
        models.for_owner(owner)
    assert [models.cached(o) for o in owners] == [False, True, True]


def test_the_module_is_inert_on_import() -> None:
    """Imported in a fresh interpreter with no key and every connection refused, it opens no
    connection and loads no database driver or hosted scorer, so the public site stays inert."""
    script = """
import json, socket, sys

attempts = []

def refuse(*args, **kwargs):
    attempts.append(repr(args))
    raise OSError("network blocked")

socket.socket.connect = refuse
socket.socket.connect_ex = refuse
socket.create_connection = refuse
socket.getaddrinfo = refuse
import makan.taste_model
banned = ("psycopg", "makan.profile_fit", "makan.providers.scoring")
loaded = sorted(m for m in sys.modules if m.startswith(banned))
print(json.dumps({"attempts": attempts, "loaded": loaded}))
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MAKAN_", "OPENROUTER_"))}
    done = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        env=env,
    )
    assert json.loads(done.stdout) == {"attempts": [], "loaded": []}


# Place facts on a rated visit


def test_place_facts_are_checked_against_the_vocabularies() -> None:
    facts = parse_place_facts(
        ["thai", "thai", "seafood"],
        ["vegan"],
        ["  Pad Thai ", "", "Tom​ Yum"],
        True,
    )
    assert facts == PlaceFacts(("thai", "seafood"), ("vegan",), ("Pad Thai", "Tom Yum"), True)
    with pytest.raises(InvalidVisit):
        parse_place_facts(["thai food"], [])
    with pytest.raises(InvalidVisit):
        parse_place_facts([], ["paleo"])
    with pytest.raises(InvalidVisit):
        parse_place_facts([], [], ["dish"] * 51)
    with pytest.raises(InvalidVisit):
        parse_place_facts([], [], ["x" * 121])
    with pytest.raises(InvalidVisit):
        parse_place_facts([], [], [], 1)  # type: ignore[arg-type]


def at(place_id: str, facts: PlaceFacts | None = JAPANESE, name: str = "A place") -> PlaceRef:
    return parse_place(SOURCE, place_id, name, None, facts)


def test_a_rated_visit_keeps_its_place_facts_and_only_those_count(world: World) -> None:
    alice = world.user()
    world.visit(alice, place=at("sushi", JAPANESE), rating=9)
    world.visit(alice, place=at("kebab", TURKISH), rating="3.5", dishes=())
    world.visit(alice, place=at("old", None), rating=8)  # saved without facts
    found = world.visits.taste_ratings(alice)
    assert sorted((r.place, r.facts, r.rating) for r in found) == sorted(
        [
            (("overture", "sushi"), JAPANESE, Decimal("9.0")),
            (("overture", "kebab"), TURKISH, Decimal("3.5")),
        ]
    )
    [stored] = [v for v in world.visits.history(alice) if v.visit.place_id == "sushi"]
    assert stored.visit.place_cuisines == ("japanese", "sushi")
    assert stored.visit.place_dishes == ("Salmon nigiri", "Dragon roll")
    assert stored.visit.place_hours_known is True


def test_taste_ratings_are_the_owner_s_own_and_never_a_guest_s(world: World) -> None:
    alice, bob = world.user("Alice"), world.user("Bob")
    world.visit(alice, place=at("a"), rating=9)
    world.visit(bob, place=at("b"), rating=1)
    assert [r.place for r in world.visits.taste_ratings(alice)] == [("overture", "a")]
    assert [r.place for r in world.visits.taste_ratings(bob)] == [("overture", "b")]
    with pytest.raises(SignInRequired):
        world.visits.taste_ratings(None)


def test_editing_the_place_keeps_replaces_or_clears_its_facts(world: World) -> None:
    alice = world.user()
    view = world.visit(alice, place=at("sushi", JAPANESE), rating=9)
    visit_id = view.visit.id
    # The same place without facts keeps them.
    world.visits.edit_visit(alice, visit_id, place=at("sushi", None, "Renamed"))
    assert world.visits.visit(alice, visit_id).visit.place_cuisines == ("japanese", "sushi")
    # New facts replace them.
    world.visits.edit_visit(alice, visit_id, place=at("sushi", TURKISH))
    assert world.visits.visit(alice, visit_id).visit.place_cuisines == ("turkish",)
    # Another place without facts keeps none, since the old ones describe a different venue.
    world.visits.edit_visit(alice, visit_id, place=at("elsewhere", None))
    moved = world.visits.visit(alice, visit_id).visit
    assert (moved.place_cuisines, moved.place_diets, moved.place_dishes) == (None, None, None)
    assert moved.place_hours_known is None
    assert world.visits.taste_ratings(alice) == []


def test_a_tagged_visit_counts_only_once_its_owner_rates_it(world: World) -> None:
    alice, bob = world.user("Alice"), world.user("Bob")
    tagger_view = world.visit(alice, place=at("sushi", JAPANESE), rating=9, party="with_others")
    tag = world.visits.tag(alice, tagger_view.visit.id, bob, acknowledged_sharing=True)
    answered = world.visits.accept_tag(bob, tag.tag.id)
    assert answered.visit is not None
    mine = answered.visit.visit
    # The venue's facts come along with its name; the tagger's rating does not.
    assert mine.place_cuisines == ("japanese", "sushi") and mine.rating is None
    assert world.visits.taste_ratings(bob) == []
    world.visits.confirm(bob, mine.id, rating=RatingAnswer("change", 4))
    [rated] = world.visits.taste_ratings(bob)
    assert (rated.rating, rated.facts) == (Decimal("4.0"), JAPANESE)
    assert [r.rating for r in world.visits.taste_ratings(alice)] == [Decimal("9.0")]


def test_taste_ratings_read_a_bounded_number_of_recent_visits(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from makan.visits import service

    monkeypatch.setattr(service, "TASTE_READ_LIMIT", 3)
    alice = world.user()
    for i in range(5):
        world.visit(alice, place=at(f"p{i}"), rating=i)
        world.clock.advance(timedelta(days=1))
    assert len(world.visits.taste_ratings(alice)) == 3


# The offline headline


def test_the_offline_eval_beats_the_average_on_the_invented_places() -> None:
    report = taste_eval.run()
    assert report.places == 120
    assert report.policy_version == POLICY_VERSION
    assert report.own.mae == pytest.approx(0.66, abs=0.02)
    assert report.average.mae == pytest.approx(0.83, abs=0.01)
    assert report.own.within_one >= 0.9
    assert report.own.exact > report.average.exact + 0.05
    # Useful from ten ratings, and better with more.
    curve = report.curve
    assert curve["10"]["own_mae"] < curve["10"]["average_mae"] - 0.03
    assert curve["30"]["own_mae"] < curve["30"]["average_mae"] - 0.05
    assert curve["5"]["own_mae"] > curve["10"]["own_mae"] > curve["30"]["own_mae"]


def test_the_model_beats_the_average_on_every_split() -> None:
    labelled = places()
    for seed in range(5):
        own, average = taste_eval.cross_validate(labelled, seed)
        own_mae = math.fsum(abs(p - y) for p, y in own) / len(own)
        average_mae = math.fsum(abs(p - y) for p, y in average) / len(average)
        assert own_mae < average_mae - 0.15


def test_held_out_folds_keep_twins_and_repeats_together() -> None:
    labelled = places()
    fold_of = taste_eval.make_folds(labelled, taste_eval.FOLDS, 0)
    by_cluster: dict[str, set[int]] = {}
    for p in labelled:
        by_cluster.setdefault(p.cluster, set()).add(fold_of[p.id])
    assert all(len(folds) == 1 for folds in by_cluster.values())
    assert set(fold_of.values()) == set(range(taste_eval.FOLDS))


def test_the_eval_prints_a_report(capsys: pytest.CaptureFixture[str]) -> None:
    taste_eval.main([])
    out = capsys.readouterr().out
    assert '"policy_version": "own-taste-ridge-v2"' in out
    assert '"own"' in out and '"average"' in out
