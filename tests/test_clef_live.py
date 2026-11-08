"""The explicit live Clef runner, exercised offline with synthetic envelopes only."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import httpx
import pytest

from makan.config import CLEF_FIT_MODEL
from makan.evals import clef_live
from makan.providers.scoring.clef import ClefScorer
from makan.providers.scoring.transport import CloudflareTransport

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "tests" / "fixtures" / "clef" / "live"
ACCOUNT = "a" * 32
TOKEN = "synthetic-secret-token-value"


def contract() -> dict[str, Any]:
    return clef_live._resource("contract.json")  # type: ignore[no-any-return]


def dual_response(body: dict[str, Any]) -> dict[str, Any]:
    """Synthetic native success echoing the request's question order; not a recording."""
    criteria = contract()["questions"]["fit_score"]["criteria"]
    ordinal = {"0": 0.0, "1": 0.0, "2": 0.1, "3": 0.2, "4": 0.7}
    answers = {
        "taste_fit": {
            "type": "choice",
            "choice": "strong",
            "probabilities": {"strong": 0.8, "partial": 0.1, "poor": 0.05, "unknown": 0.05},
            "confidence": 0.6,
        },
        "fit_score": {
            "type": "score",
            "legend": {str(i): text for i, text in enumerate(criteria)},
            "probabilities": ordinal,
            "score": math.fsum(int(k) * v for k, v in ordinal.items()),
            "confidence": 0.2,
        },
    }
    return {
        "success": True,
        "errors": [],
        "messages": [],
        "result": {
            "model": "clef-flash",
            "answers": {key: answers[key] for key in body["questions"]},
            "usage": {"input_tokens": 400, "output_tokens": 0},
        },
    }


def single_response(body: dict[str, Any]) -> dict[str, Any]:
    criteria = body["questions"]["fit_score"]["criteria"]
    ordinal = {"0": 0.1, "1": 0.6, "2": 0.1, "3": 0.1, "4": 0.1}
    return {
        "success": True,
        "errors": [],
        "messages": [],
        "result": {
            "model": "clef-flash",
            "answers": {
                "fit_score": {
                    "type": "score",
                    "legend": {str(i): text for i, text in enumerate(criteria)},
                    "probabilities": ordinal,
                    "score": math.fsum(int(k) * v for k, v in ordinal.items()),
                    "confidence": 0.3,
                }
            },
            "usage": {"input_tokens": 290, "output_tokens": 0},
        },
    }


class Wire:
    """Answers each request in turn; a callable may override one by its index."""

    def __init__(self, override: dict[int, httpx.Response] | None = None, neurons: str = "1.50"):
        self.requests: list[httpx.Request] = []
        self.override = override or {}
        self.neurons = neurons

    def __call__(self, request: httpx.Request) -> httpx.Response:
        index = len(self.requests)
        self.requests.append(request)
        if index in self.override:
            return self.override[index]
        body = json.loads(request.content)
        payload = dual_response(body) if "taste_fit" in body["questions"] else single_response(body)
        return httpx.Response(
            200,
            content=json.dumps(payload),
            headers={
                "cf-ai-neurons": self.neurons,
                "cf-ai-req-id": "0123456789abcdef",
                "api-version": "2026-10-01.epoch",
            },
        )


def run(
    tmp_path: Path,
    wire: Wire,
    jobs: list[clef_live.Job],
    budget: clef_live.Budget,
    *,
    smoke: bool,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    printed: list[str] = []
    ledger = tmp_path / "ledger.jsonl"

    def factory(client: httpx.Client) -> CloudflareTransport:
        return CloudflareTransport(ACCOUNT, TOKEN, CLEF_FIT_MODEL, client=client)

    reason = clef_live.run_jobs(
        jobs,
        ledger,
        budget,
        factory,
        smoke=smoke,
        sleep=lambda _: None,
        out=printed.append,
        http_transport=httpx.MockTransport(wire),
    )
    return reason, clef_live._read_ledger(ledger), printed


def smoke_jobs() -> list[clef_live.Job]:
    return clef_live.smoke_jobs(ClefScorer(None, CLEF_FIT_MODEL))


def test_smoke_sends_two_cases_and_one_identical_repeat(tmp_path: Path) -> None:
    wire = Wire()
    reason, rows, printed = run(
        tmp_path, wire, smoke_jobs(), clef_live.Budget(3, 15, 5), smoke=True
    )
    assert reason == "completed"
    assert [r["job"] for r in rows] == ["smoke:S1", "smoke:S2", "smoke:S1:repeat"]
    assert wire.requests[0].content == wire.requests[2].content
    assert rows[0]["request_sha256"] == rows[2]["request_sha256"]
    assert all(r["outcome"] == "ok" and r["neurons"] == 1.5 for r in rows)
    assert all(r["api_version"] == "2026-10-01.epoch" for r in rows)
    assert rows[0]["assessment"] == {"score": 90, "reason": "model_estimate"}
    summary = clef_live.summarize(rows)
    assert summary["smoke"]["repeat_identical"] is True
    assert summary["usage_all"]["neurons_measured"] == 4.5
    text = (tmp_path / "ledger.jsonl").read_text() + "\n".join(printed)
    assert TOKEN not in text and ACCOUNT not in text and "0123456789abcdef" not in text


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (401, {"success": False, "errors": [{"code": 10000, "message": "x"}]}, "HTTP 401"),
        (402, {"success": False, "errors": [{"code": 1, "message": "x"}]}, "HTTP 402"),
        (403, {"success": False, "errors": [{"code": 10000, "message": "x"}]}, "HTTP 403"),
        (429, {"success": False, "errors": [{"code": 3036, "message": "x"}]}, "rate or quota"),
        (
            400,
            {"success": False, "errors": [{"code": 5, "message": "Please upgrade your plan"}]},
            "billing",
        ),
        (200, {"success": True, "result": {"model": "other"}}, "unexpected model or schema"),
        (200, {"not": "a clef answer"}, "unexpected model or schema"),
    ],
)
def test_qualification_stops_on_auth_billing_quota_and_schema_errors(
    tmp_path: Path, status: int, payload: dict[str, Any], expected: str
) -> None:
    wire = Wire({0: httpx.Response(status, content=json.dumps(payload))})
    reason, rows, _ = run(tmp_path, wire, smoke_jobs(), clef_live.Budget(3, 15), smoke=False)
    assert expected in reason
    assert len(wire.requests) == 1 and len(rows) == 1
    assert rows[0]["http_status"] == status
    assert "upgrade" not in json.dumps(rows[0]) and '"x"' not in json.dumps(rows[0])


def test_billing_message_on_a_success_still_stops(tmp_path: Path) -> None:
    body = smoke_jobs()[0].body
    payload = {**dual_response(body), "messages": [{"code": 9, "message": "Add a payment method"}]}
    wire = Wire(
        {0: httpx.Response(200, content=json.dumps(payload), headers={"cf-ai-neurons": "1"})}
    )
    reason, rows, _ = run(tmp_path, wire, smoke_jobs(), clef_live.Budget(3, 15), smoke=False)
    assert "billing" in reason and len(wire.requests) == 1
    assert rows[0]["response"]["messages"] == 1  # count only, never the text


def test_smoke_stops_on_a_call_above_five_neurons(tmp_path: Path) -> None:
    wire = Wire(neurons="5.01")
    reason, _, _ = run(tmp_path, wire, smoke_jobs(), clef_live.Budget(3, 15, 5), smoke=True)
    assert "above 5" in reason and len(wire.requests) == 1


def test_smoke_stops_on_any_failure_and_missing_usage(tmp_path: Path) -> None:
    wire = Wire({0: httpx.Response(503, content="{}")})
    reason, _, _ = run(tmp_path, wire, smoke_jobs(), clef_live.Budget(3, 15, 5), smoke=True)
    assert reason == "smoke failure: service_unavailable" and len(wire.requests) == 1
    body = smoke_jobs()[0].body
    wire = Wire({0: httpx.Response(200, content=json.dumps(dual_response(body)))})
    reason, rows, _ = run(
        tmp_path / "b", wire, smoke_jobs(), clef_live.Budget(3, 15, 5), smoke=True
    )
    assert "usage header missing" in reason
    assert rows[0]["charged_neurons"] == rows[0]["reserved_neurons"]


def test_caps_stop_before_dispatch(tmp_path: Path) -> None:
    wire = Wire()
    reason, rows, _ = run(tmp_path, wire, smoke_jobs(), clef_live.Budget(3, 0.5), smoke=False)
    assert "Neuron cap" in reason and not wire.requests and not rows
    reason, rows, _ = run(tmp_path, wire, smoke_jobs(), clef_live.Budget(2, 15), smoke=False)
    assert reason == "request cap reached" and len(wire.requests) == 2


def test_resume_never_resends_a_recorded_failure(tmp_path: Path) -> None:
    wire = Wire({0: httpx.Response(504, content="{}")})
    run(tmp_path, wire, smoke_jobs(), clef_live.Budget(1, 60), smoke=False)
    wire2 = Wire()
    reason, rows, _ = run(tmp_path, wire2, smoke_jobs(), clef_live.Budget(3, 60), smoke=False)
    assert reason == "completed"
    assert [r["job"] for r in rows] == ["smoke:S1", "smoke:S2", "smoke:S1:repeat"]
    assert rows[0]["outcome"] == "service_unavailable"
    assert len(wire2.requests) == 2  # the failed job counted against the caps, never resent


def test_three_consecutive_failures_stop_qualification(tmp_path: Path) -> None:
    wire = Wire({i: httpx.Response(500, content="{}") for i in range(5)})
    jobs = clef_live.qualification_jobs(ClefScorer(None, CLEF_FIT_MODEL))
    reason, rows, _ = run(tmp_path, wire, jobs, clef_live.Budget(200, 500), smoke=False)
    assert reason == "three consecutive failures" and len(rows) == 3


def test_qualification_plan_is_preregistered_and_matches_the_archive() -> None:
    scorer = ClefScorer(None, CLEF_FIT_MODEL)
    jobs = clef_live.qualification_jobs(scorer)
    lanes: dict[str, list[clef_live.Job]] = {}
    for job in jobs:
        lanes.setdefault(job.lane, []).append(job)
    assert {k: len(v) for k, v in lanes.items()} == {
        "control": 40,
        "dual": 40,
        "production": 24,
        "contract": 33,
        "control_repeat": 12,
        "dual_repeat": 12,
        "dual_reversed": 12,
    }
    assert len({job.id for job in jobs}) == len(jobs)
    archive = clef_live._archive()
    for job in lanes["control"]:
        assert hashlib.sha256(clef_live.encode(job.body)).hexdigest() == archive[job.case][1]
    for job in lanes["dual"]:
        assert list(job.body["questions"]) == ["taste_fit", "fit_score"]
        assert json.loads(job.body["state"]).keys() == {"diner", "place"}
    for job in lanes["dual_reversed"]:
        assert list(job.body["questions"]) == ["fit_score", "taste_fit"]
    for job in lanes["production"]:
        state = json.loads(job.body["state"])
        assert job.body == scorer.profile_body(state)  # the product's exact bytes
        assert len(clef_live.encode(job.body)) < 8192
    patterns = [(j.meta["pattern"], j.meta["local_reason"]) for j in lanes["production"]]
    assert patterns.count(("like", "exact_like")) == 8
    assert patterns.count(("dislike", "exact_dislike")) == 8
    assert patterns.count(("unrelated", "no_exact_category_match")) == 8
    for job in lanes["contract"]:
        assert "allerg" not in job.body["state"].lower()


def test_reservation_bounds_every_planned_request_far_below_the_guard() -> None:
    jobs = clef_live.qualification_jobs(ClefScorer(None, CLEF_FIT_MODEL))
    largest = max(clef_live.reservation(len(clef_live.encode(j.body))) for j in jobs)
    assert largest < 12 and clef_live.reservation(8192) < clef_live.GATEWAY_CEILING_NEURONS
    smoke = [clef_live.reservation(len(clef_live.encode(j.body))) for j in smoke_jobs()]
    # Three worst-case smoke reservations fit the smoke ceiling even before any is settled.
    assert math.fsum(smoke) <= clef_live.SMOKE_MAX_NEURONS


@pytest.mark.parametrize(
    ("argv", "environ", "message"),
    [
        (["account"], {"CLOUDFLARE_ACCOUNT_ID": ACCOUNT, "CLOUDFLARE_API_TOKEN": TOKEN}, "--live"),
        (["account", "--live"], {}, "must be set"),
        (
            ["account", "--live"],
            {"CI": "true", "CLOUDFLARE_ACCOUNT_ID": ACCOUNT, "CLOUDFLARE_API_TOKEN": TOKEN},
            "CI",
        ),
        (
            ["smoke", "--live", "--ledger", "x"],
            {"CLOUDFLARE_ACCOUNT_ID": ACCOUNT, "CLOUDFLARE_API_TOKEN": TOKEN},
            "required",
        ),
        (
            ["smoke", "--live", "--max-requests", "4", "--max-neurons", "5", "--ledger", "x"],
            {"CLOUDFLARE_ACCOUNT_ID": ACCOUNT, "CLOUDFLARE_API_TOKEN": TOKEN},
            "smoke caps",
        ),
        (
            ["qualify", "--live", "--max-requests", "9", "--max-neurons", "999", "--ledger", "x"],
            {"CLOUDFLARE_ACCOUNT_ID": ACCOUNT, "CLOUDFLARE_API_TOKEN": TOKEN},
            "may not exceed",
        ),
        (
            ["qualify", "--live", "--max-requests", "9", "--max-neurons", "9", "--ledger", "x"],
            {"VERCEL": "1", "CLOUDFLARE_ACCOUNT_ID": ACCOUNT, "CLOUDFLARE_API_TOKEN": TOKEN},
            "VERCEL",
        ),
    ],
)
def test_live_commands_refuse_without_explicit_flag_credentials_and_caps(
    argv: list[str], environ: dict[str, str], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no client may be created")

    monkeypatch.setattr(httpx, "Client", no_network)
    with pytest.raises(clef_live.Refused) as raised:
        clef_live.main(argv, environ)
    assert message in str(raised.value) and TOKEN not in str(raised.value)


def test_account_probe_prints_only_status_and_plan_fields() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/subscriptions"):
            result: Any = [
                {
                    "rate_plan": {"id": "free", "public_name": "Workers Free"},
                    "price": 0,
                    "currency": "USD",
                    "frequency": "monthly",
                    "state": "Paid",
                    "id": "secret-sub",
                }
            ]
        elif path.endswith("/billing/profile"):
            result = {"card_number": "xxxx-1234", "first_name": "Private"}
        elif path == f"/client/v4/accounts/{ACCOUNT}":
            result = {"type": "standard", "name": "Private Account Name"}
        else:
            return httpx.Response(403, json={"success": False, "errors": [{"code": 10000}]})
        return httpx.Response(200, json={"success": True, "errors": [], "result": result})

    printed: list[str] = []
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        rows = clef_live.account_probe(ACCOUNT, TOKEN, client, printed.append)
    text = "\n".join(printed)
    assert ACCOUNT not in text and TOKEN not in text
    assert "Private" not in text and "xxxx" not in text and "secret-sub" not in text
    by_name = {row["probe"]: row for row in rows}
    assert by_name["subscriptions"]["plans"][0]["rate_plan"] == "free"
    assert by_name["billing_profile"]["has_card"] is True
    assert by_name["account"]["type"] == "standard"
    assert by_name["memberships"]["http_status"] == 403


def test_importing_the_runner_is_inert() -> None:
    import importlib

    module = importlib.reload(clef_live)
    assert not [name for name in vars(module) if isinstance(vars(module)[name], httpx.Client)]


@pytest.mark.parametrize("name", ["smoke-2026-11-08", "qualification-2026-11-08"])
def test_recorded_ledgers_reproduce_their_committed_summaries(name: str) -> None:
    rows = clef_live._read_ledger(LIVE / f"{name}.jsonl")
    committed = json.loads((LIVE / f"{name}.summary.json").read_text("utf-8"))
    assert json.loads(json.dumps(clef_live.summarize(rows))) == committed


def test_a_ledger_stopped_before_the_repeat_lanes_fails_their_gates_instead_of_raising() -> None:
    rows = clef_live._read_ledger(LIVE / "qualification-2026-11-08.jsonl")
    stopped = [
        r for r in rows if r["lane"] not in ("control_repeat", "dual_repeat", "dual_reversed")
    ]
    gates = clef_live.summarize(stopped)["gates"]
    assert gates["Q4_repeatability"] is False
    assert gates["Q6_question_order"] is False
    full = clef_live.summarize(rows)["gates"]
    assert gates["Q5_question_interference"] == full["Q5_question_interference"]


def test_a_failed_dual_call_skips_its_reversed_row_and_the_interference_gate_fails() -> None:
    rows = clef_live._read_ledger(LIVE / "qualification-2026-11-08.jsonl")
    reversed_cases = {r["case"] for r in rows if r["lane"] == "dual_reversed"}
    failed = [
        {**r, "outcome": "rate_limited"}
        if r["lane"] == "dual" and r["case"] in reversed_cases
        else r
        for r in rows
    ]
    summary = clef_live.summarize(failed)
    assert "dual_reversed" not in summary
    assert summary["gates"]["Q1_availability"] is False
    assert summary["gates"]["Q6_question_order"] is False

    no_dual = [r for r in rows if r["lane"] not in ("dual", "dual_repeat", "dual_reversed")]
    gates = clef_live.summarize(no_dual)["gates"]
    assert gates["Q4_repeatability"] is False
    assert gates["Q5_question_interference"] is False


def test_recorded_live_responses_pass_the_product_parser_unchanged() -> None:
    """Real sanitized dual-question bodies, validated by the exact production parser."""
    scorer = ClefScorer(None, CLEF_FIT_MODEL)
    rows = [
        row
        for name in ("smoke-2026-11-08", "qualification-2026-11-08")
        for row in clef_live._read_ledger(LIVE / f"{name}.jsonl")
    ]
    dual = [row for row in rows if row["kind"] == "dual"]
    assert len(dual) == 124 and all(row["outcome"] == "ok" for row in rows)
    for row in dual:
        result = scorer.parse_profile_response({**row["response"], "errors": [], "messages": []})
        assert result.choice.status.value == "ok" and result.ordinal is not None
        assert list(result.ordinal.probabilities) == pytest.approx(row["fit_score"])
        assert result.choice.usage.prompt_tokens == row["input_tokens"]
    for row in rows:
        if row["kind"] == "single":
            data = {**row["response"], "errors": [], "messages": []}
            parsed = clef_live.validate_single(data, clef_live._resource("baseline.json"))
            assert parsed["fit_score"] == pytest.approx(row["fit_score"])


def test_recorded_ledgers_hold_no_credentials_or_provider_text() -> None:
    import re

    for path in LIVE.glob("*.jsonl"):
        text = path.read_text("utf-8")
        assert not re.search(r"\b[0-9a-f]{32}\b", text)  # no account-id shaped value
        assert "Bearer" not in text and "Authorization" not in text
        assert "api.cloudflare.com" not in text and "cf-ray" not in text
        for row in map(json.loads, text.splitlines()):
            assert set(row["response"]) == {"errors", "messages", "result", "success"}
            assert row["response"]["errors"] == 0 and row["response"]["messages"] == 0


# State version 2 requalification lane, offline only


def history_jobs() -> list[clef_live.Job]:
    return clef_live.history_jobs(ClefScorer(None, CLEF_FIT_MODEL))


def test_history_lane_is_fixed_production_built_and_fits_the_existing_caps() -> None:
    jobs = history_jobs()
    assert [j.id for j in jobs] == [j.id for j in history_jobs()]
    assert [clef_live.encode(j.body) for j in jobs] == [
        clef_live.encode(j.body) for j in history_jobs()
    ]
    lanes = {j.lane for j in jobs}
    assert lanes == {"history", "history_plain", "history_repeat", "history_reversed"}
    v1 = json.loads((ROOT / "src/makan/evals/sets/clef/state.schema.json").read_text())
    for job in jobs:
        state = json.loads(job.body["state"])
        keys = (
            {"profile", "history", "candidate"}
            if job.lane != "history_plain"
            else set(v1["required"])
        )
        assert set(state) == keys
        assert len(clef_live.encode(job.body)) < 8192
    by_job = {j.id: j for j in jobs}
    for case in clef_live.HISTORY_REPEAT_CASES:
        assert by_job[f"history_repeat:{case}"].body == by_job[f"history:{case}"].body
        reversed_body = by_job[f"history_reversed:{case}"].body
        assert list(reversed_body["questions"]) == ["fit_score", "taste_fit"]
        assert reversed_body["state"] == by_job[f"history:{case}"].body["state"]
    reserved = math.fsum(clef_live.reservation(len(clef_live.encode(j.body))) for j in jobs)
    assert reserved <= clef_live.QUALIFY_MAX_NEURONS
    # Only made-up data: no typed dish text, place names, ids or dates in any state.
    text = " ".join(j.body["state"] for j in jobs)
    assert "Synthetic" not in text and "syn-" not in text and "2026-1" not in text


def test_history_lane_runs_offline_and_summarizes_against_version_1(tmp_path: Path) -> None:
    jobs = history_jobs()
    reason, rows, _ = run(tmp_path, Wire(), jobs, clef_live.Budget(len(jobs), 600, 85), smoke=False)
    assert reason == "completed" and len(rows) == len(jobs)
    summary = clef_live.summarize(rows)["history"]
    assert summary["directional_cases"] == 18
    # The synthetic wire always answers strong, so every expected-low case contradicts.
    assert len(summary["direction_contradictions"]) == 9
    assert set(summary["versus_version_1"]) == {
        r["case"] for r in rows if r["lane"] == "history_plain"
    }
    assert summary["gates"].startswith("not pre-registered")
    full = clef_live.summarize(rows)
    assert full["history_repeat"]["max_fit_score_shift"] == 0
    assert full["history_reversed"]["cases"] == len(clef_live.HISTORY_REPEAT_CASES)
    assert "gates" not in full


CAPPED = ["qualify-history", "--live", "--max-requests", "9", "--max-neurons", "9"]


def test_qualify_history_needs_the_same_explicit_live_flag_and_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no client may be created")

    monkeypatch.setattr(httpx, "Client", no_network)
    environ = {"CLOUDFLARE_ACCOUNT_ID": ACCOUNT, "CLOUDFLARE_API_TOKEN": TOKEN}
    for argv, message in (
        (["qualify-history", "--ledger", "x"], "--live"),
        (["qualify-history", "--live", "--ledger", "x"], "required"),
        (
            [*CAPPED[:4], "--max-neurons", "601", "--ledger", "x"],
            "may not exceed",
        ),
    ):
        with pytest.raises(clef_live.Refused, match=message):
            clef_live.main(argv, environ)
    with pytest.raises(clef_live.Refused, match="CI"):
        clef_live.main([*CAPPED, "--ledger", "x"], {**environ, "CI": "true"})
