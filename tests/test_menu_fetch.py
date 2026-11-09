"""One polite read of one venue page, over a mock transport: no test touches the network."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from makan.places.menu_fetch import (
    AI_CRAWLER_TOKENS,
    MAX_PAGE_BYTES,
    MAX_REDIRECTS,
    USER_AGENT,
    MenuFetcher,
    public_host,
    third_party,
)

PAGES = Path(__file__).parent / "fixtures" / "menus"
MENU = "https://lemongrass.example/menu"
HTML = {"content-type": "text/html; charset=utf-8"}
T0 = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)

Handler = Callable[[httpx.Request], httpx.Response]


def page(name: str) -> bytes:
    return (PAGES / name).read_bytes()


class Site:
    """Routes by URL to canned answers and records every request."""

    def __init__(self, routes: dict[str, httpx.Response | Handler | Exception]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get(str(request.url))
        if answer is None:
            return httpx.Response(404)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            return answer(request)
        return answer

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


class Clock:
    def __init__(self) -> None:
        self.now = T0
        self.mono = 0.0

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.mono += seconds


def fetcher(site: Site, clock: Clock | None = None, **kwargs: object) -> MenuFetcher:
    clock = clock or Clock()
    return MenuFetcher(
        httpx.Client(transport=httpx.MockTransport(site)),
        host_allowed=lambda host, port: not host.startswith("internal."),
        now=lambda: clock.now,
        monotonic=lambda: clock.mono,
        **kwargs,  # type: ignore[arg-type]
    )


def ok(name: str = "json_ld.html") -> httpx.Response:
    return httpx.Response(200, headers=HTML, content=page(name))


def robots(text: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/plain"}, text=text)


def test_a_json_ld_menu_is_read_with_its_source_and_dates() -> None:
    site = Site({"https://lemongrass.example/robots.txt": robots(""), MENU: ok()})
    facts = fetcher(site).read(MENU)
    assert facts.status == "found" and facts.method == "json_ld"
    assert [d.name for d in facts.dishes][:2] == ["Fresh Spring Rolls", "Chicken Satay"]
    assert facts.source_url == MENU
    assert facts.retrieved_at == T0 and facts.expires_at == T0 + timedelta(days=1)
    body = facts.to_json()
    assert body["source"] == {
        "url": MENU,
        "host": "lemongrass.example",
        "method": "json_ld",
        "retrieved_at": "2026-11-08T12:00:00Z",
        "expires_at": "2026-11-09T12:00:00Z",
    }
    assert body["dishes"][0] == {
        "name": "Fresh Spring Rolls",
        "price": {"amount": "7.50", "currency": "USD", "label": "$7.50"},
    }


def test_the_request_names_makan_and_carries_nothing_about_the_diner() -> None:
    site = Site({"https://lemongrass.example/robots.txt": robots(""), MENU: ok()})
    fetcher(site).read(MENU)
    assert site.urls == ["https://lemongrass.example/robots.txt", MENU]
    for request in site.requests:
        assert request.headers["user-agent"] == USER_AGENT
        assert request.method == "GET" and request.content == b""
        for header in ("cookie", "referer", "authorization", "x-forwarded-for"):
            assert header not in request.headers
        assert set(request.headers) <= {
            "host",
            "accept",
            "accept-encoding",
            "connection",
            "user-agent",
        }


def test_robots_disallow_stops_the_read_before_the_page() -> None:
    site = Site(
        {
            "https://lemongrass.example/robots.txt": robots("User-agent: *\nDisallow: /menu\n"),
            MENU: ok(),
        }
    )
    facts = fetcher(site).read(MENU)
    assert (facts.status, facts.reason) == ("refused", "robots")
    assert facts.dishes == () and site.urls == ["https://lemongrass.example/robots.txt"]


def test_a_site_that_turns_ai_crawlers_away_is_a_no_for_makan_too() -> None:
    for token in ("GPTBot", "ClaudeBot", "CCBot"):
        assert token in AI_CRAWLER_TOKENS
        rules = f"User-agent: *\nAllow: /\n\nUser-agent: {token}\nDisallow: /\n"
        site = Site({"https://lemongrass.example/robots.txt": robots(rules), MENU: ok()})
        facts = fetcher(site).read(MENU)
        assert (facts.status, facts.reason) == ("refused", "ai_rules")
        assert MENU not in site.urls


def test_a_robots_rule_for_makan_itself_is_honoured() -> None:
    rules = "User-agent: *\nAllow: /\n\nUser-agent: MakanBot\nDisallow: /\n"
    site = Site({"https://lemongrass.example/robots.txt": robots(rules), MENU: ok()})
    assert fetcher(site).read(MENU).status == "refused"


@pytest.mark.parametrize(
    ("answer", "status", "reason", "fetched"),
    [
        (httpx.Response(404), "found", None, True),  # no robots.txt: no rules
        (httpx.Response(410), "found", None, True),
        (httpx.Response(401), "refused", "robots_forbidden", False),
        (httpx.Response(403), "refused", "robots_forbidden", False),
        (httpx.Response(429), "unavailable", "robots_unavailable", False),
        (httpx.Response(503), "unavailable", "robots_unavailable", False),
        (httpx.ConnectError("refused"), "unavailable", "unreachable", False),
        (httpx.ReadTimeout("slow"), "unavailable", "timeout", False),
    ],
)
def test_what_each_robots_answer_means(
    answer: httpx.Response | Exception, status: str, reason: str | None, fetched: bool
) -> None:
    site = Site({"https://lemongrass.example/robots.txt": answer, MENU: ok()})
    facts = fetcher(site).read(MENU)
    assert facts.status == status
    assert facts.reason == reason if reason else facts.dishes
    assert (MENU in site.urls) is fetched


@pytest.mark.parametrize(("code", "status"), [(401, "refused"), (403, "refused")])
def test_a_page_that_blocks_the_client_is_a_no(code: int, status: str) -> None:
    site = Site({"https://lemongrass.example/robots.txt": robots(""), MENU: httpx.Response(code)})
    facts = fetcher(site).read(MENU)
    assert (facts.status, facts.reason) == (status, "forbidden")


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (httpx.Response(404), "not_found"),
        (httpx.Response(500), "http_500"),
        (httpx.ConnectError("no route"), "unreachable"),
        (httpx.ConnectTimeout("slow"), "timeout"),
        (httpx.ReadTimeout("slow"), "timeout"),
    ],
)
def test_a_page_that_fails_is_unavailable(answer: httpx.Response | Exception, reason: str) -> None:
    site = Site({"https://lemongrass.example/robots.txt": robots(""), MENU: answer})
    facts = fetcher(site).read(MENU)
    assert (facts.status, facts.reason) == ("unavailable", reason)


def test_the_whole_read_has_one_deadline() -> None:
    clock = Clock()

    def slow_robots(request: httpx.Request) -> httpx.Response:
        clock.advance(9)  # past the deadline before the page is asked for
        return robots("")

    site = Site({"https://lemongrass.example/robots.txt": slow_robots, MENU: ok()})
    facts = fetcher(site, clock).read(MENU)
    assert (facts.status, facts.reason) == ("unavailable", "timeout")
    assert MENU not in site.urls


def test_each_request_gets_only_the_time_left() -> None:
    clock = Clock()
    seen: list[dict[str, float | None]] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        clock.advance(5)
        return robots("") if request.url.path == "/robots.txt" else ok()

    site = Site({"https://lemongrass.example/robots.txt": record, MENU: record})
    fetcher(site, clock).read(MENU)
    assert seen[0]["read"] == 8.0 and seen[0]["connect"] == 3.0
    assert seen[1]["read"] == 3.0 and seen[1]["connect"] == 3.0


def test_an_oversized_page_is_not_read() -> None:
    big = b"<html>" + b"x" * MAX_PAGE_BYTES + b"</html>"
    declared = httpx.Response(200, headers={**HTML, "content-length": str(len(big))}, content=big)
    undeclared = httpx.Response(200, headers=HTML, content=iter([big[:600_000], big[600_000:]]))
    for answer in (declared, undeclared):
        site = Site({"https://lemongrass.example/robots.txt": robots(""), MENU: answer})
        facts = fetcher(site).read(MENU)
        assert (facts.status, facts.reason) == ("unreadable", "too_large")


@pytest.mark.parametrize(
    ("kind", "reason"),
    [("application/pdf", "pdf"), ("image/jpeg", "image"), ("application/json", "not_html")],
)
def test_only_html_is_read(kind: str, reason: str) -> None:
    answer = httpx.Response(200, headers={"content-type": kind}, content=b"%PDF-1.7")
    site = Site({"https://lemongrass.example/robots.txt": robots(""), MENU: answer})
    facts = fetcher(site).read(MENU)
    assert (facts.status, facts.reason) == ("unreadable", reason)


def test_a_page_with_no_menu_says_so() -> None:
    site = Site({"https://lemongrass.example/robots.txt": robots(""), MENU: ok("no_menu.html")})
    facts = fetcher(site).read(MENU)
    assert (facts.status, facts.reason, facts.dishes) == ("none", "no_dishes", ())


def test_the_page_charset_is_honoured() -> None:
    body = "<ul><li>Crème Brûlée €8,50</li><li>Café Crème €3,20</li><li>Tarte Tatin €7</li></ul>"
    answer = httpx.Response(
        200,
        headers={"content-type": "text/html; charset=windows-1252"},
        content=body.encode("cp1252"),
    )
    site = Site({"https://lemongrass.example/robots.txt": robots(""), MENU: answer})
    facts = fetcher(site).read(MENU)
    assert [d.name for d in facts.dishes] == ["Crème Brûlée", "Café Crème", "Tarte Tatin"]
    assert facts.dishes[0].price is not None and facts.dishes[0].price.label == "€8.50"


def test_redirects_are_followed_and_checked_again() -> None:
    site = Site(
        {
            "http://lemongrass.example/robots.txt": httpx.Response(
                301, headers={"location": "https://lemongrass.example/robots.txt"}
            ),
            "https://lemongrass.example/robots.txt": robots("User-agent: *\nDisallow: /old\n"),
            "http://lemongrass.example/menu": httpx.Response(
                301, headers={"location": "https://lemongrass.example/menu"}
            ),
            MENU: ok(),
        }
    )
    facts = fetcher(site).read("http://lemongrass.example/menu")
    assert facts.status == "found" and facts.source_url == MENU


def test_a_redirect_to_a_platform_or_a_disallowed_path_is_not_followed() -> None:
    site = Site(
        {
            "https://lemongrass.example/robots.txt": robots("User-agent: *\nDisallow: /old\n"),
            MENU: httpx.Response(302, headers={"location": "https://www.toasttab.com/lemongrass"}),
            "https://lemongrass.example/dinner": httpx.Response(302, headers={"location": "/old"}),
        }
    )
    facts = fetcher(site).read(MENU)
    assert (facts.status, facts.reason) == ("platform", "third_party")
    assert not any("toasttab" in url for url in site.urls)
    facts = fetcher(site).read("https://lemongrass.example/dinner")
    assert (facts.status, facts.reason) == ("refused", "robots")


def test_a_redirect_to_another_site_reads_that_sites_robots() -> None:
    site = Site(
        {
            "https://lemongrass.example/robots.txt": robots(""),
            MENU: httpx.Response(302, headers={"location": "https://menus.lemongrass.example/m"}),
            "https://menus.lemongrass.example/robots.txt": robots("User-agent: *\nDisallow: /\n"),
        }
    )
    facts = fetcher(site).read(MENU)
    assert (facts.status, facts.reason) == ("refused", "robots")


def test_redirect_loops_end() -> None:
    loop = httpx.Response(302, headers={"location": MENU})
    site = Site({"https://lemongrass.example/robots.txt": robots(""), MENU: loop})
    facts = fetcher(site).read(MENU)
    assert (facts.status, facts.reason) == ("unavailable", "redirects")
    assert site.urls.count(MENU) == MAX_REDIRECTS + 1


def test_platforms_private_hosts_and_odd_urls_are_never_contacted() -> None:
    site = Site({})
    reader = fetcher(site)
    for url, status, reason in [
        ("https://www.doordash.com/store/lemongrass", "platform", "third_party"),
        ("https://lemongrass.order.online/", "platform", "third_party"),
        ("https://m.facebook.com/lemongrass", "platform", "third_party"),
        ("https://internal.lemongrass.example/menu", "unavailable", "not_public"),
        ("ftp://lemongrass.example/menu", "unavailable", "bad_url"),
        ("https://user:pw@lemongrass.example/menu", "unavailable", "bad_url"),
    ]:
        facts = reader.read(url)
        assert (facts.status, facts.reason) == (status, reason), url
    assert site.requests == []


def test_a_host_that_does_not_resolve_is_unavailable() -> None:
    def no_dns(host: str, port: int) -> bool:
        raise OSError("Name or service not known")

    reader = MenuFetcher(httpx.Client(transport=httpx.MockTransport(Site({}))), host_allowed=no_dns)
    facts = reader.read(MENU)
    assert (facts.status, facts.reason) == ("unavailable", "unreachable")


def test_public_host_allows_only_public_addresses() -> None:
    def resolver(*addresses: str) -> Callable[[str, int], list[str]]:
        return lambda host, port: list(addresses)

    assert public_host("venue.example", 443, resolver("93.184.215.14"))
    assert public_host("venue.example", 443, resolver("2606:2800:21f:cb07:6820:80da:af6b:8b2c"))
    for private in ("127.0.0.1", "10.0.0.8", "192.168.1.1", "169.254.169.254", "::1", "fd00::1"):
        assert not public_host("venue.example", 443, resolver("93.184.215.14", private))
    assert not public_host("venue.example", 443, resolver())
    assert not public_host("venue.example", 8443, resolver("93.184.215.14"))
    assert public_host("venue.example", 80, resolver("93.184.215.14"))


def test_third_party_matches_the_host_and_its_parents_only() -> None:
    assert third_party("https://www.grubhub.com/restaurant/x")
    assert third_party("https://order.toasttab.com/online/x")
    assert not third_party("https://grubhub.com.lemongrass.example/")
    assert not third_party("https://notyelp.com/")


def test_an_outcome_is_remembered_for_a_day_and_then_read_again() -> None:
    clock = Clock()
    site = Site({"https://lemongrass.example/robots.txt": robots(""), MENU: ok()})
    reader = fetcher(site, clock)
    first = reader.read(MENU)
    clock.advance(23 * 3600)
    assert reader.read(MENU) is first
    assert len(site.requests) == 2
    clock.advance(3600)
    assert reader.read(MENU).retrieved_at == clock.now
    assert len(site.requests) == 4


def test_a_refusal_is_remembered_too_so_the_site_is_not_asked_again() -> None:
    clock = Clock()
    site = Site({"https://lemongrass.example/robots.txt": robots("User-agent: *\nDisallow: /")})
    reader = fetcher(site, clock)
    reader.read(MENU)
    clock.advance(12 * 3600)
    assert reader.read(MENU).status == "refused"
    assert len(site.requests) == 1


def test_a_failure_is_retried_after_ten_minutes() -> None:
    clock = Clock()
    site = Site({"https://lemongrass.example/robots.txt": httpx.Response(503)})
    reader = fetcher(site, clock)
    failed = reader.read(MENU)
    assert failed.expires_at == T0 + timedelta(minutes=10)
    clock.advance(5 * 60)
    reader.read(MENU)
    assert len(site.requests) == 1
    clock.advance(6 * 60)
    reader.read(MENU)
    assert len(site.requests) == 2


def test_the_cache_is_bounded() -> None:
    site = Site({})
    reader = fetcher(site, max_entries=2)
    for host in ("a", "b", "c"):
        reader.read(f"https://{host}.example/")
    reader.read("https://c.example/")
    assert site.urls.count("https://a.example/robots.txt") == 1
    reader.read("https://a.example/")
    assert site.urls.count("https://a.example/robots.txt") == 2


def test_reads_of_one_page_that_arrive_together_ask_the_venue_once() -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_page(request: httpx.Request) -> httpx.Response:
        started.set()
        release.wait(5)
        return ok()

    site = Site({"https://lemongrass.example/robots.txt": robots(""), MENU: slow_page})
    reader = MenuFetcher(
        httpx.Client(transport=httpx.MockTransport(site)), host_allowed=lambda host, port: True
    )
    results: list[str] = []
    threads = [
        threading.Thread(target=lambda: results.append(reader.read(MENU).status)) for _ in range(3)
    ]
    for thread in threads:
        thread.start()
    assert started.wait(5)
    release.set()
    for thread in threads:
        thread.join(5)
    assert results == ["found"] * 3
    assert site.urls == ["https://lemongrass.example/robots.txt", MENU]
