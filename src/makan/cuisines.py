"""The controlled cuisine vocabulary: what a place's source category says it serves, and what a
diner may say they like or would rather skip.

Both sides speak this one vocabulary, so a stated taste can only ever meet a cuisine that the
place data itself names. Each term lists the Overture categories that name it. Overture files
places in a hierarchy, broad to specific, and a place carries:

- the term its own primary category names, and
- every term its parent categories name, except broad regions (pizza is Italian, sushi is
  Japanese, but Greek is not counted as Mediterranean nor Italian as European).

A broad region such as Mediterranean or Middle Eastern matches only a place the source lists
under that region alone, because the source's grouping of countries into regions is too loose
to stand for a diner's taste. Anything else, such as `restaurant`, `bar_and_grill`, cafes, bars,
and taxonomy groupings like `east_asian_restaurant`, names no cuisine and stays unmatched.
Nothing is inferred from a venue's name, menu, or a related cuisine.

Diet categories (vegan, vegetarian, halal, kosher, gluten free) are a separate, soft fact:
the source's own label, never verified safety, and never a reason to include or exclude a place.
Hard requirements stay in `makan.memory.hard`.

The vocabulary is versioned: a change to its terms or their categories is a new
`VOCABULARY_VERSION`. The frozen Clef state versions 1 and 2 keep their own eight cuisines, see
`makan.fit.CLEF_CUISINES`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

VOCABULARY_VERSION = "cuisines-v1"


@dataclass(frozen=True)
class Cuisine:
    id: str  # a stable slug, which is what the API and stored tastes carry
    label: str  # how people see it
    group: str  # where the taste picker files it; display only, no matching meaning
    categories: tuple[str, ...]  # the source categories that name it
    aliases: tuple[str, ...] = ()  # other words people type for it, for search and saved text
    region: bool = False  # a broad region: never inherited from a parent category

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "group": self.group, "aliases": self.aliases}


EAST_ASIAN = "East Asian"
SOUTHEAST_ASIAN = "Southeast Asian"
SOUTH_ASIAN = "South and Central Asian"
PAN_ASIAN = "Pan-Asian and fusion"
MIDDLE_EASTERN = "Middle Eastern"
EUROPEAN = "European"
LATIN = "Latin American and Caribbean"
AFRICAN = "African"
NORTH_AMERICAN = "North American and Pacific"
DISHES = "Dishes and more"
SWEETS = "Bakeries and sweets"

# The order the picker shows groups in.
GROUPS = (
    EAST_ASIAN,
    SOUTHEAST_ASIAN,
    SOUTH_ASIAN,
    PAN_ASIAN,
    MIDDLE_EASTERN,
    EUROPEAN,
    LATIN,
    AFRICAN,
    NORTH_AMERICAN,
    DISHES,
    SWEETS,
)


def _c(
    id: str,
    label: str,
    group: str,
    *categories: str,
    aliases: tuple[str, ...] = (),
    region: bool = False,
) -> Cuisine:
    return Cuisine(id, label, group, categories or (f"{id}_restaurant",), aliases, region)


CUISINES: tuple[Cuisine, ...] = (
    # East Asian
    _c("chinese", "Chinese", EAST_ASIAN),
    _c("cantonese", "Cantonese", EAST_ASIAN),
    _c("dim_sum", "Dim sum", EAST_ASIAN, aliases=("dimsum",)),
    _c("fujian", "Fujian", EAST_ASIAN, aliases=("fujianese", "hokkien")),
    _c("hunan", "Hunan", EAST_ASIAN, aliases=("hunanese",)),
    _c("indo_chinese", "Indo-Chinese", EAST_ASIAN, aliases=("indian chinese",)),
    _c("shanghainese", "Shanghainese", EAST_ASIAN, aliases=("shanghai",)),
    _c("sichuan", "Sichuan", EAST_ASIAN, aliases=("szechuan", "szechwan")),
    _c("taiwanese", "Taiwanese", EAST_ASIAN),
    _c("hong_kong", "Hong Kong-style", EAST_ASIAN, "hong_kong_style_cafe"),
    _c("japanese", "Japanese", EAST_ASIAN),
    _c("sushi", "Sushi", EAST_ASIAN),
    _c("ramen", "Ramen", EAST_ASIAN),
    _c("korean", "Korean", EAST_ASIAN),
    _c("mongolian", "Mongolian", EAST_ASIAN),
    # Southeast Asian
    _c("thai", "Thai", SOUTHEAST_ASIAN),
    _c("vietnamese", "Vietnamese", SOUTHEAST_ASIAN),
    _c("filipino", "Filipino", SOUTHEAST_ASIAN, aliases=("philippine",)),
    _c("malaysian", "Malaysian", SOUTHEAST_ASIAN, aliases=("malay",)),
    _c("indonesian", "Indonesian", SOUTHEAST_ASIAN),
    _c("singaporean", "Singaporean", SOUTHEAST_ASIAN),
    _c("burmese", "Burmese", SOUTHEAST_ASIAN, aliases=("myanmar",)),
    _c("cambodian", "Cambodian", SOUTHEAST_ASIAN, aliases=("khmer",)),
    _c("laotian", "Laotian", SOUTHEAST_ASIAN, aliases=("lao",)),
    # South and Central Asian
    _c("indian", "Indian", SOUTH_ASIAN),
    _c("south_indian", "South Indian", SOUTH_ASIAN),
    _c("pakistani", "Pakistani", SOUTH_ASIAN),
    _c("bangladeshi", "Bangladeshi", SOUTH_ASIAN),
    _c("himalayan", "Himalayan", SOUTH_ASIAN),
    _c("nepalese", "Nepalese", SOUTH_ASIAN, aliases=("nepali",)),
    _c("tibetan", "Tibetan", SOUTH_ASIAN),
    _c("sri_lankan", "Sri Lankan", SOUTH_ASIAN),
    _c("afghan", "Afghan", SOUTH_ASIAN, "afghani_restaurant", aliases=("afghani",)),
    _c("uzbek", "Uzbek", SOUTH_ASIAN),
    # Pan-Asian and fusion
    _c("asian", "Asian", PAN_ASIAN, region=True),
    _c("asian_fusion", "Asian fusion", PAN_ASIAN),
    _c("pan_asian", "Pan-Asian", PAN_ASIAN),
    _c("fusion", "International fusion", PAN_ASIAN, "international_fusion_restaurant"),
    # Middle Eastern
    _c("middle_eastern", "Middle Eastern", MIDDLE_EASTERN, region=True),
    _c("arab", "Arab", MIDDLE_EASTERN, "arabian_restaurant", aliases=("arabian", "arabic")),
    _c("armenian", "Armenian", MIDDLE_EASTERN),
    _c("falafel", "Falafel", MIDDLE_EASTERN),
    _c("georgian", "Georgian", MIDDLE_EASTERN),
    _c("israeli", "Israeli", MIDDLE_EASTERN),
    _c("kurdish", "Kurdish", MIDDLE_EASTERN),
    _c("lebanese", "Lebanese", MIDDLE_EASTERN),
    _c("persian", "Persian", MIDDLE_EASTERN, aliases=("iranian",)),
    _c("syrian", "Syrian", MIDDLE_EASTERN),
    _c("turkish", "Turkish", MIDDLE_EASTERN),
    _c(
        "kebab", "Kebab", MIDDLE_EASTERN, "doner_kebab_restaurant", aliases=("doner", "doner kebab")
    ),
    # European
    _c("european", "European", EUROPEAN, region=True),
    _c("mediterranean", "Mediterranean", EUROPEAN, region=True),
    _c("italian", "Italian", EUROPEAN),
    _c("pizza", "Pizza", EUROPEAN, aliases=("pizzeria",)),
    _c("french", "French", EUROPEAN, "french_restaurant", "brasserie", aliases=("brasserie",)),
    _c("spanish", "Spanish", EUROPEAN),
    _c("basque", "Basque", EUROPEAN),
    _c("tapas", "Tapas", EUROPEAN, "tapas_bar"),
    _c("portuguese", "Portuguese", EUROPEAN),
    _c("greek", "Greek", EUROPEAN),
    _c("german", "German", EUROPEAN),
    _c("austrian", "Austrian", EUROPEAN),
    _c("swiss", "Swiss", EUROPEAN),
    _c("polish", "Polish", EUROPEAN),
    _c("czech", "Czech", EUROPEAN),
    _c("hungarian", "Hungarian", EUROPEAN),
    _c("eastern_european", "Eastern European", EUROPEAN, region=True),
    _c("russian", "Russian", EUROPEAN),
    _c("ukrainian", "Ukrainian", EUROPEAN),
    _c("romanian", "Romanian", EUROPEAN),
    _c(
        "serbo_croatian",
        "Serbian and Croatian",
        EUROPEAN,
        aliases=("serbian", "croatian", "bosnian", "balkan"),
    ),
    _c("british", "British", EUROPEAN, aliases=("english",)),
    _c("irish", "Irish", EUROPEAN),
    _c("belgian", "Belgian", EUROPEAN),
    _c("scandinavian", "Scandinavian", EUROPEAN, region=True, aliases=("nordic",)),
    _c("swedish", "Swedish", EUROPEAN),
    # Latin American and Caribbean
    _c("latin_american", "Latin American", LATIN, region=True, aliases=("latin", "latino")),
    _c("mexican", "Mexican", LATIN),
    _c("tacos", "Tacos", LATIN, "taco_restaurant", aliases=("taqueria",)),
    _c("tex_mex", "Tex-Mex", LATIN, "texmex_restaurant", aliases=("texmex",)),
    _c("empanadas", "Empanadas", LATIN, "empanada_restaurant"),
    _c("caribbean", "Caribbean", LATIN, region=True),
    _c("cuban", "Cuban", LATIN),
    _c("dominican", "Dominican", LATIN),
    _c("haitian", "Haitian", LATIN),
    _c("jamaican", "Jamaican", LATIN),
    _c("puerto_rican", "Puerto Rican", LATIN),
    _c("trinidadian", "Trinidadian", LATIN, aliases=("trinidad",)),
    _c("salvadoran", "Salvadoran", LATIN, aliases=("salvadorian",)),
    _c("guatemalan", "Guatemalan", LATIN),
    _c("honduran", "Honduran", LATIN),
    _c("nicaraguan", "Nicaraguan", LATIN),
    _c("costa_rican", "Costa Rican", LATIN),
    _c("panamanian", "Panamanian", LATIN),
    _c("belizean", "Belizean", LATIN),
    _c("argentine", "Argentine", LATIN, aliases=("argentinian", "argentinean")),
    _c("brazilian", "Brazilian", LATIN),
    _c("chilean", "Chilean", LATIN),
    _c("colombian", "Colombian", LATIN),
    _c("ecuadorian", "Ecuadorian", LATIN),
    _c("paraguayan", "Paraguayan", LATIN),
    _c("peruvian", "Peruvian", LATIN),
    _c("uruguayan", "Uruguayan", LATIN),
    _c("venezuelan", "Venezuelan", LATIN),
    # African
    _c("african", "African", AFRICAN, region=True),
    _c("egyptian", "Egyptian", AFRICAN),
    _c("eritrean", "Eritrean", AFRICAN),
    _c("ethiopian", "Ethiopian", AFRICAN),
    _c("moroccan", "Moroccan", AFRICAN),
    _c("nigerian", "Nigerian", AFRICAN),
    _c("senegalese", "Senegalese", AFRICAN),
    _c("south_african", "South African", AFRICAN),
    # North American and Pacific
    _c("american", "American", NORTH_AMERICAN),
    _c("southern", "Southern", NORTH_AMERICAN, "southern_american_restaurant"),
    _c(
        "cajun_creole",
        "Cajun and Creole",
        NORTH_AMERICAN,
        "cajun_and_creole_restaurant",
        aliases=("cajun", "creole"),
    ),
    _c("soul_food", "Soul food", NORTH_AMERICAN, "soul_food", aliases=("soul",)),
    _c("canadian", "Canadian", NORTH_AMERICAN),
    _c("poutine", "Poutine", NORTH_AMERICAN, "poutinerie_restaurant"),
    _c("hawaiian", "Hawaiian", NORTH_AMERICAN),
    _c("poke", "Poke", NORTH_AMERICAN),
    _c("australian", "Australian", NORTH_AMERICAN, aliases=("aussie",)),
    # Dishes and more
    _c("barbecue", "Barbecue", DISHES, aliases=("bbq", "barbeque")),
    _c("burgers", "Burgers", DISHES, "burger_restaurant", aliases=("hamburger",)),
    _c("steakhouse", "Steakhouse", DISHES, "steakhouse", aliases=("steak",)),
    _c("chicken", "Chicken", DISHES, aliases=("fried chicken",)),
    _c("wings", "Chicken wings", DISHES, "chicken_wings_restaurant", aliases=("wings",)),
    _c("hot_dogs", "Hot dogs", DISHES, "hot_dog_restaurant", aliases=("hotdog",)),
    _c("cheesesteaks", "Cheesesteaks", DISHES, "cheesesteak_restaurant"),
    _c("seafood", "Seafood", DISHES, "seafood_restaurant", "fish_restaurant", aliases=("fish",)),
    _c("fish_and_chips", "Fish and chips", DISHES),
    _c("dumplings", "Dumplings", DISHES, "dumpling_restaurant"),
    _c("soup", "Soup", DISHES),
    _c("salad", "Salad", DISHES, "salad_bar"),
    _c("sandwiches", "Sandwiches", DISHES, "sandwich_shop", aliases=("subs",)),
    _c("bagels", "Bagels", DISHES, "bagel_shop"),
    _c("deli", "Deli", DISHES, "delicatessen", aliases=("delicatessen",)),
    _c("diner", "Diner", DISHES, "diner"),
    _c(
        "breakfast_brunch",
        "Breakfast and brunch",
        DISHES,
        "breakfast_and_brunch_restaurant",
        aliases=("breakfast", "brunch"),
    ),
    _c("pancakes", "Pancakes", DISHES, "pancake_house"),
    _c("waffles", "Waffles", DISHES, "waffle_restaurant"),
    _c("fast_food", "Fast food", DISHES),
    _c("fondue", "Fondue", DISHES),
    _c("jewish", "Jewish", DISHES),
    # Bakeries and sweets
    _c("bakery", "Bakery", SWEETS, "bakery", aliases=("bakeries", "pastry", "pastries")),
    _c("desserts", "Desserts", SWEETS, "dessert_shop", aliases=("dessert",)),
    _c("ice_cream", "Ice cream", SWEETS, "ice_cream_shop", "gelato_shop", aliases=("gelato",)),
    _c("frozen_yogurt", "Frozen yogurt", SWEETS, "frozen_yogurt_shop", aliases=("froyo",)),
    _c("donuts", "Donuts", SWEETS, "donut_shop", aliases=("doughnut", "doughnuts")),
    _c("cupcakes", "Cupcakes", SWEETS, "cupcake_shop"),
    _c("pie", "Pie", SWEETS, "pie_shop"),
    _c("candy", "Candy", SWEETS, "candy_store", aliases=("sweets",)),
    _c("chocolate", "Chocolate", SWEETS, "chocolatier"),
)

# Soft diet labels from the source's special diet categories. Never verified, never eligibility.
DIETS: dict[str, str] = {
    "vegan_restaurant": "vegan",
    "vegetarian_restaurant": "vegetarian",
    "halal_restaurant": "halal",
    "kosher_restaurant": "kosher",
    "gluten_free_restaurant": "gluten_free",
}
DIET_LABELS: dict[str, str] = {
    "vegan": "Vegan",
    "vegetarian": "Vegetarian",
    "halal": "Halal",
    "kosher": "Kosher",
    "gluten_free": "Gluten-free",
}

# Overture restaurant categories that deliberately name no cuisine: generic venues, meal formats,
# and taxonomy groupings that only hold more specific categories. Drink venues (bars, coffee
# shops, tea rooms) and generic eateries (cafes, food courts, food trucks, gastropubs) outside
# the restaurant branch name no cuisine either. The vocabulary tests use this list so a category
# the source adds later is a conscious decision, not a silent gap.
GENERIC_CATEGORIES = frozenset(
    {
        "restaurant",
        "bar_and_grill_restaurant",
        "buffet_restaurant",
        "cafeteria",
        "comfort_food_restaurant",
        "diy_foods_restaurant",
        "pop_up_restaurant",
        "theme_restaurant",
        "meat_restaurant",
        "special_diet_restaurant",
        "health_food_restaurant",
        "acai_bowls",
        "live_and_raw_food_restaurant",
        "molecular_gastronomy_restaurant",
        "east_asian_restaurant",
        "south_asian_restaurant",
        "southeast_asian_restaurant",
        "central_asian_restaurant",
        "east_african_restaurant",
        "north_african_restaurant",
        "southern_african_restaurant",
        "west_african_restaurant",
        "central_european_restaurant",
        "western_european_restaurant",
        "iberian_restaurant",
        "central_american_restaurant",
        "south_american_restaurant",
        "north_american_restaurant",
        "pacific_rim_restaurant",
        "polynesian_restaurant",
    }
)

BY_ID: dict[str, Cuisine] = {c.id: c for c in CUISINES}
CUISINE_IDS = frozenset(BY_ID)
_BY_CATEGORY: dict[str, Cuisine] = {
    category: cuisine for cuisine in CUISINES for category in cuisine.categories
}


def place_cuisines(primary: str, labels: Iterable[str] = ()) -> tuple[str, ...]:
    """The cuisines a place's own categories name, most specific first.

    `primary` is the place's most specific category and `labels` the rest the source gives
    (its parents and alternates). Broad regions count only as the primary category.
    """
    found: list[str] = []
    term = _BY_CATEGORY.get(primary)
    if term is not None:
        found.append(term.id)
    for label in reversed(tuple(labels)):
        term = _BY_CATEGORY.get(label)
        if term is not None and not term.region and term.id not in found:
            found.append(term.id)
    return tuple(found)


def place_diets(primary: str, labels: Iterable[str] = ()) -> tuple[str, ...]:
    """The soft diet labels a place's own categories carry, in a stable order."""
    named = {DIETS[label] for label in (primary, *labels) if label in DIETS}
    return tuple(diet for diet in DIET_LABELS if diet in named)


def resolve(text: str) -> str | None:
    """The vocabulary id that free text such as "Thai food" or "bbq" names, or None.

    This reads what a person typed into a saved taste. It never guesses a related cuisine:
    "noodles" or "spicy" name no term and stay unsupported.
    """
    key = _key(text)
    if not key:
        return None
    for candidate in (key, *_variants(key)):
        found = _INDEX.get(candidate)
        if found is not None:
            return found
    return None


def cuisine_label(id: str) -> str:
    """How people see a cuisine id; an id from an older vocabulary is shown as plain words."""
    term = BY_ID.get(id)
    return term.label if term else id.replace("_", " ").capitalize()


def diet_label(id: str) -> str:
    return DIET_LABELS.get(id) or id.replace("_", " ").capitalize()


def public_vocabulary() -> dict[str, Any]:
    """What the taste picker needs: the version, the group order, and every term."""
    return {
        "version": VOCABULARY_VERSION,
        "groups": list(GROUPS),
        "cuisines": [c.to_json() for c in CUISINES],
    }


_SUFFIXES = (" restaurants", " restaurant", " cuisine", " food", " foods", " place", " places")


def _key(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch)).replace("'", "")
    return " ".join(re.findall(r"[a-z0-9]+", plain))


def _variants(key: str) -> list[str]:
    stems = [key]
    for suffix in _SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix):
            stems.append(key.removesuffix(suffix))
    out: list[str] = []
    for stem in stems:
        out.append(stem)
        if stem.endswith("es"):
            out.append(stem[:-2])
        if stem.endswith("s"):
            out.append(stem[:-1])
        else:
            out.append(stem + "s")
    return out


def _build_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for cuisine in CUISINES:
        for name in (cuisine.id, cuisine.label, *cuisine.aliases):
            key = _key(name.replace("_", " "))
            if index.setdefault(key, cuisine.id) != cuisine.id:
                raise ValueError(f"cuisine name {name!r} is ambiguous")
    return index


_INDEX = _build_index()
