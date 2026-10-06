"""The graph workflow engine: explicit graphs of steps, run in parallel where they are independent.

A `Graph` is a named list of `Step`s. A step names the steps it comes `after`, and it starts when
every one of them has ended, so steps with nothing between them run at the same time. The merge
step of a fan-out is simply a step that comes after all the branches.

A step is a function from a `StepContext` to a value. It runs in a worker thread, because the tools
and the core loop are synchronous. The context holds the graph input and the `StepResult` of each
step it comes after, in the order of `after`, never in the order they finished.

A step failure never ends the graph, and never vanishes. An exception or a timeout becomes a
`StepResult` with status `error` or `timeout`, and the steps after it still run and see that result,
so a merge step decides what a missing branch means. This is how the core loop treats tool errors.

Two limits keep a run bounded, both in `GraphLimits`: at most `max_concurrency` steps run at once,
and a step that runs longer than its timeout is given up on. Python cannot stop a thread, so a
timed-out step's thread keeps running in the background until its function returns. The step gives
up its slot straight away, and the process cannot exit until the thread is done, so a step that does
I/O needs its own timeout as well.

Trace events emitted, in order, with their `data`. They share one `run_id`, and `seq` orders them:

- `graph_start`: graph, steps (name, after, timeout_s for each), max_concurrency, input
- `step_start`: step, after, timeout_s
- `step_finish`: step, status (`ok`), duration_ms, parallel_with, child_runs, output
- `step_error`: step, status (`error` or `timeout`), duration_ms, parallel_with, child_runs, error
- `graph_end`: status (`ok`, or `failed` if any step was not ok), duration_ms, steps (name: status)

`parallel_with` lists the steps whose run overlapped this one, in graph order, which is how a viewer
tells what ran in parallel. `child_runs` holds the `run_id` of each core loop run or direct tool
call the step started, added before the work begins so a failed step still names them. Their own
events go to the same sink. `output` is the step's value made JSON safe, see `trace.jsonable`.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

from makan.trace import Emitter, NullSink, TraceSink, jsonable

StepStatus = Literal["ok", "error", "timeout"]


class GraphError(ValueError):
    """The graph is malformed: no steps, a repeated name, an unknown step, or a cycle."""


class StepFailed(Exception):
    """Raised by `StepResult.unwrap` when the step did not finish ok."""


@dataclass(frozen=True)
class GraphLimits:
    max_concurrency: int = 4  # steps running at once
    step_timeout_s: float = 30.0  # for a step that sets no timeout of its own

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if self.step_timeout_s <= 0:
            raise ValueError("step_timeout_s must be above 0")


@dataclass(frozen=True)
class StepResult:
    name: str
    status: StepStatus
    value: Any = None  # what the step returned, when status is `ok`
    error: str | None = None  # "ExceptionType: message", or the timeout, otherwise
    duration_ms: int = 0
    exception: BaseException | None = field(default=None, repr=False, compare=False)
    # the exception behind an `error` status, so a caller can tell failures apart by type

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def unwrap(self) -> Any:
        """The value, or a `StepFailed` for a merge step that cannot go on without this one."""
        if not self.ok:
            raise StepFailed(f"step {self.name!r} {self.status}: {self.error}")
        return self.value


@dataclass(frozen=True)
class StepContext:
    input: Any  # what was given to `run_graph`
    inputs: Mapping[str, StepResult]  # the steps this one comes after, in the order of `after`
    sink: TraceSink  # pass it to a core loop run so its events join this trace
    child_runs: list[str] = field(default_factory=list)  # add the run_id of each loop run started


@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[[StepContext], Any]  # raise to report failure; the engine records it
    after: tuple[str, ...] = ()
    timeout_s: float | None = None  # overrides `GraphLimits.step_timeout_s`

    def __post_init__(self) -> None:
        if self.timeout_s is not None and self.timeout_s <= 0:
            raise ValueError(f"step {self.name!r}: timeout_s must be above 0")


class Graph:
    """A validated set of steps. It is plain data, so one graph can be run many times."""

    def __init__(self, name: str, steps: Sequence[Step]) -> None:
        self.name = name
        self.steps = tuple(steps)
        self._order = _dependency_order(self.steps)

    def run_order(self) -> tuple[Step, ...]:
        """Every step after the steps it comes after, and otherwise in declared order."""
        return self._order


@dataclass(frozen=True)
class GraphResult:
    run_id: str
    results: dict[str, StepResult]  # every step, in declared order

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results.values())


def run_graph(
    graph: Graph,
    input: Any = None,
    *,
    limits: GraphLimits | None = None,
    sink: TraceSink | None = None,
) -> GraphResult:
    """Run the graph to the end from synchronous code. Use `run_graph_async` inside a loop."""
    return asyncio.run(run_graph_async(graph, input, limits=limits, sink=sink))


async def run_graph_async(
    graph: Graph,
    input: Any = None,
    *,
    limits: GraphLimits | None = None,
    sink: TraceSink | None = None,
) -> GraphResult:
    limits = limits or GraphLimits()
    sink = sink or NullSink()
    run_id = uuid.uuid4().hex
    emit = Emitter(run_id, sink)
    started = time.perf_counter()
    emit(
        "graph_start",
        graph=graph.name,
        steps=[
            {
                "name": s.name,
                "after": list(s.after),
                "timeout_s": s.timeout_s if s.timeout_s is not None else limits.step_timeout_s,
            }
            for s in graph.steps
        ],
        max_concurrency=limits.max_concurrency,
        input=jsonable(input),
    )

    # One worker thread per step, so the semaphore alone sets how many run at once. Nothing ever
    # waits in the pool, which would start a step's timeout before the step started.
    pool = ThreadPoolExecutor(max_workers=len(graph.steps), thread_name_prefix="makan-step")
    execution = _Execution(graph, input, limits, sink, emit, pool)
    try:
        for step in graph.run_order():
            execution.tasks[step.name] = asyncio.create_task(execution.run_step(step))
        await asyncio.gather(*execution.tasks.values())
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # a timed-out step's thread is left behind

    results = {s.name: execution.tasks[s.name].result() for s in graph.steps}
    emit(
        "graph_end",
        status="ok" if all(r.ok for r in results.values()) else "failed",
        duration_ms=_ms(started),
        steps={name: r.status for name, r in results.items()},
    )
    return GraphResult(run_id, results)


class _Execution:
    """The state of one graph run. Only the event loop thread touches it, so it needs no locks."""

    def __init__(
        self,
        graph: Graph,
        input: Any,
        limits: GraphLimits,
        sink: TraceSink,
        emit: Emitter,
        pool: ThreadPoolExecutor,
    ) -> None:
        self.tasks: dict[str, asyncio.Task[StepResult]] = {}
        self._input = input
        self._limits = limits
        self._sink = sink
        self._emit = emit
        self._pool = pool
        self._slots = asyncio.Semaphore(limits.max_concurrency)
        self._position = {s.name: i for i, s in enumerate(graph.steps)}
        self._overlaps: dict[str, set[str]] = {}  # the steps running now, with who they overlapped

    async def run_step(self, step: Step) -> StepResult:
        # A step failure is a result, not an exception, so waiting on a step never raises.
        await asyncio.gather(*(self.tasks[name] for name in step.after))
        inputs = {name: self.tasks[name].result() for name in step.after}
        async with self._slots:  # a waiting step holds no slot, so a branch cannot starve a merge
            return await self._run_in_slot(step, StepContext(self._input, inputs, self._sink))

    async def _run_in_slot(self, step: Step, ctx: StepContext) -> StepResult:
        timeout = step.timeout_s if step.timeout_s is not None else self._limits.step_timeout_s
        self._emit("step_start", step=step.name, after=list(step.after), timeout_s=timeout)
        running_now = set(self._overlaps)
        for others in self._overlaps.values():
            others.add(step.name)
        self._overlaps[step.name] = running_now
        started = time.perf_counter()

        value: Any = None
        error: str | None = None
        failure: BaseException | None = None
        status: StepStatus = "ok"
        work = asyncio.get_running_loop().run_in_executor(self._pool, step.run, ctx)
        done, _ = await asyncio.wait({work}, timeout=timeout)
        if not done:
            work.cancel()  # does nothing to a thread that already started
            status, error = "timeout", f"TimeoutError: step took longer than {timeout}s"
        elif (exc := work.exception()) is not None:
            status, error, failure = "error", f"{type(exc).__name__}: {exc}", exc
        else:
            value = work.result()

        duration_ms = _ms(started)
        overlap = self._overlaps.pop(step.name)
        data: dict[str, Any] = {
            "step": step.name,
            "status": status,
            "duration_ms": duration_ms,
            "parallel_with": sorted(overlap, key=self._position.__getitem__),
            "child_runs": list(ctx.child_runs),
        }
        if status == "ok":
            self._emit("step_finish", output=jsonable(value), **data)
        else:
            self._emit("step_error", error=error, **data)
        return StepResult(step.name, status, value, error, duration_ms, failure)


def _dependency_order(steps: Sequence[Step]) -> tuple[Step, ...]:
    if not steps:
        raise GraphError("a graph needs at least one step")
    names = [s.name for s in steps]
    for name in names:
        if names.count(name) > 1:
            raise GraphError(f"duplicate step name {name!r}")
    for step in steps:
        for dependency in step.after:
            if dependency not in names:
                raise GraphError(f"step {step.name!r} comes after unknown step {dependency!r}")

    ordered: list[Step] = []
    placed: set[str] = set()
    remaining = list(steps)
    while remaining:
        ready = [s for s in remaining if placed.issuperset(s.after)]
        if not ready:
            raise GraphError(f"cycle among steps: {', '.join(s.name for s in remaining)}")
        ordered.extend(ready)
        placed.update(s.name for s in ready)
        remaining = [s for s in remaining if s.name not in placed]
    return tuple(ordered)


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
