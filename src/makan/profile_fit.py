"""Optional display-only Clef strategy, routed exclusively through shared admission.

The async edge is bounded and cancellable. Hard eligibility runs before this module;
no score, exception or upstream text can change a candidate or its ordering.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

import httpx

from makan.config import Config
from makan.fit import CATEGORY_CUISINES, FitAssessment, FitReason, cuisine_fit
from makan.fit_inputs import FitInputs
from makan.providers.scoring.base import Evidence, Status
from makan.providers.scoring.clef import ClefScorer, ProfileCaseResult
from makan.providers.scoring.gateway import GATEWAY_REASONS_VERSION, GatewayKeys
from makan.providers.scoring.transport import ClefReason, _strict_json
from makan.solo import RankedCandidate

FIT_MIN_PROBABILITY = 0.70
FIT_BUDGET_S = 8.0
CASE_DEADLINE_S = 3.0
MAX_CONCURRENCY = 4
MAX_REPLY_BYTES = 65_536


def model_assessment(
    reason: FitReason, score: int | None = None, *, cached: bool = False
) -> FitAssessment:
    return FitAssessment(score, reason, basis="profile_model", cached=cached)


def accepted_assessment(result: ProfileCaseResult, *, cached: bool) -> FitAssessment:
    """Apply the frozen proposed policy, never treating confidence as a plate score."""
    choice, ordinal = result.choice, result.ordinal
    if choice.status != Status.OK:
        try:
            reason = FitReason(ClefReason(choice.detail).value)
        except ValueError:
            reason = FitReason.MALFORMED_RESPONSE
        return model_assessment(reason)
    distribution = choice.distribution
    if (
        choice.evidence != Evidence.CLEF_PROBABILITIES
        or distribution is None
        or ordinal is None
        or choice.choice != distribution.top
        or distribution.margin == 0
        or dict(distribution.probabilities)[distribution.top] + 1e-9 < FIT_MIN_PROBABILITY
        or choice.choice == "unknown"
    ):
        return model_assessment(FitReason.INSUFFICIENT, cached=cached)
    expectation = ordinal.expectation
    level = min(5, math.floor(1 + expectation + 0.5))
    group = "poor" if level <= 2 else "partial" if level == 3 else "strong"
    if choice.choice != group:
        return model_assessment(FitReason.INSUFFICIENT, cached=cached)
    return model_assessment(FitReason.MODEL, math.floor(25 * expectation + 0.5), cached=cached)


def profile_state(inputs: FitInputs, category: str, source: str) -> dict[str, Any] | None:
    """Only explicit bounded cuisines and actual source categories; no names or hard facts."""
    if not source.startswith("overture:") or category not in CATEGORY_CUISINES:
        return None
    release = source.removeprefix("overture:")
    if not 1 <= len(release) <= 128:
        return None
    return {
        "profile": {
            "likes": sorted(set(inputs.likes)),
            "dislikes": sorted(set(inputs.dislikes)),
            "source": "user_stated",
        },
        "candidate": {
            "categories": [category],
            "source": "overture_category",
            "source_release": release,
            "missing_facts": [],
        },
    }


@dataclass(frozen=True)
class FitSummary:
    strategy: Literal["local", "clef"]
    rated: int
    unrated: int
    cached: int
    duration_ms: int
    reasons: tuple[tuple[str, int], ...]

    def trace_summary(self) -> dict[str, Any]:
        """Only bounded counts and vocabulary; never inputs, evidence, keys or exceptions."""
        return {
            "strategy": self.strategy,
            "rated": self.rated,
            "unrated": self.unrated,
            "cached": self.cached,
            "duration_ms": self.duration_ms,
            "reasons": dict(self.reasons),
        }


class FitStrategy:
    """The one web decoration strategy, independent from the solo graph's signal scorer."""

    def __init__(
        self, config: Config, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.config = config
        self.online = config.fit_enabled and config.fit.online
        self._transport = transport
        # A parser only: no direct transport, client, credential lookup or network at startup.
        self._parser = ClefScorer(None, config.fit.model) if self.online else None

    def _keys(self) -> GatewayKeys:
        fit = self.config.fit
        assert fit.gateway_scope in ("preview", "production")
        return GatewayKeys(
            fit.hmac_secret.encode(), cast(Literal["preview", "production"], fit.gateway_scope)
        )

    async def purge(self, scope: str) -> None:
        """Best-effort withdrawal purge; callers still discard/rotate their session immediately."""
        if not self.online or not self.config.fit.ready:
            return
        try:
            async with httpx.AsyncClient(
                transport=self._transport, follow_redirects=False
            ) as client:
                async with asyncio.timeout(CASE_DEADLINE_S):
                    await client.post(
                        self.config.fit.gateway_url + "/v1/purge",
                        json={"scope": self._keys().owner_scope(scope)},
                        headers=self._headers(),
                        timeout=CASE_DEADLINE_S,
                    )
        except (TimeoutError, httpx.HTTPError):
            pass

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.fit.gateway_token}"}

    async def assess(
        self,
        ranked: Sequence[RankedCandidate],
        inputs: FitInputs,
        *,
        source: str,
        consent: bool,
        scope: str | None,
        consent_revision: str = "",
        cancelled: Callable[[], bool] = lambda: False,
    ) -> tuple[tuple[RankedCandidate, ...], FitSummary]:
        start = time.monotonic()
        assessments = [model_assessment(FitReason.DEADLINE) for _ in ranked]
        if not self.online:
            assessments = [
                cuisine_fit(
                    r.place.category,
                    inputs.likes,
                    inputs.dislikes,
                    enabled=self.config.fit_enabled,
                    problem=inputs.problem,
                )
                for r in ranked
            ]
        else:
            problem = inputs.problem
            if problem is None and not inputs.likes and not inputs.dislikes:
                problem = FitReason.NO_TASTE
            if problem is None and set(inputs.likes) & set(inputs.dislikes):
                problem = FitReason.CONFLICT
            if problem is None and not set((*inputs.likes, *inputs.dislikes)) <= set(
                CATEGORY_CUISINES.values()
            ):
                problem = FitReason.UNSUPPORTED
            if problem is None and not consent:
                problem = FitReason.NO_CONSENT
            if problem is None and not self.config.fit.ready:
                problem = FitReason.CONFIGURATION
            if problem is None and scope is None:
                problem = FitReason.NO_CONSENT
            if problem is not None:
                assessments = [model_assessment(problem) for _ in ranked]
            else:
                assert scope is not None
                async with httpx.AsyncClient(
                    transport=self._transport, follow_redirects=False, timeout=CASE_DEADLINE_S
                ) as client:
                    queue = iter(enumerate(ranked))

                    async def worker() -> None:
                        for index, candidate in queue:
                            # Queue time counts. Do not start a case too late to finish.
                            if (
                                cancelled()
                                or time.monotonic() - start > FIT_BUDGET_S - CASE_DEADLINE_S
                            ):
                                return
                            state = profile_state(inputs, candidate.place.category, source)
                            if state is None:
                                assessments[index] = model_assessment(FitReason.INSUFFICIENT)
                                continue
                            assessments[index] = await self._case(
                                client,
                                state,
                                scope,
                                source + ":" + candidate.place.id,
                                consent_revision + ":" + inputs.revision,
                            )

                    try:
                        async with asyncio.timeout(
                            max(0, FIT_BUDGET_S - (time.monotonic() - start))
                        ):
                            await asyncio.gather(*(worker() for _ in range(MAX_CONCURRENCY)))
                    except TimeoutError:
                        pass
        decorated = tuple(replace(r, fit=a) for r, a in zip(ranked, assessments, strict=True))
        reasons: dict[str, int] = {}
        for a in assessments:
            reasons[a.reason.value] = reasons.get(a.reason.value, 0) + 1
        rated = sum(a.score is not None for a in assessments)
        return decorated, FitSummary(
            "clef" if self.online else "local",
            rated,
            len(ranked) - rated,
            sum(a.cached for a in assessments),
            round((time.monotonic() - start) * 1000),
            tuple(sorted(reasons.items())),
        )

    async def _case(
        self,
        client: httpx.AsyncClient,
        state: dict[str, Any],
        scope: str,
        identity: str,
        consent_revision: str,
    ) -> FitAssessment:
        assert self._parser is not None
        try:
            body = self._parser.profile_body(state)
            now = int(time.time() * 1000)
            # Conservative daily freshness boundary, stable across searches and cold instances.
            expiry = (now // 86_400_000 + 1) * 86_400_000
            envelope = self._keys().envelope(
                body,
                opaque_owner_or_tab_nonce=scope,
                profile_revision=hashlib.sha256(
                    json.dumps([state["profile"], consent_revision], sort_keys=True).encode()
                ).hexdigest(),
                evidence_revision="source-category-daily-v1",
                candidate_identity=identity,
                fact_expires_at_ms=expiry,
                now_ms=now,
            )
            async with asyncio.timeout(CASE_DEADLINE_S):
                async with client.stream(
                    "POST",
                    self.config.fit.gateway_url + "/v1/score",
                    json=envelope,
                    headers=self._headers(),
                    follow_redirects=False,
                ) as response:
                    if response.status_code in (401, 403) or 300 <= response.status_code < 400:
                        return model_assessment(FitReason.CONFIGURATION)
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_REPLY_BYTES:
                            break
                    if response.status_code != 200:
                        return model_assessment(_gateway_failure(raw))
                    if len(raw) > MAX_REPLY_BYTES:
                        return model_assessment(FitReason.MALFORMED_RESPONSE)
            data = _strict_json(raw.decode())
            if not _gateway_header(data):
                return model_assessment(FitReason.MALFORMED_RESPONSE)
            if data.get("status") in ("error", "pending", "unknown"):
                reason = ClefReason(data["reason"])
                return model_assessment(FitReason(reason.value))
            if (
                data.get("status") != "ok"
                or data.get("reason") is not None
                or type(data.get("cached")) is not bool
                or not isinstance(data.get("data"), dict)
            ):
                return model_assessment(FitReason.MALFORMED_RESPONSE)
            return accepted_assessment(
                self._parser.parse_profile_response(data["data"]), cached=data["cached"]
            )
        except (TimeoutError, httpx.TimeoutException):
            return model_assessment(FitReason.TIMEOUT)
        except httpx.HTTPError:
            return model_assessment(FitReason.SERVICE_UNAVAILABLE)
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
            return model_assessment(FitReason.MALFORMED_RESPONSE)


def _gateway_header(data: object) -> bool:
    return (
        isinstance(data, dict)
        and type(data.get("v")) is int
        and data["v"] == 1
        and type(data.get("reasons_version")) is int
        and data["reasons_version"] == GATEWAY_REASONS_VERSION
    )


def _gateway_failure(raw: bytes | bytearray) -> FitReason:
    """The gateway's bounded reason for a non-200 reply, or plain unavailability."""
    if len(raw) <= MAX_REPLY_BYTES:
        try:
            data = _strict_json(raw.decode())
            if _gateway_header(data) and data["status"] == "error":
                return FitReason(ClefReason(data["reason"]).value)
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
            pass
    return FitReason.SERVICE_UNAVAILABLE
