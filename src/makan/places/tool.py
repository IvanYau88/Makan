"""The `search_nearby_places` tool: the model's way to find food near a location."""

from __future__ import annotations

import json
from typing import Any

from makan.places.base import (
    MAX_LIMIT,
    MAX_RADIUS_M,
    MIN_RADIUS_M,
    Place,
    PlaceQuery,
    PlacesProvider,
)
from makan.tools import Tool, int_arg, number_arg, text_arg

NAME = "search_nearby_places"
DEFAULT_RADIUS_M = 1_000
DEFAULT_LIMIT = 10

DESCRIPTION = (
    "Find places to eat or drink near a location, nearest first. "
    "Each result has an id, name, category, distance in meters, and address when known. "
    "Use cuisine for a kind of food (such as thai, ramen, or indian) and category for a kind of "
    "venue (such as cafe, bakery, bar, or fast food). "
    "If a search finds nothing, widen the radius or drop a filter. "
    "This data has no opening hours, ratings, prices, or menus, "
    "and it can include places that are not good to recommend. "
    "The location is rounded to about 100 meters."
)

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "latitude": {"type": "number", "description": "Latitude of the search center, in degrees."},
        "longitude": {
            "type": "number",
            "description": "Longitude of the search center, in degrees.",
        },
        "radius_m": {
            "type": "integer",
            "description": (
                f"Search radius in meters, {MIN_RADIUS_M} to {MAX_RADIUS_M}. "
                f"Default {DEFAULT_RADIUS_M}."
            ),
        },
        "cuisine": {"type": "string", "description": "Optional kind of food, such as thai."},
        "category": {
            "type": "string",
            "description": "Optional kind of venue, such as cafe or bakery.",
        },
        "limit": {
            "type": "integer",
            "description": f"Most results to return, 1 to {MAX_LIMIT}. Default {DEFAULT_LIMIT}.",
        },
    },
    "required": ["latitude", "longitude"],
}


def search_nearby_places(provider: PlacesProvider) -> Tool:
    def run(arguments: dict[str, Any]) -> str:
        query = PlaceQuery.near(
            number_arg(arguments, "latitude"),
            number_arg(arguments, "longitude"),
            int_arg(arguments, "radius_m", DEFAULT_RADIUS_M),
            cuisine=text_arg(arguments, "cuisine"),
            category=text_arg(arguments, "category"),
            limit=int_arg(arguments, "limit", DEFAULT_LIMIT),
        )
        return _render(query, provider.search_nearby(query))

    return Tool(NAME, DESCRIPTION, PARAMETERS, run)


def _render(query: PlaceQuery, places: list[Place]) -> str:
    """Compact JSON: the search as it was run, then one short object per place."""
    searched = {
        "latitude": query.lat,
        "longitude": query.lon,
        "radius_m": query.radius_m,
        "cuisine": query.cuisine,
        "category": query.category,
    }
    return json.dumps(
        {
            "searched": {k: v for k, v in searched.items() if v is not None},
            "count": len(places),
            "places": [_compact(p) for p in places],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _compact(place: Place) -> dict[str, Any]:
    fields = {
        "id": place.id,
        "name": place.name,
        "category": place.category,
        "distance_m": place.distance_m,
        "address": place.address,
    }
    return {k: v for k, v in fields.items() if v is not None}
