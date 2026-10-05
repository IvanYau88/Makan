"""The retrieval gate: does this turn need a memory lookup at all?

A gate is anything with a `decide` method, so a small cheap model can replace the rule
through the provider adapter without touching the callers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GateDecision:
    lookup: bool
    reason: str  # short and traceable, such as "pleasantry"


class RetrievalGate(Protocol):
    def decide(self, message: str) -> GateDecision: ...


# Words that carry nothing a memory could change. A message made only of these skips the lookup.
_PLEASANTRIES = frozenset(
    {
        *("hi", "hello", "hey", "yo", "hiya", "bye", "goodbye", "cheers"),
        *("thanks", "thank", "thx", "ty", "you", "please"),
        *("ok", "okay", "k", "cool", "great", "nice", "good", "sure", "got", "it"),
        *("yes", "yeah", "yep", "no", "nope"),
    }
)
_WORD = re.compile(r"[a-z']+")


class RuleGate:
    """Look up memory on every turn except a bare greeting, thanks, or acknowledgement.

    Skipping a lookup that mattered means ignoring an allergy, and an unneeded lookup costs a
    short query, so the rule skips only when the message has no content to personalize.
    """

    def decide(self, message: str) -> GateDecision:
        words = _WORD.findall(message.casefold())
        if not words:
            return GateDecision(False, "empty")
        if all(w in _PLEASANTRIES for w in words):
            return GateDecision(False, "pleasantry")
        return GateDecision(True, "has content")
