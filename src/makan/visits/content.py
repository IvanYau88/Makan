"""Cleaning and checking what a person types into a visit, the same way wherever it comes in.

Text is kept as typed apart from three things: it is normalized to NFC (the same characters, one
spelling), control and formatting characters are removed (which include the bidirectional overrides
that make text read backwards), and the ends are trimmed. Nothing is folded to lower case, merged,
or reordered, so a dish name and the tags on it come back exactly as the person wrote them. A value
that is too long is refused and never cut short, so the person sees what is kept.

Ratings are exact decimals, rounded half up to one decimal place after the range is checked, so
10.04 is refused and 9.96 is saved as 10.0.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from makan.visits.errors import InvalidVisit

MIN_RATING = Decimal(0)
MAX_RATING = Decimal(10)
RATING_STEP = Decimal("0.1")
MAX_RATING_CHARS = 32  # what is read as a number, so a huge string is refused before parsing

MAX_PLACE_NAME_CHARS = 200
MAX_PLACE_ADDRESS_CHARS = 300
MAX_PLACE_ID_CHARS = 200
MAX_DESCRIPTION_CHARS = 2000
MAX_DISH_NAME_CHARS = 120
MAX_DISH_COMMENT_CHARS = 1000
MAX_TAG_CHARS = 40
MAX_TAGS_PER_DISH = 20
MAX_DISHES = 50
MAX_COMPANIONS = 10

EARLIEST_VISIT = date(2000, 1, 1)
_SOURCE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_REMOVED = ("Cc", "Cf", "Cs", "Co", "Cn")


def parse_rating(raw: object, label: str = "rating") -> Decimal:
    """A rating from 0 to 10 as an exact decimal with one decimal place."""
    if isinstance(raw, bool) or not isinstance(raw, int | float | str | Decimal):
        raise InvalidVisit(f"The {label} must be a number from 0 to 10.")
    text = raw.strip() if isinstance(raw, str) else str(raw)
    if len(text) > MAX_RATING_CHARS:
        raise InvalidVisit(f"The {label} must be a number from 0 to 10.")
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(text)
    except InvalidOperation:
        raise InvalidVisit(f"The {label} must be a number from 0 to 10.") from None
    if not value.is_finite():
        raise InvalidVisit(f"The {label} must be a number from 0 to 10.")
    if not MIN_RATING <= value <= MAX_RATING:
        raise InvalidVisit(f"The {label} must be from 0 to 10.")
    rounded = value.quantize(RATING_STEP, rounding=ROUND_HALF_UP)
    return Decimal("0.0") if rounded == 0 else rounded  # never "-0.0"


def clean_text(raw: str, label: str, *, max_chars: int, multiline: bool = False) -> str:
    """The text as it will be stored, possibly empty."""
    text = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")
    kept = []
    for ch in text:
        if ch in "\n\t" and multiline:
            kept.append(ch)
        elif ch.isspace():
            kept.append(" ")
        elif unicodedata.category(ch) not in _REMOVED:
            kept.append(ch)
    cleaned = "".join(kept).strip()
    if len(cleaned) > max_chars:
        raise InvalidVisit(f"Use {max_chars} characters or fewer for the {label}.")
    return cleaned


def clean_optional(
    raw: str | None, label: str, *, max_chars: int, multiline: bool = False
) -> str | None:
    """Like `clean_text`, with nothing left meaning no value."""
    if raw is None:
        return None
    return clean_text(raw, label, max_chars=max_chars, multiline=multiline) or None


def clean_description(raw: str | None) -> str | None:
    return clean_optional(raw, "description", max_chars=MAX_DESCRIPTION_CHARS, multiline=True)


@dataclass(frozen=True)
class PlaceRef:
    """A restaurant as the places layer names it: the provider family and that provider's id."""

    source: str
    id: str
    name: str
    address: str | None = None


def parse_place(data_source: str, place_id: str, name: str, address: str | None = None) -> PlaceRef:
    """Qualify a place id by its provider, so two venues never merge by name or across providers.

    `data_source` is the places provider's `name` (such as `overture:2026-09-23.1`), which the
    search response carries as `data_source`. Only the family before the colon is kept, since the
    release is a version of the data and not part of what the venue is.
    """
    source = data_source.split(":", 1)[0].strip().lower()
    if not _SOURCE.match(source):
        raise InvalidVisit("The place needs the data source it came from.")
    clean_id = clean_text(place_id, "place id", max_chars=MAX_PLACE_ID_CHARS)
    if not clean_id:
        raise InvalidVisit("The place needs an id.")
    clean_name = clean_text(name, "place name", max_chars=MAX_PLACE_NAME_CHARS)
    if not clean_name:
        raise InvalidVisit("The place needs a name.")
    return PlaceRef(
        source,
        clean_id,
        clean_name,
        clean_optional(address, "place address", max_chars=MAX_PLACE_ADDRESS_CHARS),
    )


def clean_dish_name(raw: str) -> str:
    name = clean_text(raw, "dish name", max_chars=MAX_DISH_NAME_CHARS)
    if not name:
        raise InvalidVisit("Name each dish.")
    return name


def clean_dish_tags(raw: Sequence[str]) -> tuple[str, ...]:
    """The tags as typed, in order, with repeats kept."""
    if len(raw) > MAX_TAGS_PER_DISH:
        raise InvalidVisit(f"Use {MAX_TAGS_PER_DISH} tags or fewer on a dish.")
    tags = tuple(clean_text(tag, "tag", max_chars=MAX_TAG_CHARS) for tag in raw)
    if not all(tags):
        raise InvalidVisit("A tag cannot be empty.")
    return tags


def check_visit_date(day: date, today: date) -> date:
    """A visit already happened, so it cannot be dated after tomorrow (a day of slack for zones)."""
    if day < EARLIEST_VISIT or day > today + timedelta(days=1):
        raise InvalidVisit("Choose the day of the meal, not one in the future.")
    return day
