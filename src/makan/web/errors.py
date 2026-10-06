"""Error responses and public wording shared by the web routes.

Exception text and internal step names stay in the server log, never in a response.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence

from fastapi.responses import JSONResponse

from makan.graph import GraphResult
from makan.providers.base import ProviderBusy

log = logging.getLogger("makan.web")

MODEL_BUSY_RETRY_SECONDS = 60
_FAILED_STEP = re.compile(r"^(requested_places|nearby_places|Memory) (error|timeout):")


def error(
    status: int, code: str, message: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status,
        headers=dict(headers or {}),
    )


def workflow_failure(graph: GraphResult) -> JSONResponse:
    """Map a graph that produced no recommendation to the part that failed."""
    failed = {name: step for name, step in graph.results.items() if not step.ok}
    for name, step in failed.items():
        log.warning("step %s %s: %s", name, step.status, step.error)
    if any(isinstance(step.exception, ProviderBusy) for step in failed.values()):
        return error(
            503,
            "model_busy",
            "The language model is busy right now. Try again in a minute.",
            {"Retry-After": str(MODEL_BUSY_RETRY_SECONDS)},
        )
    if "classify" in failed:
        return error(
            502, "provider_error", "The language model failed to answer. Try again in a moment."
        )
    if "intent" in failed:
        return error(
            502,
            "provider_error",
            "The language model gave an answer Makan could not use. Try again.",
        )
    searches = [n for n in ("requested_places", "nearby_places") if n in failed]
    # Browsing nearby is the one search, so it failing alone leaves nothing to show.
    if len(searches) == 2 or set(graph.results) == {"nearby_places"}:
        timed_out = all(failed[n].status == "timeout" for n in searches)
        return error(
            504 if timed_out else 502,
            "places_error",
            "Nearby places data is unavailable right now. Try again in a moment.",
        )
    return error(500, "server_error", "Something went wrong on our side. Try again.")


def public_warnings(warnings: Sequence[str], explanation: str) -> tuple[list[str], str]:
    """The warnings and explanation with the internal step and exception text hidden.

    A warning about a failed search or memory lookup becomes one plain sentence, in both places.
    The warnings come back without repeats.
    """
    public = [_public(w) for w in warnings]
    for raw, text in zip(warnings, public, strict=True):
        explanation = explanation.replace(raw, text)
    return list(dict.fromkeys(public)), explanation


def _public(warning: str) -> str:
    if _FAILED_STEP.match(warning):
        return "Part of the search failed, so these results may be incomplete."
    return warning
