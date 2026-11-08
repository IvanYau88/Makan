"""Stateless, server-issued fit scopes, bound to the verified caller and kept in tab memory.

Consent is explicit on each search. A signed scope is never a cache key or a way to
read an assessment; it only isolates the gateway's private cache and withdrawal purge.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from makan.accounts.tokens import Identity


class FitSessions:
    def __init__(self, secret: str) -> None:
        self._secret = secret.encode()

    def _sign(self, value: str, identity: Identity | None) -> str:
        owner = str(identity.user_id) if identity else "guest"
        return hmac.new(
            self._secret, f"fit-consent-v1:{owner}:{value}".encode(), hashlib.sha256
        ).hexdigest()

    def issue(self, identity: Identity | None) -> str:
        value = f"{secrets.token_hex(32)}.{int(time.time()) + 86400}"
        return value + "." + self._sign(value, identity)

    def scope(self, token: str | None, identity: Identity | None) -> str | None:
        if token is None or not 1 <= len(token) <= 256 or len(self._secret) < 32:
            return None
        try:
            nonce, expires, signature = token.split(".")
            if (
                len(nonce) != 64
                or len(signature) != 64
                or not 0 < int(expires) - time.time() <= 86400
            ):
                return None
            if not hmac.compare_digest(signature, self._sign(nonce + "." + expires, identity)):
                return None
        except (ValueError, TypeError):
            return None
        return f"owner:{identity.user_id}" if identity else f"guest:{nonce}"
