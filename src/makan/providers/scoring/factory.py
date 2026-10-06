"""Build the configured scorer. The backend and model come from config, never from code."""

from __future__ import annotations

import httpx

from makan.config import ScorerConfig
from makan.providers.scoring.base import Scorer
from makan.providers.scoring.fake import FakeScorer
from makan.providers.scoring.jev import JevScorer
from makan.providers.scoring.logprob import LogprobScorer
from makan.providers.scoring.structured import StructuredScorer
from makan.providers.scoring.transport import OpenRouterTransport


def build_scorer(
    config: ScorerConfig, *, openrouter_api_key: str = "", client: httpx.Client | None = None
) -> Scorer | None:
    """The scorer for `config.backend`, or None when scoring is off.

    Only the chosen backend is built, and there is no fallback chain between backends: a failed
    request is a result the decision point handles, so nothing ever escalates to Jev on its own.
    Raises ProviderError when a hosted backend has no API key.
    """
    if config.backend == "none":
        return None
    if config.backend == "fake":
        return FakeScorer()
    transport = OpenRouterTransport(openrouter_api_key, timeout=config.timeout_s, client=client)
    if config.backend == "logprob":
        return LogprobScorer(transport, config.model)
    if config.backend == "structured":
        return StructuredScorer(transport, config.model)
    return JevScorer(transport, config.model)
