"""Order and letter permutations: the harness catches position bias and passes a steady scorer."""

import itertools
import math

import pytest

from makan.decisions import CONTRACT_DECISIONS, PRIMARY_INTENT, TASTE_FIT
from makan.evals.permutation import (
    LETTERS,
    Arrangement,
    arranged,
    arrangements,
    ask,
    run_permutation_eval,
)
from makan.providers.scoring import (
    Distribution,
    ErrorKind,
    FakeScorer,
    Question,
    Scorer,
    ScoreResult,
    Status,
)
from tests.contract_helpers import BiasedScorer, distribution_for, gold_scorer


def identity(n: int) -> Arrangement:
    return Arrangement(tuple(range(n)), tuple(LETTERS[:n]))


# The arrangements


def test_small_questions_get_every_order_and_every_letter_assignment() -> None:
    binary = arrangements(2)
    assert len(binary) == 4 and len(set(binary)) == 4 and binary[0] == identity(2)
    four = arrangements(4)
    assert len(four) == math.factorial(4) ** 2 == len(set(four)) and four[0] == identity(4)
    assert {a.order for a in four} == set(itertools.permutations(range(4)))
    assert {a.letters for a in four} == set(itertools.permutations("ABCD"))


def test_the_seven_option_question_gets_reversed_rotated_and_sampled_arrangements() -> None:
    plan = arrangements(7)
    assert plan == arrangements(7)  # deterministic
    assert plan != arrangements(7, seed=1)
    assert plan[0] == identity(7) and len(set(plan)) == len(plan)
    orders = {a.order for a in plan}
    standard = tuple("ABCDEFG")
    reverse = tuple(reversed(range(7)))
    assert reverse in orders
    assert {tuple(list(range(7))[k:] + list(range(7))[:k]) for k in range(7)} <= orders
    assert Arrangement(tuple(range(7)), tuple(reversed(standard))) in plan  # letters alone
    assert len(plan) > 14 and len(plan) < 60
    assert all(
        sorted(a.order) == list(range(7)) and sorted(a.letters) == sorted(standard) for a in plan
    )
    assert len(arrangements(7, sample=0)) == 1 + 1 + 6 + 7  # identity, reverse, rotations, letters
    with pytest.raises(ValueError, match="at least two"):
        arrangements(1)


def test_an_arranged_question_keeps_its_options_and_changes_only_their_order() -> None:
    q = PRIMARY_INTENT.question("Somewhere cozy")
    moved = arranged(q, Arrangement((6, 5, 4, 3, 2, 1, 0), tuple("ABCDEFG")))
    assert moved.ids == tuple(reversed(q.ids)) and moved.state == q.state
    assert {o.id: o.description for o in moved.options} == {o.id: o.description for o in q.options}


# What the harness measures


@pytest.mark.parametrize("decision", sorted(CONTRACT_DECISIONS))
def test_an_order_invariant_scorer_has_no_flips_on_any_decision(decision: str) -> None:
    for scorer in (FakeScorer(), gold_scorer(0.9), BiasedScorer()):
        report = run_permutation_eval(scorer, decision)
        assert (report.argmax_flips, report.threshold_flips) == (0, 0)
        assert report.argmax_flip_rate == 0.0 and report.threshold_flip_rate == 0.0
        assert report.max_probability_change < 1e-9 and report.max_divergence < 1e-9
        assert report.cases_unmeasured == 0 and report.unavailable == 0
        assert report.worst_cases == ()


def test_the_report_counts_the_arrangements_for_each_question_size() -> None:
    assert run_permutation_eval(BiasedScorer(), "taste_fit").arrangements_per_case == 576
    assert run_permutation_eval(BiasedScorer(), "intent_presence.mood").arrangements_per_case == 4
    seven = run_permutation_eval(BiasedScorer(), "primary_intent")
    assert seven.arrangements_per_case == len(arrangements(7))
    assert seven.evaluations == seven.cases * (seven.arrangements_per_case - 1)
    assert seven.letters_exercised is True


@pytest.mark.parametrize("decision", ["primary_intent", "taste_fit", "intent_presence.mood"])
def test_a_scorer_that_favors_early_positions_is_caught(decision: str) -> None:
    report = run_permutation_eval(BiasedScorer(sharpness=1.0, position_bias=1.0), decision)
    assert report.argmax_flips > 0 and report.cases_with_argmax_flip > 0
    assert report.threshold_flips > 0 and report.cases_with_threshold_flip > 0
    assert report.max_probability_change > 0.1 and report.max_divergence > 0.05
    assert report.worst_cases and len(report.worst_cases) <= 5


def test_a_scorer_that_favors_the_letter_a_is_caught_only_when_letters_are_exercised() -> None:
    biased = BiasedScorer(sharpness=1.0, letter_bias=3.0)
    lettered = run_permutation_eval(biased, "primary_intent")
    assert lettered.letters_exercised and lettered.argmax_flips > 0

    class OrderOnly:
        """The same scorer behind a plain `score`, so the letters cannot be chosen."""

        name, model = "order-only", ""

        def score(self, question: Question) -> ScoreResult:
            return biased.score(question)

    plain = run_permutation_eval(OrderOnly(), "primary_intent")
    assert not plain.letters_exercised
    assert plain.arrangements_per_case < lettered.arrangements_per_case
    # Letters follow position here, so the A bias still shows as an order effect.
    assert plain.argmax_flips > 0


def test_position_bias_is_independent_of_letters() -> None:
    # Reorders options but labels them in a fixed way: an order effect that letters cannot explain.
    report = run_permutation_eval(BiasedScorer(position_bias=1.0), "primary_intent")
    no_order = run_permutation_eval(BiasedScorer(letter_bias=3.0), "primary_intent")
    assert report.argmax_flips > 0 and no_order.argmax_flips > 0
    only_letters = Arrangement(tuple(range(7)), tuple("GFEDCBA"))
    question = PRIMARY_INTENT.question("Somewhere cozy")
    positional = BiasedScorer(position_bias=1.0)
    assert (
        ask(positional, question, identity(7)).distribution
        == ask(positional, question, only_letters).distribution
    )  # position bias does not care which letter is shown
    lettered = BiasedScorer(letter_bias=3.0)
    assert (
        ask(lettered, question, identity(7)).distribution
        != ask(lettered, question, only_letters).distribution
    )


def test_results_are_compared_by_semantic_id_not_by_position() -> None:
    # A scorer that always returns the same answer per id shows no change however it is laid out.
    fixed = {"strong": 0.4, "partial": 0.3, "poor": 0.2, "unknown": 0.1}
    scorer = FakeScorer(
        lambda q: ScoreResult(Status.OK, "fake", "", choice="strong", distribution=_dist(q, fixed))
    )
    report = run_permutation_eval(scorer, "taste_fit", split="dev")
    assert report.argmax_flips == 0 and report.max_probability_change == 0.0


def _dist(question: Question, probabilities: dict[str, float]) -> Distribution:
    return Distribution.of(probabilities, question.ids)


def test_a_tie_broken_by_option_order_is_not_counted_as_a_flip_and_is_never_accepted() -> None:
    flat = FakeScorer(lambda q: _flat(q))
    report = run_permutation_eval(flat, "primary_intent", threshold=0.1)
    assert report.argmax_flips == 0 and report.threshold_flips == 0
    assert report.max_probability_change == 0.0


def _flat(question: Question) -> ScoreResult:
    n = len(question.ids)
    return ScoreResult(
        Status.OK,
        "fake",
        "",
        choice=question.ids[0],  # the order-dependent tie break
        distribution=_dist(question, {i: 1 / n for i in question.ids}),
    )


def test_unavailable_results_are_counted_and_an_unavailable_reference_skips_the_case() -> None:
    calls = {"n": 0}

    def sometimes(q: Question) -> ScoreResult:
        calls["n"] += 1
        if calls["n"] % 2 == 0:
            return ScoreResult(Status.ERROR, "fake", "", error=ErrorKind.BUSY)
        return distribution_for(q, q.ids[0], 0.9)

    report = run_permutation_eval(FakeScorer(sometimes), "intent_presence.mood", split="dev")
    assert report.unavailable > 0 and report.cases_unmeasured >= 0
    down = FakeScorer(lambda q: ScoreResult(Status.ERROR, "fake", "", error=ErrorKind.BUSY))
    dead = run_permutation_eval(down, "intent_presence.mood", split="dev")
    assert dead.cases_unmeasured == dead.cases and dead.argmax_flip_rate is None
    assert dead.evaluations == 0


def test_a_degraded_scorer_can_still_flip_by_its_choice_but_is_never_accepted() -> None:
    def degraded(q: Question) -> ScoreResult:
        return ScoreResult(Status.DEGRADED, "fake", "", choice=q.ids[0])  # always the first shown

    report = run_permutation_eval(FakeScorer(degraded), "taste_fit", split="dev")
    assert report.argmax_flips > 0  # the choice moved with the layout
    assert report.threshold_flips == 0  # and nothing was ever acceptable either way


def test_invalid_input_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown contract decision"):
        run_permutation_eval(FakeScorer(), "budget_band")
    with pytest.raises(ValueError, match="no cases"):
        run_permutation_eval(FakeScorer(), "taste_fit", cases=[])
    with pytest.raises(ValueError, match="threshold"):
        run_permutation_eval(FakeScorer(), "taste_fit", threshold=0)


def test_the_permutation_run_makes_no_call_outside_the_given_scorer() -> None:
    scorer: Scorer = FakeScorer()
    run_permutation_eval(scorer, "intent_presence.discovery", split="dev")
    assert isinstance(scorer, FakeScorer) and scorer.questions
    assert all(q.decision == "intent_presence.discovery" for q in scorer.questions)
    assert TASTE_FIT.name == "taste_fit"
