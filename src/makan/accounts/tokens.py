"""Checking a Supabase access token on an API request.

Supabase signs access tokens with an asymmetric key and publishes the public half at
`<project>/auth/v1/.well-known/jwks.json`, so the backend verifies a token with no shared
secret and holds no key that could mint one. Only asymmetric algorithms are accepted, so a
token cannot pass by naming a symmetric one. The signature, the expiry, the issuer (this
project), the audience (`authenticated`), the `authenticated` role, and a UUID subject are
all required.

Keys are cached. A token signed by a key that is not cached triggers one refetch, at most one
every `refetch_seconds`, so a key rotation is picked up and a stream of made-up key ids cannot
turn into a stream of requests to Supabase. If Supabase cannot be reached, cached keys keep
working.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import httpx

from makan.accounts.errors import AuthUnavailable, InvalidToken

try:
    import jwt
except ImportError:  # pragma: no cover - only without the web extra
    raise ImportError('PyJWT is not installed; run: pip install -e ".[dev]"') from None

log = logging.getLogger("makan.accounts")

ALGORITHMS = ["ES256", "RS256", "EdDSA"]
AUDIENCE = "authenticated"
CLOCK_SKEW_SECONDS = 10


@dataclass(frozen=True)
class Identity:
    """Who a verified token is for. `email` is shown back to the person and is never stored."""

    user_id: UUID
    email: str | None = None


class TokenVerifier:
    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        *,
        client: httpx.Client | None = None,
        cache_seconds: float = 600.0,
        refetch_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._client = client or httpx.Client(timeout=5.0)
        self._cache_seconds = cache_seconds
        self._refetch_seconds = refetch_seconds
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._lock = threading.Lock()

    def verify(self, token: str) -> Identity:
        """The identity in a valid token. Raises `InvalidToken`, or `AuthUnavailable` when the
        keys are needed and Supabase cannot be reached."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise InvalidToken("not a token") from None
        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm not in ALGORITHMS or not isinstance(key_id, str):
            raise InvalidToken("unsupported token")
        key = self._key(key_id)
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=[algorithm],
                audience=AUDIENCE,
                issuer=self._issuer,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "sub", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError:
            raise InvalidToken("expired", expired=True) from None
        except jwt.PyJWTError:
            raise InvalidToken("rejected") from None
        if claims.get("role") != "authenticated":
            raise InvalidToken("not a signed-in user")
        try:
            user_id = UUID(str(claims["sub"]))
        except ValueError:
            raise InvalidToken("bad subject") from None
        email = claims.get("email")
        return Identity(user_id, email if isinstance(email, str) and email else None)

    def _key(self, key_id: str) -> jwt.PyJWK:
        with self._lock:
            now = self._clock()
            stale = self._fetched_at is None or now - self._fetched_at >= self._cache_seconds
            if stale:
                self._refresh(now)
            key = self._keys.get(key_id)
            if key is None and not stale and self._can_refetch(now):
                self._refresh(now)
                key = self._keys.get(key_id)
        if key is None:
            raise InvalidToken("unknown signing key")
        return key

    def _can_refetch(self, now: float) -> bool:
        return self._fetched_at is None or now - self._fetched_at >= self._refetch_seconds

    def _refresh(self, now: float) -> None:
        try:
            response = self._client.get(self._jwks_url)
            response.raise_for_status()
            listed = response.json()["keys"]
            if not isinstance(listed, list):
                raise TypeError("keys is not a list")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # Keep serving from the keys already fetched. The failure is retried on the next call.
            log.warning("could not fetch the signing keys (%s)", type(exc).__name__)
            if not self._keys:
                raise AuthUnavailable("could not fetch the signing keys") from None
            self._fetched_at = now - self._cache_seconds + self._refetch_seconds
            return
        keys: dict[str, jwt.PyJWK] = {}
        for jwk in listed:
            try:
                keys[jwk["kid"]] = jwt.PyJWK(jwk)
            except (jwt.PyJWTError, KeyError, TypeError):
                continue  # a key this code cannot use is skipped, not fatal
        self._keys = keys
        self._fetched_at = now
