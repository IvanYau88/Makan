"""Local cuisine fit, separate from request ranking and model confidence.

Version 1 knows only exact primary source categories. No model, name inference, or
cross-cuisine relationship supplies missing evidence. Hard constraints live elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

POLICY_VERSION = "exact-cuisine-v1"


class FitReason(StrEnum):
    DISABLED = "disabled"
    NO_TASTE = "no_taste"
    UNAVAILABLE = "unavailable"
    STALE = "stale_tastes"
    CONFLICT = "conflicting_tastes"
    UNSUPPORTED = "unsupported_tastes"
    NO_MATCH = "no_exact_category_match"
    LIKE = "exact_like"
    DISLIKE = "exact_dislike"


_EXPLANATIONS = {
    FitReason.DISABLED: "Cuisine fit is not enabled.",
    FitReason.NO_TASTE: "Add tastes to see fit.",
    FitReason.UNAVAILABLE: "Cuisine tastes are unavailable. Try again later.",
    FitReason.STALE: "Confirm your saved tastes to see fit.",
    FitReason.CONFLICT: "A cuisine is both liked and skipped. Check your tastes.",
    FitReason.UNSUPPORTED: "Some tastes are outside the supported cuisines, so fit is Unrated.",
    FitReason.NO_MATCH: "No exact cuisine match is available from the place category.",
    FitReason.LIKE: "Exact cuisine match to a stated like. Other cuisines are not assessed.",
    FitReason.DISLIKE: "Exact cuisine match to a stated dislike. Other cuisines are not assessed.",
}


@dataclass(frozen=True)
class FitAssessment:
    score: int | None = None
    reason: FitReason = FitReason.DISABLED

    def __post_init__(self) -> None:
        if not isinstance(self.reason, FitReason):
            raise ValueError("fit needs a known reason")
        if self.score is not None and (type(self.score) is not int or not 0 <= self.score <= 100):
            raise ValueError("fit score must be an integer from 0 to 100, or None")
        if (self.score is not None) != (self.reason in (FitReason.LIKE, FitReason.DISLIKE)):
            raise ValueError("only a rated assessment has a score")

    @property
    def status(self) -> str:
        if self.score is not None:
            return "rated"
        if self.reason in (FitReason.DISABLED, FitReason.NO_TASTE, FitReason.UNAVAILABLE):
            return self.reason.value
        return "insufficient_evidence"

    def to_json(self) -> dict[str, Any]:
        """The entire public allowlist. No memory facts or scorer evidence crosses it."""
        return {
            "score": self.score,
            "status": self.status,
            "basis": "cuisine_profile",
            "policy_version": POLICY_VERSION,
            "reason": self.reason.value,
            "explanation": _EXPLANATIONS[self.reason],
        }


# Deliberately exact: ramen/sushi/pizza and generic restaurant labels imply no cuisine here.
CATEGORY_CUISINES = {
    f"{cuisine}_restaurant": cuisine
    for cuisine in (
        "chinese",
        "indian",
        "italian",
        "japanese",
        "korean",
        "mexican",
        "thai",
        "vietnamese",
    )
}


def cuisine_fit(
    category: str,
    likes: tuple[str, ...],
    dislikes: tuple[str, ...],
    *,
    enabled: bool = False,
    problem: FitReason | None = None,
) -> FitAssessment:
    """An exact-category baseline, never a probability of enjoyment or a safety decision."""
    if not enabled:
        return FitAssessment()
    if problem is not None:
        return FitAssessment(reason=problem)
    if not likes and not dislikes:
        return FitAssessment(reason=FitReason.NO_TASTE)
    if set(likes) & set(dislikes):
        return FitAssessment(reason=FitReason.CONFLICT)
    if not (set(likes) | set(dislikes)) <= set(CATEGORY_CUISINES.values()):
        return FitAssessment(reason=FitReason.UNSUPPORTED)
    cuisine = CATEGORY_CUISINES.get(category)
    if cuisine in likes:
        return FitAssessment(100, FitReason.LIKE)
    if cuisine in dislikes:
        return FitAssessment(0, FitReason.DISLIKE)
    return FitAssessment(reason=FitReason.NO_MATCH)
