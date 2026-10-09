"""Hard constraints stay in plain code: no scorer text holds one, and warnings need no scorer."""

import inspect
import subprocess
import sys
from datetime import date

import pytest

from makan.decisions import CONTRACT_DECISIONS, LIMIT_MARKERS
from makan.evals.contract_cases import (
    HardFacts,
    HardSpan,
    RouterCase,
    TasteFitCase,
    load_router_cases,
    load_taste_cases,
    soft_view,
)
from makan.evals.permutation import run_permutation_eval
from makan.evals.threshold import run_router_eval, run_threshold_eval
from makan.menu_warnings import MenuText, allergen_warnings
from makan.providers.scoring import ErrorKind, FakeScorer, Question, Scorer, ScoreResult, Status
from tests.contract_helpers import distribution_for, gold_scorer, hard_leaks

TASTE = load_taste_cases()
ROUTER = load_router_cases()
WORKED = next(c for c in TASTE if "worked_case" in c.tags)
PEANUT_WARNING = (
    "Peanuts appear on this restaurant's menu, which conflicts with your no-peanuts requirement; "
    "we cannot verify ingredients for your order or cross-contact. "
    "Source: restaurant menu, 2026-09-30."
)


def menu_of(case_hard: HardFacts) -> list[MenuText]:
    return [
        MenuText(m.text, m.source, date.fromisoformat(m.date) if m.date else None)
        for m in case_hard.menu_mentions
    ]


# What reaches Question.state


@pytest.mark.parametrize("case", TASTE, ids=lambda c: c.id)
def test_no_hard_constraint_text_reaches_a_taste_state(case: TasteFitCase) -> None:
    question = CONTRACT_DECISIONS["taste_fit"].question(case.state)
    assert hard_leaks(question.state, case.hard.terms()) == []


@pytest.mark.parametrize("case", ROUTER, ids=lambda c: c.id)
def test_no_hard_constraint_text_reaches_a_router_state(case: RouterCase) -> None:
    for decision in ("primary_intent", "intent_presence.mood"):
        state = CONTRACT_DECISIONS[decision].question(case.state).state
        assert hard_leaks(state, [s.text for s in case.spans]) == []
        assert all(LIMIT_MARKERS[s.kind] in state for s in case.spans)  # the limit is still known


def test_the_isolation_checks_are_not_vacuous() -> None:
    # The fixtures really carry hard text, the detector catches it, and a leaking state is flagged.
    assert WORKED.hard.allergies == ("peanuts",) and WORKED.hard.menu_mentions
    spans = [s for c in ROUTER for s in c.spans]
    assert {s.kind for s in spans} == set(LIMIT_MARKERS)
    assert all(s.text in c.message for c in ROUTER for s in c.spans)
    assert hard_leaks("Find ramen, I am allergic to peanuts", ["peanuts"]) != []
    assert hard_leaks("a cozy place within 15 minutes", []) != []
    assert hard_leaks("nothing over $12", []) != []
    assert hard_leaks(WORKED.state + "\nNo peanuts please.", WORKED.hard.terms()) != []


def test_a_stale_hard_span_cannot_silently_leave_the_hard_text_in() -> None:
    with pytest.raises(ValueError, match="exactly once"):
        soft_view("ramen nearby", (HardSpan("within 5 minutes", "distance_time"),))
    with pytest.raises(ValueError, match="exactly once"):
        soft_view("no pork, really no pork", (HardSpan("no pork", "food_restriction"),))


def test_prices_and_distances_are_display_only_and_never_in_a_state() -> None:
    assert WORKED.display == {"distance_m": 1770, "price": "about $14"}
    assert all("$" not in c.state and "mile" not in c.state for c in TASTE)


def test_no_decision_text_is_a_hard_constraint_question() -> None:
    for decision in CONTRACT_DECISIONS.values():
        wording = " ".join(
            [decision.instructions, *(o.id + " " + o.description for o in decision.options)]
        )
        assert hard_leaks(wording) == []


def test_whole_eval_runs_never_show_the_scorer_a_hard_constraint() -> None:
    scorer = gold_scorer()  # a FakeScorer, which records every question it is asked
    terms = [t for c in TASTE for t in c.hard.terms()] + [s.text for c in ROUTER for s in c.spans]
    run_threshold_eval(scorer, "taste_fit")
    run_router_eval(scorer)
    run_permutation_eval(scorer, "intent_presence.mood")
    run_permutation_eval(scorer, "taste_fit", split="dev")
    assert len(scorer.questions) > 500
    assert all(hard_leaks(q.state + q.instructions, terms) == [] for q in scorer.questions)


# The warning path


def test_the_worked_case_gets_the_agreed_warning_and_a_separate_strong_fit() -> None:
    assert WORKED.label == "strong"
    assert allergen_warnings(WORKED.hard.allergies, menu_of(WORKED.hard)) == (PEANUT_WARNING,)
    assert "peanut" not in WORKED.state.casefold()  # the fit never mentions or claims anything


def assess(case: TasteFitCase, scorer: Scorer | None) -> tuple[tuple[str, ...], str | None]:
    """Warnings first and from plain code alone, then the soft fit if the scorer cooperates."""
    warnings = allergen_warnings(case.hard.allergies, menu_of(case.hard))
    fit = None
    if scorer is not None:
        try:
            fit = scorer.score(CONTRACT_DECISIONS["taste_fit"].question(case.state)).choice
        except Exception:  # a crashing scorer must not take the warning with it
            fit = None
    return warnings, fit


def _answer(question: Question, label: str) -> ScoreResult:
    return distribution_for(question, label, 0.95)


class Crashing:
    name, model = "crash", ""

    def score(self, question: Question) -> ScoreResult:
        raise RuntimeError("scorer exploded")


@pytest.mark.parametrize(
    ("scorer", "fit"),
    [
        (None, None),
        (gold_scorer(), "strong"),
        (FakeScorer(lambda q: _answer(q, "poor")), "poor"),  # wrong about the fit
        (FakeScorer(lambda q: _answer(q, "unknown")), "unknown"),
        (FakeScorer(lambda q: ScoreResult(Status.ERROR, "fake", "", error=ErrorKind.BUSY)), None),
        (FakeScorer(lambda q: ScoreResult(Status.UNSUPPORTED, "fake", "")), None),
        (Crashing(), None),
    ],
    ids=["off", "right", "wrong", "unknown", "error", "unsupported", "crashing"],
)
def test_the_allergen_warning_is_identical_with_the_scorer_off_wrong_or_failing(
    scorer: Scorer | None, fit: str | None
) -> None:
    warnings, got = assess(WORKED, scorer)
    assert warnings == (PEANUT_WARNING,)
    assert got == fit


def test_the_warning_helper_works_with_scorer_and_provider_modules_blocked() -> None:
    script = """
import sys
from datetime import date

class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.startswith(("makan.providers", "makan.evals", "makan.decisions")):
            raise ImportError(f"blocked: {name}")

sys.meta_path.insert(0, Blocker())
from makan.menu_warnings import MenuText, allergen_warnings

menu = [MenuText("Peanut sauce", "restaurant menu", date(2026, 9, 30))]
print(allergen_warnings(["peanuts"], menu)[0])
"""
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().startswith("Peanuts appear on this restaurant's menu")
    assert "Source: restaurant menu, 2026-09-30." in done.stdout
    assert "scorer" not in inspect.signature(allergen_warnings).parameters


def test_warning_matching_and_wording() -> None:
    menu = [
        MenuText("Pad thai with crushed peanuts", "restaurant website", date(2026, 9, 1)),
        MenuText("Peanut sauce on the side", "menu PDF"),
        MenuText("Mixed nuts and cashews", "menu PDF"),
    ]
    (warning,) = allergen_warnings(["Peanuts"], menu)
    assert warning.startswith("Peanuts appear on this restaurant's menu")
    assert "no-peanuts requirement" in warning and "cross-contact" in warning
    assert "restaurant website, 2026-09-01; menu PDF, date unknown" in warning
    # A whole-word match: peanut finds peanuts and not the other way round with plain nuts.
    assert allergen_warnings(["peanut"], menu) != ()
    assert allergen_warnings(["tree nuts"], menu) == ()
    (nuts,) = allergen_warnings(["nuts"], menu)
    assert "Mixed nuts" not in nuts and "Nuts appear" in nuts
    (sesame,) = allergen_warnings(["sesame"], [MenuText("Sesame oil", "menu")])
    assert sesame.startswith("Sesame appears") and "no-sesame" in sesame


def test_no_mention_means_no_warning_and_never_a_safety_claim() -> None:
    assert allergen_warnings(["peanuts"], [MenuText("Nasi lemak with anchovies", "menu")]) == ()
    assert allergen_warnings([], [MenuText("Peanut sauce", "menu")]) == ()
    assert allergen_warnings(["peanuts"], []) == ()
    warning = allergen_warnings(["shrimp", "peanuts"], [MenuText("Peanut and shrimp", "m")])
    assert [w.split()[0] for w in warning] == ["Shrimp", "Peanuts"]  # in the order stated
    assert not any("safe" in w.casefold() for w in warning)
