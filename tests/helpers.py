import json
from pathlib import Path
from typing import Any

from makan.places import Place
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


# Places

FIXTURES = Path(__file__).parent / "fixtures"
KLCC = (3.148, 101.695)  # the center of the Overture fixture, rounded the way the tool rounds


def overture_rows() -> list[dict[str, Any]]:
    """Real Overture places near Kuala Lumpur city center, shaped like the provider's SQL output."""
    rows: list[dict[str, Any]] = json.loads((FIXTURES / "overture_kl.json").read_text("utf-8"))
    return rows


def place(name: str, category: str, lat: float, lon: float, *labels: str) -> Place:
    return Place(
        id=name.lower().replace(" ", "-"),
        name=name,
        category=category,
        categories=(*labels, category),
        lat=lat,
        lon=lon,
    )
