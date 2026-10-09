"""Where users and profiles live, and how a person's data is exported and deleted.

`InMemoryAccountStore` is for tests, over an `InMemoryStore` of memory facts, and
`makan.accounts.postgres.PostgresAccountStore` is the real one. A store only keeps and finds
rows. The rules are in `makan.accounts.service`.
"""

from __future__ import annotations

import dataclasses
import json
import threading
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from makan.memory.store import InMemoryStore
from makan.models import Profile, User
from makan.visits.store import InMemoryVisitStore


class AccountStore(Protocol):
    def user_exists(self, user_id: UUID) -> bool: ...

    def create_user(self, user_id: UUID) -> None:
        """Make the `users` row, doing nothing if it exists."""
        ...

    def get_profile(self, user_id: UUID) -> Profile | None: ...

    def save_profile(
        self, user_id: UUID, *, display_name: str, location_history_opt_in: bool | None
    ) -> Profile:
        """Create or replace the profile. None leaves the opt-in as it is, or off for a new one.

        The `users` row must exist.
        """
        ...

    def export(self, user_id: UUID) -> dict[str, Any]:
        """Every row stored for the user, as JSON-ready data, read as one snapshot.

        That includes their visits, dishes, going-here markers, the tags they made, and the tag
        requests addressed to them.
        """
        ...

    def delete_user(self, user_id: UUID) -> None:
        """Delete the user and, by cascade, everything they own. Doing nothing if they are gone."""
        ...


class InMemoryAccountStore:
    def __init__(
        self, memory: InMemoryStore | None = None, visits: InMemoryVisitStore | None = None
    ) -> None:
        self.memory = memory or InMemoryStore()
        self.visits = visits or InMemoryVisitStore()
        self._users: dict[UUID, User] = {}
        self._profiles: dict[UUID, Profile] = {}
        self._lock = threading.Lock()

    def user_exists(self, user_id: UUID) -> bool:
        with self._lock:
            return user_id in self._users

    def create_user(self, user_id: UUID) -> None:
        with self._lock:
            self._users.setdefault(user_id, User(user_id, datetime.now(UTC)))

    def get_profile(self, user_id: UUID) -> Profile | None:
        with self._lock:
            return self._profiles.get(user_id)

    def save_profile(
        self, user_id: UUID, *, display_name: str, location_history_opt_in: bool | None
    ) -> Profile:
        with self._lock:
            if user_id not in self._users:
                raise ValueError("no such user")
            now = datetime.now(UTC)
            old = self._profiles.get(user_id)
            profile = Profile(
                user_id=user_id,
                display_name=display_name,
                location_history_opt_in=(
                    location_history_opt_in
                    if location_history_opt_in is not None
                    else (old.location_history_opt_in if old else False)
                ),
                created_at=old.created_at if old else now,
                updated_at=now,
            )
            self._profiles[user_id] = profile
            return profile

    def export(self, user_id: UUID) -> dict[str, Any]:
        with self._lock:
            user = self._users.get(user_id)
            profile = self._profiles.get(user_id)
        return {
            "user": _json(user),
            "profile": _json(profile),
            "memory_facts": [_json(f) for f in self.memory.all_for_user(user_id)],
            "sessions": [],
            "participants": [],
            "trace_events": [],
            **_json_value(self.visits.export(user_id)),
        }

    def delete_user(self, user_id: UUID) -> None:
        with self._lock:
            self._users.pop(user_id, None)
            self._profiles.pop(user_id, None)
        self.memory.delete_user(user_id)
        self.visits.delete_user(user_id)


def _json(row: Any) -> Any:
    if row is None:
        return None
    return _json_value(dataclasses.asdict(row))


def _json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_default))


def _default(value: object) -> object:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)  # as Postgres writes a numeric into JSON
    return str(value)
