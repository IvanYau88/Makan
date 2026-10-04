"""Gated lookup for one turn: ask the gate, then recall and render what the model should see."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from makan.memory.gate import GateDecision, RetrievalGate
from makan.memory.service import Memory, RecalledFact
from makan.memory.store import Owner

HEADER = (
    "What you remember about this user from earlier. "
    "Use it to personalize, but never state it as certain. "
    "Facts marked STALE may be out of date: ask the user to confirm them before relying on them, "
    "and when they do, call remember_fact with the same fact to record the confirmation."
)


@dataclass(frozen=True)
class TurnMemory:
    decision: GateDecision
    facts: tuple[RecalledFact, ...] = ()  # empty when the gate skipped the lookup
    text: str | None = None  # a block for the system prompt, or None when there is nothing to add


def recall_for_turn(memory: Memory, gate: RetrievalGate, owner: Owner, message: str) -> TurnMemory:
    """Look up memory only if the gate says this turn needs it."""
    decision = gate.decide(message)
    if not decision.lookup:
        return TurnMemory(decision)
    facts = tuple(memory.recall(owner))
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
