"""The tool interface: a name, a description for the model, a JSON Schema, and a function."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from makan.providers.base import ToolSpec


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object describing the arguments
    run: Callable[[dict[str, Any]], str]  # raise to report failure; the loop records it

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.parameters)


# Argument readers for `run` functions. The loop only checks that required arguments are present,
# so each tool checks the types and ranges itself. A ValueError reaches the model as the tool error.


def number_arg(arguments: dict[str, Any], name: str) -> float:
    value = arguments[name]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number")
    return float(value)


def int_arg(arguments: dict[str, Any], name: str, default: int) -> int:
    value = arguments.get(name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float) or value != int(value):
        raise ValueError(f"{name} must be a whole number")
    return int(value)


def text_arg(arguments: dict[str, Any], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value.strip() or None
