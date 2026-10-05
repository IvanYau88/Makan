"""The HTTP API for the web channel.

One endpoint turns a location and a request into a recommendation, and everything else is
plumbing around `makan.solo.recommend`. No account is needed: the session it creates has one
participant, is never shared, and is not stored.

Run it with `uvicorn --factory makan.web:create_app_from_env`. See the README for the run modes.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from makan.config import Config, ConfigError
from makan.graph import GraphResult
from makan.places.base import MAX_RADIUS_M, MIN_RADIUS_M, PlacesProvider
from makan.places.factory import places_provider
from makan.providers.base import Provider
from makan.providers.openrouter import OpenRouterProvider
from makan.solo import RankedCandidate, Recommendation, SoloRequest, recommend
from makan.web.demo import DemoPlaces, DemoProvider

log = logging.getLogger("makan.web")

Mode = Literal["demo", "live"]
MAX_REQUEST_CHARS = 500
_FAILED_STEP = re.compile(r"^(requested_places|nearby_places|Memory) (error|timeout):")
DEFAULT_WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


class RecommendBody(BaseModel):
    """What the browser sends. There is deliberately no user id: guests are the only caller."""

    model_config = ConfigDict(extra="forbid")

    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    request: Annotated[str, Field(min_length=1, max_length=MAX_REQUEST_CHARS)]
    radius_m: Annotated[int, Field(ge=MIN_RADIUS_M, le=MAX_RADIUS_M)] = 1000


def create_app(
    *,
    provider: Provider,
    places: PlacesProvider,
    config: Config,
    mode: Mode = "live",
    static_dir: Path | None = None,
) -> FastAPI:
    """Build the app around ready providers. Tests pass fakes, and the CLI passes real ones."""
    app = FastAPI(
        title="Makan", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json"
    )

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = ", ".join(
            ".".join(str(part) for part in e["loc"][1:]) or "body" for e in exc.errors()
        )
        return _error(422, "invalid_request", f"Check these fields: {fields}.")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, "http_error", str(exc.detail))

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "mode": mode}

    @app.post("/api/recommendations")
    async def recommendations(body: RecommendBody) -> JSONResponse:
        request = SoloRequest(body.latitude, body.longitude, body.request.strip(), body.radius_m)
        # The workflow is synchronous and runs its own event loop, so it must leave ours.
        try:
            result = await run_in_threadpool(
                recommend, request, provider=provider, places=places, config=config
            )
        except Exception:
            log.exception("recommendation crashed")
            return _error(500, "server_error", "Something went wrong on our side. Try again.")
        if result.recommendation is None:
            return _workflow_failure(result.graph)
        return JSONResponse(_recommendation_json(result.recommendation, result.graph.ok, mode))

    if static_dir is not None and static_dir.is_dir():
        # Registered last so the API routes win. `html=True` serves index.html at "/".
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return app


def create_app_from_env(env: Mapping[str, str] | None = None) -> FastAPI:
    """The uvicorn factory: demo mode when `MAKAN_DEMO` is set, otherwise real providers.

    Demo mode needs no key and makes no network call. Live mode needs `MAKAN_MODEL` and
    `OPENROUTER_API_KEY`, and fails at startup, not on the first request, when either is missing.
    """
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
        mode="live",
        static_dir=dist,
    )


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _workflow_failure(graph: GraphResult) -> JSONResponse:
    """Map a graph that produced no recommendation to the part that failed."""
    failed = {name: step for name, step in graph.results.items() if not step.ok}
    for name, step in failed.items():
        log.warning("step %s %s: %s", name, step.status, step.error)
    if {"classify", "intent"} & failed.keys():
        return _error(
            502, "provider_error", "The language model could not read your request. Try again."
        )
    if {"requested_places", "nearby_places"} <= failed.keys():
        timed_out = all(
            failed[n].status == "timeout" for n in ("requested_places", "nearby_places")
        )
        return _error(
            504 if timed_out else 502,
            "places_error",
            "Nearby places data is unavailable right now. Try again in a moment.",
        )
    return _error(500, "server_error", "Something went wrong on our side. Try again.")


def _place_json(ranked: RankedCandidate) -> dict[str, Any]:
    p = ranked.place
    return {
        "id": p.id,
        "name": p.name,
        "category": p.category,
        "distance_m": p.distance_m,
        "address": p.address,
        "reasons": list(ranked.reasons),
    }


def _public(warning: str) -> str:
    """Hide the internal step and exception text the workflow puts in some warnings."""
    if _FAILED_STEP.match(warning):
        return "Part of the search failed, so these results may be incomplete."
    return warning


def _recommendation_json(rec: Recommendation, graph_ok: bool, mode: Mode) -> dict[str, Any]:
    warnings = [_public(w) for w in rec.warnings]
    explanation = rec.explanation
    for raw, public in zip(rec.warnings, warnings, strict=True):
        explanation = explanation.replace(raw, public)
    return {
        "pick": _place_json(rec.pick) if rec.pick else None,
        "runners_up": [_place_json(r) for r in rec.runners_up],
        "explanation": explanation,
        "warnings": list(dict.fromkeys(warnings)),
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
    }
