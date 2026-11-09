"""Read one venue's own menu page, on demand, politely, and remember the outcome for a day.

This runs only when a diner opens one place's detail, never for a search list or in bulk.
One read is at most one page: the venue's robots.txt (once per site), then the page itself,
following a few redirects and checking each hop again. Nothing about the diner goes to the venue:
the request carries only the venue's own public URL, a named user agent, and an `Accept` header,
with no cookies, referrer, location, taste or identity.

Rules, in the order they are checked:
- Only http and https, on the default ports to a host that resolves only to public addresses
  (`public_host`), so a hostile link can never reach the server's own network.
- Never an ordering, delivery, review or social platform (`THIRD_PARTY_HOSTS`): their terms
  forbid reading them, and they are not the venue's own site.
- robots.txt is honoured for Makan's own token and for every common AI crawler token: a site
  that turns AI crawlers away is treated as a no for Makan too. A robots.txt that answers 401 or
  403, fails, or times out is a no; one that is missing (404 or another 4xx) allows the page.
- A 401 or 403 on the page is a no. Only HTML is read: a PDF, an image or anything else is
  unreadable, and so is a page over `MAX_PAGE_BYTES`.
- The whole read, robots.txt included, has `DEADLINE_S` seconds.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlsplit

import httpx

from makan.places.links import web_link
from makan.places.menu_extract import extract_dishes
from makan.places.menus import DishFact, MenuFacts, MenuStatus, Method
from makan.places.robots import DISALLOW_ALL, RobotsRules, parse_robots

log = logging.getLogger("makan.places.menus")

PRODUCT_TOKEN = "MakanBot"
USER_AGENT = (
    f"{PRODUCT_TOKEN}/1 (+https://github.com/IvanYau88/Makan; "
    "reads one menu page when a diner opens a place)"
)
# A site that turns these away has said no to AI use, so Makan, an AI agent, stays away too.
AI_CRAWLER_TOKENS = (
    "GPTBot",
    "ChatGPT-User",
    "OAI-SearchBot",
    "ClaudeBot",
    "Claude-User",
    "Claude-SearchBot",
    "Claude-Web",
    "anthropic-ai",
    "CCBot",
    "Google-Extended",
    "PerplexityBot",
    "Perplexity-User",
    "Applebot-Extended",
    "Bytespider",
    "cohere-ai",
    "meta-externalagent",
    "Amazonbot",
)
# Ordering, delivery, reservation, review, link and social platforms, matched on the host and
# every parent domain. Their terms forbid automated reading, and none is the venue's own site.
THIRD_PARTY_HOSTS = frozenset(
    [
        "doordash.com",
        "grubhub.com",
        "seamless.com",
        "ubereats.com",
        "uber.com",
        "postmates.com",
        "caviar.com",
        "toasttab.com",
        "toast.site",
        "order.online",
        "menufy.com",
        "chownow.com",
        "slicelife.com",
        "beyondmenu.com",
        "menupages.com",
        "allmenus.com",
        "eatstreet.com",
        "ezcater.com",
        "gloriafood.com",
        "square.site",
        "squareup.com",
        "clover.com",
        "olo.com",
        "orderexperience.net",
        "hungerrush.com",
        "spoton.com",
        "owner.com",
        "bentobox.com",
        "deliveroo.com",
        "deliveroo.co.uk",
        "just-eat.com",
        "justeat.com",
        "skipthedishes.com",
        "foodpanda.com",
        "grab.com",
        "wolt.com",
        "glovoapp.com",
        "talabat.com",
        "zomato.com",
        "swiggy.com",
        "menulog.com.au",
        "gopuff.com",
        "instacart.com",
        "opentable.com",
        "resy.com",
        "exploretock.com",
        "sevenrooms.com",
        "yelp.com",
        "tripadvisor.com",
        "google.com",
        "goo.gl",
        "facebook.com",
        "fb.com",
        "instagram.com",
        "tiktok.com",
        "x.com",
        "twitter.com",
        "linktr.ee",
        "linkin.bio",
    ]
)

DEADLINE_S = 8.0
CONNECT_TIMEOUT_S = 3.0
MAX_PAGE_BYTES = 1_000_000
MAX_ROBOTS_BYTES = 512 * 1024  # RFC 9309 asks a crawler to read at least 500 KiB
MAX_REDIRECTS = 3
CACHE_TTL = timedelta(days=1)  # the most this module ever remembers anything
RETRY_TTL = timedelta(minutes=10)  # a site that failed is not asked again for this long
MAX_ENTRIES = 512
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_-]{1,40})""", re.I)
_TRANSIENT: frozenset[MenuStatus] = frozenset({"unavailable"})

Resolver = Callable[[str, int], Iterable[str]]


def _system_resolve(host: str, port: int) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def public_host(host: str, port: int, resolve: Resolver = _system_resolve) -> bool:
    """True for a default web port on a host whose every address is a public one. Raises
    OSError when the host does not resolve, which a read reports as unavailable."""
    if port not in (80, 443):
        return False
    addresses = [ipaddress.ip_address(a.split("%", 1)[0]) for a in resolve(host, port)]
    return bool(addresses) and all(a.is_global and not a.is_multicast for a in addresses)


def third_party(url: str) -> bool:
    """Whether `url` is on an ordering, delivery, review or social platform."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    parts = host.split(".")
    return any(".".join(parts[i:]) in THIRD_PARTY_HOSTS for i in range(len(parts) - 1))


class _Stop(Exception):
    """Ends a read with this outcome."""

    def __init__(self, status: MenuStatus, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass
class _Read:
    deadline: float
    robots: dict[str, RobotsRules]


class MenuFetcher:
    """Reads venue menu pages and keeps each outcome in memory for at most a day.

    `client` is an `httpx.Client` (tests give it a mock transport). `host_allowed` decides which
    hosts may be contacted, and defaults to public addresses only. Both clocks are injectable.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        host_allowed: Callable[[str, int], bool] = public_host,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        max_entries: int = MAX_ENTRIES,
    ) -> None:
        self._client = client or httpx.Client()
        self._host_allowed = host_allowed
        self._now = now
        self._monotonic = monotonic
        self._max_entries = max_entries
        self._entries: OrderedDict[str, MenuFacts] = OrderedDict()
        self._pending: dict[str, threading.Lock] = {}  # one lock per URL being read now
        self._lock = threading.Lock()

    def read(self, url: str) -> MenuFacts:
        """What the page at `url` lists, from memory when it was read in the last day.

        Reads of one URL that arrive together wait for the first, so a venue is asked once.
        """
        with self._lock:
            hit = self._fresh(url)
            if hit is not None:
                return hit
            pending = self._pending.setdefault(url, threading.Lock())
        with pending:
            with self._lock:
                hit = self._fresh(url)
                if hit is not None:
                    return hit
            try:
                facts = self._read(url, self._now())
            except BaseException:
                with self._lock:
                    self._pending.pop(url, None)
                raise
            with self._lock:  # stored before the lock goes, so no later read misses it
                self._entries[url] = facts
                self._entries.move_to_end(url)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
                self._pending.pop(url, None)
        return facts

    def _fresh(self, url: str) -> MenuFacts | None:
        hit = self._entries.get(url)
        return hit if hit is not None and self._now() < hit.expires_at else None

    def _read(self, url: str, now: datetime) -> MenuFacts:
        ttl = CACHE_TTL
        try:
            method, dishes, truncated, final = self._fetch(url)
        except _Stop as stop:
            ttl = RETRY_TTL if stop.status in _TRANSIENT else CACHE_TTL
            log.info("menu read of %s: %s (%s)", urlsplit(url).hostname, stop.status, stop.reason)
            return MenuFacts(stop.status, url, now, now + ttl, reason=stop.reason)
        status: MenuStatus = "found" if dishes else "none"
        return MenuFacts(
            status,
            final,
            now,
            now + ttl,
            reason=None if dishes else "no_dishes",
            method=method,
            dishes=dishes,
            truncated=truncated,
        )

    def _fetch(self, url: str) -> tuple[Method | None, tuple[DishFact, ...], bool, str]:
        """The dishes on the page at `url`, after its redirects, and the URL they came from."""
        read = _Read(self._monotonic() + DEADLINE_S, {})
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            self._check(current)
            parts = urlsplit(current)
            origin = f"{parts.scheme}://{parts.netloc}"
            rules = read.robots.get(origin)
            if rules is None:
                rules = read.robots[origin] = self._robots(origin, read)
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            if not rules.allows(PRODUCT_TOKEN, path):
                raise _Stop("refused", "robots")
            if any(not rules.allows(token, path) for token in AI_CRAWLER_TOKENS):
                raise _Stop("refused", "ai_rules")
            with self._stream(current, read, "text/html,application/xhtml+xml") as response:
                location = _redirect(response)
                if location is not None:
                    current = location
                    continue
                body = self._page(response, read)
                method, dishes, truncated = extract_dishes(body)
                return method, dishes, truncated, current
        raise _Stop("unavailable", "redirects")

    def _check(self, url: str) -> None:
        if web_link(url) != url:
            raise _Stop("unavailable", "bad_url")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise _Stop("unavailable", "bad_url")
        if third_party(url):
            raise _Stop("platform", "third_party")
        host = parts.hostname or ""
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
            allowed = self._host_allowed(host, port)
        except (OSError, ValueError, UnicodeError):
            raise _Stop("unavailable", "unreachable") from None
        if not allowed:
            raise _Stop("unavailable", "not_public")

    def _robots(self, origin: str, read: _Read) -> RobotsRules:
        current = f"{origin}/robots.txt"
        for _ in range(MAX_REDIRECTS + 1):
            with self._stream(current, read, "text/plain") as response:
                location = _redirect(response)
                if location is not None:
                    try:
                        self._check(location)
                    except _Stop:
                        return DISALLOW_ALL
                    current = location
                    continue
                status = response.status_code
                if status in (401, 403):
                    raise _Stop("refused", "robots_forbidden")
                if 400 <= status < 500 and status != 429:
                    return RobotsRules()  # no robots.txt: the site sets no rules
                if status != 200:
                    raise _Stop("unavailable", "robots_unavailable")
                data = self._body(response, read, MAX_ROBOTS_BYTES, truncate=True)
                return parse_robots(data.decode("utf-8", errors="replace"))
        raise _Stop("unavailable", "robots_unavailable")

    def _stream(self, url: str, read: _Read, accept: str) -> _Response:
        remaining = read.deadline - self._monotonic()
        if remaining <= 0:
            raise _Stop("unavailable", "timeout")
        request = self._client.build_request(
            "GET",
            url,
            headers={"User-Agent": USER_AGENT, "Accept": accept},
            timeout=httpx.Timeout(remaining, connect=min(CONNECT_TIMEOUT_S, remaining)),
        )
        return _Response(self._client, request)

    def _page(self, response: httpx.Response, read: _Read) -> str:
        status = response.status_code
        if status in (401, 403):
            raise _Stop("refused", "forbidden")
        if status in (404, 410):
            raise _Stop("unavailable", "not_found")
        if status != 200:
            raise _Stop("unavailable", f"http_{status}" if 400 <= status < 600 else "http_other")
        kind = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if kind == "application/pdf":
            raise _Stop("unreadable", "pdf")
        if kind.startswith("image/"):
            raise _Stop("unreadable", "image")
        if kind not in _HTML_TYPES:
            raise _Stop("unreadable", "not_html")
        data = self._body(response, read, MAX_PAGE_BYTES, truncate=False)
        return data.decode(_charset(response, data), errors="replace")

    def _body(self, response: httpx.Response, read: _Read, cap: int, *, truncate: bool) -> bytes:
        declared = response.headers.get("content-length", "")
        if not truncate and declared.isdigit() and int(declared) > cap:
            raise _Stop("unreadable", "too_large")
        chunks: list[bytes] = []
        size = 0
        try:
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > cap:
                    if truncate:
                        chunks.append(chunk[: cap - (size - len(chunk))])
                        break
                    raise _Stop("unreadable", "too_large")
                chunks.append(chunk)
                if self._monotonic() > read.deadline:
                    raise _Stop("unavailable", "timeout")
        except httpx.TimeoutException:
            raise _Stop("unavailable", "timeout") from None
        except httpx.HTTPError:
            raise _Stop("unavailable", "unreachable") from None
        return b"".join(chunks)


class _Response:
    """One streamed request as a context manager, with transport failures as outcomes."""

    def __init__(self, client: httpx.Client, request: httpx.Request) -> None:
        self._client = client
        self._request = request
        self._response: httpx.Response | None = None

    def __enter__(self) -> httpx.Response:
        try:
            self._response = self._client.send(self._request, stream=True, follow_redirects=False)
        except httpx.TimeoutException:
            raise _Stop("unavailable", "timeout") from None
        except httpx.HTTPError:
            raise _Stop("unavailable", "unreachable") from None
        return self._response

    def __exit__(self, *_: object) -> None:
        if self._response is not None:
            self._response.close()


def _redirect(response: httpx.Response) -> str | None:
    if response.status_code not in (301, 302, 303, 307, 308):
        return None
    location = str(response.headers.get("location", "")).strip()
    if not location:
        raise _Stop("unavailable", "redirects")
    return urljoin(str(response.request.url), location)


def _charset(response: httpx.Response, data: bytes) -> str:
    for candidate in (response.charset_encoding, _meta_charset(data)):
        if candidate:
            try:
                "".encode(candidate)
            except LookupError:
                continue
            return candidate
    return "utf-8"


def _meta_charset(data: bytes) -> str | None:
    found = _META_CHARSET.search(data[:4096])
    return bytes(found.group(1)).decode("ascii") if found else None
