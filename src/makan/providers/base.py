"""Provider-neutral message types and the adapter interface.

The shapes follow the chat-completions convention that most providers speak,
but nothing here depends on a particular provider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]


class ProviderError(Exception):
    """The provider could not produce a completion."""


class ProviderBusy(ProviderError):
    """The provider is rate limited or temporarily unavailable, so trying again later may work."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text exactly as the model wrote it, parsed by the loop

    @classmethod
    def of(cls, name: str, call_id: str = "call_1", **arguments: Any) -> ToolCall:
        return cls(id=call_id, name=name, arguments=json.dumps(arguments))


@dataclass(frozen=True)
class Message:
    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None  # set on role="tool" results


@dataclass(frozen=True)
class ToolSpec:
    """What the model is told about a tool. `parameters` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
        )


@dataclass(frozen=True)
class Completion:
    message: Message
    usage: Usage = Usage()


class Provider(Protocol):
    def complete(self, *, model: str, messages: list[Message], tools: list[ToolSpec]) -> Completion:
        """Return the next assistant message. Raise ProviderError on failure."""
        ...
