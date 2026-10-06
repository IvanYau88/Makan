"""The fixed-answer scoring interface: a question, its options, and a result that states its basis.

A scorer picks one of the given options. Only a backend that can see a probability for every option
reports a distribution, and only then are the top option, the runner-up, and the margin computed.
A backend that cannot says so with a degraded, unsupported, or error status. It never invents a
distribution, a runner-up, or a margin.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol

from makan.providers.base import ProviderBusy, ProviderError, Usage

# Probabilities from a backend must sum to one within this much.
SUM_TOLERANCE = 1e-3


class Status(StrEnum):
    OK = "ok"  # a choice with a complete distribution, or the only option there was
    DEGRADED = "degraded"  # a choice, but no distribution: no runner-up or margin to act on
    UNSUPPORTED = "unsupported"  # the backend or model cannot answer this question; no choice
    ERROR = "error"  # the request failed or the answer was unusable; no choice


class Evidence(StrEnum):
    """What the result rests on. These are not interchangeable, so a caller can tell them apart."""

    TOKEN_LOGPROBS = "token_logprobs"  # probabilities read from the model's answer-token logprobs
    SAMPLED_LABEL = "sampled_label"  # the generated label only, with no usable distribution
    VERBALIZED_CONFIDENCE = "verbalized_confidence"  # the model's own rough rating of itself
    JEV_PROBABILITIES = "jev_probabilities"  # probabilities from the hosted Jev decisions model
    DETERMINISTIC = "deterministic"  # no model: a lone option, or a plain rule
    FAKE = "fake"  # a test double


class ErrorKind(StrEnum):
    BUSY = "busy"  # rate limited or temporarily unavailable
    TIMEOUT = "timeout"
    TRANSPORT = "transport"  # any other request failure
    REFUSED = "refused"
    MALFORMED = "malformed"  # truncated, not valid, or not one of the options
    LOW_LABEL_MASS = "low_label_mass"  # the model mostly did not answer with an option label
    BUDGET = "budget"  # a request cap was reached before the call was made


@dataclass(frozen=True)
class Option:
    id: str  # stable, what callers store and compare
    description: str  # what the option means, which is what the model reads


@dataclass(frozen=True)
class Question:
    """One bounded decision. `decision` and `version` name the task and its policy for the trace."""

    decision: str
    version: str
    instructions: str
    state: str  # the text to decide about
    options: tuple[Option, ...]

    def __post_init__(self) -> None:
        if not self.options:
            raise ValueError("a question needs at least one option")
        ids = [o.id for o in self.options]
        if any(not isinstance(i, str) or not i.strip() for i in ids):
            raise ValueError("option ids must be non-empty text")
        if len(set(ids)) != len(ids):
            raise ValueError("option ids must be unique")

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(o.id for o in self.options)


@dataclass(frozen=True)
class Distribution:
    """A complete probability for every option, in the question's option order."""

    probabilities: tuple[tuple[str, float], ...]
    top: str
    runner_up: str
    margin: float  # the top probability minus the runner-up's

    @classmethod
    def of(cls, probabilities: Mapping[str, float], order: tuple[str, ...]) -> Distribution:
        """Validate and rank a full distribution. A tie goes to the earlier option and has margin 0.

        Raises ValueError unless it has exactly the question's options, finite values in [0, 1],
        and a sum within tolerance of 1, so a malformed answer is never ranked.
        """
        if set(probabilities) != set(order) or len(order) < 2:
            raise ValueError("distribution must have a probability for every option and no other")
        values = [probabilities[i] for i in order]
        if any(
            not isinstance(v, int | float) or not math.isfinite(v) or not 0 <= v <= 1
            for v in values
        ):
            raise ValueError("probabilities must be finite numbers between 0 and 1")
        if abs(math.fsum(values) - 1) > SUM_TOLERANCE:
            raise ValueError("probabilities must sum to 1")
        ranked = sorted(range(len(order)), key=lambda k: (-values[k], k))
        first, second = ranked[0], ranked[1]
        return cls(
            tuple((i, float(probabilities[i])) for i in order),
            order[first],
            order[second],
            values[first] - values[second],
        )


@dataclass(frozen=True)
class ScoreResult:
    status: Status
    backend: str
    model: str
    choice: str | None = None  # an option id, present for OK and DEGRADED
    evidence: Evidence | None = None
    distribution: Distribution | None = None  # present only when the backend gave a full one
    self_confidence: float | None = None  # a rough self-rating in [0, 1], never a probability
    label_mass: float | None = None  # the logprob mass on the option labels before normalizing
    error: ErrorKind | None = None
    detail: str = ""  # why a result is not OK, with no request text in it
    usage: Usage = field(default_factory=Usage)
    cost: float | None = None  # dollars as the backend billed it, when it said
    latency_s: float = 0.0

    @property
    def margin(self) -> float | None:
        return self.distribution.margin if self.distribution else None

    @property
    def runner_up(self) -> str | None:
        return self.distribution.runner_up if self.distribution else None

    def confident_choice(self, min_margin: float) -> str | None:
        """The choice, only when a full distribution shows it beats the runner-up by `min_margin`.

        A degraded, unsupported, or failed result is never confident, and neither is a lone
        option that was not scored. Callers that need a distribution use this and treat None as
        "not sure".
        """
        if (
            self.status is Status.OK
            and self.distribution
            and self.distribution.margin >= min_margin
        ):
            return self.choice
        return None


class Scorer(Protocol):
    name: str  # the backend, such as "logprob"
    model: str  # the configured model, or "" for a backend that has none

    def score(self, question: Question) -> ScoreResult:
        """Answer the question. Backend failures are results with an error status, not raises."""
        ...


class ScorerTimeout(ProviderError):
    """The scoring request did not finish in time."""


class BaseScorer:
    """Shared behavior: one option needs no model call, and failures become error results."""

    name = ""

    def __init__(self, model: str) -> None:
        self.model = model

    def score(self, question: Question) -> ScoreResult:
        if len(question.options) == 1:
            return ScoreResult(
                Status.OK,
                self.name,
                self.model,
                choice=question.options[0].id,
                evidence=Evidence.DETERMINISTIC,
            )
        started = time.perf_counter()
        try:
            result = self._score(question)
        except ProviderBusy as exc:
            result = self.failure(ErrorKind.BUSY, str(exc))
        except ScorerTimeout as exc:
            result = self.failure(ErrorKind.TIMEOUT, str(exc))
        except ProviderError as exc:
            result = self.failure(ErrorKind.TRANSPORT, str(exc))
        return replace(result, latency_s=time.perf_counter() - started)

    def _score(self, question: Question) -> ScoreResult:
        raise NotImplementedError

    def failure(
        self,
        kind: ErrorKind,
        detail: str,
        *,
        label_mass: float | None = None,
        usage: Usage | None = None,
        cost: float | None = None,
    ) -> ScoreResult:
        return ScoreResult(
            Status.ERROR,
            self.name,
            self.model,
            error=kind,
            label_mass=label_mass,
            detail=detail[:300],
            usage=usage or Usage(),
            cost=cost,
        )

    def unsupported(
        self, detail: str, *, usage: Usage | None = None, cost: float | None = None
    ) -> ScoreResult:
        return ScoreResult(
            Status.UNSUPPORTED,
            self.name,
            self.model,
            detail=detail[:300],
            usage=usage or Usage(),
            cost=cost,
        )
