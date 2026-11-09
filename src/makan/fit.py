"""Local cuisine fit, separate from request ranking and model confidence.

It compares stated tastes with the cuisines a place's own source categories name, in the
controlled vocabulary of `makan.cuisines`. No model, name inference, or related cuisine
supplies missing evidence. Hard constraints live elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from makan.cuisines import CUISINE_IDS

# Version 2 reads the wider listed-cuisine vocabulary; version 1 knew eight exact categories.
POLICY_VERSION = "listed-cuisine-v2"
MODEL_POLICY_VERSION = "clef-profile-fit-v1-proposed"
# The same questions and acceptance over state version 2, with the owner's own history evidence.
MODEL_HISTORY_POLICY_VERSION = "clef-profile-fit-v2-proposed"


class FitReason(StrEnum):
    MODEL = "model_estimate"
    NO_CONSENT = "no_consent"
    INSUFFICIENT = "insufficient_evidence"
    DEADLINE = "deadline"
    CONFIGURATION = "configuration"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    TIMEOUT = "timeout"
    SERVICE_UNAVAILABLE = "service_unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    REFUSED = "refused"
    INVALID_INPUT = "invalid_input"
    DISABLED = "disabled"
    NO_TASTE = "no_taste"
    UNAVAILABLE = "unavailable"
    STALE = "stale_tastes"
    CONFLICT = "conflicting_tastes"
    UNSUPPORTED = "unsupported_tastes"
    NO_MATCH = "no_exact_category_match"
    NO_CUISINE = "no_listed_cuisine"
    MIXED = "mixed_cuisine_match"
    LIKE = "exact_like"
    DISLIKE = "exact_dislike"


_EXPLANATIONS = {
    FitReason.MODEL: "Taste estimate from the preferences you shared and available place facts.",
    FitReason.NO_CONSENT: "Online taste scoring is off. You can opt in under Your tastes.",
    FitReason.INSUFFICIENT: "Available taste and place facts cannot support a score.",
    FitReason.DEADLINE: "This place was not scored within the search time limit.",
    FitReason.CONFIGURATION: (
        "Online taste scoring is not configured. Nearby results are available."
    ),
    FitReason.RATE_LIMITED: "Taste scoring is busy. Try a later search.",
    FitReason.QUOTA_EXHAUSTED: "Taste scoring's daily allowance is used up.",
    FitReason.TIMEOUT: "Taste scoring timed out. No score was estimated.",
    FitReason.SERVICE_UNAVAILABLE: "Taste scoring is unavailable. Try a later search.",
    FitReason.MALFORMED_RESPONSE: "Taste scoring returned an unusable answer.",
    FitReason.REFUSED: "Taste scoring did not return an assessment.",
    FitReason.INVALID_INPUT: "These taste or place facts cannot be scored.",
    FitReason.DISABLED: "Cuisine fit is not enabled.",
    FitReason.NO_TASTE: "Add tastes to see fit.",
    FitReason.UNAVAILABLE: "Cuisine tastes are unavailable. Try again later.",
    FitReason.STALE: "Confirm your saved tastes to see fit.",
    FitReason.CONFLICT: "A cuisine is both liked and skipped. Check your tastes.",
    FitReason.UNSUPPORTED: "Some tastes are outside the supported cuisines, so fit is Unrated.",
    FitReason.NO_MATCH: "The cuisine this place lists matches none of your tastes.",
    FitReason.NO_CUISINE: "The place category names no cuisine, so fit is Unrated.",
    FitReason.MIXED: "This place lists a cuisine you like and one you skip, so fit is Unrated.",
    FitReason.LIKE: "Listed cuisine matches a stated like. Other cuisines are not assessed.",
    FitReason.DISLIKE: "Listed cuisine matches a stated dislike. Other cuisines are not assessed.",
}


@dataclass(frozen=True)
class FitAssessment:
    score: int | None = None
    reason: FitReason = FitReason.DISABLED

    basis: str = "cuisine_profile"
    cached: bool = False
    model_policy: str = MODEL_POLICY_VERSION  # only meaningful for basis=profile_model

    def __post_init__(self) -> None:
        if not isinstance(self.reason, FitReason):
            raise ValueError("fit needs a known reason")
        if self.score is not None and (type(self.score) is not int or not 0 <= self.score <= 100):
            raise ValueError("fit score must be an integer from 0 to 100, or None")
        if self.basis not in ("cuisine_profile", "profile_model") or type(self.cached) is not bool:
            raise ValueError("invalid fit provenance")
        if self.reason == FitReason.MODEL and self.basis != "profile_model":
            raise ValueError("model estimate needs model provenance")
        if self.basis == "profile_model" and self.reason in (FitReason.LIKE, FitReason.DISLIKE):
            raise ValueError("model assessment cannot claim deterministic cuisine evidence")
        if self.cached and self.basis != "profile_model":
            raise ValueError("only gateway assessments may be cached")
        if self.model_policy not in (MODEL_POLICY_VERSION, MODEL_HISTORY_POLICY_VERSION) or (
            self.basis != "profile_model" and self.model_policy != MODEL_POLICY_VERSION
        ):
            raise ValueError("invalid fit policy version")
        if (self.score is not None) != (
            self.reason in (FitReason.LIKE, FitReason.DISLIKE, FitReason.MODEL)
        ):
            raise ValueError("only a rated assessment has a score")

    @property
    def status(self) -> str:
        if self.score is not None:
            return "rated"
        if self.reason in (
            FitReason.CONFIGURATION,
            FitReason.RATE_LIMITED,
            FitReason.QUOTA_EXHAUSTED,
            FitReason.TIMEOUT,
            FitReason.SERVICE_UNAVAILABLE,
            FitReason.MALFORMED_RESPONSE,
            FitReason.REFUSED,
            FitReason.INVALID_INPUT,
            FitReason.DEADLINE,
        ):
            return "unavailable"
        if self.reason in (FitReason.DISABLED, FitReason.NO_TASTE, FitReason.UNAVAILABLE):
            return self.reason.value
        return "insufficient_evidence"

    def to_json(self) -> dict[str, Any]:
        """The entire public allowlist. No memory facts or scorer evidence crosses it."""
        return {
            "score": self.score,
            "status": self.status,
            "basis": self.basis,
            "policy_version": (
                self.model_policy if self.basis == "profile_model" else POLICY_VERSION
            ),
            "cached": self.cached,
            "reason": self.reason.value,
            "explanation": _EXPLANATIONS[self.reason],
        }


# The frozen Clef state versions 1 and 2 know only these eight exact primary categories, and
# their schemas, manifests and gateway pin are hashed, so the wider vocabulary never enters them.
CLEF_CUISINES = (
    "chinese",
    "indian",
    "italian",
    "japanese",
    "korean",
    "mexican",
    "thai",
    "vietnamese",
)
CLEF_CATEGORY_CUISINES = {f"{cuisine}_restaurant": cuisine for cuisine in CLEF_CUISINES}


def cuisine_fit(
    cuisines: tuple[str, ...],
    likes: tuple[str, ...],
    dislikes: tuple[str, ...],
    *,
    enabled: bool = False,
    problem: FitReason | None = None,
) -> FitAssessment:
    """A listed-cuisine baseline, never a probability of enjoyment or a safety decision.

    `cuisines` are the vocabulary terms the place's own categories name, see
    `makan.cuisines.place_cuisines`. A place that names both a like and a dislike is Unrated,
    so the order of tastes or categories never decides.
    """
    if not enabled:
        return FitAssessment()
    if problem is not None:
        return FitAssessment(reason=problem)
    if not likes and not dislikes:
        return FitAssessment(reason=FitReason.NO_TASTE)
    if set(likes) & set(dislikes):
        return FitAssessment(reason=FitReason.CONFLICT)
    if not (set(likes) | set(dislikes)) <= CUISINE_IDS:
        return FitAssessment(reason=FitReason.UNSUPPORTED)
    if not cuisines:
        return FitAssessment(reason=FitReason.NO_CUISINE)
    liked = set(cuisines) & set(likes)
    disliked = set(cuisines) & set(dislikes)
    if liked and disliked:
        return FitAssessment(reason=FitReason.MIXED)
    if liked:
        return FitAssessment(100, FitReason.LIKE)
    if disliked:
        return FitAssessment(0, FitReason.DISLIKE)
    return FitAssessment(reason=FitReason.NO_MATCH)
