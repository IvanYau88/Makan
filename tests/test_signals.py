"""Soft request signals, the scorer-backed retrieval gate, and the offline evals."""

import json
from uuid import uuid4

import pytest

from makan.config import Config
from makan.decisions import BUDGET_BAND, DECISIONS, RETRIEVAL_GATE
from makan.evals import RuleBaseline, compare, load_cases, run_eval
from makan.evals.__main__ import main as evals_main
from makan.evals.cases import SPLITS
from makan.memory.context import recall_for_turn
from makan.memory.gate import RuleGate
from makan.memory.scorer_gate import ScorerGate
from makan.memory.service import Memory
from makan.memory.store import InMemoryStore, Owner
from makan.places import FakePlacesProvider
from makan.providers import FakeProvider, ToolCall, Usage
from makan.providers.fake import call
from makan.providers.scoring import (
    BoundedScorer,
    Distribution,
    ErrorKind,
    Evidence,
    FakeScorer,
    Question,
    ScoreResult,
    Status,
)
from makan.signals import SoftSignals, read_signals
from makan.solo import SoloRequest, SoloResult, build_solo_graph, recommend
from tests.helpers import KLCC, place

CONFIG = Config(model="test/classifier")
RAMEN = place("Near Ramen", "ramen_restaurant", 3.1481, 101.6951)


def lean(decision_to_choice: dict[str, str], strength: float = 0.9) -> FakeScorer:
    """A fake that puts `strength` on the named option of each decision and splits the rest."""

    def policy(question: Question) -> ScoreResult:
        choice = decision_to_choice[question.decision]
        ids = question.ids
        rest = (1 - strength) / (len(ids) - 1)
        d = Distribution.of({i: strength if i == choice else rest for i in ids}, ids)
        return ScoreResult(
            Status.OK, "fake", "", choice=choice, evidence=Evidence.FAKE, distribution=d
        )

    return FakeScorer(policy)


def failing(kind: ErrorKind = ErrorKind.BUSY) -> FakeScorer:
    return FakeScorer(lambda q: ScoreResult(Status.ERROR, "fake", "", error=kind, detail="down"))


def classifier() -> FakeProvider:
    intent = {"cuisine": None, "category": None, "requirements": ["no peanuts"]}
    return FakeProvider([call(ToolCall.of("finish", answer=json.dumps(intent)))])


# Soft signals


def test_clear_answers_become_signals_and_not_stated_is_dropped() -> None:
    scorer = lean({"budget_band": "cheap", "meal_period": "not_stated"})
    signals = read_signals(scorer, "cheap eats")

    assert signals.accepted == {"budget_band": "cheap"}
    assert [q.decision for q in scorer.questions] == ["budget_band", "meal_period"]
    assert all(q.state == "cheap eats" for q in scorer.questions)
    assert signals.describe() == (
        "Read from your request (an estimate, not a requirement): budget band: cheap."
    )


def test_an_unclear_degraded_or_failed_answer_is_no_signal() -> None:
    near_tie = lean({"budget_band": "cheap", "meal_period": "dinner"}, strength=0.4)
    assert read_signals(near_tie, "x").accepted == {}

    degraded = FakeScorer(
        lambda q: ScoreResult(
            Status.DEGRADED, "structured", "", choice=q.ids[0], self_confidence=1.0
        )
    )
    assert read_signals(degraded, "x").accepted == {}
    assert read_signals(failing(), "x").accepted == {}
    assert read_signals(failing(), "x").describe() is None
    assert read_signals(None, "x") == SoftSignals()


def test_the_signals_keep_the_evidence_for_every_attribute() -> None:
    signals = read_signals(failing(ErrorKind.TIMEOUT), "x")
    assert [s.result.error for s in signals.signals] == [ErrorKind.TIMEOUT, ErrorKind.TIMEOUT]
    assert [s.value for s in signals.signals] == [None, None]


# The solo workflow


def solo(scorer: FakeScorer | None, request: str = "something cheap for dinner") -> SoloResult:
    return recommend(
        SoloRequest(*KLCC, request),
        provider=classifier(),
        places=FakePlacesProvider([RAMEN]),
        config=CONFIG,
        scorer=scorer,
    )


def test_signals_are_shown_as_estimates_and_do_not_change_the_ranking_or_warnings() -> None:
    plain = solo(None)
    scored = solo(lean({"budget_band": "cheap", "meal_period": "dinner"}))

    p, s = plain.recommendation, scored.recommendation
    assert p and s and p.pick and s.pick
    assert s.soft_signals.accepted == {"budget_band": "cheap", "meal_period": "dinner"}
    assert "an estimate, not a requirement" in s.explanation
    assert p.soft_signals.accepted == {} and "estimate" not in p.explanation
    assert (p.pick.place, p.warnings) == (s.pick.place, s.warnings)
    assert "Cannot verify requirement: no peanuts." in s.warnings  # the hard path is untouched
    assert scored.graph.ok


def test_without_a_scorer_the_graph_has_no_signals_step() -> None:
    plain = build_solo_graph(provider=classifier(), places=FakePlacesProvider([]), config=CONFIG)
    scored = build_solo_graph(
        provider=classifier(), places=FakePlacesProvider([]), config=CONFIG, scorer=FakeScorer()
    )
    assert "signals" not in {step.name for step in plain.steps}
    assert "signals" in {step.name for step in scored.steps}


def test_a_failing_scorer_leaves_the_recommendation_unchanged() -> None:
    result = solo(failing())
    rec = result.recommendation
    assert result.graph.ok and rec and rec.pick
    assert rec.soft_signals.accepted == {}
    assert rec.pick.place.id == RAMEN.id


def test_a_crashing_scorer_degrades_to_a_warning_and_never_loses_the_recommendation() -> None:
    def boom(question: Question) -> ScoreResult:
        raise RuntimeError("secret internal detail")

    result = solo(FakeScorer(boom))
    rec = result.recommendation
    assert rec and rec.pick and "Soft request signals were unavailable." in rec.warnings
    assert "secret internal detail" not in rec.explanation
    assert not result.graph.ok


def test_a_guest_session_scores_without_a_user_id() -> None:
    result = solo(lean({"budget_band": "cheap", "meal_period": "dinner"}))
    assert result.session.user_id is None


# The retrieval gate, which never hides a stored constraint


def make_memory() -> tuple[Memory, Owner]:
    memory = Memory(InMemoryStore())
    owner = Owner(user_id=uuid4())
    memory.remember(owner, "constraint", {"key": "allergy_peanut", "value": True})
    memory.remember(owner, "cuisine_like", {"cuisine": "thai"})
    return memory, owner


def test_a_skipping_gate_still_recalls_stored_constraints_but_not_taste() -> None:
    memory, owner = make_memory()
    turn = recall_for_turn(memory, RuleGate(), owner, "thanks")

    assert turn.decision.lookup is False
    assert [r.fact.kind for r in turn.facts] == ["constraint"]
    assert "allergy_peanut" in (turn.text or "")


def test_a_request_with_no_ascii_words_is_content_not_empty() -> None:
    assert RuleGate().decide("吃什么").lookup is True
    assert RuleGate().decide("   ").reason == "empty"
    memory, owner = make_memory()
    turn = recall_for_turn(memory, RuleGate(), owner, "吃什么")
    assert {r.fact.kind for r in turn.facts} == {"constraint", "cuisine_like"}


def test_the_scorer_gate_skips_only_on_a_clear_skip_and_otherwise_looks_up() -> None:
    skip = ScorerGate(lean({"retrieval_gate": "skip"}, strength=0.95))
    assert skip.decide("多谢").lookup is False
    assert "scorer skip" in skip.decide("多谢").reason

    assert ScorerGate(lean({"retrieval_gate": "skip"}, strength=0.6)).decide("多谢").lookup is True
    assert ScorerGate(lean({"retrieval_gate": "personalize"})).decide("多谢").lookup is True
    assert ScorerGate(failing()).decide("多谢").lookup is True
    assert "error" in ScorerGate(failing()).decide("多谢").reason
    unsure = FakeScorer(lambda q: ScoreResult(Status.DEGRADED, "s", "", choice="skip"))
    assert ScorerGate(unsure).decide("多谢").lookup is True


def test_the_scorer_gate_never_asks_the_scorer_when_the_rule_already_skips() -> None:
    scorer = FakeScorer()
    gate = ScorerGate(scorer)
    assert gate.decide("thanks!").lookup is False
    assert scorer.questions == []
    gate.decide("find me lunch")
    assert [q.decision for q in scorer.questions] == ["retrieval_gate"]


def test_a_scorer_skip_cannot_hide_a_constraint_in_a_solo_run() -> None:
    memory, owner = make_memory()
    assert owner.user_id is not None
    result = recommend(
        SoloRequest(*KLCC, "多谢", user_id=owner.user_id),
        provider=classifier(),
        places=FakePlacesProvider([RAMEN]),
        config=CONFIG,
        memory=memory,
        gate=ScorerGate(lean({"retrieval_gate": "skip"}, strength=0.99)),
    )
    assert result.recommendation
    assert any("Cannot verify stored constraint" in w for w in result.recommendation.warnings)


# Evals


@pytest.mark.parametrize("decision", sorted(DECISIONS))
def test_each_eval_set_is_well_formed_and_covers_what_the_report_asks_for(decision: str) -> None:
    cases = load_cases(decision)
    options = {o.id for o in DECISIONS[decision].options}

    assert {c.label for c in cases} == options  # every option is exercised
    assert {c.split for c in cases} == set(SPLITS)
    for split in SPLITS:
        assert any("near_tie" in c.tags for c in cases if c.split == split)
    tags = {t for c in cases for t in c.tags}
    assert {"near_tie", "malay", "chinese", "negation", "hostile", "out_of_domain"} <= tags
    assert any(not c.text.isascii() for c in cases)  # a non-Latin script is represented


def test_an_unknown_decision_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown decision"):
        load_cases("nope")


def test_an_oracle_scores_perfectly_and_the_report_has_every_measure() -> None:
    cases = load_cases("budget_band")
    answers = {c.text: c.label for c in cases}

    def policy(question: Question) -> ScoreResult:
        choice = answers[question.state]
        ids = question.ids
        d = Distribution.of({i: 1.0 if i == choice else 0.0 for i in ids}, ids)
        return ScoreResult(
            Status.OK, "fake", "", choice=choice, evidence=Evidence.FAKE, distribution=d
        )

    report = run_eval(FakeScorer(policy), "budget_band")

    assert (report.accuracy, report.coverage, report.accepted_error_rate) == (1.0, 1.0, 0.0)
    assert report.macro_f1 == 1.0 and report.brier == 0.0 and report.log_loss == pytest.approx(0)
    assert report.statuses == {"ok": len(cases)}
    assert report.near_tie_cases > 0 and report.near_tie_accuracy == 1.0
    assert report.near_tie_mean_margin == 1.0
    assert report.cost is None and report.cost_unreported == len(cases)
    assert set(report.per_class) == {o.id for o in BUDGET_BAND.options}


def test_failures_count_against_accuracy_and_coverage() -> None:
    report = run_eval(failing(), "meal_period")
    assert (report.accuracy, report.coverage, report.accepted_error_rate) == (0.0, 0.0, None)
    assert report.statuses == {"error": report.cases}
    assert report.brier is None and report.near_tie_mean_margin is None


def test_a_split_filters_the_cases() -> None:
    cases = load_cases("meal_period")
    held = run_eval(FakeScorer(), "meal_period", split="heldout")
    assert held.cases == sum(c.split == "heldout" for c in cases) and held.split == "heldout"
    with pytest.raises(ValueError, match="no cases"):
        run_eval(FakeScorer(), "meal_period", cases=[], split="dev")


def test_billed_cost_and_tokens_are_totalled_when_reported() -> None:
    def policy(question: Question) -> ScoreResult:
        ids = question.ids
        d = Distribution.of({i: 1 / len(ids) for i in ids}, ids)
        return ScoreResult(
            Status.OK, "fake", "", choice=ids[0], distribution=d, usage=Usage(10, 1), cost=0.5
        )

    report = run_eval(FakeScorer(policy), "retrieval_gate")
    assert report.tokens == 11 * report.cases and report.cost == pytest.approx(0.5 * report.cases)
    assert report.cost_unreported == 0


def test_the_rule_baseline_answers_only_the_retrieval_gate_and_is_never_accepted() -> None:
    report = run_eval(RuleBaseline(), "retrieval_gate")
    assert report.statuses == {"degraded": report.cases} and report.coverage == 0.0
    assert report.brier is None
    assert RuleBaseline().score(RETRIEVAL_GATE.question("谢谢")).choice == "personalize"
    assert RuleBaseline().score(RETRIEVAL_GATE.question("thanks")).choice == "skip"
    with pytest.raises(ValueError, match="only answers the retrieval gate"):
        RuleBaseline().score(BUDGET_BAND.question("x"))


def test_compare_runs_each_backend_per_decision_and_skips_a_baseline_that_cannot_answer() -> None:
    reports = compare({"fake": FakeScorer(), "rule": RuleBaseline()})
    assert sorted((r.decision, r.backend) for r in reports) == sorted(
        [(d, "fake") for d in DECISIONS] + [("retrieval_gate", "rule")]
    )


def test_a_request_cap_bounds_an_eval_run() -> None:
    report = run_eval(BoundedScorer(FakeScorer(), 5), "budget_band")
    assert report.statuses == {"ok": 5, "error": report.cases - 5}


def test_the_cli_prints_a_json_report_and_offers_only_offline_backends(
    capsys: pytest.CaptureFixture[str],
) -> None:
    evals_main(["--backend", "fake", "--decision", "meal_period", "--split", "dev"])
    (report,) = json.loads(capsys.readouterr().out)
    assert (report["decision"], report["backend"], report["split"]) == (
        "meal_period",
        "fake",
        "dev",
    )
    with pytest.raises(SystemExit):
        evals_main(["--backend", "jev"])


def test_the_decision_wording_has_no_hard_constraint_options() -> None:
    # Hard constraints are decided in plain code, so no scored decision offers them as an option.
    words = " ".join(o.id + o.description for d in DECISIONS.values() for o in d.options).lower()
    assert not {"allerg", "halal", "vegan", "vegetarian", "gluten"} & set(words.split())
