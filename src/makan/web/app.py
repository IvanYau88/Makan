"""The HTTP API for the web channel.

One endpoint turns a location and a request into a recommendation, and everything else is
plumbing around `makan.solo.recommend`. No account is needed: the session it creates has one
participant, is never shared, and is not stored.

Run it with `uvicorn --factory makan.web:create_app_from_env`. See the README for the run modes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.exceptions import HTTPException as StarletteHTTPException

from makan.config import Config, ConfigError
from makan.env import load_dotenv
from makan.places.base import MAX_RADIUS_M, MIN_RADIUS_M, PlaceQuery, PlacesProvider
from makan.places.factory import places_provider
from makan.providers.base import Provider
from makan.providers.openrouter import OpenRouterProvider
from makan.providers.scoring import Scorer, build_scorer
from makan.sessions import GroupSessions, InMemorySessionStore
from makan.sessions.service import MAX_REQUEST_CHARS
from makan.solo import RankedCandidate, Recommendation, SoloRequest, SoloResult, recommend
from makan.web.demo import DemoPlaces, DemoProvider
from makan.web.errors import error, public_warnings, workflow_failure
from makan.web.groups import register_group_routes
from makan.web.runs import Outcome, RunRecorder

log = logging.getLogger("makan.web")

Mode = Literal["demo", "live"]
DEFAULT_WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


class RecommendBody(BaseModel):
    """What the browser sends. There is deliberately no user id: guests are the only caller."""

    model_config = ConfigDict(extra="forbid")

    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    request: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_REQUEST_CHARS)
    ]
    radius_m: Annotated[int, Field(ge=MIN_RADIUS_M, le=MAX_RADIUS_M)] = 1000


def create_app(
    *,
    provider: Provider,
    places: PlacesProvider,
    config: Config,
    mode: Mode = "live",
    static_dir: Path | None = None,
    sessions: GroupSessions | None = None,
    scorer: Scorer | None = None,
) -> FastAPI:
    """Build the app around ready providers. Tests pass fakes, and the CLI passes real ones.

    A `scorer` adds soft request signals to solo recommendations. Without one there are none.

    Group sessions are kept in `sessions`, or in memory for the life of the process when it is None.
    """
    app = FastAPI(
        title="Makan", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json"
    )
    background: set[asyncio.Future[None]] = set()  # keeps streamed runs alive until they end

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = ", ".join(
            ".".join(str(part) for part in e["loc"][1:]) or "body" for e in exc.errors()
        )
        return error(422, "invalid_request", f"Check these fields: {fields}.")

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
        }

    def run_solo(
        body: RecommendBody, on_update: Callable[[dict[str, Any]], None] | None = None
    ) -> tuple[SoloResult, RunRecorder]:
        """Run the workflow with a recorder on its trace. Synchronous, so call it off the loop."""
        request = SoloRequest(body.latitude, body.longitude, body.request, body.radius_m)
        center = PlaceQuery.near(body.latitude, body.longitude, body.radius_m)
        recorder = RunRecorder(
            mode=mode,
            config=config,
            request=body.request,
            latitude=center.lat,
            longitude=center.lon,
            radius_m=center.radius_m,
            data_source=places.name,
            on_update=on_update,
        )
        result = recommend(
            request,
            provider=provider,
            places=places,
            config=config,
            sink=recorder,
            scorer=scorer,
        )
        recorder.finish(_outcome(result))
        return result, recorder

    @app.post("/api/recommendations")
    async def recommendations(body: RecommendBody) -> JSONResponse:
        # The workflow is synchronous and runs its own event loop, so it must leave ours.
        try:
            result, recorder = await run_in_threadpool(run_solo, body)
        except Exception:
            log.exception("recommendation crashed")
            return error(500, "server_error", "Something went wrong on our side. Try again.")
        if result.recommendation is None:
            return workflow_failure(result.graph)
        return JSONResponse(
            _recommendation_json(
                result.recommendation, result.graph.ok, mode, _query_json(body, result), recorder
            )
        )

    @app.post("/api/recommendations/stream")
    async def recommendations_stream(body: RecommendBody) -> StreamingResponse:
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
                result, recorder = run_solo(body, lambda run: push({"type": "run", "run": run}))
                if result.recommendation is None:
                    failure = workflow_failure(result.graph)
                    push(
                        {
                            "type": "error",
                            "status": failure.status_code,
                            **json.loads(bytes(failure.body)),
                            "run": recorder.snapshot(),
                        }
                    )
                else:
                    push(
                        {
                            "type": "result",
                            "recommendation": _recommendation_json(
                                result.recommendation,
                                result.graph.ok,
                                mode,
                                _query_json(body, result),
                                recorder,
                            ),
                        }
                    )
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
        _default_sessions(config) if sessions is None else sessions,
        provider=provider,
        places=places,
        config=config,
        mode=mode,
    )

    if static_dir is not None and static_dir.is_dir():
        # Registered last so the API routes win. `html=True` serves index.html at "/".
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return app


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
    if env.get("MAKAN_DEMO", "").strip().lower() in ("1", "true", "yes", "on"):
        config = Config.from_env({**env, "MAKAN_MODEL": env.get("MAKAN_MODEL", "") or "demo"})
        return create_app(
            provider=DemoProvider(),
            places=DemoPlaces(),
            config=config,
            mode="demo",
            static_dir=dist,
            sessions=_default_sessions(config),  # demo mode never needs a database
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
    )


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


def _query_json(body: RecommendBody, result: SoloResult) -> dict[str, Any]:
    """The search as it ran: the rounded center the places were found around, not the raw input."""
    context = result.session.context
    return {
        "latitude": context["latitude"],
        "longitude": context["longitude"],
        "radius_m": context["radius_m"],
        "request": body.request,
    }


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
        "attribution": (
            "Places data: Overture Maps Foundation (CDLA Permissive 2.0)."
            if rec.data_source.startswith("overture:")
            else None
        ),
        "partial": not graph_ok,
        "mode": mode,
        "run": recorder.snapshot(),
    }
