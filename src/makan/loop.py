"""The core loop: ask the model, run the tools it calls, show it the results, repeat.

A run ends in exactly one of four ways, and the result says which:

- `finished`: the model called the `finish` tool with its answer.
- `max_iterations`: the model was asked `limits.max_iterations` times without finishing.
- `budget_exhausted`: the tokens used so far reached `limits.token_budget`.
- `provider_error`: the provider failed. The error is traced, then re-raised.

Tool failures never end a run. They go back to the model as the tool result and are traced.

Trace events emitted, in order, with their `data`:

- `run_start`: model, tools, max_iterations, token_budget, input
- `model_request`: iteration, message_count
- `model_response`: iteration, content, tool_calls, usage, duration_ms
- `tool_call`: iteration, call_id, name, arguments
- `tool_result`: iteration, call_id, name, ok, output (what the model was shown), duration_ms
- `nudge`: iteration (the model replied without calling any tool)
- `error`: iteration, error (the provider failed)
- `run_end`: status, answer, iterations, usage
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from makan.providers.base import Message, Provider, ProviderError, ToolCall, ToolSpec, Usage
from makan.tools import Tool
from makan.trace import Emitter, NullSink, TraceSink

Status = Literal["finished", "max_iterations", "budget_exhausted", "provider_error"]

FINISH = ToolSpec(
    name="finish",
    description="Give the final answer to the user and end the run. Call this when you are done.",
    parameters={
        "type": "object",
        "properties": {"answer": {"type": "string", "description": "The final answer."}},
        "required": ["answer"],
    },
)

NUDGE = "Reply by calling a tool. When you are done, call the finish tool with your answer."


@dataclass(frozen=True)
class Limits:
    max_iterations: int = 10  # model calls per run
    token_budget: int = 50_000  # prompt plus completion tokens per run


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: Status
    answer: str | None
    iterations: int
    usage: Usage
    messages: list[Message]


def run(
    user_message: str,
    *,
    provider: Provider,
    model: str,
    tools: Sequence[Tool] = (),
    system_prompt: str = "",
    limits: Limits | None = None,
    sink: TraceSink | None = None,
    run_id: str | None = None,
) -> RunResult:
    """Run the loop. A caller that must know the run's id before it starts passes its own."""
    limits = limits or Limits()
    registry = _registry(tools)
    specs = [t.spec() for t in tools] + [FINISH]
    run_id = run_id or uuid.uuid4().hex
    emitter = Emitter(run_id, sink or NullSink())

    messages: list[Message] = []
    if system_prompt:
        messages.append(Message("system", system_prompt))
    messages.append(Message("user", user_message))

    emitter(
        "run_start",
        model=model,
        tools=[s.name for s in specs],
        max_iterations=limits.max_iterations,
        token_budget=limits.token_budget,
        input=user_message,
    )

    usage = Usage()
    iterations = 0
    status: Status = "max_iterations"
    answer: str | None = None
    error: ProviderError | None = None

    while iterations < limits.max_iterations:
        if usage.total_tokens >= limits.token_budget:
            status = "budget_exhausted"
            break
        iterations += 1
        emitter("model_request", iteration=iterations, message_count=len(messages))
        started = time.perf_counter()
        try:
            completion = provider.complete(model=model, messages=messages, tools=specs)
        except ProviderError as exc:
            status, error = "provider_error", exc
            emitter("error", iteration=iterations, error=str(exc))
            break
        usage += completion.usage
        reply = completion.message
        messages.append(reply)
        emitter(
            "model_response",
            iteration=iterations,
            content=reply.content,
            tool_calls=[
                {"id": c.id, "name": c.name, "arguments": c.arguments} for c in reply.tool_calls
            ],
            usage={
                "prompt_tokens": completion.usage.prompt_tokens,
                "completion_tokens": completion.usage.completion_tokens,
            },
            duration_ms=_ms(started),
        )

        if not reply.tool_calls:
            emitter("nudge", iteration=iterations)
            messages.append(Message("user", NUDGE))
            continue

        for tool_call in reply.tool_calls:
            answer = _handle_call(tool_call, registry, messages, emitter, iterations)
            if answer is not None:
                break
        if answer is not None:
            status = "finished"
            break

    emitter(
        "run_end",
        status=status,
        answer=answer,
        iterations=iterations,
        usage={
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
        },
    )
    if error is not None:
        raise error
    return RunResult(run_id, status, answer, iterations, usage, messages)


def _handle_call(
    tool_call: ToolCall,
    registry: dict[str, Tool],
    messages: list[Message],
    emit: Emitter,
    iteration: int,
) -> str | None:
    """Run one tool call, trace it, and answer the model. Return the answer if it was `finish`."""
    emit(
        "tool_call",
        iteration=iteration,
        call_id=tool_call.id,
        name=tool_call.name,
        arguments=tool_call.arguments,
    )
    started = time.perf_counter()
    answer: str | None = None
    if tool_call.name == FINISH.name:
        answer, output = _finish(tool_call)
        ok = answer is not None
    else:
        ok, output = _run_tool(registry, tool_call)
    emit(
        "tool_result",
        iteration=iteration,
        call_id=tool_call.id,
        name=tool_call.name,
        ok=ok,
        output=output,
        duration_ms=_ms(started),
    )
    messages.append(Message("tool", output, tool_call_id=tool_call.id))
    return answer


def _registry(tools: Sequence[Tool]) -> dict[str, Tool]:
    registry: dict[str, Tool] = {}
    for tool in tools:
        if tool.name == FINISH.name:
            raise ValueError(f"{FINISH.name!r} is reserved for the loop")
        if tool.name in registry:
            raise ValueError(f"duplicate tool name {tool.name!r}")
        registry[tool.name] = tool
    return registry


def _parse_arguments(raw: str) -> dict[str, Any]:
    try:
        arguments = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"arguments are not valid JSON: {exc.msg}") from exc
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be a JSON object")
    return arguments


def _finish(tool_call: ToolCall) -> tuple[str | None, str]:
    """Return (answer, tool output). The answer is None when the call was malformed."""
    try:
        answer = _parse_arguments(tool_call.arguments).get("answer")
    except ValueError as exc:
        return None, f"error: {exc}"
    if not isinstance(answer, str):
        return None, "error: finish needs an 'answer' string"
    return answer, "finished"


def _run_tool(registry: dict[str, Tool], tool_call: ToolCall) -> tuple[bool, str]:
    """Return (ok, output). Any failure becomes text for the model, never an exception."""
    tool = registry.get(tool_call.name)
    if tool is None:
        known = ", ".join([*sorted(registry), FINISH.name])
        return False, f"unknown tool {tool_call.name!r}; available tools: {known}"
    try:
        arguments = _parse_arguments(tool_call.arguments)
        missing = [k for k in tool.parameters.get("required", []) if k not in arguments]
        if missing:
            raise ValueError(f"missing required arguments: {', '.join(missing)}")
        return True, tool.run(arguments)
    except Exception as exc:  # a tool may fail in any way; the model must see it
        return False, f"{type(exc).__name__}: {exc}"


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
