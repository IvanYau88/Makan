"""What a fact says: the shape of `content` for each kind, and when two facts conflict.

The schema stores `content` as free JSON, so its shape belongs here.
`claim` checks and normalizes a fact into a subject and a value.
Two facts about the same subject with different values contradict each other, and
the same value means one fact was simply said again.
A like and a dislike of one cuisine share a subject, so one supersedes the other.

Content shapes:

- `cuisine_like`, `cuisine_dislike`: `{"cuisine": "thai"}`
- `constraint`: `{"key": "diet", "value": "vegetarian"}`, where the key names one slot
  such as `diet`, `budget_max`, or `allergy_peanut`, and the value is text, a number, or a boolean
- `place_rating`: `{"place_id": "...", "rating": 4}` with an optional `"name"`, rating 1 to 5
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from makan.models import MemoryKind

CUISINE_KINDS: tuple[MemoryKind, ...] = ("cuisine_like", "cuisine_dislike")
MIN_RATING = 1
MAX_RATING = 5


@dataclass(frozen=True)
class Claim:
    subject: str
    value: str | float | bool
    content: dict[str, Any]  # the normalized content, which is what gets stored

    def says_same_as(self, other: Claim) -> bool:
        """Equal values, where a boolean never equals a number (`True == 1` in Python)."""
        return isinstance(self.value, bool) == isinstance(other.value, bool) and (
            self.value == other.value
        )


def claim(kind: str, content: Any) -> Claim:
    """Check `content` for `kind` and normalize it. Raises `ValueError` the model can act on."""
    if not isinstance(content, dict):
        raise ValueError("content must be a JSON object")
    if kind in CUISINE_KINDS:
        _only(content, "cuisine")
        cuisine = _text(content, "cuisine").casefold()
        value = "like" if kind == "cuisine_like" else "dislike"
        return Claim(f"cuisine:{cuisine}", value, {"cuisine": cuisine})
    if kind == "constraint":
        _only(content, "key", "value")
        key = _text(content, "key").casefold()
        raw = content.get("value")
        if isinstance(raw, str):
            raw = raw.strip().casefold()
        if raw is None or raw == "" or not isinstance(raw, str | int | float | bool):
            raise ValueError("value must be text, a number, or true or false")
        return Claim(f"constraint:{key}", raw, {"key": key, "value": raw})
    if kind == "place_rating":
        _only(content, "place_id", "rating", "name")
        place_id = _text(content, "place_id")
        rating = content.get("rating")
        if isinstance(rating, bool) or not isinstance(rating, int | float):
            raise ValueError("rating must be a number")
        if not MIN_RATING <= rating <= MAX_RATING:
            raise ValueError(f"rating must be between {MIN_RATING} and {MAX_RATING}")
        normalized: dict[str, Any] = {"place_id": place_id, "rating": rating}
        if content.get("name") is not None:
            normalized["name"] = _text(content, "name")
        return Claim(f"place:{place_id}", float(rating), normalized)
    raise ValueError(f"unknown kind {kind!r}")


def _only(content: dict[str, Any], *allowed: str) -> None:
    extra = sorted(set(content) - set(allowed))
    if extra:
        raise ValueError(f"content has unexpected fields {extra}; allowed: {list(allowed)}")


def _text(content: dict[str, Any], name: str) -> str:
    value = content.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"content.{name} must be non-empty text")
    return value.strip()
