"""Offline Clef control replay and validation of an unqualified fit-contract hypothesis.

No provider, transport, configuration or product code imports this module. The response validator
accepts the bounded projection in response.schema.json, not arbitrary native provider payloads.
Fixtures reconstructed from a report certify arithmetic only, not a new prompt or native parser.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path
from typing import Any

SUM_TOLERANCE = 1e-3
WIRE_SCORE_TOLERANCE = 0.0006
FIT_MIN_PROBABILITY = (
    0.70  # Hypothesis awaiting qualification, independent of router configuration.
)
TASTE_IDS = ("strong", "partial", "poor", "unknown")
LEVEL_IDS = ("0", "1", "2", "3", "4")


def load_resource(name: str) -> Any:
    """Load a frozen eval resource; no credentials or transport are involved."""
    return parse_json(resources.files("makan.evals").joinpath("sets", "clef", name).read_text())


def parse_json(text: str) -> Any:
    """Reject malformed JSON, duplicate keys and JSON's nonstandard NaN/Infinity extensions."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value: str) -> Any:
        raise ValueError("non-finite JSON number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def _object(value: Any, keys: Sequence[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("object must have exactly the expected keys")
    return value


def _number(value: Any, low: float, high: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not low <= value <= high
        or not math.isfinite(value)
    ):
        raise ValueError("expected a finite number in range, excluding booleans")
    return float(value)


def probabilities(value: Any, ids: Sequence[str]) -> tuple[float, ...]:
    """Check complete keys, finite [0,1] values and sum before small rounding normalization."""
    raw = _object(value, ids)
    values = tuple(_number(raw[key], 0, 1) for key in ids)
    total = math.fsum(values)
    if total == 0 or abs(total - 1) > SUM_TOLERANCE:
        raise ValueError("probabilities must sum to one within 1e-3")
    return tuple(p / total for p in values)


def ordinal_expectation(value: Any) -> float:
    return math.fsum(i * p for i, p in enumerate(probabilities(value, LEVEL_IDS)))


def display_index(expectation: float) -> int:
    """Proposed ordinal policy index, not a probability; round once, with half-up endpoints."""
    expectation = _number(expectation, 0, 4)
    return min(100, max(0, math.floor(25 * expectation + 0.5)))


def rounded_level(expectation: float) -> int:
    expectation = _number(expectation, 0, 4)
    return min(5, math.floor(1 + expectation + 0.5))


def consistency_group(expectation: float) -> str:
    """Proposed grouping only; never used to fabricate a taste_fit posterior."""
    level = rounded_level(expectation)
    return "poor" if level <= 2 else "partial" if level == 3 else "strong"


def accepted_fit_choice(value: Any, threshold: float = FIT_MIN_PROBABILITY) -> str | None:
    """Offline hypothesis: same unique-top and threshold rule as threshold.accepted_choice."""
    threshold = _number(threshold, 0, 1)
    if threshold == 0:
        raise ValueError("threshold must be above zero")
    values = probabilities(value, TASTE_IDS)
    order = sorted(range(len(values)), key=lambda i: -values[i])
    first, second = order[:2]
    if values[first] - values[second] <= 1e-9 or values[first] + 1e-9 < threshold:
        return None
    return TASTE_IDS[first]


@dataclass(frozen=True)
class ValidatedProjection:
    """Validated offline evidence, not a production ScoreResult or public FitAssessment."""

    taste_probabilities: tuple[float, ...]
    ordinal_probabilities: tuple[float, ...]
    expectation: float


def validate_response_projection(value: Any, contract: Mapping[str, Any]) -> ValidatedProjection:
    """Check the proposed bounded response schema and cross-field numeric rules, offline.

    A future native adapter must separately validate/project metadata, usage, failures and headers.
    There are deliberately no raw dual-question successes in this slice.
    """
    envelope = _object(value, ("success", "result"))
    if envelope["success"] is not True:
        raise ValueError("response must report success=true")
    result = _object(envelope["result"], ("model", "answers"))
    if result["model"] != contract["selector"]:
        raise ValueError("unexpected returned model")
    answers = _object(result["answers"], ("taste_fit", "fit_score"))
    taste = _object(answers["taste_fit"], ("type", "probabilities"))
    ordinal = _object(answers["fit_score"], ("type", "probabilities", "legend", "score"))
    if taste["type"] != "choice" or ordinal["type"] != "score":
        raise ValueError("unexpected answer type")
    taste_probs = probabilities(taste["probabilities"], TASTE_IDS)
    score_probs = probabilities(ordinal["probabilities"], LEVEL_IDS)
    expected_legend = {
        str(i): text for i, text in enumerate(contract["questions"]["fit_score"]["criteria"])
    }
    if ordinal["legend"] != expected_legend:
        raise ValueError("unexpected ordinal legend")
    expectation = math.fsum(i * p for i, p in enumerate(score_probs))
    score = _number(ordinal["score"], 0, 4)
    if abs(score - expectation) > WIRE_SCORE_TOLERANCE:
        raise ValueError("wire score disagrees with probability expectation")
    return ValidatedProjection(taste_probs, score_probs, expectation)


def baseline_request(case: Mapping[str, Any], baseline: Mapping[str, Any]) -> bytes:
    """Reconstruct exact historical isolated bytes; never send them anywhere.

    Source labels, families and variants must not enter the request. Hard-limit text remains only
    here in the historical control and is not authorized as a production soft-state builder.
    """
    state = {key: case[key] for key in baseline["state_fields"]}
    request = {
        "model": baseline["selector"],
        "state": json.dumps(state, ensure_ascii=False),
        "questions": baseline["questions"],
    }
    return json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class ReplayReport:
    cases: int
    exact: int
    within_one: int
    normalized_mae: float


def replay_isolated(
    cases: Sequence[Mapping[str, Any]], labels: Mapping[str, Any], fixture: Mapping[str, Any]
) -> ReplayReport:
    """Replay all reported cases, rejecting missing, extra, duplicate or mismatched labels."""
    ids = [case["id"] for case in cases]
    rows = fixture["cases"]
    row_ids = [row["id"] for row in rows]
    if not ids or len(set(ids)) != len(ids) or len(set(row_ids)) != len(row_ids):
        raise ValueError("case ids must be nonempty and unique")
    if set(ids) != set(row_ids):
        raise ValueError("fixture must cover every baseline case exactly")
    exact = within_one = 0
    errors = []
    for row in rows:
        label = labels["cases"][row["id"]]["level"]
        if isinstance(label, bool) or not isinstance(label, int) or not 1 <= label <= 5:
            raise ValueError("label must be a level from one through five")
        if row["label"] != label:
            raise ValueError("fixture label differs from frozen labels")
        expectation = ordinal_expectation(row["probabilities"])
        rounded = rounded_level(expectation)
        exact += rounded == label
        within_one += abs(rounded - label) <= 1
        errors.append(abs(1 + expectation - label) / 4)
    return ReplayReport(len(ids), exact, within_one, math.fsum(errors) / len(ids))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline historical Clef vector replay; no inference"
    )
    parser.add_argument(
        "--fixture", type=Path, default=Path("tests/fixtures/clef/isolated-vectors.json")
    )
    args = parser.parse_args()
    fixture = parse_json(args.fixture.read_text("utf-8"))
    report = replay_isolated(load_resource("cases.json"), load_resource("labels.json"), fixture)
    print("Historical single-question control; model-written labels; report-reconstructed vectors.")
    print(
        "Arithmetic regression only. Proposed dual-question contract remains unqualified and off."
    )
    print(json.dumps(asdict(report), indent=2))


if __name__ == "__main__":
    main()
