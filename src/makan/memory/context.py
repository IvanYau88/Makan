"""Gated lookup for one turn: ask the gate, then recall and render what the model should see."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from makan.memory.gate import GateDecision, RetrievalGate
from makan.memory.service import Memory, RecalledFact
from makan.memory.store import Owner
from makan.models import MemoryKind

HEADER = (
    "What you remember about this user from earlier. "
    "Use it to personalize, but never state it as certain. "
    "Facts marked STALE may be out of date: ask the user to confirm them before relying on them, "
    "and when they do, call remember_fact with the same fact to record the confirmation."
)


_CONSTRAINTS: tuple[MemoryKind, ...] = ("constraint",)


@dataclass(frozen=True)
class TurnMemory:
    decision: GateDecision
    facts: tuple[RecalledFact, ...] = ()  # only constraints when the gate skipped the lookup
    text: str | None = None  # a block for the system prompt, or None when there is nothing to add

    def trace_summary(self) -> dict[str, object]:
        """What a trace may hold: the gate's decision and a count, never the stored facts."""
        return {
            "lookup": self.decision.lookup,
            "reason": self.decision.reason,
            "fact_count": len(self.facts),
        }


def recall_for_turn(memory: Memory, gate: RetrievalGate, owner: Owner, message: str) -> TurnMemory:
    """Recall for one turn. The gate decides about soft memory, never about constraints.

    Stored constraints such as allergies are recalled every turn, so no gate, rule or model,
    can hide one. The gate only decides whether the rest of memory is looked up.
    """
    decision = gate.decide(message)
    facts = tuple(memory.recall(owner, kinds=None if decision.lookup else _CONSTRAINTS))
    if not facts:
        return TurnMemory(decision)
    now = memory.now()
    lines = "\n".join(describe(r, now) for r in facts)
    return TurnMemory(decision, facts, f"{HEADER}\n{lines}")


def describe(recalled: RecalledFact, now: datetime) -> str:
    fact = recalled.fact
    days = max((now - fact.last_confirmed_at).days, 0)
    flag = {"expired": "STALE (expired)", "low_confidence": "STALE"}.get(recalled.stale or "", "")
    content = json.dumps(fact.content, ensure_ascii=False, separators=(",", ":"))
    return (
        f"- {flag + ' ' if flag else ''}{fact.kind} {content} "
        f"(confidence {recalled.confidence:.2f}, {fact.source}, last confirmed {days} days ago)"
    )
