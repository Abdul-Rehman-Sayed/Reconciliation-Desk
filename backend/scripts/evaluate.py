"""Run the deterministic engine on the bundled data and score it. No LLM, no UI.

    python scripts/evaluate.py
    python scripts/evaluate.py --verbose     # also list every incorrect case
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dataio import load_bundled, load_ground_truth  # noqa: E402
from app.matching import reconcile  # noqa: E402
from app.scoring import print_report, score  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--profile", default="standard", choices=["standard", "stress"])
    args = ap.parse_args()

    ledger, bank = load_bundled(args.profile)
    engine, summary = reconcile(ledger, bank)

    print()
    print("  ENGINE RUN  (%s dataset)" % args.profile)
    print("  " + "=" * 62)
    print("  ledger rows           %5d" % summary["ledger_rows"])
    print("  bank rows             %5d" % summary["bank_rows"])
    print("  records in play       %5d" % summary["total_records"])
    print()
    print("  %-14s %8s %9s %10s %9s %9s"
          % ("pass", "ms", "links", "records", "left L", "left B"))
    print("  " + "-" * 64)
    for p in summary["passes"]:
        print("  %-14s %8.1f %9d %10d %9d %9d"
              % (p["name"], p["duration_ms"], p["links_made"], p["records_resolved"],
                 p["remaining_ledger"], p["remaining_bank"]))
    print()
    # Two different units below, kept in two blocks on purpose. Records
    # partition the batch and sum to the total; exceptions are *groups* and do
    # not. Printing them as one column of integers invites the reader to add
    # 52 to 772 and find it does not reach 856.
    auto = summary["records_auto_resolved"]
    proposed = summary["records_proposed"]
    unresolved = summary["records_unresolved"]
    total = summary["total_records"]

    print("  RECORDS  (these three partition the batch)")
    print("  " + "-" * 62)
    print("    auto-resolved by rule %5d   match rate          %6.2f%%"
          % (auto, summary["match_rate_auto"] * 100))
    print("    LLM-proposed          %5d   incl. proposed      %6.2f%%"
          % (proposed, summary["match_rate_with_proposed"] * 100))
    print("    still unresolved      %5d" % unresolved)
    print("    %s %5d   of %d records in play"
          % ("total".ljust(21), auto + proposed + unresolved, total))
    if summary.get("accounting_overlap"):
        print("    !! %d record(s) counted in two buckets - engine bug"
              % summary["accounting_overlap"])
    print()
    print("  EXCEPTION QUEUE  (groups of records, not records - does not sum above)")
    print("  " + "-" * 62)
    print("    exceptions raised     %5d   covering %d records, %d need the LLM"
          % (summary["exceptions_total"], summary["exception_records"],
             summary["exceptions_needing_llm"]))
    print("    of which duplicates   %5d   already counted as auto-resolved"
          % summary["duplicates_flagged"])
    print()
    print("  value auto-resolved   %6.2f%% of INR %s"
          % (summary["value_rate_auto"] * 100, format(summary["value_total"], ",.2f")))

    truth = load_ground_truth(args.profile)
    if truth is None:
        print("\n  no ground_truth.json found - skipping accuracy\n")
        return

    result = score(engine, truth)
    print_report(result)

    if args.verbose:
        bad = [c for c in result["case_results"] if c["verdict"] != "correct"]
        print("  %d imperfect case(s):" % len(bad))
        for c in bad:
            print("    %s  %-18s %-16s wrong=%s missing=%s dup_missed=%s orphan_linked=%s"
                  % (c["case_id"], c["category"], c["verdict"], c["wrong"], c["missing"],
                     c["duplicate_missed"], c["orphan_incorrectly_linked"]))
        print()


if __name__ == "__main__":
    main()
