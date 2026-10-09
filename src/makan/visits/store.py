"""Where visits live. `InMemoryVisitStore` is for tests, `makan.visits.postgres` is the real one.

A store only keeps and finds rows, and the few changes that must happen together are one call so
they are atomic. Who may do what is in `makan.visits.service`. Every method that takes a
`user_id` finds only that user's rows, so another person's id is simply not found.

The in-memory store mirrors the foreign keys of `migrations/0004_visit_logging.sql`: deleting a
visit removes its dishes and tags, and clears the links that pointed at it instead of removing the
rows that held them.
"""

from __future__ import annotations

import dataclasses
import threading
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from makan.models import GoingHere, TagStatus, Visit, VisitDish, VisitTag


class VisitStore(Protocol):
    # Going here

    def add_going_here(self, marker: GoingHere) -> GoingHere:
        """Keep the marker, or return the user's open marker for the same place if there is one."""
        ...

    def get_going_here(self, user_id: UUID, marker_id: UUID) -> GoingHere | None: ...

    def open_going_here(self, user_id: UUID) -> list[GoingHere]:
        """Markers with no reminder sent, no visit, and no cancellation, soonest first."""
        ...

    def due_going_here(self, user_id: UUID, *, now: datetime, grace: timedelta) -> list[GoingHere]:
        """Open markers whose window ended no more than `grace` ago, oldest first."""
        ...

    def mark_reminded(self, user_id: UUID, marker_id: UUID, now: datetime) -> GoingHere | None:
        """Record the one reminder on an open marker. Any other marker comes back as it was."""
        ...

    def cancel_going_here(self, user_id: UUID, marker_id: UUID, now: datetime) -> GoingHere | None:
        """Cancel a marker that has no visit. A marker that has one comes back as it was."""
        ...

    # Visits

    def add_visit(
        self, visit: Visit, dishes: list[VisitDish], *, going_here_id: UUID | None = None
    ) -> None:
        """Keep a visit and its dishes together, and close the markers it answers.

        That is the marker `going_here_id` names, and any other open marker of the same user at
        the same place, so a visit logged by hand ends the reminder for the same meal.
        """
        ...

    def get_visit(self, user_id: UUID, visit_id: UUID) -> Visit | None: ...

    def get_visit_of_anyone(self, visit_id: UUID) -> Visit | None:
        """For code that already proved the caller may read it, such as an accepted tag."""
        ...

    def list_visits(self, user_id: UUID) -> list[Visit]:
        """Newest meal first."""
        ...

    def dishes_of(self, visit_id: UUID) -> list[VisitDish]:
        """In the order the person gave them."""
        ...

    def dish_counts(self, user_id: UUID) -> dict[UUID, int]: ...

    def save_visit(self, visit: Visit, dishes: list[VisitDish]) -> None:
        """Replace the visit row and make its dishes exactly `dishes`, in one step.

        A dish whose id is already stored is updated, a new id is added, and a stored dish that
        is not in the list is removed.
        """
        ...

    def delete_visit(self, user_id: UUID, visit_id: UUID) -> bool:
        """Delete the visit with its dishes and tags. Others' visits made from it are untouched."""
        ...

    # Tags

    def add_tag(self, tag: VisitTag) -> VisitTag:
        """Keep the tag, or return the one already stored for the same visit and person."""
        ...

    def get_tag(self, tag_id: UUID) -> VisitTag | None: ...

    def tags_of_visit(self, visit_id: UUID) -> list[VisitTag]: ...

    def tags_by_user(self, tagger_id: UUID) -> list[VisitTag]:
        """Every tag the user made, for showing a whole history at once."""
        ...

    def tag_accepted_into(self, visit_id: UUID) -> VisitTag | None:
        """The accepted tag whose tagged person's visit is `visit_id`."""
        ...

    def requests_for(
        self, user_id: UUID, status: TagStatus | None = None
    ) -> list[tuple[VisitTag, Visit]]:
        """Tags addressed to the user with the visit each is about, newest tag first."""
        ...

    def delete_pending_tag(self, tag_id: UUID, tagger_id: UUID) -> bool:
        """Delete a tag only while it is pending. Returns whether one was deleted."""
        ...

    def answer_tag(
        self,
        tag_id: UUID,
        tagged_user_id: UUID,
        *,
        accept: bool,
        now: datetime,
        tagged_visit: Visit | None,
    ) -> VisitTag | None:
        """Move a pending tag to accepted or declined, once.

        Accepting also keeps `tagged_visit`, which is the tagged person's own visit, and points
        the tag at it. A tag that is no longer pending comes back as it is and nothing is kept,
        which is what makes answering twice harmless. A tag that does not exist, or that is
        addressed to someone else, is None.
        """
        ...

    # Data rights

    def export(self, user_id: UUID) -> dict[str, Any]:
        """Every row the user owns, plus the requests addressed to them, as JSON-ready data."""
        ...

    def delete_user(self, user_id: UUID) -> None:
        """What deleting the user does to these tables. The Postgres store leaves it to cascade."""
        ...


class InMemoryVisitStore:
    def __init__(self) -> None:
        self._going_here: dict[UUID, GoingHere] = {}
        self._visits: dict[UUID, Visit] = {}
        self._dishes: dict[UUID, VisitDish] = {}
        self._tags: dict[UUID, VisitTag] = {}
        self._lock = threading.RLock()

    # Going here

    @staticmethod
    def _is_open(marker: GoingHere) -> bool:
        return (
            marker.reminded_at is None and marker.visit_id is None and marker.cancelled_at is None
        )

    def add_going_here(self, marker: GoingHere) -> GoingHere:
        with self._lock:
            for other in self._going_here.values():
                if self._is_open(other) and (other.user_id, other.place_source, other.place_id) == (
                    marker.user_id,
                    marker.place_source,
                    marker.place_id,
                ):
                    return other
            self._going_here[marker.id] = marker
            return marker

    def get_going_here(self, user_id: UUID, marker_id: UUID) -> GoingHere | None:
        with self._lock:
            marker = self._going_here.get(marker_id)
            return marker if marker and marker.user_id == user_id else None

    def open_going_here(self, user_id: UUID) -> list[GoingHere]:
        with self._lock:
            found = [
                m for m in self._going_here.values() if m.user_id == user_id and self._is_open(m)
            ]
        return sorted(found, key=lambda m: (m.remind_at, m.id))

    def due_going_here(self, user_id: UUID, *, now: datetime, grace: timedelta) -> list[GoingHere]:
        return [
            m for m in self.open_going_here(user_id) if m.remind_at <= now < m.remind_at + grace
        ]

    def mark_reminded(self, user_id: UUID, marker_id: UUID, now: datetime) -> GoingHere | None:
        with self._lock:
            marker = self.get_going_here(user_id, marker_id)
            if marker is None or not self._is_open(marker):
                return marker
            updated = dataclasses.replace(marker, reminded_at=now)
            self._going_here[marker_id] = updated
            return updated

    def cancel_going_here(self, user_id: UUID, marker_id: UUID, now: datetime) -> GoingHere | None:
        with self._lock:
            marker = self.get_going_here(user_id, marker_id)
            if marker is None or marker.visit_id is not None or marker.cancelled_at is not None:
                return marker
            updated = dataclasses.replace(marker, cancelled_at=now)
            self._going_here[marker_id] = updated
            return updated

    # Visits

    def add_visit(
        self, visit: Visit, dishes: list[VisitDish], *, going_here_id: UUID | None = None
    ) -> None:
        with self._lock:
            self._visits[visit.id] = visit
            for dish in dishes:
                self._dishes[dish.id] = dish
            for marker in list(self._going_here.values()):
                same_place = (marker.user_id, marker.place_source, marker.place_id) == (
                    visit.user_id,
                    visit.place_source,
                    visit.place_id,
                )
                answers = marker.id == going_here_id or (same_place and self._is_open(marker))
                if answers and marker.visit_id is None:
                    self._going_here[marker.id] = dataclasses.replace(marker, visit_id=visit.id)

    def get_visit(self, user_id: UUID, visit_id: UUID) -> Visit | None:
        with self._lock:
            visit = self._visits.get(visit_id)
            return visit if visit and visit.user_id == user_id else None

    def get_visit_of_anyone(self, visit_id: UUID) -> Visit | None:
        with self._lock:
            return self._visits.get(visit_id)

    def list_visits(self, user_id: UUID) -> list[Visit]:
        with self._lock:
            found = [v for v in self._visits.values() if v.user_id == user_id]
        return sorted(found, key=lambda v: (v.visited_on, v.created_at, v.id), reverse=True)

    def dishes_of(self, visit_id: UUID) -> list[VisitDish]:
        with self._lock:
            found = [d for d in self._dishes.values() if d.visit_id == visit_id]
        return sorted(found, key=lambda d: (d.position, d.created_at, d.id))

    def dish_counts(self, user_id: UUID) -> dict[UUID, int]:
        counts: dict[UUID, int] = {}
        with self._lock:
            for dish in self._dishes.values():
                if dish.user_id == user_id:
                    counts[dish.visit_id] = counts.get(dish.visit_id, 0) + 1
        return counts

    def save_visit(self, visit: Visit, dishes: list[VisitDish]) -> None:
        with self._lock:
            if visit.id not in self._visits:
                raise ValueError("no such visit")
            self._visits[visit.id] = visit
            keep = {d.id for d in dishes}
            for dish in self.dishes_of(visit.id):
                if dish.id not in keep:
                    self._drop_dish(dish.id)
            for dish in dishes:
                self._dishes[dish.id] = dish

    def _drop_dish(self, dish_id: UUID) -> None:
        self._dishes.pop(dish_id, None)
        for other in list(self._dishes.values()):
            if other.source_dish_id == dish_id:
                self._dishes[other.id] = dataclasses.replace(other, source_dish_id=None)

    def delete_visit(self, user_id: UUID, visit_id: UUID) -> bool:
        with self._lock:
            if self.get_visit(user_id, visit_id) is None:
                return False
            self._remove_visit(visit_id)
            return True

    def _remove_visit(self, visit_id: UUID) -> None:
        del self._visits[visit_id]
        for dish in self.dishes_of(visit_id):
            self._drop_dish(dish.id)
        for tag in list(self._tags.values()):
            if tag.visit_id == visit_id:
                del self._tags[tag.id]
            elif tag.accepted_visit_id == visit_id:
                self._tags[tag.id] = dataclasses.replace(tag, accepted_visit_id=None)
        for marker in list(self._going_here.values()):
            if marker.visit_id == visit_id:
                self._going_here[marker.id] = dataclasses.replace(marker, visit_id=None)

    # Tags

    def add_tag(self, tag: VisitTag) -> VisitTag:
        with self._lock:
            for other in self._tags.values():
                if (other.visit_id, other.tagged_user_id) == (tag.visit_id, tag.tagged_user_id):
                    return other
            if tag.visit_id not in self._visits:
                raise ValueError("no such visit")
            self._tags[tag.id] = tag
            return tag

    def get_tag(self, tag_id: UUID) -> VisitTag | None:
        with self._lock:
            return self._tags.get(tag_id)

    def tags_of_visit(self, visit_id: UUID) -> list[VisitTag]:
        with self._lock:
            found = [t for t in self._tags.values() if t.visit_id == visit_id]
        return sorted(found, key=lambda t: (t.created_at, t.id))

    def tags_by_user(self, tagger_id: UUID) -> list[VisitTag]:
        with self._lock:
            found = [t for t in self._tags.values() if t.tagger_id == tagger_id]
        return sorted(found, key=lambda t: (t.created_at, t.id))

    def tag_accepted_into(self, visit_id: UUID) -> VisitTag | None:
        with self._lock:
            for tag in self._tags.values():
                if tag.accepted_visit_id == visit_id and tag.status == "accepted":
                    return tag
        return None

    def requests_for(
        self, user_id: UUID, status: TagStatus | None = None
    ) -> list[tuple[VisitTag, Visit]]:
        with self._lock:
            found = [
                (t, self._visits[t.visit_id])
                for t in self._tags.values()
                if t.tagged_user_id == user_id and (status is None or t.status == status)
            ]
        return sorted(found, key=lambda pair: (pair[0].created_at, pair[0].id), reverse=True)

    def delete_pending_tag(self, tag_id: UUID, tagger_id: UUID) -> bool:
        with self._lock:
            tag = self._tags.get(tag_id)
            if tag is None or tag.tagger_id != tagger_id or tag.status != "pending":
                return False
            del self._tags[tag_id]
            return True

    def answer_tag(
        self,
        tag_id: UUID,
        tagged_user_id: UUID,
        *,
        accept: bool,
        now: datetime,
        tagged_visit: Visit | None,
    ) -> VisitTag | None:
        with self._lock:
            tag = self._tags.get(tag_id)
            if tag is None or tag.tagged_user_id != tagged_user_id:
                return None
            if tag.status != "pending":
                return tag
            if accept:
                assert tagged_visit is not None and tagged_visit.user_id == tagged_user_id
                self._visits[tagged_visit.id] = tagged_visit
            updated = dataclasses.replace(
                tag,
                status="accepted" if accept else "declined",
                responded_at=now,
                accepted_visit_id=tagged_visit.id if accept and tagged_visit else None,
            )
            self._tags[tag_id] = updated
            return updated

    # Data rights

    def export(self, user_id: UUID) -> dict[str, Any]:
        with self._lock:
            mine = {v.id for v in self._visits.values() if v.user_id == user_id}
            return {
                "going_here": [
                    dataclasses.asdict(m)
                    for m in sorted(self._going_here.values(), key=lambda m: (m.started_at, m.id))
                    if m.user_id == user_id
                ],
                "visits": [
                    dataclasses.asdict(v)
                    for v in sorted(self._visits.values(), key=lambda v: (v.created_at, v.id))
                    if v.user_id == user_id
                ],
                "visit_dishes": [
                    dataclasses.asdict(d)
                    for visit_id in sorted(mine)
                    for d in self.dishes_of(visit_id)
                ],
                "visit_tags": [
                    dataclasses.asdict(t)
                    for t in sorted(self._tags.values(), key=lambda t: (t.created_at, t.id))
                    if t.tagger_id == user_id
                ],
                "tag_requests": [
                    {
                        "id": tag.id,
                        "tagger_id": tag.tagger_id,
                        "status": tag.status,
                        "created_at": tag.created_at,
                        "responded_at": tag.responded_at,
                        "accepted_visit_id": tag.accepted_visit_id,
                        "place_name": visit.place_name,
                        "place_address": visit.place_address,
                        "visited_on": visit.visited_on,
                    }
                    for tag, visit in reversed(self.requests_for(user_id))
                ],
            }

    def delete_user(self, user_id: UUID) -> None:
        with self._lock:
            for tag in list(self._tags.values()):
                if tag.tagged_user_id == user_id:
                    del self._tags[tag.id]
            for visit in list(self._visits.values()):
                if visit.user_id == user_id and visit.id in self._visits:
                    self._remove_visit(visit.id)
            for marker in list(self._going_here.values()):
                if marker.user_id == user_id:
                    del self._going_here[marker.id]
            for visit in list(self._visits.values()):
                if visit.tagged_by_user_id == user_id:
                    self._visits[visit.id] = dataclasses.replace(visit, tagged_by_user_id=None)
