"""The group session endpoints: create, read, join, share inputs, close, and get the result.

Anyone with a session's link token can read it and join it, with no account. Joining returns a
participant token, which the caller sends as `Authorization: Bearer <token>` to change their own
inputs, and which the host needs to close the session or ask for the result. A reader sees who is
in the session and whether they have shared anything, never what they shared. Only the host's
result names a person's constraints, because it must warn about the ones it cannot verify.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from makan.config import Config
from makan.consensus import (
    MAX_NAME_CHARS,
    MAX_NOTE_CHARS,
    MAX_TERM_CHARS,
    MAX_TERMS,
    InvalidInputs,
    floor_score,
)
from makan.group import GroupRecommendation, RankedOption, labelled_members, recommend_group
from makan.models import Participant, Session
from makan.places.base import MAX_RADIUS_M, MIN_RADIUS_M, PlacesProvider
from makan.providers.base import Provider
from makan.sessions import (
    MAX_REQUEST_CHARS,
    GroupSessions,
    NotAParticipant,
    NotHost,
    ParticipantRequired,
    SessionClosed,
    SessionError,
    SessionExpired,
    SessionFull,
    SessionNotFound,
    SessionView,
)
from makan.web.errors import error, public_warnings, workflow_failure

log = logging.getLogger("makan.web")

Mode = Literal["demo", "live"]

_STATUS: dict[type[SessionError], int] = {
    SessionNotFound: 404,
    SessionExpired: 410,
    SessionClosed: 409,
    SessionFull: 409,
    ParticipantRequired: 401,
    NotAParticipant: 403,
    NotHost: 403,
}

DisplayName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_NAME_CHARS)
]
Term = Annotated[str, StringConstraints(max_length=MAX_TERM_CHARS)]
Note = Annotated[str, StringConstraints(max_length=MAX_NOTE_CHARS)]
Terms = Annotated[list[Term], Field(max_length=MAX_TERMS)]
Notes = Annotated[list[Note], Field(max_length=MAX_TERMS)]


class CreateGroupBody(BaseModel):
    """There is deliberately no user id, as in the solo endpoint: every caller is a guest."""

    model_config = ConfigDict(extra="forbid")

    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    request: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_REQUEST_CHARS)
    ]
    radius_m: Annotated[int, Field(ge=MIN_RADIUS_M, le=MAX_RADIUS_M)] = 1000
    display_name: DisplayName | None = None


class JoinBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: DisplayName | None = None


class ConstraintsBody(BaseModel):
    """Only `refuses` can exclude a place. The rest are warnings, because they cannot be checked."""

    model_config = ConfigDict(extra="forbid")

    refuses: Terms = []
    allergies: Notes = []
    diets: Notes = []
    budget: Note | None = None


class PreferencesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    likes: Terms = []
    dislikes: Terms = []


class InputsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    constraints: ConstraintsBody = ConstraintsBody()
    preferences: PreferencesBody = PreferencesBody()
    display_name: DisplayName | None = None


def register_group_routes(
    app: FastAPI,
    sessions: GroupSessions,
    *,
    provider: Provider,
    places: PlacesProvider,
    config: Config,
    mode: Mode,
) -> None:
    """Add the group endpoints and their error handling to `app`, before any static mount."""
    router = APIRouter(prefix="/api/groups")

    @app.exception_handler(SessionError)
    async def session_error(_: Request, exc: SessionError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, ParticipantRequired) else None
        return error(_STATUS.get(type(exc), 400), exc.code, str(exc), headers)

    @app.exception_handler(InvalidInputs)
    async def invalid_inputs(_: Request, exc: InvalidInputs) -> JSONResponse:
        return error(422, "invalid_request", str(exc))

    # These handlers are plain functions, which FastAPI runs in its thread pool, so the stores
    # and the synchronous workflow never block the event loop.

    @router.post("", status_code=201)
    @guarded
    def create(body: CreateGroupBody) -> dict[str, Any]:
        session, host = sessions.create(
            latitude=body.latitude,
            longitude=body.longitude,
            request=body.request,
            radius_m=body.radius_m,
            host_name=body.display_name,
        )
        view = sessions.view(str(session.link_token), str(host.id))
        return {
            "link_token": str(session.link_token),
            "participant_token": str(host.id),
            **_view_json(view),
        }

    @router.get("/{link_token}")
    @guarded
    def read(
        link_token: str, authorization: Annotated[str | None, Header()] = None
    ) -> dict[str, Any]:
        return _view_json(sessions.view(link_token, _bearer(authorization)))

    @router.post("/{link_token}/participants", status_code=201)
    @guarded
    def join(link_token: str, body: JoinBody = JoinBody()) -> dict[str, Any]:  # noqa: B008
        session, participant = sessions.join(link_token, display_name=body.display_name)
        view = sessions.view(str(session.link_token), str(participant.id))
        return {"participant_token": str(participant.id), **_view_json(view)}

    @router.put("/{link_token}/me")
    @guarded
    def share(
        link_token: str, body: InputsBody, authorization: Annotated[str | None, Header()] = None
    ) -> dict[str, Any]:
        token = _bearer(authorization)
        sessions.submit(
            link_token,
            token,
            constraints=body.constraints.model_dump(),
            preferences=body.preferences.model_dump(),
            display_name=body.display_name,
        )
        return _view_json(sessions.view(link_token, token))

    @router.post("/{link_token}/close")
    @guarded
    def close(
        link_token: str, authorization: Annotated[str | None, Header()] = None
    ) -> dict[str, Any]:
        token = _bearer(authorization)
        sessions.close(link_token, token)
        return _view_json(sessions.view(link_token, token))

    @router.post("/{link_token}/result", response_model=None)
    @guarded
    def result(
        link_token: str, authorization: Annotated[str | None, Header()] = None
    ) -> JSONResponse:
        session, participants = sessions.host_inputs(link_token, _bearer(authorization))
        outcome = recommend_group(
            session, participants, provider=provider, places=places, config=config
        )
        if outcome.recommendation is None:
            return workflow_failure(outcome.graph)
        return JSONResponse(_result_json(outcome.recommendation, outcome.graph.ok, mode))

    app.include_router(router)


def guarded[**P](route: Callable[P, Any]) -> Callable[P, Any]:
    """Turn any failure a route does not answer for itself into the documented 500 JSON error.

    A session rule that failed (`SessionError`) and bad input (`InvalidInputs`) have handlers of
    their own. Anything else, such as a store whose connection dropped, is logged here with its
    traceback and reported without its text, so no internal detail reaches the caller and the
    response keeps the error shape the page keys on.
    """

    @functools.wraps(route)
    def run(*args: P.args, **kwargs: P.kwargs) -> Any:
        try:
            return route(*args, **kwargs)
        except (SessionError, InvalidInputs):
            raise
        except Exception:
            log.exception("group route failed")
            return error(500, "server_error", "Something went wrong on our side. Try again.")

    return run


def _bearer(authorization: str | None) -> str | None:
    """The token of an `Authorization: Bearer <token>` header, or None for anything else."""
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    return (token.strip() or None) if scheme.lower() == "bearer" else None


def _utc(moment: datetime) -> str:
    """A timestamp in UTC whatever time zone the store read it back in."""
    return moment.astimezone(UTC).isoformat()


def _view_json(view: SessionView) -> dict[str, Any]:
    """The session as a reader sees it: who is in, and who has shared, but never what."""
    labelled = labelled_members(view.participants)
    session: Session = view.session
    body: dict[str, Any] = {
        "session": {
            "request": session.context["request"],
            "latitude": session.context["latitude"],
            "longitude": session.context["longitude"],
            "radius_m": session.context["radius_m"],
            "created_at": _utc(session.created_at),
            "expires_at": _utc(session.expires_at) if session.expires_at else None,
            "closed": session.closed_at is not None,
            "participants": [
                {"name": m.name, "is_host": p.is_host, "submitted": m.submitted}
                for p, m in labelled
            ],
        },
        "you": None,
    }
    if view.you is not None:
        mine: Participant = view.you
        member = next(m for p, m in labelled if p.id == mine.id)
        body["you"] = {
            "name": member.name,
            "is_host": mine.is_host,
            "submitted": member.submitted,
            "constraints": member.constraints.to_json(),
            "preferences": member.preferences.to_json(),
        }
    return body


def _option_json(option: RankedOption) -> dict[str, Any]:
    p = option.place
    return {
        "id": p.id,
        "name": p.name,
        "category": p.category,
        "distance_m": p.distance_m,
        "address": p.address,
        "reasons": list(option.reasons),
        "lowest_score": None if option.minimum is None else floor_score(option.minimum),
        "lowest_scorers": list(option.least_happy),
        "average_score": None if option.average is None else round(option.average, 2),
        "warnings": list(option.warnings),
    }


def _result_json(rec: GroupRecommendation, graph_ok: bool, mode: Mode) -> dict[str, Any]:
    warnings, explanation = public_warnings(rec.warnings, rec.explanation)
    return {
        "pick": _option_json(rec.pick) if rec.pick else None,
        "runners_up": [_option_json(o) for o in rec.runners_up],
        "excluded": [
            {
                "id": e.place.id,
                "name": e.place.name,
                "category": e.place.category,
                "refusals": [{"person": r.member, "term": r.term} for r in e.refusals],
            }
            for e in rec.excluded
        ],
        "explanation": explanation,
        "warnings": warnings,
        "participant_count": rec.participant_count,
        "pending": list(rec.pending),
        "uncounted": list(rec.uncounted),
        "data_source": rec.data_source,
        "attribution": (
            "Places data: Overture Maps Foundation (CDLA Permissive 2.0)."
            if rec.data_source.startswith("overture:")
            else None
        ),
        "partial": not graph_ok,
        "mode": mode,
    }
