"""Narrow local fit inputs. Guest choices never become account memory or model input."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from makan.fit import FitReason
from makan.memory.content import claim
from makan.memory.hard import names_place, parse_hard
from makan.memory.service import Memory
from makan.memory.store import Owner

Cuisine = Literal[
    "chinese", "indian", "italian", "japanese", "korean", "mexican", "thai", "vietnamese"
]


@dataclass(frozen=True)
class FitInputs:
    likes: tuple[str, ...] = ()
    dislikes: tuple[str, ...] = ()
    problem: FitReason | None = None
    never_places: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def excludes(self, name: str) -> bool:
        return any(names_place(term, name) for term in self.never_places)


def owner_fit_inputs(memory: Memory, user_id: UUID) -> FitInputs:
    """Read this owner's explicit tastes and hard rules, with no visits or free prose.

    A stale or unreadable cuisine prevents a partial profile from looking complete.
    Hard constraints are processed separately, in plain code, regardless of soft evidence.
    """
    likes: list[str] = []
    dislikes: list[str] = []
    never: list[str] = []
    warnings: list[str] = []
    problem = None
    for recalled in memory.recall(
        Owner(user_id=user_id), kinds=("cuisine_like", "cuisine_dislike", "constraint")
    ):
        fact = recalled.fact
        if fact.kind == "constraint":
            hard = parse_hard(fact.content)
            if hard is not None and hard.group == "never_place":
                never.append(hard.term)
            elif hard is not None:
                warnings.append(
                    f"Cannot verify your {hard.describe()} from place categories. "
                    "Check with the venue; a fit score does not establish safety."
                )
            else:
                warnings.append(
                    "A saved hard requirement cannot be verified from place categories."
                )
            continue
        if fact.source != "stated":
            continue
        if recalled.stale:
            problem = FitReason.STALE
            continue
        try:
            cuisine = str(claim(fact.kind, fact.content).content["cuisine"])
        except ValueError:
            problem = FitReason.UNSUPPORTED
            continue
        (likes if fact.kind == "cuisine_like" else dislikes).append(cuisine)
    return FitInputs(tuple(likes), tuple(dislikes), problem, tuple(never), tuple(warnings))
