import pytest

from makan.places import COORD_DECIMALS, PlaceQuery, distance_label, distance_m, rank_nearby
from tests.helpers import place

# Three places on a line due east of the origin, about 111 m apart per 0.001 degree at the equator.
NEAR = place("Near Noodles", "ramen_restaurant", 0.0, 0.001, "food_and_drink", "restaurant")
MID = place("Mid Cafe", "cafe", 0.0, 0.002, "food_and_drink", "casual_eatery")
FAR = place("Far Thai", "thai_restaurant", 0.0, 0.01, "food_and_drink", "restaurant")


def query(**overrides: object) -> PlaceQuery:
    return PlaceQuery(**{"lat": 0.0, "lon": 0.0, "radius_m": 5000, **overrides})  # type: ignore[arg-type]


def test_distance_is_great_circle_meters() -> None:
    assert distance_m(0, 0, 0, 0) == 0
    assert distance_m(0, 0, 0, 0.001) == 111
    assert distance_m(3.148, 101.695, 3.158, 101.695) == pytest.approx(1112, abs=2)


@pytest.mark.parametrize(
    ("meters", "label"),
    [
        (0, "0 ft"),
        (30, "100 ft"),
        (160, "520 ft"),  # just under a tenth of a mile
        (161, "0.1 mi"),
        (482, "0.3 mi"),
        (1609, "1.0 mi"),
        (2000, "1.2 mi"),
        (5000, "3.1 mi"),
    ],
)
def test_distance_label_is_miles_or_feet(meters: int, label: str) -> None:
    assert distance_label(meters) == label


def test_results_are_nearest_first_with_distances() -> None:
    found = rank_nearby([FAR, MID, NEAR], query())
    assert [p.name for p in found] == ["Near Noodles", "Mid Cafe", "Far Thai"]
    assert [p.distance_m for p in found] == [111, 222, 1112]


def test_places_outside_the_radius_are_dropped() -> None:
    assert [p.name for p in rank_nearby([NEAR, MID, FAR], query(radius_m=500))] == [
        "Near Noodles",
        "Mid Cafe",
    ]


def test_limit_keeps_the_nearest() -> None:
    assert [p.name for p in rank_nearby([FAR, MID, NEAR], query(limit=2))] == [
        "Near Noodles",
        "Mid Cafe",
    ]


def test_ties_break_on_name_so_results_are_deterministic() -> None:
    a = place("Alpha", "cafe", 0.0, 0.001, "cafe")
    b = place("Bravo", "cafe", 0.0, -0.001, "cafe")
    assert [p.name for p in rank_nearby([b, a], query())] == ["Alpha", "Bravo"]


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("thai", True),
        ("Thai", True),
        ("thai restaurant", True),
        ("restaurant", True),  # a broad label counts
        ("fast food", False),
        ("thaiwan", False),  # whole words only
        ("", False),
        ("   ", False),
    ],
)
def test_matches_whole_words_of_one_category_label(term: str, expected: bool) -> None:
    assert FAR.matches(term) is expected


def test_multi_word_terms_match_within_one_label() -> None:
    fast = place("Burger Barn", "fast_food_restaurant", 0, 0, "food_and_drink", "restaurant")
    assert fast.matches("fast food")
    assert fast.matches("fast_food_restaurant")
    assert not fast.matches("food truck")  # words spread over different labels do not count


def test_cuisine_and_category_filters_both_apply() -> None:
    places = [NEAR, MID, FAR]
    assert [p.name for p in rank_nearby(places, query(cuisine="ramen"))] == ["Near Noodles"]
    assert [p.name for p in rank_nearby(places, query(category="cafe"))] == ["Mid Cafe"]
    assert rank_nearby(places, query(cuisine="ramen", category="cafe")) == []


def test_ranking_does_not_modify_its_input() -> None:
    rank_nearby([NEAR], query())
    assert NEAR.distance_m is None


def test_near_rounds_the_location_to_about_a_hundred_meters() -> None:
    q = PlaceQuery.near(3.14781234, 101.69549876, 1000)
    assert (q.lat, q.lon) == (3.148, 101.695)
    assert COORD_DECIMALS == 3


@pytest.mark.parametrize(
    "overrides",
    [
        {"lat": 91.0},
        {"lon": -181.0},
        {"radius_m": 99},
        {"radius_m": 5001},
        {"limit": 0},
        {"limit": 21},
    ],
)
def test_out_of_range_queries_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        query(**overrides)
