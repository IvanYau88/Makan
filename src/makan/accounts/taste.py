"""What a person tells Makan about their food, and how it is stored as memory facts.

The form has five lists. Cuisines to like and cuisines to skip are soft: they are
`cuisine_like` and `cuisine_dislike` facts that raise or lower a place's score. Allergies,
diets, and places the person will never go to are hard: they are `constraint` facts in the
vocabulary of `makan.memory.hard`, and plain code decides what they do.

Saving makes the stored facts match the form. A new term is remembered, a repeated one is
reconfirmed, and a term the person removed is forgotten. A cuisine cannot be both liked and
skipped. Other facts (place ratings, constraints this form does not own) are left alone.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from makan.accounts.errors import InvalidProfile
from makan.memory.content import claim
from makan.memory.hard import HARD_GROUPS, HardGroup, hard_content, parse_hard, words
from makan.memory.service import Memory
from makan.memory.store import Owner
from makan.models import MemoryFact, MemoryKind

MAX_TERMS = 20  # entries in one list
MAX_TERM_CHARS = 40  # one cuisine, allergy, diet, or place

_REMOVED = ("Cc", "Cf", "Cs", "Co", "Cn")


@dataclass(frozen=True)
class Taste:
    likes: tuple[str, ...] = ()
    dislikes: tuple[str, ...] = ()  # the soft "skip"
    allergies: tuple[str, ...] = ()  # the hard "never" lists
    diets: tuple[str, ...] = ()
    never_places: tuple[str, ...] = ()

    def to_json(self) -> dict[str, list[str]]:
        return {
            "likes": list(self.likes),
            "dislikes": list(self.dislikes),
            "allergies": list(self.allergies),
            "diets": list(self.diets),
            "never_places": list(self.never_places),
        }


_HARD_FIELDS: dict[HardGroup, str] = {
    "allergy": "allergies",
    "diet": "diets",
    "never_place": "never_places",
}


def clean_terms(raw: Iterable[str], label: str) -> tuple[str, ...]:
    """Trim, drop control characters, fold case, and remove repeats, keeping the first spelling.

    Raises `InvalidProfile` for a term with no letter or digit, a term that is too long, or a
    list that is too long. `label` is how the message names the list.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for item in raw:
        spaced = "".join(" " if ch.isspace() else ch for ch in unicodedata.normalize("NFC", item))
        text = " ".join(
            "".join(c for c in spaced if unicodedata.category(c) not in _REMOVED).split()
        )
        if not text:
            continue
        if not words(text):
            raise InvalidProfile(f"Each entry in {label} needs a letter or a number.")
        if len(text) > MAX_TERM_CHARS:
            raise InvalidProfile(f"Keep each entry in {label} to {MAX_TERM_CHARS} characters.")
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            terms.append(key)
    if len(terms) > MAX_TERMS:
        raise InvalidProfile(f"Use at most {MAX_TERMS} entries in {label}.")
    return tuple(terms)


def checked(taste: Taste) -> Taste:
    """The taste with every list cleaned, or `InvalidProfile` when it contradicts itself."""
    cleaned = Taste(
        likes=clean_terms(taste.likes, "cuisines you like"),
        dislikes=clean_terms(taste.dislikes, "cuisines you would rather skip"),
        allergies=clean_terms(taste.allergies, "allergies"),
        diets=clean_terms(taste.diets, "diets"),
        never_places=clean_terms(taste.never_places, "places you will not go to"),
    )
    both = sorted(set(cleaned.likes) & set(cleaned.dislikes))
    if both:
        raise InvalidProfile(f"{', '.join(both)} cannot be both liked and skipped.")
    return cleaned


def read_taste(memory: Memory, user_id: UUID) -> Taste:
    """The form as the stored facts say it, oldest term first."""
    lists: dict[str, list[str]] = {
        name: [] for name in ("likes", "dislikes", *_HARD_FIELDS.values())
    }
    for recalled in sorted(memory.recall(Owner(user_id=user_id)), key=lambda r: r.fact.observed_at):
        fact = recalled.fact
        if fact.kind in ("cuisine_like", "cuisine_dislike"):
            lists["likes" if fact.kind == "cuisine_like" else "dislikes"].append(_cuisine(fact))
        elif fact.kind == "constraint" and (hard := parse_hard(fact.content)) is not None:
            lists[_HARD_FIELDS[hard.group]].append(hard.term)
    return Taste(**{name: tuple(terms) for name, terms in lists.items()})


def save_taste(memory: Memory, user_id: UUID, taste: Taste) -> Taste:
    """Make the stored facts match `taste`, which must already be `checked`, and return them."""
    owner = Owner(user_id=user_id)
    wanted: dict[tuple[MemoryKind, str], dict[str, Any]] = {}
    for term in taste.likes:
        wanted[("cuisine_like", term)] = {"cuisine": term}
    for term in taste.dislikes:
        wanted[("cuisine_dislike", term)] = {"cuisine": term}
    for group in HARD_GROUPS:
        for term in getattr(taste, _HARD_FIELDS[group]):
            wanted[("constraint", f"{group}:{term}")] = hard_content(group, term)

    # Remember first, so a cuisine moved from liked to skipped supersedes its old fact and keeps
    # the history. What is left in force and not wanted was removed by the person, so it goes.
    for (kind, _), content in wanted.items():
        memory.remember(owner, kind, content)
    for recalled in memory.recall(owner):
        label = _label(recalled.fact)
        if label is not None and label not in wanted:
            memory.forget(user_id, recalled.fact.id)
    return read_taste(memory, user_id)


def _cuisine(fact: MemoryFact) -> str:
    return str(claim(fact.kind, fact.content).content["cuisine"])


def _label(fact: MemoryFact) -> tuple[MemoryKind, str] | None:
    """The key `save_taste` files a fact under, or None for a fact this form does not own."""
    if fact.kind in ("cuisine_like", "cuisine_dislike"):
        try:
            return fact.kind, _cuisine(fact)
        except ValueError:
            return None
    if fact.kind == "constraint" and (hard := parse_hard(fact.content)) is not None:
        return "constraint", f"{hard.group}:{hard.term}"
    return None
