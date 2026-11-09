"""OpenStreetMap tags become allowlisted soft facts, and their opening hours are read at display."""

import pytest

from makan.places.facts import (
    OSM_LICENCE,
    FactSource,
    OsmFacts,
    cuisine_value_label,
    facts_from_tags,
    parse_cuisines,
)
from makan.places.hours import DAY_MINUTES, parse_hours

SOURCE = FactSource("openstreetmap", "2026-11-08T10:00:00Z", OSM_LICENCE, "2026-11-08")
CLOSED = ()


def facts(**tags: str) -> OsmFacts:
    return facts_from_tags("node/1", tags, SOURCE)


def test_every_listed_tag_becomes_a_fact() -> None:
    found = facts(
        cuisine="pizza;italian",
        opening_hours="Mo-Su 11:00-23:00",
        **{"diet:vegan": "yes", "diet:vegetarian": "only", "website:menu": "venue.example/menu"},
        takeaway="yes",
        delivery="no",
        outdoor_seating="yes",
        wheelchair="limited",
        check_date="2025-06-01",
    )
    assert found.cuisines == ("pizza", "italian")
    assert found.diets == (("vegetarian", "only"), ("vegan", "yes"))  # strongest first
    assert (found.takeaway, found.delivery, found.outdoor_seating) == ("yes", "no", "yes")
    assert found.wheelchair == "limited"
    assert found.opening_hours == "Mo-Su 11:00-23:00"
    assert found.menu_url == "https://venue.example/menu"
    assert found.check_date == "2025-06-01"
    assert found.has_facts


@pytest.mark.parametrize(
    "tags",
    [
        {"takeaway": "maybe"},
        {"delivery": "1"},
        {"outdoor_seating": "only"},
        {"wheelchair": "designated"},
        {"diet:vegan": "sure"},
        {"diet:raw": "yes"},  # not an allowlisted diet
        {"website:menu": "javascript:alert(1)"},
        {"website:menu": "https://user:pw@venue.example/"},
        {"check_date": "2025-13-01"},
        {"check_date": "June 2025"},
        {"opening_hours": "x" * 256},
        {"opening_hours": "Mo 10:00-12:00\x00"},
        {"cuisine": "<script>"},
        {"name": "Venue", "amenity": "cafe"},  # names and the amenity are not facts
    ],
)
def test_values_outside_the_allowlists_are_dropped(tags: dict[str, str]) -> None:
    assert not facts(**tags).has_facts


def test_cuisines_are_cleaned_deduplicated_and_bounded() -> None:
    assert parse_cuisines(" Pizza ; ice cream;pizza;;bad value!") == ("pizza", "ice_cream")
    assert len(parse_cuisines(";".join(f"c{i}" for i in range(20)))) == 8
    assert parse_cuisines(None) == ()


def test_cuisine_labels_use_the_vocabulary_only_for_display() -> None:
    assert cuisine_value_label("burger") == "Burgers"
    assert cuisine_value_label("thai") == "Thai"
    assert cuisine_value_label("bubble_tea_shop") == "Bubble tea shop"


def test_json_round_trips_and_checks_everything_again() -> None:
    found = facts(cuisine="thai", takeaway="only", **{"diet:halal": "yes"})
    assert OsmFacts.from_json(found.to_json()) == found
    tampered = {**found.to_json(), "takeaway": "always", "menu_url": "javascript:x"}
    assert OsmFacts.from_json(tampered) == facts(cuisine="thai", **{"diet:halal": "yes"})


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "node/1",
        {"ref": "node/1", "takeaway": "yes"},  # no source
        {"ref": "file:///etc", "takeaway": "yes", "source": SOURCE.to_json()},
        {"ref": "node/1", "takeaway": "yes", "source": {**SOURCE.to_json(), "licence": ""}},
        {"ref": "node/1", "source": SOURCE.to_json()},  # no fact at all
    ],
)
def test_malformed_json_is_no_facts(raw: object) -> None:
    assert OsmFacts.from_json(raw) is None


# Opening hours


def test_a_plain_week() -> None:
    hours = parse_hours("Mo-Fr 11:00-22:00; Sa,Su 12:00-23:30")
    assert hours is not None
    assert hours.days[:5] == (((660, 1320),),) * 5
    assert hours.days[5:] == (((720, 1410),),) * 2
    assert not hours.always_open and not hours.holidays_differ


def test_twenty_four_seven() -> None:
    hours = parse_hours("24/7")
    assert hours is not None and hours.always_open
    assert hours.days == (((0, DAY_MINUTES),),) * 7


def test_unmentioned_days_are_closed_and_later_rules_replace_earlier_ones() -> None:
    hours = parse_hours("Mo-Su 11:00-14:00,17:00-22:00; We off")
    assert hours is not None
    assert hours.days[2] == CLOSED
    assert hours.days[0] == ((660, 840), (1020, 1320))
    only_weekdays = parse_hours("Mo-Fr 09:00-17:00")
    assert only_weekdays is not None and only_weekdays.days[5:] == (CLOSED, CLOSED)


def test_a_comma_before_a_day_adds_a_rule() -> None:
    hours = parse_hours("Mo-Fr 10:00-20:00, Sa 10:00-14:00")
    assert hours is not None
    assert hours.days[4] == ((600, 1200),) and hours.days[5] == ((600, 840),)
    assert hours.days[6] == CLOSED


def test_an_additional_off_rule_closes_the_days_it_names() -> None:
    hours = parse_hours("Mo-Fr 10:00-20:00, We off")
    assert hours is not None
    assert hours.days[2] == CLOSED
    assert hours.days[1] == ((600, 1200),) and hours.days[3] == ((600, 1200),)


def test_ranges_past_midnight_and_wrapping_days() -> None:
    hours = parse_hours("Fr-Mo 18:00-02:00")
    assert hours is not None
    assert [bool(day) for day in hours.days] == [True, False, False, False, True, True, True]
    assert hours.days[4] == ((1080, 1560),)


def test_holiday_rules_are_noted_not_applied() -> None:
    hours = parse_hours("Mo-Su 10:00-22:00; PH off")
    assert hours is not None and hours.holidays_differ
    assert all(day == ((600, 1320),) for day in hours.days)


def test_case_and_spacing_do_not_matter() -> None:
    assert parse_hours("mo-fr 09:00 - 17:00") == parse_hours("Mo-Fr 09:00-17:00")


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "sunrise-sunset",
        "Mo-Fr 09:00+",
        "Jan-Mar Mo 10:00-12:00",
        'Mo-Fr 08:00-12:00 "by appointment"',
        'Mo-Fr 10:00-20:00 || "call"',
        "PH off",  # says nothing about any weekday
        "Mo 24:00-26:00",
        "Mo 10:61-12:00",
        "Mo-Fr",
        "open",
        "Mo[1] 10:00-12:00",
    ],
)
def test_anything_outside_the_readable_subset_is_unreadable(text: str | None) -> None:
    assert parse_hours(text) is None


def test_hours_json_is_minutes_per_day() -> None:
    hours = parse_hours("Mo 10:00-12:00")
    assert hours is not None
    assert hours.to_json() == {
        "status": "listed",
        "days": [[[600, 720]], [], [], [], [], [], []],
        "always_open": False,
        "holidays_differ": False,
    }
