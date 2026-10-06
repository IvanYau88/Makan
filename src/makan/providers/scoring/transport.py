"""A JSON POST to OpenRouter for the scorers, which need request fields `Provider.complete` lacks.

The chat-completions provider carries only messages and tools. Scoring needs logprob and
structured-output parameters, and Jev lives on a different path, so this is a separate surface
and `Provider.complete` stays unchanged.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

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
