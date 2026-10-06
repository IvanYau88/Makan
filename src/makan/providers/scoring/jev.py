"""Hosted Jev (typesafe/jev-1.13) through OpenRouter's Decisions API. Paid, and off by default.

This is a separate surface from chat completions: a `state` and named `questions` go to the
decisions endpoint, and each Choice answer carries probabilities for every option. It is built
only when `MAKAN_SCORER_BACKEND=jev` is set, and it is never a fallback for another backend, so a
failed request on another scorer can never turn into a paid call.

The request and response shapes follow OpenRouter's public Jev tutorial. They have not been
checked against a live response, so the parser accepts only a complete, normalized distribution
and reports anything else as malformed instead of guessing.
"""

from __future__ import annotations

from typing import Any

from makan.providers.base import Usage
from makan.providers.scoring.base import (
    BaseScorer,
    Distribution,
    ErrorKind,
    Evidence,
    Question,
    ScoreResult,
    Status,
)
from makan.providers.scoring.transport import JsonTransport, usage_of

URL = "https://openrouter.ai/api/alpha/decisions"
_QUESTION_ID = "q"


class JevScorer(BaseScorer):
    name = "jev"

    def __init__(self, transport: JsonTransport, model: str, *, url: str = URL) -> None:
        super().__init__(model)
        self._transport = transport
        self._url = url

    def _score(self, question: Question) -> ScoreResult:
        body: dict[str, Any] = {
            "model": self.model,
            "state": question.state,
            "questions": {
                _QUESTION_ID: {
                    "type": "choice",
                    "instructions": question.instructions,
                    "criteria": {o.id: o.description for o in question.options},
                }
            },
        }
        data = self._transport.post(self._url, body)
        prompt, completion, cost = usage_of(data)
        usage = Usage(prompt, completion)
        try:
            answer = data["answers"][_QUESTION_ID]
            probabilities = answer["probabilities"]
            if not isinstance(probabilities, dict):
                raise TypeError("probabilities is not an object")
            distribution = Distribution.of(probabilities, question.ids)
        except (KeyError, TypeError, ValueError) as exc:
            return self.failure(
                ErrorKind.MALFORMED, f"unusable Jev answer: {exc}", usage=usage, cost=cost
            )
        return ScoreResult(
            Status.OK,
            self.name,
            self.model,
            choice=distribution.top,
            evidence=Evidence.JEV_PROBABILITIES,
            distribution=distribution,
            usage=usage,
            cost=cost,
        )
