"""Run the taste-fit and router contract evals offline: `python -m makan.evals.contract`.

This is separate from `python -m makan.evals`, which runs the three decision sets by margin. Here
the router is judged by top probability, each question is calibrated on its own, and the order
permutation check runs too. Only the deterministic fake is offered, so the output is contract
evidence that the plumbing works and says nothing about any model.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from makan.decisions import CONTRACT_DECISIONS
from makan.evals.cases import SPLITS
from makan.evals.permutation import run_permutation_eval
from makan.evals.threshold import ROUTER_MIN_PROBABILITY, run_router_eval, run_threshold_eval
from makan.providers.scoring import FakeScorer


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m makan.evals.contract", description=__doc__)
    parser.add_argument("--backend", choices=("fake",), default="fake")
    parser.add_argument("--split", choices=SPLITS)
    parser.add_argument("--threshold", type=float, default=ROUTER_MIN_PROBABILITY)
    parser.add_argument("--permutations", action="store_true", help="also run the order checks")
    args = parser.parse_args(argv)
    scorer = FakeScorer()
    output: dict[str, object] = {
        "taste_fit": asdict(
            run_threshold_eval(scorer, "taste_fit", split=args.split, threshold=args.threshold)
        ),
        "router": asdict(run_router_eval(scorer, split=args.split, threshold=args.threshold)),
    }
    if args.permutations:
        output["permutations"] = [
            asdict(run_permutation_eval(scorer, name, split=args.split, threshold=args.threshold))
            for name in CONTRACT_DECISIONS
        ]
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
