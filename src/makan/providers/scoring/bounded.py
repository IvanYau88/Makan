"""A request cap around any scorer."""

from __future__ import annotations

import threading

from makan.providers.scoring.base import ErrorKind, Question, Scorer, ScoreResult, Status


class BoundedScorer:
    """Caps how many requests a scorer may make, so a run or an eval has a hard ceiling.

    A call past the cap returns a budget error without reaching the backend.
    """

    def __init__(self, inner: Scorer, max_requests: int) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be at least 1")
        self._inner = inner
        self._remaining = max_requests
        self._lock = threading.Lock()
        self.name = inner.name
        self.model = inner.model

    def score(self, question: Question) -> ScoreResult:
        with self._lock:
            allowed = self._remaining > 0
            if allowed:
                self._remaining -= 1
        if not allowed:
            return ScoreResult(
                Status.ERROR,
                self.name,
                self.model,
                error=ErrorKind.BUDGET,
                detail="request cap reached",
            )
        return self._inner.score(question)
