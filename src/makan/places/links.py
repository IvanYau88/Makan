"""Links taken from source data, checked so they are safe to show and follow."""

from __future__ import annotations

from urllib.parse import urlsplit

MAX_URL_CHARS = 2048


def web_link(text: object) -> str | None:
    """`text` as an absolute http or https address that is safe to link to, else None.

    Source data is not trusted: no other scheme, no credentials, spaces, or control characters,
    so a link out can never run script or hide where it goes. A bare domain such as
    `www.venue.example`, which sources often give, is read as https.
    """
    if not isinstance(text, str):
        return None
    url = text.strip()
    if url and "://" not in url and not url.startswith("//") and ":" not in url.split("/")[0]:
        url = "https://" + url
    if (
        not url
        or len(url) > MAX_URL_CHARS
        or any(ch.isspace() or not ch.isprintable() for ch in url)
    ):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host or "." not in host:
        return None
    if "@" in parts.netloc:
        return None
    return url
