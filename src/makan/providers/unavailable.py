"""A provider for a run with no language model, which fails loudly if anything asks it."""

from __future__ import annotations

from makan.providers.base import Completion, Message, ProviderError, ToolSpec


class UnavailableProvider:
    """Stands in where a run has no model, such as places-only browsing.

    Nothing should call it, because Browse nearby never reads a model. If something does, that is
    a bug, so it raises and no request goes anywhere.
    """

    def complete(self, *, model: str, messages: list[Message], tools: list[ToolSpec]) -> Completion:
        raise ProviderError("no language model is configured for this run")
