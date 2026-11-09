"""A diner's own taste, learned only from their own ratings of places.

This is the own taste model of "Own taste model" in `docs/DESIGN.md`: a ridge regression, which is
an additive model, over the facts a rated place listed when the rating was saved. The features are
the place's vocabulary cuisines, its soft diet labels, and whether its weekly hours were known,
which are exactly the facts a search shows, so training and scoring see the same features. Each
distinct place counts once, by the owner's newest rating of it, and one owner's rated places fit
one model, on the CPU, with an unpenalised mean, so a prediction is that owner's average plus a
small nudge for each fact the place lists.

- Owner only: a model is fitted from the ratings `Visits.taste_ratings` reads for its owner and
  cached under that owner, so no model is shared or mixed across owners, and a guest has none.
- Deterministic and versioned: the same ratings give the same weights and the same estimate, and
  `POLICY_VERSION` names the features, the ridge strength and the thresholds together, so any
  change to them is a new version.
- Inert on import: no key, network, database or model, and no fallback to a hosted scorer.
- Never a hard rule: an estimate changes no order, admits or removes no place, and never touches
  an allergy, strict diet or never-go rule, which stay in plain code. A listed diet is only a
  descriptive fact here and never appears in the reason shown.
"""

from __future__ import annotations

import hashlib
import math
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from uuid import UUID

from makan.cuisines import CUISINE_IDS, DIET_LABELS, cuisine_label
from makan.fit import FitReason, cuisine_fit
from makan.places.hours import parse_hours
from makan.visits.content import PlaceFacts
from makan.visits.service import RatedPlace

# Names the features, the ridge strength, the thresholds and the reason rules together.
POLICY_VERSION = "own-taste-ridge-v2"

# Fixed rather than tuned per owner: on the report's ratings it matched an inner tuning at every
# size from 5 to 96 ratings, and a fixed value keeps one owner's fit cheap and reproducible.
RIDGE_STRENGTH = 1.0
# Fewer rated places than this show no personal score: the model barely beats the owner's average.
MIN_RATINGS = 10
# Below this many rated places the score is shown as an estimate.
SETTLED_RATINGS = 30
# The rating scale, the same 0 to 10 a visit is rated on, and the step a score is shown in.
MAX_SCORE = 10.0
SCORE_STEP = 0.5
# A cuisine counts as a lean for the reason shown when the owner's shrunk average for places
# listing it is at least this far from their overall average (half a level of five).
LEAN_MARGIN = 1.0
# Each extra rating of a cuisine counts fully, and the first counts half, so one rating alone is
# rarely a lean.
LEAN_SHRINK = 1.0
# Owners whose fitted model is kept in memory at once.
CACHE_OWNERS = 256


def features(facts: PlaceFacts) -> frozenset[str]:
    """The binary features of a place: what it lists, never who rated it or where it is.

    Dish names are not features: a search reads no menu, so a weight learned on them would never
    apply when a place is scored.
    """
    found = {f"cuisine:{c}" for c in facts.cuisines if c in CUISINE_IDS}
    found |= {f"diet:{d}" for d in facts.diets if d in DIET_LABELS}
    if facts.hours_known:
        found.add("hours_known")
    return frozenset(found)


def place_facts(
    cuisines: Iterable[str], diets: Iterable[str], opening_hours: str | None
) -> PlaceFacts:
    """The facts of a place a search shows. A search reads no menu, so it lists no dishes, and
    its hours are known when the mapped opening hours are readable as a week."""
    return PlaceFacts(tuple(cuisines), tuple(diets), (), parse_hours(opening_hours) is not None)


@dataclass(frozen=True)
class TasteEstimate:
    """A personal score for one place, on the owner's own 0 to 10 rating scale."""

    score: float
    ratings: int
    why: str

    @property
    def estimate(self) -> bool:
        return self.ratings < SETTLED_RATINGS

    def to_json(self) -> dict[str, Any]:
        """The whole public shape. No weight, other rating or other place crosses it."""
        return {
            "score": self.score,
            "scale": MAX_SCORE,
            "estimate": self.estimate,
            "ratings": self.ratings,
            "why": self.why,
            "policy_version": POLICY_VERSION,
        }


@dataclass(frozen=True)
class TasteModel:
    """One owner's fitted model: their average, a weight per feature, and their cuisine leans."""

    mean: float
    weights: Mapping[str, float]
    ratings: int
    # The free cuisine match's profile inferred from the same ratings: a shrunk difference from
    # the owner's average for each cuisine they rated, used only for the reason shown.
    leans: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))

    def predict(self, facts: PlaceFacts) -> float:
        """The owner's expected rating of a place, from 0 to 10."""
        nudge = sum(self.weights.get(f, 0.0) for f in sorted(features(facts)))
        return min(MAX_SCORE, max(0.0, self.mean + nudge))

    def estimate(self, facts: PlaceFacts) -> TasteEstimate:
        raw = self.predict(facts)
        score = math.floor(raw / SCORE_STEP + 0.5) * SCORE_STEP
        return TasteEstimate(score, self.ratings, self.why(facts))

    def why(self, facts: PlaceFacts) -> str:
        """One plain line from the free cuisine match over the owner's inferred leans."""
        likes = tuple(c for c, lean in self.leans.items() if lean >= LEAN_MARGIN)
        dislikes = tuple(c for c, lean in self.leans.items() if lean <= -LEAN_MARGIN)
        match = cuisine_fit(facts.cuisines, likes, dislikes, enabled=True)
        if match.reason in (FitReason.LIKE, FitReason.DISLIKE):
            matched = [c for c in facts.cuisines if c in (likes + dislikes)]
            strongest = max(matched, key=lambda c: (abs(self.leans[c]), c))
            label = cuisine_label(strongest)
            if match.reason == FitReason.LIKE:
                return f"You tend to like {label}."
            return f"You tend not to like {label}."
        if not any(f in self.weights for f in features(facts)):
            # Nothing it lists was on a place the owner rated, so the score is their average.
            return "Your usual rating. You have not rated a place like this yet."
        return f"From your {self.ratings} ratings."


def fit(
    ratings: Sequence[tuple[PlaceFacts, float]], strength: float = RIDGE_STRENGTH
) -> TasteModel:
    """Ridge regression with an unpenalised mean on binary features.

    The weights solve `(X'X + strength I) w = X'(y - mean)` by conjugate gradients over the sparse
    rows, which is the same solution as the dual (kernel) form the report evaluated, at a cost
    that grows with the number of listed facts rather than the cube of the ratings.
    """
    if not ratings:
        raise ValueError("a taste model needs at least one rating")
    if not strength > 0:
        raise ValueError("the ridge strength must be positive")
    rows = [features(facts) for facts, _ in ratings]
    names = sorted(set().union(*rows))
    index = {name: i for i, name in enumerate(names)}
    sparse = [sorted(index[name] for name in row) for row in rows]
    values = [float(rating) for _, rating in ratings]
    mean = math.fsum(values) / len(values)
    centered = [v - mean for v in values]
    target = [0.0] * len(names)
    for row, residual in zip(sparse, centered, strict=True):
        for j in row:
            target[j] += residual
    solved = _conjugate_gradients(sparse, len(names), target, strength)
    weights = {name: solved[i] for name, i in index.items()}
    return TasteModel(mean, MappingProxyType(weights), len(ratings), _leans(ratings, mean))


def _conjugate_gradients(
    rows: Sequence[Sequence[int]], size: int, target: Sequence[float], strength: float
) -> list[float]:
    """Solve the ridge normal equations, which are symmetric and positive definite."""

    def apply(v: Sequence[float]) -> list[float]:
        out = [strength * x for x in v]
        for row in rows:
            total = math.fsum(v[j] for j in row)
            for j in row:
                out[j] += total
        return out

    x = [0.0] * size
    residual = list(target)
    direction = list(residual)
    norm = _dot(residual, residual)
    stop = 1e-24 * max(norm, 1e-300)
    # Exact arithmetic converges within `size` steps; the slack covers rounding.
    for _ in range(2 * size + 10):
        if norm <= stop:
            break
        moved = apply(direction)
        step = norm / _dot(direction, moved)
        x = [a + step * d for a, d in zip(x, direction, strict=True)]
        residual = [r - step * m for r, m in zip(residual, moved, strict=True)]
        new_norm = _dot(residual, residual)
        direction = [r + (new_norm / norm) * d for r, d in zip(residual, direction, strict=True)]
        norm = new_norm
    return x


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return math.fsum(x * y for x, y in zip(a, b, strict=True))


def _leans(ratings: Sequence[tuple[PlaceFacts, float]], mean: float) -> Mapping[str, float]:
    """For each rated cuisine, `n / (n + 1)` times its average's distance from the overall one."""
    by_cuisine: dict[str, list[float]] = {}
    for facts, rating in ratings:
        for cuisine in dict.fromkeys(facts.cuisines):
            if cuisine in CUISINE_IDS:
                by_cuisine.setdefault(cuisine, []).append(float(rating))
    return MappingProxyType(
        {
            cuisine: len(rs) / (len(rs) + LEAN_SHRINK) * (math.fsum(rs) / len(rs) - mean)
            for cuisine, rs in sorted(by_cuisine.items())
        }
    )


def newest_per_place(ratings: Sequence[RatedPlace]) -> list[RatedPlace]:
    """Each place once, by the first of its ratings, which is the newest in the newest-first
    order `Visits.taste_ratings` reads them in, so repeat visits never count as more places."""
    seen: set[tuple[str, str]] = set()
    kept: list[RatedPlace] = []
    for r in ratings:
        if r.place not in seen:
            seen.add(r.place)
            kept.append(r)
    return kept


def _fingerprint(ratings: Sequence[RatedPlace]) -> str:
    """Changes whenever any counted rating, its place's features, or the set of them changes."""
    digest = hashlib.sha256(POLICY_VERSION.encode())
    for r in ratings:
        f = r.facts
        digest.update(
            repr(
                (str(r.visit_id), r.place, str(r.rating), f.cuisines, f.diets, f.hours_known)
            ).encode()
        )
    return digest.hexdigest()


class TasteModels:
    """Fits each owner's model lazily and keeps a few in memory.

    A cached model is reused only while the owner's ratings are exactly the ones it was fitted on,
    so a new, edited or deleted rating refits it on the next read in any process. Each place counts
    once, by its newest rating, and an owner with fewer than `MIN_RATINGS` distinct rated places has
    no model, and anything cached for them is dropped.
    """

    def __init__(
        self,
        ratings_of: Callable[[UUID], Sequence[RatedPlace]],
        *,
        capacity: int = CACHE_OWNERS,
    ) -> None:
        self._ratings_of = ratings_of
        self._capacity = capacity
        self._lock = threading.Lock()
        self._cache: OrderedDict[UUID, tuple[str, TasteModel]] = OrderedDict()

    def for_owner(self, user_id: UUID) -> TasteModel | None:
        ratings = newest_per_place(self._ratings_of(user_id))
        if len(ratings) < MIN_RATINGS:
            self.forget(user_id)
            return None
        key = _fingerprint(ratings)
        with self._lock:
            cached = self._cache.get(user_id)
            if cached is not None and cached[0] == key:
                self._cache.move_to_end(user_id)
                return cached[1]
        model = fit([(r.facts, float(r.rating)) for r in ratings])
        with self._lock:
            self._cache[user_id] = (key, model)
            self._cache.move_to_end(user_id)
            while len(self._cache) > self._capacity:
                self._cache.popitem(last=False)
        return model

    def forget(self, user_id: UUID) -> None:
        """Drop the owner's cached model, for example when their account is deleted."""
        with self._lock:
            self._cache.pop(user_id, None)

    def cached(self, user_id: UUID) -> bool:
        with self._lock:
            return user_id in self._cache
