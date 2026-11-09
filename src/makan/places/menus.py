"""Dish facts read from a restaurant's own website, and what one read of it came to.

They are facts only: a dish name, an optional price, and where and when they were read. No page
text, menu description, section prose or image is ever kept. Every name is untrusted text from a
third-party page, so it is checked here before anything shows it (`makan.places.menu_extract`).
None of it is verified, and none of it may decide an allergy, a strict diet or a never-go rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

# found: dishes were read. none: the page was read and had no dish list Makan could read.
# refused: the site asked not to be read (robots.txt, AI crawler rules, 401 or 403).
# platform: the link is an ordering, delivery, review or social platform, which Makan never reads.
# unreadable: the menu is a PDF, an image or another format, or too large.
# unavailable: the site did not answer, timed out, failed, or the page is gone.
MenuStatus = Literal["found", "none", "refused", "platform", "unreadable", "unavailable"]
Method = Literal["json_ld", "microdata", "html"]

MAX_DISHES = 50
MAX_DISH_CHARS = 80
_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}  # only for a currency the page named by code


@dataclass(frozen=True)
class Price:
    """A price as the page states it. `currency` is an ISO code only when the page says which:
    a bare "$" could be US, Canadian or another dollar, so it stays None with the symbol kept."""

    amount: str  # "12.95", or "12" for a whole amount
    currency: str | None = None
    symbol: str | None = None

    @property
    def label(self) -> str:
        symbol = self.symbol or _SYMBOLS.get(self.currency or "")
        if symbol:
            return f"{symbol}{self.amount}"
        if self.currency:
            return f"{self.amount} {self.currency}"
        return self.amount

    def to_json(self) -> dict[str, Any]:
        return {"amount": self.amount, "currency": self.currency, "label": self.label}


@dataclass(frozen=True)
class DishFact:
    name: str
    price: Price | None = None

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "price": self.price.to_json() if self.price else None}


@dataclass(frozen=True)
class MenuFacts:
    """One read of one venue page: its outcome, the dishes it gave, and their provenance.

    Every dish comes from the one page at `source_url`, read at `retrieved_at` and good until
    `expires_at`, so the provenance is the object's, like `OsmFacts`.
    """

    status: MenuStatus
    source_url: str
    retrieved_at: datetime
    expires_at: datetime
    reason: str | None = None  # a short code, such as "robots" or "pdf", for the page's wording
    method: Method | None = None
    dishes: tuple[DishFact, ...] = ()
    truncated: bool = False  # more dishes were listed than `MAX_DISHES`

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "dishes": [d.to_json() for d in self.dishes],
            "truncated": self.truncated,
            "source": {
                "url": self.source_url,
                "host": host_label(self.source_url),
                "method": self.method,
                "retrieved_at": _iso(self.retrieved_at),
                "expires_at": _iso(self.expires_at),
            },
        }


def host_label(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.removeprefix("www.")


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")
