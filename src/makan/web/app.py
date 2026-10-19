"""The HTTP API for the web channel.

One endpoint turns a location and a request into a recommendation, and everything else is
plumbing around `makan.solo.recommend`. No account is needed: the session it creates has one
participant, is never shared, and is not stored. A request that carries a valid access token
is a signed-in person's, and reads their stored memory and greets them by name (see
`makan.web.accounts`).

Run it with `python -m makan.web` (see `makan.web.__main__`). See the README for the run modes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

from makan.accounts.tokens import Identity
from makan.browse import BrowseRequest, BrowseResult, browse
from makan.config import Config, ConfigError
from makan.env import load_dotenv
from makan.fit import FitReason, cuisine_fit
from makan.fit_inputs import Cuisine, FitInputs, owner_fit_inputs
from makan.places.base import MAX_RADIUS_M, MIN_RADIUS_M, PlaceQuery, PlacesProvider
from makan.places.factory import places_provider
from makan.providers.base import Provider
from makan.providers.openrouter import OpenRouterProvider
from makan.providers.scoring import Scorer, build_scorer
from makan.providers.unavailable import UnavailableProvider
from makan.sessions import GroupSessions, InMemorySessionStore
from makan.sessions.service import MAX_REQUEST_CHARS
from makan.solo import (
    RankedCandidate,
    Recommendation,
    SoloRequest,
    SoloResult,
    build_solo_graph,
    recommend,
)
from makan.web.accounts import (
    AccountServices,
    build_account_services,
    optional_identity,
    register_account_routes,
)
from makan.web.demo import DemoPlaces, DemoProvider
from makan.web.errors import ApiError, error, public_warnings, workflow_failure
from makan.web.groups import register_group_routes
from makan.web.purge import purge_forever
from makan.web.runs import Outcome, RunRecorder, SearchMode
from makan.web.visits import register_visit_routes

log = logging.getLogger("makan.web")

# What the data and the language model are: invented stand-ins, real places with no model
# ("places", browsing only), or everything real.
Mode = Literal["demo", "live", "places"]
DEFAULT_WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


class GuestTastes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    likes: list[Cuisine] = Field(default_factory=list, max_length=8)
    dislikes: list[Cuisine] = Field(default_factory=list, max_length=8)


class RecommendBody(BaseModel):
    """What the browser sends. There is deliberately no user id: who is asking comes only from
    a verified access token in the `Authorization` header, never from the body.

    The `mode` is always stated, so no request is guessed at. `recommend` needs a `request`, and
    `browse` takes none: there is nothing to read, and no model is called.
    """

    model_config = ConfigDict(extra="forbid")

    mode: SearchMode
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    request: Annotated[
        str | None, StringConstraints(strip_whitespace=True, max_length=MAX_REQUEST_CHARS)
    ] = Field(default=None, validate_default=True)
    radius_m: Annotated[int, Field(ge=MIN_RADIUS_M, le=MAX_RADIUS_M)] = 1000
    tastes: GuestTastes | None = None

    @field_validator("request")
    @classmethod
    def request_fits_mode(cls, request: str | None, info: ValidationInfo) -> str | None:
        mode = info.data.get("mode")
        if mode == "recommend" and not request:
            raise ValueError("a recommendation needs a request")
        if mode == "browse" and request is not None:
            raise ValueError("browsing nearby takes no request")
        return request


@dataclass(frozen=True)
class Answer:
    """What one search came to: the JSON to send, or the error to send in its place."""

    recorder: RunRecorder
    payload: dict[str, Any] | None = None
    failure: JSONResponse | None = None


def create_app(
    *,
    provider: Provider,
    places: PlacesProvider,
    config: Config,
    mode: Mode = "live",
    static_dir: Path | None = None,
    sessions: GroupSessions | None = None,
    scorer: Scorer | None = None,
    accounts: AccountServices | None = None,
) -> FastAPI:
    """Build the app around ready providers. Tests pass fakes, and the CLI passes real ones.

    A `scorer` adds soft request signals to solo recommendations. Without one there are none.

    With `accounts`, a signed-in person's solo searches read their memory and greet them, and
    `/api/me` serves their profile, taste, export, and deletion. Without it everyone is a guest,
    and an `Authorization` header on a search is ignored.

    Group sessions are kept in `sessions`, or in memory for the life of the process when it is None.
    While the app runs it deletes expired sessions at startup and then every
    `config.session_purge_interval_minutes`, so expired participant data does not stay stored.
    With `config.groups_enabled` False, or in `places` mode, there are no group routes, no group
    page, no sessions, and no purge task.

    In `places` mode the app answers Browse nearby only, with no model: a recommendation is
    refused, and `provider` is never called.
    """
    # A group needs the language model to pick, so there are none in a run with no model.
    groups = config.groups_enabled and mode != "places"
    group_sessions: GroupSessions | None = None
    if groups:
        group_sessions = _default_sessions(config) if sessions is None else sessions

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if group_sessions is None:
            yield
            return
        purge = asyncio.create_task(
            purge_forever(group_sessions, config.session_purge_interval_minutes * 60)
        )
        try:
            yield
        finally:
            purge.cancel()
            await asyncio.gather(purge, return_exceptions=True)

    app = FastAPI(
        title="Makan",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    background: set[asyncio.Future[None]] = set()  # keeps streamed runs alive until they end

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = ", ".join(
            ".".join(str(part) for part in e["loc"][1:]) or "body" for e in exc.errors()
        )
        return error(422, "invalid_request", f"Check these fields: {fields}.")

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error(exc.status, exc.code, str(exc), exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error(exc.status_code, "http_error", str(exc.detail))

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "mode": mode}

    @app.get("/api/config")
    async def public_config() -> dict[str, Any]:
        """What the page needs to start: the run mode and where its map tiles come from."""
        return {
            "mode": mode,
            "map": {
                "tile_url": config.map_tile_url,
                "attribution": config.map_attribution,
                "attribution_url": config.map_attribution_url or None,
            },
            "auth": accounts.public_config() if accounts else None,
            # What this deployment offers, so the page shows no screen that would only fail.
            "features": {
                "recommend": mode != "places",
                "groups": groups,
                "accounts": accounts is not None,
                "fit": config.fit_enabled,
            },
        }

    app.state.accounts = accounts
    if accounts is not None:
        register_account_routes(app, accounts)
        register_visit_routes(app, accounts)

    # The stages of the full solo graph, so a browse run can show the ones it skipped.
    plan = tuple(
        (step.name, step.after)
        for step in build_solo_graph(
            provider=provider, places=places, config=config, scorer=scorer
        ).steps
    )

    def require_recommendations(body: RecommendBody) -> None:
        if mode == "places" and body.mode == "recommend":
            raise ApiError(
                422,
                "recommendations_unavailable",
                "This site shows real places nearby only. Use Browse nearby.",
            )

    def run_search(
        body: RecommendBody,
        on_update: Callable[[dict[str, Any]], None] | None = None,
        identity: Identity | None = None,
    ) -> Answer:
        """Run the search with a recorder on its trace. Synchronous, so call it off the loop."""
        center = PlaceQuery.near(body.latitude, body.longitude, body.radius_m)
        recorder = RunRecorder(
            mode=mode,
            search_mode=body.mode,
            plan=plan if body.mode == "browse" else None,
            config=config,
            request=body.request,
            latitude=center.lat,
            longitude=center.lon,
            radius_m=center.radius_m,
            data_source=places.name,
            on_update=on_update,
        )
        inputs = FitInputs()
        # Disabling soft scores must never disable known hard exclusions or their warnings.
        if identity is not None and accounts is not None:
            try:
                inputs = owner_fit_inputs(accounts.accounts.memory, identity.user_id)
            except Exception:
                log.exception("could not read local fit inputs")
                inputs = FitInputs(
                    problem=FitReason.UNAVAILABLE,
                    warnings=("Saved tastes and exclusions are unavailable. Check before going.",),
                )
        elif config.fit_enabled and body.tastes is not None:
            inputs = FitInputs(tuple(body.tastes.likes), tuple(body.tastes.dislikes))

        def assess(ranked: tuple[RankedCandidate, ...]) -> tuple[RankedCandidate, ...]:
            return tuple(
                replace(
                    r,
                    fit=cuisine_fit(
                        r.place.category,
                        inputs.likes,
                        inputs.dislikes,
                        enabled=config.fit_enabled,
                        problem=inputs.problem,
                    ),
                )
                for r in ranked
                if not inputs.excludes(r.place.name)
            )

        if body.mode == "browse":
            found = browse(
                BrowseRequest(body.latitude, body.longitude, body.radius_m),
                places=places,
                config=config,
                sink=recorder,
            )
            if found.places is not None:
                assessed = assess(found.places)
                excluded = len(assessed) != len(found.places)
                found = replace(
                    found,
                    places=assessed,
                    warnings=found.warnings
                    + inputs.warnings
                    + (("Left out places you said you will not go to.",) if excluded else ()),
                )
            recorder.finish(_browse_outcome(found))
            if found.places is None:
                return Answer(recorder, failure=workflow_failure(found.graph))
            return Answer(recorder, _browse_json(found, mode, recorder))
        assert body.request is not None  # a recommendation always has one, see RecommendBody
        # `identity` is None for a guest, and always when accounts are off.
        result = recommend(
            SoloRequest(
                body.latitude,
                body.longitude,
                body.request,
                body.radius_m,
                user_id=identity.user_id if identity else None,
                display_name=_name_of(accounts, identity),
            ),
            provider=provider,
            places=places,
            config=config,
            memory=accounts.accounts.memory if identity and accounts else None,
            sink=recorder,
            scorer=scorer,
        )
        if result.recommendation is None:
            recorder.finish(_outcome(result))
            return Answer(recorder, failure=workflow_failure(result.graph))
        rec = result.recommendation
        ranked = assess(rec.ranked)
        # A single immutable value per candidate is reused by all three wire views.
        rec = replace(
            rec,
            ranked=ranked,
            pick=ranked[0] if ranked else None,
            runners_up=ranked[1:4],
            warnings=tuple(dict.fromkeys((*rec.warnings, *inputs.warnings))),
        )
        if len(ranked) != len(result.recommendation.ranked):
            rec = replace(
                rec,
                explanation=(
                    f"Try {ranked[0].place.name}."
                    if ranked
                    else "No places found within this search radius."
                ),
                warnings=(*rec.warnings, "Left out places you said you will not go to."),
            )
        recorder.finish(_outcome(replace(result, recommendation=rec)))
        payload = _recommendation_json(
            rec, result.graph.ok, mode, _query_json(body, result), recorder
        )
        return Answer(recorder, payload)

    @app.post("/api/recommendations")
    async def recommendations(
        body: RecommendBody, identity: Annotated[Identity | None, Depends(optional_identity)]
    ) -> JSONResponse:
        require_recommendations(body)
        # The workflow is synchronous and runs its own event loop, so it must leave ours.
        try:
            answer = await run_in_threadpool(run_search, body, None, identity)
        except Exception:
            log.exception("recommendation crashed")
            return error(500, "server_error", "Something went wrong on our side. Try again.")
        if answer.failure is not None:
            return answer.failure
        return JSONResponse(answer.payload, headers={"Cache-Control": "private, no-store"})

    @app.post("/api/recommendations/stream")
    async def recommendations_stream(
        body: RecommendBody, identity: Annotated[Identity | None, Depends(optional_identity)]
    ) -> StreamingResponse:
        """The same answer as one JSON object per line, led by the run as its stages progress.

        Lines are `{"type": "run", "run": ...}` while it works, then one last `result` line with
        the recommendation or an `error` line with the same code and message as the plain route.
        """
        require_recommendations(body)
        loop = asyncio.get_running_loop()
        lines: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def push(line: dict[str, Any] | None) -> None:
            loop.call_soon_threadsafe(lines.put_nowait, line)

        def work() -> None:
            try:
                answer = run_search(body, lambda run: push({"type": "run", "run": run}), identity)
                if answer.failure is not None:
                    push(
                        {
                            "type": "error",
                            "status": answer.failure.status_code,
                            **json.loads(bytes(answer.failure.body)),
                            "run": answer.recorder.snapshot(),
                        }
                    )
                else:
                    push({"type": "result", "recommendation": answer.payload})
            except Exception:
                log.exception("recommendation crashed")
                push(
                    {
                        "type": "error",
                        "status": 500,
                        "error": {
                            "code": "server_error",
                            "message": "Something went wrong on our side. Try again.",
                        },
                    }
                )
            finally:
                push(None)

        # The thread cannot be cancelled, so it finishes even if the browser leaves.
        finished = asyncio.ensure_future(run_in_threadpool(work))
        background.add(finished)
        finished.add_done_callback(background.discard)

        async def body_lines() -> AsyncIterator[str]:
            while (line := await lines.get()) is not None:
                yield json.dumps(line, ensure_ascii=False) + "\n"

        return StreamingResponse(
            body_lines(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"},
        )

    if group_sessions is not None and mode != "places":
        register_group_routes(
            app,
            group_sessions,
            provider=provider,
            places=places,
            config=config,
            mode=mode,
        )

    # A switched off feature is a plain 404 for every method, not a fall through to the static
    # files, which would answer a POST with a 405 that suggests the route exists.
    switched_off = [("/api/groups", not groups), ("/api/me", accounts is None)]
    for prefix, off in switched_off:
        if off:
            _refuse_every_method(app, prefix)

    if static_dir is not None and static_dir.is_dir():
        index = static_dir / "index.html"

        if groups:

            @app.get("/g/{link_token}", include_in_schema=False, response_model=None)
            @app.get("/g/{link_token}/", include_in_schema=False, response_model=None)
            def group_page(link_token: str) -> FileResponse | JSONResponse:
                """A shared group link opens the same page. The page asks the API about it."""
                if not index.is_file():
                    return error(404, "not_found", "Not found.")
                return FileResponse(index, headers={"Cache-Control": "no-store"})

        # Registered last so the API routes win. `html=True` serves index.html at "/".
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return app


def _refuse_every_method(app: FastAPI, prefix: str) -> None:
    """Answer 404 to anything at or under `prefix`, for every method."""

    async def not_found(request: Request) -> JSONResponse:
        return error(404, "not_found", "Not found.")

    for path in (prefix, f"{prefix}/{{rest:path}}"):
        app.add_api_route(
            path,
            not_found,
            methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            include_in_schema=False,
        )


def _requested(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def demo_requested(env: Mapping[str, str]) -> bool:
    """Whether `MAKAN_DEMO` asks for sample places and no key or network."""
    return _requested(env, "MAKAN_DEMO")


def places_only_requested(env: Mapping[str, str]) -> bool:
    """Whether `MAKAN_PLACES_ONLY` asks for real places with Browse nearby and no model."""
    return _requested(env, "MAKAN_PLACES_ONLY")


def create_app_from_env(env: Mapping[str, str] | None = None) -> FastAPI:
    """The uvicorn factory. `MAKAN_DEMO` gives sample places, `MAKAN_PLACES_ONLY` gives real
    places with Browse nearby only, and otherwise the providers are real.

    Demo mode wins when both are set, since it is the explicit choice of invented data. Demo and
    places-only modes need no key. Places-only makes no call at startup either: Overture is read
    on the first search. Live mode needs `MAKAN_MODEL` and `OPENROUTER_API_KEY`, and fails at
    startup, not on the first request, when either is missing.
    `MAKAN_GROUPS_ENABLED` and `MAKAN_ACCOUNTS_ENABLED` set to a false value remove those features
    in every mode. Called with no `env`, it first loads `.env` (see `makan.env`) into the real
    environment.
    """
    if env is None:
        load_dotenv()
    env = os.environ if env is None else env
    dist = Path(env.get("MAKAN_WEB_DIST", "").strip() or DEFAULT_WEB_DIST)
    if demo_requested(env) or places_only_requested(env):
        demo = demo_requested(env)
        stand_in = "demo" if demo else "none"
        config = Config.from_env({**env, "MAKAN_MODEL": env.get("MAKAN_MODEL", "") or stand_in})
        return create_app(
            provider=DemoProvider() if demo else UnavailableProvider(),
            places=DemoPlaces() if demo else places_provider(config),
            config=config,
            mode="demo" if demo else "places",
            static_dir=dist,
            sessions=_default_sessions(config),  # demo group sessions never need a database
            accounts=build_account_services(config),  # accounts do, when Supabase is set
        )
    try:
        config = Config.from_env(env)
        if not config.openrouter_api_key:
            raise ConfigError("OPENROUTER_API_KEY is not set")
    except ConfigError as exc:
        raise ConfigError(
            f"{exc}. Set it in .env, or run with MAKAN_DEMO=1 for sample data."
        ) from exc
    return create_app(
        provider=OpenRouterProvider(config.openrouter_api_key),
        places=places_provider(config),
        config=config,
        scorer=build_scorer(config.scorer, openrouter_api_key=config.openrouter_api_key),
        mode="live",
        static_dir=dist,
        sessions=_group_sessions(config) if config.groups_enabled else None,
        accounts=build_account_services(config),
    )


def _name_of(accounts: AccountServices | None, identity: Identity | None) -> str | None:
    """The signed-in person's name for the greeting, or None for a guest or no profile yet.

    A lookup that fails only costs the greeting, not the search.
    """
    if accounts is None or identity is None:
        return None
    try:
        return accounts.accounts.display_name(identity.user_id)
    except Exception:
        log.exception("could not read the display name")
        return None


def _default_sessions(config: Config) -> GroupSessions:
    return GroupSessions(
        InMemorySessionStore(), retention=timedelta(hours=config.session_retention_hours)
    )


def _group_sessions(config: Config) -> GroupSessions:
    """Group sessions in Postgres when `MAKAN_DATABASE_URL` is set, otherwise in memory.

    The Postgres connection is opened once here, so a wrong URL fails at startup.
    """
    if not config.database_url:
        log.warning("MAKAN_DATABASE_URL is not set, so group sessions are lost on restart")
        return _default_sessions(config)
    try:
        import psycopg

        from makan.sessions.postgres import PostgresSessionStore
    except ImportError as exc:
        raise ConfigError(
            'MAKAN_DATABASE_URL is set but psycopg is not installed; run: pip install ".[postgres]"'
        ) from exc
    try:
        conn = psycopg.connect(config.database_url, autocommit=True)
    except psycopg.Error as exc:
        # The message can echo the URL, so only the error type is reported.
        raise ConfigError(
            f"could not connect to MAKAN_DATABASE_URL ({type(exc).__name__})"
        ) from None
    return GroupSessions(
        PostgresSessionStore(conn), retention=timedelta(hours=config.session_retention_hours)
    )


def _outcome(result: SoloResult) -> Outcome:
    """What the person got, which the graph status alone cannot say: a failed graph can still
    carry a usable recommendation, and an ok graph can find nothing."""
    rec = result.recommendation
    if rec is None:
        return "failed"
    if not result.graph.ok:
        return "partial"
    return "complete" if rec.pick else "no_result"


def _browse_outcome(result: BrowseResult) -> Outcome:
    if result.places is None:
        return "failed"
    return "complete" if result.places else "no_result"


def _query_json(body: RecommendBody, result: SoloResult) -> dict[str, Any]:
    """The search as it ran: the rounded center the places were found around, not the raw input."""
    context = result.session.context
    return {
        "mode": body.mode,
        "latitude": context["latitude"],
        "longitude": context["longitude"],
        "radius_m": context["radius_m"],
        "request": body.request,
    }


def _attribution(data_source: str) -> str | None:
    if data_source.startswith("overture:"):
        return "Places data: Overture Maps Foundation (CDLA Permissive 2.0)."
    return None


def _place_json(ranked: RankedCandidate, rank: int) -> dict[str, Any]:
    p = ranked.place
    return {
        "id": p.id,
        "name": p.name,
        "category": p.category,
        "distance_m": p.distance_m,
        "address": p.address,
        "lat": p.lat,
        "lon": p.lon,
        "rank": rank,
        "matched": p.request_fit > 0,
        "reasons": list(ranked.reasons),
        "fit": ranked.fit.to_json(),
    }


def _recommendation_json(
    rec: Recommendation,
    graph_ok: bool,
    mode: Mode,
    query: dict[str, Any],
    recorder: RunRecorder,
) -> dict[str, Any]:
    warnings, explanation = public_warnings(rec.warnings, rec.explanation)
    rank_of = {r.place.id: i for i, r in enumerate(rec.ranked, start=1)}
    return {
        "query": query,
        "intent": (
            {"cuisine": rec.intent.cuisine, "category": rec.intent.category} if rec.intent else None
        ),
        "pick": _place_json(rec.pick, 1) if rec.pick else None,
        "runners_up": [_place_json(r, rank_of[r.place.id]) for r in rec.runners_up],
        "places": [_place_json(r, i) for i, r in enumerate(rec.ranked, start=1)],
        "candidate_count": len(rec.ranked),
        "truncated": rec.truncated,
        "explanation": explanation,
        "greeting": rec.greeting,
        "warnings": warnings,
        "stale_facts": [
            {
                "id": str(r.fact.id),
                "kind": r.fact.kind,
                "content": r.fact.content,
                "reason": r.stale,
            }
            for r in rec.stale_facts
        ],
        "data_source": rec.data_source,
        "attribution": _attribution(rec.data_source),
        "partial": not graph_ok,
        "mode": mode,
        "run": recorder.snapshot(),
    }


def _browse_json(result: BrowseResult, mode: Mode, recorder: RunRecorder) -> dict[str, Any]:
    """The same shape as a recommendation, with no pick, no runners-up, and no intent."""
    assert result.places is not None
    return {
        "query": {
            "mode": "browse",
            "latitude": result.query.lat,
            "longitude": result.query.lon,
            "radius_m": result.query.radius_m,
            "request": None,
        },
        "intent": None,
        "pick": None,
        "runners_up": [],
        "places": [_place_json(r, i) for i, r in enumerate(result.places, start=1)],
        "candidate_count": len(result.places),
        "truncated": result.truncated,
        "explanation": "The places nearest to you come first. Makan made no recommendation.",
        "greeting": None,
        "warnings": list(result.warnings),
        "stale_facts": [],
        "data_source": result.data_source,
        "attribution": _attribution(result.data_source),
        "partial": False,
        "mode": mode,
        "run": recorder.snapshot(),
    }
