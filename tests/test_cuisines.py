"""The controlled cuisine vocabulary, checked against real Overture categories, with no network."""

import json
from pathlib import Path
from typing import Any

import pytest

from makan.cuisines import (
    BY_ID,
    CUISINE_IDS,
    CUISINES,
    DIET_LABELS,
    DIETS,
    GENERIC_CATEGORIES,
    GROUPS,
    VOCABULARY_VERSION,
    place_cuisines,
    place_diets,
    public_vocabulary,
    resolve,
)
from makan.fit import CLEF_CUISINES

FIXTURE = Path(__file__).parent / "fixtures" / "overture_food_categories.json"


def observed() -> list[dict[str, Any]]:
    """Every food category seen in five US city centers in one Overture release."""
    rows: list[dict[str, Any]] = json.loads(FIXTURE.read_text("utf-8"))["categories"]
    return rows


def hierarchy_of(category: str) -> list[str]:
    return next(row["hierarchy"] for row in observed() if row["category"] == category)


def test_every_observed_restaurant_category_is_a_conscious_decision() -> None:
    """A restaurant category maps to a cuisine or a diet, or is listed as naming no cuisine."""
    mapped = {category for cuisine in CUISINES for category in cuisine.categories}
    restaurants = [row["category"] for row in observed() if "restaurant" in row["hierarchy"]]
    undecided = sorted(
        c for c in restaurants if c not in mapped and c not in DIETS and c not in GENERIC_CATEGORIES
    )
    assert undecided == []


def test_every_mapped_category_is_one_the_source_really_uses() -> None:
    seen = {label for row in observed() for label in (row["category"], *row["hierarchy"])}
    mapped = {category for cuisine in CUISINES for category in cuisine.categories}
    assert mapped - seen == set()
    assert set(DIETS) <= seen
    assert seen >= GENERIC_CATEGORIES


def test_the_vocabulary_widens_matchable_places_well_past_the_old_eight() -> None:
    restaurants = [row for row in observed() if "restaurant" in row["hierarchy"]]
    total = sum(row["count"] for row in restaurants)
    old = {f"{c}_restaurant" for c in CLEF_CUISINES}
    before = sum(row["count"] for row in restaurants if row["category"] in old)
    after = sum(
        row["count"] for row in restaurants if place_cuisines(row["category"], row["hierarchy"])
    )
    assert before / total < 0.3 < 0.7 < after / total


def test_ids_labels_and_groups_are_well_formed() -> None:
    assert len(CUISINE_IDS) == len(CUISINES) > 100
    assert set(CLEF_CUISINES) <= CUISINE_IDS
    for cuisine in CUISINES:
        assert cuisine.id.isascii() and cuisine.id == cuisine.id.lower()
        assert len(cuisine.id) <= 40 and " " not in cuisine.id
        assert cuisine.label and cuisine.group in GROUPS
    assert all(any(c.group == group for c in CUISINES) for group in GROUPS)
    assert len({category for c in CUISINES for category in c.categories}) == sum(
        len(c.categories) for c in CUISINES
    ), "a source category names exactly one term"


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("thai_restaurant", ("thai",)),
        # The source files pizza under Italian and sushi under Japanese, so both count.
        ("pizza_restaurant", ("pizza", "italian")),
        ("sushi_restaurant", ("sushi", "japanese")),
        ("dim_sum_restaurant", ("dim_sum", "chinese")),
        ("poke_restaurant", ("poke", "hawaiian")),
        ("fish_and_chips_restaurant", ("fish_and_chips", "seafood")),
        ("donut_shop", ("donuts", "desserts")),
        ("brasserie", ("french",)),
        # Ramen is not filed under Japanese by the source, so nothing is inferred.
        ("ramen_restaurant", ("ramen",)),
        # Broad regions are never inherited from a parent category.
        ("greek_restaurant", ("greek",)),
        ("cuban_restaurant", ("cuban",)),
        ("lebanese_restaurant", ("lebanese",)),
        # A region counts only when the source lists the place under that region alone.
        ("mediterranean_restaurant", ("mediterranean",)),
        ("asian_restaurant", ("asian",)),
    ],
)
def test_a_place_carries_its_own_cuisine_and_specific_parents(
    category: str, expected: tuple[str, ...]
) -> None:
    assert place_cuisines(category, hierarchy_of(category)) == expected


@pytest.mark.parametrize(
    "category",
    [
        "restaurant",
        "food_and_drink",
        "bar_and_grill_restaurant",
        "buffet_restaurant",
        "east_asian_restaurant",
        "south_american_restaurant",
        "cafe",
        "coffee_shop",
        "cocktail_bar",
        "food_truck_stand",
        "vegan_restaurant",
        "a_category_the_source_adds_later",
    ],
)
def test_generic_unknown_and_diet_categories_name_no_cuisine(category: str) -> None:
    labels: list[str] = next(
        (row["hierarchy"] for row in observed() if row["category"] == category), []
    )
    assert place_cuisines(category, labels) == ()


def test_alternate_categories_count_but_not_as_regions() -> None:
    labels = ["food_and_drink", "restaurant", "thai_restaurant", "asian_restaurant"]
    assert place_cuisines("restaurant", labels) == ("thai",)


def test_diet_categories_are_soft_labels_in_a_stable_order() -> None:
    assert place_diets("vegan_restaurant", hierarchy_of("vegan_restaurant")) == ("vegan",)
    assert place_diets("restaurant", ["halal_restaurant", "kosher_restaurant"]) == (
        "halal",
        "kosher",
    )
    assert place_diets("thai_restaurant", hierarchy_of("thai_restaurant")) == ()
    assert place_diets("health_food_restaurant") == ()
    assert set(DIETS.values()) == set(DIET_LABELS)
    assert not set(DIET_LABELS) & CUISINE_IDS, "diets are never cuisine tastes"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("thai", "thai"),
        ("Thai", "thai"),
        ("  Thai food ", "thai"),
        ("Japanese cuisine", "japanese"),
        ("ramen", "ramen"),
        ("burger", "burgers"),
        ("Burgers", "burgers"),
        ("bbq", "barbecue"),
        ("tex-mex", "tex_mex"),
        ("Tex Mex", "tex_mex"),
        ("szechuan", "sichuan"),
        ("Döner", "kebab"),
        ("dim sum", "dim_sum"),
        ("chicken wings", "wings"),
        ("hot dog", "hot_dogs"),
        ("puerto_rican", "puerto_rican"),
        ("Middle Eastern", "middle_eastern"),
        ("noodles", None),
        ("spicy", None),
        ("vegan", None),
        ("private arbitrary prose", None),
        ("", None),
        ("!!!", None),
    ],
)
def test_saved_free_text_resolves_only_to_a_term_it_names(text: str, expected: str | None) -> None:
    assert resolve(text) == expected


def test_every_id_label_and_alias_resolves_to_its_own_term() -> None:
    for cuisine in CUISINES:
        for name in (cuisine.id, cuisine.label, *cuisine.aliases):
            assert resolve(name) == cuisine.id, name


def test_the_public_vocabulary_is_bounded_and_versioned() -> None:
    public = public_vocabulary()
    assert public["version"] == VOCABULARY_VERSION
    assert public["groups"] == list(GROUPS)
    assert [c["id"] for c in public["cuisines"]] == [c.id for c in CUISINES]
    assert all(set(c) == {"id", "label", "group", "aliases"} for c in public["cuisines"])
    assert len(json.dumps(public)) < 16_000
    assert BY_ID["thai"].label == "Thai"
