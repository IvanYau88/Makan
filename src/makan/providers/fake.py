"""A scripted provider for tests. It never touches the network."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from makan.providers.base import Completion, Message, ProviderError, ToolCall, ToolSpec, Usage


@dataclass(frozen=True)
class Request:
    model: str
    messages: list[Message]
    tools: list[ToolSpec]


class FakeProvider:
    """Replays a fixed script of completions, one per call, and records every request."""

    def __init__(self, script: Iterable[Completion | ProviderError]) -> None:
        self._script = list(script)
        self.requests: list[Request] = []

    def complete(self, *, model: str, messages: list[Message], tools: list[ToolSpec]) -> Completion:
        # Copy the lists: the loop keeps appending to its own.
        self.requests.append(Request(model, list(messages), list(tools)))
        if len(self.requests) > len(self._script):
            raise AssertionError("FakeProvider script exhausted")
        step = self._script[len(self.requests) - 1]
        if isinstance(step, ProviderError):
            raise step
        return step


def say(text: str, *, tokens: int = 10) -> Completion:
    """A plain text reply with no tool calls."""
    return Completion(Message("assistant", text), Usage(tokens, 0))


def call(*calls: ToolCall, tokens: int = 10) -> Completion:
    """An assistant turn that requests one or more tool calls."""
    return Completion(Message("assistant", tool_calls=calls), Usage(tokens, 0))
