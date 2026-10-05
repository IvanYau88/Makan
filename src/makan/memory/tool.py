"""The `remember_fact` tool: the model's way to record what the user says about their taste.

Reading memory is not a tool. The retrieval gate decides per turn whether to look up
(`makan.memory.context.recall_for_turn`), and the result goes into the system prompt.
"""

from __future__ import annotations

import json
from typing import Any, cast

from makan.memory.service import Memory
from makan.memory.store import Owner
from makan.models import MEMORY_KINDS, MemoryKind
from makan.tools import Tool

NAME = "remember_fact"

DESCRIPTION = (
    "Remember something the user told you about their food taste, a hard constraint, or a place. "
    "Call it when the user states a preference, such as liking or avoiding a cuisine, "
    "a dietary need or allergy, a budget limit, or how they rated a place. "
    "Do not call it for a guess or for something only you suggested. "
    "Content by kind: "
    'cuisine_like and cuisine_dislike take {"cuisine": "thai"}; '
    'constraint takes {"key": "diet", "value": "vegetarian"} where the key names one slot '
    "(diet, budget_max, allergy_peanut) and a new value for a key replaces the old one; "
    'place_rating takes {"place_id": "...", "rating": 4} with an optional "name", rating 1 to 5. '
    "Saying a fact again confirms the one already remembered, "
    "so call it again when the user reconfirms a fact marked STALE. "
    "A contradicting fact replaces the older one."
)

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(MEMORY_KINDS), "description": "The kind of fact."},
        "content": {"type": "object", "description": "The fact, in the shape for its kind."},
    },
    "required": ["kind", "content"],
}


def remember_fact(memory: Memory, owner: Owner) -> Tool:
    def run(arguments: dict[str, Any]) -> str:
        kind = arguments["kind"]
        if kind not in MEMORY_KINDS:
            raise ValueError(f"kind must be one of {list(MEMORY_KINDS)}")
        result = memory.remember(
            owner, cast(MemoryKind, kind), arguments["content"], source="stated"
        )
        return json.dumps(
            {
                "outcome": result.outcome,
                "kind": result.fact.kind,
                "content": result.fact.content,
                "replaced": [f.content for f in result.superseded],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    return Tool(NAME, DESCRIPTION, PARAMETERS, run)
