"""The group consensus workflow: one place the whole group can agree on.

`build_group_graph` is a graph in the style of `makan.solo`. It reuses the solo research steps to
get the candidate options for the session's request, and the rest follows the Group consensus
section of docs/DESIGN.md: collect each person's inputs, apply hard constraints, score the
remaining options per person, pick by least misery, and explain. The scoring and the pick are the
pure functions in `makan.consensus`, so the steps only fetch, call, and word the result.

Call `recommend_group` with a stored session and its participants to run it. The workflow never
claims an option is safe: allergies, diets, and budgets cannot be checked against the places data,
so each is a warning against the pick and every runner-up.
"""

from __future__ import annotations

from dataclasses import dataclass

from makan.config import Config
from makan.consensus import (
    Consensus,
    Constraints,
    Exclusion,
    Member,
    Preferences,
    Scored,
    apply_constraints,
    floor_score,
    order_options,
    parse_constraints,
    parse_preferences,
    score_options,
    shared_unverified_warnings,
    unverified_warnings,
)
from makan.graph import Graph, GraphResult, Step, StepContext, run_graph
from makan.models import Participant, Session
from makan.places.base import PlacesProvider, distance_label
from makan.providers.base import Provider
from makan.solo import Candidate, Research, research_steps
from makan.trace import TraceSink

RUNNERS_UP = 3  # as in the solo recommendation
LISTED_EXCLUSIONS = 5  # how many excluded places the explanation names

_REFUSAL_NOTE = (
    "Refused categories are checked against each place's primary category only, "
    "so a place listed under a broader category may still serve what someone refuses."
)


@dataclass(frozen=True)
class GroupInput:
    """What the graph runs on: the session and everyone in it.

    Its repr leaves out the participant rows, so trace events never hold a participant id.
    """

    session: Session
    participants: tuple[Participant, ...]

    def __repr__(self) -> str:
        return f"GroupInput(session={self.session.id}, participants={len(self.participants)})"


@dataclass(frozen=True)
class RankedOption:
    place: Candidate
    reasons: tuple[str, ...]
    minimum: float | None  # the lowest score anyone gave it, which the pick is made on
    average: float | None  # both are None when nobody shared a taste, so no one scored it
    least_happy: tuple[str, ...]  # who gave it the minimum
    warnings: tuple[str, ...]  # constraints that could not be verified for this option, by person
    shared_warnings: tuple[str, ...]  # the same, with nobody named, for the rest of the group


@dataclass(frozen=True)
class GroupRecommendation:
    pick: RankedOption | None
    runners_up: tuple[RankedOption, ...]
    excluded: tuple[Exclusion, ...]
    explanation: str  # for the host: names a person next to what they shared
    shared_explanation: str  # for the rest of the group: says what was shared, never by whom
    warnings: tuple[str, ...]  # about the whole search, not one option
    data_source: str
    participant_count: int
    pending: tuple[str, ...]  # who has not shared anything yet
    uncounted: tuple[str, ...]  # who shared no taste, so is left out of the scores, pending or not


@dataclass(frozen=True)
class GroupResult:
    graph: GraphResult
    recommendation: GroupRecommendation | None


@dataclass(frozen=True)
class _Filtered:
    kept: tuple[Candidate, ...]
    excluded: tuple[Exclusion, ...]


def labelled_members(
    participants: tuple[Participant, ...] | list[Participant],
) -> tuple[tuple[Participant, Member], ...]:
    """Pair stored participants with people of unique names: the host first, then by join time.

    A person with no display name is "Guest" and their place in the join order, and a name that
    repeats gets its count after it, so scores can always be told apart. Nothing of the order the
    rows arrived in leaks through, because they are sorted first.
    """
    ordered = sorted(participants, key=lambda p: (not p.is_host, p.joined_at, p.id))
    names: list[str] = []
    for index, participant in enumerate(ordered, start=1):
        base = participant.display_name or f"Guest {index}"
        name, n = base, 1
        while name in names:
            n += 1
            name = f"{base} ({n})"
        names.append(name)
    return tuple(
        (participant, _member(name, participant))
        for name, participant in zip(names, ordered, strict=True)
    )


def members_of(participants: tuple[Participant, ...] | list[Participant]) -> tuple[Member, ...]:
    return tuple(member for _, member in labelled_members(participants))


def _member(name: str, participant: Participant) -> Member:
    if not participant.constraints and not participant.preferences:
        return Member(name, Constraints(), Preferences(), submitted=False)
    return Member(
        name,
        parse_constraints(participant.constraints),
        parse_preferences(participant.preferences),
    )


def build_group_graph(*, provider: Provider, places: PlacesProvider, config: Config) -> Graph:
    """Research the options, then collect, filter, score, pick, and explain.

    Input is a `GroupInput`. No stored memory is read: a group session uses only what each person
    chose to share in it, never their history. The places provider must support concurrent
    searches (the built-in providers do).
    """

    def collect(ctx: StepContext) -> tuple[Member, ...]:
        group: GroupInput = ctx.input
        return members_of(group.participants)

    def apply_hard_constraints(ctx: StepContext) -> _Filtered:
        research: Research = ctx.inputs["merge"].unwrap()
        members: tuple[Member, ...] = ctx.inputs["participants"].unwrap()
        kept, excluded = apply_constraints(research.candidates, members)
        return _Filtered(kept, excluded)

    def score(ctx: StepContext) -> tuple[Scored, ...]:
        group: GroupInput = ctx.input
        filtered: _Filtered = ctx.inputs["constraints"].unwrap()
        members: tuple[Member, ...] = ctx.inputs["participants"].unwrap()
        research: Research = ctx.inputs["merge"].unwrap()
        requested = sum(bool(t) for t in (research.intent.cuisine, research.intent.category))
        return score_options(filtered.kept, members, group.session.context["radius_m"], requested)

    def pick(ctx: StepContext) -> tuple[Scored, ...]:
        return order_options(ctx.inputs["score"].unwrap())

    def explain(ctx: StepContext) -> GroupRecommendation:
        research: Research = ctx.inputs["merge"].unwrap()
        members: tuple[Member, ...] = ctx.inputs["participants"].unwrap()
        filtered: _Filtered = ctx.inputs["constraints"].unwrap()
        ranked: tuple[Scored, ...] = ctx.inputs["pick"].unwrap()
        return _recommendation(
            research,
            members,
            Consensus(ranked, filtered.excluded),
            nearby=len(research.candidates),
            source=places.name,
        )

    return Graph(
        "group_consensus",
        [
            *research_steps(
                provider=provider,
                places=places,
                config=config,
                session_of=lambda ctx: ctx.input.session,
            ),
            Step("participants", collect),
            Step("constraints", apply_hard_constraints, after=("merge", "participants")),
            Step("score", score, after=("merge", "constraints", "participants")),
            Step("pick", pick, after=("score",)),
            Step("explain", explain, after=("merge", "participants", "constraints", "pick")),
        ],
    )


def recommend_group(
    session: Session,
    participants: list[Participant] | tuple[Participant, ...],
    *,
    provider: Provider,
    places: PlacesProvider,
    config: Config,
    sink: TraceSink | None = None,
) -> GroupResult:
    """Run the group graph for a stored session and its participants.

    The result carries the graph evidence, as `makan.solo.recommend` does: a failed search can
    give a partial recommendation with warnings, and a failed classification or both searches
    failing give none.
    """
    graph = run_graph(
        build_group_graph(provider=provider, places=places, config=config),
        GroupInput(session, tuple(participants)),
        limits=config.graph_limits,
        sink=sink,
    )
    final = graph.results["explain"]
    return GroupResult(graph, final.value if final.ok else None)


def _recommendation(
    research: Research,
    members: tuple[Member, ...],
    result: Consensus,
    *,
    nearby: int,
    source: str,
) -> GroupRecommendation:
    unverified = unverified_warnings(members)
    shared_unverified = shared_unverified_warnings(members)
    pending = tuple(m.name for m in members if not m.submitted)
    uncounted = tuple(m.name for m in members if not m.has_taste)
    warnings = list(research.warnings)
    if any(m.constraints.refuses for m in members):
        warnings.append(_REFUSAL_NOTE)
    warnings.extend(f"{name} has not shared anything yet." for name in pending)

    options = tuple(_option(s, len(members), unverified, shared_unverified) for s in result.ranked)
    pick, runners_up = (options[0] if options else None), options[1 : 1 + RUNNERS_UP]

    def explain(*, named: bool) -> str:
        parts: list[str] = []
        if pick is not None:
            parts.append(f"Try {pick.place.name}: {'; '.join(pick.reasons)}.")
            parts.append(_least_misery(pick, named))
            if runners_up:
                listed = "; ".join(_runner_up(o, named) for o in runners_up)
                parts.append(f"Runners-up: {listed}.")
        elif nearby == 0:
            parts.append("No places found within this search radius.")
        else:
            parts.append(
                f"No place is left after hard constraints: all {nearby} nearby places match a "
                "category someone refuses, so Makan has nothing to recommend. "
                "Ask them to relax a refusal, or search a wider radius."
            )
        if result.excluded:
            shown = result.excluded[:LISTED_EXCLUSIONS]
            text = "; ".join(f"{e.place.name} ({_refusals(e, named)})" for e in shown)
            more = len(result.excluded) - len(shown)
            parts.append(
                f"Excluded before scoring: {text}" + (f"; and {more} more." if more else ".")
            )
        if uncounted:
            if named:
                parts.append(
                    f"Not counted in the scores, because they shared no taste preferences: "
                    f"{_join(uncounted)}. Their hard constraints still applied."
                )
            else:
                parts.append(
                    f"Not counted in the scores: {_count(len(uncounted))} who shared no taste "
                    "preferences. Their hard constraints still applied."
                )
        all_warnings = [*(unverified if named else shared_unverified), *warnings]
        if all_warnings:
            parts.append(" ".join(all_warnings))
        if source.startswith("overture:"):
            parts.append("Places data: Overture Maps Foundation (CDLA Permissive 2.0).")
        return " ".join(parts)

    return GroupRecommendation(
        pick,
        runners_up,
        result.excluded,
        explain(named=True),
        explain(named=False),
        tuple(warnings),
        source,
        len(members),
        pending,
        uncounted,
    )


def _option(
    scored: Scored, people: int, unverified: tuple[str, ...], shared_unverified: tuple[str, ...]
) -> RankedOption:
    place = scored.place
    reasons = [f"{distance_label(place.distance_m)} from the search point"]
    if place.request_fit:
        reasons.append(f"matches {place.request_fit} requested category filter(s)")
    else:
        reasons.append("nearby alternative; requested category match not confirmed")
    if scored.likes:
        reasons.append(f"liked by {scored.likes} of {people}")
    if scored.dislikes:
        reasons.append(f"disliked by {scored.dislikes} of {people}")
    counted = bool(scored.scores)
    return RankedOption(
        place,
        tuple(reasons),
        scored.minimum if counted else None,
        scored.average if counted else None,
        scored.least_happy,
        unverified,
        shared_unverified,
    )


def _least_misery(option: RankedOption, named: bool) -> str:
    if option.minimum is None or option.average is None:
        return (
            "Nobody shared a taste preference, so there are no scores to compare. "
            "This is the best match for the request, and then the nearest place."
        )
    low = f"{floor_score(option.minimum):.2f}"
    average = f"The group average is {option.average:.2f}."
    if not named:
        return f"Nobody scored it below {low} out of 1.00. {average}"
    verb = "is" if len(option.least_happy) == 1 else "are"
    return (
        f"{_join(option.least_happy)} {verb} least happy with it, scoring it {low} out of 1.00, "
        f"so nobody scored it below {low}. {average}"
    )


def _runner_up(option: RankedOption, named: bool) -> str:
    if option.minimum is None or option.average is None:
        return f"{option.place.name} ({distance_label(option.place.distance_m)})"
    low = f"{floor_score(option.minimum):.2f}"
    who = f" from {_join(option.least_happy)}" if named else ""
    return f"{option.place.name} (lowest score {low}{who}; average {option.average:.2f})"


def _count(people: int) -> str:
    return f"{people} {'person' if people == 1 else 'people'}"


def _join(names: tuple[str, ...]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _refusals(exclusion: Exclusion, named: bool) -> str:
    if named:
        return "; ".join(f"{r.member} refuses {r.term}" for r in exclusion.refusals)
    terms = sorted({r.term for r in exclusion.refusals})
    return f"refused by someone in the group: {', '.join(terms)}"
