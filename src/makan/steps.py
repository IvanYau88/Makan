"""Ready-made graph steps: one that calls a tool, and one that runs the core loop."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from makan import loop
from makan.graph import Step, StepContext
from makan.providers.base import Provider
from makan.tools import Tool


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
    """

    def run(ctx: StepContext) -> str:
        args = dict(arguments(ctx) if callable(arguments) else arguments)
        missing = [k for k in tool.parameters.get("required", []) if k not in args]
        if missing:
            raise ValueError(f"missing required arguments: {', '.join(missing)}")
        return tool.run(args)

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

    The run's events go to the graph's sink, and its `run_id` is recorded on the step events.
    Give the provider to one agent step at a time unless it is safe to call from several threads.
    """

    def run(ctx: StepContext) -> str:
        result = loop.run(
            prompt(ctx),
            provider=provider,
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            limits=limits,
            sink=ctx.sink,
        )
        ctx.child_runs.append(result.run_id)
        if result.answer is None:
            raise AgentRunFailed(f"run {result.run_id} ended {result.status} with no answer")
        return result.answer

    return Step(name, run, after, timeout_s)
