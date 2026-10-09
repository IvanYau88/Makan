"""The rules of visit logging, on top of any `VisitStore`.

- Visits are for signed-in people. Every call takes the user id of the person it is for, and
  `SignInRequired` is raised for anything else, so no code path can make or read a guest's row.
- Every row is looked up by its owner, so another person's visit, dish, marker, or tag is
  `NotFound`, the same as one that never existed.
- A companion tag is a request, and a pending or declined one counts as nothing: it shares nothing,
  and it is not a visit with that person. It moves once, to accepted or declined, and answering the
  same way again changes nothing.
- Accepting makes a visit that belongs to the tagged person. They see the tagger's visit through
  the tag, with the same restaurant, rating, dishes, and notes, and each rating becomes theirs only
  when they confirm or change it. The tagger's ratings never count for them, and theirs never count
  for the tagger. Nothing they confirm is a copy that follows the tagger, and deleting the tagger's
  visit leaves it as it was.
- A rating is `fresh` when the person entered the value, and `confirmed` when they accepted the
  tagger's value as it was.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID, uuid4

from makan.memory.service import utc_now
from makan.models import (
    PARTIES,
    GoingHere,
    Profile,
    RatingOrigin,
    TagStatus,
    Visit,
    VisitDish,
    VisitTag,
)
from makan.visits.content import (
    MAX_COMPANIONS,
    MAX_DISH_COMMENT_CHARS,
    MAX_DISHES,
    PlaceRef,
    check_visit_date,
    clean_description,
    clean_dish_name,
    clean_dish_tags,
    clean_optional,
    parse_rating,
)
from makan.visits.errors import Conflict, InvalidVisit, NotFound, SignInRequired
from makan.visits.store import VisitStore

GOING_HERE_WINDOW = timedelta(hours=48)
# A reminder is dropped for good once it is this long overdue, so one that was never sent (the
# sender was down, or the person was away) is not sent weeks late.
REMINDER_GRACE = timedelta(days=7)

TAG_SHARING_NOTICE = (
    "The people you tag will see your ratings, dishes and notes for this visit if they accept."
)

# The tagged person sees the same view as the tagger, so the free-text description is shown to them
# after they accept. Set this to False to keep it private to the tagger. Nothing else changes.
SHARE_TAGGER_DESCRIPTION = True


class _Unset:
    """Stands for a field a request did not mention, which is different from one it set to null."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()


class People(Protocol):
    """What the visit rules need to know about other accounts. `AccountStore` has both."""

    def user_exists(self, user_id: UUID) -> bool: ...

    def get_profile(self, user_id: UUID) -> Profile | None: ...


@dataclass(frozen=True)
class DishInput:
    """A dish as typed. `id` names an existing dish of the visit being edited."""

    name: str
    rating: object
    tags: Sequence[str] = ()
    comment: str | None = None
    id: UUID | None = None


@dataclass(frozen=True)
class RatingAnswer:
    """The tagged person's answer about the restaurant: the same rating, or their own."""

    action: Literal["same", "change"]
    value: object = None


@dataclass(frozen=True)
class DishAnswer:
    """The tagged person's answer about one of the tagger's dishes.

    `same` has them confirm they had it with the tagger's rating, `change` keeps their own
    rating for it, and `skip` says they did not have it. Something different is a dish of their
    own, added by editing the visit.
    """

    source_dish_id: UUID
    action: Literal["same", "change", "skip"]
    rating: object = None
    tags: Sequence[str] | _Unset = UNSET
    comment: str | _Unset | None = UNSET


@dataclass(frozen=True)
class TagView:
    tag: VisitTag
    name: str | None  # the other person's display name, when they have one


@dataclass(frozen=True)
class SharedDish:
    dish: VisitDish
    answered_by: UUID | None  # the tagged person's dish that answers it


@dataclass(frozen=True)
class SharedView:
    """What an accepted tag shares: the tagger's visit as it is now, for the tagged person only."""

    tagger_name: str | None
    rating: Decimal | None
    description: str | None
    dishes: tuple[SharedDish, ...]


@dataclass(frozen=True)
class VisitView:
    visit: Visit
    dishes: tuple[VisitDish, ...]
    tags: tuple[TagView, ...]  # tags the owner made, with every state
    tagged_by_name: str | None
    shared: SharedView | None  # set only on a visit made from an accepted tag whose source remains


@dataclass(frozen=True)
class VisitSummary:
    visit: Visit
    dish_count: int
    companions: tuple[str | None, ...]  # accepted tags only, as names
    tagged_by_name: str | None


@dataclass(frozen=True)
class TagRequest:
    """What a tag request shows the person it is addressed to: the restaurant, the date, and who."""

    tag: VisitTag
    place_name: str
    place_address: str | None
    visited_on: date
    tagger_name: str | None


@dataclass(frozen=True)
class Answered:
    request: TagRequest
    visit: VisitView | None  # their visit, once accepted and while it still exists


class Visits:
    def __init__(
        self,
        store: VisitStore,
        people: People,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store = store
        self.people = people
        self._clock = clock

    # Going here

    def going_here(
        self, user_id: UUID | None, place: PlaceRef, planned_on: date | None = None
    ) -> GoingHere:
        """Mark a place as where the person is going, which starts the reminder window.

        Marking a place that already has an open marker returns that marker and does not restart
        the window.
        """
        user = _signed_in(user_id)
        now = self._clock()
        today = now.astimezone(UTC).date()
        day = check_visit_date(planned_on or today, today)
        return self.store.add_going_here(
            GoingHere(
                id=uuid4(),
                user_id=user,
                place_source=place.source,
                place_id=place.id,
                place_name=place.name,
                place_address=place.address,
                planned_on=day,
                started_at=now,
                remind_at=now + GOING_HERE_WINDOW,
            )
        )

    def open_going_here(self, user_id: UUID | None) -> list[GoingHere]:
        return self.store.open_going_here(_signed_in(user_id))

    def due_reminders(self, user_id: UUID | None) -> list[GoingHere]:
        """The markers whose window has ended and that have not been reminded. Sends nothing."""
        return self.store.due_going_here(
            _signed_in(user_id), now=self._clock(), grace=REMINDER_GRACE
        )

    def mark_reminded(self, user_id: UUID | None, marker_id: UUID) -> GoingHere:
        """Record that the one reminder was sent. After this the marker is not due again."""
        marker = self.store.mark_reminded(_signed_in(user_id), marker_id, self._clock())
        if marker is None:
            raise NotFound("No such place was marked.")
        return marker

    def cancel_going_here(self, user_id: UUID | None, marker_id: UUID) -> GoingHere:
        marker = self.store.cancel_going_here(_signed_in(user_id), marker_id, self._clock())
        if marker is None:
            raise NotFound("No such place was marked.")
        if marker.cancelled_at is None:
            raise Conflict("A visit was already logged for this place.")
        return marker

    # Visits

    def log_visit(
        self,
        user_id: UUID | None,
        *,
        place: PlaceRef,
        rating: object,
        party: str,
        visited_on: date | None = None,
        description: str | None = None,
        dishes: Sequence[DishInput] = (),
        companions: Sequence[UUID] = (),
        acknowledged_sharing: bool = False,
        going_here_id: UUID | None = None,
    ) -> VisitView:
        """Record a meal. Everything is checked before anything is stored."""
        user = _signed_in(user_id)
        now = self._clock()
        score = parse_rating(rating, "restaurant rating")
        party_ = _party(party)
        companion_ids = self._check_companions(user, party_, companions, acknowledged_sharing)
        marker = None
        if going_here_id is not None:
            marker = self.store.get_going_here(user, going_here_id)
            if marker is None:
                raise NotFound("No such place was marked.")
            if marker.visit_id is not None or marker.cancelled_at is not None:
                raise Conflict("That place is no longer marked as somewhere you are going.")
            if (marker.place_source, marker.place_id) != (place.source, place.id):
                raise InvalidVisit("This visit is for a different place than the one you marked.")
        default_day = marker.planned_on if marker else now.astimezone(UTC).date()
        day = check_visit_date(visited_on or default_day, now.astimezone(UTC).date())
        visit_id = uuid4()
        visit = Visit(
            id=visit_id,
            user_id=user,
            place_source=place.source,
            place_id=place.id,
            place_name=place.name,
            place_address=place.address,
            visited_on=day,
            party=party_,
            rating=score,
            rating_origin="fresh",
            description=clean_description(description),
            created_at=now,
            updated_at=now,
        )
        rows = _dish_rows(visit_id, user, dishes, {}, now)
        self.store.add_visit(visit, rows, going_here_id=going_here_id)
        for companion in companion_ids:
            self.store.add_tag(
                VisitTag(
                    id=uuid4(),
                    visit_id=visit_id,
                    tagger_id=user,
                    tagged_user_id=companion,
                    status="pending",
                    created_at=now,
                )
            )
        return self._view(visit)

    def visit(self, user_id: UUID | None, visit_id: UUID) -> VisitView:
        return self._view(self._own_visit(_signed_in(user_id), visit_id))

    def history(self, user_id: UUID | None) -> list[VisitSummary]:
        """Every visit the person has, newest meal first. A visit with no dishes is a visit."""
        user = _signed_in(user_id)
        visits = self.store.list_visits(user)
        counts = self.store.dish_counts(user)
        accepted: dict[UUID, list[UUID]] = {}
        for tag in self.store.tags_by_user(user):
            if tag.status == "accepted":
                accepted.setdefault(tag.visit_id, []).append(tag.tagged_user_id)
        return [
            VisitSummary(
                visit=v,
                dish_count=counts.get(v.id, 0),
                companions=tuple(self._name(c) for c in accepted.get(v.id, ())),
                tagged_by_name=self._name(v.tagged_by_user_id),
            )
            for v in visits
        ]

    def edit_visit(
        self,
        user_id: UUID | None,
        visit_id: UUID,
        *,
        place: PlaceRef | _Unset = UNSET,
        rating: object = UNSET,
        party: str | _Unset = UNSET,
        visited_on: date | _Unset = UNSET,
        description: str | _Unset | None = UNSET,
        dishes: Sequence[DishInput] | _Unset = UNSET,
    ) -> VisitView:
        """Change the fields given. A list of dishes replaces the whole list, keeping the dishes
        whose id it names and removing the ones it leaves out."""
        user = _signed_in(user_id)
        now = self._clock()
        visit = self._own_visit(user, visit_id)
        changes: dict[str, object] = {}
        if not isinstance(place, _Unset):
            moved = (place.source, place.id) != (visit.place_source, visit.place_id)
            if visit.tagged_by_user_id is not None and moved:
                raise Conflict("A visit you were tagged in keeps its restaurant.")
            changes |= {
                "place_source": place.source,
                "place_id": place.id,
                "place_name": place.name,
                "place_address": place.address,
            }
        if not isinstance(rating, _Unset):
            if rating is None:
                if visit.rating is not None:
                    raise InvalidVisit("Rate the restaurant from 0 to 10.")
            else:
                score = parse_rating(rating, "restaurant rating")
                keep = score == visit.rating and visit.rating_origin is not None
                origin: RatingOrigin = (
                    visit.rating_origin if keep and visit.rating_origin else "fresh"
                )
                changes |= {"rating": score, "rating_origin": origin}
        if not isinstance(party, _Unset):
            party_ = _party(party)
            if party_ != visit.party:
                if visit.tagged_by_user_id is not None:
                    raise Conflict(
                        "A visit you were tagged in was shared, so it stays with others."
                    )
                if party_ == "solo" and any(
                    t.status != "declined" for t in self.store.tags_of_visit(visit.id)
                ):
                    raise Conflict(
                        "This visit has people tagged on it. Withdraw the requests that are "
                        "waiting before marking it solo."
                    )
            changes["party"] = party_
        if not isinstance(visited_on, _Unset):
            changes["visited_on"] = check_visit_date(visited_on, now.astimezone(UTC).date())
        if not isinstance(description, _Unset):
            changes["description"] = clean_description(description)
        saved = dataclasses.replace(visit, updated_at=now, **changes)  # type: ignore[arg-type]
        if isinstance(dishes, _Unset):
            rows = self.store.dishes_of(visit.id)
        else:
            existing = {d.id: d for d in self.store.dishes_of(visit.id)}
            rows = _dish_rows(visit.id, user, dishes, existing, now)
        self.store.save_visit(saved, rows)
        return self._view(saved)

    def delete_visit(self, user_id: UUID | None, visit_id: UUID) -> None:
        """Delete the visit, its dishes, and the tags on it.

        A visit someone else made from one of its tags is theirs, and stays.
        """
        if not self.store.delete_visit(_signed_in(user_id), visit_id):
            raise NotFound("No such visit.")

    # Companion tags

    def tag(
        self,
        user_id: UUID | None,
        visit_id: UUID,
        tagged_user_id: UUID,
        *,
        acknowledged_sharing: bool,
    ) -> TagView:
        """Ask a Makan user to confirm they ate with the person. Asking again returns the request
        as it stands, so a declined request is not sent a second time."""
        user = _signed_in(user_id)
        visit = self._own_visit(user, visit_id)
        (companion,) = self._check_companions(
            user, visit.party, [tagged_user_id], acknowledged_sharing
        )
        existing = self.store.tags_of_visit(visit.id)
        if len(existing) >= MAX_COMPANIONS and all(t.tagged_user_id != companion for t in existing):
            raise InvalidVisit(f"Tag {MAX_COMPANIONS} people or fewer on a visit.")
        tag = self.store.add_tag(
            VisitTag(
                id=uuid4(),
                visit_id=visit.id,
                tagger_id=user,
                tagged_user_id=companion,
                status="pending",
                created_at=self._clock(),
            )
        )
        return TagView(tag, self._name(companion))

    def withdraw_tag(self, user_id: UUID | None, visit_id: UUID, tag_id: UUID) -> None:
        """Take back a request that has not been answered."""
        user = _signed_in(user_id)
        visit = self._own_visit(user, visit_id)
        tag = self.store.get_tag(tag_id)
        if tag is None or tag.visit_id != visit.id:
            raise NotFound("No such request.")
        if not self.store.delete_pending_tag(tag_id, user):
            raise Conflict("Only a request that has not been answered can be withdrawn.")

    def tag_requests(
        self, user_id: UUID | None, status: TagStatus | None = "pending"
    ) -> list[TagRequest]:
        """Requests addressed to the person. They show the restaurant, the date, and who asked,
        and nothing the tagger rated or wrote."""
        user = _signed_in(user_id)
        return [self._request(tag, source) for tag, source in self.store.requests_for(user, status)]

    def accept_tag(self, user_id: UUID | None, tag_id: UUID) -> Answered:
        """Accept a request, which makes a visit of the person's own. Accepting again returns it."""
        return self._answer(_signed_in(user_id), tag_id, accept=True)

    def decline_tag(self, user_id: UUID | None, tag_id: UUID) -> Answered:
        return self._answer(_signed_in(user_id), tag_id, accept=False)

    def _answer(self, user: UUID, tag_id: UUID, *, accept: bool) -> Answered:
        tag = self.store.get_tag(tag_id)
        if tag is None or tag.tagged_user_id != user:
            raise NotFound("No such request.")
        wanted: TagStatus = "accepted" if accept else "declined"
        source = self.store.get_visit_of_anyone(tag.visit_id)
        if source is None:
            raise NotFound("No such request.")
        now = self._clock()
        tagged_visit = (
            Visit(
                id=uuid4(),
                user_id=user,
                place_source=source.place_source,
                place_id=source.place_id,
                place_name=source.place_name,
                place_address=source.place_address,
                visited_on=source.visited_on,
                party="with_others",
                created_at=now,
                updated_at=now,
                tagged_by_user_id=tag.tagger_id,
            )
            if accept
            else None
        )
        answered = self.store.answer_tag(
            tag_id, user, accept=accept, now=now, tagged_visit=tagged_visit
        )
        if answered is None:
            raise NotFound("No such request.")
        if answered.status != wanted:
            raise Conflict(
                f"You already {'declined' if answered.status == 'declined' else 'accepted'} "
                "this request."
            )
        view = None
        if answered.accepted_visit_id is not None:
            mine = self.store.get_visit(user, answered.accepted_visit_id)
            view = self._view(mine) if mine else None
        return Answered(self._request(answered, source), view)

    # Confirming a tagged visit

    def confirm(
        self,
        user_id: UUID | None,
        visit_id: UUID,
        *,
        rating: RatingAnswer | None = None,
        dishes: Sequence[DishAnswer] = (),
    ) -> VisitView:
        """Answer the tagger's visit: for the restaurant and for each dish, say it was the same,
        give a rating of your own, or (for a dish) that you did not have it.

        Answering again changes the answer. Everything is checked before anything is stored.
        """
        user = _signed_in(user_id)
        now = self._clock()
        visit = self._own_visit(user, visit_id)
        tag = self.store.tag_accepted_into(visit.id)
        source = self.store.get_visit_of_anyone(tag.visit_id) if tag else None
        if tag is None or tag.tagged_user_id != user or source is None:
            raise Conflict(
                "There is no tagged visit to answer here. Edit your own ratings instead."
            )
        saved = visit
        if rating is not None:
            if rating.action == "same":
                if source.rating is None:
                    raise Conflict("There is no rating to confirm. Give your own.")
                saved = dataclasses.replace(
                    visit, rating=source.rating, rating_origin=_origin("same"), updated_at=now
                )
            else:
                saved = dataclasses.replace(
                    visit,
                    rating=parse_rating(rating.value, "restaurant rating"),
                    rating_origin=_origin("change"),
                    updated_at=now,
                )
        theirs = {d.id: d for d in self.store.dishes_of(source.id)}
        mine = self.store.dishes_of(visit.id)
        answered_by = {d.source_dish_id: d for d in mine if d.source_dish_id}
        seen: set[UUID] = set()
        rows = list(mine)
        for answer in dishes:
            if answer.source_dish_id in seen:
                raise InvalidVisit("Answer each dish once.")
            seen.add(answer.source_dish_id)
            original = theirs.get(answer.source_dish_id)
            if original is None:
                raise InvalidVisit("That dish is not on the visit you were tagged in.")
            current = answered_by.get(original.id)
            if answer.action == "skip":
                if current is not None:
                    rows = [d for d in rows if d.id != current.id]
                continue
            if answer.action == "same":
                score = original.rating
            else:
                score = parse_rating(answer.rating, f"rating for {original.name}")
            fields: dict[str, object] = {
                "name": original.name,
                "rating": score,
                "rating_origin": _origin(answer.action),
                "updated_at": now,
            }
            if not isinstance(answer.tags, _Unset):
                fields["tags"] = clean_dish_tags(answer.tags)
            elif current is None:
                fields["tags"] = ()
            if not isinstance(answer.comment, _Unset):
                fields["comment"] = clean_optional(
                    answer.comment, "comment", max_chars=MAX_DISH_COMMENT_CHARS, multiline=True
                )
            elif current is None:
                fields["comment"] = None
            if current is not None:
                rows = [dataclasses.replace(d, **fields) if d.id == current.id else d for d in rows]  # type: ignore[arg-type]
            else:
                rows.append(
                    VisitDish(
                        id=uuid4(),
                        visit_id=visit.id,
                        user_id=user,
                        position=len(rows),
                        created_at=now,
                        source_dish_id=original.id,
                        **fields,  # type: ignore[arg-type]
                    )
                )
        if len(rows) > MAX_DISHES:
            raise InvalidVisit(f"Use {MAX_DISHES} dishes or fewer on a visit.")
        rows = [dataclasses.replace(d, position=i) for i, d in enumerate(rows)]
        self.store.save_visit(saved, rows)
        return self._view(saved)

    # Helpers

    def _own_visit(self, user: UUID, visit_id: UUID) -> Visit:
        visit = self.store.get_visit(user, visit_id)
        if visit is None:
            raise NotFound("No such visit.")
        return visit

    def _name(self, user_id: UUID | None) -> str | None:
        if user_id is None:
            return None
        profile = self.people.get_profile(user_id)
        return profile.display_name if profile else None

    def _check_companions(
        self, user: UUID, party: str, companions: Sequence[UUID], acknowledged: bool
    ) -> list[UUID]:
        ids = list(dict.fromkeys(companions))
        if not ids:
            return []
        if party != "with_others":
            raise Conflict("Mark the meal as eaten with others before tagging people.")
        if len(ids) > MAX_COMPANIONS:
            raise InvalidVisit(f"Tag {MAX_COMPANIONS} people or fewer on a visit.")
        if not acknowledged:
            raise InvalidVisit(TAG_SHARING_NOTICE)
        for companion in ids:
            if companion == user:
                raise InvalidVisit("You cannot tag yourself.")
            if not self.people.user_exists(companion):
                raise InvalidVisit("You can only tag people who are on Makan.")
        return ids

    def _request(self, tag: VisitTag, source: Visit) -> TagRequest:
        return TagRequest(
            tag=tag,
            place_name=source.place_name,
            place_address=source.place_address,
            visited_on=source.visited_on,
            tagger_name=self._name(tag.tagger_id),
        )

    def _view(self, visit: Visit) -> VisitView:
        dishes = self.store.dishes_of(visit.id)
        tags = tuple(
            TagView(t, self._name(t.tagged_user_id)) for t in self.store.tags_of_visit(visit.id)
        )
        return VisitView(
            visit=visit,
            dishes=tuple(dishes),
            tags=tags,
            tagged_by_name=self._name(visit.tagged_by_user_id),
            shared=self._shared(visit, dishes),
        )

    def _shared(self, visit: Visit, own: Sequence[VisitDish]) -> SharedView | None:
        """The tagger's visit, only for the accepted tag that made `visit`, and only for its owner.

        The tag must be accepted and name this visit and this owner, and the visit it reads must
        be the tagger's own, so nothing here follows an id a request supplied.
        """
        tag = self.store.tag_accepted_into(visit.id)
        if tag is None or tag.status != "accepted" or tag.tagged_user_id != visit.user_id:
            return None
        source = self.store.get_visit_of_anyone(tag.visit_id)
        if source is None or source.user_id != tag.tagger_id:
            return None
        answered = {d.source_dish_id: d.id for d in own if d.source_dish_id}
        return SharedView(
            tagger_name=self._name(tag.tagger_id),
            rating=source.rating,
            description=source.description if SHARE_TAGGER_DESCRIPTION else None,
            dishes=tuple(
                SharedDish(d, answered.get(d.id)) for d in self.store.dishes_of(source.id)
            ),
        )


def _signed_in(user_id: UUID | None) -> UUID:
    if user_id is None:
        raise SignInRequired("Sign in to log your visits.")
    return user_id


def _party(raw: str) -> Literal["solo", "with_others"]:
    if raw not in PARTIES:
        raise InvalidVisit("Say whether the meal was solo or with others.")
    return "solo" if raw == "solo" else "with_others"


def _origin(action: str) -> RatingOrigin:
    """How a rating given in answer to a tagger's came to be: only taking theirs as it is counts as
    confirming it, and a rating the person typed is their own."""
    return "confirmed" if action == "same" else "fresh"


def _dish_rows(
    visit_id: UUID,
    user: UUID,
    inputs: Sequence[DishInput],
    existing: dict[UUID, VisitDish],
    now: datetime,
) -> list[VisitDish]:
    """The dishes of a visit, in the order given. A dish that names an existing id keeps its
    identity, its link to the tagger's dish, and its origin while its rating stays the same."""
    if len(inputs) > MAX_DISHES:
        raise InvalidVisit(f"Use {MAX_DISHES} dishes or fewer on a visit.")
    rows: list[VisitDish] = []
    used: set[UUID] = set()
    for position, item in enumerate(inputs):
        name = clean_dish_name(item.name)
        score = parse_rating(item.rating, f"rating for {name}")
        tags = clean_dish_tags(item.tags)
        comment = clean_optional(
            item.comment, "comment", max_chars=MAX_DISH_COMMENT_CHARS, multiline=True
        )
        before = None
        if item.id is not None:
            before = existing.get(item.id)
            if before is None or item.id in used:
                raise InvalidVisit("That dish is not on this visit.")
            used.add(item.id)
        origin: RatingOrigin = (
            before.rating_origin if before and before.rating == score else "fresh"
        )
        rows.append(
            VisitDish(
                id=before.id if before else uuid4(),
                visit_id=visit_id,
                user_id=user,
                position=position,
                name=name,
                rating=score,
                rating_origin=origin,
                tags=tags,
                comment=comment,
                source_dish_id=before.source_dish_id if before else None,
                created_at=before.created_at if before else now,
                updated_at=now,
            )
        )
    return rows
