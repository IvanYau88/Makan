"""An `AccountStore` over `users`, `profiles`, and everything that cascades from them.

It takes an open connection and does not manage it. The connection is the backend's privileged
one, which row-level security does not bind, so every query here names the user it is for and
the caller has already checked who that is. One lock serializes use of the connection across
threads, and it can be shared with another store on the same connection.
"""

from __future__ import annotations

import threading
from typing import Any
from uuid import UUID

from makan.models import Profile

try:
    import psycopg
except ImportError:  # pragma: no cover - only without the extra
    raise ImportError('psycopg is not installed; run: pip install -e ".[dev]"') from None

_PROFILE = "user_id, display_name, location_history_opt_in, created_at, updated_at"

# One statement, so every part of the export comes from one snapshot. `to_jsonb` of a row keeps
# every column, so a column added by a later migration is exported without a change here.
_EXPORT = """
select jsonb_build_object(
  'user', (select to_jsonb(u) from users u where u.id = %(id)s),
  'profile', (select to_jsonb(p) from profiles p where p.user_id = %(id)s),
  'memory_facts', coalesce((select jsonb_agg(to_jsonb(m) order by m.created_at, m.id)
                            from memory_facts m where m.user_id = %(id)s), '[]'::jsonb),
  'sessions', coalesce((select jsonb_agg(to_jsonb(s) order by s.created_at, s.id)
                        from sessions s where s.user_id = %(id)s), '[]'::jsonb),
  'participants', coalesce((select jsonb_agg(to_jsonb(q) order by q.joined_at, q.id)
                            from participants q where q.user_id = %(id)s), '[]'::jsonb),
  'trace_events', coalesce((select jsonb_agg(to_jsonb(t) order by t.ts, t.run_id, t.seq)
                            from trace_events t where t.user_id = %(id)s), '[]'::jsonb)
)
"""


class PostgresAccountStore:
    def __init__(
        self, conn: psycopg.Connection[Any], *, lock: threading.Lock | None = None
    ) -> None:
        self._conn = conn
        self._lock = lock or threading.Lock()

    def user_exists(self, user_id: UUID) -> bool:
        with self._lock:
            row = self._conn.execute("select 1 from users where id = %s", (user_id,)).fetchone()
        return row is not None

    def create_user(self, user_id: UUID) -> None:
        with self._lock:
            self._conn.execute(
                "insert into users (id) values (%s) on conflict (id) do nothing", (user_id,)
            )

    def get_profile(self, user_id: UUID) -> Profile | None:
        with self._lock:
            row = self._conn.execute(
                f"select {_PROFILE} from profiles where user_id = %s", (user_id,)
            ).fetchone()
        return None if row is None else _profile(row)

    def save_profile(
        self, user_id: UUID, *, display_name: str, location_history_opt_in: bool | None
    ) -> Profile:
        with self._lock:
            row = self._conn.execute(
                "insert into profiles (user_id, display_name, location_history_opt_in) "
                "values (%s, %s, coalesce(%s, false)) "
                "on conflict (user_id) do update set display_name = excluded.display_name, "
                "location_history_opt_in = coalesce(%s, profiles.location_history_opt_in) "
                f"returning {_PROFILE}",
                (user_id, display_name, location_history_opt_in, location_history_opt_in),
            ).fetchone()
        assert row is not None
        return _profile(row)

    def export(self, user_id: UUID) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(_EXPORT, {"id": user_id}).fetchone()
        assert row is not None
        data: dict[str, Any] = row[0]
        return data

    def delete_user(self, user_id: UUID) -> None:
        with self._lock:
            self._conn.execute("delete from users where id = %s", (user_id,))


def _profile(row: tuple[Any, ...]) -> Profile:
    return Profile(
        user_id=row[0],
        display_name=row[1],
        location_history_opt_in=row[2],
        created_at=row[3],
        updated_at=row[4],
    )
