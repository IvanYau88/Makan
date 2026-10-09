"""Top-probability acceptance and calibration reports for the taste-fit and router decisions.

The existing harness accepts an answer by margin, top minus runner-up, which suits a binary soft
attribute. The router commits on a different rule: the top option's own probability must reach a
threshold, provisionally 0.70, and below it the main agent asks one clarifying question. These are
different quantities, so this module has its own acceptance function and the margin policy in
`makan.evals.harness` is left alone. Passing 0.70 to `confident_choice` would demand a 70 point
margin, which is not the rule.

Nothing here makes a request: the caller passes the scorer, and the offline runs use the fakes.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from makan.decisions import CONTRACT_DECISIONS, INTENT_KINDS, Decision
from makan.evals.cases import EvalCase
from makan.evals.contract_cases import (
    PRESENCE_PREFIX,
    contract_eval_cases,
    load_router_cases,
)
from makan.evals.harness import (
    CaseOutcome,
    ClassScores,
    class_scores,
    mean,
    outcome_brier,
    outcome_log_loss,
    percentile,
)
from makan.providers.scoring import Distribution, Scorer, ScoreResult, Status

# The provisional router threshold. It is a hypothesis to tune on the calibration split, then
# freeze before the held-out split is read.
ROUTER_MIN_PROBABILITY = 0.70
# The grid the sweep reports, in the report's declared range.
DEFAULT_GRID = tuple(round(0.50 + 0.05 * k, 2) for k in range(10))
BINS = 10
_SLACK = 1e-9  # so a probability that is 0.7 up to rounding still meets 0.70


def check_threshold(threshold: float) -> float:
    if isinstance(threshold, bool) or not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError("a probability threshold must be a number above 0 and at most 1")
    return threshold


def accepted_choice(result: ScoreResult, threshold: float) -> str | None:
    """The top option, only when it is a trustworthy top and its probability reaches `threshold`.

    The result must be `ok` with a complete distribution, which `confident_choice(0.0)` checks, and
    that distribution is validated again here in case it was built without `Distribution.of`.
    A degraded, unsupported, or failed result, a tie for the top, and a choice that is not the
    distribution's top are all None, so option order can never break a tie into a decision.
    """
    check_threshold(threshold)
    choice = result.confident_choice(0.0)
    distribution = result.distribution
    if choice is None or distribution is None:
        return None
    ids = tuple(option for option, _ in distribution.probabilities)
    try:
        valid = Distribution.of(dict(distribution.probabilities), ids)
    except ValueError:
        return None
    probability = dict(valid.probabilities)[choice]
    if choice != valid.top or valid.margin <= _SLACK or probability + _SLACK < threshold:
        return None
    return choice


@dataclass(frozen=True)
class ReliabilityBin:
    lower: float
    upper: float
    count: int
    mean_probability: float
    observed_rate: float
    ci_low: float  # a 95% Wilson interval on the observed rate
    ci_high: float


@dataclass(frozen=True)
class Calibration:
    kind: str  # "top_label" for a multiclass question, "present" for a presence question
    cases: int  # results with a full distribution
    ece: float | None
    bins: tuple[ReliabilityBin, ...]


@dataclass(frozen=True)
class SweepPoint:
    threshold: float
    accepted: int
    coverage: float
    accepted_error_rate: float | None


@dataclass(frozen=True)
class ThresholdReport:
    decision: str
    version: str
    backend: str
    model: str
    split: str | None
    threshold: float
    cases: int
    accuracy: float  # a case with no choice counts as wrong
    coverage: float  # the share of cases whose top reached the threshold
    accepted_error_rate: float | None
    macro_f1: float
    per_class: dict[str, ClassScores]
    brier: float | None  # over results with a full distribution only
    log_loss: float | None
    calibration: Calibration
    sweep: tuple[SweepPoint, ...]
    statuses: dict[str, int]
    availability_failures: int  # results that were not ok: never dropped from the counts above
    latency_p50_s: float
    latency_p95_s: float
    tokens: int
    cost: float | None
    cost_unreported: int


@dataclass(frozen=True)
class RouterReport:
    """The seven router questions over the same cases, each calibrated on its own."""

    threshold: float
    split: str | None
    cases: int
    primary: ThresholdReport
    presence: dict[str, ThresholdReport]
    committed: int  # the primary reading reached the threshold
    clarified: int  # it did not, so the main agent would ask one question
    committed_primary_accuracy: float | None
    action_accuracy: float  # commit or clarify, as the case says it should be
    intent_set_exact: float  # accepted-present intents equal the gold set, an unsure one counts out


def run_threshold_eval(
    scorer: Scorer,
    decision: str,
    cases: Iterable[EvalCase] | None = None,
    *,
    split: str | None = None,
    threshold: float = ROUTER_MIN_PROBABILITY,
    grid: Sequence[float] = DEFAULT_GRID,
) -> ThresholdReport:
    """Score every case of one contract decision and report it under top-probability acceptance."""
    check_threshold(threshold)
    spec = _spec(decision)
    chosen = [
        c
        for c in (contract_eval_cases(decision) if cases is None else cases)
        if split in (None, c.split)
    ]
    if not chosen:
        raise ValueError(f"no cases for {decision} in split {split!r}")
    return _report(scorer, spec, split, threshold, grid, _score(scorer, spec, chosen, threshold))


def run_router_eval(
    scorer: Scorer,
    *,
    split: str | None = None,
    threshold: float = ROUTER_MIN_PROBABILITY,
    grid: Sequence[float] = DEFAULT_GRID,
) -> RouterReport:
    """Ask the primary question and the six presence questions of every router case.

    Each question is calibrated separately, because six presence probabilities are not one
    multiclass distribution and are never renormalized into one.
    """
    check_threshold(threshold)
    router = [c for c in load_router_cases() if split in (None, c.split)]
    if not router:
        raise ValueError(f"no router cases in split {split!r}")
    by_id = {c.id: c for c in router}
    primary_spec = CONTRACT_DECISIONS["primary_intent"]
    primary = _score(
        scorer, primary_spec, [c.eval_case("primary_intent") for c in router], threshold
    )
    presence_outcomes = {}
    presence = {}
    for kind in INTENT_KINDS:
        spec = CONTRACT_DECISIONS[f"{PRESENCE_PREFIX}{kind}"]
        outcomes = _score(scorer, spec, [c.eval_case(spec.name) for c in router], threshold)
        presence_outcomes[kind] = {o.case.id: o for o in outcomes}
        presence[kind] = _report(scorer, spec, split, threshold, grid, outcomes)
    committed = [o for o in primary if o.accepted]
    action_hits = 0
    set_hits = 0
    for outcome in primary:
        case = by_id[outcome.case.id]
        action_hits += (case.action == "commit") == outcome.accepted
        active = {
            kind
            for kind in INTENT_KINDS
            if (p := presence_outcomes[kind][case.id]).accepted and p.result.choice == "present"
        }
        set_hits += active == set(case.present)
    return RouterReport(
        threshold=threshold,
        split=split,
        cases=len(router),
        primary=_report(scorer, primary_spec, split, threshold, grid, primary),
        presence=presence,
        committed=len(committed),
        clarified=len(primary) - len(committed),
        committed_primary_accuracy=(
            sum(o.correct for o in committed) / len(committed) if committed else None
        ),
        action_accuracy=action_hits / len(primary),
        intent_set_exact=set_hits / len(primary),
    )


def _spec(decision: str) -> Decision:
    if decision not in CONTRACT_DECISIONS:
        raise ValueError(f"unknown contract decision {decision!r}")
    return CONTRACT_DECISIONS[decision]


def _score(
    scorer: Scorer, spec: Decision, cases: Iterable[EvalCase], threshold: float
) -> list[CaseOutcome]:
    outcomes = []
    for case in cases:
        result = scorer.score(spec.question(case.text))
        outcomes.append(CaseOutcome(case, result, accepted_choice(result, threshold) is not None))
    return outcomes


def _report(
    scorer: Scorer,
    spec: Decision,
    split: str | None,
    threshold: float,
    grid: Sequence[float],
    outcomes: list[CaseOutcome],
) -> ThresholdReport:
    n = len(outcomes)
    accepted = [o for o in outcomes if o.accepted]
    per_class = {o.id: class_scores(o.id, outcomes) for o in spec.options}
    f1s = [s.f1 if s.f1 is not None else 0.0 for s in per_class.values()]
    scored = [o for o in outcomes if o.result.distribution is not None]
    latencies = sorted(o.result.latency_s for o in outcomes)
    costs = [o.result.cost for o in outcomes if o.result.cost is not None]
    return ThresholdReport(
        decision=spec.name,
        version=spec.version,
        backend=scorer.name,
        model=scorer.model,
        split=split,
        threshold=threshold,
        cases=n,
        accuracy=sum(o.correct for o in outcomes) / n,
        coverage=len(accepted) / n,
        accepted_error_rate=(
            sum(not o.correct for o in accepted) / len(accepted) if accepted else None
        ),
        macro_f1=math.fsum(f1s) / len(f1s),
        per_class=per_class,
        brier=mean(outcome_brier(o) for o in scored),
        log_loss=mean(outcome_log_loss(o) for o in scored),
        calibration=calibration(outcomes, positive="present" if _is_binary(spec) else None),
        sweep=sweep(outcomes, grid),
        statuses=dict(Counter(o.result.status.value for o in outcomes)),
        availability_failures=sum(o.result.status is not Status.OK for o in outcomes),
        latency_p50_s=percentile(latencies, 0.50),
        latency_p95_s=percentile(latencies, 0.95),
        tokens=sum(o.result.usage.total_tokens for o in outcomes),
        cost=math.fsum(costs) if costs else None,
        cost_unreported=n - len(costs),
    )


def _is_binary(spec: Decision) -> bool:
    return spec.name.startswith(PRESENCE_PREFIX)


def calibration(outcomes: Sequence[CaseOutcome], *, positive: str | None = None) -> Calibration:
    """Reliability bins and ECE over the results that gave a full distribution.

    A multiclass question is binned on its top option's probability against how often that top
    option was right. With `positive` set, a binary question is binned on that option's own
    probability against how often it was the label, so a confident `absent` lands in a low bin.
    Degraded and failed results have no probability and are counted by the report's statuses.
    """
    points = []
    for outcome in outcomes:
        distribution = outcome.result.distribution
        if distribution is None:
            continue
        probabilities = dict(distribution.probabilities)
        if positive is None:
            points.append((probabilities[distribution.top], distribution.top == outcome.case.label))
        else:
            points.append((probabilities[positive], outcome.case.label == positive))
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(BINS)]
    for probability, hit in points:
        buckets[min(int(probability * BINS), BINS - 1)].append((probability, hit))
    bins = []
    for k, bucket in enumerate(buckets):
        if not bucket:
            continue
        hits = sum(hit for _, hit in bucket)
        low, high = _wilson(hits, len(bucket))
        bins.append(
            ReliabilityBin(
                k / BINS,
                (k + 1) / BINS,
                len(bucket),
                math.fsum(p for p, _ in bucket) / len(bucket),
                hits / len(bucket),
                low,
                high,
            )
        )
    ece = (
        math.fsum(b.count * abs(b.mean_probability - b.observed_rate) for b in bins) / len(points)
        if points
        else None
    )
    return Calibration("present" if positive else "top_label", len(points), ece, tuple(bins))


def sweep(
    outcomes: Sequence[CaseOutcome], grid: Sequence[float] = DEFAULT_GRID
) -> tuple[SweepPoint, ...]:
    """Coverage and accepted error at each threshold, to choose one on the calibration split."""
    points = []
    for threshold in grid:
        kept = [o for o in outcomes if accepted_choice(o.result, threshold)]
        points.append(
            SweepPoint(
                threshold,
                len(kept),
                len(kept) / len(outcomes),
                sum(not o.correct for o in kept) / len(kept) if kept else None,
            )
        )
    return tuple(points)


def _wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = hits / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)
