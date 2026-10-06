"""The single-token-label logprob scorer.

Each option gets a one-letter label. The model is asked for one label, and the probabilities come
from the alternatives the endpoint reports at that first answer position. Whole-string option
likelihood is never used, because one completion exposes only one generated prefix.

A label that is missing from the reported alternatives is unknown, not zero, so a distribution is
given only when every label is covered. Anything less is a degraded or unsupported result.
"""

from __future__ import annotations

import math
import string
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

URL = "https://openrouter.ai/api/v1/chat/completions"
LABELS = string.ascii_uppercase[:10]  # A to J: top_logprobs allows 20, so every label can be listed
TOP_LOGPROBS = 20
# Below this much total probability on the labels, the model mostly did not answer in the format.
MIN_LABEL_MASS = 0.5
# Logprobs are at most 0. A hair above is rounding, anything else is a malformed answer.
_LOGPROB_SLACK = 1e-6

_SYSTEM = (
    "{instructions}\nReply with exactly one letter, the label of the best option, and nothing else."
)


class LogprobScorer(BaseScorer):
    name = "logprob"

    def __init__(
        self,
        transport: JsonTransport,
        model: str,
        *,
        min_label_mass: float = MIN_LABEL_MASS,
        url: str = URL,
    ) -> None:
        super().__init__(model)
        self._transport = transport
        self._min_label_mass = min_label_mass
        self._url = url

    def _score(self, question: Question) -> ScoreResult:
        if len(question.options) > len(LABELS):
            return self.unsupported(f"more than {len(LABELS)} options cannot all be labelled")
        labels = dict(zip(LABELS, question.ids, strict=False))
        menu = "\n".join(
            f"{label}: {o.description}" for label, o in zip(LABELS, question.options, strict=False)
        )
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM.format(instructions=question.instructions)},
                {"role": "user", "content": f"Text:\n{question.state}\n\nOptions:\n{menu}"},
            ],
            "max_tokens": 1,
            "temperature": 0,
            "logprobs": True,
            "top_logprobs": TOP_LOGPROBS,
            # Ask for the feature or fail, so a route that ignores logprobs is not silently used.
            "provider": {"require_parameters": True},
        }
        data = self._transport.post(self._url, body)
        prompt, completion, cost = usage_of(data)
        usage = Usage(prompt, completion)
        try:
            position = data["choices"][0]["logprobs"]["content"][0]
            sampled = str(position["token"]).strip()
            seen = _label_probabilities(position, labels)
        except (KeyError, IndexError, TypeError, ValueError):
            return self.unsupported(
                "the endpoint returned no usable token logprobs", usage=usage, cost=cost
            )
        missing = [label for label in labels if label not in seen]
        if missing:
            choice = labels.get(sampled)
            if choice is None:
                return self.unsupported(
                    "not every option label was among the reported alternatives",
                    usage=usage,
                    cost=cost,
                )
            return ScoreResult(
                Status.DEGRADED,
                self.name,
                self.model,
                choice=choice,
                evidence=Evidence.SAMPLED_LABEL,
                detail="not every option label was among the reported alternatives",
                usage=usage,
                cost=cost,
            )
        mass = math.fsum(seen.values())
        if mass < self._min_label_mass:
            return self.failure(
                ErrorKind.LOW_LABEL_MASS,
                f"only {mass:.2f} of the probability was on the option labels",
                label_mass=mass,
                usage=usage,
                cost=cost,
            )
        distribution = Distribution.of({labels[k]: v / mass for k, v in seen.items()}, question.ids)
        return ScoreResult(
            Status.OK,
            self.name,
            self.model,
            choice=distribution.top,
            evidence=Evidence.TOKEN_LOGPROBS,
            distribution=distribution,
            label_mass=mass,
            usage=usage,
            cost=cost,
        )


def _label_probabilities(position: dict[str, Any], labels: dict[str, str]) -> dict[str, float]:
    """Probability of each option label at the answer position, from its reported alternatives.

    Tokens match after trimming whitespace, so " A" and "A" both count as label A and their
    probabilities add. Other tokens are outside the labels. Raises ValueError on a bad logprob.
    """
    alternatives = [
        *position.get("top_logprobs", []),
        {"token": position["token"], "logprob": position["logprob"]},
    ]
    # The generated token also appears in top_logprobs, so keep the highest value per exact token.
    best: dict[str, float] = {}
    for entry in alternatives:
        token, logprob = str(entry["token"]), entry["logprob"]
        if isinstance(logprob, bool) or not isinstance(logprob, int | float):
            raise ValueError("logprob is not a number")
        if not math.isfinite(logprob) or logprob > _LOGPROB_SLACK:
            raise ValueError("logprob is not a valid log probability")
        best[token] = max(logprob, best.get(token, -math.inf))
    seen: dict[str, float] = {}
    for token, logprob in best.items():
        label = token.strip()
        if label in labels:
            seen[label] = seen.get(label, 0.0) + math.exp(logprob)
    return seen
