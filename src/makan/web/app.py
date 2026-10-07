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
from dataclasses import dataclass
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
from makan.places.base import MAX_RADIUS_M, MIN_RADIUS_M, PlaceQuery, PlacesProvider
from makan.places.factory import places_provider
from makan.providers.base import Provider
from makan.providers.openrouter import OpenRouterProvider
from makan.providers.scoring import Scorer, build_scorer
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

log = logging.getLogger("makan.web")

Mode = Literal["demo", "live"]
DEFAULT_WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


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
    """
    group_sessions = _default_sessions(config) if sessions is None else sessions

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
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
        }

    app.state.accounts = accounts
    if accounts is not None:
        register_account_routes(app, accounts)

    # The stages of the full solo graph, so a browse run can show the ones it skipped.
    plan = tuple(
        (step.name, step.after)
        for step in build_solo_graph(
            provider=provider, places=places, config=config, scorer=scorer
        ).steps
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
        if body.mode == "browse":
            found = browse(
                BrowseRequest(body.latitude, body.longitude, body.radius_m),
                places=places,
                config=config,
                sink=recorder,
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
        recorder.finish(_outcome(result))
        if result.recommendation is None:
            return Answer(recorder, failure=workflow_failure(result.graph))
        payload = _recommendation_json(
            result.recommendation, result.graph.ok, mode, _query_json(body, result), recorder
        )
        return Answer(recorder, payload)

    @app.post("/api/recommendations")
    async def recommendations(
        body: RecommendBody, identity: Annotated[Identity | None, Depends(optional_identity)]
    ) -> JSONResponse:
        # The workflow is synchronous and runs its own event loop, so it must leave ours.
        try:
            answer = await run_in_threadpool(run_search, body, None, identity)
        except Exception:
            log.exception("recommendation crashed")
            return error(500, "server_error", "Something went wrong on our side. Try again.")
        if answer.failure is not None:
            return answer.failure
        return JSONResponse(answer.payload)

    @app.post("/api/recommendations/stream")
    async def recommendations_stream(
        body: RecommendBody, identity: Annotated[Identity | None, Depends(optional_identity)]
    ) -> StreamingResponse:
        """The same answer as one JSON object per line, led by the run as its stages progress.

        Lines are `{"type": "run", "run": ...}` while it works, then one last `result` line with
        the recommendation or an `error` line with the same code and message as the plain route.
        """
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
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    register_group_routes(
        app,
        group_sessions,
        provider=provider,
        places=places,
        config=config,
        mode=mode,
    )

    if static_dir is not None and static_dir.is_dir():
        index = static_dir / "index.html"

        @app.get("/g/{link_token}", include_in_schema=False, response_model=None)
        @app.get("/g/{link_token}/", include_in_schema=False, response_model=None)
        def group_page(link_token: str) -> FileResponse | JSONResponse:
            """A shared group link opens the same page. The page asks the API about the link."""
            if not index.is_file():
                return error(404, "not_found", "Not found.")
            return FileResponse(index, headers={"Cache-Control": "no-store"})

        # Registered last so the API routes win. `html=True` serves index.html at "/".
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return app


def demo_requested(env: Mapping[str, str]) -> bool:
    """Whether `MAKAN_DEMO` asks for sample places and no key or network."""
    return env.get("MAKAN_DEMO", "").strip().lower() in ("1", "true", "yes", "on")


def create_app_from_env(env: Mapping[str, str] | None = None) -> FastAPI:
    """The uvicorn factory: demo mode when `MAKAN_DEMO` is set, otherwise real providers.

    Demo mode needs no key and makes no network call. Live mode needs `MAKAN_MODEL` and
    `OPENROUTER_API_KEY`, and fails at startup, not on the first request, when either is missing.
    Called with no `env`, it first loads `.env` (see `makan.env`) into the real environment.
    """
    if env is None:
        load_dotenv()
    env = os.environ if env is None else env
    dist = Path(env.get("MAKAN_WEB_DIST", "").strip() or DEFAULT_WEB_DIST)
    if demo_requested(env):
        config = Config.from_env({**env, "MAKAN_MODEL": env.get("MAKAN_MODEL", "") or "demo"})
        return create_app(
            provider=DemoProvider(),
            places=DemoPlaces(),
            config=config,
            mode="demo",
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
        sessions=_group_sessions(config),
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
