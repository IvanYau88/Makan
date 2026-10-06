"""The fixed-answer scorers and their result semantics. No real network calls."""

import json
import math
from typing import Any

import httpx
import pytest

from makan.config import ConfigError, ScorerConfig
from makan.providers import ProviderError
from makan.providers.scoring import (
    BoundedScorer,
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
