"""Group consensus: hard constraints, per-person scores, and the least-misery pick.

Every function here is pure and deterministic. The same options and people give the same ranking
whatever order they arrive in, so the workflow in `makan.group` only has to call them.

1. `parse_constraints` and `parse_preferences` validate what a participant chose to share.
2. `apply_constraints` removes the options a hard constraint rules out. Only a refusal of a
   category or cuisine can do that, because it is the only constraint the places data can check.
   Allergies, diets, and budgets cannot be checked, so `unverified_warnings` lists them instead,
   and nothing in this module ever calls an option safe.
3. `score_options` gives each person a score from 0 to 1 for each remaining option. A person who
   shared no taste preference has no opinion to count, so they are left out of the scores, though
   their hard constraints already applied.
4. `order_options` picks by least misery over every option left: the best option is the one whose
   lowest-scoring person scores highest, and options within `CLOSE_SCORE_MARGIN` of that lowest
   score are compared on their average score instead. If nobody shared a taste, the pick is the
   best match for the request and then the nearest place, which is the solo ranking.

`consensus` runs steps 2 to 4. Scores are all rounded to 9 decimals so float noise can never
decide a tie, and nothing depends on the order of the inputs.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from makan.solo import Candidate

# How close two options' lowest scores must be to count as "close", on the 0 to 1 scale. Inside
# this margin the option with the better average score wins, so one lukewarm person cannot decide
# the pick over a clearly happier group. It equals PROXIMITY_WEIGHT, so a small walking difference
# alone never beats a better average. Raising it favors the average, and 0 is a strict
# least-misery pick that falls back to the average only on an exact tie.
CLOSE_SCORE_MARGIN = 0.05

# A score is TASTE_WEIGHT * taste + REQUEST_WEIGHT * request + PROXIMITY_WEIGHT * proximity, so it
# stays between 0 and 1. Taste is 1 for a liked category, 0 for a disliked one, and NEUTRAL_TASTE
# otherwise. The request is how much of what the group asked for the option matches, and proximity
# is how near it is, and both are the same for everyone, so they nudge the pick without taking a
# side. One step of taste (0.35) is worth more than the request and proximity together (0.30), so
# the request can never outweigh what someone likes or dislikes, and it still beats distance.
TASTE_WEIGHT = 0.70
REQUEST_WEIGHT = 0.25
PROXIMITY_WEIGHT = 0.05
NEUTRAL_TASTE = 0.5

MAX_TERMS = 20  # entries in any one list a person shares
MAX_TERM_CHARS = 40  # a category or cuisine term
MAX_NOTE_CHARS = 80  # an allergy, diet, or budget note, which are free text
MAX_NAME_CHARS = 40  # a display name

_PRECISION = 9
_WORDS = re.compile(r"[a-z0-9]+")

CONSTRAINT_KEYS = ("refuses", "allergies", "diets", "budget")
PREFERENCE_KEYS = ("likes", "dislikes")


class InvalidInputs(ValueError):
    """What a participant shared is malformed. The message is safe to show them."""


@dataclass(frozen=True)
class Constraints:
    """Hard constraints. Only `refuses` is checkable against the places data."""

    refuses: tuple[str, ...] = ()  # category or cuisine terms such as "seafood" or "thai"
    allergies: tuple[str, ...] = ()  # free text, never verifiable
    diets: tuple[str, ...] = ()  # free text, never verifiable
    budget: str | None = None  # free text, never verifiable

    def to_json(self) -> dict[str, Any]:
        return {
            "refuses": list(self.refuses),
            "allergies": list(self.allergies),
            "diets": list(self.diets),
            "budget": self.budget,
        }


@dataclass(frozen=True)
class Preferences:
    likes: tuple[str, ...] = ()
    dislikes: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {"likes": list(self.likes), "dislikes": list(self.dislikes)}


@dataclass(frozen=True)
class Member:
    """One person in the consensus. `name` is unique within a group and is how scores are keyed."""

    name: str
    constraints: Constraints = Constraints()
    preferences: Preferences = Preferences()
    submitted: bool = True  # False for someone who joined and has shared nothing yet

    @property
    def has_taste(self) -> bool:
        """Whether they shared any like or dislike. Without one they have no opinion to count."""
        return bool(self.preferences.likes or self.preferences.dislikes)


@dataclass(frozen=True)
class Refusal:
    member: str
    term: str


@dataclass(frozen=True)
class Exclusion:
    place: Candidate
    refusals: tuple[Refusal, ...]


@dataclass(frozen=True)
class Scored:
    """An option with the score of each person who shared a taste, as `(name, score)` by name.

    `scores` is empty when nobody shared a taste, and then there is no minimum or average.
    """

    place: Candidate
    scores: tuple[tuple[str, float], ...]
    likes: int = 0  # how many people like it
    dislikes: int = 0  # how many people dislike it

    @property
    def minimum(self) -> float:
        return min(score for _, score in self.scores)

    @property
    def average(self) -> float:
        return _q(math.fsum(score for _, score in self.scores) / len(self.scores))

    @property
    def least_happy(self) -> tuple[str, ...]:
        """Everyone who scored the option at its minimum, by name, or nobody if none scored it."""
        if not self.scores:
            return ()
        low = self.minimum
        return tuple(name for name, score in self.scores if score == low)


@dataclass(frozen=True)
class Consensus:
    ranked: tuple[Scored, ...]  # best first, so `ranked[0]` is the pick
    excluded: tuple[Exclusion, ...]  # nearest first


# Parsing


def parse_constraints(raw: Mapping[str, Any]) -> Constraints:
    """Validate the constraints a person shares. An empty mapping is no constraints.

    An unknown key is an error, never ignored, so a misspelled "allergy" cannot silently drop one.
    """
    _only_keys(raw, CONSTRAINT_KEYS, "constraints")
    budget = raw.get("budget")
    return Constraints(
        refuses=_terms(raw.get("refuses"), "refuses"),
        allergies=_notes(raw.get("allergies"), "allergies"),
        diets=_notes(raw.get("diets"), "diets"),
        budget=None if budget is None else _note(budget, "budget"),
    )


def parse_preferences(raw: Mapping[str, Any]) -> Preferences:
    """Validate the soft preferences a person shares. An empty mapping is no preferences."""
    _only_keys(raw, PREFERENCE_KEYS, "preferences")
    return Preferences(
        likes=_terms(raw.get("likes"), "likes"),
        dislikes=_terms(raw.get("dislikes"), "dislikes"),
    )


def clean_name(value: str, what: str = "display name") -> str:
    """Trim and collapse whitespace. Raise `InvalidInputs` if nothing, or too much, is left."""
    text = " ".join(value.split())
    if not text:
        raise InvalidInputs(f"{what} must not be empty")
    if len(text) > MAX_NAME_CHARS:
        raise InvalidInputs(f"{what} must be at most {MAX_NAME_CHARS} characters")
    if any(not ch.isprintable() for ch in text):
        raise InvalidInputs(f"{what} must not contain control characters")
    return text


def _only_keys(raw: Mapping[str, Any], allowed: Sequence[str], what: str) -> None:
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise InvalidInputs(f"unknown {what} field: {', '.join(unknown)}")


def _list(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidInputs(f"{field} must be a list")
    if len(value) > MAX_TERMS:
        raise InvalidInputs(f"{field} may have at most {MAX_TERMS} entries")
    return value


def _terms(value: Any, field: str) -> tuple[str, ...]:
    """Category or cuisine terms, lowercased. They must be matchable, or a refusal is inert."""
    terms: list[str] = []
    for item in _list(value, field):
        if not isinstance(item, str):
            raise InvalidInputs(f"{field} must be a list of text")
        if any(not ch.isprintable() for ch in item):
            # Checked before the text is split into words, or a control character could hide
            # inside a word and make a refusal match nothing.
            raise InvalidInputs(f"{field}: entries must not contain control characters")
        term = " ".join(item.lower().split())
        if not _WORDS.search(term):
            raise InvalidInputs(
                f"{field}: {item!r} has no letters or digits to match a category with"
            )
        if len(term) > MAX_TERM_CHARS:
            raise InvalidInputs(f"{field}: each entry must be at most {MAX_TERM_CHARS} characters")
        terms.append(term)
    return tuple(dict.fromkeys(terms))


def _note(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidInputs(f"{field} must be text")
    text = " ".join(value.split())
    if not text:
        raise InvalidInputs(f"{field} must not be empty")
    if len(text) > MAX_NOTE_CHARS:
        raise InvalidInputs(f"{field}: each entry must be at most {MAX_NOTE_CHARS} characters")
    if any(not ch.isprintable() for ch in text):
        raise InvalidInputs(f"{field} must not contain control characters")
    return text


def _notes(value: Any, field: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_note(item, field) for item in _list(value, field)))


# Hard constraints


def apply_constraints(
    candidates: Sequence[Candidate], members: Sequence[Member]
) -> tuple[tuple[Candidate, ...], tuple[Exclusion, ...]]:
    """Split options into those that survive every refusal and those a refusal rules out.

    An option is excluded when its primary category matches a term someone refuses, with every
    such refusal recorded. The tool carries the primary category only, so a place whose primary
    label is generic is not excluded, and `unverified_warnings` says so. Survivors keep their
    input order, and exclusions are nearest first.
    """
    kept: list[Candidate] = []
    excluded: list[Exclusion] = []
    for candidate in candidates:
        refusals = sorted(
            {
                Refusal(m.name, term)
                for m in members
                for term in m.constraints.refuses
                if candidate.matches(term)
            },
            key=lambda r: (r.member, r.term),
        )
        if refusals:
            excluded.append(Exclusion(candidate, tuple(refusals)))
        else:
            kept.append(candidate)
    excluded.sort(key=lambda e: _place_key(e.place))
    return tuple(kept), tuple(excluded)


def unverified_warnings(members: Sequence[Member]) -> tuple[str, ...]:
    """One warning for every shared constraint the places data cannot check, by person.

    Allergies, diets, and budgets need menus, ingredients, or prices, and the data has none. These
    apply to every option equally, so the workflow puts them against the pick and each runner-up.
    """
    warnings: list[str] = []
    for member in sorted(members, key=lambda m: m.name):
        c = member.constraints
        warnings.extend(f"Cannot verify {member.name}'s allergy to {a}." for a in c.allergies)
        warnings.extend(f"Cannot verify {member.name}'s diet: {d}." for d in c.diets)
        if c.budget is not None:
            warnings.append(f"Cannot verify {member.name}'s budget: {c.budget}.")
    return tuple(warnings)


def shared_unverified_warnings(members: Sequence[Member]) -> tuple[str, ...]:
    """The same warnings as `unverified_warnings`, with nobody's name next to their constraint.

    This is what the group sees: that the data cannot check an allergy, a diet, or a budget, and
    which ones were shared, but not who shared them. Repeats are dropped and the order is fixed.
    """
    constraints = [m.constraints for m in members]
    allergies = sorted({a for c in constraints for a in c.allergies})
    diets = sorted({d for c in constraints for d in c.diets})
    budgets = sorted({c.budget for c in constraints if c.budget is not None})
    return (
        *(f"Cannot verify an allergy to {a}." for a in allergies),
        *(f"Cannot verify a diet: {d}." for d in diets),
        *(f"Cannot verify a budget: {b}." for b in budgets),
    )


# Scoring


def taste(candidate: Candidate, preferences: Preferences) -> float:
    """1 for a liked category, 0 for a disliked one, and `NEUTRAL_TASTE` for neither or both."""
    liked = any(candidate.matches(t) for t in preferences.likes)
    disliked = any(candidate.matches(t) for t in preferences.dislikes)
    return NEUTRAL_TASTE + (0.5 if liked else 0.0) - (0.5 if disliked else 0.0)


def proximity(candidate: Candidate, radius_m: int) -> float:
    """1 at the search point down to 0 at the edge of the radius."""
    return 1.0 - min(candidate.distance_m / radius_m, 1.0)


def request_match(candidate: Candidate, requested: int) -> float:
    """The share of the requested filters the option matches, or 0 when nothing was requested."""
    return min(candidate.request_fit / requested, 1.0) if requested else 0.0


def score(candidate: Candidate, member: Member, radius_m: int, requested: int = 0) -> float:
    """How happy `member` would be with `candidate`, from 0 to 1.

    `requested` is how many filters, up to two, the group's request asked for.
    """
    return _q(
        TASTE_WEIGHT * taste(candidate, member.preferences)
        + REQUEST_WEIGHT * request_match(candidate, requested)
        + PROXIMITY_WEIGHT * proximity(candidate, radius_m)
    )


def score_options(
    candidates: Sequence[Candidate],
    members: Sequence[Member],
    radius_m: int,
    requested: int = 0,
) -> tuple[Scored, ...]:
    """Score every option for every person who shared a taste, in the order the options came.

    A person with no likes or dislikes is neutral, which would otherwise score every option from
    distance alone, and could then cap the minimum or lower the average for options a person who
    does have a taste likes. They are left out, and their hard constraints already applied.
    """
    if not members:
        raise ValueError("a consensus needs at least one person")
    if radius_m < 1:
        raise ValueError("radius_m must be at least 1")
    names = [m.name for m in members]
    if len(set(names)) != len(names):
        raise ValueError("people in a consensus need distinct names")
    people = sorted(members, key=lambda m: m.name)
    counted = [m for m in people if m.has_taste]
    return tuple(
        Scored(
            candidate,
            tuple((m.name, score(candidate, m, radius_m, requested)) for m in counted),
            likes=sum(any(candidate.matches(t) for t in m.preferences.likes) for m in people),
            dislikes=sum(any(candidate.matches(t) for t in m.preferences.dislikes) for m in people),
        )
        for candidate in candidates
    )


# The pick


def order_options(scored: Sequence[Scored]) -> tuple[Scored, ...]:
    """Rank options best first by least misery, so the first one is the pick.

    The pick is chosen among the options whose lowest score is within `CLOSE_SCORE_MARGIN` of the
    best lowest score, and the best average score wins there. Ties on both fall to the nearest
    place, then name, then id. The runners-up are found the same way from what is left, so the
    order never relies on comparing two near-equal options pairwise, which could disagree with
    itself across three. When nobody shared a taste there are no scores, and the order is the solo
    ranking: the best match for the request, then nearest, then name, then id.
    """
    remaining = list(scored)
    ordered: list[Scored] = []
    while remaining:
        best = _pick(remaining)
        ordered.append(best)
        remaining.remove(best)
    return tuple(ordered)


def consensus(
    candidates: Sequence[Candidate],
    members: Sequence[Member],
    radius_m: int,
    requested: int = 0,
) -> Consensus:
    """Apply hard constraints, score what is left, and rank it by least misery."""
    kept, excluded = apply_constraints(candidates, members)
    return Consensus(order_options(score_options(kept, members, radius_m, requested)), excluded)


def floor_score(value: float) -> float:
    """Round down to 2 decimals, so "nobody scored it below X" is true of the shown X."""
    return float(Decimal(repr(_q(value))).quantize(Decimal("0.01"), rounding=ROUND_FLOOR))


def _pick(options: Sequence[Scored]) -> Scored:
    if not any(o.scores for o in options):  # nobody shared a taste
        return min(options, key=lambda o: (-o.place.request_fit, *_place_key(o.place)))
    best_minimum = max(o.minimum for o in options)
    close = [o for o in options if _q(best_minimum - o.minimum) <= CLOSE_SCORE_MARGIN]
    return min(close, key=lambda o: (-o.average, *_place_key(o.place)))


def _place_key(place: Candidate) -> tuple[int, str, str]:
    return place.distance_m, place.name, place.id


def _q(value: float) -> float:
    return round(value, _PRECISION)
