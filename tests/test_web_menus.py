"""Menu facts in the web API: off by default, and on, read only for a ref the server signed."""

from dataclasses import replace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from makan.config import Config, ConfigError, MenuFactsConfig
from makan.places import FakePlacesProvider, Place
from makan.places.facts import OSM_LICENCE, FactSource, OsmFacts, facts_from_tags
from makan.places.menu_fetch import MenuFetcher
from makan.providers import FakeProvider
from makan.web import create_app
from makan.web.menus import MenuRefs, menu_page
from makan.web.public import create_public_app
from tests.helpers import place

SECRET = "s" * 32
ON = Config(model="test/classifier", menu_facts=MenuFactsConfig(enabled=True, secret=SECRET))
OFF = Config(model="test/classifier")
BROWSE: dict[str, Any] = {"mode": "browse", "latitude": 3.14812, "longitude": 101.69498}
SOURCE = FactSource("openstreetmap", "2026-11-08T09:30:00Z", OSM_LICENCE, "2026-11-08")
MENU_HTML = b"<ul><li>Nasi Lemak $9.00</li><li>Roti Canai $3.50</li><li>Teh Tarik $2.50</li></ul>"

THAI = replace(place("Mid Thai", "thai_restaurant", 3.1485, 101.6951), website="thai.example")
RAMEN = replace(
    place("Ramen Ya", "ramen_restaurant", 3.1484, 101.6951),
    website="https://ramen.example/",
    osm=facts_from_tags("node/7", {"website:menu": "https://ramen.example/menu"}, SOURCE),
)
ORDER_ONLY = replace(
    place("Order Only", "cafe", 3.1483, 101.6951), website="https://www.doordash.com/store/x"
)
PLAIN = place("Plain Noodle", "noodle_restaurant", 3.1482, 101.6951)
HOSTED = replace(
    place("Hosted Menu", "cafe", 3.1481, 101.6951),
    website="https://hosted.example/",
    osm=facts_from_tags("node/8", {"website:menu": "https://hosted.popmenu.com/menu"}, SOURCE),
)
MAPPED_ONLY = replace(
    place("Mapped Only", "cafe", 3.1480, 101.6951),
    osm=facts_from_tags("node/9", {"website:menu": "https://mapped.example/menu"}, SOURCE),
)


class Venue:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=MENU_HTML)


def app(config: Config, venue: Venue | None = None, *places: Place) -> TestClient:
    fetcher = MenuFetcher(
        httpx.Client(transport=httpx.MockTransport(venue or Venue())),
        host_allowed=lambda host, port: True,
    )
    return TestClient(
        create_app(
            provider=FakeProvider([]),
            places=FakePlacesProvider(
                list(places or (THAI, RAMEN, ORDER_ONLY, PLAIN, HOSTED, MAPPED_ONLY))
            ),
            config=config,
            menus=fetcher,
        )
    )


def browse(client: TestClient) -> dict[str, dict[str, Any]]:
    response = client.post("/api/recommendations", json=BROWSE)
    assert response.status_code == 200
    return {p["name"]: p for p in response.json()["places"]}


def test_off_by_default_places_carry_no_ref_and_the_endpoint_is_absent() -> None:
    assert OFF.menu_facts.enabled is False
    venue = Venue()
    client = app(OFF, venue)
    for found in browse(client).values():
        assert "menu_ref" not in found
    ref = MenuRefs(SECRET).issue("https://thai.example")
    for method in ("get", "post"):
        response = client.request(method, "/api/places/menu", json={"ref": ref})
        assert response.status_code == 404
    assert venue.requests == []


def test_on_each_place_with_its_own_site_carries_a_ref_and_search_reads_no_page() -> None:
    venue = Venue()
    found = browse(app(ON, venue))
    refs = MenuRefs(SECRET)
    assert refs.read(found["Mid Thai"]["menu_ref"]) == "https://thai.example"
    assert refs.read(found["Ramen Ya"]["menu_ref"]) == "https://ramen.example/menu"  # OSM's menu
    assert found["Order Only"]["menu_ref"] is None  # a platform is never read
    assert found["Plain Noodle"]["menu_ref"] is None
    assert found["Hosted Menu"]["menu_ref"] is None  # a menu on another domain is not its own
    assert found["Mapped Only"]["menu_ref"] is None  # no website to say which domain is its own
    assert venue.requests == []  # a search list never fetches a menu


def test_the_menu_endpoint_reads_the_one_page_and_returns_facts_only() -> None:
    venue = Venue()
    client = app(ON, venue)
    ref = browse(client)["Mid Thai"]["menu_ref"]
    response = client.post(
        "/api/places/menu", json={"ref": ref}, headers={"Authorization": "Bearer diner-token"}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()
    assert body["status"] == "found" and body["source"]["host"] == "thai.example"
    assert [d["name"] for d in body["dishes"]] == ["Nasi Lemak", "Roti Canai", "Teh Tarik"]
    assert set(body) == {"status", "reason", "dishes", "truncated", "source"}
    assert [str(r.url) for r in venue.requests] == [
        "https://thai.example/robots.txt",
        "https://thai.example",
    ]
    for request in venue.requests:  # the diner's token, location and tastes never leave
        assert "authorization" not in request.headers
        assert "3.148" not in str(request.url) and "101.69" not in str(request.url)


def test_a_second_open_is_served_from_memory() -> None:
    venue = Venue()
    client = app(ON, venue)
    ref = browse(client)["Mid Thai"]["menu_ref"]
    for _ in range(3):
        assert client.post("/api/places/menu", json={"ref": ref}).json()["status"] == "found"
    assert len(venue.requests) == 2


@pytest.mark.parametrize(
    "ref",
    [
        MenuRefs("x" * 32).issue("https://thai.example"),  # another server's key
        MenuRefs(SECRET).issue("https://thai.example")[:-1] + "0",
        "v1.aHR0cHM6Ly9ldmlsLmV4YW1wbGU.0123456789abcdef0123456789abcdef",
        "v2." + MenuRefs(SECRET).issue("https://thai.example").split(".", 1)[1],
        "not a ref",
        "v1.%%%.é",
    ],
)
def test_a_ref_this_server_did_not_sign_reads_nothing(ref: str) -> None:
    venue = Venue()
    response = app(ON, venue).post("/api/places/menu", json={"ref": ref})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_menu_ref"
    assert venue.requests == []


def test_the_body_takes_only_a_ref() -> None:
    client = app(ON)
    assert client.post("/api/places/menu", json={}).status_code == 422
    assert client.post("/api/places/menu", json={"url": "https://thai.example"}).status_code == 422
    ref = MenuRefs(SECRET).issue("https://thai.example")
    extra = {"ref": ref, "latitude": 3.1}
    assert client.post("/api/places/menu", json=extra).status_code == 422
    assert client.post("/api/places/menu", json={"ref": "x" * 5000}).status_code == 422


def mapped(menu_url: str) -> OsmFacts:
    return facts_from_tags("node/1", {"website:menu": menu_url}, SOURCE)


def test_menu_page_prefers_the_mapped_menu_on_the_venues_own_domain() -> None:
    assert menu_page("venue.example/home", None) == "https://venue.example/home"
    menu = mapped("https://venue.example/menu")
    assert menu_page("https://www.venue.example/", menu) == "https://venue.example/menu"
    on_subdomain = mapped("https://menus.venue.example/dinner")
    assert menu_page("venue.example", on_subdomain) == "https://menus.venue.example/dinner"


@pytest.mark.parametrize(
    "menu_url",
    [
        "https://www.grubhub.com/x",
        "https://venue.popmenu.com/menu",
        "https://venue.example.net/menu",
        "https://other.github.io/menu",
    ],
)
def test_a_mapped_menu_on_another_domain_is_never_read(menu_url: str) -> None:
    assert menu_page("https://venue.example/", mapped(menu_url)) is None
    assert menu_page("https://venue.github.io/", mapped(menu_url)) is None


def test_no_readable_website_means_no_menu_page() -> None:
    assert menu_page(None, mapped("https://venue.example/menu")) is None
    assert menu_page("https://www.doordash.com/store/x", mapped("https://doordash.com/m")) is None
    assert menu_page("https://93.184.215.14/", None) is None
    assert menu_page("javascript:alert(1)", None) is None
    assert menu_page(None, None) is None


def test_the_flag_needs_a_signing_secret() -> None:
    with pytest.raises(ConfigError, match="MAKAN_MENU_FACTS_SECRET"):
        Config.from_env({"MAKAN_MODEL": "m", "MAKAN_MENU_FACTS_ENABLED": "1"})
    with pytest.raises(ConfigError, match="MAKAN_MENU_FACTS_ENABLED"):
        Config.from_env({"MAKAN_MODEL": "m", "MAKAN_MENU_FACTS_ENABLED": "maybe"})
    config = Config.from_env(
        {"MAKAN_MODEL": "m", "MAKAN_MENU_FACTS_ENABLED": "on", "MAKAN_MENU_FACTS_SECRET": SECRET}
    )
    assert config.menu_facts.enabled and SECRET not in repr(config)
    assert Config.from_env({"MAKAN_MODEL": "m"}).menu_facts == MenuFactsConfig()


def test_the_public_site_leaves_menu_facts_off() -> None:
    client = TestClient(create_public_app({"MAKAN_DEMO": "1"}))
    response = client.post("/api/recommendations", json=BROWSE)
    assert all("menu_ref" not in p for p in response.json()["places"])
    assert client.post("/api/places/menu", json={"ref": "v1.a.b"}).status_code == 404
