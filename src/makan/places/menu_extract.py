"""Dish facts from one HTML page: schema.org menus first, then a conservative reading of the page.

The page is a third party's and is not trusted. Only dish names and prices come out, each name
checked against a narrow shape (`clean_dish_name`), so page prose, descriptions, hidden text,
links, markup and text written to steer a model are dropped rather than passed on.

Order of preference:
1. JSON-LD `MenuItem` objects, reached through `@graph`, `hasMenu`, `hasMenuSection`,
   `hasMenuItem` and the list keys schema.org uses for menus.
2. schema.org microdata: elements with an `itemtype` of `MenuItem` and their `name` and `price`.
3. Visible text blocks that read as "Dish name ... $12.95", or a name block right before a block
   that is only a price. A page needs at least `MIN_HTML_DISHES` of these, so one stray price
   such as "$5 off your first order" never reads as a menu.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

from makan.places.menus import MAX_DISH_CHARS, MAX_DISHES, DishFact, Method, Price

MIN_HTML_DISHES = 3
MAX_NODES = 60_000
MAX_DEPTH = 200
MAX_BLOCKS = 20_000
MAX_JSON_NODES = 10_000
MAX_JSON_DEPTH = 24
MAX_NAME_WORDS = 8
MAX_AMOUNT = Decimal(10_000)


def _tags(text: str) -> frozenset[str]:
    return frozenset(text.split())


VOID = _tags("area base br col embed hr img input link meta param source track wbr")
# Never read: code, embedded documents, chrome around the page, and controls.
SKIPPED = _tags(
    "script style noscript template svg math iframe object embed head nav footer form button "
    "select textarea option canvas audio video picture dialog"
)
BLOCK = _tags(
    "address article aside blockquote body br caption dd details div dl dt fieldset figcaption "
    "figure h1 h2 h3 h4 h5 h6 header hr li main ol p pre section summary table tbody td tfoot th "
    "thead tr ul"
)
# A start tag of one of these closes an open one of the same family inside the same container.
_SIBLINGS = {
    "li": {"li"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "p": {"p"},
    "tr": {"tr"},
    "td": {"td", "th"},
    "th": {"td", "th"},
}
_SCOPES = _tags("ul ol dl table tbody thead tfoot div section article main body")
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
_HIDDEN_CLASSES = _tags("hidden hide d-none sr-only visually-hidden screen-reader-text invisible")

_PRICE = r"(?:[$€£]\s?\d{1,4}(?:[.,]\d{2})?|\d{1,4}[.,]\d{2}(?:\s?[€£])?)"
_MORE_PRICES = rf"(?:\s*(?:/|\||,|or)\s*{_PRICE})*"
_NAME_THEN_PRICE = re.compile(
    rf"^(?P<name>.*?\S)[\s.\u2026\u00b7\u2022_|:\u2013\u2014-]*?(?P<price>{_PRICE}){_MORE_PRICES}$"
)
_ONLY_PRICE = re.compile(rf"^(?P<price>{_PRICE}){_MORE_PRICES}$")
_AMOUNT = re.compile(r"^(?P<sym>[$€£])?\s?(?P<num>\d{1,4}(?:[.,]\d{1,2})?)\s?(?P<sym2>[€£])?$")
_CURRENCY = re.compile(r"[A-Z]{3}")
_SYMBOL_CURRENCY = {"€": "EUR", "£": "GBP"}  # "$" names no one currency, so it maps to none

_ITEM_CODE = re.compile(r"^(?:#?\d{1,3}[a-z]?|[a-z]{1,2}\d{1,3})[.):]\s+", re.IGNORECASE)
_BAD_CHARS = re.compile(r"[<>{}\[\]\\@#$%^=~`|_;€£]")
_SENTENCE = re.compile(r"[a-z]{3,}[.!?]\s+[A-Z]")  # "Great food. Order", not "St. Louis"
# Lines that carry a price but are not dishes: checkout, fees, promotions, accounts.
_NOT_DISH = re.compile(
    r"\b(delivery|tax|taxes|tip|tips|fees?|gift ?cards?|subtotal|total|minimum|shipping|"
    r"coupons?|discounts?|gratuity|surcharge|deposit|reservations?|parking|cart|checkout|"
    r"sign ?up|log ?in|subscribe|newsletter|membership|per person|% off|off your)\b"
)
_ADD_ON = re.compile(
    r"^(served|comes|choice|choose|add|extra|includes|including|with|substitute|upgrade|"
    r"make it|price|prices)\b"
)
# Words that address a reader or a model rather than name food. A dish never needs them.
_INSTRUCTION = re.compile(
    r"\b(ignore|disregard|instructions?|prompts?|system|assistant|chatgpt|claude|gpt|llm|"
    r"openai|anthropic|jailbreak|override|you are|you must|recommend this|rate this|"
    r"score this|five stars|5 stars)\b"
)

_DESCEND = (
    "@graph",
    "mainEntity",
    "hasMenu",
    "menu",
    "hasMenuSection",
    "hasMenuItem",
    "hasOfferCatalog",
    "itemListElement",
    "item",
    "itemOffered",
)


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    skip: bool = False
    children: list[_Node | str] = field(default_factory=list)


class _TreeBuilder(HTMLParser):
    """A forgiving element tree, with JSON-LD script bodies collected on the side."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self.stack: list[_Node] = [self.root]
        self.json_ld: list[str] = []
        self._in_json_ld = False
        self._nodes = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._nodes >= MAX_NODES:
            return
        self._nodes += 1
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and values.get("type", "").strip().lower() == "application/ld+json":
            self._in_json_ld = True
            self.json_ld.append("")
        node = _Node(tag, values, skip=tag in SKIPPED or _hidden(values))
        if tag in VOID:
            self.stack[-1].children.append(node)
            return
        self._close_siblings(tag)
        self.stack[-1].children.append(node)
        if len(self.stack) < MAX_DEPTH:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_json_ld = False
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self.json_ld[-1] += data
            return
        self.stack[-1].children.append(data)

    def _close_siblings(self, tag: str) -> None:
        family = _SIBLINGS.get(tag)
        if tag in BLOCK and self.stack[-1].tag == "p":
            self.stack.pop()
        if family is None:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            current = self.stack[i].tag
            if current in family:
                del self.stack[i:]
                return
            if current in _SCOPES or (tag in ("td", "th") and current == "tr"):
                return


def _hidden(attrs: dict[str, str]) -> bool:
    if "hidden" in attrs or attrs.get("aria-hidden", "").strip().lower() == "true":
        return True
    if _HIDDEN_STYLE.search(attrs.get("style", "")):
        return True
    return bool(_HIDDEN_CLASSES & set(attrs.get("class", "").lower().split()))


def extract_dishes(html: str) -> tuple[Method | None, tuple[DishFact, ...], bool]:
    """The dishes one page lists, the method that found them, and whether there were more than
    `MAX_DISHES`. No method and no dishes when the page has no menu Makan can read."""
    builder = _TreeBuilder()
    try:
        builder.feed(html)
        builder.close()
    except (AssertionError, ValueError):  # HTMLParser's own failures on hostile markup
        pass
    found: list[tuple[Method, list[DishFact]]] = [
        ("json_ld", _json_ld_dishes(builder.json_ld)),
        ("microdata", _microdata_dishes(builder.root)),
    ]
    html_dishes = _heuristic_dishes(_blocks(builder.root))
    if len(html_dishes) >= MIN_HTML_DISHES:
        found.append(("html", html_dishes))
    for method, dishes in found:
        unique = _unique(dishes)
        if unique:
            return method, tuple(unique[:MAX_DISHES]), len(unique) > MAX_DISHES
    return None, (), False


def clean_dish_name(text: object) -> str | None:
    """`text` as a dish name, or None when it does not read as one.

    A dish name is short, has letters, and uses no markup, link, code or price characters. Prose
    (several sentences, lists of ingredients), add-on lines, checkout lines and anything that
    addresses a reader or a model are refused.
    """
    if not isinstance(text, str):
        return None
    name = " ".join(text.split())
    name = _ITEM_CODE.sub("", name).strip(" .\u00b7\u2022-\u2013\u2014:*")
    lower = name.lower()
    if (
        not 2 <= len(name) <= MAX_DISH_CHARS
        or not name.isprintable()
        or _BAD_CHARS.search(name)
        or "http" in lower
        or "www." in lower
        or sum(c.isalpha() for c in name) < 2
        or len(name.split()) > MAX_NAME_WORDS
        or name.count(",") >= 2
        or _SENTENCE.search(name)
        or _NOT_DISH.search(lower)
        or _ADD_ON.search(lower)
        or _INSTRUCTION.search(lower)
    ):
        return None
    return name


def parse_price(value: object, currency: object = None) -> Price | None:
    """A price from structured data (a number or a string) or from page text such as "$12.95"."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    text = str(value).strip()
    found = _AMOUNT.match(text)
    if found is None:
        return None
    symbol = found.group("sym") or found.group("sym2")
    number = found.group("num").replace(",", ".")
    try:
        amount = Decimal(number)
    except InvalidOperation:
        return None
    if not Decimal(0) < amount < MAX_AMOUNT:
        return None
    # As the page wrote it: "12" stays whole, and "12.00" or "12.5" keeps two decimals.
    shown = f"{amount:.2f}" if "." in number else str(int(amount))
    code = currency.strip().upper() if isinstance(currency, str) else None
    if code is not None and not _CURRENCY.fullmatch(code):
        code = None
    if code is None and symbol:
        code = _SYMBOL_CURRENCY.get(symbol)
    return Price(shown, code, symbol)


def _unique(dishes: list[DishFact]) -> list[DishFact]:
    seen: set[str] = set()
    kept = []
    for dish in dishes:
        key = dish.name.casefold()
        if key not in seen:
            seen.add(key)
            kept.append(dish)
    return kept


# JSON-LD


def _json_ld_dishes(scripts: list[str]) -> list[DishFact]:
    dishes: list[DishFact] = []
    budget = MAX_JSON_NODES
    for raw in scripts:
        text = raw.strip().removeprefix("<!--").removesuffix("-->").strip()
        try:
            data = json.loads(text)
        except ValueError:
            continue
        stack: list[tuple[object, int]] = [(data, 0)]
        while stack and budget > 0:
            node, depth = stack.pop()
            budget -= 1
            if depth > MAX_JSON_DEPTH:
                continue
            if isinstance(node, list):
                stack.extend((child, depth + 1) for child in reversed(node))
                continue
            if not isinstance(node, dict):
                continue
            if "MenuItem" in _types(node.get("@type")):
                dish = _json_ld_item(node)
                if dish is not None:
                    dishes.append(dish)
            children = [node[key] for key in _DESCEND if key in node]
            stack.extend((child, depth + 1) for child in reversed(children))
    return dishes


def _types(raw: object) -> set[str]:
    values = raw if isinstance(raw, list) else [raw]
    return {
        v.rsplit("/", 1)[-1].removeprefix("schema:") for v in values if isinstance(v, str) and v
    }


def _json_ld_item(node: dict[str, object]) -> DishFact | None:
    name = clean_dish_name(node.get("name"))
    if name is None:
        return None
    offers = node.get("offers")
    candidates = offers if isinstance(offers, list) else [offers]
    for offer in candidates:
        if not isinstance(offer, dict):
            continue
        spec = offer.get("priceSpecification")
        spec = spec[0] if isinstance(spec, list) and spec else spec
        for source in (offer, spec if isinstance(spec, dict) else {}):
            for key in ("price", "lowPrice"):
                price = parse_price(source.get(key), source.get("priceCurrency"))
                if price is not None:
                    return DishFact(name, price)
    return DishFact(name)


# Microdata


def _microdata_dishes(root: _Node) -> list[DishFact]:
    dishes: list[DishFact] = []
    for node in _walk(root):
        if "itemscope" in node.attrs and any(
            t.rstrip("/").endswith("MenuItem") for t in node.attrs.get("itemtype", "").split()
        ):
            name = clean_dish_name(_prop(node, "name", own_scope=True))
            if name is not None:
                price = parse_price(_prop(node, "price"), _prop(node, "priceCurrency"))
                dishes.append(DishFact(name, price))
    return dishes


def _walk(node: _Node) -> list[_Node]:
    """Every visible element under `node`, in document order."""
    found: list[_Node] = []
    stack = [node]
    while stack:
        current = stack.pop()
        found.append(current)
        stack.extend(c for c in reversed(current.children) if isinstance(c, _Node) and not c.skip)
    return found[1:]


def _prop(scope: _Node, prop: str, *, own_scope: bool = False) -> str | None:
    """The first value of `itemprop=prop` under `scope`, from `content` or the element's text.

    With `own_scope`, properties of nested items (such as an `Offer`) are not the scope's own.
    """
    stack = [c for c in reversed(scope.children) if isinstance(c, _Node) and not c.skip]
    while stack:
        node = stack.pop()
        if prop in node.attrs.get("itemprop", "").split():
            return node.attrs.get("content") or _text(node)
        if own_scope and "itemscope" in node.attrs:
            continue
        stack.extend(c for c in reversed(node.children) if isinstance(c, _Node) and not c.skip)
    return None


def _text(node: _Node) -> str:
    parts: list[str] = []
    stack: list[_Node | str] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            parts.append(current)
        elif not current.skip:
            stack.extend(reversed(current.children))
    return " ".join(" ".join(parts).split())


# Visible text


def _blocks(root: _Node) -> list[str]:
    """The page's visible text, one string per block-level run, in document order."""
    blocks: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        text = " ".join(" ".join(buffer).split())
        buffer.clear()
        if text and len(blocks) < MAX_BLOCKS:
            blocks.append(text)

    # A stack of children, with a marker after each block element to flush when it ends.
    stack: list[_Node | str | None] = [root]
    while stack:
        current = stack.pop()
        if current is None:
            flush()
        elif isinstance(current, str):
            buffer.append(current)
        elif not current.skip:
            block = current.tag in BLOCK
            if block:
                flush()
                stack.append(None)
            stack.extend(reversed(current.children))
    flush()
    return blocks


def _heuristic_dishes(blocks: list[str]) -> list[DishFact]:
    dishes: list[DishFact] = []
    for i, block in enumerate(blocks):
        if _ONLY_PRICE.match(block):
            continue
        line = _NAME_THEN_PRICE.match(block)
        if line is not None:
            name = clean_dish_name(line.group("name"))
            price = parse_price(line.group("price"))
            if name is not None and price is not None:
                dishes.append(DishFact(name, price))
            continue
        after = _ONLY_PRICE.match(blocks[i + 1]) if i + 1 < len(blocks) else None
        if after is not None:
            name = clean_dish_name(block)
            price = parse_price(after.group("price"))
            if name is not None and price is not None:
                dishes.append(DishFact(name, price))
    return dishes
