"""Recover already-paid-for Groq verdicts out of saved runs and into the v2 cache.

The cache key is a hash of the exact payload sent, so changing the payload shape
orphans every entry written under the old shape. Those entries were paid for in
real quota. This walks the saved runs, rebuilds the v2 fingerprint from the
exception and its records, and re-files the verdict under the new key.

    python scripts/migrate_cache.py            # report what would move
    python scripts/migrate_cache.py --write    # actually move it
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm, store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write the recovered entries")
    args = ap.parse_args()

    cache = llm._load_cache()
    before = len(cache)
    recovered = 0
    skipped = 0

    run_files = sorted(store.RUNS_DIR.glob("run_*.json")) if store.RUNS_DIR.exists() else []
    for path in run_files:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        lookup = {r["id"]: r for r in run["records"]["ledger"] + run["records"]["bank"]}
        for exc in run.get("exceptions", []):
            verdict = exc.get("llm")
            # Only real model output is worth recovering. An "unavailable"
            # placeholder is not an answer, and caching one would pin a failure
            # in place forever.
            if not verdict or verdict.get("source") != "groq":
                skipped += 1
                continue
            key = llm.fingerprint(llm.compact_payload(exc, lookup))
            if key in cache:
                continue
            cache[key] = verdict
            recovered += 1

    print()
    print("  runs scanned          %5d" % len(run_files))
    print("  cache entries before  %5d" % before)
    print("  groq verdicts found   %5d" % (recovered + skipped))
    print("  recovered under v2    %5d" % recovered)
    print("  cache entries after   %5d" % len(cache))

    if args.write and recovered:
        llm._save_cache(cache)
        print("\n  written to %s\n" % llm.CACHE_PATH)
    elif recovered:
        print("\n  dry run - pass --write to keep them\n")
    else:
        print("\n  nothing to recover\n")


if __name__ == "__main__":
    main()
