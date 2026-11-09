"""Pre-registered synthetic owner histories for requalifying Clef state version 2.

Every case is made up and built through the production path: an in-memory visit store, the real
`Visits.taste_history` owner-only evidence builder, and `makan.profile_fit.profile_state`. So the
qualification sends exactly the states a diner would send, and nothing here is personal data.
Expected directions are a first draft for the project owner to review, not human diner labels.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from makan.accounts import InMemoryAccountStore
from makan.cuisines import place_cuisines
from makan.fit import CLEF_CUISINES, cuisine_fit
from makan.fit_inputs import FitInputs
from makan.profile_fit import profile_state
from makan.visits import DishInput, PlaceRef, Visits
from makan.visits.evidence import policy, vocabulary

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
RELEASE = "overture:2026-09-23.1"
CUISINES = CLEF_CUISINES
Expected = Literal["high", "low", "uncertain"]


@dataclass(frozen=True)
class Meal:
    cuisine: str
    rating: float
    dishes: tuple[tuple[str, float, tuple[str, ...]], ...]
    days_ago: int = 30
    place: str = ""  # a fixed place id, so the candidate can be a place the owner visited


@dataclass(frozen=True)
class HistoryCase:
    id: str
    pattern: str
    expected: Expected
    likes: tuple[str, ...]
    dislikes: tuple[str, ...]
    candidate: str  # cuisine
    meals: tuple[Meal, ...]
    candidate_place: str = ""


def _dishes(cuisine: str) -> tuple[str, str]:
    """Two fixed dish names of one cuisine, taken from the frozen vocabulary."""
    words = vocabulary()
    ids = sorted(d for d, c in words.dish_cuisines.items() if c == cuisine)[:2]
    names = {d: policy()["dishes"][d]["names"][0] for d in ids}
    return names[ids[0]], names[ids[1]]


def _other(cuisine: str) -> str:
    return CUISINES[(CUISINES.index(cuisine) + 1) % len(CUISINES)]


def _meal(cuisine: str, rating: float, dish_rating: float, **kwargs: Any) -> Meal:
    a, b = _dishes(cuisine)
    return Meal(cuisine, rating, ((a, dish_rating, ()), (b, dish_rating, ())), **kwargs)


def history_cases() -> tuple[HistoryCase, ...]:
    """The fixed pre-registered set, in order. Changing it needs a new pre-registration."""
    cases: list[HistoryCase] = []
    for cuisine in CUISINES:
        other = _other(cuisine)
        loved = (_meal(cuisine, 9, 9), _meal(cuisine, 8.5, 9), _meal(other, 5, 5))
        hated = (_meal(cuisine, 3, 2), _meal(cuisine, 3.5, 2.5), _meal(other, 8, 8))
        cases.append(
            HistoryCase(f"H-{cuisine}-loved", "history_like", "high", (), (), cuisine, loved)
        )
        cases.append(
            HistoryCase(f"H-{cuisine}-hated", "history_dislike", "low", (), (), cuisine, hated)
        )
    for cuisine in CUISINES[:4]:
        other = _other(cuisine)
        hated = (_meal(cuisine, 3, 2), _meal(cuisine, 3.5, 2.5), _meal(other, 8, 8))
        cases.append(
            HistoryCase(
                f"H-{cuisine}-stated-like-rated-low",
                "stated_like_history_dislike",
                "uncertain",
                (cuisine,),
                (),
                cuisine,
                hated,
            )
        )
    thai, korean = "thai", "korean"
    cases += [
        HistoryCase(
            "H-this-place-loved",
            "this_place_like",
            "high",
            (),
            (),
            thai,
            (_meal(thai, 9.5, 7, place="here"), _meal(korean, 5, 7), _meal(korean, 5.5, 7)),
            "here",
        ),
        HistoryCase(
            "H-this-place-hated",
            "this_place_dislike",
            "low",
            (),
            (),
            thai,
            (_meal(thai, 2, 7, place="here"), _meal(korean, 7, 7), _meal(korean, 7.5, 7)),
            "here",
        ),
        HistoryCase(
            "H-mixed-ratings",
            "conflicting_history",
            "uncertain",
            (),
            (),
            thai,
            (_meal(thai, 9, 10), _meal(thai, 2, 1), _meal(korean, 6, 5.5)),
        ),
        HistoryCase(
            "H-faded-dislike",
            "faded_history_dislike",
            "uncertain",
            (),
            (),
            thai,
            (_meal(thai, 2, 2, days_ago=4 * 365), _meal(korean, 7, 7), _meal(korean, 7.5, 7.5)),
        ),
        HistoryCase(
            "H-negated-tags",
            "negated_tags",
            "uncertain",
            ("thai",),
            (),
            thai,
            (
                Meal(thai, 7, (("pad thai", 9, ("not spicy",)), ("tom yum", 3, ("spicy",)))),
                _meal(korean, 6, 6),
            ),
        ),
    ]
    return tuple(cases)


def build(case: HistoryCase) -> dict[str, Any]:
    """The version 2 state and its version 1 counterpart, through the production builders."""
    store = InMemoryAccountStore()
    owner = uuid5(NAMESPACE_URL, f"makan-history-case:{case.id}")
    store.create_user(owner)
    visits = Visits(store.visits, store, clock=lambda: NOW)
    for index, meal in enumerate(case.meals):
        place = meal.place or f"syn-{case.id}-{index}"
        visits.log_visit(
            owner,
            place=PlaceRef("overture", place, f"Synthetic {index}"),
            rating=meal.rating,
            party="solo",
            visited_on=NOW.date() - timedelta(days=meal.days_ago),
            dishes=tuple(DishInput(name, rating, list(tags)) for name, rating, tags in meal.dishes),
        )
    history = visits.taste_history(owner)
    category = f"{case.candidate}_restaurant"
    inputs = FitInputs(case.likes, case.dislikes, history=history)
    state = profile_state(inputs, category, RELEASE, case.candidate_place)
    plain = profile_state(FitInputs(case.likes, case.dislikes), category, RELEASE)
    if state is None or "history" not in state:
        raise ValueError(f"history case {case.id} did not build a version 2 state")
    local = cuisine_fit(place_cuisines(category), case.likes, case.dislikes, enabled=True)
    return {
        "state": state,
        "plain": plain if (case.likes or case.dislikes) else None,
        "local_score": local.score,
        "local_reason": local.reason.value,
    }


def history_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Direction and coverage per pattern, and against each case's version 1 counterpart."""
    ok = [r for r in rows if r["lane"] == "history" and r.get("assessment") is not None]
    plain = {r["case"]: r for r in rows if r["lane"] == "history_plain" and r.get("assessment")}
    by_pattern: dict[str, dict[str, Any]] = {}
    for row in ok:
        entry = by_pattern.setdefault(
            row["meta"]["pattern"],
            {"cases": 0, "rated": 0, "scores": [], "modal_choices": Counter()},
        )
        entry["cases"] += 1
        score = row["assessment"]["score"]
        entry["rated"] += score is not None
        entry["scores"].append(score)
        entry["modal_choices"][row["taste_choice"]] += 1
    for entry in by_pattern.values():
        entry["modal_choices"] = dict(entry["modal_choices"])

    def wrong(row: Mapping[str, Any]) -> bool:
        score, expected = row["assessment"]["score"], row["meta"]["expected"]
        return score is not None and (
            (expected == "high" and score < 50) or (expected == "low" and score >= 50)
        )

    directional = [r for r in ok if r["meta"]["expected"] in ("high", "low")]
    uncertain = [r for r in ok if r["meta"]["expected"] == "uncertain"]
    return {
        "by_pattern": by_pattern,
        "directional_cases": len(directional),
        "directional_rated": sum(r["assessment"]["score"] is not None for r in directional),
        "direction_contradictions": [r["case"] for r in ok if wrong(r)],
        "uncertain_cases": len(uncertain),
        "uncertain_rated_strongly": [
            r["case"]
            for r in uncertain
            if r["assessment"]["score"] is not None and not 25 <= r["assessment"]["score"] <= 75
        ],
        "versus_version_1": {
            case: {"version_1": plain[case]["assessment"], "version_2": row["assessment"]}
            for row in ok
            if (case := row["case"]) in plain
        },
        "gates": "not pre-registered: commit gates under tests/fixtures/clef/live/ before a run",
    }
