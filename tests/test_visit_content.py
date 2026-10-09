"""What a person types into a visit is checked and cleaned the same way everywhere."""

from datetime import date
from decimal import Decimal

import pytest

from makan.visits import InvalidVisit, parse_place, parse_rating
from makan.visits.content import (
    check_visit_date,
    clean_description,
    clean_dish_name,
    clean_dish_tags,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, "0.0"),
        (10, "10.0"),
        (5, "5.0"),
        (5.7, "5.7"),
        ("5.7", "5.7"),
        (" 5.7 ", "5.7"),
        (Decimal("5.70"), "5.7"),
        (5.75, "5.8"),  # half up, not to even and not by float error
        (5.85, "5.9"),
        (5.74, "5.7"),
        ("5.05", "5.1"),
        ("9.96", "10.0"),
        ("0.04", "0.0"),
        ("0.05", "0.1"),
        (1e-3, "0.0"),
        ("-0", "0.0"),
        ("-0.0", "0.0"),
        ("1e1", "10.0"),
    ],
)
def test_a_rating_is_an_exact_decimal_with_one_place(raw: object, expected: str) -> None:
    rating = parse_rating(raw)
    assert isinstance(rating, Decimal)
    assert str(rating) == expected


@pytest.mark.parametrize(
    "raw",
    [
        10.01,
        "10.04",
        -0.01,
        "-0.04",
        11,
        -1,
        "1e2",
        "abc",
        "",
        " ",
        "5,7",
        "NaN",
        "Infinity",
        float("nan"),
        float("inf"),
        Decimal("NaN"),
        True,
        False,
        None,
        [5],
        {"a": 1},
        "9" * 40,
    ],
)
def test_a_rating_out_of_range_or_not_a_number_is_refused(raw: object) -> None:
    with pytest.raises(InvalidVisit, match="rating"):
        parse_rating(raw)


def test_the_range_is_checked_before_rounding() -> None:
    assert parse_rating("10") == Decimal("10.0")
    with pytest.raises(InvalidVisit):
        parse_rating("10.04")  # it would round to 10.0, but it is over 10


def test_the_label_names_the_field_in_the_message() -> None:
    with pytest.raises(InvalidVisit, match="rating for Tom Yum"):
        parse_rating(11, "rating for Tom Yum")


def test_text_loses_controls_and_keeps_its_wording() -> None:
    assert clean_dish_name("  Pad\tThai​‮  ") == "Pad Thai"
    assert clean_dish_name("ÉCLAIR  Au  Chocolat") == "ÉCLAIR  Au  Chocolat"
    assert clean_dish_name("café") == "café"  # NFC: the same letters, one spelling
    with pytest.raises(InvalidVisit):
        clean_dish_name("​ \t")
    with pytest.raises(InvalidVisit):
        clean_dish_name("x" * 121)
    assert clean_dish_name("x" * 120) == "x" * 120


def test_tags_stay_in_order_with_repeats() -> None:
    assert clean_dish_tags(["spicy", "not spicy", "lunch", "spicy", "Spicy"]) == (
        "spicy",
        "not spicy",
        "lunch",
        "spicy",
        "Spicy",
    )
    assert clean_dish_tags([]) == ()
    with pytest.raises(InvalidVisit):
        clean_dish_tags(["ok", ""])
    with pytest.raises(InvalidVisit):
        clean_dish_tags(["x" * 41])
    with pytest.raises(InvalidVisit):
        clean_dish_tags(["t"] * 21)


def test_a_description_keeps_its_lines() -> None:
    assert clean_description("  a\r\nb\n\n\tc  ") == "a\nb\n\n\tc"
    assert clean_description("   ") is None
    assert clean_description(None) is None
    with pytest.raises(InvalidVisit):
        clean_description("x" * 2001)


def test_a_place_is_qualified_by_its_provider_family() -> None:
    place = parse_place(" Overture:2026-09-23.1 ", " abc-123 ", "  Mid  Thai ", " 1 Jalan ")
    assert (place.source, place.id, place.name, place.address) == (
        "overture",
        "abc-123",
        "Mid  Thai",
        "1 Jalan",
    )
    assert parse_place("demo", "demo-0", "Sample").source == "demo"
    assert parse_place("demo", "demo-0", "Sample", "  ").address is None
    for args in [
        ("", "id", "n"),
        (":2026", "id", "n"),
        ("bad source", "id", "n"),
        ("overture:x", "", "n"),
        ("overture:x", "id", "  "),
        ("overture:x", "i" * 201, "n"),
        ("overture:x", "id", "n" * 201),
    ]:
        with pytest.raises(InvalidVisit):
            parse_place(*args)


def test_a_visit_cannot_be_dated_in_the_future() -> None:
    today = date(2026, 10, 8)
    assert check_visit_date(date(2026, 10, 9), today) == date(2026, 10, 9)  # a zone ahead of us
    assert check_visit_date(date(2001, 1, 1), today) == date(2001, 1, 1)
    for bad in (date(2026, 10, 10), date(1999, 12, 31)):
        with pytest.raises(InvalidVisit):
            check_visit_date(bad, today)
