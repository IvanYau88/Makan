"""Behavioral tests for the graph engine, using plain functions as steps."""

import asyncio
import json
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from makan.graph import (
    Graph,
    GraphError,
    GraphLimits,
    Step,
    StepContext,
    StepFailed,
    StepResult,
    run_graph,
    run_graph_async,
)
from makan.trace import ListSink, TraceEvent


def value(result: Any) -> Any:
    return lambda ctx: result


def boom(ctx: StepContext) -> Any:
    raise RuntimeError("the kitchen is on fire")


def events(sink: ListSink, *types: str) -> list[TraceEvent]:
    return [e for e in sink.events if e.type in types]


def ended(sink: ListSink, step: str) -> TraceEvent:
    (event,) = [e for e in events(sink, "step_finish", "step_error") if e.data["step"] == step]
    return event


# Order and inputs


def test_a_step_gets_the_graph_input_and_the_results_it_comes_after() -> None:
    graph = Graph(
        "g",
        [
            Step("a", lambda ctx: ctx.input.upper()),
            Step("b", lambda ctx: ctx.inputs["a"].value + "!", after=("a",)),
        ],
    )
    result = run_graph(graph, "ramen")
    assert result.ok
    assert [r.value for r in result.results.values()] == ["RAMEN", "RAMEN!"]
    assert result.results["a"].status == "ok"


def test_a_step_runs_only_after_the_steps_it_comes_after_have_ended() -> None:
    order: list[str] = []

    def record(name: str) -> Callable[[StepContext], None]:
        def run(ctx: StepContext) -> None:
            order.append(name)

        return run

    # Declared backwards on purpose: the order comes from `after`, not from the list.
    graph = Graph(
        "g",
        [
            Step("c", record("c"), after=("b",)),
            Step("b", record("b"), after=("a",)),
            Step("a", record("a")),
        ],
    )
    run_graph(graph)
    assert order == ["a", "b", "c"]


def test_merge_inputs_are_in_the_declared_order_not_the_finishing_order() -> None:
    sink = ListSink()

    def finished_steps() -> list[str]:
        return [e.data["step"] for e in events(sink, "step_finish")]

    def wait_until_finished(step: str) -> None:
        deadline = time.monotonic() + 2
        while step not in finished_steps():
            assert time.monotonic() < deadline
            time.sleep(0.005)

    def a(ctx: StepContext) -> str:
        wait_until_finished("b")
        return "a"

    def b(ctx: StepContext) -> str:
        wait_until_finished("c")
        return "b"

    def c(ctx: StepContext) -> str:
        return "c"

    def merge(ctx: StepContext) -> list[str]:
        return list(ctx.inputs)

    graph = Graph(
        "g",
        [Step("a", a), Step("b", b), Step("c", c), Step("merge", merge, after=("c", "a", "b"))],
    )
    result = run_graph(graph, sink=sink)
    assert result.results["merge"].value == ["c", "a", "b"]
    assert finished_steps() == ["c", "b", "a", "merge"]  # they really did finish in the other order


# Parallelism and bounds


def test_independent_steps_run_at_the_same_time() -> None:
    barrier = threading.Barrier(3)

    def meet(ctx: StepContext) -> str:
        barrier.wait(timeout=2)  # only passes when all three are running together
        return "met"

    graph = Graph("g", [Step(n, meet) for n in "abc"])
    result = run_graph(graph, limits=GraphLimits(max_concurrency=3))
    assert result.ok


def test_concurrency_is_bounded() -> None:
    lock = threading.Lock()
    active = peak = 0

    def work(ctx: StepContext) -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1

    graph = Graph("g", [Step(f"s{i}", work) for i in range(6)])
    result = run_graph(graph, limits=GraphLimits(max_concurrency=2))
    assert result.ok
    assert peak == 2


def test_a_waiting_step_does_not_hold_a_slot() -> None:
    # With one slot, the merge waits for both branches. If waiting held the slot, this would hang.
    graph = Graph(
        "g",
        [
            Step("merge", lambda ctx: list(ctx.inputs), after=("a", "b")),
            Step("a", value(1)),
            Step("b", value(2)),
        ],
    )
    result = run_graph(graph, limits=GraphLimits(max_concurrency=1))
    assert result.results["merge"].value == ["a", "b"]


def test_the_graph_runs_inside_an_existing_event_loop() -> None:
    async def main() -> bool:
        graph = Graph("g", [Step("a", value(1)), Step("b", lambda ctx: 2, after=("a",))])
        return (await run_graph_async(graph)).ok

    assert asyncio.run(main())


# Failures and timeouts


def test_a_failed_branch_is_an_explicit_result_for_the_merge_step() -> None:
    def merge(ctx: StepContext) -> dict[str, str]:
        return {name: r.status for name, r in ctx.inputs.items()}

    graph = Graph(
        "g",
        [
            Step("good", value("fine")),
            Step("bad", boom),
            Step("merge", merge, after=("good", "bad")),
        ],
    )
    result = run_graph(graph)
    assert not result.ok
    assert result.results["merge"].value == {"good": "ok", "bad": "error"}
    bad = result.results["bad"]
    assert (bad.status, bad.value, bad.error) == (
        "error",
        None,
        "RuntimeError: the kitchen is on fire",
    )


def test_a_merge_step_can_insist_on_a_branch_with_unwrap() -> None:
    graph = Graph(
        "g",
        [Step("bad", boom), Step("merge", lambda ctx: ctx.inputs["bad"].unwrap(), after=("bad",))],
    )
    result = run_graph(graph)
    merge = result.results["merge"]
    assert merge.status == "error"
    assert merge.error == "StepFailed: step 'bad' error: RuntimeError: the kitchen is on fire"
    with pytest.raises(StepFailed):
        StepResult("x", "timeout", error="slow").unwrap()


def test_a_step_that_hangs_times_out_and_the_rest_carry_on() -> None:
    release = threading.Event()

    def hang(ctx: StepContext) -> str:
        release.wait(5)
        return "late"

    def merge(ctx: StepContext) -> str:
        return ctx.inputs["hang"].status + "," + ctx.inputs["fast"].status

    graph = Graph(
        "g",
        [
            Step("hang", hang, timeout_s=0.05),
            Step("fast", value("ok")),
            Step("merge", merge, after=("hang", "fast")),
        ],
    )
    try:
        started = time.perf_counter()
        result = run_graph(graph)
        elapsed = time.perf_counter() - started
    finally:
        release.set()
    assert elapsed < 2  # it did not wait for the hung thread
    assert result.results["hang"].status == "timeout"
    assert result.results["hang"].error == "TimeoutError: step took longer than 0.05s"
    assert result.results["merge"].value == "timeout,ok"
    assert not result.ok


def test_the_default_timeout_applies_to_steps_that_set_none() -> None:
    release = threading.Event()

    def hang(ctx: StepContext) -> None:
        release.wait(5)

    try:
        result = run_graph(
            Graph("g", [Step("hang", hang)]), limits=GraphLimits(step_timeout_s=0.05)
        )
    finally:
        release.set()
    assert result.results["hang"].status == "timeout"


def test_a_timeout_error_raised_by_a_step_is_an_error_not_a_timeout() -> None:
    def raises_timeout(ctx: StepContext) -> None:
        raise TimeoutError("socket timed out")

    result = run_graph(Graph("g", [Step("a", raises_timeout)]))
    assert result.results["a"].status == "error"
    assert result.results["a"].error == "TimeoutError: socket timed out"


def test_a_timed_out_step_gives_up_its_slot() -> None:
    release = threading.Event()

    def hang(ctx: StepContext) -> None:
        release.wait(5)

    graph = Graph("g", [Step("hang", hang, timeout_s=0.05), Step("next", value("ran"))])
    try:
        result = run_graph(graph, limits=GraphLimits(max_concurrency=1))
    finally:
        release.set()
    assert [r.status for r in result.results.values()] == ["timeout", "ok"]


# Trace


def test_trace_records_every_step_start_finish_and_failure_in_one_run() -> None:
    sink = ListSink()
    graph = Graph(
        "demo", [Step("a", value("x")), Step("b", boom), Step("m", value("y"), after=("a", "b"))]
    )
    result = run_graph(graph, {"q": 1}, limits=GraphLimits(max_concurrency=2), sink=sink)

    assert {e.run_id for e in sink.events} == {result.run_id}
    assert [e.seq for e in sink.events] == list(range(1, len(sink.events) + 1))
    assert sink.types()[0] == "graph_start"
    assert sink.types()[-1] == "graph_end"

    start = sink.events[0].data
    assert start["graph"] == "demo"
    assert start["input"] == {"q": 1}
    assert start["max_concurrency"] == 2
    assert start["steps"] == [
        {"name": "a", "after": [], "timeout_s": 30.0},
        {"name": "b", "after": [], "timeout_s": 30.0},
        {"name": "m", "after": ["a", "b"], "timeout_s": 30.0},
    ]
    assert sorted(e.data["step"] for e in events(sink, "step_start")) == ["a", "b", "m"]
    assert ended(sink, "a").type == "step_finish"
    assert ended(sink, "a").data["output"] == "x"
    failure = ended(sink, "b")
    assert failure.type == "step_error"
    assert (failure.data["status"], failure.data["error"]) == (
        "error",
        "RuntimeError: the kitchen is on fire",
    )
    assert sink.events[-1].data["status"] == "failed"
    assert sink.events[-1].data["steps"] == {"a": "ok", "b": "error", "m": "ok"}


def test_a_timeout_is_a_step_error_event_with_status_timeout() -> None:
    release = threading.Event()
    sink = ListSink()

    def hang(ctx: StepContext) -> None:
        release.wait(5)

    try:
        run_graph(Graph("g", [Step("hang", hang, timeout_s=0.05)]), sink=sink)
    finally:
        release.set()
    event = ended(sink, "hang")
    assert (event.type, event.data["status"]) == ("step_error", "timeout")
    assert "longer than 0.05s" in event.data["error"]


def test_trace_says_which_steps_ran_in_parallel() -> None:
    barrier = threading.Barrier(2)

    def meet(ctx: StepContext) -> None:
        barrier.wait(timeout=2)

    sink = ListSink()
    graph = Graph(
        "g",
        [
            Step("first", value(1)),
            Step("x", meet, after=("first",)),
            Step("y", meet, after=("first",)),
            Step("last", value(2), after=("x", "y")),
        ],
    )
    run_graph(graph, sink=sink)
    parallel = {e.data["step"]: e.data["parallel_with"] for e in events(sink, "step_finish")}
    assert parallel == {"first": [], "x": ["y"], "y": ["x"], "last": []}


def test_every_event_is_json_even_when_a_step_returns_something_odd() -> None:
    sink = ListSink()
    run_graph(Graph("g", [Step("a", value({1, 2}))]), sink=sink)
    for event in sink.events:
        json.loads(event.to_json())
    assert isinstance(ended(sink, "a").data["output"], str)


# Validation


def test_a_malformed_graph_is_rejected_when_it_is_built() -> None:
    with pytest.raises(GraphError, match="at least one step"):
        Graph("g", [])
    with pytest.raises(GraphError, match="duplicate step name 'a'"):
        Graph("g", [Step("a", value(1)), Step("a", value(2))])
    with pytest.raises(GraphError, match="unknown step 'nope'"):
        Graph("g", [Step("a", value(1), after=("nope",))])
    with pytest.raises(GraphError, match="cycle among steps: a, b"):
        Graph("g", [Step("a", value(1), after=("b",)), Step("b", value(1), after=("a",))])
    with pytest.raises(GraphError, match="cycle"):
        Graph("g", [Step("a", value(1), after=("a",))])


def test_limits_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        GraphLimits(max_concurrency=0)
    with pytest.raises(ValueError, match="step_timeout_s"):
        GraphLimits(step_timeout_s=0)
    with pytest.raises(ValueError, match="timeout_s"):
        Step("a", value(1), timeout_s=-1)
