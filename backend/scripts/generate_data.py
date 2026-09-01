from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.datagen import generate

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data"


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic ledger / bank / ground truth")
    ap.add_argument("--cases", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20260822)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--stress", action="store_true",
                    help="use the adversarial mix instead of the standard one")
    args = ap.parse_args()

    truth = generate(args.out, n_cases=args.cases, seed=args.seed, stress=args.stress)

    print("wrote -> " + str(args.out))
    print("  ledger.csv          %4d rows" % truth["ledger_rows"])
    print("  bank_statement.csv  %4d rows" % truth["bank_rows"])
    print("  ground_truth.json   %4d cases (seed %d, %s profile)"
          % (truth["case_count"], truth["seed"], truth["profile"]))
    print()
    print("  %-18s %5s" % ("category", "cases"))
    print("  " + "-" * 24)
    for cat, n in sorted(truth["cases_by_category"].items(), key=lambda kv: -kv[1]):
        print("  %-18s %5d" % (cat, n))


if __name__ == "__main__":
    main()
