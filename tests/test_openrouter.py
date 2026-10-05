"""The OpenRouter adapter against a mock transport. No real network calls."""

import json
from typing import Any

import httpx
import pytest

from makan.loop import run
from makan.providers import (
    Message,
    OpenRouterProvider,
    ProviderBusy,
    ProviderError,
    ToolCall,
    ToolSpec,
    Usage,
)
from makan.trace import ListSink
from tests.helpers import ECHO

TOOLS = [ToolSpec("echo", "Echo.", {"type": "object", "properties": {}})]


def provider_with(handler: Any) -> OpenRouterProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenRouterProvider("sk-test", client=client)


def test_request_shape_and_text_response() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    history = [
        Message("user", "go"),
        Message("assistant", tool_calls=(ToolCall("c1", "echo", '{"text": "x"}'),)),
        Message("tool", "x", tool_call_id="c1"),
    ]
    completion = provider_with(handler).complete(model="a/b", messages=history, tools=TOOLS)

    assert completion.message == Message("assistant", "hi")
    assert completion.usage == Usage(5, 2)
    (request,) = seen
    assert request.url == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body["model"] == "a/b"
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert body["messages"][1]["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": '{"text": "x"}'}}
    ]
    assert body["messages"][2] == {"role": "tool", "content": "x", "tool_call_id": "c1"}


def test_tool_calls_are_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c9",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"text": "x"}'},
                }
            ],
        }
        return httpx.Response(200, json={"choices": [{"message": message}]})

    completion = provider_with(handler).complete(
        model="m", messages=[Message("user", "go")], tools=TOOLS
    )
    assert completion.message.tool_calls == (ToolCall("c9", "echo", '{"text": "x"}'),)
    assert completion.message.content is None
    assert completion.usage == Usage()


@pytest.mark.parametrize(
    ("sent", "expected"),
    [({"text": "x"}, '{"text": "x"}'), (None, "null"), ([1], "[1]")],
)
def test_non_string_tool_arguments_are_reserialized_as_json_text(sent: Any, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        message = {
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": sent}}
            ]
        }
        return httpx.Response(200, json={"choices": [{"message": message}]})

    completion = provider_with(handler).complete(
        model="m", messages=[Message("user", "go")], tools=TOOLS
    )
    assert completion.message.tool_calls == (ToolCall("c1", "echo", expected),)


@pytest.mark.parametrize("sent", [None, [1], "not json"])
def test_malformed_finish_arguments_from_the_provider_do_not_crash_the_run(
    sent: Any, sink: ListSink
) -> None:
    replies = iter(
        [
            {"id": "c1", "function": {"name": "finish", "arguments": sent}},
            {"id": "c2", "function": {"name": "finish", "arguments": '{"answer": "ok"}'}},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        message = {"tool_calls": [next(replies)]}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    result = run("hungry", provider=provider_with(handler), model="m", tools=(ECHO,), sink=sink)

    assert (result.status, result.answer) == ("finished", "ok")
    assert next(e.data["ok"] for e in sink.events if e.type == "tool_result") is False
    assert sink.events[-1].type == "run_end"


def test_tools_are_omitted_when_there_are_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "tools" not in json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider_with(handler).complete(model="m", messages=[Message("user", "go")], tools=[])


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, text="slow down"),
        httpx.Response(200, json={"error": {"message": "upstream failed"}}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, text="<html>"),
    ],
)
def test_failures_become_provider_errors(response: httpx.Response) -> None:
    with pytest.raises(ProviderError):
        provider_with(lambda request: response).complete(model="m", messages=[], tools=[])


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, text="rate limited upstream"),
        httpx.Response(500, text="oops"),
        httpx.Response(503, text="overloaded"),
        httpx.Response(200, json={"error": {"code": 429, "message": "rate limited"}}),
        httpx.Response(200, json={"error": {"code": "502", "message": "upstream down"}}),
    ],
)
def test_a_rate_limit_or_server_failure_is_a_busy_provider(response: httpx.Response) -> None:
    with pytest.raises(ProviderBusy):
        provider_with(lambda request: response).complete(model="m", messages=[], tools=[])


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, text="bad request"),
        httpx.Response(401, text="no such key"),
        httpx.Response(404, text="no such model"),
        httpx.Response(200, json={"error": {"code": 400, "message": "bad"}}),
        httpx.Response(200, json={"error": "plain text"}),
        httpx.Response(200, json={"choices": []}),
    ],
)
def test_other_failures_are_not_busy(response: httpx.Response) -> None:
    with pytest.raises(ProviderError) as caught:
        provider_with(lambda request: response).complete(model="m", messages=[], tools=[])
    assert not isinstance(caught.value, ProviderBusy)


def test_network_failure_becomes_a_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(ProviderError, match="ConnectError"):
        provider_with(handler).complete(model="m", messages=[], tools=[])


def test_missing_api_key_is_an_error() -> None:
    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY"):
        OpenRouterProvider("")
