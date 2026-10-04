"""A generic workflow shaped like single-user research, run on fakes.

classify (agent step) -> fan out to three tool steps -> merge -> rank.
It proves the engine carries the shape the real workflows will have. It is not that workflow.
"""

import json
import threading
from typing import Any

from makan.graph import Graph, GraphLimits, GraphResult, Step, StepContext, run_graph
from makan.places import FakePlacesProvider, search_nearby_places
from makan.providers import FakeProvider, ProviderError, ToolCall
from makan.providers.fake import call
from makan.steps import agent_step, tool_step
from makan.tools import Tool
from makan.trace import ListSink
from tests.helpers import KLCC, place

NEAR = place("Near Noodles", "ramen_restaurant", 3.1481, 101.6951, "food_and_drink", "restaurant")
THAI = place("Mid Thai", "thai_restaurant", 3.1485, 101.6951, "food_and_drink", "thai_restaurant")
FAR = place("Far Thai", "thai_restaurant", 3.1520, 101.695, "food_and_drink", "thai_restaurant")

RATINGS = {"mid-thai": 4.2, "far-thai": 4.8}
HOURS = {"mid-thai": "open", "far-thai": "closed"}


def lookup_tool(name: str, table: dict[str, Any]) -> Tool:
    """A fake tool that answers per place id, like reviews or opening hours would."""

    def run(arguments: dict[str, Any]) -> str:
        return json.dumps({pid: table[pid] for pid in arguments["place_ids"] if pid in table})

    return Tool(name, f"Look up {name}.", {"type": "object", "required": ["place_ids"]}, run)


def failing_tool(name: str) -> Tool:
    def run(arguments: dict[str, Any]) -> str:
        raise RuntimeError(f"{name} service is down")

    return Tool(name, "Always fails.", {"type": "object"}, run)


def rendezvous(tool: Tool, barrier: threading.Barrier) -> Tool:
    """Make a tool wait until every fan-out branch is running, so overlap is certain, not lucky."""

    def run(arguments: dict[str, Any]) -> str:
        barrier.wait(timeout=2)
        return tool.run(arguments)

    return Tool(tool.name, tool.description, tool.parameters, run)


def build(
    *,
    reviews: Tool | None = None,
    hours: Tool | None = None,
    hours_timeout_s: float | None = None,
    provider: FakeProvider | None = None,
    branches: int = 3,
) -> tuple[Graph, FakePlacesProvider]:
    places = FakePlacesProvider([NEAR, THAI, FAR])
    barrier = threading.Barrier(branches)
    provider = provider or FakeProvider([call(ToolCall.of("finish", answer="thai"))])

    def nearby_args(ctx: StepContext) -> dict[str, Any]:
        cuisine = ctx.inputs["classify"].unwrap()
        return {"latitude": KLCC[0], "longitude": KLCC[1], "radius_m": 2000, "cuisine": cuisine}

    def lookup_args(ctx: StepContext) -> dict[str, Any]:
        return {"place_ids": ["near-noodles", "mid-thai", "far-thai"]}

    def merge(ctx: StepContext) -> list[dict[str, Any]]:
        """Join the branches on place id. A branch that failed leaves its field unknown."""
        found = json.loads(ctx.inputs["nearby"].unwrap())["places"]  # no places, nothing to rank
        rated = json.loads(ctx.inputs["reviews"].value) if ctx.inputs["reviews"].ok else {}
        hours = json.loads(ctx.inputs["hours"].value) if ctx.inputs["hours"].ok else {}
        return [
            {
                "name": p["name"],
                "distance_m": p["distance_m"],
                "rating": rated.get(p["id"]),
                "hours": hours.get(p["id"], "unknown"),
            }
            for p in found
        ]

    def rank(ctx: StepContext) -> str:
        options = [o for o in ctx.inputs["merge"].unwrap() if o["hours"] != "closed"]
        options.sort(key=lambda o: (-(o["rating"] or 0), o["distance_m"]))
        return str(options[0]["name"])

    graph = Graph(
        "research",
        [
            agent_step(
                "classify",
                provider=provider,
                model="test/model",
                prompt=lambda ctx: f"What cuisine is this request? {ctx.input}",
            ),
            tool_step(
                "nearby",
                rendezvous(search_nearby_places(places), barrier),
                nearby_args,
                after=("classify",),
            ),
            tool_step(
                "reviews",
                rendezvous(reviews or lookup_tool("reviews", RATINGS), barrier),
                lookup_args,
                after=("classify",),
            ),
            tool_step(
                "hours",
                rendezvous(hours or lookup_tool("hours", HOURS), barrier),
                lookup_args,
                after=("classify",),
                timeout_s=hours_timeout_s,
            ),
            Step("merge", merge, after=("nearby", "reviews", "hours")),
            Step("rank", rank, after=("merge",)),
        ],
    )
    return graph, places


def go(graph: Graph, sink: ListSink) -> GraphResult:
    return run_graph(graph, "thai please", limits=GraphLimits(max_concurrency=3), sink=sink)


def test_the_research_shape_runs_end_to_end() -> None:
    graph, places = build()
    sink = ListSink()
    result = go(graph, sink)

    assert result.ok
    assert result.results["classify"].value == "thai"
    assert places.queries[0].cuisine == "thai"  # the fan-out saw the classification
    merged = result.results["merge"].value
    assert [o["name"] for o in merged] == ["Mid Thai", "Far Thai"]
    assert result.results["rank"].value == "Mid Thai"  # Far Thai rates higher but is closed


def test_the_fan_out_ran_in_parallel_and_the_rest_did_not() -> None:
    graph, _ = build()
    sink = ListSink()
    go(graph, sink)
    parallel = {
        e.data["step"]: e.data["parallel_with"] for e in sink.events if e.type == "step_finish"
    }
    assert parallel["nearby"] == ["reviews", "hours"]
    assert parallel["reviews"] == ["nearby", "hours"]
    assert parallel["hours"] == ["nearby", "reviews"]
    assert parallel["classify"] == parallel["merge"] == parallel["rank"] == []


def test_the_agent_step_trace_links_to_its_own_run() -> None:
    graph, _ = build()
    sink = ListSink()
    result = go(graph, sink)
    (classify,) = [
        e for e in sink.events if e.data.get("step") == "classify" and "child_runs" in e.data
    ]
    (child,) = classify.data["child_runs"]
    assert child != result.run_id
    assert {"run_start", "run_end"} <= {e.type for e in sink.events if e.run_id == child}


def test_a_failed_branch_reaches_the_merge_and_the_trace() -> None:
    graph, _ = build(hours=failing_tool("hours"))
    sink = ListSink()
    result = go(graph, sink)

    assert result.results["hours"].status == "error"
    assert "hours service is down" in (result.results["hours"].error or "")
    assert [o["hours"] for o in result.results["merge"].value] == ["unknown", "unknown"]
    assert result.results["rank"].value == "Far Thai"  # nothing known to be closed any more
    assert not result.ok
    errors = [e for e in sink.events if e.type == "step_error"]
    assert [e.data["step"] for e in errors] == ["hours"]


def test_a_branch_that_times_out_reaches_the_merge_as_a_timeout() -> None:
    release = threading.Event()

    def slow(arguments: dict[str, Any]) -> str:
        release.wait(5)
        return "{}"

    hanging = Tool("hours", "Slow.", {"type": "object"}, slow)
    graph, _ = build(hours=hanging, hours_timeout_s=0.05)
    sink = ListSink()
    try:
        result = go(graph, sink)
    finally:
        release.set()
    assert result.results["hours"].status == "timeout"
    assert result.results["rank"].value == "Far Thai"
    (event,) = [e for e in sink.events if e.type == "step_error"]
    assert event.data["status"] == "timeout"


def test_a_failed_agent_step_does_not_stop_the_graph() -> None:
    provider = FakeProvider([ProviderError("model is down")])
    graph, places = build(provider=provider, branches=2)  # nearby never starts
    result = run_graph(graph, "x", limits=GraphLimits(max_concurrency=3))
    assert result.results["classify"].status == "error"
    assert result.results["nearby"].status == "error"  # unwrap of the failed classify
    assert places.queries == []
