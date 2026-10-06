"""Ready-made graph steps: one that calls a tool, and one that runs the core loop."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from makan import loop
from makan.graph import Step, StepContext
from makan.providers.base import Provider
from makan.tools import Tool
from makan.trace import Emitter, jsonable


class AgentRunFailed(Exception):
    """A core loop run ended without an answer."""


def tool_step(
    name: str,
    tool: Tool,
    arguments: Mapping[str, Any] | Callable[[StepContext], Mapping[str, Any]],
    *,
    after: tuple[str, ...] = (),
    timeout_s: float | None = None,
) -> Step:
    """Call a tool directly, with no model in between. Its output text is the step value.

    Arguments are fixed, or built from the context so they can depend on the graph input and on
    earlier steps. A tool failure is a step error, the same as it is a tool error in the loop.
    The call is traced as its own small run, recorded in `child_runs`: a `tool_call` event with the
    name and arguments, then a `tool_result` event with `ok`, the output or error, `duration_ms`.
    """

    def run(ctx: StepContext) -> str:
        args = dict(arguments(ctx) if callable(arguments) else arguments)
        call_run = uuid.uuid4().hex
        ctx.child_runs.append(call_run)
        emit = Emitter(call_run, ctx.sink)
        emit("tool_call", name=tool.name, arguments=jsonable(args))
        started = time.perf_counter()
        try:
            missing = [k for k in tool.parameters.get("required", []) if k not in args]
            if missing:
                raise ValueError(f"missing required arguments: {', '.join(missing)}")
            output = tool.run(args)
        except Exception as exc:
            emit(
                "tool_result",
                name=tool.name,
                ok=False,
                output=f"{type(exc).__name__}: {exc}",
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
            raise
        emit(
            "tool_result",
            name=tool.name,
            ok=True,
            output=output,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return output

    return Step(name, run, after, timeout_s)


def agent_step(
    name: str,
    *,
    provider: Provider,
    model: str,
    prompt: Callable[[StepContext], str],
    tools: Sequence[Tool] = (),
    system_prompt: str = "",
    limits: loop.Limits | None = None,
    after: tuple[str, ...] = (),
    timeout_s: float | None = None,
) -> Step:
    """Run the core loop once. Its answer is the step value, and a run with no answer is an error.

    The run's events go to the graph's sink, and its `run_id` is recorded on the step events,
    before the run starts, so a provider failure that raises still leaves the link.
    Give the provider to one agent step at a time unless it is safe to call from several threads.
    """

    def run(ctx: StepContext) -> str:
        run_id = uuid.uuid4().hex
        ctx.child_runs.append(run_id)
        result = loop.run(
            prompt(ctx),
            provider=provider,
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            limits=limits,
            sink=ctx.sink,
            run_id=run_id,
        )
        if result.answer is None:
            raise AgentRunFailed(f"run {result.run_id} ended {result.status} with no answer")
        return result.answer

    return Step(name, run, after, timeout_s)
