"""Deterministic scorers for tests, evals, and offline runs. They never touch the network."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import replace

from makan.providers.scoring.base import (
    BaseScorer,
    Distribution,
    Evidence,
    Question,
    ScoreResult,
    Status,
)

Policy = Callable[[Question], ScoreResult]

_WORD = re.compile(r"\w+")


def lexical_policy(question: Question) -> ScoreResult:
    """A full distribution from word overlap between the text and each option.

    Each option starts at weight 1 and gains one per word it shares with the text, so the same
    text always gives the same distribution, and a text that matches nothing is a flat one.
    It is a stand-in with the right shape, not a model of meaning.
    """
    words = set(_WORD.findall(question.state.casefold()))
    weights = {
        o.id: 1
        + len(words & set(_WORD.findall(f"{o.id.replace('_', ' ')} {o.description}".casefold())))
        for o in question.options
    }
    total = sum(weights.values())
    distribution = Distribution.of({k: v / total for k, v in weights.items()}, question.ids)
    return ScoreResult(
        Status.OK,
        "fake",
        "",
        choice=distribution.top,
        evidence=Evidence.FAKE,
        distribution=distribution,
    )


class FakeScorer(BaseScorer):
    """Answers every question with a policy and records the questions it was asked.

    The default policy is `lexical_policy`. `scripted` replays fixed results instead.
    """

    name = "fake"

    def __init__(self, policy: Policy = lexical_policy, model: str = "") -> None:
        super().__init__(model)
        self._policy = policy
        self.questions: list[Question] = []

    @classmethod
    def scripted(cls, results: Iterable[ScoreResult]) -> FakeScorer:
        """Replay results in order, one per question, and fail the test when they run out."""
        queue = list(results)

        def next_result(question: Question) -> ScoreResult:
            if not queue:
                raise AssertionError("FakeScorer script exhausted")
            return queue.pop(0)

        return cls(next_result)

    def _score(self, question: Question) -> ScoreResult:
        self.questions.append(question)
        return replace(self._policy(question), backend=self.name, model=self.model)


def oracle(answers: dict[str, str]) -> Policy:
    """A policy that answers each decision with a fixed option id and full confidence.

    `answers` maps a decision name to an option id, and a decision it lacks fails the test.
    """

    def policy(question: Question) -> ScoreResult:
        choice = answers[question.decision]
        others = [i for i in question.ids if i != choice]
        probabilities = {i: 0.0 for i in others} | {choice: 1.0}
        distribution = Distribution.of(probabilities, question.ids)
        return ScoreResult(
            Status.OK, "fake", "", choice=choice, evidence=Evidence.FAKE, distribution=distribution
        )

    return policy
