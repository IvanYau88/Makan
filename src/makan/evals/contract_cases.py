"""Draft labelled cases for the taste-fit and intent-router decisions.

These are a first draft written by hand for the captain to review and grow. They are not a
certified benchmark, and their counts are a starting workload, not a statistical release gate.

The two sets live in `sets/contract/`, apart from the three decision sets that `load_cases`
reads. A line is one JSON object, and a line starting with `#` is a comment, which is how each file
carries its header. Annotation rules:

- The label is what a careful reader would decide from the text on its own, never what a backend
  answered. Where a reasonable reader could disagree the case says so with the `near_tie` or
  `ambiguous` tag, and `note` records why.
- A case feeds the scorer soft preferences and sourced soft facts only. Allergies, diets, budget
  ceilings, and travel bounds are written in separate fields (`hard` on a taste case, `spans` on a
  router case), which plain code uses and the renderers never read. Prices and distances are
  `display` fields for the same reason.
- A taste case is one user's stated likes and dislikes, their visits so far, and one candidate
  restaurant's dated facts. All visits happened before the decision, so no future rating leaks in.
  `habit` is that user's own average rating, written by hand, because the fixtures judge
  a rating against it and deriving the average is a later step.
- A router case is one message. `primary` is the reading that organizes it and `present` is every
  intent it carries. `action` is `commit` when one reading is clear, or `clarify` when it is
  truly ambiguous, and `clarify_between` names the readings that one question would separate.
- A user, a restaurant, or a paraphrase `family` appears on one split only, so nothing about it
  leaks across splits. The loader rejects a set that breaks this.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from makan.decisions import CONTRACT_DECISIONS, INTENT_KINDS, LIMIT_MARKERS
from makan.evals.cases import SPLITS, EvalCase

TASTE_FIT_LABELS = ("strong", "partial", "poor", "unknown")
PRIMARY_LABELS = (*INTENT_KINDS, "other")
ACTIONS = ("commit", "clarify")
RATING_MAX = 10.0
PRESENCE_PREFIX = "intent_presence."


@dataclass(frozen=True)
class SourcedFact:
    text: str
    source: str
    date: str | None  # as written on the source, or None when it carries no date


@dataclass(frozen=True)
class Dish:
    name: str
    rating: float  # out of 10, decimals allowed
    tags: tuple[str, ...] = ()  # the user's own words, kept literally


@dataclass(frozen=True)
class Visit:
    restaurant: str
    rating: float  # the 0 to 10 slider
    dishes: tuple[Dish, ...] = ()  # zero dishes is a valid visit


@dataclass(frozen=True)
class Habit:
    """The user's own average rating, for judging a rating against how they usually rate."""

    restaurant_mean: float | None = None
    dish_mean: float | None = None


@dataclass(frozen=True)
class HardFacts:
    """Hard constraints and the safety facts they apply to. Only plain code reads these."""

    allergies: tuple[str, ...] = ()
    diets: tuple[str, ...] = ()
    budget_ceiling: str | None = None
    travel_bound: str | None = None
    menu_mentions: tuple[SourcedFact, ...] = ()

    def terms(self) -> tuple[str, ...]:
        """Every literal string that must never reach a scorer."""
        return (
            *self.allergies,
            *self.diets,
            *([self.budget_ceiling] if self.budget_ceiling else []),
            *([self.travel_bound] if self.travel_bound else []),
        )


@dataclass(frozen=True)
class TasteFitCase:
    id: str
    split: str
    user: str
    restaurant: str  # the candidate's identity, for the split-leak check
    family: str
    tags: tuple[str, ...]
    likes: tuple[str, ...]
    dislikes: tuple[str, ...]
    habit: Habit
    visits: tuple[Visit, ...]
    candidate: str
    facts: tuple[SourcedFact, ...]
    unsure: tuple[str, ...]  # facts the candidate lacks, flagged unsure and never guessed
    label: str
    hard: HardFacts = field(default_factory=HardFacts)
    display: Mapping[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def state(self) -> str:
        return render_taste_state(self)

    def eval_case(self) -> EvalCase:
        return EvalCase(self.id, "taste_fit", self.state, self.label, self.split, self.tags)


@dataclass(frozen=True)
class HardSpan:
    text: str  # exactly as written in the message
    kind: str  # a key of LIMIT_MARKERS


@dataclass(frozen=True)
class RouterCase:
    id: str
    split: str
    user: str
    family: str
    tags: tuple[str, ...]
    message: str
    spans: tuple[HardSpan, ...]
    primary: str
    present: tuple[str, ...]
    action: str
    clarify_between: tuple[str, ...] = ()
    slots: Mapping[str, str] = field(default_factory=dict)
    note: str = ""

    @property
    def state(self) -> str:
        return soft_view(self.message, self.spans)

    def eval_case(self, decision: str) -> EvalCase:
        if decision == "primary_intent":
            label = self.primary
        elif decision.startswith(PRESENCE_PREFIX):
            label = (
                "present" if decision.removeprefix(PRESENCE_PREFIX) in self.present else "absent"
            )
        else:
            raise ValueError(f"{decision!r} is not a router decision")
        return EvalCase(self.id, decision, self.state, label, self.split, self.tags)


def soft_view(message: str, spans: tuple[HardSpan, ...]) -> str:
    """The message with each hard span replaced by a neutral marker, which is all a scorer sees.

    The marker says a limit was stated and of what general kind, never its content. A span that is
    missing from the message or appears twice is an error, so a stale annotation cannot silently
    leave the hard text in.
    """
    view = message
    for span in spans:
        if view.count(span.text) != 1:
            raise ValueError(f"hard span {span.text!r} must appear exactly once in the message")
        view = view.replace(span.text, LIMIT_MARKERS[span.kind])
    return view


def render_taste_state(case: TasteFitCase) -> str:
    """The deterministic scorer text for a taste case, built only from allowlisted soft fields."""
    lines = [
        f"Likes: {', '.join(case.likes) or 'none stated'}.",
        f"Dislikes: {', '.join(case.dislikes) or 'none stated'}.",
    ]
    if case.visits:
        lines.append("Visits so far, oldest first:")
        for visit in case.visits:
            lines.append(f"- {visit.restaurant}: restaurant {visit.rating:.1f}/10")
            for dish in visit.dishes:
                tags = f" [{', '.join(dish.tags)}]" if dish.tags else ""
                lines.append(f"  - dish {dish.name}: {dish.rating:.1f}/10{tags}")
            if not visit.dishes:
                lines.append("  - no dishes entered")
        averages = [
            f"{name} {value:.1f}/10"
            for name, value in (
                ("restaurants", case.habit.restaurant_mean),
                ("dishes", case.habit.dish_mean),
            )
            if value is not None
        ]
        if averages:
            lines.append(f"This person's own average ratings: {', '.join(averages)}.")
    else:
        lines.append("No visits yet, so judge on the stated likes and dislikes alone.")
    lines.append(f"Candidate: {case.candidate}")
    for fact in case.facts:
        lines.append(f"- {fact.text} ({fact.source}, {fact.date or 'undated'})")
    for missing in case.unsure:
        lines.append(f"- Unsure: {missing}")
    return "\n".join(lines)


def _read(name: str) -> list[tuple[int, dict[str, Any]]]:
    path = resources.files("makan.evals").joinpath("sets", "contract", f"{name}.jsonl")
    rows = []
    for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        if line.strip() and not line.startswith("#"):
            rows.append((number, json.loads(line)))
    return rows


def header(name: str) -> str:
    """The comment lines at the top of a contract set, without their `# `."""
    path = resources.files("makan.evals").joinpath("sets", "contract", f"{name}.jsonl")
    lines = path.read_text("utf-8").splitlines()
    return "\n".join(line.removeprefix("#").strip() for line in lines if line.startswith("#"))


def _fact(raw: Mapping[str, Any]) -> SourcedFact:
    return SourcedFact(raw["text"], raw["source"], raw.get("date"))


def _rating(value: Any, where: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not 0 <= value <= RATING_MAX
    ):
        raise ValueError(f"{where}: a rating must be a number from 0 to {RATING_MAX:g}")
    return float(value)


def _taste_case(number: int, raw: Mapping[str, Any]) -> TasteFitCase:
    where = f"taste_fit line {number}"
    habit = raw.get("habit") or {}
    hard = raw.get("hard") or {}
    case = TasteFitCase(
        id=raw["id"],
        split=raw["split"],
        user=raw["user"],
        restaurant=raw["restaurant"],
        family=raw["family"],
        tags=tuple(raw["tags"]),
        likes=tuple(raw["likes"]),
        dislikes=tuple(raw["dislikes"]),
        habit=Habit(
            *(
                None if habit.get(k) is None else _rating(habit[k], where)
                for k in ("restaurant_mean", "dish_mean")
            )
        ),
        visits=tuple(
            Visit(
                v["restaurant"],
                _rating(v["rating"], where),
                tuple(
                    Dish(d["name"], _rating(d["rating"], where), tuple(d.get("tags", ())))
                    for d in v.get("dishes", ())
                ),
            )
            for v in raw.get("visits", ())
        ),
        candidate=raw["candidate"],
        facts=tuple(_fact(f) for f in raw.get("facts", ())),
        unsure=tuple(raw.get("unsure", ())),
        label=raw["label"],
        hard=HardFacts(
            tuple(hard.get("allergies", ())),
            tuple(hard.get("diets", ())),
            hard.get("budget_ceiling"),
            hard.get("travel_bound"),
            tuple(_fact(m) for m in hard.get("menu_mentions", ())),
        ),
        display=dict(raw.get("display", {})),
        note=raw.get("note", ""),
    )
    if case.label not in TASTE_FIT_LABELS:
        raise ValueError(f"{where}: label {case.label!r} is not an option")
    if case.visits and case.habit.restaurant_mean is None:
        raise ValueError(f"{where}: a user with visits needs their own average restaurant rating")
    if any(v.dishes for v in case.visits) and case.habit.dish_mean is None:
        raise ValueError(f"{where}: a user with rated dishes needs their own average dish rating")
    return case


def _router_case(number: int, raw: Mapping[str, Any]) -> RouterCase:
    where = f"router line {number}"
    case = RouterCase(
        id=raw["id"],
        split=raw["split"],
        user=raw["user"],
        family=raw["family"],
        tags=tuple(raw["tags"]),
        message=raw["message"],
        spans=tuple(HardSpan(s["text"], s["kind"]) for s in raw.get("spans", ())),
        primary=raw["primary"],
        present=tuple(raw["present"]),
        action=raw["action"],
        clarify_between=tuple(raw.get("clarify_between", ())),
        slots=dict(raw.get("slots", {})),
        note=raw.get("note", ""),
    )
    if case.primary not in PRIMARY_LABELS:
        raise ValueError(f"{where}: primary {case.primary!r} is not an option")
    if not set(case.present) <= set(INTENT_KINDS):
        raise ValueError(f"{where}: present has an unknown intent")
    if case.primary != "other" and case.primary not in case.present:
        raise ValueError(f"{where}: the primary intent must also be present")
    if case.primary == "other" and case.present:
        raise ValueError(f"{where}: an 'other' message carries no named intent")
    if case.action not in ACTIONS:
        raise ValueError(f"{where}: unknown action {case.action!r}")
    if (case.action == "clarify") != bool(case.clarify_between):
        raise ValueError(f"{where}: clarify_between goes with action clarify, and only with it")
    if any(s.kind not in LIMIT_MARKERS for s in case.spans):
        raise ValueError(f"{where}: unknown hard span kind")
    case.state  # noqa: B018 - raises when a span does not match the message
    return case


def _check_set[C: (TasteFitCase, RouterCase)](
    name: str, cases: list[C], owners: tuple[str, ...]
) -> tuple[C, ...]:
    if len({c.id for c in cases}) != len(cases):
        raise ValueError(f"{name}: case ids must be unique")
    for case in cases:
        if case.split not in SPLITS:
            raise ValueError(f"{name}: {case.id} has unknown split {case.split!r}")
    for owner in owners:  # a user, restaurant, or family on two splits would leak the answer
        seen: dict[str, str] = {}
        for case in cases:
            key = getattr(case, owner)
            if seen.setdefault(key, case.split) != case.split:
                raise ValueError(f"{name}: {owner} {key!r} appears on more than one split")
    return tuple(cases)


def load_taste_cases() -> tuple[TasteFitCase, ...]:
    cases = [_taste_case(n, raw) for n, raw in _read("taste_fit")]
    checked = _check_set("taste_fit", cases, ("user", "restaurant", "family"))
    seen: dict[str, str] = {}  # a restaurant someone visited counts as much as the candidate
    for case in checked:
        for name in (case.candidate, *(v.restaurant for v in case.visits)):
            if seen.setdefault(name, case.split) != case.split:
                raise ValueError(f"taste_fit: restaurant {name!r} appears on more than one split")
    return checked


def load_router_cases() -> tuple[RouterCase, ...]:
    cases = [_router_case(n, raw) for n, raw in _read("router")]
    return _check_set("router", cases, ("user", "family"))


def contract_eval_cases(decision: str) -> tuple[EvalCase, ...]:
    """The cases of one contract decision as `EvalCase`s, with the labels that decision asks for.

    Each presence question gets its own present/absent label, so a message with several intents
    is present for each of them and absent for the rest.
    """
    if decision not in CONTRACT_DECISIONS:
        raise ValueError(f"unknown contract decision {decision!r}")
    if decision == "taste_fit":
        return tuple(c.eval_case() for c in load_taste_cases())
    return tuple(c.eval_case(decision) for c in load_router_cases())
