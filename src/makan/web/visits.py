"""Visit logging over HTTP: going here, visits and their dishes, companion tags, and confirming.

Every route is for a signed-in person and is scoped to the token's user, and the request body has
no user id for the owner. A request with no `Authorization` header is a guest and is refused with
401 `sign_in_required`, so a guest is told what to do and is not mistaken for someone whose session
ended. Searching and recommending stay open to guests, and nothing here changes them. Like
`/api/me`, these routes exist only when accounts are configured.

Ratings go out as JSON numbers with one decimal place, which round-trip exactly, and come in as
numbers or strings. Nothing sent here reaches a client except through the builders in this module,
so a field exists in a response only because it is listed here.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from makan.accounts.tokens import Identity
from makan.models import GoingHere, TagStatus, Visit, VisitDish
from makan.visits import (
    TAG_SHARING_NOTICE,
    UNSET,
    Answered,
    DishAnswer,
    DishInput,
    PlaceRef,
    RatingAnswer,
    SharedView,
    TagRequest,
    TagView,
    VisitError,
    VisitSummary,
    VisitView,
    parse_place,
)
from makan.web.accounts import AccountServices, guarded, required_identity
from makan.web.errors import ApiError, error

_CHALLENGE = {"WWW-Authenticate": "Bearer"}

# What the body accepts before the service cleans it, so a huge body is refused early.
Rating = Annotated[Decimal, Field(allow_inf_nan=False, max_digits=32)]
Name = Annotated[str, StringConstraints(max_length=400)]
Note = Annotated[str, StringConstraints(max_length=8000)]
Party = Literal["solo", "with_others"]


class PlaceBody(BaseModel):
    """A place as a search returned it: its id and name, and the `data_source` of that search."""

    model_config = ConfigDict(extra="forbid")

    data_source: Annotated[str, StringConstraints(max_length=100)]
    id: Annotated[str, StringConstraints(max_length=1000)]
    name: Name
    address: Annotated[str | None, StringConstraints(max_length=1000)] = None

    def parsed(self) -> PlaceRef:
        return parse_place(self.data_source, self.id, self.name, self.address)


class DishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None  # an existing dish of the visit, to keep it
    name: Name
    rating: Rating
    tags: Annotated[list[Name], Field(max_length=100)] = []
    comment: Note | None = None

    def parsed(self) -> DishInput:
        return DishInput(self.name, self.rating, self.tags, self.comment, self.id)


class GoingHereBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    place: PlaceBody
    planned_on: date | None = None  # the person's own calendar day, which the visit starts from


class VisitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    place: PlaceBody
    rating: Rating
    party: Party
    visited_on: date | None = None
    description: Note | None = None
    dishes: Annotated[list[DishBody], Field(max_length=200)] = []
    companions: Annotated[list[UUID], Field(max_length=100)] = []
    acknowledged_sharing: bool = False
    going_here_id: UUID | None = None


class VisitChangesBody(BaseModel):
    """Only the fields sent change. `dishes` replaces the whole list."""

    model_config = ConfigDict(extra="forbid")

    place: PlaceBody | None = None
    rating: Rating | None = None
    party: Party | None = None
    visited_on: date | None = None
    description: Note | None = None
    dishes: Annotated[list[DishBody], Field(max_length=200)] | None = None


class TagBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    acknowledged_sharing: bool = False


class RatingAnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["same", "change"]
    value: Rating | None = None


class DishAnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_dish_id: UUID
    action: Literal["same", "change", "skip"]
    rating: Rating | None = None
    tags: Annotated[list[Name], Field(max_length=100)] = []
    comment: Note | None = None


class ConfirmationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: RatingAnswerBody | None = None
    dishes: Annotated[list[DishAnswerBody], Field(max_length=200)] = []


# Response builders


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _when(value: Any) -> str | None:
    return None if value is None else str(value.isoformat())


def _id(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def _place_json(row: Visit | GoingHere) -> dict[str, Any]:
    return {
        "source": row.place_source,
        "id": row.place_id,
        "name": row.place_name,
        "address": row.place_address,
    }


def _going_here_json(m: GoingHere) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "place": _place_json(m),
        "planned_on": m.planned_on.isoformat(),
        "started_at": _when(m.started_at),
        "remind_at": _when(m.remind_at),
        "reminded_at": _when(m.reminded_at),
        "visit_id": _id(m.visit_id),
        "cancelled_at": _when(m.cancelled_at),
    }


def _dish_json(d: VisitDish) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "position": d.position,
        "name": d.name,
        "rating": _num(d.rating),
        "rating_origin": d.rating_origin,
        "tags": list(d.tags),
        "comment": d.comment,
        "answers_dish_id": _id(d.source_dish_id),
    }


def _tag_json(view: TagView) -> dict[str, Any]:
    t = view.tag
    return {
        "id": str(t.id),
        "user_id": str(t.tagged_user_id),
        "display_name": view.name,
        "status": t.status,
        "created_at": _when(t.created_at),
        "responded_at": _when(t.responded_at),
    }


def _shared_json(shared: SharedView) -> dict[str, Any]:
    return {
        "tagger_name": shared.tagger_name,
        "rating": _num(shared.rating),
        "description": shared.description,
        "dishes": [
            {**_dish_json(s.dish), "answered_by": _id(s.answered_by)} for s in shared.dishes
        ],
    }


def _visit_json(view: VisitView) -> dict[str, Any]:
    v = view.visit
    return {
        "id": str(v.id),
        "place": _place_json(v),
        "visited_on": v.visited_on.isoformat(),
        "party": v.party,
        "rating": _num(v.rating),
        "rating_origin": v.rating_origin,
        "description": v.description,
        "tagged_by": None if v.tagged_by_user_id is None else {"display_name": view.tagged_by_name},
        "dishes": [_dish_json(d) for d in view.dishes],
        "dish_count": len(view.dishes),
        "tags": [_tag_json(t) for t in view.tags],
        "shared": None if view.shared is None else _shared_json(view.shared),
        "created_at": _when(v.created_at),
        "updated_at": _when(v.updated_at),
    }


def _summary_json(s: VisitSummary) -> dict[str, Any]:
    v = s.visit
    return {
        "id": str(v.id),
        "place": _place_json(v),
        "visited_on": v.visited_on.isoformat(),
        "party": v.party,
        "rating": _num(v.rating),
        "dish_count": s.dish_count,
        "companions": [c for c in s.companions],
        "tagged_by": None if v.tagged_by_user_id is None else {"display_name": s.tagged_by_name},
    }


def _request_json(r: TagRequest) -> dict[str, Any]:
    """What a request shows its recipient, and nothing the tagger rated or wrote."""
    t = r.tag
    return {
        "id": str(t.id),
        "status": t.status,
        "created_at": _when(t.created_at),
        "responded_at": _when(t.responded_at),
        "place": {"name": r.place_name, "address": r.place_address},
        "visited_on": r.visited_on.isoformat(),
        "tagged_by": {"display_name": r.tagger_name},
        "visit_id": _id(t.accepted_visit_id),
    }


def _answered_json(a: Answered) -> dict[str, Any]:
    return {
        "request": _request_json(a.request),
        "visit": None if a.visit is None else _visit_json(a.visit),
    }


def signed_in(request: Request, authorization: Annotated[str | None, Header()] = None) -> Identity:
    """A guest sends no token, and is told to sign in. A token that fails is the usual 401."""
    if authorization is None:
        raise ApiError(
            401,
            "sign_in_required",
            "Sign in to log your visits. Searching works without an account.",
            _CHALLENGE,
        )
    return required_identity(request, authorization)


def register_visit_routes(app: FastAPI, services: AccountServices) -> None:
    """Add the visit routes under `/api/me` to `app`, before any static mount."""
    accounts = services.accounts
    visits = services.visits
    router = APIRouter(prefix="/api/me")

    @app.exception_handler(VisitError)
    async def visit_error(_: Any, exc: VisitError) -> JSONResponse:
        return error(exc.status, exc.code, str(exc))

    def current(identity: Annotated[Identity, Depends(signed_in)]) -> Identity:
        """The signed-in person, with their `users` row made on the first call."""
        accounts.ensure_user(identity.user_id)
        return identity

    Me = Annotated[Identity, Depends(current)]

    # Going here. Declared before the routes that take an id, so "due" is never read as one.

    @router.post("/going-here")
    @guarded
    def going_here(body: GoingHereBody, me: Me) -> dict[str, Any]:
        marker = visits.going_here(me.user_id, body.place.parsed(), body.planned_on)
        return {"going_here": _going_here_json(marker)}

    @router.get("/going-here")
    @guarded
    def open_markers(me: Me) -> dict[str, Any]:
        return {"going_here": [_going_here_json(m) for m in visits.open_going_here(me.user_id)]}

    @router.get("/going-here/due")
    @guarded
    def due(me: Me) -> dict[str, Any]:
        """What is due a reminder now. This sends nothing and changes nothing."""
        return {"due": [_going_here_json(m) for m in visits.due_reminders(me.user_id)]}

    @router.post("/going-here/{marker_id}/reminded")
    @guarded
    def reminded(marker_id: UUID, me: Me) -> dict[str, Any]:
        return {"going_here": _going_here_json(visits.mark_reminded(me.user_id, marker_id))}

    @router.delete("/going-here/{marker_id}")
    @guarded
    def cancel(marker_id: UUID, me: Me) -> dict[str, Any]:
        return {"going_here": _going_here_json(visits.cancel_going_here(me.user_id, marker_id))}

    # Visits

    @router.post("/visits", status_code=201)
    @guarded
    def log_visit(body: VisitBody, me: Me) -> dict[str, Any]:
        view = visits.log_visit(
            me.user_id,
            place=body.place.parsed(),
            rating=body.rating,
            party=body.party,
            visited_on=body.visited_on,
            description=body.description,
            dishes=[d.parsed() for d in body.dishes],
            companions=body.companions,
            acknowledged_sharing=body.acknowledged_sharing,
            going_here_id=body.going_here_id,
        )
        return {"visit": _visit_json(view)}

    @router.get("/visits")
    @guarded
    def history(me: Me) -> dict[str, Any]:
        return {
            "visits": [_summary_json(s) for s in visits.history(me.user_id)],
            "sharing_notice": TAG_SHARING_NOTICE,
        }

    @router.get("/visits/{visit_id}")
    @guarded
    def get_visit(visit_id: UUID, me: Me) -> dict[str, Any]:
        return {"visit": _visit_json(visits.visit(me.user_id, visit_id))}

    @router.patch("/visits/{visit_id}")
    @guarded
    def edit_visit(visit_id: UUID, body: VisitChangesBody, me: Me) -> dict[str, Any]:
        given = body.model_fields_set
        for name in ("place", "party", "visited_on", "dishes"):
            if name in given and getattr(body, name) is None:
                raise ApiError(422, "invalid_request", f"Check these fields: {name}.")
        view = visits.edit_visit(
            me.user_id,
            visit_id,
            place=body.place.parsed() if body.place else UNSET,
            rating=body.rating if "rating" in given else UNSET,
            party=body.party if body.party else UNSET,
            visited_on=body.visited_on if body.visited_on else UNSET,
            description=body.description if "description" in given else UNSET,
            dishes=[d.parsed() for d in body.dishes] if body.dishes is not None else UNSET,
        )
        return {"visit": _visit_json(view)}

    @router.delete("/visits/{visit_id}", status_code=204)
    @guarded
    def delete_visit(visit_id: UUID, me: Me) -> Response:
        visits.delete_visit(me.user_id, visit_id)
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    @router.put("/visits/{visit_id}/confirmation")
    @guarded
    def confirm(visit_id: UUID, body: ConfirmationBody, me: Me) -> dict[str, Any]:
        """Answer the tagger's visit: confirm or change the restaurant rating and each dish."""
        view = visits.confirm(
            me.user_id,
            visit_id,
            rating=(
                None if body.rating is None else RatingAnswer(body.rating.action, body.rating.value)
            ),
            dishes=[
                DishAnswer(
                    d.source_dish_id,
                    d.action,
                    d.rating,
                    d.tags if "tags" in d.model_fields_set else UNSET,
                    d.comment if "comment" in d.model_fields_set else UNSET,
                )
                for d in body.dishes
            ],
        )
        return {"visit": _visit_json(view)}

    # Companion tags

    @router.post("/visits/{visit_id}/tags")
    @guarded
    def tag(visit_id: UUID, body: TagBody, me: Me) -> dict[str, Any]:
        made = visits.tag(
            me.user_id, visit_id, body.user_id, acknowledged_sharing=body.acknowledged_sharing
        )
        return {"tag": _tag_json(made)}

    @router.delete("/visits/{visit_id}/tags/{tag_id}", status_code=204)
    @guarded
    def withdraw(visit_id: UUID, tag_id: UUID, me: Me) -> Response:
        visits.withdraw_tag(me.user_id, visit_id, tag_id)
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    @router.get("/tag-requests")
    @guarded
    def tag_requests(
        me: Me,
        status: Annotated[Literal["pending", "accepted", "declined", "all"], Query()] = "pending",
    ) -> dict[str, Any]:
        wanted: TagStatus | None = None if status == "all" else status
        return {"requests": [_request_json(r) for r in visits.tag_requests(me.user_id, wanted)]}

    @router.post("/tag-requests/{tag_id}/accept")
    @guarded
    def accept(tag_id: UUID, me: Me) -> dict[str, Any]:
        return _answered_json(visits.accept_tag(me.user_id, tag_id))

    @router.post("/tag-requests/{tag_id}/decline")
    @guarded
    def decline(tag_id: UUID, me: Me) -> dict[str, Any]:
        return _answered_json(visits.decline_tag(me.user_id, tag_id))

    app.include_router(router)
