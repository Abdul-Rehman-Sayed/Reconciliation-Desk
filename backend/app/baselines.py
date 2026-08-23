"""
What the layered engine is actually better than.

A match rate with nothing beside it is not a result, it is a number. Two
baselines give it a shape:

  1. **Naive rules.** An exact join on reference and amount - the twenty lines
     of pandas that every finance team has already written in a spreadsheet
     macro. This is the honest floor. If the layered engine is not meaningfully
     above it, six passes were not worth building.

  2. **LLM decides everything.** No deterministic layer at all: both sides go
     to the model and it returns the pairings. This is the version a demo gets
     built as when nobody has thought about cost.

Baseline 1 is deterministic and recomputed on demand - it is free.

Baseline 2 costs real tokens, and running it over the full batch would be
spending exactly what the architecture exists to save, which would also make
the comparison dishonest: "we avoided this cost" is not a claim you can support
by paying the cost. So it runs once, over a fixed seeded subsample, and freezes
to data/baselines/. It is never recomputed unless someone passes --force, and
every number derived from it is labelled a subsample projection wherever it is
displayed.
"""

from __future__ import annotations

import json
import random
import time
from itertools import product
from pathlib import Path
from typing import Any

from . import llm
from .matching import normalize_ref, records_from_rows

BASELINE_DIR = Path(__file__).resolve().parents[1] / "data" / "baselines"

# Big enough for the category mix to show up, small enough to cost one request.
SUBSAMPLE_RECORDS = 40
SUBSAMPLE_SEED = 4242


# --------------------------------------------------------------------------
# Shared scoring
# --------------------------------------------------------------------------
def _expected_pairs(truth: dict[str, Any]) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for case in truth.get("cases", []):
        for lid, sid in case.get("expected_links", []):
            out.add((lid, sid))
    return out


def _score_pairs(proposed: set[tuple[str, str]],
                 expected: set[tuple[str, str]]) -> dict[str, Any]:
    tp = proposed & expected
    precision = len(tp) / len(proposed) if proposed else 0.0
    recall = len(tp) / len(expected) if expected else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "proposed": len(proposed),
        "expected": len(expected),
        "true_positives": len(tp),
        "false_positives": len(proposed - expected),
        "false_negatives": len(expected - proposed),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


# --------------------------------------------------------------------------
# Baseline 1 - naive deterministic join. Free.
# --------------------------------------------------------------------------
def naive_join(ledger_rows: list[dict[str, Any]], bank_rows: list[dict[str, Any]],
               truth: dict[str, Any]) -> dict[str, Any]:
    """Exact reference + exact amount, one row to one row, first come first served.

    No fee awareness, no date window, no duplicate handling, no fuzzy matching,
    no ambiguity check. This is the thing being replaced.
    """
    t0 = time.perf_counter()
    ledger = records_from_rows(ledger_rows, "ledger")
    bank = records_from_rows(bank_rows, "bank")

    # A real spreadsheet join reads the reference column and nothing else - it
    # does not know references can hide in a narration, so neither does this.
    index: dict[tuple[str, int], list[str]] = {}
    for b in bank:
        raw = normalize_ref(b.reference_number) if b.ref_source == "column" else ""
        if raw:
            index.setdefault((raw, int(round(b.amount * 100))), []).append(b.rec_id)

    proposed: set[tuple[str, str]] = set()
    used: set[str] = set()
    for l in ledger:
        key = (normalize_ref(l.reference_number), int(round(l.amount * 100)))
        for sid in index.get(key, []):
            if sid in used:
                continue
            used.add(sid)
            proposed.add((l.rec_id, sid))
            break

    expected = _expected_pairs(truth)
    result = _score_pairs(proposed, expected)
    matched_records = len(used) + len({p[0] for p in proposed})
    total = len(ledger) + len(bank)
    result.update({
        "name": "naive_exact_join",
        "label": "Exact reference + amount join",
        "description": (
            "One equality join on the reference column and the amount to the paisa. "
            "No fee handling, no settlement delay, no duplicates, no fuzzy matching."
        ),
        "records": total,
        "records_matched": matched_records,
        "match_rate": round(matched_records / total, 4) if total else 0.0,
        "wall_seconds": round(time.perf_counter() - t0, 4),
        "cost": "free - deterministic",
    })
    return result


# --------------------------------------------------------------------------
# Baseline 2 - the model decides everything. Capped, frozen, never rerun.
# --------------------------------------------------------------------------
_BASELINE_SYSTEM = (
    "You reconcile a merchant ledger against a bank statement for an Indian payments "
    "desk. Amounts are INR.\n"
    "You are given every ledger row and every bank row as [id,date,amount,reference,party].\n"
    "Return every pairing you believe is the same underlying transaction.\n"
    "Rules: one ledger row pairs with at most one bank row. Do not pair rows you cannot "
    "justify - a missing pair is far better than a wrong one. Some rows genuinely have "
    "no counterpart; leave those out."
)

_BASELINE_SCHEMA = {
    "type": "object",
    "properties": {
        "pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"l": {"type": "string"}, "b": {"type": "string"}},
                "required": ["l", "b"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["pairs"],
    "additionalProperties": False,
}


def _subsample(ledger_rows: list[dict[str, Any]], bank_rows: list[dict[str, Any]],
               truth: dict[str, Any], size: int) -> tuple[list[Any], list[Any], set[tuple[str, str]]]:
    """A seeded slice of whole cases, so the flaw mix survives the sampling.

    Sampling loose records would break pairs across the boundary and score the
    model for failing to find a row that was never in the prompt. Whole cases
    keep both halves together.
    """
    rng = random.Random(SUBSAMPLE_SEED)
    cases = list(truth.get("cases", []))
    rng.shuffle(cases)

    keep_ids: set[str] = set()
    keep_pairs: set[tuple[str, str]] = set()
    for case in cases:
        ids = set()
        for lid, sid in case.get("expected_links", []):
            ids.update((lid, sid))
        ids.update(case.get("duplicate_ids", []))
        ids.update(case.get("unresolved_ids", []))
        if len(keep_ids) + len(ids) > size:
            continue
        keep_ids.update(ids)
        for lid, sid in case.get("expected_links", []):
            keep_pairs.add((lid, sid))
        if len(keep_ids) >= size:
            break

    ledger = [r for r in ledger_rows if str(r["txn_id"]) in keep_ids]
    bank = [r for r in bank_rows if str(r["stmt_id"]) in keep_ids]
    return ledger, bank, keep_pairs


def _wire(rows: list[dict[str, Any]], side: str) -> list[list[Any]]:
    out = []
    for r in rows:
        if side == "ledger":
            out.append([str(r["txn_id"]), str(r["date"])[5:10], round(float(r["amount"]), 2),
                        str(r.get("reference_number", ""))[:24],
                        str(r.get("counterparty", ""))[:32]])
        else:
            out.append([str(r["stmt_id"]), str(r["date"])[5:10], round(float(r["amount"]), 2),
                        str(r.get("reference_number", ""))[:24],
                        str(r.get("narration", ""))[:44]])
    return out


def baseline_path(profile: str) -> Path:
    return BASELINE_DIR / ("llm_only_%s.json" % profile)


def load_llm_only(profile: str) -> dict[str, Any] | None:
    path = baseline_path(profile)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def run_llm_only(profile: str, ledger_rows: list[dict[str, Any]],
                 bank_rows: list[dict[str, Any]], truth: dict[str, Any],
                 force: bool = False, size: int = SUBSAMPLE_RECORDS) -> dict[str, Any]:
    """Measure the LLM-only baseline once and freeze it.

    Returns the frozen result untouched unless force=True. This function is the
    only place in the repo that will spend tokens on something other than a real
    exception, and it is deliberately awkward to trigger: nothing calls it on a
    request path, only scripts/baseline.py does.
    """
    existing = load_llm_only(profile)
    if existing and not force:
        return existing

    ledger, bank, expected = _subsample(ledger_rows, bank_rows, truth, size)
    payload = {"ledger": _wire(ledger, "ledger"), "bank": _wire(bank, "bank")}
    user = (
        "Rows are [id,date,amount,reference,party].\n"
        + json.dumps(payload, separators=(",", ":"))
    )

    t0 = time.perf_counter()
    usage: dict[str, Any] = {}
    proposed: set[tuple[str, str]] = set()
    error = None

    if llm.use_mock():
        # The mock cannot do this task - it classifies exceptions, it does not
        # match a book. Refusing is the correct answer; a fabricated baseline
        # would be worse than none.
        error = "USE_MOCK_LLM is on. The LLM-only baseline needs the real model to mean anything."
    else:
        key = llm.api_key()
        if not key:
            error = "GROQ_API_KEY is not set"
        else:
            try:
                model = llm.resolve_model()
                body = {
                    "model": model,
                    "temperature": 0.0,
                    # One pair is ~18 tokens, but gpt-oss spends reasoning
                    # tokens against the same budget before it emits any JSON.
                    # Sized too tightly the first time and the reply came back
                    # truncated, which Groq reports as a schema failure with an
                    # empty completion rather than as a length error.
                    "max_tokens": min(4000, 40 * len(ledger) + 1200),
                    "reasoning_effort": "low",
                    "messages": [
                        {"role": "system", "content": _BASELINE_SYSTEM},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": "pairs", "schema": _BASELINE_SCHEMA,
                                        "strict": True},
                    },
                }
                stats: dict[str, Any] = {}
                llm._bucket.take(int(len(user) / llm.CHARS_PER_TOKEN)
                                 + int(body["max_tokens"]))
                if not llm._budget_take():
                    raise RuntimeError("local daily call budget is spent")
                raw = llm._post(body, key, stats)
                usage = raw.get("usage", {})
                parsed = json.loads(raw["choices"][0]["message"]["content"])
                for pair in parsed.get("pairs", []):
                    l_id, b_id = str(pair.get("l", "")), str(pair.get("b", ""))
                    if l_id and b_id:
                        proposed.add((l_id, b_id))
            except Exception as exc:                  # noqa: BLE001
                error = str(exc)[:300]

    wall = time.perf_counter() - t0
    scored = _score_pairs(proposed, expected)
    result = {
        "name": "llm_only",
        "label": "Model decides every pairing",
        "description": (
            "Both sides handed to the model with no deterministic layer at all, and its "
            "pairings taken as the answer."
        ),
        "profile": profile,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": None if error else llm.resolve_model(),
        "records_sampled": len(ledger) + len(bank),
        "ledger_sampled": len(ledger),
        "bank_sampled": len(bank),
        "subsample_seed": SUBSAMPLE_SEED,
        "batch_size": len(ledger) + len(bank),
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0)
        or int(usage.get("prompt_tokens", 0) or 0) + int(usage.get("completion_tokens", 0) or 0),
        "wall_seconds": round(wall, 3),
        "error": error,
        "caveat": (
            "Measured on a fixed %d-record subsample, never on the full batch. Every "
            "figure projected from it is a projection and is labelled as one."
            % (len(ledger) + len(bank))
        ),
        **scored,
    }

    if error:
        # Do not freeze a failure. A poisoned baseline file would answer "the
        # model scored zero" forever, which is a claim we would not have earned.
        return result
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    baseline_path(profile).write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def bundle(profile: str, ledger_rows: list[dict[str, Any]], bank_rows: list[dict[str, Any]],
           truth: dict[str, Any] | None) -> dict[str, Any] | None:
    """Both baselines for the comparison panel. Never triggers a live call."""
    if truth is None:
        return None
    return {
        "profile": profile,
        "naive": naive_join(ledger_rows, bank_rows, truth),
        "llm_only": load_llm_only(profile),
    }
