"""How facts age: the confidence they start with, how fast it decays, and when they are stale.

Decay is computed when a fact is read and is never stored, so reconfirming is one update.
Confidence halves every `half_life`, counted from `last_confirmed_at`.
The numbers are starting points to tune once there is real usage, not measured values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from makan.models import MEMORY_KINDS, MEMORY_SOURCES, MemoryFact, MemoryKind, MemorySource


@dataclass(frozen=True)
class DecayPolicy:
    """`half_life` of None means the fact never decays. Nothing uses that by default."""

    half_life: timedelta | None


def _default_decay() -> dict[MemoryKind, DecayPolicy]:
    # Every kind decays. Whether hard constraints such as allergies should be exempt is an
    # open question in docs/DESIGN.md, so this does not decide it.
    return {
        "cuisine_like": DecayPolicy(timedelta(days=180)),
        "cuisine_dislike": DecayPolicy(timedelta(days=180)),
        "constraint": DecayPolicy(timedelta(days=365)),
        "place_rating": DecayPolicy(timedelta(days=90)),
    }


def _default_initial() -> dict[MemorySource, float]:
    return {"stated": 0.9, "observed": 0.6}


@dataclass(frozen=True)
class MemoryPolicy:
    decay: Mapping[MemoryKind, DecayPolicy] = field(default_factory=_default_decay)
    initial_confidence: Mapping[MemorySource, float] = field(default_factory=_default_initial)
    stale_below: float = 0.5  # a fact whose decayed confidence is under this is stale

    def __post_init__(self) -> None:
        if set(self.decay) != set(MEMORY_KINDS):
            raise ValueError(f"decay must set every kind: {MEMORY_KINDS}")
        if set(self.initial_confidence) != set(MEMORY_SOURCES):
            raise ValueError(f"initial_confidence must set every source: {MEMORY_SOURCES}")
        if not all(0 <= c <= 1 for c in self.initial_confidence.values()):
            raise ValueError("initial confidence must be between 0 and 1")
        if not 0 <= self.stale_below <= 1:
            raise ValueError("stale_below must be between 0 and 1")
        if any(
            d.half_life is not None and d.half_life <= timedelta(0) for d in self.decay.values()
        ):
            raise ValueError("a half life must be positive")

    def confidence_at(self, fact: MemoryFact, now: datetime) -> float:
        """The fact's confidence now: its stored value, halved for every half life unconfirmed."""
        half_life = self.decay[fact.kind].half_life
        if half_life is None:
            return fact.confidence
        age = max(now - fact.last_confirmed_at, timedelta(0))
        return float(fact.confidence * 0.5 ** (age / half_life))
