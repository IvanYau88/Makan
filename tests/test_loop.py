"""Behavioral tests for the core loop, driven by a scripted fake provider."""

from typing import Any

import pytest

from makan.loop import NUDGE, Limits, RunResult, run
from makan.providers import FakeProvider, Message, ProviderError, ToolCall
from makan.providers.fake import call, say
from makan.tools import Tool
from makan.trace import ListSink, TraceEvent
from tests.helpers import BOOM, ECHO

MODEL = "test/model"


def finish(answer: str, call_id: str = "fin") -> ToolCall:
    return ToolCall.of("finish", call_id, answer=answer)


def go(
    provider: FakeProvider,
    sink: ListSink,
    *,
    tools: tuple[Tool, ...] = (ECHO, BOOM),
    limits: Limits | None = None,
) -> RunResult:
    return run("hungry", provider=provider, model=MODEL, tools=tools, limits=limits, sink=sink)


def events(sink: ListSink, type: str) -> list[TraceEvent]:
    return [e for e in sink.events if e.type == type]


# Termination


def test_finish_ends_the_run_with_the_answer(sink: ListSink) -> None:
    provider = FakeProvider([call(finish("ramen")), say("never reached")])
    result = go(provider, sink)
    assert (result.status, result.answer, result.iterations) == ("finished", "ramen", 1)
    assert len(provider.requests) == 1


def test_tool_results_are_shown_to_the_model_on_the_next_turn(sink: ListSink) -> None:
    provider = FakeProvider([call(ToolCall.of("echo", "c1", text="hello")), call(finish("done"))])
    result = go(provider, sink)
    assert result.status == "finished"
    second_request = provider.requests[1].messages
    assert second_request[-1] == Message("tool", "hello", tool_call_id="c1")
    assert second_request[-2].tool_calls[0].name == "echo"


def test_model_is_given_the_configured_model_name_and_the_finish_tool(sink: ListSink) -> None:
    provider = FakeProvider([call(finish("ok"))])
    go(provider, sink)
    request = provider.requests[0]
    assert request.model == MODEL
    assert [t.name for t in request.tools] == ["echo", "boom", "finish"]


def test_max_iterations_is_a_hard_stop(sink: ListSink) -> None:
    script = [call(ToolCall.of("echo", f"c{i}", text="again")) for i in range(10)]
    provider = FakeProvider(script)
    result = go(provider, sink, limits=Limits(max_iterations=3))
    assert (result.status, result.answer, result.iterations) == ("max_iterations", None, 3)
    assert len(provider.requests) == 3


def test_token_budget_stops_the_run_before_the_next_model_call(sink: ListSink) -> None:
    script = [call(ToolCall.of("echo", f"c{i}", text="x"), tokens=60) for i in range(5)]
    provider = FakeProvider(script)
    result = go(provider, sink, limits=Limits(max_iterations=10, token_budget=100))
    assert result.status == "budget_exhausted"
    assert result.iterations == 2
    assert result.usage.total_tokens == 120
    assert len(provider.requests) == 2


def test_reply_without_tool_call_is_nudged_to_finish(sink: ListSink) -> None:
    provider = FakeProvider([say("I think sushi"), call(finish("sushi"))])
    result = go(provider, sink)
    assert (result.status, result.answer, result.iterations) == ("finished", "sushi", 2)
    assert provider.requests[1].messages[-1] == Message("user", NUDGE)
    assert events(sink, "nudge")


def test_a_chatty_model_that_never_finishes_still_stops(sink: ListSink) -> None:
    provider = FakeProvider([say("hmm")] * 4)
    result = go(provider, sink, limits=Limits(max_iterations=4))
    assert result.status == "max_iterations"


def test_calls_after_finish_are_not_run(sink: ListSink) -> None:
    ran: list[str] = []

    def spy_run(arguments: dict[str, Any]) -> str:
        ran.append("spy")
        return ""

    spy = Tool("spy", "", {"type": "object"}, spy_run)
    provider = FakeProvider([call(finish("first"), ToolCall.of("spy", "c2"))])
    result = go(provider, sink, tools=(spy,))
    assert result.answer == "first"
    assert ran == []


def test_provider_failure_is_traced_then_raised(sink: ListSink) -> None:
    provider = FakeProvider([ProviderError("rate limited")])
    with pytest.raises(ProviderError, match="rate limited"):
        go(provider, sink)
    assert sink.types()[-2:] == ["error", "run_end"]
    assert events(sink, "run_end")[0].data["status"] == "provider_error"


# Tool errors


def test_tool_exception_is_returned_to_the_model_and_recorded(sink: ListSink) -> None:
    provider = FakeProvider([call(ToolCall.of("boom", "c1")), call(finish("recovered"))])
    result = go(provider, sink)
    assert result.status == "finished"
    shown = provider.requests[1].messages[-1]
    assert shown.role == "tool"
    assert shown.content == "RuntimeError: the kitchen is on fire"
    tool_result = events(sink, "tool_result")[0]
    assert tool_result.data["ok"] is False
    assert tool_result.data["output"] == "RuntimeError: the kitchen is on fire"


@pytest.mark.parametrize(
    ("bad_call", "expected"),
    [
        (ToolCall.of("teleport", "c1"), "unknown tool 'teleport'"),
        (ToolCall("c1", "echo", "{not json"), "not valid JSON"),
        (ToolCall("c1", "echo", "[1, 2]"), "must be a JSON object"),
        (ToolCall.of("echo", "c1"), "missing required arguments: text"),
    ],
)
def test_bad_tool_calls_become_error_results_and_the_run_continues(
    sink: ListSink, bad_call: ToolCall, expected: str
) -> None:
    provider = FakeProvider([call(bad_call), call(finish("ok"))])
    result = go(provider, sink)
    assert result.status == "finished"
    shown = provider.requests[1].messages[-1].content or ""
    assert expected in shown
    assert events(sink, "tool_result")[0].data["ok"] is False


def test_unknown_tool_error_lists_available_tools(sink: ListSink) -> None:
    provider = FakeProvider([call(ToolCall.of("teleport", "c1")), call(finish("ok"))])
    go(provider, sink)
    assert "available tools: boom, echo, finish" in (
        provider.requests[1].messages[-1].content or ""
    )


def test_malformed_finish_is_an_error_and_does_not_end_the_run(sink: ListSink) -> None:
    provider = FakeProvider([call(ToolCall.of("finish", "c1", answer=42)), call(finish("ok"))])
    result = go(provider, sink)
    assert (result.status, result.answer, result.iterations) == ("finished", "ok", 2)
    assert events(sink, "tool_result")[0].data["ok"] is False


def test_one_failing_call_does_not_stop_the_other_calls_in_the_same_turn(sink: ListSink) -> None:
    provider = FakeProvider(
        [
            call(ToolCall.of("boom", "c1"), ToolCall.of("echo", "c2", text="fine")),
            call(finish("ok")),
        ]
    )
    go(provider, sink)
    results = events(sink, "tool_result")
    assert [e.data["ok"] for e in results] == [False, True, True]  # boom, echo, finish
    tool_messages = [m for m in provider.requests[1].messages if m.role == "tool"]
    assert [m.tool_call_id for m in tool_messages] == ["c1", "c2"]


def test_reserved_and_duplicate_tool_names_are_rejected(sink: ListSink) -> None:
    provider = FakeProvider([])
    with pytest.raises(ValueError, match="reserved"):
        go(provider, sink, tools=(Tool("finish", "", {"type": "object"}, lambda a: ""),))
    with pytest.raises(ValueError, match="duplicate"):
        go(provider, sink, tools=(ECHO, ECHO))


# Trace output


def test_trace_records_every_step_in_order(sink: ListSink) -> None:
    provider = FakeProvider(
        [call(ToolCall.of("echo", "c1", text="hi")), say("hm"), call(finish("done"))]
    )
    go(provider, sink)
    assert sink.types() == [
        "run_start",
        "model_request",
        "model_response",
        "tool_call",
        "tool_result",
        "model_request",
        "model_response",
        "nudge",
        "model_request",
        "model_response",
        "tool_call",
        "tool_result",
        "run_end",
    ]


def test_trace_events_share_a_run_id_and_are_sequenced(sink: ListSink) -> None:
    provider = FakeProvider([call(finish("done"))])
    result = go(provider, sink)
    assert {e.run_id for e in sink.events} == {result.run_id}
    assert [e.seq for e in sink.events] == list(range(1, len(sink.events) + 1))


def test_trace_payloads(sink: ListSink) -> None:
    provider = FakeProvider(
        [call(ToolCall.of("echo", "c1", text="hi"), tokens=7), call(finish("done"))]
    )
    go(provider, sink, limits=Limits(max_iterations=5, token_budget=999))

    start = events(sink, "run_start")[0].data
    assert start == {
        "model": MODEL,
        "tools": ["echo", "boom", "finish"],
        "max_iterations": 5,
        "token_budget": 999,
        "input": "hungry",
    }
    response = events(sink, "model_response")[0].data
    assert response["tool_calls"] == [{"id": "c1", "name": "echo", "arguments": '{"text": "hi"}'}]
    assert response["usage"] == {"prompt_tokens": 7, "completion_tokens": 0}
    assert isinstance(response["duration_ms"], int)
    call_event = events(sink, "tool_call")[0].data
    assert (call_event["name"], call_event["call_id"]) == ("echo", "c1")
    result_event = events(sink, "tool_result")[0].data
    assert (result_event["ok"], result_event["output"]) == (True, "hi")
    end = events(sink, "run_end")[0].data
    assert end["status"] == "finished"
    assert end["answer"] == "done"
    assert end["iterations"] == 2
    assert end["usage"] == {"prompt_tokens": 17, "completion_tokens": 0}


def test_run_end_is_emitted_for_every_stop_reason(sink: ListSink) -> None:
    go(FakeProvider([say("hm")] * 2), sink, limits=Limits(max_iterations=2))
    assert sink.types()[-1] == "run_end"
    assert events(sink, "run_end")[0].data["status"] == "max_iterations"
