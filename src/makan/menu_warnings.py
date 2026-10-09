"""Plain-code allergen warnings from dated menu text. No scorer, model, or network is involved.

A restaurant whose menu mentions a stated allergen is kept, and the person is told so. The warning
says what is known (the menu mentions it, from which source and as of when) and what cannot be
verified (the ingredients of their order, and cross-contact). It never says the place is safe, and
a menu that does not mention an allergen gets no warning here, because that proves nothing either
way. The existing "Cannot verify" warnings cover the rest.

Nothing calls this yet. It exists so the warning path is a plain function that the isolation tests
can run with a scorer that is off, wrong, or failing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class MenuText:
    """A piece of a restaurant's menu, with where it came from and when it was seen."""

    text: str
    source: str  # such as "restaurant menu page" or a URL
    observed_on: date | None = None  # None when the source carries no date


def allergen_warnings(allergies: Iterable[str], menu: Iterable[MenuText]) -> tuple[str, ...]:
    """One warning per stated allergen that the menu text mentions, in the order stated."""
    pieces = tuple(menu)
    warnings = []
    for allergen in dict.fromkeys(a.strip().casefold() for a in allergies if a.strip()):
        pattern = _mention(allergen)
        found = [m for m in pieces if pattern.search(m.text)]
        if found:
            warnings.append(_warning(allergen, found))
    return tuple(warnings)


def _mention(allergen: str) -> re.Pattern[str]:
    """The allergen as a whole word, singular or plural. 'peanut' finds 'peanuts', never 'nuts'."""
    stem = allergen[:-1] if allergen.endswith("s") and len(allergen) > 3 else allergen
    return re.compile(rf"\b{re.escape(stem)}s?\b", re.IGNORECASE)


def _warning(allergen: str, found: list[MenuText]) -> str:
    verb = "appear" if allergen.endswith("s") else "appears"
    sources = "; ".join(
        dict.fromkeys(
            f"{m.source}, {m.observed_on.isoformat() if m.observed_on else 'date unknown'}"
            for m in found
        )
    )
    return (
        f"{allergen.capitalize()} {verb} on this restaurant's menu, which conflicts with your "
        f"no-{allergen} requirement; we cannot verify ingredients for your order or "
        f"cross-contact. Source: {sources}."
    )
