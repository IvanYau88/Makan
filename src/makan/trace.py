"""Trace events: one structured, JSON-serializable record per step of a run.

Every event is `{"v", "run_id", "seq", "ts", "type", "data"}`, written as one JSON line.
`seq` orders events within a run. The `data` payload depends on `type` and is documented
next to the code that emits it, in `makan.loop` and `makan.graph`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class TraceEvent:
    run_id: str
    seq: int
    type: str
    data: dict[str, Any]
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "v": SCHEMA_VERSION,
            "run_id": self.run_id,
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def jsonable(value: Any) -> Any:
    """A JSON safe copy of `value`, with `repr` standing in for what JSON cannot hold.

    An object that defines `trace_summary()` is written as what that returns, so its author, not
    a generic walk over its fields, decides what a trace may hold.
    """
    try:
        return json.loads(json.dumps(value, default=_default, ensure_ascii=False))
    except (TypeError, ValueError):
        return repr(value)


def _default(value: Any) -> Any:
    summary = getattr(value, "trace_summary", None)
    return summary() if callable(summary) else repr(value)


class TraceSink(Protocol):
    def emit(self, event: TraceEvent) -> None: ...


class Emitter:
    """Numbers the events of one run and sends them to a sink. Call it with a type and data."""

    def __init__(self, run_id: str, sink: TraceSink) -> None:
        self._run_id = run_id
        self._sink = sink
        self._seq = 0

    def __call__(self, type: str, **data: Any) -> None:
        self._seq += 1
        self._sink.emit(TraceEvent(self._run_id, self._seq, type, data))


class NullSink:
    def emit(self, event: TraceEvent) -> None:
        pass


class ListSink:
    """Collects events in memory."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def emit(self, event: TraceEvent) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [e.type for e in self.events]


class JsonlSink:
    """Appends each event as one line of JSON, so a partial run is still readable."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: TraceEvent) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(event.to_json() + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read events back as dicts. This is the entry point a trace viewer builds on."""
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
