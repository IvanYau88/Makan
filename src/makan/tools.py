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
