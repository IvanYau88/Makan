"""A `VisitStore` over `going_here`, `visits`, `visit_dishes`, and `visit_tags`, using `psycopg`.

It takes an open connection and does not manage it. The connection is the backend's privileged
one, which row-level security does not bind, so every query here names the user it is for and the
caller has already checked who that is. One lock serializes use of the connection across threads,
and it can be shared with the other stores on the same connection.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from makan.models import GoingHere, TagStatus, Visit, VisitDish, VisitTag

try:
    import psycopg
except ImportError:  # pragma: no cover - only without the extra
    raise ImportError('psycopg is not installed; run: pip install -e ".[dev]"') from None

_GOING_HERE = (
    "id, user_id, place_source, place_id, place_name, planned_on, started_at, remind_at, "
    "place_address, reminded_at, visit_id, cancelled_at"
)
_VISIT = (
    "id, user_id, place_source, place_id, place_name, visited_on, party, created_at, updated_at, "
    "place_address, rating, rating_origin, description, tagged_by_user_id"
)
_DISH = (
    "id, visit_id, user_id, position, name, rating, rating_origin, tags, created_at, updated_at, "
    "comment, source_dish_id"
)
_TAG = (
    "id, visit_id, tagger_id, tagged_user_id, status, created_at, accepted_visit_id, responded_at"
)
_OPEN = "reminded_at is null and visit_id is null and cancelled_at is null"

# The visits of each tag in a request carry the same columns as the stored visit.
_VISIT_OF_TAG = ", ".join(f"v.{c.strip()}" for c in _VISIT.split(","))
_TAG_OF_REQUEST = ", ".join(f"t.{c.strip()}" for c in _TAG.split(","))

# An expression, so `PostgresAccountStore` can add it to its own export statement and the whole
# export comes from one snapshot. `to_jsonb` of a row keeps
# every column, so a column added by a later migration is exported without a change here. A request
# addressed to the person shows only what the request itself showed them: who tagged them, the
# restaurant, and the date.
VISITS_EXPORT = """
jsonb_build_object(
  'going_here', coalesce((select jsonb_agg(to_jsonb(g) order by g.started_at, g.id)
                          from going_here g where g.user_id = %(id)s), '[]'::jsonb),
  'visits', coalesce((select jsonb_agg(to_jsonb(v) order by v.created_at, v.id)
                      from visits v where v.user_id = %(id)s), '[]'::jsonb),
  'visit_dishes', coalesce((select jsonb_agg(to_jsonb(d) order by d.visit_id, d.position, d.id)
                            from visit_dishes d where d.user_id = %(id)s), '[]'::jsonb),
  'visit_tags', coalesce((select jsonb_agg(to_jsonb(t) order by t.created_at, t.id)
                          from visit_tags t where t.tagger_id = %(id)s), '[]'::jsonb),
  'tag_requests', coalesce((
    select jsonb_agg(jsonb_build_object(
      'id', t.id, 'tagger_id', t.tagger_id, 'status', t.status, 'created_at', t.created_at,
      'responded_at', t.responded_at, 'accepted_visit_id', t.accepted_visit_id,
      'place_name', v.place_name, 'place_address', v.place_address, 'visited_on', v.visited_on
    ) order by t.created_at, t.id)
    from visit_tags t join visits v on v.id = t.visit_id
    where t.tagged_user_id = %(id)s
  ), '[]'::jsonb)
)
"""


class PostgresVisitStore:
    def __init__(
        self, conn: psycopg.Connection[Any], *, lock: threading.Lock | None = None
    ) -> None:
        self._conn = conn
        self._lock = lock or threading.Lock()

    def _one(self, sql: str, args: Any) -> tuple[Any, ...] | None:
        with self._lock:
            return self._conn.execute(sql, args).fetchone()

    def _all(self, sql: str, args: Any) -> list[tuple[Any, ...]]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    # Going here

    def add_going_here(self, marker: GoingHere) -> GoingHere:
        with self._lock, self._conn.transaction():
            row = self._conn.execute(
                f"insert into going_here ({_GOING_HERE}) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                f"on conflict (user_id, place_source, place_id) where {_OPEN} do nothing "
                f"returning {_GOING_HERE}",
                (
                    marker.id,
                    marker.user_id,
                    marker.place_source,
                    marker.place_id,
                    marker.place_name,
                    marker.planned_on,
                    marker.started_at,
                    marker.remind_at,
                    marker.place_address,
                    marker.reminded_at,
                    marker.visit_id,
                    marker.cancelled_at,
                ),
            ).fetchone()
            if row is None:
                row = self._conn.execute(
                    f"select {_GOING_HERE} from going_here where user_id = %s "
                    f"and place_source = %s and place_id = %s and {_OPEN}",
                    (marker.user_id, marker.place_source, marker.place_id),
                ).fetchone()
        assert row is not None
        return _going_here(row)

    def get_going_here(self, user_id: UUID, marker_id: UUID) -> GoingHere | None:
        row = self._one(
            f"select {_GOING_HERE} from going_here where id = %s and user_id = %s",
            (marker_id, user_id),
        )
        return None if row is None else _going_here(row)

    def open_going_here(self, user_id: UUID) -> list[GoingHere]:
        rows = self._all(
            f"select {_GOING_HERE} from going_here where user_id = %s and {_OPEN} "
            "order by remind_at, id",
            (user_id,),
        )
        return [_going_here(r) for r in rows]

    def due_going_here(self, user_id: UUID, *, now: datetime, grace: timedelta) -> list[GoingHere]:
        rows = self._all(
            f"select {_GOING_HERE} from going_here where user_id = %s and {_OPEN} "
            "and remind_at <= %s and %s < remind_at + %s order by remind_at, id",
            (user_id, now, now, grace),
        )
        return [_going_here(r) for r in rows]

    def mark_reminded(self, user_id: UUID, marker_id: UUID, now: datetime) -> GoingHere | None:
        with self._lock, self._conn.transaction():
            self._conn.execute(
                "update going_here set reminded_at = %s where id = %s and user_id = %s "
                f"and {_OPEN}",
                (now, marker_id, user_id),
            )
        return self.get_going_here(user_id, marker_id)

    def cancel_going_here(self, user_id: UUID, marker_id: UUID, now: datetime) -> GoingHere | None:
        with self._lock, self._conn.transaction():
            self._conn.execute(
                "update going_here set cancelled_at = %s where id = %s and user_id = %s "
                "and visit_id is null and cancelled_at is null",
                (now, marker_id, user_id),
            )
        return self.get_going_here(user_id, marker_id)

    # Visits

    def add_visit(
        self, visit: Visit, dishes: list[VisitDish], *, going_here_id: UUID | None = None
    ) -> None:
        with self._lock, self._conn.transaction():
            self._conn.execute(
                f"insert into visits ({_VISIT}) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                _visit_args(visit),
            )
            for dish in dishes:
                self._insert_dish(dish)
            self._conn.execute(
                "update going_here set visit_id = %s where user_id = %s and visit_id is null "
                "and (id = %s or (place_source = %s and place_id = %s "
                "and reminded_at is null and cancelled_at is null))",
                (visit.id, visit.user_id, going_here_id, visit.place_source, visit.place_id),
            )

    def _insert_dish(self, dish: VisitDish) -> None:
        self._conn.execute(
            f"insert into visit_dishes ({_DISH}) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s::text[], %s, %s, %s, %s) "
            "on conflict (id) do update set position = excluded.position, name = excluded.name, "
            "rating = excluded.rating, rating_origin = excluded.rating_origin, "
            "tags = excluded.tags, comment = excluded.comment, "
            "source_dish_id = excluded.source_dish_id "
            "where visit_dishes.visit_id = excluded.visit_id "
            "and visit_dishes.user_id = excluded.user_id",
            (
                dish.id,
                dish.visit_id,
                dish.user_id,
                dish.position,
                dish.name,
                dish.rating,
                dish.rating_origin,
                list(dish.tags),
                dish.created_at,
                dish.updated_at,
                dish.comment,
                dish.source_dish_id,
            ),
        )

    def get_visit(self, user_id: UUID, visit_id: UUID) -> Visit | None:
        row = self._one(
            f"select {_VISIT} from visits where id = %s and user_id = %s", (visit_id, user_id)
        )
        return None if row is None else _visit(row)

    def get_visit_of_anyone(self, visit_id: UUID) -> Visit | None:
        row = self._one(f"select {_VISIT} from visits where id = %s", (visit_id,))
        return None if row is None else _visit(row)

    def list_visits(self, user_id: UUID) -> list[Visit]:
        rows = self._all(
            f"select {_VISIT} from visits where user_id = %s "
            "order by visited_on desc, created_at desc, id desc",
            (user_id,),
        )
        return [_visit(r) for r in rows]

    def dishes_of(self, visit_id: UUID) -> list[VisitDish]:
        rows = self._all(
            f"select {_DISH} from visit_dishes where visit_id = %s "
            "order by position, created_at, id",
            (visit_id,),
        )
        return [_dish(r) for r in rows]

    def dish_counts(self, user_id: UUID) -> dict[UUID, int]:
        rows = self._all(
            "select visit_id, count(*) from visit_dishes where user_id = %s group by visit_id",
            (user_id,),
        )
        return {visit_id: int(n) for visit_id, n in rows}

    def save_visit(self, visit: Visit, dishes: list[VisitDish]) -> None:
        with self._lock, self._conn.transaction():
            updated = self._conn.execute(
                "update visits set place_source = %s, place_id = %s, place_name = %s, "
                "place_address = %s, visited_on = %s, party = %s, rating = %s, "
                "rating_origin = %s, description = %s where id = %s and user_id = %s",
                (
                    visit.place_source,
                    visit.place_id,
                    visit.place_name,
                    visit.place_address,
                    visit.visited_on,
                    visit.party,
                    visit.rating,
                    visit.rating_origin,
                    visit.description,
                    visit.id,
                    visit.user_id,
                ),
            ).rowcount
            if updated != 1:
                raise ValueError("no such visit")
            self._conn.execute(
                "delete from visit_dishes where visit_id = %s and id <> all(%s)",
                (visit.id, [d.id for d in dishes]),
            )
            for dish in dishes:
                self._insert_dish(dish)

    def delete_visit(self, user_id: UUID, visit_id: UUID) -> bool:
        with self._lock, self._conn.transaction():
            return (
                self._conn.execute(
                    "delete from visits where id = %s and user_id = %s", (visit_id, user_id)
                ).rowcount
                == 1
            )

    # Tags

    def add_tag(self, tag: VisitTag) -> VisitTag:
        with self._lock, self._conn.transaction():
            row = self._conn.execute(
                f"insert into visit_tags ({_TAG}) values (%s, %s, %s, %s, %s, %s, %s, %s) "
                "on conflict (visit_id, tagged_user_id) do nothing "
                f"returning {_TAG}",
                (
                    tag.id,
                    tag.visit_id,
                    tag.tagger_id,
                    tag.tagged_user_id,
                    tag.status,
                    tag.created_at,
                    tag.accepted_visit_id,
                    tag.responded_at,
                ),
            ).fetchone()
            if row is None:
                row = self._conn.execute(
                    f"select {_TAG} from visit_tags where visit_id = %s and tagged_user_id = %s",
                    (tag.visit_id, tag.tagged_user_id),
                ).fetchone()
        assert row is not None
        return _tag(row)

    def get_tag(self, tag_id: UUID) -> VisitTag | None:
        row = self._one(f"select {_TAG} from visit_tags where id = %s", (tag_id,))
        return None if row is None else _tag(row)

    def tags_of_visit(self, visit_id: UUID) -> list[VisitTag]:
        rows = self._all(
            f"select {_TAG} from visit_tags where visit_id = %s order by created_at, id",
            (visit_id,),
        )
        return [_tag(r) for r in rows]

    def tags_by_user(self, tagger_id: UUID) -> list[VisitTag]:
        rows = self._all(
            f"select {_TAG} from visit_tags where tagger_id = %s order by created_at, id",
            (tagger_id,),
        )
        return [_tag(r) for r in rows]

    def tag_accepted_into(self, visit_id: UUID) -> VisitTag | None:
        row = self._one(
            f"select {_TAG} from visit_tags where accepted_visit_id = %s and status = 'accepted'",
            (visit_id,),
        )
        return None if row is None else _tag(row)

    def requests_for(
        self, user_id: UUID, status: TagStatus | None = None
    ) -> list[tuple[VisitTag, Visit]]:
        rows = self._all(
            f"select {_TAG_OF_REQUEST}, {_VISIT_OF_TAG} from visit_tags t "
            "join visits v on v.id = t.visit_id "
            "where t.tagged_user_id = %s and (%s::text is null or t.status = %s) "
            "order by t.created_at desc, t.id desc",
            (user_id, status, status),
        )
        width = len(_TAG.split(","))
        return [(_tag(r[:width]), _visit(r[width:])) for r in rows]

    def delete_pending_tag(self, tag_id: UUID, tagger_id: UUID) -> bool:
        with self._lock, self._conn.transaction():
            return (
                self._conn.execute(
                    "delete from visit_tags where id = %s and tagger_id = %s "
                    "and status = 'pending'",
                    (tag_id, tagger_id),
                ).rowcount
                == 1
            )

    def answer_tag(
        self,
        tag_id: UUID,
        tagged_user_id: UUID,
        *,
        accept: bool,
        now: datetime,
        tagged_visit: Visit | None,
    ) -> VisitTag | None:
        with self._lock, self._conn.transaction():
            row = self._conn.execute(
                f"select {_TAG} from visit_tags where id = %s and tagged_user_id = %s for update",
                (tag_id, tagged_user_id),
            ).fetchone()
            if row is None:
                return None
            tag = _tag(row)
            if tag.status != "pending":
                return tag
            if accept:
                assert tagged_visit is not None and tagged_visit.user_id == tagged_user_id
                self._conn.execute(
                    f"insert into visits ({_VISIT}) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    _visit_args(tagged_visit),
                )
            done = self._conn.execute(
                "update visit_tags set status = %s, responded_at = %s, accepted_visit_id = %s "
                f"where id = %s returning {_TAG}",
                (
                    "accepted" if accept else "declined",
                    now,
                    tagged_visit.id if accept and tagged_visit else None,
                    tag_id,
                ),
            ).fetchone()
        assert done is not None
        return _tag(done)

    # Data rights

    def export(self, user_id: UUID) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(f"select {VISITS_EXPORT}", {"id": user_id}).fetchone()
        assert row is not None
        data: dict[str, Any] = row[0]
        return data

    def delete_user(self, user_id: UUID) -> None:
        """Deleting the `users` row cascades to every table here, so there is nothing to do."""


def _visit_args(v: Visit) -> tuple[Any, ...]:
    return (
        v.id,
        v.user_id,
        v.place_source,
        v.place_id,
        v.place_name,
        v.visited_on,
        v.party,
        v.created_at,
        v.updated_at,
        v.place_address,
        v.rating,
        v.rating_origin,
        v.description,
        v.tagged_by_user_id,
    )


def _going_here(row: tuple[Any, ...]) -> GoingHere:
    return GoingHere(*row)


def _visit(row: tuple[Any, ...]) -> Visit:
    return Visit(*row)


def _dish(row: tuple[Any, ...]) -> VisitDish:
    (id_, visit_id, user_id, position, name, rating, origin, tags, created, updated, *rest) = row
    return VisitDish(
        id_,
        visit_id,
        user_id,
        position,
        name,
        rating,
        origin,
        tuple(tags),
        created,
        updated,
        comment=rest[0],
        source_dish_id=rest[1],
    )


def _tag(row: tuple[Any, ...]) -> VisitTag:
    return VisitTag(*row)
