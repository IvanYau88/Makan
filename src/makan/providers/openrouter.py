"""OpenRouter adapter (https://openrouter.ai/docs), over its chat-completions endpoint."""

from __future__ import annotations

import json
from typing import Any

import httpx

from makan.providers.base import (
    Completion,
    Message,
    ProviderError,
    ToolCall,
    ToolSpec,
    Usage,
)

BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError("OPENROUTER_API_KEY is not set")
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._client = client or httpx.Client(timeout=timeout)

    def complete(self, *, model: str, messages: list[Message], tools: list[ToolSpec]) -> Completion:
        body: dict[str, Any] = {"model": model, "messages": [_wire_message(m) for m in messages]}
        if tools:
            body["tools"] = [_wire_tool(t) for t in tools]
        try:
            response = self._client.post(self._url, headers=self._headers, json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenRouter request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise ProviderError(
                f"OpenRouter returned {response.status_code}: {response.text[:300]}"
            )
        try:
            return _parse(response.json())
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"OpenRouter response was malformed: {exc!r}") from exc


def _wire_tool(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _wire_message(message: Message) -> dict[str, Any]:
    wire: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": c.arguments},
            }
            for c in message.tool_calls
        ]
    if message.tool_call_id is not None:
        wire["tool_call_id"] = message.tool_call_id
    return wire


def _arguments(function: dict[str, Any]) -> str:
    # `ToolCall.arguments` is always JSON text; some upstreams send an object or null instead.
    arguments = function.get("arguments")
    return arguments if isinstance(arguments, str) else json.dumps(arguments)


def _parse(data: dict[str, Any]) -> Completion:
    # OpenRouter can answer 200 with an error object, for example when an upstream model fails.
    if "error" in data:
        raise ProviderError(f"OpenRouter error: {str(data['error'])[:300]}")
    raw = data["choices"][0]["message"]
    calls = tuple(
        ToolCall(id=c["id"], name=c["function"]["name"], arguments=_arguments(c["function"]))
        for c in raw.get("tool_calls") or []
    )
    usage = data.get("usage") or {}
    return Completion(
        message=Message("assistant", raw.get("content") or None, calls),
        usage=Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)),
    )
