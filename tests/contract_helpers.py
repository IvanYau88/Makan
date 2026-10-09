"""Scripted scorers and checks shared by the contract eval tests. Nothing here touches a network."""

import math
import re
from collections.abc import Sequence

from makan.decisions import CONTRACT_DECISIONS
from makan.evals.contract_cases import contract_eval_cases
from makan.providers.scoring import (
    BaseScorer,
    Distribution,
    Evidence,
    FakeScorer,
    Question,
    ScoreResult,
    Status,
)

STANDARD = "ABCDEFGHIJ"

# Text that must never reach a scorer: a hard constraint word, or a number with a unit or currency.
HARD_TEXT = re.compile(
    r"allerg|anaphyla|peanut|vegan|vegetarian|halal|kosher|gluten|celiac|coeliac|lactose"
    r"|pork[- ]free|no pork|budget|\$\s?\d|\bRM\s?\d|\d\s?(?:usd|dollars|ringgit)"
    r"|\d\s?(?:min|mins|minutes|hours?|km|kilomet\w+|miles?|mi|ft|feet|m|meters?|metres?)\b",
    re.IGNORECASE,
)


def hard_leaks(text: str, terms: Sequence[str] = ()) -> list[str]:
    """Everything in `text` that is a given hard term or looks like a hard constraint."""
    found = [t for t in terms if t.casefold() in text.casefold()]
    return [*found, *(m.group(0) for m in HARD_TEXT.finditer(text))]


def gold_labels() -> dict[tuple[str, str], str]:
    """The gold label of every contract case, by decision and exact scorer text."""
    return {
        (decision, case.text): case.label
        for decision in CONTRACT_DECISIONS
        for case in contract_eval_cases(decision)
    }


def distribution_for(question: Question, label: str, p: float) -> ScoreResult:
    others = [i for i in question.ids if i != label]
    probabilities = {i: (1 - p) / len(others) for i in others} | {label: p}
    distribution = Distribution.of(probabilities, question.ids)
    return ScoreResult(
        Status.OK,
        "fake",
        "",
        choice=distribution.top,
        evidence=Evidence.FAKE,
        distribution=distribution,
    )


def gold_scorer(p: float = 0.9) -> FakeScorer:
    """Puts probability `p` on each case's gold label and splits the rest evenly."""
    gold = gold_labels()
    return FakeScorer(lambda q: distribution_for(q, gold[(q.decision, q.state)], p))


class BiasedScorer(BaseScorer):
    """Knows each case's gold label but is pulled by where and under which letter options appear.

    `position_bias` favors earlier positions and `letter_bias` favors the letter A, both on top of
    a semantic preference for the gold label. With both at zero it ignores layout entirely.
    """

    name = "biased"

    def __init__(
        self, *, sharpness: float = 1.0, position_bias: float = 0.0, letter_bias: float = 0.0
    ):
        super().__init__("")
        self._gold = gold_labels()
        self._sharpness = sharpness
        self._position_bias = position_bias
        self._letter_bias = letter_bias

    def _score(self, question: Question) -> ScoreResult:
        return self.score_lettered(question, STANDARD[: len(question.options)])

    def score_lettered(self, question: Question, letters: Sequence[str]) -> ScoreResult:
        label = self._gold[(question.decision, question.state)]
        n = len(question.options)
        weights = {}
        for position, (option, letter) in enumerate(zip(question.options, letters, strict=True)):
            weight = 1.0 + (self._sharpness if option.id == label else 0.0)
            weight *= math.exp(self._position_bias * (n - 1 - position))
            weight *= math.exp(self._letter_bias) if letter == "A" else 1.0
            weights[option.id] = weight
        total = sum(weights.values())
        distribution = Distribution.of({k: v / total for k, v in weights.items()}, question.ids)
        return ScoreResult(
            Status.OK,
            self.name,
            "",
            choice=distribution.top,
            evidence=Evidence.FAKE,
            distribution=distribution,
        )
