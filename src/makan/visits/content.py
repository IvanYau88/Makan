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

from makan.cuisines import CUISINE_IDS, DIET_LABELS
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
# A place's facts kept on a visit, matching the bounds in migration 0005.
MAX_PLACE_CUISINES = 20
MAX_PLACE_DIETS = 10
MAX_PLACE_DISHES = 50

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
class PlaceFacts:
    """What a place listed when it was rated, kept on the visit for the owner's taste model.

    `cuisines` are vocabulary ids (`makan.cuisines`) and `diets` the source's soft diet labels,
    each unique and in the order given. `dishes` are the dish names the place lists, when they
    were known (empty otherwise), and `hours_known` says whether its weekly hours were readable.
    None of these is ever a safety fact: a listed diet decides nothing.
    """

    cuisines: tuple[str, ...] = ()
    diets: tuple[str, ...] = ()
    dishes: tuple[str, ...] = ()
    hours_known: bool = False


@dataclass(frozen=True)
class PlaceRef:
    """A restaurant as the places layer names it: the provider family and that provider's id.

    `facts` is what the place listed, when the client sent it, to keep with a rating.
    """

    source: str
    id: str
    name: str
    address: str | None = None
    facts: PlaceFacts | None = None


def parse_place_facts(
    cuisines: Sequence[str],
    diets: Sequence[str],
    dishes: Sequence[str] = (),
    hours_known: bool = False,
) -> PlaceFacts:
    """Check place facts against the vocabularies, so only known ids and bounded text are kept.

    Repeated ids are kept once. A dish name is cleaned like a typed one, and an empty one is
    dropped, since a listed menu can have blank entries.
    """
    if len(cuisines) > MAX_PLACE_CUISINES or not set(cuisines) <= CUISINE_IDS:
        raise InvalidVisit("The place's cuisines must be terms from the cuisine vocabulary.")
    if len(diets) > MAX_PLACE_DIETS or not set(diets) <= set(DIET_LABELS):
        raise InvalidVisit("The place's diets must be known diet labels.")
    if len(dishes) > MAX_PLACE_DISHES:
        raise InvalidVisit(f"Send {MAX_PLACE_DISHES} of the place's dishes or fewer.")
    if type(hours_known) is not bool:
        raise InvalidVisit("Say whether the place's hours are known.")
    names = (clean_text(d, "dish name", max_chars=MAX_DISH_NAME_CHARS) for d in dishes)
    return PlaceFacts(
        tuple(dict.fromkeys(cuisines)),
        tuple(dict.fromkeys(diets)),
        tuple(name for name in names if name),
        hours_known,
    )


def parse_place(
    data_source: str,
    place_id: str,
    name: str,
    address: str | None = None,
    facts: PlaceFacts | None = None,
) -> PlaceRef:
    """Qualify a place id by its provider, so two venues never merge by name or across providers.

    `data_source` is the places provider's `name` (such as `overture:2026-09-23.1`), which the
    search response carries as `data_source`. Only the family before the colon is kept, since the
    release is a version of the data and not part of what the venue is. `facts` are already
    checked by `parse_place_facts`.
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
        facts,
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
