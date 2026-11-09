"""Venue facts from OpenStreetMap, kept apart from the Overture facts on `Place`.

OpenStreetMap is licensed ODbL, so its facts live in their own object with their own provenance,
and anything that shows one must credit "© OpenStreetMap contributors" and link the licence.
Every fact here is a soft hint mapped by volunteers. None of them is verified, and none may decide
an allergy, a strict diet, an access need, or a never-go rule.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from makan.cuisines import cuisine_label, resolve
from makan.places.links import web_link

OSM_SOURCE = "openstreetmap"
OSM_LICENCE = "ODbL-1.0"
OSM_LICENCE_URL = "https://www.openstreetmap.org/copyright"
OSM_CREDIT = "© OpenStreetMap contributors"

# Closed allowlists. A tag value outside them is dropped, never guessed at.
SERVICE_VALUES: dict[str, frozenset[str]] = {
    "takeaway": frozenset({"yes", "only", "no"}),
    "delivery": frozenset({"yes", "only", "no"}),
    "outdoor_seating": frozenset({"yes", "no"}),
}
WHEELCHAIR_VALUES = frozenset({"yes", "limited", "no"})
DIET_KEYS: dict[str, str] = {
    "vegetarian": "Vegetarian",
    "vegan": "Vegan",
    "halal": "Halal",
    "kosher": "Kosher",
    "gluten_free": "Gluten-free",
    "lactose_free": "Lactose-free",
    "pescetarian": "Pescatarian",
}
DIET_LEVELS = ("only", "yes", "limited", "no")  # most to least, the order they are shown in
MAX_HOURS_CHARS = 255  # OpenStreetMap's own limit on a tag value
MAX_CUISINES = 8
_CUISINE_VALUE = re.compile(r"[a-z0-9_]{1,40}")
_CHECK_DATE = re.compile(r"(\d{4})-(\d{2})(?:-(\d{2}))?")
_ELEMENT_REF = re.compile(r"(node|way|relation)/[1-9][0-9]{0,15}")


@dataclass(frozen=True)
class FactSource:
    """Where a group of facts came from: the source, its data version, licence and retrieval date.

    `version` is the source's own data timestamp, such as OpenStreetMap's replication time for
    the extract, and `retrieved` is the day Makan built the extract from it.
    """

    source: str
    version: str
    licence: str
    retrieved: str

    def to_json(self) -> dict[str, str]:
        return {
            "source": self.source,
            "version": self.version,
            "licence": self.licence,
            "retrieved": self.retrieved,
        }

    @classmethod
    def from_json(cls, raw: object) -> FactSource | None:
        if not isinstance(raw, Mapping):
            return None
        fields = [raw.get(k) for k in ("source", "version", "licence", "retrieved")]
        if not all(isinstance(f, str) and f and len(f) <= 64 for f in fields):
            return None
        source, version, licence, retrieved = fields
        return cls(str(source), str(version), str(licence), str(retrieved))


@dataclass(frozen=True)
class OsmFacts:
    """What one OpenStreetMap element says about a venue, already checked against the allowlists.

    `ref` is the element, such as `node/123`, so a person can look at it on openstreetmap.org.
    `opening_hours` stays as the raw tag text and is read only when it is shown
    (`makan.places.hours`), so an unreadable value is shown as unreadable, never as closed.
    """

    ref: str
    source: FactSource
    cuisines: tuple[str, ...] = ()  # OpenStreetMap's own cuisine values, such as "peruvian"
    diets: tuple[tuple[str, str], ...] = ()  # (diet, level) pairs such as ("vegan", "only")
    takeaway: str | None = None
    delivery: str | None = None
    outdoor_seating: str | None = None
    wheelchair: str | None = None
    opening_hours: str | None = None
    menu_url: str | None = None
    check_date: str | None = None  # when a mapper last checked the venue, "YYYY-MM-DD" or "YYYY-MM"

    @property
    def has_facts(self) -> bool:
        return any(
            (
                self.cuisines,
                self.diets,
                self.takeaway,
                self.delivery,
                self.outdoor_seating,
                self.wheelchair,
                self.opening_hours,
                self.menu_url,
                self.check_date,
            )
        )

    def to_json(self) -> dict[str, Any]:
        """Compact JSON with only the facts that are known, plus the ref and the source."""
        fields: dict[str, Any] = {
            "ref": self.ref,
            "cuisines": list(self.cuisines) or None,
            "diets": {diet: level for diet, level in self.diets} or None,
            "takeaway": self.takeaway,
            "delivery": self.delivery,
            "outdoor_seating": self.outdoor_seating,
            "wheelchair": self.wheelchair,
            "opening_hours": self.opening_hours,
            "menu_url": self.menu_url,
            "check_date": self.check_date,
            "source": self.source.to_json(),
        }
        return {k: v for k, v in fields.items() if v is not None}

    @classmethod
    def from_json(cls, raw: object) -> OsmFacts | None:
        """Read `to_json` output back, checking every value again. Anything malformed is None."""
        if not isinstance(raw, Mapping):
            return None
        ref = raw.get("ref")
        source = FactSource.from_json(raw.get("source"))
        if not isinstance(ref, str) or not _ELEMENT_REF.fullmatch(ref) or source is None:
            return None
        diets = raw.get("diets")
        tags: dict[str, str] = {}
        if isinstance(diets, Mapping):
            tags.update({f"diet:{k}": v for k, v in diets.items() if isinstance(v, str)})
        cuisines = raw.get("cuisines")
        if isinstance(cuisines, list):
            tags["cuisine"] = ";".join(c for c in cuisines if isinstance(c, str))
        for key in (*SERVICE_VALUES, "wheelchair", "opening_hours", "check_date"):
            value = raw.get(key)
            if isinstance(value, str):
                tags[key] = value
        menu = raw.get("menu_url")
        if isinstance(menu, str):
            tags["website:menu"] = menu
        facts = facts_from_tags(ref, tags, source)
        return facts if facts.has_facts else None


def facts_from_tags(ref: str, tags: Mapping[str, str], source: FactSource) -> OsmFacts:
    """The facts an element's tags state, keeping only allowlisted keys and values.

    Source data is not trusted: an unknown value, an overlong string, a malformed date, or an
    unsafe link is dropped, so the page and the model see only what the allowlists permit.
    """
    services = {
        key: value
        for key, allowed in SERVICE_VALUES.items()
        if (value := _token(tags.get(key))) in allowed
    }
    wheelchair = _token(tags.get("wheelchair"))
    diets = sorted(
        (
            (diet, level)
            for diet in DIET_KEYS
            if (level := _token(tags.get(f"diet:{diet}"))) in DIET_LEVELS
        ),
        key=lambda pair: (DIET_LEVELS.index(pair[1]), list(DIET_KEYS).index(pair[0])),
    )
    return OsmFacts(
        ref=ref,
        source=source,
        cuisines=parse_cuisines(tags.get("cuisine")),
        diets=tuple(diets),
        takeaway=services.get("takeaway"),
        delivery=services.get("delivery"),
        outdoor_seating=services.get("outdoor_seating"),
        wheelchair=wheelchair if wheelchair in WHEELCHAIR_VALUES else None,
        opening_hours=_hours_text(tags.get("opening_hours")),
        menu_url=web_link(tags.get("website:menu")),
        check_date=_check_date(tags.get("check_date")),
    )


def parse_cuisines(raw: object) -> tuple[str, ...]:
    """OpenStreetMap's `cuisine` tag, `donut;coffee_shop`, as clean values in their given order."""
    if not isinstance(raw, str):
        return ()
    values = [v.strip().lower().replace(" ", "_") for v in raw.split(";")]
    kept = [v for v in dict.fromkeys(values) if _CUISINE_VALUE.fullmatch(v)]
    return tuple(kept[:MAX_CUISINES])


def cuisine_value_label(value: str) -> str:
    """A label for one OpenStreetMap cuisine value: the vocabulary's own when it names a term,
    such as "Burgers" for `burger`, else the value in words, such as "Coffee shop".

    This is for display only. OpenStreetMap values never become fit evidence here.
    """
    term = resolve(value.replace("_", " "))
    return cuisine_label(term) if term else value.replace("_", " ").capitalize()


def osm_url(ref: str) -> str:
    """The element's page on openstreetmap.org, where a person can check or correct it."""
    return f"https://www.openstreetmap.org/{ref}"


def _token(raw: object) -> str | None:
    return raw.strip().lower() if isinstance(raw, str) else None


def _hours_text(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    text = " ".join(raw.split())
    if not text or len(text) > MAX_HOURS_CHARS or not text.isprintable():
        return None
    return text


def _check_date(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    found = _CHECK_DATE.fullmatch(raw.strip())
    if found is None:
        return None
    year, month, day = found.groups()
    try:
        date(int(year), int(month), int(day or 1))
    except ValueError:
        return None
    return found.group(0)
