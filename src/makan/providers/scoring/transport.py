"""Native JSON transports for scorers, separate from `Provider.complete`.

The chat-completions provider carries only messages and tools. Scoring needs logprob and
structured-output parameters, Jev has its own path, and Clef uses Cloudflare's native API.
OpenRouter's existing transport behavior is unchanged; Clef has its own bounded failures.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import httpx

from makan.config import ConfigError, clef_selector
from makan.providers.base import ProviderBusy, ProviderError
from makan.providers.openrouter import is_busy
from makan.providers.scoring.base import ScorerTimeout


class JsonTransport(Protocol):
    def post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        """Return the decoded JSON object. Raise ProviderError, ProviderBusy, or ScorerTimeout."""
        ...


class OpenRouterTransport:
    def __init__(
        self, api_key: str, *, timeout: float = 15.0, client: httpx.Client | None = None
    ) -> None:
        if not api_key:
            raise ProviderError("OPENROUTER_API_KEY is not set")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._client = client or httpx.Client(timeout=timeout)

    def post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(url, headers=self._headers, json=body)
        except httpx.TimeoutException as exc:
            raise ScorerTimeout("scoring request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"scoring request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            message = f"OpenRouter returned {response.status_code}: {response.text[:300]}"
            raise (ProviderBusy if is_busy(response.status_code) else ProviderError)(message)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("scoring response was not JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError("scoring response was not a JSON object")
        error = data.get("error")
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            raise (ProviderBusy if is_busy(code) else ProviderError)(
                f"OpenRouter error: {str(error)[:300]}"
            )
        return data


def usage_of(data: dict[str, Any]) -> tuple[int, int, float | None]:
    """Prompt tokens, completion tokens, and billed cost from `usage`, as far as it gives them."""
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return 0, 0, None
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0))
    cost = usage.get("cost", data.get("cost"))
    return (
        prompt if isinstance(prompt, int) else 0,
        completion if isinstance(completion, int) else 0,
        float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else None,
    )


class ClefReason(StrEnum):
    """Bounded infrastructure reasons. None of these means semantic taste_fit=unknown."""

    CONFIGURATION = "configuration"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    SERVICE_UNAVAILABLE = "service_unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    REFUSED = "refused"
    INVALID_INPUT = "invalid_input"


@dataclass(frozen=True)
class CloudflareMetadata:
    """Only parsed usage headers and validated correlation ids may leave the transport."""

    neurons: float | None = None
    retry_after: str | None = None
    request_ids: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CloudflareResponse:
    data: dict[str, Any] = field(repr=False)
    metadata: CloudflareMetadata = field(default_factory=CloudflareMetadata)


class ClefError(ProviderError):
    def __init__(self, reason: ClefReason, metadata: CloudflareMetadata | None = None) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.metadata = metadata or CloudflareMetadata()


def _strict_json(text: str) -> Any:
    """Reject duplicate keys and nonstandard numeric constants before validating evidence."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    def constant(value: str) -> Any:
        raise ValueError("non-finite JSON number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def _cloudflare_metadata(headers: httpx.Headers) -> CloudflareMetadata:
    neurons = None
    raw = headers.get("cf-ai-neurons", "")
    if re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,12})?", raw):
        value = float(raw)
        if math.isfinite(value):
            neurons = value
    retry = headers.get("retry-after", "")
    # Keep delay seconds or the standard HTTP-date shape, never arbitrary header text.
    if not (
        re.fullmatch(r"[0-9]{1,10}", retry)
        or re.fullmatch(
            r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), [0-9]{2} "
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
            r"[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT",
            retry,
        )
    ):
        retry = ""
    ids = tuple(
        (key, value)
        for key in ("cf-ai-req-id", "cf-ray")
        if (value := headers.get(key, ""))
        and re.fullmatch(r"[a-fA-F0-9-]{8,64}(?:-[A-Z]{3})?", value)
    )
    return CloudflareMetadata(neurons, retry or None, ids)


class CloudflareTransport:
    """One native request, no redirects or retries, with lazy client ownership.

    This direct adapter is not public request-path wiring or shared quota admission.
    The future gateway supplies coordinated deadlines/admission before production enablement.
    """

    def __init__(
        self,
        account_id: str,
        api_token: str,
        model: str,
        *,
        timeout: float = 3.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.selector = clef_selector(model)
        if not re.fullmatch(r"[a-fA-F0-9]{32}", account_id):
            raise ConfigError("CLOUDFLARE_ACCOUNT_ID must be a 32-character hexadecimal id")
        if (
            not api_token
            or not api_token.isascii()
            or any(c.isspace() or not c.isprintable() for c in api_token)
        ):
            raise ConfigError("CLOUDFLARE_API_TOKEN must be set to a valid server-side token")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ConfigError("Clef timeout must be finite and positive")
        self._url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
        self._headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
        self._client = client
        self._owns_client = client is None
        self._timeout = httpx.Timeout(timeout)

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def post(self, body: dict[str, Any]) -> CloudflareResponse:
        if body.get("model") != self.selector:
            raise ClefError(ClefReason.CONFIGURATION)
        try:
            encoded = json.dumps(
                body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ClefError(ClefReason.INVALID_INPUT) from None
        if len(encoded) > 8192:
            raise ClefError(ClefReason.INVALID_INPUT)
        try:
            if self._client is None:
                self._client = httpx.Client(timeout=self._timeout, follow_redirects=False)
            response = self._client.post(
                self._url,
                headers=self._headers,
                content=encoded,
                timeout=self._timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise ClefError(ClefReason.TIMEOUT) from None
        except httpx.HTTPError:
            raise ClefError(ClefReason.SERVICE_UNAVAILABLE) from None
        metadata = _cloudflare_metadata(response.headers)
        if response.status_code in (401, 403):
            raise ClefError(ClefReason.CONFIGURATION, metadata)
        if response.status_code == 429:
            raise ClefError(ClefReason.RATE_LIMITED, metadata)
        if response.status_code != 200:
            reason = (
                ClefReason.SERVICE_UNAVAILABLE
                if response.status_code >= 500
                else ClefReason.CONFIGURATION
            )
            raise ClefError(reason, metadata)
        try:
            data = _strict_json(response.text)
        except (ValueError, UnicodeError, RecursionError):
            raise ClefError(ClefReason.MALFORMED_RESPONSE, metadata) from None
        if not isinstance(data, dict):
            raise ClefError(ClefReason.MALFORMED_RESPONSE, metadata)
        return CloudflareResponse(data, metadata)
