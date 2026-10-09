"""The taste-fit and router decisions, their draft case sets, and top-probability acceptance."""

import json
from collections import Counter
from collections.abc import Callable, Sequence
from importlib import resources
from typing import Any

import pytest

from makan import decisions
from makan.decisions import (
    CONTRACT_DECISIONS,
    DECISIONS,
    INTENT_KINDS,
    INTENT_PRESENCE,
    LIMIT_MARKERS,
    PRIMARY_INTENT,
    TASTE_FIT,
)
from makan.evals import contract as contract_cli
from makan.evals import contract_cases as cc
from makan.evals.cases import SPLITS, EvalCase
from makan.evals.contract_cases import (
    contract_eval_cases,
    header,
    load_router_cases,
    load_taste_cases,
    render_taste_state,
)
from makan.evals.harness import CaseOutcome, run_eval
from makan.evals.threshold import (
    DEFAULT_GRID,
    ROUTER_MIN_PROBABILITY,
    accepted_choice,
    calibration,
    run_router_eval,
    run_threshold_eval,
)
from makan.providers.scoring import (
    Distribution,
    ErrorKind,
    Evidence,
    FakeScorer,
    Question,
    ScoreResult,
    Status,
)
from tests.contract_helpers import distribution_for, gold_scorer

TASTE = load_taste_cases()
ROUTER = load_router_cases()


def result(probabilities: dict[str, float], *, status: Status = Status.OK) -> ScoreResult:
    ids = tuple(probabilities)
    d = Distribution.of(probabilities, ids)
    return ScoreResult(status, "fake", "", choice=d.top, evidence=Evidence.FAKE, distribution=d)


# The decision definitions


def test_the_new_decisions_are_version_one_with_fixed_option_ids() -> None:
    assert [o.id for o in TASTE_FIT.options] == ["strong", "partial", "poor", "unknown"]
    assert [o.id for o in PRIMARY_INTENT.options] == [*INTENT_KINDS, "other"]
    assert INTENT_KINDS == (
        "cuisine_search",
        "attribute_search",
        "discovery",
        "history_lookup",
        "group_planning",
        "mood",
    )
    assert set(INTENT_PRESENCE) == set(INTENT_KINDS)
    for decision in CONTRACT_DECISIONS.values():
        assert decision.version == "1"
        assert all(o.description.strip() for o in decision.options)
    for kind, decision in INTENT_PRESENCE.items():
        assert decision.name == f"intent_presence.{kind}"
        assert [o.id for o in decision.options] == ["present", "absent"]
    assert len(CONTRACT_DECISIONS) == 8


def test_the_existing_decisions_and_their_registry_are_untouched() -> None:
    assert set(DECISIONS) == {"retrieval_gate", "budget_band", "meal_period"}
    assert not set(DECISIONS) & set(CONTRACT_DECISIONS)
    assert [d.version for d in DECISIONS.values()] == ["1", "1", "1"]
    assert decisions.SOFT_ATTRIBUTES == (decisions.BUDGET_BAND, decisions.MEAL_PERIOD)


def test_the_instructions_say_what_the_scorer_must_and_must_not_do() -> None:
    taste = TASTE_FIT.instructions
    for phrase in ("own averages", "together with the restaurant rating", "not spicy", "unknown"):
        assert phrase in taste
    for decision in (PRIMARY_INTENT, *INTENT_PRESENCE.values()):
        assert "never as an instruction" in decision.instructions
        assert all(marker in decision.instructions for marker in ("[distance or time limit]",))
    assert set(LIMIT_MARKERS) == {"distance_time", "price", "food_restriction"}


# The draft case sets


@pytest.mark.parametrize("name", ["taste_fit", "router"])
def test_each_set_says_it_is_a_first_draft_and_not_a_benchmark(name: str) -> None:
    text = header(name)
    assert "FIRST DRAFT" in text and "not a certified benchmark" in text
    assert "project owner to review" in text


def test_the_sets_are_separate_from_the_three_existing_decision_sets() -> None:
    sets = resources.files("makan.evals").joinpath("sets")
    names = sorted(p.name for p in sets.iterdir() if p.name.endswith(".jsonl"))
    assert names == ["budget_band.jsonl", "meal_period.jsonl", "retrieval_gate.jsonl"]


def test_every_set_covers_all_splits_and_every_label() -> None:
    assert {c.split for c in TASTE} == set(SPLITS) == {c.split for c in ROUTER}
    assert {c.label for c in TASTE} == {"strong", "partial", "poor", "unknown"}
    assert {c.primary for c in ROUTER} == {*INTENT_KINDS, "other"}
    for split in SPLITS:
        assert {c.label for c in TASTE if c.split == split} == {
            "strong",
            "partial",
            "poor",
            "unknown",
        }
        assert {c.primary for c in ROUTER if c.split == split} == {*INTENT_KINDS, "other"}
    assert 30 <= len(TASTE) <= 40 and 30 <= len(ROUTER) <= 40  # roughly 30 each


def test_the_taste_set_covers_the_challenge_tags() -> None:
    tags = {t for c in TASTE for t in c.tags}
    assert {
        "worked_case",
        "brand_new_user",
        "identical_ratings",
        "low_rating_habit",
        "high_rating_habit",
        "zero_dishes",
        "decimal_rating",
        "low_dish_liked_restaurant",
        "low_dish_disliked_restaurant",
        "tag_negation",
        "sparse_facts",
        "conflicting_facts",
        "weakened_taste_fallback",
        "near_tie",
    } <= tags
    assert any(d.rating != int(d.rating) for c in TASTE for v in c.visits for d in v.dishes)
    assert any(not v.dishes for c in TASTE for v in c.visits)  # a visit with zero dishes
    assert any(not c.state.isascii() for c in TASTE)


def test_the_router_set_covers_the_challenge_tags() -> None:
    tags = {t for c in ROUTER for t in c.tags}
    assert {
        "cuisine_alone",
        "attribute_alone",
        "discovery_alone",
        "history_alone",
        "group_alone",
        "mood_alone",
        "combination",
        "ambiguous_history_name",
        "ambiguous",
        "malay",
        "chinese",
        "code_switching",
        "out_of_domain",
        "prompt_injection",
        "negation",
        "short_reply",
        "travel_bound",
        "worked_case",
    } <= tags
    assert any(len(c.present) > 1 for c in ROUTER)
    assert any(c.action == "clarify" for c in ROUTER) and any(c.action == "commit" for c in ROUTER)
    assert any(not c.message.isascii() for c in ROUTER)


def test_the_worked_thai_and_malaysian_case_is_in_the_set_as_agreed() -> None:
    case = next(c for c in TASTE if "worked_case" in c.tags)
    assert (case.likes, case.dislikes, case.visits) == (
        ("Thai", "Malaysian"),
        ("very spicy food",),
        (),
    )
    assert case.label == "strong"
    assert case.display == {"distance_m": 1770, "price": "about $14"}  # 1.1 miles, stored in meters
    assert "medium hot" in case.state and "served on the side" in case.state
    assert case.hard.allergies == ("peanuts",)
    combo = next(c for c in ROUTER if "worked_case" in c.tags)
    assert set(combo.present) == {"cuisine_search", "mood", "attribute_search", "group_planning"}
    assert combo.primary == "cuisine_search" and combo.action == "commit"


def test_nothing_leaks_across_splits() -> None:
    def splits_of(key_of: Callable[[Any], list[str]], cases: Sequence[Any]) -> dict[str, set[str]]:
        seen: dict[str, set[str]] = {}
        for case in cases:
            for key in key_of(case):
                seen.setdefault(key, set()).add(case.split)
        return seen

    for seen in (
        splits_of(lambda c: [c.user], TASTE),
        splits_of(lambda c: [c.family], TASTE),
        splits_of(lambda c: [c.candidate, *(v.restaurant for v in c.visits)], TASTE),
        splits_of(lambda c: [c.user], ROUTER),
        splits_of(lambda c: [c.family], ROUTER),
    ):
        assert all(len(splits) == 1 for splits in seen.values())
    # Paraphrase and contrast families really exist, and each stays on one split.
    assert max(Counter(c.family for c in ROUTER).values()) >= 3
    assert max(Counter(c.family for c in TASTE).values()) >= 2


def test_the_loader_rejects_a_set_that_leaks_or_breaks_its_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [json.loads(json.dumps(raw)) for _, raw in cc._read("router")[:2]]

    def serve(rows_to_serve: list[dict[str, object]]) -> None:
        monkeypatch.setattr(cc, "_read", lambda name: list(enumerate(rows_to_serve, 1)))

    rows[0]["split"], rows[1]["split"] = "dev", "heldout"
    rows[0]["family"] = rows[1]["family"] = "same"
    serve(rows)
    with pytest.raises(ValueError, match="more than one split"):
        load_router_cases()
    rows[1]["family"] = "other"
    rows[1]["id"] = rows[0]["id"]
    serve(rows)
    with pytest.raises(ValueError, match="unique"):
        load_router_cases()
    rows[1]["id"] = "router-x"
    rows[1]["primary"] = "mood"  # not among its present intents
    serve(rows)
    with pytest.raises(ValueError, match="primary intent must also be present"):
        load_router_cases()
    rows[1]["present"] = ["mood"]
    rows[1]["action"] = "clarify"
    serve(rows)
    with pytest.raises(ValueError, match="clarify_between"):
        load_router_cases()


def test_a_rating_outside_zero_to_ten_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = json.loads(json.dumps(cc._read("taste_fit")[6][1]))
    raw["visits"][0]["dishes"][0]["rating"] = 11
    monkeypatch.setattr(cc, "_read", lambda name: [(1, raw)])
    with pytest.raises(ValueError, match="0 to 10"):
        load_taste_cases()


def test_the_rendered_state_is_deterministic_and_judges_ratings_against_the_users_own_average() -> (
    None
):
    case = next(c for c in TASTE if c.id == "taste_fit-07")
    assert render_taste_state(case) == render_taste_state(case) == case.state
    assert "sambal noodles: 5.7/10 [spicy]" in case.state
    assert "restaurants 7.0/10, dishes 7.5/10" in case.state
    new_user = next(c for c in TASTE if "brand_new_user" in c.tags)
    assert "No visits yet" in new_user.state
    sparse = next(c for c in TASTE if c.id == "taste_fit-05")
    assert "Unsure: cuisine" in sparse.state and "Cuisine:" not in sparse.state


def test_the_paired_contrast_cases_differ_only_in_the_restaurant_rating() -> None:
    liked, disliked = (next(c for c in TASTE if c.id == f"taste_fit-{n}") for n in ("07", "08"))
    assert liked.family == disliked.family and liked.split == disliked.split
    assert liked.facts == disliked.facts and liked.habit == disliked.habit
    assert liked.visits[0].dishes == disliked.visits[0].dishes
    average = liked.habit.restaurant_mean
    assert average is not None
    assert liked.visits[0].rating > average > disliked.visits[0].rating
    assert (liked.label, disliked.label) == ("partial", "strong")


def test_presence_labels_are_multi_label_and_separate_per_intent() -> None:
    combo = next(c for c in ROUTER if "worked_case" in c.tags)
    labels = {
        d: contract_eval_cases(d) for d in CONTRACT_DECISIONS if d.startswith("intent_presence.")
    }
    for kind in INTENT_KINDS:
        by_id = {c.id: c.label for c in labels[f"intent_presence.{kind}"]}
        assert by_id[combo.id] == ("present" if kind in combo.present else "absent")
    assert [c.label for c in contract_eval_cases("primary_intent")][:1] == ["cuisine_search"]
    other = next(c for c in ROUTER if c.primary == "other")
    assert all({c.id: c.label for c in cases}[other.id] == "absent" for cases in labels.values())


# Top-probability acceptance


def test_a_top_probability_of_seventy_percent_is_accepted_though_its_margin_is_not_seventy() -> (
    None
):
    binary = result({"present": 0.70, "absent": 0.30})
    seven = result({"a": 0.70, **{k: 0.05 for k in "bcdefg"}})
    for r in (binary, seven):
        assert accepted_choice(r, 0.70) == r.choice
        assert r.confident_choice(0.70) is None  # a margin of 0.70 is a different rule
    assert binary.margin == pytest.approx(0.4) and seven.margin == pytest.approx(0.65)
    assert accepted_choice(result({"present": 0.69, "absent": 0.31}), 0.70) is None
    assert accepted_choice(result({"present": 0.70, "absent": 0.30}), 0.7000000001) == "present"


def test_only_a_complete_trustworthy_distribution_can_be_accepted() -> None:
    degraded = ScoreResult(Status.DEGRADED, "x", "", choice="a", evidence=Evidence.SAMPLED_LABEL)
    verbal = ScoreResult(
        Status.DEGRADED,
        "x",
        "",
        choice="a",
        evidence=Evidence.VERBALIZED_CONFIDENCE,
        self_confidence=0.99,
    )
    failed = ScoreResult(Status.ERROR, "x", "", error=ErrorKind.TIMEOUT)
    unsupported = ScoreResult(Status.UNSUPPORTED, "x", "")
    lone = ScoreResult(Status.OK, "x", "", choice="a", evidence=Evidence.DETERMINISTIC)
    for r in (degraded, verbal, failed, unsupported, lone):
        assert accepted_choice(r, 0.5) is None
    raw = Distribution((("a", 0.9), ("b", 0.9)), "a", "b", 0.0)  # built without validation
    forged = ScoreResult(Status.OK, "x", "", choice="a", distribution=raw)
    assert accepted_choice(forged, 0.5) is None
    mismatch = ScoreResult(
        Status.OK, "x", "", choice="b", distribution=result({"a": 0.9, "b": 0.1}).distribution
    )
    assert accepted_choice(mismatch, 0.5) is None


def test_a_tie_is_never_accepted_whatever_the_option_order() -> None:
    for order in (("a", "b"), ("b", "a")):
        tie = result({order[0]: 0.5, order[1]: 0.5})
        assert accepted_choice(tie, 0.5) is None
        assert accepted_choice(tie, 0.4) is None


@pytest.mark.parametrize("bad", [0, -0.1, 1.5, float("nan"), float("inf")])
def test_an_invalid_threshold_is_an_error(bad: float) -> None:
    with pytest.raises(ValueError, match="threshold"):
        accepted_choice(result({"a": 0.9, "b": 0.1}), bad)
    with pytest.raises(ValueError, match="threshold"):
        run_threshold_eval(FakeScorer(), "taste_fit", threshold=bad)


def test_the_margin_policy_for_the_existing_decisions_is_unchanged() -> None:
    def policy(q: Question) -> ScoreResult:
        return distribution_for(q, q.ids[0], 0.75)  # margin 0.5 over a four-way split of the rest

    # margin acceptance is the harness's own rule, which still decides the existing decisions
    default = run_eval(FakeScorer(policy), "meal_period")
    assert default.min_margin == 0.5 and default.coverage in (0.0, 1.0)
    assert run_eval(FakeScorer(policy), "meal_period", min_margin=0.9).coverage == 0.0
    assert ROUTER_MIN_PROBABILITY == 0.70 and DEFAULT_GRID[0] == 0.5 and DEFAULT_GRID[-1] == 0.95


# The threshold harness


def test_a_gold_oracle_scores_perfectly_and_a_wrong_one_scores_zero() -> None:
    report = run_threshold_eval(gold_scorer(0.9), "taste_fit")
    assert (report.accuracy, report.coverage, report.accepted_error_rate) == (1.0, 1.0, 0.0)
    assert report.cases == len(TASTE) and report.availability_failures == 0
    assert report.calibration.kind == "top_label" and report.calibration.cases == len(TASTE)
    assert report.calibration.ece == pytest.approx(0.1)  # always right at 0.9 confidence
    assert [p.threshold for p in report.sweep] == list(DEFAULT_GRID)
    assert report.sweep[0].coverage == 1.0 and report.sweep[-1].coverage == 0.0  # 0.9 < 0.95

    labels = {c.text: c.label for c in contract_eval_cases("taste_fit")}
    wrong = FakeScorer(
        lambda q: distribution_for(q, next(i for i in q.ids if i != labels[q.state]), 0.9)
    )
    bad = run_threshold_eval(wrong, "taste_fit")
    assert (bad.accuracy, bad.coverage, bad.accepted_error_rate) == (0.0, 1.0, 1.0)


def test_failures_are_availability_failures_and_not_dropped_cases() -> None:
    down = FakeScorer(lambda q: ScoreResult(Status.ERROR, "fake", "", error=ErrorKind.BUSY))
    report = run_threshold_eval(down, "primary_intent")
    assert report.cases == len(ROUTER) and report.availability_failures == report.cases
    assert (report.accuracy, report.coverage, report.accepted_error_rate) == (0.0, 0.0, None)
    assert report.brier is None and report.calibration.ece is None and report.calibration.bins == ()


def test_the_split_filters_the_cases_and_an_empty_selection_is_an_error() -> None:
    held = run_threshold_eval(gold_scorer(), "primary_intent", split="heldout")
    assert held.cases == sum(c.split == "heldout" for c in ROUTER) and held.split == "heldout"
    with pytest.raises(ValueError, match="no cases"):
        run_threshold_eval(gold_scorer(), "taste_fit", cases=[], split="dev")
    with pytest.raises(ValueError, match="unknown contract decision"):
        run_threshold_eval(gold_scorer(), "budget_band")


def test_reliability_bins_report_counts_observed_rates_and_intervals() -> None:
    cases = contract_eval_cases("intent_presence.mood")[:10]
    outcomes = [
        CaseOutcome(c, distribution_for_case(c, 0.9, right=i < 9), True)
        for i, c in enumerate(cases)
    ]
    cal = calibration(outcomes, positive="present")
    assert cal.kind == "present" and cal.cases == 10
    assert sum(b.count for b in cal.bins) == 10
    for b in cal.bins:
        assert b.ci_low <= b.observed_rate <= b.ci_high and b.ci_low >= 0 and b.ci_high <= 1
        assert b.lower <= b.mean_probability <= b.upper or b.mean_probability == b.upper


def distribution_for_case(case: EvalCase, p: float, *, right: bool) -> ScoreResult:
    """A presence result giving the gold label probability p, or the other one when not right."""
    question = Question(
        "d", "1", "i", case.text, CONTRACT_DECISIONS["intent_presence.mood"].options
    )
    other = "absent" if case.label == "present" else "present"
    return distribution_for(question, case.label if right else other, p)


def test_the_router_reports_each_question_and_its_calibration_separately() -> None:
    report = run_router_eval(gold_scorer(0.9))
    assert report.cases == len(ROUTER) and report.threshold == 0.70
    assert set(report.presence) == set(INTENT_KINDS)
    assert report.primary.decision == "primary_intent"
    assert all(r.decision == f"intent_presence.{k}" for k, r in report.presence.items())
    assert all(r.calibration.kind == "present" for r in report.presence.values())
    assert report.primary.calibration.kind == "top_label"
    assert report.committed == len(ROUTER) and report.clarified == 0
    assert report.committed_primary_accuracy == 1.0
    assert report.intent_set_exact == 1.0  # several intents present at once are all recovered
    clarify = sum(c.action == "clarify" for c in ROUTER)
    assert report.action_accuracy == pytest.approx(1 - clarify / len(ROUTER))
    # Presence questions are not renormalized into one whole: each has its own two-way distribution.
    assert len({id(r) for r in report.presence.values()}) == 6


def test_below_the_threshold_the_router_clarifies_instead_of_committing() -> None:
    report = run_router_eval(gold_scorer(0.6))
    assert report.committed == 0 and report.clarified == len(ROUTER)
    assert report.committed_primary_accuracy is None
    assert report.intent_set_exact < 1.0
    lower = run_router_eval(gold_scorer(0.6), threshold=0.6)
    assert lower.committed == len(ROUTER)
    assert report.primary.accuracy == 1.0 and report.primary.coverage == 0.0


def test_the_fake_runs_every_contract_decision_offline_and_the_cli_prints_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for decision in CONTRACT_DECISIONS:
        report = run_threshold_eval(FakeScorer(), decision)
        assert report.backend == "fake" and report.cases > 0 and report.availability_failures == 0
    contract_cli.main(["--split", "dev", "--permutations"])
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"taste_fit", "router", "permutations"}
    assert len(out["permutations"]) == len(CONTRACT_DECISIONS)
    with pytest.raises(SystemExit):
        contract_cli.main(["--backend", "jev"])
    with pytest.raises(SystemExit):
        contract_cli.main(["--backend", "logprob"])
