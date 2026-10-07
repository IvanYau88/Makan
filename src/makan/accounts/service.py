"""The account rules on top of any `AccountStore`: sign-in, profile, taste, export, and deletion.

Who a request is for is decided before this is called, by `makan.accounts.tokens`. This
trusts the user id it is given.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from makan.accounts.errors import AccountGone
from makan.accounts.profile import clean_display_name
from makan.accounts.store import AccountStore
from makan.accounts.taste import Taste, checked, read_taste, save_taste
from makan.memory.service import Memory
from makan.models import Profile

log = logging.getLogger("makan.accounts")


class AuthAdmin(Protocol):
    """What the service needs from the auth provider's admin API."""

    def user_exists(self, user_id: UUID) -> bool: ...

    def delete_user(self, user_id: UUID) -> None: ...


class Accounts:
    def __init__(self, store: AccountStore, memory: Memory, admin: AuthAdmin) -> None:
        self.store = store
        self.memory = memory
        self.admin = admin

    def ensure_user(self, user_id: UUID) -> None:
        """Make the `users` row on a person's first sign-in.

        A token stays valid until it expires, even after the account is deleted, so before a row
        is made for someone new the auth provider is asked whether the account still exists.
        That is one extra call, only the first time, and it stops a deleted person's token from
        bringing their rows back.
        """
        if self.store.user_exists(user_id):
            return
        if not self.admin.user_exists(user_id):
            raise AccountGone("the account was deleted")
        self.store.create_user(user_id)

    def profile(self, user_id: UUID) -> Profile | None:
        return self.store.get_profile(user_id)

    def display_name(self, user_id: UUID) -> str | None:
        """The name to greet the person with, or None before they have made a profile."""
        profile = self.store.get_profile(user_id)
        return profile.display_name if profile else None

    def save_profile(
        self, user_id: UUID, *, display_name: str, location_history_opt_in: bool | None = None
    ) -> Profile:
        """Create or update the profile. The name is required and is cleaned here."""
        self.ensure_user(user_id)
        return self.store.save_profile(
            user_id,
            display_name=clean_display_name(display_name),
            location_history_opt_in=location_history_opt_in,
        )

    def taste(self, user_id: UUID) -> Taste:
        return read_taste(self.memory, user_id)

    def save_taste(self, user_id: UUID, taste: Taste) -> Taste:
        cleaned = checked(taste)
        self.ensure_user(user_id)
        return save_taste(self.memory, user_id, cleaned)

    def export(self, user_id: UUID) -> dict[str, Any]:
        """Everything stored for the person, plus when it was exported."""
        data = self.store.export(user_id)
        return {"exported_at": datetime.now(UTC).isoformat(), **data}

    def delete(self, user_id: UUID) -> None:
        """Delete the person's rows, then their auth account.

        The rows go first. If the auth account then fails to delete, the person can still sign
        in and try again, and nothing of theirs is left stored. The other order could leave
        their data stored behind an account they can no longer open. Both steps can be repeated.
        """
        self.store.delete_user(user_id)
        self.admin.delete_user(user_id)
        log.info("deleted an account and its data")
