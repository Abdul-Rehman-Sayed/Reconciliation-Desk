"""Score the engine on datasets generated from seeds it was never tuned against.

The bundled numbers come from two fixed datasets we generated, so a 100% on them
proves the engine agrees with itself. This is the check that it generalises: nine
fresh draws per profile, thresholds untouched.

    python scripts/holdout.py                  # the pinned nine, both profiles
    python scripts/holdout.py --seeds 1 2 3    # any other draw

The seeds are pinned in this file rather than described in prose so the figure in
the README is one command away from being checked. Case accuracy moves by a case
or two between draws - 0.9975 is exactly 399/400 - so the number that carries the
argument is auto-resolve precision, which is what a wrong auto-match would break.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.datagen import generate  # noqa: E402
from app.dataio import read_bank, read_ledger  # noqa: E402
from app.matching import reconcile  # noqa: E402
from app.scoring import score  # noqa: E402

# Never used while tuning thresholds. Changing these changes the published
# figure, so they are part of the claim.
HOLDOUT_SEEDS = [101, 202, 303, 404, 505, 606, 707, 808, 909]


def run_profile(seeds: list[int], cases: int, stress: bool, out: Path) -> dict[str, float]:
    accs: list[float] = []
    autos: list[float] = []
    for seed in seeds:
        d = out / ("stress" if stress else "standard") / str(seed)
        generate(d, n_cases=cases, seed=seed, stress=stress)
        engine, _summary = reconcile(read_ledger(d / "ledger.csv"),
                                     read_bank(d / "bank_statement.csv"))
        truth = json.loads((d / "ground_truth.json").read_text(encoding="utf-8"))
        result = score(engine, truth)
        accs.append(result["case_accuracy"])
        autos.append(result["auto_precision"])
    return {
        "mean_case_accuracy": statistics.mean(accs),
        "min_case_accuracy": min(accs),
        "min_auto_precision": min(autos),
        "runs": len(seeds),
        "per_seed": accs,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=HOLDOUT_SEEDS)
    ap.add_argument("--cases", type=int, default=400)
    ap.add_argument("--keep", type=Path, default=None,
                    help="write the generated datasets here instead of a temp dir")
    args = ap.parse_args()

    out = args.keep or Path(tempfile.mkdtemp(prefix="holdout_"))
    print()
    print("  CROSS-SEED HOLDOUT  (%d seeds x 2 profiles, %d cases each)"
          % (len(args.seeds), args.cases))
    print("  seeds: " + " ".join(str(s) for s in args.seeds))
    print("  " + "=" * 62)
    print("  %-14s %10s %10s %18s" % ("profile", "mean acc", "min acc", "min auto-prec"))
    print("  " + "-" * 62)

    worst_auto = 1.0
    for stress in (False, True):
        r = run_profile(args.seeds, args.cases, stress, out)
        worst_auto = min(worst_auto, r["min_auto_precision"])
        print("  %-14s %10.4f %10.4f %18.4f"
              % ("adversarial" if stress else "standard",
                 r["mean_case_accuracy"], r["min_case_accuracy"], r["min_auto_precision"]))

    print()
    print("  Auto-resolve precision across all %d runs never dropped below %.4f."
          % (len(args.seeds) * 2, worst_auto))
    print("  That is the one that matters: a missed match becomes a queue item,")
    print("  a wrong auto-match becomes a silently balanced set of books.")
    print()


if __name__ == "__main__":
    main()
