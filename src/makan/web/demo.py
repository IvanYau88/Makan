"""Offline stand-ins so the web channel runs with no API key and no network.

`DemoProvider` classifies a request by keyword, and `DemoPlaces` invents a fixed set of sample
places around whatever point is asked about, so "locate me" works anywhere on Earth.
Nothing here is real data, and the app says so whenever it runs on these.
"""

from __future__ import annotations

import json
import math
import re

from makan.places.base import Place, PlaceQuery, rank_nearby
from makan.providers.base import Completion, Message, ToolCall, ToolSpec, Usage

DEMO_SOURCE = "demo"
METERS_PER_DEGREE = 111_320
POLAR_LATITUDE = 89

CUISINES = (
    "thai",
    "ramen",
    "japanese",
    "sushi",
    "indian",
    "chinese",
    "korean",
    "mexican",
    "italian",
    "pizza",
    "burger",
    "vietnamese",
    "malay",
)
CATEGORIES = {"cafe": "cafe", "coffee": "cafe", "bakery": "bakery", "bar": "bar"}


class DemoProvider:
    """Answers the classification prompt from the words of the request. It is stateless."""

    def complete(self, *, model: str, messages: list[Message], tools: list[ToolSpec]) -> Completion:
        request = next((m.content or "" for m in reversed(messages) if m.role == "user"), "")
        words = set(re.findall(r"[a-z]+", request.lower()))
        intent: dict[str, object] = {
            "cuisine": next((c for c in CUISINES if c in words), None),
            "category": next((v for k, v in CATEGORIES.items() if k in words), None),
            "requirements": [],
        }
        call = ToolCall.of("finish", answer=json.dumps(intent))
        return Completion(Message("assistant", tool_calls=(call,)), Usage())


# Name, primary category, and a distance in meters with a bearing in degrees from the search point.
_SAMPLES = (
    ("Sample Thai Garden", "thai_restaurant", 140, 20),
    ("Sample Ramen House", "ramen_restaurant", 220, 80),
    ("Sample Corner Cafe", "cafe", 90, 150),
    ("Sample Noodle Bar", "noodle_restaurant", 310, 200),
    ("Sample Curry Leaf", "indian_restaurant", 380, 250),
    ("Sample Burger Shack", "burger_restaurant", 450, 300),
    ("Sample Sushi Counter", "sushi_restaurant", 520, 340),
    ("Sample Bakehouse", "bakery", 600, 45),
    ("Sample Thai Basil", "thai_restaurant", 760, 110),
    ("Sample Pizza Oven", "pizza_restaurant", 840, 190),
    ("Sample Taco Stand", "mexican_restaurant", 930, 270),
    ("Sample Dim Sum Palace", "chinese_restaurant", 1200, 320),
)


class DemoPlaces:
    """Sample places placed deterministically around the query point, run through real ranking."""

    name = DEMO_SOURCE

    def search_nearby(self, query: PlaceQuery) -> list[Place]:
        if abs(query.lat) >= POLAR_LATITUDE:
            return []  # nobody serves lunch at the poles, which gives demo mode a no-results case
        return rank_nearby(self._around(query.lat, query.lon), query)

    @staticmethod
    def _around(lat: float, lon: float) -> list[Place]:
        cos_lat = max(math.cos(math.radians(lat)), 0.01)
        places = []
        for i, (name, category, meters, bearing) in enumerate(_SAMPLES):
            north = meters * math.cos(math.radians(bearing)) / METERS_PER_DEGREE
            east = meters * math.sin(math.radians(bearing)) / (METERS_PER_DEGREE * cos_lat)
            places.append(
                Place(
                    id=f"demo-{i}",
                    name=name,
                    category=category,
                    categories=("food_and_drink", category),
                    lat=lat + north,
                    lon=lon + east,
                    address=f"{10 + i} Sample Street",
                )
            )
        return places
