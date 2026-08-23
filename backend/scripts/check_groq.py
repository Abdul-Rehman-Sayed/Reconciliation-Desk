"""Verify Groq before trusting it in a demo.

Checks, in order: the key loads, the live model list comes back, our preferred
model is actually on it, and a real structured-output call returns valid JSON.

    python scripts/check_groq.py
    python scripts/check_groq.py --list      # just print every model Groq serves
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm  # noqa: E402

SAMPLE = {
    "exception_id": "EX_SELFTEST",
    "engine_finding": "unmatched_bank",
    "engine_note": (
        "The statement shows 4820.00 on 2026-07-14 narrated "
        "'NEFT-HDFC-pay_9xQm2LtVb4 -UNKNOWNTRADERS', and there is no ledger entry behind it."
    ),
    "engine_confidence": 0.15,
    "ledger_records": [],
    "bank_records": [{
        "id": "S09999", "side": "bank", "date": "2026-07-14", "amount": 4820.00,
        "reference_number": "pay_9xQm2LtVb4", "type": "CREDIT",
        "narration": "NEFT-HDFC-pay_9xQm2LtVb4-UNKNOWNTRADERS",
    }],
    "evidence": {"nearest_on_other_side": None},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    print()
    if llm.use_mock():
        print("  [stop] USE_MOCK_LLM is on, so this script would test the rule-based")
        print("         stand-in rather than Groq. Set USE_MOCK_LLM=false in backend/.env")
        print("         to check the real thing.")
        print()
        return 1

    key = llm.api_key()
    if not key:
        print("  [FAIL] GROQ_API_KEY is not set.")
        print("         cp .env.example .env   then paste your key from")
        print("         https://console.groq.com/keys")
        return 1
    print("  [ ok ] key loaded from .env  (%s...%s)" % (key[:7], key[-4:]))

    try:
        available = llm.list_models()
    except Exception as exc:                          # noqa: BLE001
        print("  [FAIL] could not reach Groq: %s" % str(exc)[:200])
        return 1
    print("  [ ok ] Groq is serving %d models" % len(available))

    if args.list:
        for m in available:
            print("         " + m)
        return 0

    try:
        model = llm.resolve_model(force=True)
    except Exception as exc:                          # noqa: BLE001
        print("  [FAIL] %s" % str(exc)[:300])
        return 1

    rank = llm.MODEL_PREFERENCE.index(model) + 1 if model in llm.MODEL_PREFERENCE else None
    print("  [ ok ] selected %s%s" % (model, "  (preference #%d)" % rank if rank else ""))
    missing = [m for m in llm.MODEL_PREFERENCE if m not in available]
    if missing:
        print("         not currently served: " + ", ".join(missing))

    print("  [ .. ] sending one structured-output call")
    results, stats = llm.explain_exceptions([SAMPLE])
    got = results.get("EX_SELFTEST")
    if not got or got.get("source") != "groq":
        print("  [FAIL] no usable answer came back")
        print("         %s" % json.dumps(stats, indent=2)[:600])
        return 1

    print("  [ ok ] valid JSON back in the required shape")
    print()
    print("         category         %s" % got["category"])
    print("         confidence       %.2f" % got["confidence"])
    print("         suggested_action %s" % got["suggested_action"])
    print("         explanation      %s" % got["explanation"])
    print()
    print("         tokens  prompt %d / completion %d  in %d api call(s)"
          % (stats["prompt_tokens"], stats["completion_tokens"], stats["api_calls"]))
    print()
    if got["suggested_action"] == "approve":
        print("  [warn] it suggested 'approve' for a bank credit with no ledger record.")
        print("         That is the failure mode the prompt is written to prevent -")
        print("         worth re-running before the demo.")
    else:
        print("  [ ok ] it did not try to resolve an orphan.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
