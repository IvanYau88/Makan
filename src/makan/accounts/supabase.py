"""The Supabase Auth admin API, for the two things only the server may do.

It checks that an account still exists before the first rows are made for it, and it deletes
an account. Both need the service role key, which can do anything to the project's users, so
it stays on the server: it is read from the environment, hidden from `repr`, sent only to
Supabase, and no error here ever carries it, a request URL, or a response body.
"""

from __future__ import annotations

import logging
from uuid import UUID

import httpx

from makan.accounts.errors import AuthUnavailable

log = logging.getLogger("makan.accounts")


class SupabaseAdmin:
    def __init__(
        self, url: str, service_role_key: str, *, client: httpx.Client | None = None
    ) -> None:
        self._base = f"{url.rstrip('/')}/auth/v1/admin/users"
        self._client = client or httpx.Client(timeout=10.0)
        # A legacy key is a JWT that also goes in Authorization. A newer `sb_secret_` key is not
        # a JWT, and goes in `apikey` only.
        self._headers = {"apikey": service_role_key}
        if service_role_key.count(".") == 2:
            self._headers["Authorization"] = f"Bearer {service_role_key}"

    def __repr__(self) -> str:
        return "SupabaseAdmin()"

    def user_exists(self, user_id: UUID) -> bool:
        status = self._send("GET", user_id)
        if status == 200:
            return True
        if status == 404:
            return False
        raise AuthUnavailable(f"Supabase answered {status}")

    def delete_user(self, user_id: UUID) -> None:
        """Delete the account. One already gone counts as deleted, so a retry is safe."""
        status = self._send("DELETE", user_id)
        if status not in (200, 204, 404):
            raise AuthUnavailable(f"Supabase answered {status}")

    def _send(self, method: str, user_id: UUID) -> int:
        try:
            return self._client.request(
                method, f"{self._base}/{user_id}", headers=self._headers
            ).status_code
        except httpx.HTTPError as exc:
            log.warning("Supabase admin request failed (%s)", type(exc).__name__)
            raise AuthUnavailable("could not reach Supabase") from None
