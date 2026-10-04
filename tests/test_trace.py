import json
from pathlib import Path

from makan.loop import run
from makan.providers import FakeProvider, ToolCall
from makan.providers.fake import call
from makan.trace import JsonlSink, TraceEvent, read_jsonl
from tests.helpers import ECHO


def test_event_serializes_to_one_json_line_with_a_schema_version() -> None:
    event = TraceEvent("r1", 3, "tool_call", {"name": "echo"}, ts="2026-01-01T00:00:00+00:00")
    assert json.loads(event.to_json()) == {
        "v": 1,
        "run_id": "r1",
        "seq": 3,
        "ts": "2026-01-01T00:00:00+00:00",
        "type": "tool_call",
        "data": {"name": "echo"},
    }
    assert "\n" not in event.to_json()


def test_a_run_written_to_jsonl_can_be_read_back(tmp_path: Path) -> None:
    path = tmp_path / "traces" / "run.jsonl"
    provider = FakeProvider(
        [call(ToolCall.of("echo", "c1", text="café")), call(ToolCall.of("finish", answer="done"))]
    )
    result = run("hi", provider=provider, model="m", tools=[ECHO], sink=JsonlSink(path))

    events = read_jsonl(path)
    assert events[0]["type"] == "run_start"
    assert events[-1]["type"] == "run_end"
    assert {e["run_id"] for e in events} == {result.run_id}
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert any(e["type"] == "tool_result" and e["data"]["output"] == "café" for e in events)
