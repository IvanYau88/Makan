"""Explicitly invoked live Clef checks: account probe, capped smoke and capped qualification.

Nothing imports this module from product code, CI, or build and deploy hooks, and importing it
opens no client. Every network subcommand needs `--live`, the server-side environment variables
`CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`, and refuses to run under CI or Vercel.
Smoke and qualification also need explicit request and Neuron caps, and never retry: a job with
any ledger row, including a failure, is never sent again, even on resume.

Only sanitized rows reach the ledger: synthetic inputs' hashes, HTTP status, Cloudflare error
codes, usage, latency, validated probabilities and the model's success body. Credentials, the
account URL, request ids, provider error text and exception text are never printed or stored.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import httpx

from makan.config import CLEF_FIT_MODEL
from makan.evals.contract_cases import load_taste_cases, render_taste_state
from makan.evals.fit import LEVEL_IDS, TASTE_IDS, parse_json, probabilities
from makan.fit import CATEGORY_CUISINES, cuisine_fit
from makan.profile_fit import accepted_assessment
from makan.providers.scoring.clef import ClefScorer, ProfileCaseResult
from makan.providers.scoring.transport import ClefError, ClefReason, CloudflareTransport

API = "https://api.cloudflare.com/client/v4"
NEURONS_PER_MILLION_INPUT_TOKENS = 3455  # pricing assumption, checked against cf-ai-neurons
TOKEN_OVERHEAD = 256  # template allowance; the 2026-10-23 run measured ~3 body bytes/token
GATEWAY_CEILING_NEURONS = 85.0  # the gateway's full-window reservation; no call may exceed it
SMOKE_MAX_CALL_NEURONS = 5.0
SMOKE_MAX_REQUESTS = 3
SMOKE_MAX_NEURONS = 30.0
QUALIFY_MAX_NEURONS = 600.0  # hard ceiling on any --max-neurons, well under the 1,000 guard
PACING_S = 0.5
OVERTURE_RELEASE = "2026-09-23.1"
REFUSE_ENV = ("CI", "GITHUB_ACTIONS", "VERCEL", "VERCEL_ENV", "NOW_BUILDER")
BILLING_WORDS = ("billing", "payment", "upgrade", "paid", "subscription", "credit card", "plan")
REPEAT_CASES = ("O01", "O02", "O03", "O04", "F01", "M01", "F02", "M02", "D02", "T02", "D03", "T03")
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 20261021


class Refused(SystemExit):
    """A precondition failed before any network call; the message names no secret."""


class Stop(Exception):
    """A stop rule fired; the run ends and later jobs stay unsent."""


@dataclass(frozen=True)
class Job:
    id: str
    lane: str
    case: str
    body: dict[str, Any] = field(repr=False)
    kind: str  # "single" (historical control) or "dual" (frozen same-case envelope)
    meta: dict[str, Any] = field(default_factory=dict)


def _clef_root() -> Any:
    return resources.files("makan.evals").joinpath("sets", "clef")


def _resource(name: str) -> Any:
    return parse_json(_clef_root().joinpath(name).read_text("utf-8"))


def encode(body: Mapping[str, Any]) -> bytes:
    """The exact bytes `CloudflareTransport.post` sends."""
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def reservation(size: int) -> float:
    """Upper bound in Neurons for one request of `size` body bytes, rounded up to 0.01."""
    return math.ceil((size + TOKEN_OVERHEAD) * NEURONS_PER_MILLION_INPUT_TOKENS / 1e4) / 100


# Case construction. All inputs are synthetic or frozen archive data; none is personal.


def _historical_state(case: Mapping[str, Any]) -> str:
    return json.dumps({"diner": case["diner"], "place": case["place"]}, ensure_ascii=False)


def control_body(case: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    """The archived single-question request, byte for byte (checked against its hash)."""
    return {
        "model": baseline["selector"],
        "state": _historical_state(case),
        "questions": baseline["questions"],
    }


def dual_questions(contract: Mapping[str, Any], *, reverse: bool = False) -> dict[str, Any]:
    items = [
        (key, {name: question[name] for name in ("type", "instructions", "criteria")})
        for key, question in contract["questions"].items()
    ]
    return dict(reversed(items) if reverse else items)


def dual_body(state: str, contract: Mapping[str, Any], *, reverse: bool = False) -> dict[str, Any]:
    """The frozen two-question envelope about one case, for a qualification state."""
    return {
        "model": contract["selector"],
        "state": state,
        "questions": dual_questions(contract, reverse=reverse),
    }


def production_state(
    likes: Sequence[str], dislikes: Sequence[str], category: str
) -> dict[str, Any]:
    """The same shape `makan.profile_fit.profile_state` builds for one place."""
    return {
        "profile": {"likes": sorted(likes), "dislikes": sorted(dislikes), "source": "user_stated"},
        "candidate": {
            "categories": [category],
            "source": "overture_category",
            "source_release": OVERTURE_RELEASE,
            "missing_facts": [],
        },
    }


CUISINES = tuple(CATEGORY_CUISINES.values())


def production_cases() -> list[dict[str, Any]]:
    """Pre-registered: for each cuisine, a stated like, a stated dislike and an unrelated like."""
    cases = []
    for index, cuisine in enumerate(CUISINES):
        other = CUISINES[(index + 1) % len(CUISINES)]
        category = f"{cuisine}_restaurant"
        for pattern, likes, dislikes in (
            ("like", [cuisine], []),
            ("dislike", [], [cuisine]),
            ("unrelated", [other], []),
        ):
            local = cuisine_fit(category, tuple(likes), tuple(dislikes), enabled=True)
            cases.append(
                {
                    "id": f"P-{cuisine}-{pattern}",
                    "pattern": pattern,
                    "state": production_state(likes, dislikes, category),
                    "local_score": local.score,
                    "local_reason": local.reason.value,
                }
            )
    return cases


SMOKE_CASES = (
    ("S1", production_state(["japanese"], [], "japanese_restaurant")),
    ("S2", production_state([], ["italian"], "italian_restaurant")),
)


def smoke_jobs(scorer: ClefScorer) -> list[Job]:
    """Two tiny synthetic production-shaped cases, then one identical repeat of the first."""
    jobs = [
        Job(f"smoke:{case}", "smoke", case, scorer.profile_body(state), "dual")
        for case, state in SMOKE_CASES
    ]
    case, state = SMOKE_CASES[0]
    jobs.append(Job(f"smoke:{case}:repeat", "smoke", case, scorer.profile_body(state), "dual"))
    return jobs


def qualification_jobs(scorer: ClefScorer) -> list[Job]:
    """Every lane, in a fixed pre-registered order; repeats and order probes come last."""
    baseline, contract = _resource("baseline.json"), _resource("contract.json")
    cases = _resource("cases.json")
    labels = _resource("labels.json")["cases"]
    families = {
        case_id: family
        for family, ids in _resource("families.json")["families"].items()
        for case_id in ids
    }
    by_id = {case["id"]: case for case in cases}
    jobs: list[Job] = []
    for case in cases:
        meta = {"label": labels[case["id"]]["level"], "family": families[case["id"]]}
        jobs.append(
            Job(
                f"control:{case['id']}",
                "control",
                case["id"],
                control_body(case, baseline),
                "single",
                meta,
            )
        )
    for case in cases:
        meta = {"label": labels[case["id"]]["level"], "family": families[case["id"]]}
        jobs.append(
            Job(
                f"dual:{case['id']}",
                "dual",
                case["id"],
                dual_body(_historical_state(case), contract),
                "dual",
                meta,
            )
        )
    for item in production_cases():
        meta = {k: item[k] for k in ("pattern", "local_score", "local_reason")}
        jobs.append(
            Job(
                f"production:{item['id']}",
                "production",
                item["id"],
                scorer.profile_body(item["state"]),
                "dual",
                meta,
            )
        )
    for taste in load_taste_cases():
        meta = {"label": taste.label, "split": taste.split, "tags": list(taste.tags)}
        jobs.append(
            Job(
                f"contract:{taste.id}",
                "contract",
                taste.id,
                dual_body(render_taste_state(taste), contract),
                "dual",
                meta,
            )
        )
    for case_id in REPEAT_CASES:
        case = by_id[case_id]
        meta = {"label": labels[case_id]["level"], "family": families[case_id]}
        jobs.append(
            Job(
                f"control_repeat:{case_id}",
                "control_repeat",
                case_id,
                control_body(case, baseline),
                "single",
                meta,
            )
        )
        jobs.append(
            Job(
                f"dual_repeat:{case_id}",
                "dual_repeat",
                case_id,
                dual_body(_historical_state(case), contract),
                "dual",
                meta,
            )
        )
        jobs.append(
            Job(
                f"dual_reversed:{case_id}",
                "dual_reversed",
                case_id,
                dual_body(_historical_state(case), contract, reverse=True),
                "dual",
                meta,
            )
        )
    return jobs


# Response validation, reusing the product parser for every dual-question response.


def validate_single(data: Any, baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the historical control response; raises ValueError on any schema problem."""
    if not isinstance(data, dict) or data.get("success") is not True or data.get("errors"):
        raise ValueError("unsuccessful response")
    result = data["result"]
    if result.get("model") != baseline["selector"]:
        raise ValueError("unexpected model")
    answers = result["answers"]
    if not isinstance(answers, dict) or set(answers) != {"fit_score"}:
        raise ValueError("unexpected answers")
    answer = answers["fit_score"]
    criteria = baseline["questions"]["fit_score"]["criteria"]
    if answer.get("type") != "score" or answer.get("legend") != dict(
        zip(LEVEL_IDS, criteria, strict=True)
    ):
        raise ValueError("unexpected score answer")
    probs = probabilities(answer["probabilities"], LEVEL_IDS)
    expectation = math.fsum(i * p for i, p in enumerate(probs))
    score = answer["score"]
    if isinstance(score, bool) or not isinstance(score, int | float):
        raise ValueError("invalid score")
    if abs(score - expectation) > 0.0006:
        raise ValueError("wire score disagrees with probabilities")
    usage = result["usage"]
    tokens = (usage["input_tokens"], usage["output_tokens"])
    if any(isinstance(t, bool) or not isinstance(t, int) or t < 0 for t in tokens):
        raise ValueError("invalid usage")
    return {"fit_score": list(probs), "input_tokens": tokens[0], "output_tokens": tokens[1]}


def sanitize_response(data: Any) -> dict[str, Any] | None:
    """Keep the model's success body only; provider errors and messages are reduced to counts."""
    if not isinstance(data, dict):
        return None
    kept = {key: data[key] for key in ("success", "result") if key in data}
    kept["errors"] = len(data.get("errors") or [])
    kept["messages"] = len(data.get("messages") or [])
    return kept


# Live execution.


@dataclass
class Observation:
    """Per-request facts captured from the response hook, never the body text or headers."""

    status: int | None = None
    error_codes: list[int] = field(default_factory=list)
    billing_hint: bool = False
    messages: int = 0
    api_version: str | None = None  # model identity evidence for canary comparisons
    data: Any = None


def _scan(response: httpx.Response, seen: Observation) -> None:
    response.read()
    seen.status = response.status_code
    version = response.headers.get("api-version", "")
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:\.[a-z]{1,16})?", version):
        seen.api_version = version
    try:
        data = json.loads(response.text)
    except ValueError:
        return
    seen.data = data
    if not isinstance(data, dict):
        return
    notes = [*(data.get("errors") or []), *(data.get("messages") or [])]
    seen.messages = len(data.get("messages") or [])
    for note in notes:
        if isinstance(note, dict):
            code = note.get("code")
            if isinstance(code, int) and not isinstance(code, bool):
                seen.error_codes.append(code)
            text = str(note.get("message", "")).lower()
            seen.billing_hint |= any(word in text for word in BILLING_WORDS)
    seen.billing_hint |= response.status_code == 402


def credentials(environ: Mapping[str, str]) -> tuple[str, str]:
    for name in REFUSE_ENV:
        if environ.get(name):
            raise Refused(f"refusing live Clef calls with {name} set (CI, build or deploy)")
    account, token = (
        environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
        environ.get("CLOUDFLARE_API_TOKEN", ""),
    )
    if not account or not token:
        raise Refused("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN must be set explicitly")
    return account, token


@dataclass
class Budget:
    max_requests: int
    max_neurons: float
    max_call_neurons: float = GATEWAY_CEILING_NEURONS
    requests: int = 0
    neurons: float = 0.0  # measured, or the full reservation when usage is uncertain

    def admit(self, size: int) -> float:
        reserve = reservation(size)
        if self.requests >= self.max_requests:
            raise Stop("request cap reached")
        if self.neurons + reserve > self.max_neurons + 1e-9:
            raise Stop("Neuron cap would be exceeded by this call's reservation")
        return reserve


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def run_jobs(
    jobs: Sequence[Job],
    ledger: Path,
    budget: Budget,
    transport_factory: Callable[[httpx.Client], CloudflareTransport],
    *,
    smoke: bool,
    pacing: float = PACING_S,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
    http_transport: httpx.BaseTransport | None = None,
) -> str:
    """Send each unsent job once, append a sanitized row per attempt, and stop on any rule."""
    baseline = _resource("baseline.json")
    scorer = ClefScorer(None, CLEF_FIT_MODEL)
    rows = _read_ledger(ledger)
    done = {row["job"] for row in rows}
    for row in rows:  # resumed runs keep counting earlier spend against the same caps
        budget.requests += 1
        budget.neurons += row["charged_neurons"]
    current = [Observation()]
    client = httpx.Client(
        transport=http_transport,
        follow_redirects=False,
        event_hooks={"response": [lambda response: _scan(response, current[0])]},
    )
    transport = transport_factory(client)
    consecutive_failures = 0
    last_start = 0.0
    ledger.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ledger.open("a", encoding="utf-8") as sink:
            for job in jobs:
                if job.id in done:
                    continue
                encoded = encode(job.body)
                try:
                    reserve = budget.admit(len(encoded))
                except Stop as stop:
                    return str(stop)
                wait = pacing - (time.perf_counter() - last_start)
                if last_start and wait > 0:
                    sleep(wait)
                seen = current[0] = Observation()
                last_start = time.perf_counter()
                started = _now()
                outcome, parsed, neurons = "ok", {}, None
                try:
                    response = transport.post(job.body)
                except ClefError as exc:
                    latency = time.perf_counter() - last_start
                    outcome, neurons = exc.reason.value, exc.metadata.neurons
                else:
                    latency = time.perf_counter() - last_start
                    neurons = response.metadata.neurons
                    try:
                        if job.kind == "single":
                            parsed = validate_single(response.data, baseline)
                        else:
                            parsed = _dual(scorer.parse_profile_response(response.data))
                            outcome = parsed.pop("outcome")
                    except (KeyError, TypeError, ValueError, AttributeError):
                        outcome = ClefReason.MALFORMED_RESPONSE.value
                charged = reserve if neurons is None else neurons
                budget.requests += 1
                budget.neurons += charged
                row = {
                    "job": job.id,
                    "lane": job.lane,
                    "case": job.case,
                    "kind": job.kind,
                    "meta": job.meta,
                    "utc": started,
                    "request_sha256": hashlib.sha256(encoded).hexdigest(),
                    "request_bytes": len(encoded),
                    "reserved_neurons": reserve,
                    "charged_neurons": charged,
                    "neurons": neurons,
                    "http_status": seen.status,
                    "api_version": seen.api_version,
                    "outcome": outcome,
                    "cf_error_codes": seen.error_codes,
                    "billing_hint": seen.billing_hint,
                    "latency_ms": round(latency * 1000, 3),
                    **parsed,
                    "response": sanitize_response(seen.data) if seen.status == 200 else None,
                }
                sink.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                sink.flush()
                out(
                    f"{job.id}: http={seen.status} outcome={outcome} "
                    f"tokens={parsed.get('input_tokens')} neurons={neurons} "
                    f"latency_ms={row['latency_ms']} spent={budget.neurons:.2f}"
                )
                reason = stop_reason(row, smoke=smoke, max_call=budget.max_call_neurons)
                if reason:
                    return reason
                consecutive_failures = 0 if outcome == "ok" else consecutive_failures + 1
                if consecutive_failures >= 3:
                    return "three consecutive failures"
        return "completed"
    finally:
        transport.close()
        client.close()


def _dual(result: ProfileCaseResult) -> dict[str, Any]:
    choice, ordinal = result.choice, result.ordinal
    if choice.status.value != "ok" or ordinal is None or choice.distribution is None:
        return {"outcome": choice.detail or ClefReason.MALFORMED_RESPONSE.value}
    assessment = accepted_assessment(result, cached=False)
    return {
        "outcome": "ok",
        "input_tokens": choice.usage.prompt_tokens,
        "output_tokens": choice.usage.completion_tokens,
        "taste_fit": dict(choice.distribution.probabilities),
        "taste_choice": choice.choice,
        "fit_score": list(ordinal.probabilities),
        "assessment": {"score": assessment.score, "reason": assessment.reason.value},
    }


def stop_reason(row: Mapping[str, Any], *, smoke: bool, max_call: float) -> str | None:
    status, outcome, neurons = row["http_status"], row["outcome"], row["neurons"]
    if status in (401, 402, 403):
        return f"HTTP {status}: authorization or plan problem"
    if row["billing_hint"]:
        return "billing or plan wording in a provider response"
    if status == 429 or outcome in ("rate_limited", "quota_exhausted"):
        return "rate or quota limit"
    if status == 200 and outcome != "ok":
        return f"unexpected model or schema result: {outcome}"
    if outcome in ("configuration", "malformed_response", "refused", "invalid_input"):
        return f"unexpected model or schema result: {outcome}"
    if outcome == "ok" and neurons is None:
        return "usage header missing; spend cannot be measured"
    if neurons is not None and neurons > row["reserved_neurons"] + 1e-9:
        return "measured Neurons exceed the request's reservation"
    if neurons is not None and neurons > max_call + 1e-9:
        return f"call above {max_call:g} measured Neurons"
    tokens = row.get("input_tokens")
    if tokens is not None and tokens > row["request_bytes"] + TOKEN_OVERHEAD:
        return "reported tokens exceed the preflight bound"
    if smoke and outcome != "ok":
        return f"smoke failure: {outcome}"
    return None


# Read-only account probe.

ACCOUNT_PROBES = (
    ("token_verify_user", "/user/tokens/verify"),
    ("token_verify_account", "/accounts/{account}/tokens/verify"),
    ("account", "/accounts/{account}"),
    ("subscriptions", "/accounts/{account}/subscriptions"),
    ("billing_profile", "/accounts/{account}/billing/profile"),
    ("user_billing_profile", "/user/billing/profile"),
    ("memberships", "/memberships"),
    ("workers_scripts", "/accounts/{account}/workers/scripts"),
    ("durable_object_namespaces", "/accounts/{account}/workers/durable_objects/namespaces"),
    ("workers_subdomain", "/accounts/{account}/workers/subdomain"),
    ("workers_account_settings", "/accounts/{account}/workers/account-settings"),
    ("ai_model_search", "/accounts/{account}/ai/models/search?search=clef-flash"),
)


def _safe_fields(name: str, result: Any) -> dict[str, Any]:
    """Only non-identifying plan facts; names, ids, emails, subdomains and cards never print."""
    if name.startswith("token_verify") and isinstance(result, dict):
        return {"token_status": result.get("status")}
    if name == "account" and isinstance(result, dict):
        return {"type": result.get("type")}
    if name == "subscriptions" and isinstance(result, list):
        return {
            "plans": [
                {
                    "rate_plan": (item.get("rate_plan") or {}).get("id"),
                    "public_name": (item.get("rate_plan") or {}).get("public_name"),
                    "price": item.get("price"),
                    "currency": item.get("currency"),
                    "frequency": item.get("frequency"),
                    "state": item.get("state"),
                }
                for item in result
                if isinstance(item, dict)
            ]
        }
    if name.endswith("billing_profile") and isinstance(result, dict):
        return {
            "has_card": bool(result.get("card_number") or result.get("card_expiry_year")),
            "has_payment_gateway": bool(result.get("payment_gateway")),
        }
    if isinstance(result, list):
        if name == "ai_model_search":
            return {
                "clef_flash_listed": any(
                    isinstance(item, dict) and item.get("name") == CLEF_FIT_MODEL for item in result
                )
            }
        return {"count": len(result)}
    if name == "workers_account_settings" and isinstance(result, dict):
        return {"default_usage_model": result.get("default_usage_model")}
    return {}


def account_probe(
    account: str, token: str, client: httpx.Client, out: Callable[[str], None] = print
) -> list[dict[str, Any]]:
    rows = []
    for name, template in ACCOUNT_PROBES:
        row: dict[str, Any] = {"probe": name, "path": template}
        try:
            response = client.get(
                API + template.format(account=account),
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            row["error"] = type(exc).__name__  # exception text can carry the account URL
            rows.append(row)
            out(json.dumps(row))
            continue
        row["http_status"] = response.status_code
        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict):
            row["success"] = data.get("success")
            row["error_codes"] = [
                e.get("code") for e in data.get("errors") or [] if isinstance(e, dict)
            ]
            if data.get("success") is True:
                row.update(_safe_fields(name, data.get("result")))
        rows.append(row)
        out(json.dumps(row))
    return rows


# Offline summary of a ledger.


def _expected(fit_score: Sequence[float]) -> float:
    return math.fsum(i * p for i, p in enumerate(fit_score))


def _level(expectation: float) -> int:
    return min(5, math.floor(1 + expectation + 0.5))


def _quantile(values: Sequence[float], q: float) -> float | None:
    """Linear interpolation, as NumPy's default, matching the earlier Clef report."""
    if not values:
        return None
    items = sorted(values)
    position = (len(items) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return items[low] + (items[high] - items[low]) * (position - low)


def _family_kind(family: str) -> str:
    """original-NN, pair-ANN (matched fit/mismatch) or pair-BNN (hard-limit/tradeoff)."""
    return "original" if family.startswith("original") else family[:6]


def ordinal_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Earlier report's method: one-based expected level against one-to-five labels."""
    scored = [r for r in rows if r.get("fit_score") is not None]
    if not scored:
        return {"cases": 0}
    exact = within = 0
    errors, brier, logloss = [], [], []
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(5)]
    for row in scored:
        label, probs = row["meta"]["label"], row["fit_score"]
        expectation = _expected(probs)
        level = _level(expectation)
        exact += level == label
        within += abs(level - label) <= 1
        errors.append(abs(1 + expectation - label) / 4)
        brier.append(math.fsum((p - (i + 1 == label)) ** 2 for i, p in enumerate(probs)))
        logloss.append(-math.log(max(probs[label - 1], 1e-15)))
        top = max(probs)
        modal = probs.index(top) + 1
        bins[min(4, math.floor(top / 0.2))].append((top, modal == label))
    n = len(scored)
    ece = math.fsum(
        len(b) / n * abs(math.fsum(t for t, _ in b) / len(b) - sum(c for _, c in b) / len(b))
        for b in bins
        if b
    )
    return {
        "cases": n,
        "exact": exact,
        "within_one": within,
        "normalized_mae": round(math.fsum(errors) / n, 6),
        "brier": round(math.fsum(brier) / n, 6),
        "log_loss": round(math.fsum(logloss) / n, 6),
        "top_label_ece": round(ece, 6),
        "errors_by_family_kind": dict(
            Counter(
                _family_kind(r["meta"]["family"])
                for r in scored
                if _level(_expected(r["fit_score"])) != r["meta"]["label"]
            )
        ),
        "errors": sorted(
            f"{r['case']}: label {r['meta']['label']}, level {_level(_expected(r['fit_score']))}"
            for r in scored
            if _level(_expected(r["fit_score"])) != r["meta"]["label"]
        ),
    }


def _family_bootstrap(
    pairs: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]],
) -> dict[str, Any]:
    """Paired dual-minus-control MAE and exact differences over family resamples."""
    families: dict[str, list[str]] = {}
    for case_id, (row, _) in pairs.items():
        families.setdefault(row["meta"]["family"], []).append(case_id)
    names = sorted(families)
    rng = random.Random(BOOTSTRAP_SEED)

    def stats(case_ids: Iterable[str]) -> tuple[float, float]:
        ids = list(case_ids)
        mae = exact = 0.0
        for case_id in ids:
            dual, control = pairs[case_id]
            label = dual["meta"]["label"]
            for sign, row in ((1, dual), (-1, control)):
                expectation = _expected(row["fit_score"])
                mae += sign * abs(1 + expectation - label) / 4
                exact += sign * (_level(expectation) == label)
        return mae / len(ids), 100 * exact / len(ids)

    point = stats(pairs)
    draws = []
    for _ in range(BOOTSTRAP_DRAWS):
        chosen = [case for _ in names for case in families[rng.choice(names)]]
        draws.append(stats(chosen))
    mae, exact = sorted(d[0] for d in draws), sorted(d[1] for d in draws)
    return {
        "method": f"{BOOTSTRAP_DRAWS} family resamples, Python random seed {BOOTSTRAP_SEED}",
        "mae_difference": round(point[0], 6),
        "mae_difference_95": [
            round(_quantile(mae, 0.025) or 0, 6),
            round(_quantile(mae, 0.975) or 0, 6),
        ],
        "exact_difference_pp": round(point[1], 4),
        "exact_difference_pp_95": [
            round(_quantile(exact, 0.025) or 0, 4),
            round(_quantile(exact, 0.975) or 0, 4),
        ],
    }


def _max_shift(a: Sequence[float], b: Sequence[float]) -> float:
    return max(abs(x - y) for x, y in zip(a, b, strict=True))


def _taste_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r.get("taste_fit") is not None]
    reasons = Counter(r["assessment"]["reason"] for r in ok)
    accepted = [r for r in ok if max(r["taste_fit"].values()) + 1e-9 >= 0.70]
    consistency_abstained = 0
    for r in accepted:
        group = ("poor", "poor", "partial", "strong", "strong")[
            _level(_expected(r["fit_score"])) - 1
        ]
        consistency_abstained += r["taste_choice"] != "unknown" and r["taste_choice"] != group
    return {
        "responses": len(ok),
        "modal_choices": dict(Counter(r["taste_choice"] for r in ok)),
        "accepted_at_0_70": len(accepted),
        "accepted_unknown": sum(r["taste_choice"] == "unknown" for r in accepted),
        "consistency_abstentions": consistency_abstained,
        "rated": reasons.get("model_estimate", 0),
        "assessment_reasons": dict(reasons),
        "rated_scores": sorted(
            r["assessment"]["score"] for r in ok if r["assessment"]["score"] is not None
        ),
    }


def _contract_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r.get("taste_fit") is not None]
    if not ok:
        return {"cases": 0}
    modal = sum(r["taste_choice"] == r["meta"]["label"] for r in ok)
    brier = [
        math.fsum((r["taste_fit"][k] - (k == r["meta"]["label"])) ** 2 for k in TASTE_IDS)
        for r in ok
    ]
    logloss = [-math.log(max(r["taste_fit"][r["meta"]["label"]], 1e-15)) for r in ok]
    accepted = [r for r in ok if max(r["taste_fit"].values()) + 1e-9 >= 0.70]
    per_class = {}
    for label in TASTE_IDS:
        predicted = [r for r in accepted if r["taste_choice"] == label]
        actual = [r for r in ok if r["meta"]["label"] == label]
        per_class[label] = {
            "labelled": len(actual),
            "accepted_predictions": len(predicted),
            "accepted_correct": sum(r["meta"]["label"] == label for r in predicted),
            "recall_accepted": (
                round(
                    sum(r["taste_choice"] == label for r in accepted if r in actual) / len(actual),
                    4,
                )
                if actual
                else None
            ),
        }
    rated = [r for r in ok if r["assessment"]["score"] is not None]
    return {
        "cases": len(ok),
        "modal_accuracy": round(modal / len(ok), 4),
        "brier": round(math.fsum(brier) / len(ok), 6),
        "log_loss": round(math.fsum(logloss) / len(ok), 6),
        "coverage_at_0_70": round(len(accepted) / len(ok), 4),
        "accepted_correct": sum(r["taste_choice"] == r["meta"]["label"] for r in accepted),
        "accepted": len(accepted),
        "per_class": per_class,
        "rated_after_consistency": len(rated),
        "rated_on_labelled_poor_with_score_above_50": sum(
            r["meta"]["label"] == "poor" and r["assessment"]["score"] > 50 for r in rated
        ),
        "rated_on_labelled_strong_with_score_below_50": sum(
            r["meta"]["label"] == "strong" and r["assessment"]["score"] < 50 for r in rated
        ),
        "by_split_modal_accuracy": {
            split: round(
                sum(
                    r["taste_choice"] == r["meta"]["label"]
                    for r in ok
                    if r["meta"]["split"] == split
                )
                / max(1, sum(r["meta"]["split"] == split for r in ok)),
                4,
            )
            for split in sorted({r["meta"]["split"] for r in ok})
        },
        "errors": sorted(
            f"{r['case']}: label {r['meta']['label']}, modal {r['taste_choice']}"
            for r in ok
            if r["taste_choice"] != r["meta"]["label"]
        ),
    }


def _production_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r.get("taste_fit") is not None]
    by_pattern: dict[str, dict[str, Any]] = {}
    for pattern in ("like", "dislike", "unrelated"):
        items = [r for r in ok if r["meta"]["pattern"] == pattern]
        by_pattern[pattern] = {
            "cases": len(items),
            "local_baseline_rated": sum(r["meta"]["local_score"] is not None for r in items),
            "model_rated": sum(r["assessment"]["score"] is not None for r in items),
            "model_scores": [r["assessment"]["score"] for r in items],
            "modal_choices": dict(Counter(r["taste_choice"] for r in items)),
            "expected_index_range": [
                round(min(25 * _expected(r["fit_score"]) for r in items), 2),
                round(max(25 * _expected(r["fit_score"]) for r in items), 2),
            ]
            if items
            else None,
        }
    contradictions = [
        r["case"]
        for r in ok
        if r["assessment"]["score"] is not None
        and r["meta"]["local_score"] is not None
        and (r["assessment"]["score"] >= 50) != (r["meta"]["local_score"] >= 50)
    ]
    return {"by_pattern": by_pattern, "direction_contradictions_vs_local": contradictions}


def _usage(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    latencies = [r["latency_ms"] for r in rows]
    tokens = [r["input_tokens"] for r in rows if r.get("input_tokens") is not None]
    neurons = [r["neurons"] for r in rows if r["neurons"] is not None]
    return {
        "calls": len(rows),
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "http_status": dict(Counter(str(r["http_status"]) for r in rows)),
        "input_tokens": sum(tokens),
        "input_tokens_range": [min(tokens), max(tokens)] if tokens else None,
        "neurons_measured": round(math.fsum(neurons), 2),
        "neurons_charged": round(math.fsum(r["charged_neurons"] for r in rows), 2),
        "neurons_per_call_range": [min(neurons), max(neurons)] if neurons else None,
        "latency_ms_p50": round(_quantile(latencies, 0.5) or 0, 1),
        "latency_ms_p95": round(_quantile(latencies, 0.95) or 0, 1),
        "latency_ms_max": round(max(latencies), 1) if latencies else None,
    }


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Every metric from the sanitized ledger alone, never dropping failed rows from usage."""
    lanes: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        lanes.setdefault(row["lane"], []).append(row)
    summary: dict[str, Any] = {
        "usage_all": _usage(rows),
        "usage_by_lane": {lane: _usage(items) for lane, items in lanes.items()},
        "failures": [
            {k: r[k] for k in ("job", "http_status", "outcome", "cf_error_codes")}
            for r in rows
            if r["outcome"] != "ok"
        ],
        "billing_hints": sum(bool(r["billing_hint"]) for r in rows),
    }
    first = {(r["lane"], r["case"]): r for r in rows if r["outcome"] == "ok"}
    if "control" in lanes:
        archive = _archive()
        vectors = {case: vector for case, (vector, _) in archive.items()}
        hashes = {case: digest for case, (_, digest) in archive.items()}
        control = [r for r in lanes["control"] if r["outcome"] == "ok"]
        shifts = [
            _max_shift(r["fit_score"], vectors[r["case"]]) for r in control if r["case"] in vectors
        ]
        summary["control"] = {
            **ordinal_metrics(control),
            "request_hashes_match_archive": sum(
                hashes.get(r["case"]) == r["request_sha256"] for r in lanes["control"]
            ),
            "max_probability_shift_vs_archive": round(max(shifts), 6) if shifts else None,
            "cases_shifted_beyond_rounding": sum(s > 0.00011 for s in shifts),
            "rounded_level_changes_vs_archive": sum(
                _level(_expected(r["fit_score"])) != _level(_expected(vectors[r["case"]]))
                for r in control
                if r["case"] in vectors
            ),
        }
    if "dual" in lanes:
        dual = [r for r in lanes["dual"] if r["outcome"] == "ok"]
        summary["dual_fit_score"] = ordinal_metrics(dual)
        summary["dual_taste_fit"] = _taste_summary(dual)
        summary["dual_taste_fit"]["label_group_proxy_agreement_of_accepted"] = sum(
            r["taste_choice"]
            == ("poor", "poor", "partial", "strong", "strong")[r["meta"]["label"] - 1]
            for r in dual
            if max(r["taste_fit"].values()) + 1e-9 >= 0.70
        )
        pairs = {
            r["case"]: (r, first[("control", r["case"])])
            for r in dual
            if ("control", r["case"]) in first
        }
        if pairs:
            summary["dual_minus_control"] = {
                **_family_bootstrap(pairs),
                "rounded_level_changes": sum(
                    _level(_expected(d["fit_score"])) != _level(_expected(c["fit_score"]))
                    for d, c in pairs.values()
                ),
                "mean_abs_expectation_shift": round(
                    math.fsum(
                        abs(_expected(d["fit_score"]) - _expected(c["fit_score"]))
                        for d, c in pairs.values()
                    )
                    / len(pairs),
                    6,
                ),
                "token_ratio": round(
                    math.fsum(d["input_tokens"] for d, _ in pairs.values())
                    / math.fsum(c["input_tokens"] for _, c in pairs.values()),
                    4,
                ),
            }
    for lane, base in (
        ("control_repeat", "control"),
        ("dual_repeat", "dual"),
        ("dual_reversed", "dual"),
    ):
        items = [
            r for r in lanes.get(lane, []) if r["outcome"] == "ok" and (base, r["case"]) in first
        ]
        if not items:
            continue
        entry: dict[str, Any] = {
            "cases": len(items),
            "max_fit_score_shift": round(
                max(
                    _max_shift(r["fit_score"], first[(base, r["case"])]["fit_score"]) for r in items
                ),
                6,
            ),
            "rounded_level_changes": sum(
                _level(_expected(r["fit_score"]))
                != _level(_expected(first[(base, r["case"])]["fit_score"]))
                for r in items
            ),
        }
        if base == "dual":
            entry["max_taste_fit_shift"] = round(
                max(
                    _max_shift(
                        [r["taste_fit"][k] for k in TASTE_IDS],
                        [first[(base, r["case"])]["taste_fit"][k] for k in TASTE_IDS],
                    )
                    for r in items
                ),
                6,
            )
            entry["assessment_changes"] = sum(
                r["assessment"] != first[(base, r["case"])]["assessment"] for r in items
            )
            entry["modal_choice_changes"] = sum(
                r["taste_choice"] != first[(base, r["case"])]["taste_choice"] for r in items
            )
        summary[lane] = entry
    if "production" in lanes:
        production = [r for r in lanes["production"] if r["outcome"] == "ok"]
        summary["production"] = {
            **_taste_summary(production),
            **_production_summary(production),
        }
        neurons = [r["neurons"] for r in production if r["neurons"] is not None]
        if neurons:
            per_case = math.fsum(neurons) / len(neurons)
            summary["production"]["capacity_estimate"] = {
                "mean_neurons_per_case": round(per_case, 4),
                "case_calls_per_9000_neurons": math.floor(9000 / per_case),
                "full_cold_browse_searches_20": math.floor(9000 / per_case / 20),
                "full_cold_recommendation_searches_40": math.floor(9000 / per_case / 40),
                "note": "Arithmetic for these short states only; not a tested entitlement.",
            }
    if "contract" in lanes:
        summary["contract"] = _contract_summary(
            [r for r in lanes["contract"] if r["outcome"] == "ok"]
        )
        summary["contract"]["taste"] = _taste_summary(
            [r for r in lanes["contract"] if r["outcome"] == "ok"]
        )
    smoke = [r for r in rows if r["lane"].startswith("smoke")]
    if smoke:
        by_case: dict[str, list[Mapping[str, Any]]] = {}
        for r in smoke:
            by_case.setdefault(r["case"], []).append(r)
        summary["smoke"] = {
            "repeat_identical": all(
                len(items) < 2
                or all(
                    i.get("fit_score") == items[0].get("fit_score")
                    and i.get("taste_fit") == items[0].get("taste_fit")
                    for i in items
                )
                for items in by_case.values()
            ),
            "assessments": {r["job"]: r.get("assessment") for r in smoke},
        }
    if "control" in summary and "production" in summary and "contract" in summary:
        summary["gates"] = gates(rows, summary)
    return summary


def gates(rows: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]) -> dict[str, Any]:
    """The pre-registered qualification gates (tests/fixtures/clef/live/README.md), unchanged."""
    first = {(r["lane"], r["case"]): r for r in rows if r["outcome"] == "ok"}
    usage, control = summary["usage_all"], summary["control"]
    reversed_rows = [r for r in rows if r["lane"] == "dual_reversed" and r["outcome"] == "ok"]

    def moved(row: Mapping[str, Any]) -> bool:
        before, after = (
            first[("dual", row["case"])]["assessment"]["score"],
            row["assessment"]["score"],
        )
        if before is None or after is None:
            return (before is None) != (after is None)
        return bool(abs(before - after) > 10)

    order_ok = not any(moved(row) for row in reversed_rows)
    production = summary["production"]["by_pattern"]
    contract = summary["contract"]
    accepted_error = (
        1 - contract["accepted_correct"] / contract["accepted"] if contract["accepted"] else None
    )
    delta = summary["dual_minus_control"]
    result = {
        "Q1_availability": usage["outcomes"] == {"ok": usage["calls"]},
        "Q2_spend_and_plan": not summary["billing_hints"]
        and not any(r["http_status"] in (401, 402, 403) for r in rows)
        and all(r["charged_neurons"] <= r["reserved_neurons"] + 1e-9 for r in rows),
        "Q3_control_canary": control["request_hashes_match_archive"] == 40
        and control["cases_shifted_beyond_rounding"] == 0,
        "Q4_repeatability": summary["control_repeat"]["max_fit_score_shift"] == 0
        and summary["dual_repeat"]["max_fit_score_shift"] == 0
        and summary["dual_repeat"]["max_taste_fit_shift"] == 0,
        "Q5_question_interference": delta["mae_difference"] <= 0.025
        and delta["mae_difference_95"][1] < 0.05,
        "Q6_question_order": order_ok,
        "Q7_production_direction": not summary["production"]["direction_contradictions_vs_local"]
        and production["like"]["model_rated"] + production["dislike"]["model_rated"] >= 12,
        "Q8_draft_taste_fit_labels": accepted_error is not None
        and accepted_error <= 0.20
        and contract["rated_on_labelled_poor_with_score_above_50"] == 0
        and contract["rated_on_labelled_strong_with_score_below_50"] == 0,
        "Q9_human_diner_labels": "open: no independently reviewed human labels exist",
    }
    result["accepted_error_q8"] = None if accepted_error is None else round(accepted_error, 4)
    result["rated_liked_or_disliked_q7"] = (
        production["like"]["model_rated"] + production["dislike"]["model_rated"]
    )
    return result


def _fixture_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "clef"
        / "isolated-vectors.json"
    )


def _archive() -> dict[str, tuple[list[float], str]]:
    """Frozen isolated vectors and request hashes, from the checkout's offline replay fixture."""
    path = _fixture_path()
    if not path.exists():
        return {}
    return {
        row["id"]: ([row["probabilities"][k] for k in LEVEL_IDS], row["request_sha256"])
        for row in parse_json(path.read_text("utf-8"))["cases"]
    }


# Command line.


def _caps(args: argparse.Namespace, *, smoke: bool) -> Budget:
    if args.max_requests is None or args.max_neurons is None:
        raise Refused("--max-requests and --max-neurons are required")
    if args.max_requests < 1 or args.max_neurons <= 0:
        raise Refused("caps must be positive")
    if smoke and (args.max_requests > SMOKE_MAX_REQUESTS or args.max_neurons > SMOKE_MAX_NEURONS):
        raise Refused(
            f"smoke caps are at most {SMOKE_MAX_REQUESTS} requests and "
            f"{SMOKE_MAX_NEURONS:g} Neurons"
        )
    if args.max_neurons > QUALIFY_MAX_NEURONS:
        raise Refused(f"--max-neurons may not exceed {QUALIFY_MAX_NEURONS:g}")
    return Budget(
        args.max_requests,
        args.max_neurons,
        SMOKE_MAX_CALL_NEURONS if smoke else GATEWAY_CEILING_NEURONS,
    )


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> str:
    parser = argparse.ArgumentParser(
        prog="python -m makan.evals.clef_live",
        description="Explicit live Clef checks; never run by CI, build or deploy hooks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    account = sub.add_parser("account", help="read-only account and token probes")
    account.add_argument("--live", action="store_true")
    for name in ("smoke", "qualify"):
        item = sub.add_parser(name)
        item.add_argument("--live", action="store_true")
        item.add_argument("--max-requests", type=int)
        item.add_argument("--max-neurons", type=float)
        item.add_argument("--ledger", type=Path, required=True)
    summary = sub.add_parser("summarize", help="offline metrics from a sanitized ledger")
    summary.add_argument("--ledger", type=Path, action="append", required=True)
    summary.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ

    if args.command == "summarize":
        rows = [row for path in args.ledger for row in _read_ledger(path)]
        text = json.dumps(summarize(rows), indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(text, "utf-8")
        else:
            sys.stdout.write(text)
        return "summarized"
    if not args.live:
        raise Refused("live Clef calls need the explicit --live flag")
    account_id, token = credentials(environ)
    if args.command == "account":
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            account_probe(account_id, token, client)
        return "probed"
    smoke = args.command == "smoke"
    budget = _caps(args, smoke=smoke)
    scorer = ClefScorer(None, CLEF_FIT_MODEL)
    jobs = smoke_jobs(scorer) if smoke else qualification_jobs(scorer)

    def factory(client: httpx.Client) -> CloudflareTransport:
        return CloudflareTransport(account_id, token, CLEF_FIT_MODEL, client=client)

    reason = run_jobs(jobs, args.ledger, budget, factory, smoke=smoke)
    print(f"stopped: {reason}; requests={budget.requests} charged_neurons={budget.neurons:.2f}")
    return reason


if __name__ == "__main__":
    main()
