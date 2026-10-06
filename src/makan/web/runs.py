"""The public view of one run: its graph stages, built from trace events and bounded in size.

`RunRecorder` is a `TraceSink` for one HTTP request. The browser gets its `snapshot`, never the raw
events, because raw events hold model text, the session link token, and exception text that the
web channel keeps out of responses. Stage inputs and outputs are chosen here by stage name, and
every string and list is capped, so the inspector shows structured data and nothing unbounded.

A run is held only for the request that made it, and the browser keeps what it wants in its own tab.
Nothing here is stored, so there is no history to read across users.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Literal

from makan.config import Config
from makan.trace import TraceEvent
from makan.web.errors import public_warnings

Outcome = Literal["complete", "partial", "no_result", "failed"]
StageStatus = Literal["waiting", "queued", "running", "ok", "error", "timeout", "skipped"]
SearchMode = Literal["recommend", "browse"]
Plan = Sequence[tuple[str, Sequence[str]]]  # every stage of the full graph, with what it follows

MAX_TEXT = 300
MAX_ITEMS = 5  # places previewed for a search stage
MAX_LIST = 10
MAX_DEPTH = 4
_TERMINAL = ("ok", "error", "timeout")
_STRUCTURAL = ("graph_start", "step_start", "step_finish", "step_error", "graph_end")
_UNKNOWN_FAILURE = "Something unexpected went wrong in this stage."
_FAILURES = {
    "ProviderBusy": "The language model was busy.",
    "ProviderError": "The language model failed to answer.",
    "AgentRunFailed": "The language model finished without an answer.",
    "PlacesError": "Nearby places data was unavailable.",
    "StepFailed": "A stage this one depends on did not finish.",
}
_OWN_TEXT = ("ValueError", "JSONDecodeError")  # raised with wording this code base wrote


class RunRecorder:
    """Collects the events of one run and builds its public snapshot. Safe across threads."""

    def __init__(
        self,
        *,
        mode: str,
        search_mode: SearchMode = "recommend",
        plan: Plan | None = None,
        config: Config,
        request: str | None,
        latitude: float,
        longitude: float,
        radius_m: int,
        data_source: str,
        on_update: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._meta = {
            "mode": mode,
            "search_mode": search_mode,
            "model": config.model,
            "request": request,
            "center": {"latitude": latitude, "longitude": longitude},
            "radius_m": radius_m,
            "data_source": data_source,
            "limits": {
                "max_concurrency": config.graph_limits.max_concurrency,
                "step_timeout_s": config.graph_limits.step_timeout_s,
                "max_iterations": config.limits.max_iterations,
                "token_budget": config.limits.token_budget,
            },
        }
        self._plan = plan
        self._on_update = on_update
        self._lock = threading.Lock()
        self._events: list[TraceEvent] = []
        self._outcome: Outcome | None = None

    def emit(self, event: TraceEvent) -> None:
        with self._lock:
            self._events.append(event)
        if self._on_update and event.type in _STRUCTURAL:
            self._on_update(self.snapshot())

    def finish(self, outcome: Outcome) -> dict[str, Any]:
        """Record the product outcome, which the graph status alone cannot say."""
        with self._lock:
            self._outcome = outcome
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)
            outcome = self._outcome
        return _snapshot(self._meta, events, outcome, self._plan)


def _snapshot(
    meta: dict[str, Any], events: list[TraceEvent], outcome: Outcome | None, plan: Plan | None
) -> dict[str, Any]:
    start = next((e for e in events if e.type == "graph_start"), None)
    if start is None:
        return {"run_id": None, "graph": None, "status": "running", **meta, "stages": []}
    end = next((e for e in events if e.type == "graph_end"), None)
    by_run: dict[str, list[TraceEvent]] = {}
    for e in events:
        by_run.setdefault(e.run_id, []).append(e)
    graph_events = by_run[start.run_id]
    t0 = _time(start)

    stages: list[dict[str, Any]] = []
    for spec in start.data["steps"]:
        name = spec["name"]
        began = _find(graph_events, "step_start", name)
        ended = _find(graph_events, "step_finish", name) or _find(graph_events, "step_error", name)
        status: StageStatus
        if ended:
            status = ended.data["status"]
        elif began:
            status = "running"
        else:
            deps = [_state(graph_events, d) for d in spec["after"]]
            status = "queued" if all(d in _TERMINAL for d in deps) else "waiting"
        children = [
            c for rid in (ended.data["child_runs"] if ended else []) for c in [by_run.get(rid, [])]
        ]
        stage: dict[str, Any] = {
            "name": name,
            "after": spec["after"],
            "status": status,
            "timeout_s": spec["timeout_s"],
            "started_ms": _ms_between(t0, _time(began)) if began else None,
            "duration_ms": ended.data["duration_ms"] if ended else None,
            "parallel_with": ended.data["parallel_with"] if ended else [],
            "input": None,
            "output": _bound(_output(name, ended.data.get("output")))
            if ended and ended.type == "step_finish"
            else None,
            "error": _failure(ended.data, spec["timeout_s"])
            if ended and ended.type == "step_error"
            else None,
            "calls": [c for events_ in children if (c := _call(events_))],
        }
        stages.append(stage)
    if plan is not None:
        stages = _with_skipped(stages, plan)
    _fill_inputs(stages, meta)

    ok = end.data["status"] == "ok" if end else None
    return {
        "run_id": start.run_id,
        "graph": start.data["graph"],
        "status": "running" if end is None else end.data["status"],
        "graph_ok": ok,
        "outcome": outcome,
        "started_at": start.ts,
        "ended_at": end.ts if end else None,
        "duration_ms": end.data["duration_ms"] if end else None,
        **meta,
        "stages": stages,
    }


def _with_skipped(stages: list[dict[str, Any]], plan: Plan) -> list[dict[str, Any]]:
    """The run's stages in the order of the full graph, each stage it did not run marked skipped.

    A search that needs only part of the graph (browse nearby) still shows the whole picture, so a
    stage that never ran reads as skipped and not as one that is waiting or failed.
    """
    ran = {s["name"]: s for s in stages}
    return [
        ran[name]
        if name in ran
        else {
            "name": name,
            "after": list(after),
            "status": "skipped",
            "timeout_s": 0,
            "started_ms": None,
            "duration_ms": None,
            "parallel_with": [],
            "input": None,
            "output": None,
            "error": None,
            "calls": [],
        }
        for name, after in plan
    ]


def _find(events: list[TraceEvent], kind: str, step: str) -> TraceEvent | None:
    return next((e for e in events if e.type == kind and e.data.get("step") == step), None)


def _state(events: list[TraceEvent], step: str) -> str:
    ended = _find(events, "step_finish", step) or _find(events, "step_error", step)
    return ended.data["status"] if ended else "pending"


def _time(event: TraceEvent) -> datetime:
    return datetime.fromisoformat(event.ts)


def _ms_between(a: datetime, b: datetime) -> int:
    return max(0, round((b - a).total_seconds() * 1000))


def _text(value: object, limit: int = MAX_TEXT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=repr)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _bound(value: Any, depth: int = 0) -> Any:
    """`value` with every string, list, and nesting level capped, so a stage can never be huge."""
    if isinstance(value, str):
        return _text(value)
    if depth >= MAX_DEPTH:
        return _text(value)
    if isinstance(value, dict):
        return {_text(k, 60): _bound(v, depth + 1) for k, v in list(value.items())[:MAX_LIST]}
    if isinstance(value, list):
        items = [_bound(v, depth + 1) for v in value[:MAX_LIST]]
        if len(value) > MAX_LIST:
            items.append(f"… {len(value) - MAX_LIST} more")
        return items
    return value


def _failure(data: dict[str, Any], timeout_s: float) -> dict[str, str]:
    """The failure in words safe to show: exception text stays on the server."""
    kind = str(data["error"]).split(":", 1)[0]
    if data["status"] == "timeout":
        return {
            "type": "TimeoutError",
            "message": f"Gave up after {timeout_s:g} s. The work is not cancelled and may go on.",
        }
    if kind in _OWN_TEXT:
        return {"type": kind, "message": _text(str(data["error"]).split(": ", 1)[-1], 160)}
    return {"type": kind, "message": _FAILURES.get(kind, _UNKNOWN_FAILURE)}


def _call(events: list[TraceEvent]) -> dict[str, Any] | None:
    """A model loop or direct tool call that a stage started, reduced to what an inspector shows."""
    types = [e.type for e in events]
    if "run_start" in types:
        responses = [e.data for e in events if e.type == "model_response"]
        end = next((e.data for e in events if e.type == "run_end"), None)
        return {
            "kind": "model",
            "status": end["status"] if end else "running",
            "model_calls": [
                {
                    "iteration": r["iteration"],
                    "duration_ms": r["duration_ms"],
                    "tokens": r["usage"]["prompt_tokens"] + r["usage"]["completion_tokens"],
                }
                for r in responses[:MAX_ITEMS]
            ],
            "iterations": end["iterations"] if end else len(responses),
        }
    call = next((e.data for e in events if e.type == "tool_call"), None)
    if call is None:
        return None
    result = next((e.data for e in events if e.type == "tool_result"), None)
    return {
        "kind": "tool",
        "name": call["name"],
        "arguments": call["arguments"],
        "ok": result["ok"] if result else None,
        "duration_ms": result["duration_ms"] if result else None,
    }


def _output(stage: str, output: Any) -> Any:
    """A bounded, structured copy of what a stage returned."""
    if stage == "classify":
        try:
            parsed = json.loads(output)
        except (TypeError, ValueError):
            return {"answer": _text(output)}
        return parsed if isinstance(parsed, dict) else {"answer": _text(output)}
    if stage in ("requested_places", "nearby_places"):
        try:
            found = json.loads(output)
            places = found["places"]
            return {
                "count": found["count"],
                "places": [
                    {k: p.get(k) for k in ("name", "category", "distance_m")}
                    for p in places[:MAX_ITEMS]
                ],
                "more": max(0, len(places) - MAX_ITEMS),
            }
        except (TypeError, ValueError, KeyError):
            return {"result": _text(output)}
    if stage == "merge" and isinstance(output, dict):
        warnings, _ = public_warnings(output.get("warnings", []), "")
        return {**output, "warnings": [_text(w) for w in warnings[:MAX_ITEMS]]}
    if stage == "rank" and isinstance(output, list) and len(output) == 2:
        ranked = output[1]
        return {
            "ranked": len(ranked),
            "top": [
                {"name": r["place"]["name"], "reasons": [_text(x) for x in r["reasons"]]}
                for r in ranked[:3]
            ],
        }
    if stage == "explain" and isinstance(output, dict):
        warnings, _ = public_warnings(output.get("warnings", []), "")
        return {**output, "warnings": [_text(w) for w in warnings[:MAX_ITEMS]]}
    return output if isinstance(output, dict) else {"value": _text(output)}


def _fill_inputs(stages: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    """Each stage's inputs: what it was given, taken from the run's own record."""
    by_name = {s["name"]: s for s in stages}
    limits = meta["limits"]
    for stage in stages:
        name = stage["name"]
        if stage["status"] == "skipped":
            continue
        if name == "classify":
            stage["input"] = {
                "request": _text(meta["request"]),
                "model": meta["model"],
                "max_iterations": limits["max_iterations"],
                "token_budget": limits["token_budget"],
            }
        elif name == "intent":
            stage["input"] = {"classification": by_name["classify"]["output"]}
        elif name in ("requested_places", "nearby_places"):
            tool = next((c for c in stage["calls"] if c["kind"] == "tool"), None)
            stage["input"] = (
                {"tool": tool["name"], **tool["arguments"]}
                if tool
                else {"center": meta["center"], "radius_m": meta["radius_m"]}
            )
        elif name == "memory":
            stage["input"] = {"user": "guest"}
        else:
            stage["input"] = {d: by_name[d]["status"] for d in stage["after"]}
        stage["input"] = _bound(stage["input"])
