"""Measure the two naive baselines the layered engine is compared against.

The naive join is deterministic and free, so it recomputes every time.

The LLM-only baseline costs one real request and is frozen to
data/baselines/ the first time it runs. It will not run again unless you pass
--force, which is the point: the whole claim being made is that sending
everything to a model is expensive, and re-proving that on every rehearsal
would be an odd way to demonstrate it.

    python scripts/baseline.py                    # naive only, plus whatever is frozen
    python scripts/baseline.py --llm              # measure LLM-only if not already frozen
    python scripts/baseline.py --llm --force      # re-measure it (spends one request)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import baselines, llm  # noqa: E402
from app.dataio import load_bundled, load_ground_truth  # noqa: E402
from app.matching import reconcile  # noqa: E402
from app.scoring import score  # noqa: E402


def row(label: str, precision: float, recall: float, f1: float, extra: str = "") -> None:
    print("  %-34s %8.2f%% %8.2f%% %8.2f%%   %s"
          % (label, precision * 100, recall * 100, f1 * 100, extra))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="standard", choices=["standard", "stress"])
    ap.add_argument("--llm", action="store_true", help="measure the LLM-only baseline")
    ap.add_argument("--force", action="store_true", help="re-measure even if frozen")
    args = ap.parse_args()

    ledger, bank = load_bundled(args.profile)
    truth = load_ground_truth(args.profile)
    if truth is None:
        print("no ground truth for profile %s" % args.profile)
        return

    naive = baselines.naive_join(ledger, bank, truth)
    engine, summary = reconcile(ledger, bank)
    layered = score(engine, truth)

    print()
    print("  BASELINE COMPARISON  (%s dataset, %d records)"
          % (args.profile, summary["total_records"]))
    print("  " + "=" * 74)
    print("  %-34s %8s %8s %8s   %s"
          % ("approach", "precision", "recall", "F1", "cost"))
    print("  " + "-" * 74)

    auto_recall = layered["auto_true_positives"] / max(1, layered["pairs_expected"])
    auto_p = layered["auto_precision"]
    auto_f1 = (2 * auto_p * auto_recall / (auto_p + auto_recall)) if (auto_p + auto_recall) else 0.0

    row("Naive exact reference+amount join", naive["precision"], naive["recall"],
        naive["f1"], "free")
    row("Layered engine, auto-resolved only", auto_p, auto_recall, auto_f1, "free")
    row("Layered engine, incl. proposals", layered["precision"], layered["recall"],
        layered["f1"], "%d LLM calls" % max(1, -(-summary["exceptions_needing_llm"] // llm.BATCH_SIZE)))

    frozen = baselines.load_llm_only(args.profile)
    if args.llm and (args.force or frozen is None):
        print()
        print("  measuring the LLM-only baseline on a %d-record subsample..."
              % baselines.SUBSAMPLE_RECORDS)
        frozen = baselines.run_llm_only(args.profile, ledger, bank, truth, force=args.force)

    if frozen:
        if frozen.get("error"):
            print()
            print("  LLM-only baseline not measured: %s" % frozen["error"])
        else:
            row("Model decides everything *", frozen["precision"], frozen["recall"],
                frozen["f1"], "%d tokens / %d records"
                % (frozen["total_tokens"], frozen["records_sampled"]))
            print()
            print("  * measured on a fixed %d-record subsample (seed %d), frozen at %s."
                  % (frozen["records_sampled"], frozen["subsample_seed"], frozen["measured_at"]))
            print("    Projected over the full %d records: ~%d tokens, ~%.0fs wall."
                  % (summary["total_records"],
                     frozen["total_tokens"] / max(1, frozen["records_sampled"])
                     * summary["total_records"],
                     frozen["wall_seconds"] / max(1, frozen["records_sampled"])
                     * summary["total_records"]))
    else:
        print()
        print("  LLM-only baseline not measured yet. Run with --llm to spend one request.")

    print()
    print("  The naive join is the floor. It gets the clean matches and nothing else -")
    print("  no fee deduction, no settlement delay, no duplicate, no damaged reference.")
    print()


if __name__ == "__main__":
    main()
