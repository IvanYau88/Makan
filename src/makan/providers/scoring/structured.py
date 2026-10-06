"""The structured-output fallback: a valid fixed choice with a rough, labelled self-rating.

The model picks an option id under a JSON schema and rates its own confidence. That rating is
the model's word, not a probability, so the result has no distribution, runner-up, or margin and
is always degraded. A caller that needs a margin must treat it as not sure.
"""

from __future__ import annotations

import json
import math
from typing import Any

from makan.providers.base import Usage
from makan.providers.scoring.base import (
    BaseScorer,
    ErrorKind,
    Evidence,
    Question,
    ScoreResult,
    Status,
)
from makan.providers.scoring.transport import JsonTransport, usage_of

URL = "https://openrouter.ai/api/v1/chat/completions"

_SYSTEM = (
    "{instructions}\n"
    "Answer with a JSON object: choice is the id of the best option, and confidence is a number "
    "from 0 to 1 for how sure you are."
)


class StructuredScorer(BaseScorer):
    name = "structured"

    def __init__(self, transport: JsonTransport, model: str, *, url: str = URL) -> None:
        super().__init__(model)
        self._transport = transport
        self._url = url

    def _score(self, question: Question) -> ScoreResult:
        menu = "\n".join(f"{o.id}: {o.description}" for o in question.options)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM.format(instructions=question.instructions)},
                {"role": "user", "content": f"Text:\n{question.state}\n\nOptions:\n{menu}"},
            ],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "choice",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "choice": {"type": "string", "enum": list(question.ids)},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": ["choice", "confidence"],
                        "additionalProperties": False,
                    },
                },
            },
            "provider": {"require_parameters": True},
        }
        data = self._transport.post(self._url, body)
        prompt, completion, cost = usage_of(data)
        usage = Usage(prompt, completion)
        try:
            choice_message = data["choices"][0]
            message = choice_message["message"]
        except (KeyError, IndexError, TypeError):
            return self.failure(
                ErrorKind.MALFORMED, "no message in the response", usage=usage, cost=cost
            )
        if message.get("refusal"):
            return self.failure(
                ErrorKind.REFUSED, "the model refused to answer", usage=usage, cost=cost
            )
        if choice_message.get("finish_reason") == "length":
            return self.failure(
                ErrorKind.MALFORMED, "the answer was truncated", usage=usage, cost=cost
            )
        try:
            answer = json.loads(message.get("content") or "")
        except (TypeError, ValueError):
            return self.failure(
                ErrorKind.MALFORMED, "the answer was not JSON", usage=usage, cost=cost
            )
        choice = answer.get("choice") if isinstance(answer, dict) else None
        confidence = answer.get("confidence") if isinstance(answer, dict) else None
        if (
            choice not in question.ids
            or isinstance(confidence, bool)
            or not isinstance(confidence, int | float)
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
            or set(answer) != {"choice", "confidence"}
        ):
            return self.failure(
                ErrorKind.MALFORMED,
                "the answer was not a valid option and confidence",
                usage=usage,
                cost=cost,
            )
        return ScoreResult(
            Status.DEGRADED,
            self.name,
            self.model,
            choice=choice,
            evidence=Evidence.VERBALIZED_CONFIDENCE,
            self_confidence=float(confidence),
            detail="self-reported confidence only; no distribution or margin",
            usage=usage,
            cost=cost,
        )
