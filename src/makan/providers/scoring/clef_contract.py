"""The frozen Clef state contracts, each with its own schema, manifest hash and policy version.

Version 1 is the slice 1 guest-cuisine state, unchanged. Version 2 adds the owner's own bounded
history summaries. Both use the same frozen questions and acceptance policy in `contract.json`.
A state's top-level keys select exactly one contract, so a version is never guessed or mixed.
The gateway applies the same rules to the same files.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from makan.providers.scoring.transport import _strict_json


@dataclass(frozen=True)
class StateContract:
    version: int
    manifest: str  # the manifest's content_sha256, which the gateway pins
    policy_version: str
    schema: Mapping[str, Any]

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(self.schema["required"])


def _read(*parts: str) -> Any:
    root = resources.files("makan").joinpath("evals", "sets", "clef")
    for part in parts[:-1]:
        root = root.joinpath(part)
    return _strict_json(root.joinpath(parts[-1]).read_text("utf-8"))


@cache
def contracts() -> tuple[StateContract, ...]:
    v1 = _read("manifest.json")
    v2 = _read("v2", "manifest.json")
    if v2["extends"] != v1["content_sha256"]:
        raise ValueError("state contract 2 must extend the frozen version 1 manifest")
    return (
        StateContract(
            1, v1["content_sha256"], _read("contract.json")["version"], _read("state.schema.json")
        ),
        StateContract(
            2, v2["content_sha256"], v2["policy_version"], _read("v2", "state.schema.json")
        ),
    )


def contract_for(state: Mapping[str, Any]) -> StateContract:
    if not isinstance(state, Mapping):
        raise ValueError("state must be an object")
    for contract in contracts():
        if set(state) == contract.keys:
            return contract
    raise ValueError("state matches no frozen contract")


def check(schema: Mapping[str, Any], value: Any) -> None:
    """The small JSON Schema subset the frozen contracts use, applied strictly.

    Objects have exactly their required keys, arrays are bounded and unique (by whole item and
    by the `x-unique-by` fields), and strings match a const, enum, pattern or length bound.
    """
    if "const" in schema:
        if value != schema["const"] or not isinstance(value, str):
            raise ValueError("invalid constant")
        return
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["required"]):
            raise ValueError("unexpected object")
        if set(schema["required"]) != set(schema["properties"]):
            raise ValueError("schema properties must all be required")
        for name, spec in schema["properties"].items():
            check(spec, value[name])
    elif kind == "array":
        if not isinstance(value, list) or len(value) > schema.get("maxItems", len(value)):
            raise ValueError("invalid array")
        for item in value:
            check(schema["items"], item)
        encoded = [json.dumps(item, sort_keys=True) for item in value]
        if schema.get("uniqueItems") and len(set(encoded)) != len(encoded):
            raise ValueError("duplicate item")
        unique_by = schema.get("x-unique-by")
        if unique_by is not None:
            keys = [tuple(item[k] for k in unique_by) for item in value]
            if len(set(keys)) != len(keys):
                raise ValueError("duplicate item")
    elif kind == "string":
        if not isinstance(value, str):
            raise ValueError("invalid string")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError("invalid item")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ValueError("invalid item")
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", len(value)):
            raise ValueError("invalid length")
    else:
        raise ValueError("unsupported schema type")


def has_taste(state: Mapping[str, Any]) -> bool:
    """Some soft evidence to judge: a stated taste, or (in version 2) a history summary."""
    profile = state["profile"]
    if profile["likes"] or profile["dislikes"]:
        return True
    history = state.get("history")
    return bool(
        history
        and (
            history["cuisines"]
            or history["dishes"]
            or history["tags"]
            or history["this_place"]["visits"] != "none"
        )
    )


def validated(state: Mapping[str, Any]) -> StateContract:
    """Validate one state against the contract its keys select; any problem raises ValueError."""
    contract = contract_for(state)
    check(contract.schema, dict(state))
    if not has_taste(state):
        raise ValueError("no taste profile")
    return contract
