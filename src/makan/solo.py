"""Single-participant recommendations using only the data the places tool carries.

Build a reusable graph with `build_solo_graph`, or call `recommend` to create a session,
run the graph, and return its recommendation and failure evidence together.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from makan.config import Config
from makan.graph import Graph, GraphResult, Step, StepContext, StepFailed, run_graph
from makan.memory.content import claim
from makan.memory.context import TurnMemory, recall_for_turn
from makan.memory.gate import GateDecision, RetrievalGate, RuleGate
from makan.memory.service import Memory, RecalledFact, utc_now
from makan.memory.store import Owner
from makan.models import Participant, Session
from makan.places.base import MAX_LIMIT, Place, PlaceQuery, PlacesProvider
from makan.places.tool import search_nearby_places
from makan.providers.base import Provider
from makan.steps import agent_step, tool_step
from makan.trace import TraceSink


@dataclass(frozen=True)
class SoloRequest:
    latitude: float
    longitude: float
    request: str
    radius_m: int = 1000
    user_id: UUID | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        PlaceQuery.near(self.latitude, self.longitude, self.radius_m)
        if not isinstance(self.request, str) or not self.request.strip():
            raise ValueError("request must be non-empty text")


@dataclass(frozen=True)
class Intent:
    cuisine: str | None
    category: str | None
    requirements: tuple[str, ...]


@dataclass(frozen=True)
class Candidate:
    id: str
    name: str
    category: str
    distance_m: int
    address: str | None
    request_fit: int

    def matches(self, term: str) -> bool:
        # The tool exposes the primary category only, not the source's full taxonomy.
        return Place(self.id, self.name, self.category, (self.category,), 0, 0).matches(term)


@dataclass(frozen=True)
class Research:
    intent: Intent
    candidates: tuple[Candidate, ...]
    memory: TurnMemory
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class RankedCandidate:
    place: Candidate
    reasons: tuple[str, ...]
    memory_score: float


@dataclass(frozen=True)
class Recommendation:
    pick: RankedCandidate | None
    runners_up: tuple[RankedCandidate, ...]
    explanation: str
    warnings: tuple[str, ...]
    stale_facts: tuple[RecalledFact, ...]
    data_source: str


@dataclass(frozen=True)
class SoloResult:
    session: Session
    participant: Participant
    graph: GraphResult
    recommendation: Recommendation | None


_CLASSIFY = """Classify only the user's current request, never invent preferences.
Call finish with a JSON object containing exactly these keys:
- cuisine: a category-search term such as thai or ramen, or null
- category: a venue-search term such as cafe or bakery, or null
- requirements: an array of every additional requirement stated, including allergies,
  dietary restrictions, price, menus, reviews, and opening hours/open now.
Only cuisine and venue category can be checked with the available places data.
Do not treat an allergy or diet as proven by a cuisine label.
"""


def _intent(answer: str) -> Intent:
    raw = json.loads(answer)
    if not isinstance(raw, dict) or set(raw) != {"cuisine", "category", "requirements"}:
        raise ValueError("classification must contain cuisine, category, and requirements")
    for key in ("cuisine", "category"):
        value = raw[key]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{key} must be non-empty text or null")
    requirements = raw["requirements"]
    if not isinstance(requirements, list) or any(
        not isinstance(r, str) or not r.strip() for r in requirements
    ):
        raise ValueError("requirements must be an array of non-empty text")
    return Intent(
        raw["cuisine"].strip() if raw["cuisine"] else None,
        raw["category"].strip() if raw["category"] else None,
        tuple(r.strip() for r in requirements),
    )


def build_solo_graph(
    *,
    provider: Provider,
    places: PlacesProvider,
    config: Config,
    memory: Memory | None = None,
    gate: RetrievalGate | None = None,
) -> Graph:
    """Classify, search in parallel, merge, rank, and explain.

    The places provider must support concurrent searches (the built-in providers do).
    Input is a Session made by `recommend`; guests never read stored memory.
    """
    tool = search_nearby_places(places)
    retrieval_gate = gate or RuleGate()

    def arguments(ctx: StepContext, *, filtered: bool) -> dict[str, Any]:
        intent: Intent = ctx.inputs["intent"].unwrap()
        session: Session = ctx.input
        args = {k: session.context[k] for k in ("latitude", "longitude", "radius_m")}
        args["limit"] = MAX_LIMIT
        if filtered:
            if intent.cuisine:
                args["cuisine"] = intent.cuisine
            if intent.category:
                args["category"] = intent.category
        return args

    def recall(ctx: StepContext) -> TurnMemory:
        session: Session = ctx.input
        if memory is None or session.user_id is None:
            return TurnMemory(GateDecision(False, "no user memory"))
        return recall_for_turn(
            memory, retrieval_gate, Owner(user_id=session.user_id), session.context["request"]
        )

    def merge(ctx: StepContext) -> Research:
        intent: Intent = ctx.inputs["intent"].unwrap()
        warnings = [
            "Opening hours, menus, prices, and public reviews are unavailable; verify before going."
        ]
        warnings.extend(f"Cannot verify requirement: {r}." for r in intent.requirements)
        candidates: dict[str, Candidate] = {}
        successes = 0
        for name in ("requested_places", "nearby_places"):
            result = ctx.inputs[name]
            if not result.ok:
                warnings.append(f"{name} {result.status}: {result.error}")
                continue
            successes += 1
            for p in json.loads(result.unwrap())["places"]:
                candidate = Candidate(
                    p["id"], p["name"], p["category"], p["distance_m"], p.get("address"), 0
                )
                fit = sum(
                    bool(term) and (name == "requested_places" or candidate.matches(term))
                    for term in (intent.cuisine, intent.category)
                    if term
                )
                candidate = replace(candidate, request_fit=fit)
                # Filtered branch comes first, preserving full-taxonomy match evidence.
                candidates.setdefault(candidate.id, candidate)
        if not successes:
            raise StepFailed("both places searches failed; no recommendation available")
        recalled = ctx.inputs["memory"]
        turn = TurnMemory(GateDecision(False, "memory unavailable"))
        if recalled.ok:
            turn = recalled.unwrap()
        else:
            warnings.append(f"Memory {recalled.status}: {recalled.error}")
        for r in turn.facts:
            if r.stale:
                warnings.append(f"Confirm stale memory {r.fact.id}: {r.fact.kind} {r.fact.content}")
            elif r.fact.kind == "constraint":
                warnings.append(f"Cannot verify stored constraint: {r.fact.content}.")
        return Research(intent, tuple(candidates.values()), turn, tuple(warnings))

    def rank(ctx: StepContext) -> tuple[Research, tuple[RankedCandidate, ...]]:
        research: Research = ctx.inputs["merge"].unwrap()
        ranked: list[RankedCandidate] = []
        for candidate in research.candidates:
            reasons = [f"{candidate.distance_m} m from your approximate location"]
            if candidate.request_fit:
                reasons.append(f"matches {candidate.request_fit} requested category filter(s)")
            elif research.intent.cuisine or research.intent.category:
                reasons.append("nearby alternative; requested category match not confirmed")
            score = 0.0
            for r in research.memory.facts:
                if r.stale or r.fact.kind == "constraint":
                    continue
                try:
                    content = claim(r.fact.kind, r.fact.content).content
                except ValueError:
                    continue
                if r.fact.kind in ("cuisine_like", "cuisine_dislike") and candidate.matches(
                    content["cuisine"]
                ):
                    sign = 1 if r.fact.kind == "cuisine_like" else -1
                    score += sign * r.confidence
                    reasons.append(f"stored {r.fact.kind}: {content['cuisine']}")
                elif r.fact.kind == "place_rating" and content["place_id"] == candidate.id:
                    score += (float(content["rating"]) - 3) / 2 * r.confidence
                    reasons.append(f"your stored rating: {content['rating']}/5")
            ranked.append(RankedCandidate(candidate, tuple(reasons), score))
        ranked.sort(
            key=lambda r: (
                -r.place.request_fit,
                -r.memory_score,
                r.place.distance_m,
                r.place.name,
                r.place.id,
            )
        )
        return research, tuple(ranked)

    def explain(ctx: StepContext) -> Recommendation:
        research, ranked = ctx.inputs["rank"].unwrap()
        pick = ranked[0] if ranked else None
        explanation = (
            f"Try {pick.place.name}: {'; '.join(pick.reasons)}."
            if pick
            else "No places found within this search radius."
        )
        if ranked[1:4]:
            explanation += (
                " Runners-up: "
                + "; ".join(f"{r.place.name} ({'; '.join(r.reasons)})" for r in ranked[1:4])
                + "."
            )
        if research.warnings:
            explanation += " " + " ".join(research.warnings)
        source = places.name
        if source.startswith("overture:"):
            explanation += " Places data: Overture Maps Foundation (CDLA Permissive 2.0)."
        return Recommendation(
            pick,
            ranked[1:4],
            explanation,
            research.warnings,
            tuple(r for r in research.memory.facts if r.stale),
            source,
        )

    return Graph(
        "solo_recommendation",
        [
            agent_step(
                "classify",
                provider=provider,
                model=config.model,
                limits=config.limits,
                system_prompt=_CLASSIFY,
                prompt=lambda ctx: ctx.input.context["request"],
            ),
            Step(
                "intent", lambda ctx: _intent(ctx.inputs["classify"].unwrap()), after=("classify",)
            ),
            tool_step(
                "requested_places",
                tool,
                lambda ctx: arguments(ctx, filtered=True),
                after=("intent",),
            ),
            tool_step(
                "nearby_places", tool, lambda ctx: arguments(ctx, filtered=False), after=("intent",)
            ),
            Step("memory", recall, after=("intent",)),
            Step("merge", merge, after=("intent", "requested_places", "nearby_places", "memory")),
            Step("rank", rank, after=("merge",)),
            Step("explain", explain, after=("rank",)),
        ],
    )


def recommend(
    request: SoloRequest,
    *,
    provider: Provider,
    places: PlacesProvider,
    config: Config,
    memory: Memory | None = None,
    gate: RetrievalGate | None = None,
    sink: TraceSink | None = None,
) -> SoloResult:
    """Create an unshared session with one host and run the solo graph.

    Rows are returned for a future persistence/channel adapter; nothing writes location history.
    Session expiry is caller policy, as in the schema. No account is required.
    """
    now = utc_now()
    query = PlaceQuery.near(request.latitude, request.longitude, request.radius_m)
    session = Session(
        id=uuid4(),
        link_token=uuid4(),
        created_at=now,
        user_id=request.user_id,
        expires_at=request.expires_at,
        context={
            "latitude": query.lat,
            "longitude": query.lon,
            "radius_m": query.radius_m,
            "request": request.request,
        },
    )
    participant = Participant(
        id=uuid4(),
        session_id=session.id,
        is_host=True,
        constraints={},
        preferences={},
        joined_at=now,
        updated_at=now,
        user_id=request.user_id,
    )
    graph = run_graph(
        build_solo_graph(provider=provider, places=places, config=config, memory=memory, gate=gate),
        session,
        limits=config.graph_limits,
        sink=sink,
    )
    final = graph.results["explain"]
    return SoloResult(session, participant, graph, final.value if final.ok else None)
