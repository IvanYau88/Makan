"""Labelled eval cases, one JSONL set per decision point.

Each line is `{"id", "text", "label", "split", "tags"}`, where `label` is an option id of the
decision named by the file. The annotation rules:

- The label is what the text says on its own, never what a backend answered.
- When the text spans two options, or says nothing about the attribute, the label is the explicit
  "not stated" option for a soft attribute. For the retrieval gate it is `personalize`, the safe
  side, because a skipped lookup is the mistake that costs something.
- A stated budget ceiling is still labelled with its soft band. The ceiling itself is a hard
  constraint and stays in plain code.
- `near_tie` marks challenge cases where two options are close. They test the margin and are not
  a sample of real traffic, so report them apart from the rest.
- `split` is `dev` for writing prompts, `calibration` for tuning thresholds, and `heldout` for the
  final comparison. Freeze thresholds before looking at `heldout`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

from makan.decisions import DECISIONS

SPLITS = ("dev", "calibration", "heldout")


@dataclass(frozen=True)
class EvalCase:
    id: str
    decision: str
    text: str
    label: str
    split: str
    tags: tuple[str, ...] = ()


def load_cases(decision: str) -> tuple[EvalCase, ...]:
    """The cases for one decision point. Raises ValueError for a set that breaks its own rules."""
    if decision not in DECISIONS:
        raise ValueError(f"unknown decision {decision!r}")
    path = resources.files("makan.evals").joinpath("sets", f"{decision}.jsonl")
    options = {o.id for o in DECISIONS[decision].options}
    cases = []
    for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        case = EvalCase(
            raw["id"], decision, raw["text"], raw["label"], raw["split"], tuple(raw["tags"])
        )
        if case.label not in options:
            raise ValueError(f"{decision} line {number}: label {case.label!r} is not an option")
        if case.split not in SPLITS:
            raise ValueError(f"{decision} line {number}: unknown split {case.split!r}")
        cases.append(case)
    if len({c.id for c in cases}) != len(cases):
        raise ValueError(f"{decision}: case ids must be unique")
    return tuple(cases)
