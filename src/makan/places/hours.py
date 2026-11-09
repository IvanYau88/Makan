"""Reads OpenStreetMap's `opening_hours` tag, for display only.

The tag has a large grammar (https://wiki.openstreetmap.org/wiki/Key:opening_hours). This reads
the plain weekly subset most venues use: `24/7`, day ranges and lists, several time ranges per
day, `off`, ranges past midnight, and holiday rules, which are noted but not applied.
Anything else (months, weeks, sunrise, comments, open ends, fallback rules) is unreadable, and
the caller shows the raw text as unreadable, never a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

DAYS = ("mo", "tu", "we", "th", "fr", "sa", "su")
DAY_MINUTES = 24 * 60
HOLIDAYS = ("ph", "sh")  # public and school holidays: their rules are not applied here

_DAY = r"(?:mo|tu|we|th|fr|sa|su|ph|sh)"
_DAY_ITEM = rf"{_DAY}(?:\s*-\s*{_DAY})?"
_DAYS = rf"{_DAY_ITEM}(?:\s*,\s*{_DAY_ITEM})*"
_TIME_RANGE = r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}"
_TIMES = rf"(?:off|closed|24/7|{_TIME_RANGE}(?:\s*,\s*{_TIME_RANGE})*)"
_RULE = re.compile(rf"(?:(?P<days>{_DAYS})\s+)?(?P<times>{_TIMES})")
# A comma after a time or `off` that starts a new day selector begins an additional rule, which
# adds to those days instead of replacing them: `Mo-Fr 10:00-20:00, Sa 10:00-14:00`. An additional
# `off` closes the days it names: `Mo-Fr 10:00-20:00, We off`.
_ADDITIONAL = re.compile(rf"(?<=\d|f|d)\s*,\s*(?={_DAY}\b)")

Ranges = tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class WeeklyHours:
    """Opening times per weekday, Monday first, in minutes from midnight.

    A range that ends after midnight ends past 1440, so `Fr 18:00-02:00` is (1080, 1560) on
    Friday. An empty day is closed.
    """

    days: tuple[Ranges, ...]
    always_open: bool = False
    holidays_differ: bool = False  # the tag has holiday rules, which are not shown

    def to_json(self) -> dict[str, Any]:
        return {
            "status": "listed",
            "days": [[list(r) for r in day] for day in self.days],
            "always_open": self.always_open,
            "holidays_differ": self.holidays_differ,
        }


def parse_hours(text: str | None) -> WeeklyHours | None:
    """The weekly hours `text` states, or None when it is missing or not in the readable subset."""
    if not text:
        return None
    source = " ".join(text.split()).lower()
    if source == "24/7":
        return WeeklyHours(tuple(((0, DAY_MINUTES),) for _ in DAYS), always_open=True)
    if "||" in source:
        return None
    week: list[list[tuple[int, int]]] = [[] for _ in DAYS]
    known = False
    holidays = False
    for rule in filter(None, (r.strip() for r in source.split(";"))):
        for position, part in enumerate(_ADDITIONAL.split(rule)):
            parsed = _rule(part.strip())
            if parsed is None:
                return None
            days, ranges, mentions_holiday = parsed
            holidays = holidays or mentions_holiday
            for day in days:
                if position == 0 or not ranges:
                    week[day] = list(ranges)  # a later rule replaces what an earlier one said
                else:
                    week[day].extend(ranges)
                known = True
    if not known:
        return None
    return WeeklyHours(tuple(_merge(day) for day in week), holidays_differ=holidays)


def _rule(text: str) -> tuple[list[int], Ranges, bool] | None:
    found = _RULE.fullmatch(text)
    if found is None:
        return None
    days, holiday = _days(found.group("days"))
    times = found.group("times")
    if times in ("off", "closed"):
        ranges: Ranges = ()
    elif times == "24/7":
        ranges = ((0, DAY_MINUTES),)
    else:
        parsed = [_range(item) for item in times.split(",")]
        if any(r is None for r in parsed):
            return None
        ranges = tuple(r for r in parsed if r is not None)
    return days, ranges, holiday


def _days(selector: str | None) -> tuple[list[int], bool]:
    """Weekday indexes a selector names, and whether it names a holiday. None means every day."""
    if selector is None:
        return list(range(len(DAYS))), False
    days: list[int] = []
    holiday = False
    for item in selector.split(","):
        ends = [end.strip() for end in item.split("-")]
        if any(end in HOLIDAYS for end in ends):
            holiday = True
            continue
        first = DAYS.index(ends[0])
        last = DAYS.index(ends[-1])
        span = (last - first) % len(DAYS)  # Fr-Mo wraps over the weekend
        days.extend((first + step) % len(DAYS) for step in range(span + 1))
    return list(dict.fromkeys(days)), holiday


def _range(text: str) -> tuple[int, int] | None:
    start_text, end_text = (part.strip() for part in text.split("-"))
    start, end = _minutes(start_text), _minutes(end_text)
    if start is None or end is None or start >= DAY_MINUTES or end > 2 * DAY_MINUTES:
        return None
    if end <= start:
        end += DAY_MINUTES  # past midnight
        if end > start + DAY_MINUTES:
            return None
    return start, end


def _minutes(text: str) -> int | None:
    hours, minutes = (int(part) for part in text.split(":"))
    if minutes > 59:
        return None
    return hours * 60 + minutes


def _merge(ranges: list[tuple[int, int]]) -> Ranges:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)
