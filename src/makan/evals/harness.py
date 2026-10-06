"""Run a scorer over a decision point's labelled cases and report how well it did.

The harness takes any `Scorer`, so a live backend can be compared later with the same code.
Nothing here makes a request by itself: the caller passes the scorer, and ordinary runs use only
the deterministic fakes.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from makan.decisions import DECISIONS
from makan.evals.cases import EvalCase, load_cases
from makan.memory.gate import RuleGate
from makan.providers.scoring import (
    Evidence,
    Question,
    Scorer,
    ScoreResult,
    Status,
)
from makan.signals import MIN_MARGIN

_EPSILON = 1e-12


@dataclass(frozen=True)
class CaseOutcome:
    case: EvalCase
    result: ScoreResult
    accepted: bool  # the result passed the margin policy, so a caller would act on it

    @property
    def correct(self) -> bool:
        return self.result.choice == self.case.label


@dataclass(frozen=True)
class ClassScores:
    precision: float | None
    recall: float | None
    f1: float | None


@dataclass(frozen=True)
class EvalReport:
    decision: str
    version: str
    backend: str
    model: str
    split: str | None
    min_margin: float
    cases: int
    accuracy: float  # a case with no choice counts as wrong
    coverage: float  # the share of cases whose result passed the margin policy
    accepted_error_rate: float | None  # wrong among accepted, None when nothing was accepted
    macro_f1: float
    per_class: dict[str, ClassScores]
    brier: float | None  # over results with a full distribution only
    log_loss: float | None
    statuses: dict[str, int]
    near_tie_cases: int
    near_tie_accuracy: float | None
    near_tie_mean_margin: float | None
    latency_p50_s: float
    latency_p95_s: float
    tokens: int
    cost: float | None  # the billed total, None when no result reported a cost
    cost_unreported: int  # results that said nothing about cost


class RuleBaseline:
    """The existing rule gate as a scorer for the retrieval gate, to compare backends against.

    It has no distribution, so its answers are degraded and never pass a margin policy.
    """

    name = "rule"
    model = ""

    def __init__(self) -> None:
        self._rule = RuleGate()

    def score(self, question: Question) -> ScoreResult:
        if question.decision != "retrieval_gate":
            raise ValueError("the rule baseline only answers the retrieval gate")
        choice = "personalize" if self._rule.decide(question.state).lookup else "skip"
        return ScoreResult(
            Status.DEGRADED,
            self.name,
            self.model,
            choice=choice,
            evidence=Evidence.DETERMINISTIC,
            detail="a plain rule, with no distribution",
        )


def run_eval(
    scorer: Scorer,
    decision: str,
    cases: Iterable[EvalCase] | None = None,
    *,
    split: str | None = None,
    min_margin: float = MIN_MARGIN,
) -> EvalReport:
    """Score every case of `decision` (optionally one split) and summarize the outcomes."""
    chosen = [
        c for c in (load_cases(decision) if cases is None else cases) if split in (None, c.split)
    ]
    if not chosen:
        raise ValueError(f"no cases for {decision} in split {split!r}")
    decision_spec = DECISIONS[decision]
    outcomes = []
    for case in chosen:
        result = scorer.score(decision_spec.question(case.text))
        outcomes.append(CaseOutcome(case, result, result.confident_choice(min_margin) is not None))
    return _report(scorer, decision_spec.version, decision, split, min_margin, outcomes)


def compare(
    scorers: Mapping[str, Scorer],
    decisions: Iterable[str] = tuple(DECISIONS),
    *,
    split: str | None = None,
    min_margin: float = MIN_MARGIN,
) -> list[EvalReport]:
    """Run each scorer over each decision. A baseline that cannot answer a decision is skipped."""
    reports = []
    for decision in decisions:
        for scorer in scorers.values():
            if isinstance(scorer, RuleBaseline) and decision != "retrieval_gate":
                continue
            reports.append(run_eval(scorer, decision, split=split, min_margin=min_margin))
    return reports


def _report(
    scorer: Scorer,
    version: str,
    decision: str,
    split: str | None,
    min_margin: float,
    outcomes: list[CaseOutcome],
) -> EvalReport:
    n = len(outcomes)
    accepted = [o for o in outcomes if o.accepted]
    labels = [o.id for o in DECISIONS[decision].options]
    per_class = {label: _class_scores(label, outcomes) for label in labels}
    f1s = [s.f1 if s.f1 is not None else 0.0 for s in per_class.values()]
    scored = [o for o in outcomes if o.result.distribution is not None]
    near_ties = [o for o in outcomes if "near_tie" in o.case.tags]
    near_margins = [m for o in near_ties if (m := o.result.margin) is not None]
    latencies = sorted(o.result.latency_s for o in outcomes)
    costs = [o.result.cost for o in outcomes if o.result.cost is not None]
    return EvalReport(
        decision=decision,
        version=version,
        backend=scorer.name,
        model=scorer.model,
        split=split,
        min_margin=min_margin,
        cases=n,
        accuracy=sum(o.correct for o in outcomes) / n,
        coverage=len(accepted) / n,
        accepted_error_rate=(
            sum(not o.correct for o in accepted) / len(accepted) if accepted else None
        ),
        macro_f1=math.fsum(f1s) / len(f1s),
        per_class=per_class,
        brier=_mean(_brier(o) for o in scored),
        log_loss=_mean(_log_loss(o) for o in scored),
        statuses=dict(Counter(o.result.status.value for o in outcomes)),
        near_tie_cases=len(near_ties),
        near_tie_accuracy=_mean(float(o.correct) for o in near_ties),
        near_tie_mean_margin=_mean(near_margins),
        latency_p50_s=_percentile(latencies, 0.50),
        latency_p95_s=_percentile(latencies, 0.95),
        tokens=sum(o.result.usage.total_tokens for o in outcomes),
        cost=math.fsum(costs) if costs else None,
        cost_unreported=n - len(costs),
    )


def _class_scores(label: str, outcomes: list[CaseOutcome]) -> ClassScores:
    predicted = [o for o in outcomes if o.result.choice == label]
    actual = [o for o in outcomes if o.case.label == label]
    hits = sum(o.correct for o in predicted)
    precision = hits / len(predicted) if predicted else None
    recall = hits / len(actual) if actual else None
    if precision is None or recall is None or precision + recall == 0:
        f1 = 0.0 if actual or predicted else None
        return ClassScores(precision, recall, f1)
    return ClassScores(precision, recall, 2 * precision * recall / (precision + recall))


def _probabilities(outcome: CaseOutcome) -> dict[str, float]:
    distribution = outcome.result.distribution
    assert distribution is not None
    return dict(distribution.probabilities)


def _brier(outcome: CaseOutcome) -> float:
    return math.fsum(
        (p - (1.0 if option == outcome.case.label else 0.0)) ** 2
        for option, p in _probabilities(outcome).items()
    )


def _log_loss(outcome: CaseOutcome) -> float:
    return -math.log(max(_probabilities(outcome)[outcome.case.label], _EPSILON))


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return math.fsum(items) / len(items) if items else None


def _percentile(sorted_values: list[float], q: float) -> float:
    """Nearest rank, so a p95 is a latency that really happened."""
    if not sorted_values:
        return 0.0
    return sorted_values[max(math.ceil(q * len(sorted_values)) - 1, 0)]
