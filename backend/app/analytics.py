"""
Analysis over a finished run. Reads what is already on disk and computes nothing
that would need another model call.

Four things live here:

  * a per-category confusion matrix for the classifier, so "accurate" stops
    being one blended number and starts being a claim per flaw type
  * confidence calibration, which is the only evidence that the confidence
    figure means anything at all
  * the cost and latency split between the deterministic layer and the model
  * the same numbers translated into hours of manual work not done

None of it calls Groq. Every input is either the engine's own output, the
synthetic answer key, or a cached verdict that was paid for once already. If
anything in this module ever needs a live call, that is a bug in the design of
the metric, not a budget to ask for.
"""

from __future__ import annotations

from typing import Any

from .llm import CATEGORIES

# --------------------------------------------------------------------------
# What the classifier should have said
# --------------------------------------------------------------------------
# The answer key records which flaw was injected. The model answers in its own
# category vocabulary. This is the map between them, and it is the whole basis
# of the confusion matrix - so it is written out explicitly rather than inferred,
# and every judgement call in it is defended on the line it appears.
GROUND_TRUTH_TO_CATEGORY: dict[str, str] = {
    "fee_deducted": "fee_adjustment",
    "date_shift": "date_delay",
    "late_settlement": "date_delay",       # same phenomenon, further out
    "split_batch": "split_payment",
    "reference_typo": "reference_mismatch",
    "narration_only_ref": "reference_mismatch",   # the reference was findable, just not in its column
    "ambiguous_decoy": "reference_mismatch",      # a reference that fits two rows is still a reference problem
    "duplicate_ledger": "duplicate",
    "refund_reversal": "refund",
    "orphan_bank": "orphan_bank",
    "orphan_ledger": "orphan_ledger",
    # A short settlement is not a matching failure - the pairing is right and the
    # money is wrong. There is no category for "the bank paid less than it owed",
    # and inventing one to flatter the score would be cheating. "other" is the
    # honest answer, and the model gets credit for reaching it.
    "partial_settlement": "other",
    # Clean matches never reach the classifier. If one appears here, something
    # upstream let a solved record through, which is worth seeing.
    "clean_exact": "__should_not_reach_llm__",
}


def _case_index(truth: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """record id -> the case that produced it."""
    index: dict[str, dict[str, Any]] = {}
    for case in truth.get("cases", []):
        for lid, sid in case.get("expected_links", []):
            index[lid] = case
            index[sid] = case
        for rid in case.get("duplicate_ids", []) + case.get("unresolved_ids", []):
            index[rid] = case
    return index


def _expected_category(case: dict[str, Any]) -> str | None:
    return GROUND_TRUTH_TO_CATEGORY.get(case.get("category", ""))


def _verdicts(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Every exception that carries a real verdict, with its records attached."""
    out = []
    for exc in run.get("exceptions", []):
        verdict = exc.get("llm") or {}
        if verdict.get("source") not in ("groq", "mock"):
            continue
        out.append({"exception": exc, "verdict": verdict})
    return out


# --------------------------------------------------------------------------
# 1. Per-category confusion matrix
# --------------------------------------------------------------------------
def confusion(run: dict[str, Any], truth: dict[str, Any] | None) -> dict[str, Any] | None:
    """Precision and recall per flaw category for the classifier.

    Not the engine's matching accuracy - that is scoring.py. This is the second
    layer answering "what kind of problem is this", scored against the flaw that
    was actually injected. One blended accuracy number hides the thing you most
    want to know, which is whether it is good at the categories that matter and
    bad at the ones that do not.
    """
    if truth is None:
        return None

    index = _case_index(truth)
    rows = _verdicts(run)
    if not rows:
        return None

    matrix: dict[str, dict[str, int]] = {}
    unmapped = 0
    scored = 0
    leaked_clean = 0

    for row in rows:
        exc = row["exception"]
        record_ids = list(exc.get("ledger_ids", [])) + list(exc.get("stmt_ids", []))
        case = next((index[r] for r in record_ids if r in index), None)
        if case is None:
            unmapped += 1
            continue
        expected = _expected_category(case)
        if expected is None:
            unmapped += 1
            continue
        if expected == "__should_not_reach_llm__":
            leaked_clean += 1
            continue

        predicted = row["verdict"].get("category", "other")
        matrix.setdefault(expected, {}).setdefault(predicted, 0)
        matrix[expected][predicted] += 1
        scored += 1

    # Per-category precision/recall from the matrix.
    per_category = []
    predicted_totals: dict[str, int] = {}
    for actuals in matrix.values():
        for predicted, n in actuals.items():
            predicted_totals[predicted] = predicted_totals.get(predicted, 0) + n

    for expected in sorted(matrix, key=lambda c: -sum(matrix[c].values())):
        actuals = matrix[expected]
        support = sum(actuals.values())
        tp = actuals.get(expected, 0)
        predicted_n = predicted_totals.get(expected, 0)
        precision = tp / predicted_n if predicted_n else 0.0
        recall = tp / support if support else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        per_category.append({
            "category": expected,
            "support": support,
            "correct": tp,
            "predicted_as_this": predicted_n,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "confused_with": sorted(
                ({"category": k, "count": v} for k, v in actuals.items() if k != expected),
                key=lambda d: -d["count"],
            ),
        })

    total_correct = sum(c["correct"] for c in per_category)
    macro_f1 = (sum(c["f1"] for c in per_category) / len(per_category)) if per_category else 0.0

    # The matrix is deliberately narrow, and that narrowness is the point.
    # Most flaw categories never reach the classifier at all - a gateway fee or
    # a settlement delay is resolved by a rule, with a proof, before the model
    # is asked. Listing what did not arrive turns a thin-looking matrix into the
    # architecture argument: these are the categories the LLM was never needed
    # for. Without this the reader concludes the classifier was only tested on
    # four things, when what actually happened is it was only *required* for four.
    reached = set(matrix)
    resolved_first = []
    for gt_category, count in (truth.get("cases_by_category") or {}).items():
        expected = GROUND_TRUTH_TO_CATEGORY.get(gt_category)
        if expected is None or expected == "__should_not_reach_llm__" or expected in reached:
            continue
        resolved_first.append({
            "ground_truth_category": gt_category,
            "would_have_been": expected,
            "cases": count,
        })
    resolved_first.sort(key=lambda d: -d["cases"])

    return {
        "scored": scored,
        "correct": total_correct,
        "accuracy": round(total_correct / scored, 4) if scored else 0.0,
        "macro_f1": round(macro_f1, 4),
        "unmapped": unmapped,
        "clean_matches_that_reached_the_model": leaked_clean,
        "resolved_before_the_model": resolved_first,
        "cases_resolved_before_the_model": sum(d["cases"] for d in resolved_first),
        "labels": [c for c in CATEGORIES if c in matrix or c in predicted_totals],
        "matrix": {k: dict(v) for k, v in matrix.items()},
        "by_category": per_category,
        "note": (
            "Rows are the flaw that was actually injected, columns are what the "
            "classifier called it. Scored only on exceptions traceable to a case in "
            "the answer key."
        ),
    }


# --------------------------------------------------------------------------
# 2. Confidence calibration
# --------------------------------------------------------------------------
CALIBRATION_BINS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01))


def calibration(run: dict[str, Any], truth: dict[str, Any] | None) -> dict[str, Any] | None:
    """Of the verdicts claiming >=90% confidence, how many were actually right?

    A confidence score nobody has checked is decoration. This checks it. The
    number that matters for the pitch is the top bin: if the model says 90% and
    is right 60% of the time, the number is worse than useless, because an
    operator would act on it.
    """
    if truth is None:
        return None

    index = _case_index(truth)
    rows = _verdicts(run)
    if not rows:
        return None

    bins = [{"lower": lo, "upper": min(hi, 1.0), "n": 0, "correct": 0} for lo, hi in CALIBRATION_BINS]
    points: list[tuple[float, bool]] = []

    for row in rows:
        exc = row["exception"]
        record_ids = list(exc.get("ledger_ids", [])) + list(exc.get("stmt_ids", []))
        case = next((index[r] for r in record_ids if r in index), None)
        if case is None:
            continue
        expected = _expected_category(case)
        if expected is None or expected == "__should_not_reach_llm__":
            continue

        verdict = row["verdict"]
        confidence = float(verdict.get("confidence", 0.0) or 0.0)
        correct = verdict.get("category") == expected
        points.append((confidence, correct))
        for b, (lo, hi) in zip(bins, CALIBRATION_BINS):
            if lo <= confidence < hi:
                b["n"] += 1
                b["correct"] += int(correct)
                break

    if not points:
        return None

    for b in bins:
        b["actual_accuracy"] = round(b["correct"] / b["n"], 4) if b["n"] else None
        b["stated_midpoint"] = round((b["lower"] + b["upper"]) / 2, 3)
        b["gap"] = (round(b["actual_accuracy"] - b["stated_midpoint"], 4)
                    if b["actual_accuracy"] is not None else None)

    mean_confidence = sum(c for c, _ in points) / len(points)
    accuracy = sum(1 for _, ok in points if ok) / len(points)

    # Expected calibration error: how far the stated confidence sits from
    # observed accuracy, weighted by how many verdicts fall in each bin.
    ece = sum(
        (b["n"] / len(points)) * abs(b["actual_accuracy"] - b["stated_midpoint"])
        for b in bins if b["n"] and b["actual_accuracy"] is not None
    )

    high = [(c, ok) for c, ok in points if c >= 0.9]
    high_accuracy = (sum(1 for _, ok in high if ok) / len(high)) if high else None

    return {
        "scored": len(points),
        "mean_confidence": round(mean_confidence, 4),
        "actual_accuracy": round(accuracy, 4),
        "overconfidence": round(mean_confidence - accuracy, 4),
        "expected_calibration_error": round(ece, 4),
        "high_confidence_n": len(high),
        "high_confidence_accuracy": round(high_accuracy, 4) if high_accuracy is not None else None,
        "bins": bins,
        "note": (
            "'Correct' means the classifier named the flaw that was actually injected. "
            "A positive overconfidence figure means the model claims more certainty "
            "than it earns."
        ),
    }


# --------------------------------------------------------------------------
# 3. Cost and latency split
# --------------------------------------------------------------------------
def cost_split(run: dict[str, Any], baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the layering actually bought, in calls, tokens and milliseconds.

    The headline is the share of records that never touched a model at all. The
    counterfactual - what the same batch would have cost sent entirely to an LLM
    - comes from the frozen subsample in data/baselines/, never from a live run
    over the whole batch, because measuring that properly would cost exactly the
    thing the architecture exists to avoid.
    """
    summary = run.get("summary", {})
    stats = run.get("llm_stats") or {}
    total = int(summary.get("total_records", 0) or 0)

    exceptions = run.get("exceptions", [])
    touched_ids: set[str] = set()
    for exc in exceptions:
        if exc.get("needs_llm"):
            touched_ids.update(exc.get("ledger_ids", []))
            touched_ids.update(exc.get("stmt_ids", []))
    touched = len(touched_ids)

    engine_ms = sum(float(p.get("duration_ms", 0) or 0) for p in summary.get("passes", []))
    calls = int(stats.get("api_calls", 0) or 0)
    from_cache = int(stats.get("from_cache", 0) or 0)
    requested = int(stats.get("requested", 0) or 0)
    prompt_tokens = int(stats.get("prompt_tokens", 0) or 0)
    completion_tokens = int(stats.get("completion_tokens", 0) or 0)

    # A run served entirely from cache reports zero tokens, which is true and
    # also useless for the comparison - it would make the layered approach look
    # infinitely cheap rather than cheap. So the cold cost is estimated from the
    # payloads that would have been sent, and labelled an estimate. The measured
    # figure is always preferred when there is one.
    # Driven by the exceptions themselves rather than by llm_stats["requested"].
    # A run that was fully cached, or one whose stats block is missing, still has
    # a real cold cost - reading it off the stats would report zero for exactly
    # the runs the estimate exists to serve.
    from .llm import CHARS_PER_TOKEN, MAX_TOKENS_PER_RECORD, compact_payload
    import json as _json

    estimated_cold_tokens = 0
    lookup = {r["id"]: r for r in run["records"]["ledger"] + run["records"]["bank"]}
    for exc in exceptions:
        if not exc.get("needs_llm"):
            continue
        blob = _json.dumps(compact_payload(exc, lookup), separators=(",", ":"), default=str)
        estimated_cold_tokens += int(len(blob) / CHARS_PER_TOKEN) + MAX_TOKENS_PER_RECORD

    measured_tokens = prompt_tokens + completion_tokens
    tokens_for_comparison = measured_tokens or estimated_cold_tokens

    out: dict[str, Any] = {
        "total_records": total,
        "records_never_seen_by_model": total - touched,
        "records_seen_by_model": touched,
        "share_resolved_without_model": round((total - touched) / total, 4) if total else 0.0,
        "exceptions_requested": requested,
        "exceptions_served_from_cache": from_cache,
        "cache_hit_rate": round(from_cache / requested, 4) if requested else 0.0,
        "api_calls": calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": measured_tokens,
        "estimated_cold_tokens": estimated_cold_tokens,
        "tokens_measured": bool(measured_tokens),
        "tokens_saved_by_cache": int(stats.get("tokens_saved_by_cache", 0) or 0),
        "engine_ms": round(engine_ms, 2),
        "records_per_second": round(total / (engine_ms / 1000), 0) if engine_ms else None,
        "mode": stats.get("mode"),
        "model": stats.get("model"),
    }

    if baseline and baseline.get("llm_only"):
        llm_only = baseline["llm_only"]
        n = int(llm_only.get("records_sampled", 0) or 0)
        if n:
            tokens_per_record = float(llm_only.get("total_tokens", 0) or 0) / n
            seconds_per_record = float(llm_only.get("wall_seconds", 0) or 0) / n
            out["llm_only_projection"] = {
                "measured_on_records": n,
                "tokens_per_record": round(tokens_per_record, 1),
                "projected_tokens_full_batch": int(tokens_per_record * total),
                "projected_seconds_full_batch": round(seconds_per_record * total, 1),
                "projected_calls_full_batch": int(
                    round(total / max(1, int(llm_only.get("batch_size", 1) or 1)))
                ),
                "token_multiple": (
                    round((tokens_per_record * total) / tokens_for_comparison, 1)
                    if tokens_for_comparison else None
                ),
                "token_multiple_basis": (
                    "measured" if measured_tokens else "estimated from the payloads that "
                    "would have been sent, because this run was served from cache"
                ),
                "caveat": (
                    "Projected from a fixed %d-record subsample, not measured over the full "
                    "batch. Running the LLM-only baseline across all %d records would cost "
                    "precisely what the layered design exists to avoid, so it was measured "
                    "once, small, and frozen." % (n, total)
                ),
            }
    return out


# --------------------------------------------------------------------------
# 4. Hours of manual reconciliation avoided
# --------------------------------------------------------------------------
# An analyst tying out a statement line by line in a spreadsheet. 45 seconds is
# the working figure for a clean line - find the reference, confirm the amount,
# tick it - and an exception is several minutes because it means chasing
# something. These are assumptions, they are stated as assumptions, and they are
# exposed as parameters so anyone who thinks they are wrong can move them and
# see the answer change rather than argue about it.
SECONDS_PER_CLEAN_MATCH = 45
SECONDS_PER_EXCEPTION = 240
WORKING_DAY_HOURS = 7.5


def hours_saved(
    run: dict[str, Any],
    seconds_per_clean: float = SECONDS_PER_CLEAN_MATCH,
    seconds_per_exception: float = SECONDS_PER_EXCEPTION,
) -> dict[str, Any]:
    summary = run.get("summary", {})
    total = int(summary.get("total_records", 0) or 0)
    auto = int(summary.get("records_auto_resolved", 0) or 0)
    exceptions = int(summary.get("exceptions_total", 0) or 0)

    manual_seconds = total * seconds_per_clean
    # After the engine: the auto-resolved share costs nothing, and what is left
    # is a queue of exceptions that still has to be worked by a person - the
    # honest version of this number does not pretend the queue is free.
    remaining_seconds = exceptions * seconds_per_exception
    saved = max(0.0, manual_seconds - remaining_seconds)

    return {
        "assumptions": {
            "seconds_per_clean_match": seconds_per_clean,
            "seconds_per_exception": seconds_per_exception,
            "working_day_hours": WORKING_DAY_HOURS,
            "basis": (
                "A person ticking off a statement line by line. Both figures are "
                "assumptions, exposed so they can be argued with."
            ),
        },
        "records": total,
        "manual_hours": round(manual_seconds / 3600, 2),
        "hours_still_needed": round(remaining_seconds / 3600, 2),
        "hours_saved": round(saved / 3600, 2),
        "working_days_saved": round(saved / 3600 / WORKING_DAY_HOURS, 2),
        "share_of_effort_removed": round(saved / manual_seconds, 4) if manual_seconds else 0.0,
        "records_auto_resolved": auto,
        "exceptions_to_work": exceptions,
        "note": (
            "The queue is not counted as free. Manual hours minus the hours the "
            "exception queue still costs is the number, which is why it is lower "
            "than the match rate alone would suggest."
        ),
    }
