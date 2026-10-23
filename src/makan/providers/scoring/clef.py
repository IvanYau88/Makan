"""Native Clef evidence, deliberately unwired from product requests.

The frozen dual-question proposal supplies both kinds of evidence in one HTTP request.
No acceptance threshold, public assessment, fallback, cache or admission policy lives here.
Runtime code reads the frozen contract data, never the historical cases or eval helpers.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from importlib import resources
from typing import Any

from makan.config import clef_selector
from makan.providers.base import Usage
from makan.providers.scoring.base import (
    SUM_TOLERANCE,
    BaseScorer,
    Distribution,
    ErrorKind,
    Evidence,
    OrdinalResult,
    Question,
    ScoreResult,
    Status,
)
from makan.providers.scoring.transport import (
    ClefError,
    ClefReason,
    CloudflareMetadata,
    CloudflareTransport,
    _strict_json,
)


@dataclass(frozen=True)
class ProfileCaseResult:
    """One call's choice and separate ordinal evidence; usage is counted on choice only."""

    choice: ScoreResult
    ordinal: OrdinalResult | None = None
    metadata: CloudflareMetadata = field(default_factory=CloudflareMetadata)


def _object(value: Any, keys: tuple[str, ...] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or (keys is not None and set(value) != set(keys)):
        raise ValueError("unexpected object")
    return value


def _number(value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
        raise ValueError("invalid number")
    return float(value)


def _probabilities(value: Any, ids: tuple[str, ...], tolerance: float) -> tuple[float, ...]:
    raw = _object(value, ids)
    values = tuple(_number(raw[key], 0, 1) for key in ids)
    total = math.fsum(values)
    if total == 0 or abs(total - 1) > tolerance:
        raise ValueError("invalid probability sum")
    return tuple(p / total for p in values)


def _usage(result: dict[str, Any]) -> Usage:
    usage = _object(result["usage"])
    values = [usage[key] for key in ("input_tokens", "output_tokens")]
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values):
        raise ValueError("invalid usage")
    return Usage(*values)


def _refused(value: dict[str, Any]) -> bool:
    return (
        bool(value.get("refusal")) or value.get("refused") is True or value.get("type") == "refusal"
    )


class ClefScorer(BaseScorer):
    name = "clef"

    def __init__(self, transport: CloudflareTransport, model: str) -> None:
        super().__init__(model)
        self._selector = clef_selector(model)
        if transport.selector != self._selector:
            raise ValueError("Clef transport model mismatch")
        self._transport = transport
        root = resources.files("makan").joinpath("evals", "sets", "clef")
        self._contract = _object(_strict_json(root.joinpath("contract.json").read_text("utf-8")))
        self._state_schema = _object(
            _strict_json(root.joinpath("state.schema.json").read_text("utf-8"))
        )

    def close(self) -> None:
        self._transport.close()

    def _failure(self, reason: ClefReason, usage: Usage | None = None) -> ScoreResult:
        kind = {
            ClefReason.RATE_LIMITED: ErrorKind.BUSY,
            ClefReason.TIMEOUT: ErrorKind.TIMEOUT,
            ClefReason.MALFORMED_RESPONSE: ErrorKind.MALFORMED,
            ClefReason.REFUSED: ErrorKind.REFUSED,
        }.get(reason, ErrorKind.TRANSPORT)
        return self.failure(kind, reason.value, usage=usage)

    def _score(self, question: Question) -> ScoreResult:
        """Choice-only adapter for offline Scorer tooling, with no ordinal inference call."""
        body = {
            "model": self._selector,
            "state": question.state,
            "questions": {
                "q": {
                    "type": "choice",
                    "instructions": question.instructions,
                    "criteria": {o.id: o.description for o in question.options},
                }
            },
        }
        return self._request(body, "q", question.ids).choice

    def score_profile_case(self, state: Mapping[str, Any]) -> ProfileCaseResult:
        """Validate one soft case and return both answers from exactly one request.

        Invalid or oversize inputs fail closed, without sending partially filtered data.
        The owner-aware state builder and eligibility policy are a later integration slice.
        """
        started = time.perf_counter()
        try:
            canonical = self._canonical_state(state)
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
            return ProfileCaseResult(
                replace(
                    self._failure(ClefReason.INVALID_INPUT),
                    latency_s=time.perf_counter() - started,
                )
            )
        questions = {
            key: {field: question[field] for field in ("type", "instructions", "criteria")}
            for key, question in self._contract["questions"].items()
        }
        body = {"model": self._selector, "state": canonical, "questions": questions}
        ids = tuple(questions["taste_fit"]["criteria"])
        result = self._request(body, "taste_fit", ids, ordinal=True)
        return replace(
            result, choice=replace(result.choice, latency_s=time.perf_counter() - started)
        )

    def _canonical_state(self, state: Mapping[str, Any]) -> str:
        # Read allowlists from the frozen schema instead of inventing a second input contract.
        data = _object(dict(state), ("profile", "candidate"))
        properties = self._state_schema["properties"]
        for key in ("profile", "candidate"):
            schema = properties[key]
            item = _object(data[key], tuple(schema["required"]))
            for name, spec in schema["properties"].items():
                value = item[name]
                if "const" in spec:
                    if value != spec["const"]:
                        raise ValueError("invalid source")
                elif spec["type"] == "array":
                    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                        raise ValueError("invalid array")
                    if len(value) != len(set(value)):
                        raise ValueError("duplicate item")
                    allowed = spec["items"].get("enum")
                    pattern = spec["items"].get("pattern")
                    if any(
                        (allowed is not None and v not in allowed)
                        or (pattern is not None and re.fullmatch(pattern, v) is None)
                        for v in value
                    ):
                        raise ValueError("invalid item")
                elif not isinstance(value, str) or not (
                    spec["minLength"] <= len(value) <= spec["maxLength"]
                ):
                    raise ValueError("invalid source release")
        if not data["profile"]["likes"] and not data["profile"]["dislikes"]:
            raise ValueError("no taste profile")
        return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def _request(
        self,
        body: dict[str, Any],
        choice_id: str,
        ids: tuple[str, ...],
        *,
        ordinal: bool = False,
    ) -> ProfileCaseResult:
        metadata = CloudflareMetadata()
        usage = Usage()
        try:
            response = self._transport.post(body)
            metadata = response.metadata
            data = response.data
            if _refused(data):
                raise ClefError(ClefReason.REFUSED)
            if data.get("success") is not True or data.get("errors"):
                raise ClefError(ClefReason.SERVICE_UNAVAILABLE)
            result = _object(data["result"])
            if _refused(result):
                raise ClefError(ClefReason.REFUSED)
            if result.get("model") != self._selector:
                raise ClefError(ClefReason.CONFIGURATION)
            usage = _usage(result)
            expected = (choice_id, "fit_score") if ordinal else (choice_id,)
            answers = _object(result["answers"], expected)
            if any(_refused(_object(answer)) for answer in answers.values()):
                raise ClefError(ClefReason.REFUSED)
            choice = _object(answers[choice_id])
            if choice["type"] != "choice":
                raise ValueError("wrong answer type")
            probabilities = _probabilities(choice["probabilities"], ids, SUM_TOLERANCE)
            distribution = Distribution.of(dict(zip(ids, probabilities, strict=True)), ids)
            _number(choice["confidence"], 0, 1)  # validated native metadata, never evidence
            selected = choice["choice"]
            if selected not in ids or dict(distribution.probabilities)[selected] != max(
                probabilities
            ):
                raise ValueError("choice disagrees with probabilities")
            ordered = self._ordinal(answers["fit_score"]) if ordinal else None
            return ProfileCaseResult(
                ScoreResult(
                    Status.OK,
                    self.name,
                    self.model,
                    choice=distribution.top,
                    evidence=Evidence.CLEF_PROBABILITIES,
                    distribution=distribution,
                    usage=usage,
                ),
                ordered,
                metadata,
            )
        except ClefError as exc:
            # A transport failure may still carry bounded usage headers, never its body.
            if exc.metadata != CloudflareMetadata():
                metadata = exc.metadata
            return ProfileCaseResult(self._failure(exc.reason, usage), metadata=metadata)
        except (KeyError, TypeError, ValueError, OverflowError):
            return ProfileCaseResult(
                self._failure(ClefReason.MALFORMED_RESPONSE, usage), metadata=metadata
            )

    def _ordinal(self, value: Any) -> OrdinalResult:
        answer = _object(value)
        if answer["type"] != "score":
            raise ValueError("wrong ordinal type")
        contract = self._contract
        criteria = contract["questions"]["fit_score"]["criteria"]
        ids = tuple(str(i) for i in range(len(criteria)))
        if answer["legend"] != dict(zip(ids, criteria, strict=True)):
            raise ValueError("wrong ordinal legend")
        probabilities = _probabilities(
            answer["probabilities"], ids, contract["validation"]["probability_sum_tolerance"]
        )
        expectation = math.fsum(i * p for i, p in enumerate(probabilities))
        wire_score = _number(answer["score"], 0, len(criteria) - 1)
        if abs(wire_score - expectation) > contract["validation"]["wire_score_tolerance"]:
            raise ValueError("wire score disagrees with probabilities")
        _number(answer["confidence"], 0, 1)
        return OrdinalResult(probabilities, expectation)
