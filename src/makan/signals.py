"""Soft request signals read by a scorer, kept for one run only.

A signal is an estimate of what the request leans toward, such as a cheap meal or dinner. It is
shown beside the recommendation as an estimate and does not change the ranking, filter a place,
satisfy or weaken a requirement, or get written to memory. Hard requirements still go through
the classifier's `requirements` and stay unverified warnings.
"""

from __future__ import annotations

from dataclasses import dataclass

from makan.decisions import NOT_STATED, SOFT_ATTRIBUTES, Decision
from makan.providers.scoring import Scorer, ScoreResult

# A signal needs the top option to lead the runner-up by this much. It is a provisional setting
# to tune from live evals, per backend, before any decision relies on it.
MIN_MARGIN = 0.5


@dataclass(frozen=True)
class Signal:
    attribute: str
    value: str | None  # the accepted option id, or None when the scorer was not sure
    result: ScoreResult  # the evidence, whatever the outcome


@dataclass(frozen=True)
class SoftSignals:
    signals: tuple[Signal, ...] = ()

    @property
    def accepted(self) -> dict[str, str]:
        return {s.attribute: s.value for s in self.signals if s.value is not None}

    def describe(self) -> str | None:
        """One sentence stating the accepted signals as estimates, or None when there are none."""
        accepted = self.accepted
        if not accepted:
            return None
        parts = ", ".join(
            f"{k.replace('_', ' ')}: {v.replace('_', ' ')}" for k, v in accepted.items()
        )
        return f"Read from your request (an estimate, not a requirement): {parts}."


def read_signals(
    scorer: Scorer | None,
    request: str,
    attributes: tuple[Decision, ...] = SOFT_ATTRIBUTES,
    *,
    min_margin: float = MIN_MARGIN,
) -> SoftSignals:
    """Ask the scorer each attribute question about the request, accepting only clear answers.

    Each attribute is its own request. An answer is accepted only from a complete distribution
    whose margin reaches `min_margin` and whose choice is a stated value. Anything else, including a
    degraded, unsupported, or failed result, leaves that attribute unset, so the run falls back to
    having no signal. With no scorer there are no signals.
    """
    if scorer is None:
        return SoftSignals()
    signals = []
    for attribute in attributes:
        result = scorer.score(attribute.question(request))
        choice = result.confident_choice(min_margin)
        signals.append(Signal(attribute.name, None if choice == NOT_STATED else choice, result))
    return SoftSignals(tuple(signals))
