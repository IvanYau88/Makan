"""Run the scorer evals offline: `python -m makan.evals`.

Only the deterministic backends are offered. Comparing live backends costs money and needs a
captain-approved key and budget, so it is deliberately not wired here.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from makan.decisions import DECISIONS
from makan.evals.cases import SPLITS
from makan.evals.harness import RuleBaseline, compare
from makan.providers.scoring import FakeScorer, Scorer
from makan.signals import MIN_MARGIN


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m makan.evals", description=__doc__)
    parser.add_argument("--backend", action="append", choices=("fake", "rule"), default=None)
    parser.add_argument("--decision", action="append", choices=tuple(DECISIONS), default=None)
    parser.add_argument("--split", choices=SPLITS)
    parser.add_argument("--min-margin", type=float, default=MIN_MARGIN)
    args = parser.parse_args(argv)
    scorers: dict[str, Scorer] = {}
    for backend in args.backend or ["fake", "rule"]:
        scorers[backend] = FakeScorer() if backend == "fake" else RuleBaseline()
    reports = compare(
        scorers, args.decision or tuple(DECISIONS), split=args.split, min_margin=args.min_margin
    )
    print(json.dumps([asdict(r) for r in reports], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
