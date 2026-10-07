"""Hard constraints a person states, kept as `constraint` memory facts.

A hard "never" and a soft "skip" are different things. A skip is a `cuisine_dislike`: it lowers
a place's score and anything can still win. A never rules something out or warns about it, and
it is plain code, so no model and no scorer decides it:

- `allergy:<term>` and `diet:<term>` cannot be checked against the places data, so they come
  back as a "cannot verify" warning on every recommendation and never as a guarantee.
- `never_place:<name>` is a place the person will not go to. The places data has a name for
  every venue, so `names_place` rules it out before ranking.

Each is a `constraint` fact whose key is `<group>:<term>` and whose value is true, so the memory
rules apply as they do to any fact: saying it again reconfirms it, and the kind never decays (see
`makan.memory.policy`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

HardGroup = Literal["allergy", "diet", "never_place"]
HARD_GROUPS: tuple[HardGroup, ...] = ("allergy", "diet", "never_place")

_WORDS = re.compile(r"[^\W_]+")
_APOSTROPHES = str.maketrans("", "", "'\u2019\u02bc")


@dataclass(frozen=True)
class HardConstraint:
    group: HardGroup
    term: str

    def describe(self) -> str:
        """A sentence fragment for a warning, such as `allergy to peanut`."""
        if self.group == "allergy":
            return f"allergy to {self.term}"
        if self.group == "diet":
            return f"{self.term} diet"
        return f"never going to {self.term}"


def hard_content(group: HardGroup, term: str) -> dict[str, Any]:
    """The `content` of the constraint fact for `term`, which `content.claim` then normalizes."""
    return {"key": f"{group}:{term}", "value": True}


def parse_hard(content: Any) -> HardConstraint | None:
    """What a constraint fact says, or None for a constraint this module does not own."""
    if not isinstance(content, dict):
        return None
    key = content.get("key")
    if not isinstance(key, str) or content.get("value") is not True:
        return None
    group, _, term = key.partition(":")
    if group not in HARD_GROUPS or not term.strip():
        return None
    return HardConstraint(group, term.strip())


def words(text: str) -> list[str]:
    """Lowercase words with apostrophes dropped, so `McDonald's` and `mcdonalds` agree."""
    return _WORDS.findall(text.casefold().translate(_APOSTROPHES))


def names_place(term: str, place_name: str) -> bool:
    """Whether a never-go term names this place: its words appear in order in the place's name.

    `pizza hut` rules out `Pizza Hut` and `Pizza Hut Express` and not `Pizza Palace`. A term with
    no letters or digits names nothing.
    """
    wanted = words(term)
    have = words(place_name)
    if not wanted:
        return False
    return any(have[i : i + len(wanted)] == wanted for i in range(len(have) - len(wanted) + 1))
