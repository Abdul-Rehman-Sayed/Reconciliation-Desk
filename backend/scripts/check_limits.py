from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm

SKIP = ("whisper", "tts", "guard", "orpheus", "safeguard")


def probe(model: str, key: str) -> dict[str, str]:
    r = requests.post(
        llm.GROQ_BASE + "/chat/completions",
        headers={"Authorization": "Bearer " + key},
        json={"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
        timeout=30,
    )
    h = r.headers
    return {
        "status": str(r.status_code),
        "rpd": h.get("x-ratelimit-limit-requests", "?"),
        "rpd_left": h.get("x-ratelimit-remaining-requests", "?"),
        "tpm": h.get("x-ratelimit-limit-tokens", "?"),
        "tpm_left": h.get("x-ratelimit-remaining-tokens", "?"),
        "reset_req": h.get("x-ratelimit-reset-requests", "?"),
        "error": "" if r.ok else r.text[:120],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="probe only this model")
    args = ap.parse_args()

    if llm.use_mock():
        print("\n  USE_MOCK_LLM is on. Unset it to probe real limits.\n")
        return

    key = llm.api_key()
    if not key:
        print("\n  GROQ_API_KEY is not set in backend/.env\n")
        return

    try:
        available = llm.list_models()
    except Exception as exc:
        print("\n  could not list models: %s\n" % str(exc)[:200])
        return

    models = [args.model] if args.model else [
        m for m in available if not any(x in m for x in SKIP)
    ]
    if args.model and args.model not in available:
        print("\n  %s is not served on this account. Available: %s\n"
              % (args.model, ", ".join(available[:12])))
        return

    print()
    print("  GROQ LIMITS, READ FROM THE LIVE RESPONSE HEADERS")
    print("  " + "=" * 74)
    print("  %-30s %8s %9s %8s %10s  %s"
          % ("model", "req/day", "left", "tok/min", "left", "refill"))
    print("  " + "-" * 74)

    rows = []
    for model in models:
        try:
            info = probe(model, key)
        except requests.RequestException as exc:
            print("  %-30s  unreachable: %s" % (model, str(exc)[:40]))
            continue
        rows.append((model, info))
        print("  %-30s %8s %9s %8s %10s  %s%s"
              % (model, info["rpd"], info["rpd_left"], info["tpm"], info["tpm_left"],
                 info["reset_req"], "" if not info["error"] else "  !" + info["error"][:40]))

    usable = [(m, i) for m, i in rows if i["rpd"].isdigit() and i["tpm"].isdigit()]
    if usable:
        by_requests = max(usable, key=lambda r: int(r[1]["rpd"]))
        by_tokens = max(usable, key=lambda r: int(r[1]["tpm"]))
        print()
        print("  most requests per day   %s (%s)" % (by_requests[0], by_requests[1]["rpd"]))
        print("  most tokens per minute  %s (%s)" % (by_tokens[0], by_tokens[1]["tpm"]))
        print()
        print("  This project batches %d exceptions per request, so request count is rarely"
              % llm.BATCH_SIZE)
        print("  the binding limit - the per-minute token bucket is. Prefer the model with")
        print("  headroom on whichever of the two your workload actually presses against.")
    print()
    print("  currently preferred: %s" % ", ".join(llm.MODEL_PREFERENCE[:3]))
    print("  local daily budget:  %s" % llm.budget_status())
    print()


if __name__ == "__main__":
    main()
