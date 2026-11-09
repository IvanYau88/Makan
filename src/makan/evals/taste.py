"""Offline check of the own taste model on invented places: `python -m makan.evals.taste`.

It reruns the taste-learning investigation's check with the product's own code: the same
features, ridge fit and policy as `makan.taste_model`, on the 120 invented places one rater
labelled (`sets/taste/places.json`), held out with grouped five-fold cross-validation so twins and
repeats never sit across a split. The places keep their dish names, but the model, like a search,
does not use them. It reports the model against always guessing the rater's
average, and a learning curve over how many ratings the model was given. No network, key or
hosted model is involved.

Labels are levels from 1 to 5 and the product rates from 0 to 10, so each label is rated at twice
its level and each prediction is read back as half, kept within 1 to 5. Errors are in levels.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from importlib import resources
from typing import Any

from makan.taste_model import POLICY_VERSION, fit
from makan.visits.content import PlaceFacts

LEVELS = (1, 5)
FOLDS = 5
CURVE_SIZES = (5, 10, 20, 30)
CURVE_REPEATS = 8


@dataclass(frozen=True)
class LabelledPlace:
    id: str
    cluster: str
    facts: PlaceFacts
    level: int


@dataclass(frozen=True)
class Scores:
    mae: float
    exact: float
    within_one: float


@dataclass(frozen=True)
class Report:
    policy_version: str
    places: int
    seed: int
    own: Scores
    average: Scores
    curve: dict[str, dict[str, float]]


def load_places() -> list[LabelledPlace]:
    raw = json.loads(
        resources.files("makan.evals").joinpath("sets", "taste", "places.json").read_text()
    )
    return [
        LabelledPlace(
            p["id"],
            p["cluster"],
            PlaceFacts(
                tuple(p["cuisines"]), tuple(p["diets"]), tuple(p["dishes"]), p["hours_known"]
            ),
            int(p["level"]),
        )
        for p in raw["places"]
    ]


def make_folds(places: Sequence[LabelledPlace], k: int, seed: int) -> dict[str, int]:
    """Grouped folds: every cluster lands whole in one fold, biggest clusters first, each to the
    least loaded fold. The same split as the investigation for the same seed."""
    groups: dict[str, list[str]] = {}
    for p in places:
        groups.setdefault(p.cluster, []).append(p.id)
    keys = sorted(groups)
    random.Random(seed).shuffle(keys)
    keys.sort(key=lambda g: -len(groups[g]))
    load = [0] * k
    fold_of: dict[str, int] = {}
    for g in keys:
        chosen = min(range(k), key=lambda i: (load[i], random.Random(f"{seed}{g}{i}").random()))
        load[chosen] += len(groups[g])
        for place_id in groups[g]:
            fold_of[place_id] = chosen
    return fold_of


def predict_levels(train: Sequence[LabelledPlace], test: Sequence[LabelledPlace]) -> list[float]:
    """Fit on `train` at the product's scale and predict each of `test` as a level."""
    model = fit([(p.facts, 2.0 * p.level) for p in train])
    return [_clip(model.predict(p.facts) / 2) for p in test]


def scores(pairs: Sequence[tuple[float, int]]) -> Scores:
    """Mean absolute error in levels, and how often the rounded level is exact or within one."""
    rounded = [(_clip(math.floor(pred + 0.5)), level) for pred, level in pairs]
    return Scores(
        mae=math.fsum(abs(pred - level) for pred, level in pairs) / len(pairs),
        exact=sum(r == level for r, level in rounded) / len(pairs),
        within_one=sum(abs(r - level) <= 1 for r, level in rounded) / len(pairs),
    )


def cross_validate(
    places: Sequence[LabelledPlace], seed: int = 0, size: int | None = None, repeat: int = 0
) -> tuple[list[tuple[float, int]], list[tuple[float, int]]]:
    """Held-out (prediction, level) pairs for the model and for the training average.

    With `size`, each fold trains on a random `size` of its training places instead of all.
    """
    fold_of = make_folds(places, FOLDS, seed)
    own: list[tuple[float, int]] = []
    average: list[tuple[float, int]] = []
    for f in range(FOLDS):
        train = [p for p in places if fold_of[p.id] != f]
        test = [p for p in places if fold_of[p.id] == f]
        if size is not None:
            train = sorted(train, key=lambda p: random.Random(f"{repeat}-{f}-{p.id}").random())
            train = train[:size]
        mean = math.fsum(p.level for p in train) / len(train)
        for pred, p in zip(predict_levels(train, test), test, strict=True):
            own.append((pred, p.level))
            average.append((mean, p.level))
    return own, average


def run(seed: int = 0) -> Report:
    places = load_places()
    own, average = cross_validate(places, seed)
    curve: dict[str, dict[str, float]] = {}
    for size in CURVE_SIZES:
        own_errors: list[float] = []
        average_errors: list[float] = []
        for repeat in range(CURVE_REPEATS):
            o, a = cross_validate(places, seed, size, repeat)
            own_errors += [abs(pred - level) for pred, level in o]
            average_errors += [abs(pred - level) for pred, level in a]
        curve[str(size)] = {
            "own_mae": _mean(own_errors),
            "average_mae": _mean(average_errors),
        }
    return Report(POLICY_VERSION, len(places), seed, scores(own), scores(average), curve)


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def _clip(level: float) -> float:
    return min(float(LEVELS[1]), max(float(LEVELS[0]), level))


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, Mapping):
        return {k: _rounded(v) for k, v in value.items()}
    return value


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m makan.evals.taste", description=__doc__)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    print(json.dumps(_rounded(asdict(run(args.seed))), indent=2))


if __name__ == "__main__":
    main()
