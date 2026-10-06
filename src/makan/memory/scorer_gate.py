"""A retrieval gate that asks a fixed-answer scorer whether a turn needs taste memory.

It decides about soft memory only: `recall_for_turn` recalls stored constraints on every turn
whatever a gate says. It also fails toward looking up. Skipping is allowed only when the rule
gate already skips or the scorer gives a complete distribution that clearly favors skipping,
and an unsure, degraded, unsupported, or failed answer means a lookup.
"""

from __future__ import annotations

from makan.decisions import RETRIEVAL_GATE
from makan.memory.gate import GateDecision, RuleGate
from makan.providers.scoring import Scorer, Status

# The margin by which "skip" must lead "personalize". Skipping is the costly mistake, so it is
# set high, and it is a provisional setting to tune from live evals per backend.
MIN_SKIP_MARGIN = 0.8


class ScorerGate:
    def __init__(
        self,
        scorer: Scorer,
        *,
        rule: RuleGate | None = None,
        min_skip_margin: float = MIN_SKIP_MARGIN,
    ) -> None:
        self._scorer = scorer
        self._rule = rule or RuleGate()
        self._min_skip_margin = min_skip_margin

    def decide(self, message: str) -> GateDecision:
        ruled = self._rule.decide(message)
        if not ruled.lookup:
            return ruled  # the rule is free and certain, so the scorer is never asked
        result = self._scorer.score(RETRIEVAL_GATE.question(message))
        if result.status is not Status.OK:
            return GateDecision(True, f"scorer {result.status.value}: looking up")
        if result.confident_choice(self._min_skip_margin) == "skip":
            return GateDecision(False, f"scorer skip, margin {result.margin:.2f}")
        return GateDecision(True, "scorer personalize or unsure")
