"""Order-permutation tests: does an answer change when only the order or the letters change?

A scorer that favors the first option, or the letter A, answers differently for the same question
depending on how its options are laid out. That is bias, not meaning, and it would move a case
across the acceptance threshold for no reason. This module re-asks each case under deterministic
rearrangements, maps every result back to the stable semantic option ids, and counts how often the
answer moves.

An `Arrangement` has two independent parts. `order` is the presentation order of the options.
`letters` is the label shown beside each presented option. The real logprob scorer always labels
by position, so for it the two move together; a scorer that can take explicit labels offers
`score_lettered`, and only then are letter-only rearrangements exercised and reported.

Small questions (up to four options) get every order and every letter assignment. A larger one
gets the identity, the reversal, every rotation, letter reversals and rotations, and a seeded
sample of random pairs. The same inputs always give the same arrangements.
"""

from __future__ import annotations

import itertools
import math
import random
import string
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from makan.decisions import CONTRACT_DECISIONS, Decision
from makan.evals.cases import EvalCase
from makan.evals.contract_cases import contract_eval_cases
from makan.evals.threshold import ROUTER_MIN_PROBABILITY, accepted_choice, check_threshold
from makan.providers.scoring import Question, Scorer, ScoreResult

EXHAUSTIVE_UP_TO = 4  # options; beyond this the permutations are sampled
DEFAULT_SAMPLE = 24
DEFAULT_SEED = 20260610
_TIE = 1e-9  # probabilities this close are a tie for the top
LETTERS = string.ascii_uppercase


@dataclass(frozen=True)
class Arrangement:
    order: tuple[int, ...]  # presented position -> index of the original option
    letters: tuple[str, ...]  # presented position -> the label shown with it


class LetteredScorer(Scorer, Protocol):
    """What a scorer offers to have its letter assignment exercised, beyond `Scorer.score`."""

    def score_lettered(self, question: Question, letters: Sequence[str]) -> ScoreResult:
        """Answer with `letters[i]` as the label of `question.options[i]`."""
        ...


def arrangements(
    n: int, *, sample: int = DEFAULT_SAMPLE, seed: int = DEFAULT_SEED
) -> tuple[Arrangement, ...]:
    """The deterministic arrangements for an `n`-option question. The identity is always first."""
    if n < 2:
        raise ValueError("a question needs at least two options to rearrange")
    standard = tuple(LETTERS[:n])
    identity = tuple(range(n))
    if n <= EXHAUSTIVE_UP_TO:
        orders = list(itertools.permutations(identity))
        labellings = list(itertools.permutations(standard))
        pairs = [(o, tuple(lt)) for o in orders for lt in labellings]
    else:
        reverse = tuple(reversed(identity))
        rotations = [identity[k:] + identity[:k] for k in range(1, n)]
        pairs = [(identity, standard), (reverse, standard)]
        pairs += [(r, standard) for r in rotations]
        pairs += [(identity, tuple(standard[i] for i in r)) for r in (reverse, *rotations)]
        rng = random.Random(seed)
        for _ in range(sample):
            order, letters = list(identity), list(standard)
            rng.shuffle(order)
            rng.shuffle(letters)
            pairs.append((tuple(order), tuple(letters)))
    unique = dict.fromkeys(pairs)
    ordered = [(identity, standard), *(p for p in unique if p != (identity, standard))]
    return tuple(Arrangement(o, lt) for o, lt in ordered)


def arranged(question: Question, arrangement: Arrangement) -> Question:
    """The same question with its options presented in the arrangement's order."""
    options = tuple(question.options[i] for i in arrangement.order)
    return Question(
        question.decision, question.version, question.instructions, question.state, options
    )


def ask(scorer: Scorer, question: Question, arrangement: Arrangement) -> ScoreResult:
    """Ask the arranged question, passing the letters only to a scorer that can take them."""
    presented = arranged(question, arrangement)
    lettered = getattr(scorer, "score_lettered", None)
    if callable(lettered):
        result: ScoreResult = lettered(presented, arrangement.letters)
        return result
    return scorer.score(presented)


def _lettered(scorer: Scorer) -> bool:
    return callable(getattr(scorer, "score_lettered", None))


@dataclass(frozen=True)
class CasePermutation:
    case_id: str
    measured: int  # arrangements compared with the reference
    unavailable: int  # arrangements whose result had no choice at all
    argmax_flips: int
    threshold_flips: int
    max_probability_change: float
    max_divergence: float


@dataclass(frozen=True)
class PermutationReport:
    decision: str
    version: str
    backend: str
    model: str
    threshold: float
    letters_exercised: bool
    cases: int
    cases_unmeasured: int  # the reference arrangement itself had no result
    arrangements_per_case: int
    evaluations: int
    unavailable: int
    argmax_flips: int
    argmax_flip_rate: float | None
    cases_with_argmax_flip: int
    threshold_flips: int  # accepted or not, or which option was accepted, changed
    threshold_flip_rate: float | None
    cases_with_threshold_flip: int
    max_probability_change: float
    max_divergence: float  # Jensen-Shannon, in nats
    worst_cases: tuple[str, ...]  # the most order-sensitive case ids, worst first


def run_permutation_eval(
    scorer: Scorer,
    decision: str,
    cases: Iterable[EvalCase] | None = None,
    *,
    split: str | None = None,
    threshold: float = ROUTER_MIN_PROBABILITY,
    sample: int = DEFAULT_SAMPLE,
    seed: int = DEFAULT_SEED,
) -> PermutationReport:
    """Ask every case under every arrangement and measure how far the answers move."""
    check_threshold(threshold)
    if decision not in CONTRACT_DECISIONS:
        raise ValueError(f"unknown contract decision {decision!r}")
    spec: Decision = CONTRACT_DECISIONS[decision]
    chosen = [
        c
        for c in (contract_eval_cases(decision) if cases is None else cases)
        if split in (None, c.split)
    ]
    if not chosen:
        raise ValueError(f"no cases for {decision} in split {split!r}")
    plan = arrangements(len(spec.options), sample=sample, seed=seed)
    if not _lettered(scorer):  # the letters are invisible, so only the order can matter
        plan = tuple(a for a in plan if a.letters == tuple(LETTERS[: len(a.letters)]))
    per_case = [
        _measure(scorer, spec.question(case.text), case.id, plan, threshold) for case in chosen
    ]
    measured = [c for c in per_case if c is not None]
    evaluations = sum(c.measured for c in measured)
    argmax = sum(c.argmax_flips for c in measured)
    crossing = sum(c.threshold_flips for c in measured)
    worst = sorted(measured, key=lambda c: (-c.argmax_flips, -c.threshold_flips, c.case_id))
    return PermutationReport(
        decision=spec.name,
        version=spec.version,
        backend=scorer.name,
        model=scorer.model,
        threshold=threshold,
        letters_exercised=_lettered(scorer),
        cases=len(chosen),
        cases_unmeasured=len(chosen) - len(measured),
        arrangements_per_case=len(plan),
        evaluations=evaluations,
        unavailable=sum(c.unavailable for c in measured),
        argmax_flips=argmax,
        argmax_flip_rate=argmax / evaluations if evaluations else None,
        cases_with_argmax_flip=sum(c.argmax_flips > 0 for c in measured),
        threshold_flips=crossing,
        threshold_flip_rate=crossing / evaluations if evaluations else None,
        cases_with_threshold_flip=sum(c.threshold_flips > 0 for c in measured),
        max_probability_change=max((c.max_probability_change for c in measured), default=0.0),
        max_divergence=max((c.max_divergence for c in measured), default=0.0),
        worst_cases=tuple(c.case_id for c in worst if c.argmax_flips or c.threshold_flips)[:5],
    )


def _measure(
    scorer: Scorer,
    question: Question,
    case_id: str,
    plan: Sequence[Arrangement],
    threshold: float,
) -> CasePermutation | None:
    reference = ask(scorer, question, plan[0])
    reference_top = _top_ids(reference)
    if reference_top is None:
        return None
    reference_accept = accepted_choice(reference, threshold)
    reference_p = _probabilities(reference)
    measured = unavailable = argmax = crossing = 0
    change = divergence = 0.0
    for arrangement in plan[1:]:
        result = ask(scorer, question, arrangement)
        top = _top_ids(result)
        if top is None:
            unavailable += 1
            continue
        measured += 1
        argmax += top != reference_top
        crossing += accepted_choice(result, threshold) != reference_accept
        p = _probabilities(result)
        if p is not None and reference_p is not None:
            change = max(change, max(abs(p[i] - reference_p[i]) for i in reference_p))
            divergence = max(divergence, _jensen_shannon(reference_p, p))
    return CasePermutation(case_id, measured, unavailable, argmax, crossing, change, divergence)


def _top_ids(result: ScoreResult) -> frozenset[str] | None:
    """The semantic ids at the top. A tie is every tied id, so tie-breaking by order is no flip."""
    p = _probabilities(result)
    if p is not None:
        best = max(p.values())
        return frozenset(i for i, v in p.items() if v >= best - _TIE)
    return frozenset({result.choice}) if result.choice else None


def _probabilities(result: ScoreResult) -> dict[str, float] | None:
    """Probability by semantic option id, which does not depend on the presented order."""
    return dict(result.distribution.probabilities) if result.distribution else None


def _jensen_shannon(p: dict[str, float], q: dict[str, float]) -> float:
    def kl(a: dict[str, float], b: dict[str, float]) -> float:
        return math.fsum(a[i] * math.log(a[i] / b[i]) for i in a if a[i] > 0)

    m = {i: (p[i] + q[i]) / 2 for i in p}
    return (kl(p, m) + kl(q, m)) / 2
