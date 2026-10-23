"""Offline Clef control arithmetic and proposed validation rules, with no HTTP mocks or calls."""

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from makan.decisions import CONTRACT_DECISIONS, DECISIONS, PROFILE_FIT_SCORE, TASTE_FIT
from makan.evals.fit import (
    FIT_MIN_PROBABILITY,
    SUM_TOLERANCE,
    WIRE_SCORE_TOLERANCE,
    accepted_fit_choice,
    baseline_request,
    consistency_group,
    display_index,
    load_resource,
    ordinal_expectation,
    parse_json,
    probabilities,
    replay_isolated,
    rounded_level,
    validate_response_projection,
)
from makan.evals.threshold import accepted_choice
from makan.providers.scoring import Distribution, ScoreResult, Status

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/clef/isolated-vectors.json"


def vector(*values: float) -> dict[str, float]:
    return dict(zip(map(str, range(5)), values, strict=True))


def synthetic_projection() -> dict[str, Any]:
    """Test-authored projection only; never presented as a captured dual-question response."""
    contract = load_resource("contract.json")
    return {
        "success": True,
        "result": {
            "model": "clef-flash",
            "answers": {
                "taste_fit": {
                    "type": "choice",
                    "probabilities": {"strong": 0.8, "partial": 0.1, "poor": 0.05, "unknown": 0.05},
                },
                "fit_score": {
                    "type": "score",
                    "probabilities": vector(0, 0, 0, 0, 1),
                    "legend": {
                        str(i): text
                        for i, text in enumerate(contract["questions"]["fit_score"]["criteria"])
                    },
                    "score": 4.0,
                },
            },
        },
    }


def test_exact_archive_bytes_and_content_hashed_manifest() -> None:
    manifest = load_resource("manifest.json")
    digest = manifest.pop("content_sha256")
    canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == digest
    for path, expected in manifest["files"].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected, path
    assert manifest["files"]["src/makan/evals/sets/clef/cases.json"] == (
        "49e24d6ac40045628f4fdb2bb7574ea620ecfb50d4927da59fa27c33273f6900"
    )
    assert manifest["files"]["src/makan/evals/sets/clef/labels.json"] == (
        "773e600f286b7445966ed9cfba43d1963f69b91fdedd1e96cac0b8cb105d1f75"
    )


def test_family_groups_preserve_pairs_without_inventing_qualification_splits() -> None:
    cases = load_resource("cases.json")
    families = load_resource("families.json")
    assert len(cases) == 40
    assert len(families["families"]) == 26
    assert "not dev/calibration/heldout" in families["split_policy"]
    assert sorted(c["id"] for c in cases) == sorted(
        case_id for ids in families["families"].values() for case_id in ids
    )
    for case in cases:
        assert case["id"] in families["families"][case["family"]]
    for i in range(1, 8):
        assert families["families"][f"pair-A{i:02}"] == [f"F{i:02}", f"M{i:02}"]
        assert families["families"][f"pair-B{i:02}"] == [f"D{i:02}", f"T{i:02}"]


def test_replay_reproduces_report_and_exact_original_request_hashes() -> None:
    cases = load_resource("cases.json")
    labels = load_resource("labels.json")
    fixture = parse_json(FIXTURE.read_text())
    assert "Model-written" in labels["annotation"]
    assert fixture["provenance"]["kind"] == "reconstructed_from_report_table"
    assert fixture["provenance"]["raw_response_available"] is False
    report = replay_isolated(cases, labels, fixture)
    assert (report.cases, report.exact, report.within_one) == (40, 28, 40)
    assert f"{report.normalized_mae:.6f}" == "0.102837"
    rows = {r["id"]: r for r in fixture["cases"]}
    baseline = load_resource("baseline.json")
    for case in cases:
        body = baseline_request(case, baseline)
        assert hashlib.sha256(body).hexdigest() == rows[case["id"]]["request_sha256"]
        request = parse_json(body.decode())
        assert list(parse_json(request["state"])) == ["diner", "place"]
        assert list(request["questions"]) == ["fit_score"]
    f02 = ordinal_expectation(rows["F02"]["probabilities"])
    assert 25 * f02 == pytest.approx(94.0925)
    assert display_index(f02) == 94
    assert "0.5 miles" in next(c["diner"] for c in cases if c["id"] == "D03")


@pytest.mark.parametrize("damage", ["missing", "extra", "duplicate", "label"])
def test_replay_never_silently_drops_or_mislabels_cases(damage: str) -> None:
    fixture = parse_json(FIXTURE.read_text())
    if damage == "missing":
        fixture["cases"].pop()
    elif damage == "extra":
        row = deepcopy(fixture["cases"][0])
        row["id"] = "extra"
        fixture["cases"].append(row)
    elif damage == "duplicate":
        fixture["cases"].append(fixture["cases"][0])
    else:
        fixture["cases"][0]["label"] = 5
    with pytest.raises(ValueError):
        replay_isolated(load_resource("cases.json"), load_resource("labels.json"), fixture)


def test_contract_reuses_exact_v1_taste_and_keeps_ordinal_out_of_choice_registries() -> None:
    contract = load_resource("contract.json")
    assert contract["status"] == "hypothesis_awaiting_qualification"
    assert contract["runtime_enabled"] is False
    assert contract["case_cardinality"] == {"profiles": 1, "candidates": 1}
    taste = contract["questions"]["taste_fit"]
    assert taste["version"] == TASTE_FIT.version == "1"
    assert taste["instructions"] == TASTE_FIT.instructions
    assert taste["criteria"] == {o.id: o.description for o in TASTE_FIT.options}
    score = contract["questions"]["fit_score"]
    assert score["instructions"] == PROFILE_FIT_SCORE.instructions
    assert score["criteria"] == list(PROFILE_FIT_SCORE.criteria)
    assert score["version"] == PROFILE_FIT_SCORE.version
    assert set(DECISIONS) == {"retrieval_gate", "budget_band", "meal_period"}
    assert len(CONTRACT_DECISIONS) == 8
    assert "fit_score" not in CONTRACT_DECISIONS
    assert contract["validation"]["probability_sum_tolerance"] == SUM_TOLERANCE
    assert contract["validation"]["wire_score_tolerance"] == WIRE_SCORE_TOLERANCE
    assert contract["policies"]["acceptance"]["threshold"] == FIT_MIN_PROBABILITY == 0.70
    assert contract["policies"]["unknown"]["accepted_unknown"] == "null/insufficient_evidence"
    assert contract["policies"]["unknown"]["infrastructure_failure"].startswith("null/unavailable")


def test_frozen_schemas_are_explicit_about_their_scope_and_exact_keys() -> None:
    response = load_resource("response.schema.json")
    assert "not a recorded native response" in response["$comment"]
    result = response["properties"]["result"]
    answers = result["properties"]["answers"]
    assert answers["required"] == ["taste_fit", "fit_score"]
    assert answers["additionalProperties"] is False
    score = answers["properties"]["fit_score"]
    assert score["required"] == ["type", "probabilities", "legend", "score"]
    assert score["properties"]["probabilities"]["required"] == list(map(str, range(5)))
    state = load_resource("state.schema.json")
    assert state["required"] == ["profile", "candidate"]
    assert state["additionalProperties"] is False
    assert state["properties"]["profile"]["required"] == ["likes", "dislikes", "source"]
    assert "hard_constraints" not in state["properties"]


@pytest.mark.parametrize("level", range(5))
def test_pure_levels_map_to_plate_endpoints(level: int) -> None:
    expectation = ordinal_expectation(vector(*(float(i == level) for i in range(5))))
    assert expectation == level
    assert display_index(expectation) == 25 * level
    assert rounded_level(expectation) == level + 1
    assert consistency_group(expectation) == ("poor", "poor", "partial", "strong", "strong")[level]


def test_uniform_half_up_and_consistency_boundaries() -> None:
    assert display_index(ordinal_expectation(vector(0.2, 0.2, 0.2, 0.2, 0.2))) == 50
    assert display_index(0.02) == 1  # half up, not Python's ties-to-even round
    assert display_index(0.019999) == 0
    assert consistency_group(1.49999) == "poor"
    assert consistency_group(1.5) == "partial"
    assert consistency_group(2.49999) == "partial"
    assert consistency_group(2.5) == "strong"


@pytest.mark.parametrize("bad", [True, False, -1, 4.001, float("nan"), float("inf"), "2", 10**400])
def test_rounding_rejects_invalid_expectations(bad: Any) -> None:
    for fn in (display_index, rounded_level, consistency_group):
        with pytest.raises(ValueError):
            fn(bad)


@pytest.mark.parametrize("bad", [True, False, -0.1, 1.1, float("nan"), float("inf"), "0.2", None])
def test_probability_values_must_be_finite_numbers_not_booleans(bad: Any) -> None:
    value: dict[str, Any] = vector(0.2, 0.2, 0.2, 0.2, 0.2)
    value["0"] = bad
    with pytest.raises(ValueError):
        ordinal_expectation(value)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {"0": 1},
        {str(i): 0.2 for i in range(6)},
        vector(0, 0, 0, 0, 0),
        vector(0.2, 0.2, 0.2, 0.2, 0.202),
    ],
)
def test_probabilities_reject_missing_extra_keys_or_bad_sums(bad: Any) -> None:
    with pytest.raises(ValueError):
        ordinal_expectation(bad)


def test_small_rounding_normalizes_only_after_sum_validation() -> None:
    values = probabilities(vector(0.2, 0.2, 0.2, 0.2, 0.2005), tuple(map(str, range(5))))
    assert sum(values) == pytest.approx(1)
    assert values[-1] == pytest.approx(0.2005 / 1.0005)
    response = synthetic_projection()
    response["result"]["answers"]["fit_score"]["score"] = 3.9995
    assert validate_response_projection(response, load_resource("contract.json")).expectation == 4
    response["result"]["answers"]["fit_score"]["score"] = 3.9993
    with pytest.raises(ValueError, match="wire score"):
        validate_response_projection(response, load_resource("contract.json"))


@pytest.mark.parametrize(
    ("path", "bad"),
    [
        (("success",), False),
        (("success",), 1),
        (("result", "model"), "different-model"),
        (("result", "answers", "taste_fit", "type"), "score"),
        (("result", "answers", "fit_score", "type"), "choice"),
        (("result", "answers", "fit_score", "score"), True),
        (("result", "answers", "fit_score", "score"), float("nan")),
        (("result", "answers", "fit_score", "score"), 2),
        (("result", "answers", "fit_score", "legend"), {"0": "wrong"}),
        (("result", "answers", "taste_fit", "probabilities"), {"strong": 1}),
        (("result", "answers", "fit_score", "probabilities"), {"0": 1}),
    ],
)
def test_response_projection_rejects_wrong_model_types_score_and_legend(
    path: tuple[str, ...], bad: Any
) -> None:
    response = synthetic_projection()
    target = response
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad
    with pytest.raises(ValueError):
        validate_response_projection(response, load_resource("contract.json"))


@pytest.mark.parametrize("damage", ["missing", "extra", "refusal", "legend_order"])
def test_response_projection_rejects_incomplete_answers_and_refusals(damage: str) -> None:
    response = synthetic_projection()
    answers = response["result"]["answers"]
    if damage == "missing":
        del answers["taste_fit"]
    elif damage == "extra":
        answers["extra"] = answers["taste_fit"]
    elif damage == "refusal":
        answers["fit_score"] = {"refusal": "no"}
    else:
        legend = answers["fit_score"]["legend"]
        legend["0"], legend["4"] = legend["4"], legend["0"]
    with pytest.raises(ValueError):
        validate_response_projection(response, load_resource("contract.json"))


def test_valid_projection_keeps_choice_and_ordinal_evidence_separate() -> None:
    result = validate_response_projection(synthetic_projection(), load_resource("contract.json"))
    assert len(result.taste_probabilities) == 4
    assert len(result.ordinal_probabilities) == 5
    assert result.expectation == 4
    assert result.taste_probabilities[-1] == 0.05  # unknown was not fabricated as zero


@pytest.mark.parametrize(
    ("values", "threshold", "expected"),
    [
        ((0.70, 0.1, 0.1, 0.1), 0.70, "strong"),
        ((0.69, 0.11, 0.1, 0.1), 0.70, None),
        ((0.4, 0.4, 0.1, 0.1), 0.30, None),
        ((0.1, 0.1, 0.1, 0.7), 0.70, "unknown"),
    ],
)
def test_fit_threshold_matches_existing_unique_top_policy(
    values: tuple[float, ...], threshold: float, expected: str | None
) -> None:
    ids = tuple(o.id for o in TASTE_FIT.options)
    probs = dict(zip(ids, values, strict=True))
    distribution = Distribution.of(probs, ids)
    result = ScoreResult(
        Status.OK, "offline", "", choice=distribution.top, distribution=distribution
    )
    assert accepted_fit_choice(probs, threshold) == accepted_choice(result, threshold) == expected


@pytest.mark.parametrize("bad", [0, -1, 1.1, True, float("nan")])
def test_acceptance_rejects_invalid_thresholds(bad: Any) -> None:
    with pytest.raises(ValueError):
        accepted_fit_choice({"strong": 1, "partial": 0, "poor": 0, "unknown": 0}, bad)


@pytest.mark.parametrize("text", ["{", '{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_malformed_json_is_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        parse_json(text)


def test_cli_runs_without_credentials_or_network() -> None:
    # The existing eval package imports httpx; the replay itself must never open a connection.
    code = """
import runpy, sys
def audit(event, args):
    if event in ("socket.connect", "socket.getaddrinfo"):
        raise AssertionError("offline eval attempted network")
sys.addaudithook(audit)
sys.argv = ["makan.evals.fit"]
runpy.run_module("makan.evals.fit", run_name="__main__")
"""
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env={}, text=True, capture_output=True, check=True
    )
    assert '"exact": 28' in completed.stdout
    assert "unqualified and off" in completed.stdout


def test_pure_math_and_validators_do_not_need_httpx() -> None:
    # Load only this module so the existing makan.evals package exports are not imported.
    code = """
import runpy, sys
sys.modules["httpx"] = None
sys.modules["httpx2"] = None
module = runpy.run_path("src/makan/evals/fit.py")
assert module["display_index"](module["ordinal_expectation"]({str(i): .2 for i in range(5)})) == 50
probs = {"strong": .7, "partial": .1, "poor": .1, "unknown": .1}
assert module["accepted_fit_choice"](probs) == "strong"
"""
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={}, check=True)
