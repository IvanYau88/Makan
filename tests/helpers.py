from typing import Any

from makan.tools import Tool


def _echo(arguments: dict[str, Any]) -> str:
    return str(arguments["text"])


ECHO = Tool(
    name="echo",
    description="Return the given text unchanged.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    run=_echo,
)


def _boom(arguments: dict[str, Any]) -> str:
    raise RuntimeError("the kitchen is on fire")


BOOM = Tool(
    name="boom",
    description="Always fails.",
    parameters={"type": "object", "properties": {}},
    run=_boom,
)
