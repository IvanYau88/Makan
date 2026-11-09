"""The regional OpenStreetMap extract's format, shared by its builder and its reader."""

from __future__ import annotations

import re
from dataclasses import dataclass

EXTRACT_FORMAT = "makan-osm-food-v1"
NAME_KEYS = ("name", "name:en", "official_name", "alt_name")
HOUSE_NUMBER_KEY = "addr:housenumber"  # kept only to reject a same-named branch at another address
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")  # OpenStreetMap's data time


class ExtractError(Exception):
    """The extract could not be built."""


@dataclass(frozen=True)
class Box:
    west: float
    south: float
    east: float
    north: float

    @classmethod
    def parse(cls, text: str) -> Box:
        """`west,south,east,north` in degrees, the order GeoJSON and Overture use."""
        try:
            west, south, east, north = (float(part) for part in text.split(","))
        except ValueError:
            raise ExtractError(f"a box is west,south,east,north in degrees, got {text!r}") from None
        box = cls(west, south, east, north)
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ExtractError(f"the box {text!r} is empty or outside the globe")
        return box

    def text(self) -> str:
        return ",".join(f"{v:.6f}" for v in (self.west, self.south, self.east, self.north))
