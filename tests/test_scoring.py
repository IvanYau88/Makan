"""The fixed-answer scorers and their result semantics. No real network calls."""

import importlib.util
import json
import math
import socket
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
import pytest

from makan.config import CLEF_FIT_MODEL, Config, ConfigError, FitConfig, ScorerConfig
from makan.providers import ProviderError
from makan.providers.scoring import (
    BoundedScorer,
    ClefScorer,
    CloudflareTransport,
    Distribution,
    ErrorKind,
    Evidence,
    FakeScorer,
    JevScorer,
    LogprobScorer,
    OpenRouterTransport,
    Option,
    Question,
    ScoreResult,
    Status,
    StructuredScorer,
    build_fit_scorer,
    build_scorer,
    lexical_policy,
    oracle,
)
from makan.providers.scoring.transport import usage_of

OPTIONS = (
    Option("thai", "Thai food"),
    Option("ramen", "Ramen or noodles"),
    Option("none", "Neither"),
)


def question(*options: Option, state: str = "pad thai please") -> Question:
    return Question("test", "1", "Pick one.", state, options or OPTIONS)


class Wire:
    """An httpx mock transport that records requests and answers from a fixed handler."""

    def __init__(self, respond: Any) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond
        client = httpx.Client(transport=httpx.MockTransport(self._handle))
        self.transport = OpenRouterTransport("sk-test", client=client)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self._respond, httpx.Response):
            return self._respond
        if isinstance(self._respond, Exception):
            raise self._respond
        return httpx.Response(200, json=self._respond)

    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)  # type: ignore[no-any-return]


def logprob_response(sampled: str, alternatives: dict[str, float], **extra: Any) -> dict[str, Any]:
    """A chat completion whose first answer token has the given alternatives (probabilities)."""
    top = [{"token": t, "logprob": math.log(p)} for t, p in alternatives.items()]
    return {
        "choices": [
            {
                "message": {"content": sampled},
                "logprobs": {
                    "content": [
                        {
                            "token": sampled,
                            "logprob": math.log(alternatives.get(sampled, 0.5)),
                            "top_logprobs": top,
                        }
                    ]
                },
            }
        ],
        "usage": {"prompt_tokens": 40, "completion_tokens": 1, "cost": 0.0},
        **extra,
    }


# Question and Distribution


def test_a_question_needs_unique_options() -> None:
    with pytest.raises(ValueError, match="at least one"):
        Question("d", "1", "i", "s", ())
    with pytest.raises(ValueError, match="unique"):
        question(Option("a", "x"), Option("a", "y"))
    with pytest.raises(ValueError, match="non-empty"):
        question(Option(" ", "x"), Option("b", "y"))


def test_distribution_ranks_top_runner_up_and_margin() -> None:
    d = Distribution.of({"thai": 0.1, "ramen": 0.7, "none": 0.2}, ("thai", "ramen", "none"))
    assert (d.top, d.runner_up) == ("ramen", "none")
    assert d.margin == pytest.approx(0.5)
    assert [k for k, _ in d.probabilities] == ["thai", "ramen", "none"]


def test_a_tie_goes_to_the_earlier_option_with_zero_margin() -> None:
    d = Distribution.of({"a": 0.5, "b": 0.5}, ("a", "b"))
    assert (d.top, d.runner_up, d.margin) == ("a", "b", 0.0)


@pytest.mark.parametrize(
    "probabilities",
    [
        {"a": 1.0},  # a missing option
        {"a": 0.5, "b": 0.5, "c": 0.0},  # an extra option
        {"a": 0.6, "b": 0.6},  # does not sum to 1
        {"a": math.nan, "b": 0.5},
        {"a": 1.5, "b": -0.5},
    ],
)
def test_a_malformed_distribution_is_rejected(probabilities: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        Distribution.of(probabilities, ("a", "b"))


def test_only_a_clear_full_distribution_is_a_confident_choice() -> None:
    full = FakeScorer(oracle({"test": "thai"})).score(question())
    assert full.confident_choice(0.5) == "thai"
    assert full.confident_choice(1.5) is None
    degraded = ScoreResult(Status.DEGRADED, "b", "m", choice="thai")
    assert degraded.confident_choice(0.0) is None
    assert degraded.margin is None and degraded.runner_up is None


# Transport and shared failure handling


def test_one_option_is_chosen_without_a_request() -> None:
    wire = Wire({})
    result = LogprobScorer(wire.transport, "m").score(question(Option("only", "The only one")))
    assert (result.status, result.choice, result.evidence) == (
        Status.OK,
        "only",
        Evidence.DETERMINISTIC,
    )
    assert result.distribution is None and wire.requests == []


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (httpx.Response(429, text="slow down"), ErrorKind.BUSY),
        (httpx.Response(503, text="down"), ErrorKind.BUSY),
        (httpx.Response(401, text="no"), ErrorKind.TRANSPORT),
        (httpx.Response(200, text="<html>"), ErrorKind.TRANSPORT),
        (httpx.Response(200, json={"error": {"code": 429, "message": "busy"}}), ErrorKind.BUSY),
        (httpx.Response(200, json={"error": {"code": 400, "message": "bad"}}), ErrorKind.TRANSPORT),
        (httpx.ReadTimeout("slow"), ErrorKind.TIMEOUT),
        (httpx.ConnectError("down"), ErrorKind.TRANSPORT),
    ],
)
def test_request_failures_are_error_results_not_raises(response: Any, kind: ErrorKind) -> None:
    result = StructuredScorer(Wire(response).transport, "m").score(question())
    assert (result.status, result.error, result.choice) == (Status.ERROR, kind, None)


def test_the_transport_needs_an_api_key_and_sends_it_as_a_bearer_token() -> None:
    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY"):
        OpenRouterTransport("")
    wire = Wire(logprob_response("A", {"A": 0.9, "B": 0.05, "C": 0.05}))
    LogprobScorer(wire.transport, "m").score(question())
    assert wire.requests[0].headers["authorization"] == "Bearer sk-test"


def test_usage_and_cost_are_read_when_given_and_unknown_otherwise() -> None:
    assert usage_of({"usage": {"prompt_tokens": 3, "completion_tokens": 2, "cost": 0.5}}) == (
        3,
        2,
        0.5,
    )
    assert usage_of({"usage": {"input_tokens": 7}}) == (7, 0, None)
    assert usage_of({}) == (0, 0, None)
    assert usage_of({"usage": {"cost": True}})[2] is None


# Logprob scorer


def test_logprobs_give_a_full_distribution_over_the_option_ids() -> None:
    wire = Wire(logprob_response("A", {"A": 0.6, "B": 0.3, "C": 0.1}))
    result = LogprobScorer(wire.transport, "vendor/model").score(question())

    assert result.status is Status.OK and result.evidence is Evidence.TOKEN_LOGPROBS
    assert result.choice == "thai" and result.runner_up == "ramen"
    assert result.margin == pytest.approx(0.3)
    assert dict(result.distribution.probabilities)["none"] == pytest.approx(0.1)  # type: ignore[union-attr]
    assert result.label_mass == pytest.approx(1.0)
    assert result.model == "vendor/model" and result.usage.prompt_tokens == 40
    assert result.cost == 0.0 and result.latency_s >= 0


def test_the_request_asks_for_logprobs_and_requires_the_endpoint_to_support_them() -> None:
    wire = Wire(logprob_response("A", {"A": 0.6, "B": 0.3, "C": 0.1}))
    LogprobScorer(wire.transport, "vendor/model").score(question(state="pad thai please"))

    (request,) = wire.requests
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    body = wire.body()
    assert body["model"] == "vendor/model"
    assert body["logprobs"] is True and body["top_logprobs"] == 20
    assert body["max_tokens"] == 1 and body["temperature"] == 0
    assert body["provider"] == {"require_parameters": True}
    prompt = body["messages"][1]["content"]
    assert "pad thai please" in prompt and "A: Thai food" in prompt and "C: Neither" in prompt
    assert "thai" not in body["messages"][0]["content"].split("Pick one.")[1]  # ids stay private


def test_label_probabilities_are_renormalized_and_the_label_mass_is_kept() -> None:
    wire = Wire(logprob_response("A", {"A": 0.30, "B": 0.20, "C": 0.10, "The": 0.40}))
    result = LogprobScorer(wire.transport, "m").score(question())

    assert result.status is Status.OK
    assert result.label_mass == pytest.approx(0.6)
    assert dict(result.distribution.probabilities)["thai"] == pytest.approx(0.5)  # type: ignore[union-attr]


def test_a_leading_space_variant_counts_toward_the_same_label() -> None:
    wire = Wire(logprob_response("A", {"A": 0.4, " A": 0.2, "B": 0.2, "C": 0.2}))
    result = LogprobScorer(wire.transport, "m").score(question())
    assert dict(result.distribution.probabilities)["thai"] == pytest.approx(0.6)  # type: ignore[union-attr]


def test_a_missing_label_is_unknown_not_zero() -> None:
    wire = Wire(logprob_response("A", {"A": 0.9, "B": 0.1}))  # no C among the alternatives
    result = LogprobScorer(wire.transport, "m").score(question())

    assert result.status is Status.DEGRADED and result.evidence is Evidence.SAMPLED_LABEL
    assert result.choice == "thai"
    assert result.distribution is None and result.margin is None
    assert result.confident_choice(0.0) is None


def test_a_missing_label_with_an_unusable_sampled_token_is_unsupported() -> None:
    wire = Wire(logprob_response("The", {"The": 0.9, "A": 0.1}))
    result = LogprobScorer(wire.transport, "m").score(question())
    assert (result.status, result.choice) == (Status.UNSUPPORTED, None)


def test_a_model_that_mostly_ignores_the_labels_is_an_error_with_its_mass() -> None:
    wire = Wire(logprob_response("The", {"The": 0.9, "A": 0.04, "B": 0.03, "C": 0.03}))
    result = LogprobScorer(wire.transport, "m").score(question())

    assert (result.status, result.error) == (Status.ERROR, ErrorKind.LOW_LABEL_MASS)
    assert result.label_mass == pytest.approx(0.1)
    assert result.choice is None


@pytest.mark.parametrize(
    "response",
    [
        {"choices": [{"message": {"content": "A"}}]},  # logprobs ignored by the endpoint
        {"choices": [{"message": {"content": "A"}, "logprobs": {"content": []}}]},
        {"choices": []},
        {
            "choices": [
                {
                    "logprobs": {
                        "content": [
                            {
                                "token": "A",
                                "logprob": 0.7,
                                "top_logprobs": [],
                            },  # not a log probability
                        ]
                    }
                }
            ]
        },
    ],
)
def test_missing_or_invalid_logprobs_are_unsupported(response: dict[str, Any]) -> None:
    result = LogprobScorer(Wire(response).transport, "m").score(question())
    assert (result.status, result.choice, result.distribution) == (Status.UNSUPPORTED, None, None)


def test_more_options_than_labels_are_unsupported_without_a_request() -> None:
    wire = Wire({})
    many = tuple(Option(f"o{i}", "x") for i in range(11))
    result = LogprobScorer(wire.transport, "m").score(question(*many))
    assert result.status is Status.UNSUPPORTED and wire.requests == []


# Structured fallback


def structured_response(content: str, **choice: Any) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, **choice}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 8},
    }


def test_structured_choice_is_degraded_with_a_labelled_self_rating_and_no_margin() -> None:
    wire = Wire(structured_response('{"choice": "ramen", "confidence": 0.7}'))
    result = StructuredScorer(wire.transport, "vendor/model").score(question())

    assert result.status is Status.DEGRADED and result.evidence is Evidence.VERBALIZED_CONFIDENCE
    assert result.choice == "ramen" and result.self_confidence == 0.7
    assert result.distribution is None and result.margin is None and result.runner_up is None
    assert result.confident_choice(0.0) is None
    schema = wire.body()["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["properties"]["choice"]["enum"] == ["thai", "ramen", "none"]
    assert wire.body()["provider"] == {"require_parameters": True}


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (structured_response("not json"), ErrorKind.MALFORMED),
        (structured_response(""), ErrorKind.MALFORMED),
        (structured_response('{"choice": "pizza", "confidence": 0.5}'), ErrorKind.MALFORMED),
        (structured_response('{"choice": "thai", "confidence": 1.5}'), ErrorKind.MALFORMED),
        (structured_response('{"choice": "thai", "confidence": true}'), ErrorKind.MALFORMED),
        (structured_response('{"choice": "thai"}'), ErrorKind.MALFORMED),
        (
            structured_response('{"choice": "thai", "confidence": 0.5, "extra": 1}'),
            ErrorKind.MALFORMED,
        ),
        (structured_response("[]"), ErrorKind.MALFORMED),
        (
            structured_response('{"choice": "thai", "confidence": 0.5}', finish_reason="length"),
            ErrorKind.MALFORMED,
        ),
        (
            {"choices": [{"message": {"content": None, "refusal": "I can't help with that"}}]},
            ErrorKind.REFUSED,
        ),
        ({"choices": []}, ErrorKind.MALFORMED),
    ],
)
def test_unusable_structured_answers_are_identified_errors(
    data: dict[str, Any], kind: ErrorKind
) -> None:
    result = StructuredScorer(Wire(data).transport, "m").score(question())
    assert (result.status, result.error, result.choice) == (Status.ERROR, kind, None)


# Hosted Jev


def jev_response(probabilities: dict[str, float]) -> dict[str, Any]:
    return {
        "answers": {"q": {"choice": "thai", "probabilities": probabilities, "confidence": 0.9}},
        "usage": {"input_tokens": 120, "cost": 0.000005},
    }


def test_jev_gives_probabilities_through_the_decisions_endpoint() -> None:
    wire = Wire(jev_response({"thai": 0.8, "ramen": 0.15, "none": 0.05}))
    result = JevScorer(wire.transport, "vendor/jev").score(question(state="pad thai please"))

    assert result.status is Status.OK and result.evidence is Evidence.JEV_PROBABILITIES
    assert result.choice == "thai" and result.runner_up == "ramen"
    assert result.margin == pytest.approx(0.65)
    assert result.cost == 0.000005 and result.usage.prompt_tokens == 120
    assert str(wire.requests[0].url) == "https://openrouter.ai/api/alpha/decisions"
    body = wire.body()
    assert body["model"] == "vendor/jev" and body["state"] == "pad thai please"
    assert body["questions"]["q"] == {
        "type": "choice",
        "instructions": "Pick one.",
        "criteria": {"thai": "Thai food", "ramen": "Ramen or noodles", "none": "Neither"},
    }


@pytest.mark.parametrize(
    "probabilities",
    [{"thai": 0.8, "ramen": 0.2}, {"thai": 0.5, "ramen": 0.5, "none": 0.5}, {"x": 1.0}],
)
def test_an_incomplete_or_unnormalized_jev_answer_is_malformed(
    probabilities: dict[str, float],
) -> None:
    result = JevScorer(Wire(jev_response(probabilities)).transport, "m").score(question())
    assert (result.status, result.error, result.choice) == (Status.ERROR, ErrorKind.MALFORMED, None)


def test_a_jev_answer_without_probabilities_is_malformed() -> None:
    result = JevScorer(Wire({"answers": {}}).transport, "m").score(question())
    assert result.error is ErrorKind.MALFORMED


# Fakes and the request cap


def test_the_default_fake_is_a_deterministic_full_distribution() -> None:
    fake = FakeScorer()
    q = question(state="I want ramen noodles")
    first, second = fake.score(q), fake.score(q)

    assert first.distribution == second.distribution
    assert first.choice == "ramen" and first.status is Status.OK and first.evidence is Evidence.FAKE
    assert fake.questions == [q, q]
    flat = fake.score(question(state="zzz"))
    assert flat.margin == pytest.approx(0.0)
    assert lexical_policy(q).distribution is not None


def test_a_scripted_fake_replays_results_and_fails_loudly_when_exhausted() -> None:
    degraded = ScoreResult(Status.DEGRADED, "fake", "", choice="thai")
    fake = FakeScorer.scripted([degraded])
    assert fake.score(question()).choice == "thai"
    with pytest.raises(AssertionError, match="exhausted"):
        fake.score(question())


def test_a_request_cap_returns_a_budget_error_without_calling_the_backend() -> None:
    fake = FakeScorer()
    bounded = BoundedScorer(fake, 1)
    assert bounded.score(question()).status is Status.OK
    capped = bounded.score(question())
    assert (capped.status, capped.error) == (Status.ERROR, ErrorKind.BUDGET)
    assert len(fake.questions) == 1
    with pytest.raises(ValueError):
        BoundedScorer(fake, 0)


# Config and the factory


def test_scoring_is_off_by_default() -> None:
    assert ScorerConfig().backend == "none"
    assert build_scorer(ScorerConfig()) is None


def test_the_factory_builds_only_the_configured_backend() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    kinds = {
        "logprob": LogprobScorer,
        "structured": StructuredScorer,
        "jev": JevScorer,
    }
    for backend, kind in kinds.items():
        scorer = build_scorer(
            ScorerConfig(backend, "vendor/model"), openrouter_api_key="sk-test", client=client
        )
        assert type(scorer) is kind
        assert scorer is not None and scorer.model == "vendor/model"
    assert isinstance(build_scorer(ScorerConfig("fake")), FakeScorer)


def test_a_hosted_backend_needs_a_key() -> None:
    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY"):
        build_scorer(ScorerConfig("jev", "vendor/jev"))


def test_a_failed_request_never_falls_over_to_another_backend() -> None:
    wire = Wire(httpx.Response(503, text="down"))
    scorer = build_scorer(
        ScorerConfig("logprob", "m"), openrouter_api_key="sk", client=wire.transport._client
    )
    assert scorer is not None
    result = scorer.score(question())
    assert result.error is ErrorKind.BUSY and len(wire.requests) == 1
    assert {str(r.url) for r in wire.requests} == {"https://openrouter.ai/api/v1/chat/completions"}


@pytest.mark.parametrize(
    ("backend", "model", "message"),
    [
        ("nonsense", "", "MAKAN_SCORER_BACKEND"),
        ("logprob", "", "MAKAN_SCORER_MODEL"),
        ("jev", "", "MAKAN_SCORER_MODEL"),
    ],
)
def test_bad_scorer_settings_are_rejected(backend: str, model: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        ScorerConfig(backend, model)


# Native Clef: every body below is synthetic, optionally using report-reconstructed vectors.
# No raw success responses survived slice 1, and no dual-question call has been qualified.


def clef_contract() -> dict[str, Any]:
    path = resources.files("makan").joinpath("evals", "sets", "clef", "contract.json")
    return json.loads(path.read_text("utf-8"))  # type: ignore[no-any-return]


def clef_state() -> dict[str, Any]:
    return {
        "profile": {"likes": ["thai"], "dislikes": ["italian"], "source": "user_stated"},
        "candidate": {
            "categories": ["thai_restaurant"],
            "source": "overture_category",
            "source_release": "2026-09-23.1",
            "missing_facts": [],
        },
    }


def clef_response(
    ordinal: dict[str, float] | None = None, *, unknown: bool = False
) -> dict[str, Any]:
    """Synthetic native envelope, not a recorded or qualified dual-question success."""
    ordinal = ordinal or {"0": 0.0, "1": 0.0, "2": 0.0, "3": 0.0, "4": 1.0}
    criteria = clef_contract()["questions"]["fit_score"]["criteria"]
    return {
        "success": True,
        "errors": [],
        "messages": [],
        "result": {
            "model": "clef-flash",
            "answers": {
                "taste_fit": {
                    "type": "choice",
                    "choice": "unknown" if unknown else "strong",
                    "probabilities": {
                        "strong": 0.0 if unknown else 0.8,
                        "partial": 0.1,
                        "poor": 0.05,
                        "unknown": 0.85 if unknown else 0.05,
                    },
                    "confidence": 0.6,
                },
                "fit_score": {
                    "type": "score",
                    "legend": {str(i): text for i, text in enumerate(criteria)},
                    "probabilities": ordinal,
                    "score": math.fsum(int(i) * p for i, p in ordinal.items())
                    / math.fsum(ordinal.values()),
                    "confidence": 0.123,  # deliberately unrelated to the ordinal expectation
                },
            },
            "usage": {"input_tokens": 300, "output_tokens": 0},
        },
        "usage": {"prompt_tokens": 999, "completion_tokens": 999, "cost": 88},
    }


class ClefWire:
    def __init__(self, response: Any) -> None:
        self.requests: list[httpx.Request] = []
        self._response = response
        self.client = httpx.Client(
            transport=httpx.MockTransport(self._handle), timeout=100, follow_redirects=True
        )
        self.scorer = ClefScorer(
            CloudflareTransport("a" * 32, "synthetic-token", CLEF_FIT_MODEL, client=self.client),
            CLEF_FIT_MODEL,
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self._response, Exception):
            raise self._response
        if isinstance(self._response, httpx.Response):
            return self._response
        return httpx.Response(200, content=json.dumps(self._response))


def test_clef_sends_one_soft_case_and_both_frozen_questions_in_one_request() -> None:
    wire = ClefWire(clef_response())
    result = wire.scorer.score_profile_case(clef_state())
    assert len(wire.requests) == 1
    request = wire.requests[0]
    assert str(request.url) == (
        "https://api.cloudflare.com/client/v4/accounts/"
        + "a" * 32
        + "/ai/run/@cf/cloudflare/clef-flash"
    )
    assert request.headers["authorization"] == "Bearer synthetic-token"
    assert request.headers["content-type"] == "application/json"
    assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 3.0)
    body = json.loads(request.content)
    assert body["model"] == "clef-flash"
    assert body["state"] == json.dumps(
        clef_state(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    assert body["questions"] == {
        key: {name: q[name] for name in ("type", "instructions", "criteria")}
        for key, q in clef_contract()["questions"].items()
    }
    assert result.choice.status is Status.OK
    assert result.choice.evidence is Evidence.CLEF_PROBABILITIES
    assert result.choice.choice == "strong"
    assert result.choice.margin == pytest.approx(0.7)
    assert result.choice.usage.prompt_tokens == 300
    assert result.choice.usage.completion_tokens == 0
    assert result.choice.cost is None and result.choice.self_confidence is None
    assert result.choice.latency_s > 0
    assert result.ordinal is not None and result.ordinal.expectation == 4
    assert not hasattr(result.ordinal, "distribution")
    assert not hasattr(result.ordinal, "choice")


def test_clef_standard_scorer_adapter_returns_choice_only_with_one_request() -> None:
    body = clef_response()
    choice = body["result"]["answers"]["taste_fit"]
    choice["choice"] = "thai"
    choice["probabilities"] = {"thai": 0.8, "ramen": 0.15, "none": 0.05}
    body["result"]["answers"] = {"q": choice}
    wire = ClefWire(body)
    result = wire.scorer.score(question())
    assert result.status is Status.OK and result.choice == "thai"
    assert len(wire.requests) == 1
    assert set(json.loads(wire.requests[0].content)["questions"]) == {"q"}
    lone = wire.scorer.score(question(Option("only", "Only")))
    assert lone.evidence is Evidence.DETERMINISTIC and len(wire.requests) == 1


def test_clef_replays_every_archived_vector_through_a_synthetic_native_envelope() -> None:
    archive = json.loads(
        (Path(__file__).parent / "fixtures/clef/isolated-vectors.json").read_text("utf-8")
    )
    for row in archive["cases"]:
        wire = ClefWire(clef_response(row["probabilities"]))
        result = wire.scorer.score_profile_case(clef_state())
        assert result.choice.status is Status.OK, row["id"]
        assert result.ordinal is not None
        expected = math.fsum(int(i) * p for i, p in row["probabilities"].items()) / math.fsum(
            row["probabilities"].values()
        )
        assert result.ordinal.expectation == pytest.approx(expected)
        if row["id"] == "F02":
            assert 25 * result.ordinal.expectation == pytest.approx(94.0925)


@pytest.mark.parametrize("level", range(5))
def test_clef_pure_levels_preserve_zero_and_endpoints(level: int) -> None:
    probabilities = {str(i): float(i == level) for i in range(5)}
    result = ClefWire(clef_response(probabilities)).scorer.score_profile_case(clef_state())
    assert result.ordinal is not None and result.ordinal.expectation == level


def test_clef_semantic_unknown_remains_valid_evidence_distinct_from_unavailability() -> None:
    unknown = ClefWire(clef_response(dict.fromkeys("01234", 0.2), unknown=True))
    result = unknown.scorer.score_profile_case(clef_state())
    assert result.choice.status is Status.OK and result.choice.choice == "unknown"
    assert result.choice.error is None and result.choice.detail == ""
    assert result.ordinal is not None and result.ordinal.expectation == 2
    failed = ClefWire(httpx.Response(503)).scorer.score_profile_case(clef_state())
    assert failed.choice.status is Status.ERROR and failed.choice.choice is None
    assert failed.choice.detail == "service_unavailable" and failed.ordinal is None


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (("success",), False, "service_unavailable"),
        (("success",), 1, "service_unavailable"),
        (("errors",), [{"message": "private-provider-error"}], "service_unavailable"),
        (("result", "model"), "clef", "configuration"),
        (("result", "answers"), {}, "malformed_response"),
        (("result", "answers", "extra"), {}, "malformed_response"),
        (("result", "answers", "taste_fit", "type"), "score", "malformed_response"),
        (("result", "answers", "taste_fit", "choice"), "poor", "malformed_response"),
        (("result", "answers", "taste_fit", "choice"), True, "malformed_response"),
        (("result", "answers", "taste_fit", "confidence"), True, "malformed_response"),
        (("result", "answers", "taste_fit", "probabilities"), {}, "malformed_response"),
        (("result", "answers", "fit_score", "type"), "choice", "malformed_response"),
        (("result", "answers", "fit_score", "legend"), {}, "malformed_response"),
        (("result", "answers", "fit_score", "score"), 3, "malformed_response"),
        (("result", "answers", "fit_score", "score"), True, "malformed_response"),
        (("result", "answers", "fit_score", "score"), "4", "malformed_response"),
        (("result", "answers", "fit_score", "score"), 4.001, "malformed_response"),
        (("result", "answers", "fit_score", "confidence"), -0.1, "malformed_response"),
        (("result", "usage"), {}, "malformed_response"),
        (("result", "usage", "input_tokens"), True, "malformed_response"),
        (("result", "usage", "output_tokens"), -1, "malformed_response"),
        (("result", "usage", "input_tokens"), 3.5, "malformed_response"),
        (("result", "refusal"), "private-provider-error", "refused"),
        (("result", "answers", "taste_fit", "refusal"), "private-provider-error", "refused"),
        (("result", "answers", "fit_score", "type"), "refusal", "refused"),
    ],
)
def test_clef_rejects_invalid_native_fields_without_partial_evidence(
    path: tuple[str, ...], value: Any, reason: str
) -> None:
    body = clef_response()
    current = body
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    wire = ClefWire(body)
    result = wire.scorer.score_profile_case(clef_state())
    assert result.choice.status is Status.ERROR
    assert result.choice.detail == reason
    assert result.choice.choice is None and result.choice.distribution is None
    assert result.ordinal is None and len(wire.requests) == 1
    assert "private-provider-error" not in repr(result)


@pytest.mark.parametrize("answer", ["taste_fit", "fit_score"])
@pytest.mark.parametrize("bad", [True, False, "0", -0.1, 1.1, math.nan, math.inf, None])
def test_clef_rejects_nonprobability_values_in_both_answers(answer: str, bad: Any) -> None:
    body = clef_response()
    key = "strong" if answer == "taste_fit" else "4"
    body["result"]["answers"][answer]["probabilities"][key] = bad
    result = ClefWire(body).scorer.score_profile_case(clef_state())
    assert result.choice.detail == "malformed_response" and result.ordinal is None


@pytest.mark.parametrize("answer", ["taste_fit", "fit_score"])
@pytest.mark.parametrize("mutation", ["missing", "extra", "sum"])
def test_clef_requires_complete_probability_keys_and_sums(answer: str, mutation: str) -> None:
    body = clef_response()
    probs = body["result"]["answers"][answer]["probabilities"]
    key = "strong" if answer == "taste_fit" else "4"
    if mutation == "missing":
        del probs[key]
    elif mutation == "extra":
        probs["extra"] = 0
    else:
        probs[key] = 0.5
    result = ClefWire(body).scorer.score_profile_case(clef_state())
    assert result.choice.detail == "malformed_response" and result.ordinal is None


def test_clef_normalizes_only_small_rounding_differences_and_checks_wire_tolerance() -> None:
    body = clef_response({"0": 0, "1": 0, "2": 0.2, "3": 0.3, "4": 0.4999})
    body["result"]["answers"]["taste_fit"]["probabilities"]["strong"] = 0.7999
    wire_score = body["result"]["answers"]["fit_score"]["score"]
    body["result"]["answers"]["fit_score"]["score"] = wire_score - 0.00059
    result = ClefWire(body).scorer.score_profile_case(clef_state())
    assert result.choice.status is Status.OK and result.ordinal is not None
    assert math.fsum(result.ordinal.probabilities) == pytest.approx(1)
    body["result"]["answers"]["fit_score"]["score"] = wire_score - 0.00061
    assert (
        ClefWire(body).scorer.score_profile_case(clef_state()).choice.detail == "malformed_response"
    )


@pytest.mark.parametrize("change", ["extra", "missing", "changed", "swapped"])
def test_clef_requires_the_exact_five_level_legend(change: str) -> None:
    body = clef_response()
    legend = body["result"]["answers"]["fit_score"]["legend"]
    if change == "extra":
        legend["5"] = "Another level"
    elif change == "missing":
        del legend["0"]
    elif change == "changed":
        legend["0"] = "A different rubric"
    else:
        legend["0"], legend["1"] = legend["1"], legend["0"]
    assert (
        ClefWire(body).scorer.score_profile_case(clef_state()).choice.detail == "malformed_response"
    )


@pytest.mark.parametrize(
    "path",
    [
        ("result", "usage"),
        ("result", "answers", "fit_score"),
        ("result", "answers", "taste_fit", "type"),
        ("result", "answers", "taste_fit", "choice"),
        ("result", "answers", "taste_fit", "confidence"),
        ("result", "answers", "fit_score", "score"),
        ("result", "answers", "fit_score", "confidence"),
    ],
)
def test_clef_rejects_missing_required_native_fields(path: tuple[str, ...]) -> None:
    body = clef_response()
    current = body
    for key in path[:-1]:
        current = current[key]
    del current[path[-1]]
    assert (
        ClefWire(body).scorer.score_profile_case(clef_state()).choice.detail == "malformed_response"
    )


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(401, text="private-provider-error"), "configuration"),
        (httpx.Response(403, text="private-provider-error"), "configuration"),
        (httpx.Response(429, text="private-provider-error"), "rate_limited"),
        (httpx.Response(500, text="private-provider-error"), "service_unavailable"),
        (httpx.Response(503, text="private-provider-error"), "service_unavailable"),
        (httpx.Response(400, text="private-provider-error"), "configuration"),
        (httpx.Response(302, headers={"location": "https://other.test"}), "configuration"),
        (httpx.ReadTimeout("synthetic-token private-profile"), "timeout"),
        (httpx.ConnectError("synthetic-token private-profile"), "service_unavailable"),
        (httpx.Response(200, text="<html>private-provider-error"), "malformed_response"),
        (httpx.Response(200, text='{"success":true,"success":false}'), "malformed_response"),
        (httpx.Response(200, json=[]), "malformed_response"),
    ],
)
def test_clef_failures_are_sanitized_results_with_no_retries_or_fallback(
    response: Any, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    wire = ClefWire(response)
    result = wire.scorer.score_profile_case(clef_state())
    assert result.choice.status is Status.ERROR and result.choice.detail == reason
    assert result.ordinal is None and len(wire.requests) == 1
    assert wire.scorer.score(question()).detail == reason
    assert len(wire.requests) == 2  # one per explicit operation, even on redirects or failures
    for secret in ("synthetic-token", "private-profile", "private-provider-error", "thai"):
        assert secret not in repr(result) + caplog.text


def test_clef_only_retains_allowlisted_validated_response_headers() -> None:
    wire = ClefWire(
        httpx.Response(
            429,
            headers={
                "CF-AI-Neurons": "1.04",
                "Retry-After": "60",
                "CF-AI-Req-ID": "12345678-abcd-1234-abcd-123456789abc",
                "CF-Ray": "1234567890abcdef-ORD",
                "Set-Cookie": "private-cookie",
                "Authorization": "private-token",
                "X-Other": "private-profile",
            },
        )
    )
    result = wire.scorer.score_profile_case(clef_state())
    assert result.metadata.neurons == 1.04
    assert result.metadata.retry_after == "60"
    assert dict(result.metadata.request_ids) == {
        "cf-ai-req-id": "12345678-abcd-1234-abcd-123456789abc",
        "cf-ray": "1234567890abcdef-ORD",
    }
    assert "private" not in repr(result)
    bad_headers = httpx.Response(
        200,
        json=clef_response(),
        headers={
            "cf-ai-neurons": "NaN",
            "retry-after": "private-token",
            "cf-ray": "private-profile",
        },
    )
    metadata = ClefWire(bad_headers).scorer.score_profile_case(clef_state()).metadata
    assert metadata.neurons is None and metadata.retry_after is None and not metadata.request_ids


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("profile", "likes"), ["not spicy"]),
        (("profile", "likes"), ["thai", "thai"]),
        (("profile", "likes"), "thai"),
        (("profile", "likes"), [True]),
        (("profile", "source"), "inferred"),
        (("profile", "allergy"), "peanuts"),
        (("candidate", "name"), "private-brand-name"),
        (("candidate", "source"), "invented"),
        (("candidate", "source_release"), ""),
        (("candidate", "source_release"), "x" * 129),
        (("candidate", "categories"), ["private profile text"]),
        (("candidate", "missing_facts"), ["menus"]),
        (("profile",), []),
        (("extra_profile",), {}),
    ],
)
def test_clef_rejects_states_outside_frozen_soft_schema_without_sending(
    path: tuple[str, ...], value: Any
) -> None:
    state = clef_state()
    current = state
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    wire = ClefWire(clef_response())
    result = wire.scorer.score_profile_case(state)
    assert result.choice.detail == "invalid_input" and not wire.requests


def test_clef_rejects_no_taste_and_oversized_complete_request_without_truncating() -> None:
    wire = ClefWire(clef_response())
    state = clef_state()
    state["profile"]["likes"] = state["profile"]["dislikes"] = []
    assert wire.scorer.score_profile_case(state).choice.detail == "invalid_input"
    state = clef_state()
    state["candidate"]["categories"] = [f"category_{i:04d}" for i in range(1000)]
    assert wire.scorer.score_profile_case(state).choice.detail == "invalid_input"
    assert not wire.requests


def test_clef_factory_is_separate_from_request_signals_and_never_constructs_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("fit constructed an unrelated transport")

    monkeypatch.setattr("makan.providers.scoring.factory.OpenRouterTransport", refuse)
    config = FitConfig("clef", CLEF_FIT_MODEL, "a" * 32, "synthetic-token")
    wire = ClefWire(clef_response())
    scorer = build_fit_scorer(config, enabled=True, client=wire.client)
    assert isinstance(scorer, ClefScorer)
    assert scorer.score_profile_case(clef_state()).choice.status is Status.OK
    with pytest.raises(ConfigError, match="MAKAN_SCORER_BACKEND"):
        ScorerConfig("clef", CLEF_FIT_MODEL)
    assert build_scorer(ScorerConfig()) is None


def test_disabled_clef_and_local_fit_need_no_credentials_or_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("disabled fit opened a client")

    monkeypatch.setattr(httpx, "Client", refuse)
    config = Config.from_env(
        {"MAKAN_MODEL": "m", "MAKAN_FIT_BACKEND": "clef", "MAKAN_FIT_MODEL": CLEF_FIT_MODEL}
    )
    assert not config.fit_enabled
    assert build_fit_scorer(config.fit, enabled=config.fit_enabled) is None
    assert build_fit_scorer(FitConfig(), enabled=True) is None
    # Even an explicitly enabled adapter creates its owned client only at the first request.
    scorer = build_fit_scorer(
        FitConfig("clef", CLEF_FIT_MODEL, "a" * 32, "synthetic-token"), enabled=True
    )
    assert scorer is not None
    scorer.close()


def test_disabled_clef_keeps_vercel_import_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("import constructed Cloudflare or opened a network connection")

    monkeypatch.setattr("makan.providers.scoring.factory.CloudflareTransport", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    for name in (
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
        "MAKAN_MODEL",
        "OPENROUTER_API_KEY",
        "MAKAN_DATABASE_URL",
        "MAKAN_DEMO",
        "SUPABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAKAN_FIT_ENABLED", "0")
    monkeypatch.setenv("MAKAN_FIT_BACKEND", "clef")
    monkeypatch.setenv("MAKAN_FIT_MODEL", CLEF_FIT_MODEL)
    monkeypatch.setenv("MAKAN_GROUPS_ENABLED", "0")
    monkeypatch.setenv("MAKAN_ACCOUNTS_ENABLED", "0")
    monkeypatch.setenv("MAKAN_PLACES_ONLY", "1")
    spec = importlib.util.spec_from_file_location(
        "clef_vercel_entrypoint", Path(__file__).resolve().parent.parent / "app.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.app is not None


@pytest.mark.parametrize(
    ("account", "token"),
    [("", ""), ("../elsewhere", "synthetic-token"), ("a" * 32, ""), ("a" * 32, "bad\ntoken")],
)
def test_enabled_clef_factory_requires_valid_server_credentials(account: str, token: str) -> None:
    with pytest.raises(ConfigError):
        build_fit_scorer(FitConfig("clef", CLEF_FIT_MODEL, account, token), enabled=True)


@pytest.mark.parametrize(
    ("backend", "model"),
    [
        ("other", ""),
        ("clef", ""),
        ("clef", "clef-flash"),
        ("clef", "@cf/cloudflare/clef"),
        ("clef", "@cf/other/clef-flash"),
        ("local", CLEF_FIT_MODEL),
    ],
)
def test_fit_rejects_unsupported_backend_model_selector_combinations(
    backend: str, model: str
) -> None:
    with pytest.raises(ConfigError):
        FitConfig(backend, model)


def test_fit_defaults_and_environment_keep_credentials_out_of_repr() -> None:
    config = Config.from_env({"MAKAN_MODEL": "m"})
    assert config.fit == FitConfig() and config.scorer == ScorerConfig()
    env = {
        "MAKAN_MODEL": "m",
        "MAKAN_FIT_ENABLED": "1",
        "MAKAN_FIT_BACKEND": "clef",
        "MAKAN_FIT_MODEL": CLEF_FIT_MODEL,
        "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
        "CLOUDFLARE_API_TOKEN": "synthetic-token",
    }
    config = Config.from_env(env)
    assert config.fit_enabled and config.fit.backend == "clef"
    assert config.scorer.backend == "none"
    assert config.fit.cloudflare_account_id == "a" * 32
    assert config.fit.cloudflare_api_token == "synthetic-token"
    assert "synthetic-token" not in repr(config) and "a" * 32 not in repr(config)
