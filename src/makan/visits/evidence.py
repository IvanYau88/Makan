"""Deterministic owner-only taste evidence from a person's own visits and dishes.

This is plain arithmetic over rows the caller already proved belong to one owner. It knows
nothing about any hosted scorer: it returns bounded, allowlisted summaries that a state builder
may later encode. The frozen rules live in `history_policy.json`, versioned and hash-checked.

- Only the owner's own ratings count: a restaurant rating they entered or confirmed, and their own
  dish rows. A visit made from an accepted tag has no rating until its owner gives one, and the
  tagger's visit, dishes, tags and notes are never read here.
- Every rating is read against the owner's own average for its kind, never as an absolute score.
- Dish names and tags leave this module only as canonical ids from a closed vocabulary. The
  typed text, notes, companions, dates, addresses, names and ids never do.
- A history too long to read whole is unusable rather than truncated, because the unread part
  could hold a dislike.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import cache
from importlib import resources
from typing import Any, Literal
from uuid import UUID

from makan.models import Visit, VisitDish

Status = Literal["ok", "empty", "too_long", "material_omission"]
MATERIAL = ("below", "well_below", "mixed")  # leans that may never be dropped silently
Bucket = Literal["none", "one", "few", "many"]
LEANS = ("well_below", "below", "near", "above", "well_above", "mixed")
STRENGTHS = ("faint", "some", "strong")


@cache
def policy() -> Mapping[str, Any]:
    """The frozen policy data, read once; a change needs a new version and qualification."""
    text = resources.files("makan.visits").joinpath("history_policy.json").read_text("utf-8")
    data: Mapping[str, Any] = json.loads(text)
    return data


def policy_sha256() -> str:
    raw = resources.files("makan.visits").joinpath("history_policy.json").read_bytes()
    return hashlib.sha256(raw).hexdigest()


def normalize(text: str) -> str:
    """NFKD without combining marks, case folded, with non-alphanumeric runs as single spaces."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in plain).split())


@dataclass(frozen=True)
class Vocabulary:
    dish_aliases: tuple[tuple[str, str], ...]  # (normalized alias, dish id), longest first
    dish_cuisines: Mapping[str, str]
    tag_aliases: Mapping[str, str]  # normalized whole tag -> tag id, negations included
    cuisine_tags: Mapping[str, str]  # normalized whole tag -> cuisine

    def dish(self, name: str) -> str | None:
        """The one dish named in `name` as whole words, or None when none or several are.

        An alias inside a longer matched alias does not count, so "spaghetti carbonara" is
        carbonara, while "pho and pad thai" names two dishes and matches nothing.
        """
        padded = f" {normalize(name)} "
        matched: list[tuple[str, str]] = []
        for alias, dish in self.dish_aliases:  # longest first
            if f" {alias} " in padded and not any(f" {alias} " in f" {m} " for m, _ in matched):
                matched.append((alias, dish))
        found = {dish for _, dish in matched}
        return next(iter(found)) if len(found) == 1 else None


@cache
def vocabulary() -> Vocabulary:
    data = policy()
    cuisines = set(data["cuisines"])
    aliases: dict[str, str] = {}
    dish_cuisines: dict[str, str] = {}
    for dish, spec in data["dishes"].items():
        if spec["cuisine"] not in cuisines:
            raise ValueError("dish of an unknown cuisine")
        dish_cuisines[dish] = spec["cuisine"]
        for name in spec["names"]:
            key = normalize(name)
            if key != name or aliases.setdefault(key, dish) != dish:
                raise ValueError("ambiguous or unnormalized dish alias")
    tags: dict[str, str] = {}
    prefix = data["negation_prefix"]
    for tag, names in data["tags"].items():
        for name in names:
            for key, value in ((name, tag), (prefix + name, "not_" + tag)):
                if normalize(key) != key or tags.setdefault(key, value) != value:
                    raise ValueError("ambiguous or unnormalized tag alias")
    cuisine_tags: dict[str, str] = {}
    for cuisine, names in data["cuisine_tags"].items():
        for name in names:
            if name in tags or cuisine_tags.setdefault(name, cuisine) != cuisine:
                raise ValueError("ambiguous cuisine tag")
    ordered = tuple(sorted(aliases.items(), key=lambda item: (-len(item[0]), item[0])))
    return Vocabulary(ordered, dish_cuisines, tags, cuisine_tags)


def tag_ids() -> tuple[str, ...]:
    return tuple(sorted(set(vocabulary().tag_aliases.values())))


def dish_ids() -> tuple[str, ...]:
    return tuple(sorted(vocabulary().dish_cuisines))


@dataclass(frozen=True)
class Summary:
    """One attribute's evidence: its weighted lean against the owner's averages, and weight."""

    lean: str
    strength: str


@dataclass(frozen=True)
class PlaceEvidence:
    """The owner's own visits at one place, kept server-side and matched to a candidate."""

    visits: Bucket
    lean: str  # a lean, or "unrated" when no visit there has the owner's own rating


@dataclass(frozen=True)
class TasteHistory:
    """Bounded, scorer-neutral summaries. Place keys never leave the server."""

    status: Status
    rated_visits: Bucket = "none"
    rated_dishes: Bucket = "none"
    cuisines: tuple[tuple[str, str, Summary], ...] = ()  # (cuisine, "visits"|"dishes", summary)
    dishes: tuple[tuple[str, Summary], ...] = ()  # every recognized dish, sorted by id
    tags: tuple[tuple[str, Summary], ...] = ()
    places: tuple[tuple[tuple[str, str], PlaceEvidence], ...] = ()
    revision: str = ""

    @property
    def usable(self) -> bool:
        return self.status == "ok"

    def has_attributes(self) -> bool:
        return bool(self.cuisines or self.dishes or self.tags)

    def place(self, source: str, place_id: str) -> PlaceEvidence | None:
        family = source.split(":", 1)[0].strip().lower()
        return dict(self.places).get((family, place_id))

    def dishes_of(self, cuisine: str) -> tuple[tuple[str, Summary], ...]:
        cuisines = vocabulary().dish_cuisines
        return tuple(item for item in self.dishes if cuisines[item[0]] == cuisine)


EMPTY = TasteHistory("empty", revision="empty")
TOO_LONG = TasteHistory("too_long", revision="too_long")
OMITTED = TasteHistory("material_omission", revision="material_omission")


def bucket(count: int) -> Bucket:
    spec = policy()["count_buckets"]
    if count >= spec["many"]:
        return "many"
    if count >= spec["few"]:
        return "few"
    return "one" if count >= spec["one"] else "none"


def summarize(items: Sequence[tuple[float, float]]) -> Summary:
    """Weighted (delta, weight) evidence as a lean and a strength, under the frozen policy."""
    data = policy()
    total = math.fsum(w for _, w in items)
    mean = math.fsum(d * w for d, w in items) / total
    margin, dominance = data["mixed"]["margin"], data["mixed"]["dominance"]
    high = math.fsum(w for d, w in items if d >= margin)
    low = math.fsum(w for d, w in items if d <= -margin)
    lean_rule = data["lean"]
    if high > 0 and low > 0 and max(high, low) < dominance * min(high, low):
        lean = "mixed"
    elif mean >= lean_rule["well_above"]:
        lean = "well_above"
    elif mean >= lean_rule["above"]:
        lean = "above"
    elif mean <= lean_rule["well_below"]:
        lean = "well_below"
    elif mean <= lean_rule["below"]:
        lean = "below"
    else:
        lean = "near"
    strength_rule = data["strength"]
    strength = (
        "strong"
        if total >= strength_rule["strong"]
        else "some"
        if total >= strength_rule["some"]
        else "faint"
    )
    return Summary(lean, strength)


def fade(visited_on: date, today: date) -> float:
    age = max(0, (today - visited_on).days)
    return float(0.5 ** (age / policy()["fading"]["half_life_days"]))


def context(dish_delta: float, restaurant_delta: float | None) -> float:
    rule = policy()["dish_context"]
    margin = rule["margin"]
    if restaurant_delta is None or abs(dish_delta) < margin or abs(restaurant_delta) < margin:
        return float(rule["neutral"])
    same = (dish_delta > 0) == (restaurant_delta > 0)
    return float(rule["follows_restaurant"] if same else rule["stands_apart"])


def _mean(values: Iterable[Decimal]) -> float | None:
    found = list(values)
    return float(sum(found) / len(found)) if found else None


def owner_history(
    owner: UUID,
    visits: Sequence[Visit],
    dishes: Sequence[VisitDish],
    today: date,
    *,
    complete: bool = True,
) -> TasteHistory:
    """Summarize one owner's rows. Rows of anyone else are ignored, never counted.

    `complete` is False when the caller could not read the whole history within the policy's
    read limit, which makes the evidence unusable instead of silently partial.
    """
    if not complete:
        return TOO_LONG
    own = [v for v in visits if v.user_id == owner]
    if len(own) > policy()["read_limit_visits"]:
        return TOO_LONG
    by_visit = {v.id: v for v in own}
    rated = [v for v in own if _own_rating(v) is not None]
    own_dishes = [
        d
        for d in dishes
        if d.user_id == owner
        and d.visit_id in by_visit
        and d.rating_origin in ("fresh", "confirmed")
    ]
    if not rated and not own_dishes:
        return EMPTY
    words = vocabulary()
    restaurant_avg = _mean(r for v in rated if (r := _own_rating(v)) is not None)
    dish_avg = _mean(d.rating for d in own_dishes)

    restaurant_delta: dict[UUID, float] = {}
    for v in rated:
        rating = _own_rating(v)
        assert rating is not None and restaurant_avg is not None
        restaurant_delta[v.id] = float(rating) - restaurant_avg

    cuisine_items: dict[tuple[str, str], list[tuple[float, float]]] = {}
    dish_items: dict[str, list[tuple[float, float]]] = {}
    tag_items: dict[str, list[tuple[float, float]]] = {}
    visit_cuisines: dict[UUID, set[str | None]] = {}
    for dish in own_dishes:
        assert dish_avg is not None
        visit = by_visit[dish.visit_id]
        delta = float(dish.rating) - dish_avg
        weight = fade(visit.visited_on, today) * context(delta, restaurant_delta.get(visit.id))
        dish_id = words.dish(dish.name)
        tagged = {t for raw in dish.tags if (t := words.cuisine_tags.get(normalize(raw)))}
        cuisines = {words.dish_cuisines[dish_id]} if dish_id else set()
        cuisines |= tagged
        cuisine = next(iter(cuisines)) if len(cuisines) == 1 else None
        if dish_id is not None and cuisine is not None:
            dish_items.setdefault(dish_id, []).append((delta, weight))
        if cuisine is not None:
            cuisine_items.setdefault((cuisine, "dishes"), []).append((delta, weight))
        if dish_id is not None or tagged:
            visit_cuisines.setdefault(visit.id, set()).add(cuisine)
        for tag in sorted({t for raw in dish.tags if (t := words.tag_aliases.get(normalize(raw)))}):
            tag_items.setdefault(tag, []).append((delta, weight))

    for visit_id, found in visit_cuisines.items():
        if len(found) == 1 and None not in found and visit_id in restaurant_delta:
            (cuisine,) = found
            assert cuisine is not None
            weight = fade(by_visit[visit_id].visited_on, today)
            cuisine_items.setdefault((cuisine, "visits"), []).append(
                (restaurant_delta[visit_id], weight)
            )

    place_visits: dict[tuple[str, str], list[Visit]] = {}
    for v in own:
        place_visits.setdefault((v.place_source, v.place_id), []).append(v)
    places = []
    for key, at in sorted(place_visits.items()):
        items = [
            (restaurant_delta[v.id], fade(v.visited_on, today))
            for v in at
            if v.id in restaurant_delta
        ]
        lean = summarize(items).lean if items else "unrated"
        places.append((key, PlaceEvidence(bucket(len(at)), lean)))

    tags = _select_tags(tag_items)
    if tags is None:
        return OMITTED
    history = TasteHistory(
        "ok",
        rated_visits=bucket(len(rated)),
        rated_dishes=bucket(len(own_dishes)),
        cuisines=tuple(
            (cuisine, source, summarize(items))
            for (cuisine, source), items in sorted(cuisine_items.items())
        ),
        dishes=tuple((dish, summarize(items)) for dish, items in sorted(dish_items.items())),
        tags=tags,
        places=tuple(places),
    )
    digest = hashlib.sha256(
        json.dumps(
            [policy()["version"], _plain(history)], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return dataclasses.replace(history, revision=digest)


def _select_tags(
    items: Mapping[str, Sequence[tuple[float, float]]],
) -> tuple[tuple[str, Summary], ...] | None:
    """At most max_tags summaries, dislikes and conflicts first; None if one would be dropped."""
    ranked = sorted(
        ((tag, summarize(found), math.fsum(w for _, w in found)) for tag, found in items.items()),
        key=lambda item: (item[1].lean not in MATERIAL, -item[2], item[0]),
    )
    limit = policy()["max_tags"]
    if any(summary.lean in MATERIAL for _, summary, _ in ranked[limit:]):
        return None
    return tuple(sorted((tag, summary) for tag, summary, _ in ranked[:limit]))


def _own_rating(visit: Visit) -> Decimal | None:
    """A restaurant rating counts only when the owner entered or confirmed it."""
    if visit.rating is None or visit.rating_origin not in ("fresh", "confirmed"):
        return None
    return visit.rating


def _plain(history: TasteHistory) -> Any:
    return [
        history.status,
        history.rated_visits,
        history.rated_dishes,
        [[c, s, x.lean, x.strength] for c, s, x in history.cuisines],
        [[d, x.lean, x.strength] for d, x in history.dishes],
        [[t, x.lean, x.strength] for t, x in history.tags],
        [[list(k), p.visits, p.lean] for k, p in history.places],
    ]
