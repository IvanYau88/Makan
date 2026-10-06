"""Browse nearby: a places search with no request, no model call, and no recommendation.

`browse` runs a one step graph, the same `nearby_places` tool call the solo workflow makes, and
returns the places nearest first. Nothing reads a request, memory, or a scorer, and no place is
picked, so the result cannot claim a recommendation. Guests and signed in people get the same list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from makan.config import Config
from makan.graph import Graph, GraphResult, StepContext, run_graph
from makan.places.base import MAX_LIMIT, PlaceQuery, PlacesProvider, distance_label
from makan.places.tool import search_nearby_places
from makan.solo import Candidate, RankedCandidate
from makan.steps import tool_step
from makan.trace import TraceSink

STAGE = "nearby_places"
UNVERIFIED = (
    "Opening hours, menus, prices, and public reviews are unavailable; verify before going."
)


@dataclass(frozen=True)
class BrowseRequest:
    latitude: float
    longitude: float
    radius_m: int = 1000

    def __post_init__(self) -> None:
        PlaceQuery.near(self.latitude, self.longitude, self.radius_m)

    def trace_summary(self) -> dict[str, Any]:
        query = PlaceQuery.near(self.latitude, self.longitude, self.radius_m)
        return {"latitude": query.lat, "longitude": query.lon, "radius_m": query.radius_m}


@dataclass(frozen=True)
class BrowseResult:
    graph: GraphResult
    query: PlaceQuery  # the search as it ran, with the rounded center
    places: tuple[RankedCandidate, ...] | None  # nearest first, or None when the search failed
    truncated: bool  # the search returned its limit, so more places may lie in the radius
    warnings: tuple[str, ...]
    data_source: str


def build_browse_graph(places: PlacesProvider) -> Graph:
    tool = search_nearby_places(places)

    def arguments(ctx: StepContext) -> dict[str, Any]:
        request: BrowseRequest = ctx.input
        query = PlaceQuery.near(request.latitude, request.longitude, request.radius_m)
        return {
            "latitude": query.lat,
            "longitude": query.lon,
            "radius_m": query.radius_m,
            "limit": MAX_LIMIT,
        }

    return Graph("browse_nearby", [tool_step(STAGE, tool, arguments)])


def browse(
    request: BrowseRequest,
    *,
    places: PlacesProvider,
    config: Config,
    sink: TraceSink | None = None,
) -> BrowseResult:
    """Find places around the point, nearest first. A failed search gives `places` of None."""
    query = PlaceQuery.near(request.latitude, request.longitude, request.radius_m)
    graph = run_graph(build_browse_graph(places), request, limits=config.graph_limits, sink=sink)
    found = graph.results[STAGE]
    ranked: tuple[RankedCandidate, ...] | None = None
    truncated = False
    if found.ok:
        data = json.loads(found.unwrap())
        truncated = data["count"] >= MAX_LIMIT
        candidates = sorted(
            (
                Candidate(
                    p["id"],
                    p["name"],
                    p["category"],
                    p["distance_m"],
                    p.get("address"),
                    0,
                    p.get("lat"),
                    p.get("lon"),
                )
                for p in data["places"]
            ),
            key=lambda c: (c.distance_m, c.name, c.id),
        )
        ranked = tuple(
            RankedCandidate(
                c, (f"{distance_label(c.distance_m)} from your approximate location",), 0.0
            )
            for c in candidates
        )
    return BrowseResult(graph, query, ranked, truncated, (UNVERIFIED,), places.name)
