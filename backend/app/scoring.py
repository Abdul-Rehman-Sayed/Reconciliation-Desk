from __future__ import annotations

from itertools import product
from typing import Any

from .matching import Engine


def _pairs(link_ledger: list[str], link_stmt: list[str]) -> set[tuple[str, str]]:
    return set(product(link_ledger, link_stmt))


def score(engine: Engine, truth: dict[str, Any]) -> dict[str, Any]:
    engine_pairs: set[tuple[str, str]] = set()
    pair_is_auto: dict[tuple[str, str], bool] = {}
    for link in engine.links:
        for pair in _pairs(link.ledger_ids, link.stmt_ids):
            engine_pairs.add(pair)
            pair_is_auto[pair] = link.auto_resolved

    engine_duplicates = {
        (e.ledger_ids + e.stmt_ids)[0] for e in engine.exceptions if e.kind == "duplicate"
    }
    linked_records: set[str] = set()
    for link in engine.links:
        linked_records.update(link.ledger_ids)
        linked_records.update(link.stmt_ids)

    expected_pairs: set[tuple[str, str]] = set()
    case_of_record: dict[str, str] = {}
    cases: dict[str, dict[str, Any]] = {}
    for case in truth["cases"]:
        cases[case["case_id"]] = case
        for lid, sid in case["expected_links"]:
            expected_pairs.add((lid, sid))
            case_of_record[lid] = case["case_id"]
            case_of_record[sid] = case["case_id"]
        for rid in case["duplicate_ids"] + case["unresolved_ids"]:
            case_of_record[rid] = case["case_id"]

    tp = engine_pairs & expected_pairs
    fp = engine_pairs - expected_pairs
    fn = expected_pairs - engine_pairs

    auto_pairs = {p for p in engine_pairs if pair_is_auto.get(p)}
    auto_tp = auto_pairs & expected_pairs
    auto_fp = auto_pairs - expected_pairs

    precision = len(tp) / len(engine_pairs) if engine_pairs else 0.0
    recall = len(tp) / len(expected_pairs) if expected_pairs else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    auto_precision = len(auto_tp) / len(auto_pairs) if auto_pairs else 0.0

    by_category: dict[str, dict[str, Any]] = {}
    case_results: list[dict[str, Any]] = []

    for case_id, case in cases.items():
        cat = case["category"]
        bucket = by_category.setdefault(
            cat,
            {"category": cat, "cases": 0, "correct": 0, "wrong_link": 0,
             "missed": 0, "duplicate_missed": 0, "escaped_review": 0,
             "auto": 0, "proposed": 0},
        )
        bucket["cases"] += 1

        expected = {tuple(p) for p in case["expected_links"]}
        touched = set(case["duplicate_ids"]) | set(case["unresolved_ids"])
        for lid, sid in expected:
            touched.add(lid)
            touched.add(sid)

        actual = {p for p in engine_pairs if p[0] in touched or p[1] in touched}

        wrong = actual - expected
        missing = expected - actual
        dup_missed = [d for d in case["duplicate_ids"] if d not in engine_duplicates]
        orphan_linked = [u for u in case["unresolved_ids"] if u in linked_records]

        if actual:
            mode = "auto" if all(pair_is_auto.get(p, False) for p in actual) else "proposed"
        else:
            mode = "none"

        escaped_review = bool(case.get("require_human")) and mode == "auto"

        if wrong or orphan_linked:
            verdict = "wrong_link"
        elif missing:
            verdict = "missed"
        elif dup_missed:
            verdict = "duplicate_missed"
        elif escaped_review:
            verdict = "escaped_review"
        else:
            verdict = "correct"
        bucket[verdict] = bucket.get(verdict, 0) + 1

        if mode != "none":
            bucket[mode] += 1

        case_results.append(
            {"case_id": case_id, "category": cat, "verdict": verdict, "mode": mode,
             "expected": sorted(expected), "actual": sorted(actual),
             "wrong": sorted(wrong), "missing": sorted(missing),
             "duplicate_missed": dup_missed, "orphan_incorrectly_linked": orphan_linked,
             "escaped_review": escaped_review}
        )

    for bucket in by_category.values():
        bucket["accuracy"] = round(bucket["correct"] / bucket["cases"], 4) if bucket["cases"] else 0.0

    total_cases = len(cases)
    total_correct = sum(b["correct"] for b in by_category.values())

    return {
        "validated_against": "synthetic ground truth (ground_truth.json)",
        "caveat": (
            "This accuracy figure exists only because the dataset is synthetic and we "
            "hold the answer key. Production reconciliation has no answer key - there, "
            "the honest metrics are the auto-resolve rate and the size of the exception "
            "queue."
        ),
        "cases_total": total_cases,
        "cases_correct": total_correct,
        "case_accuracy": round(total_correct / total_cases, 4) if total_cases else 0.0,
        "pairs_expected": len(expected_pairs),
        "pairs_proposed_by_engine": len(engine_pairs),
        "true_positives": len(tp),
        "false_positives": len(fp),
        "false_negatives": len(fn),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "auto_pairs": len(auto_pairs),
        "auto_true_positives": len(auto_tp),
        "auto_false_positives": len(auto_fp),
        "auto_precision": round(auto_precision, 4),
        "by_category": sorted(by_category.values(), key=lambda b: -b["cases"]),
        "false_positive_pairs": sorted(fp)[:50],
        "false_negative_pairs": sorted(fn)[:50],
        "case_results": case_results,
    }


def print_report(result: dict[str, Any]) -> None:
    print()
    print("  RECONCILIATION ACCURACY  (vs synthetic ground truth)")
    print("  " + "=" * 62)
    print("  cases                 %5d" % result["cases_total"])
    print("  cases fully correct   %5d   (%.2f%%)"
          % (result["cases_correct"], result["case_accuracy"] * 100))
    print()
    print("  pair precision        %6.2f%%   (%d TP / %d proposed)"
          % (result["precision"] * 100, result["true_positives"],
             result["pairs_proposed_by_engine"]))
    print("  pair recall           %6.2f%%   (%d TP / %d expected)"
          % (result["recall"] * 100, result["true_positives"], result["pairs_expected"]))
    print("  F1                    %6.2f%%" % (result["f1"] * 100))
    print()
    print("  AUTO-RESOLVED ONLY (no human, no LLM)")
    print("  auto precision        %6.2f%%   (%d TP / %d auto pairs, %d wrong)"
          % (result["auto_precision"] * 100, result["auto_true_positives"],
             result["auto_pairs"], result["auto_false_positives"]))
    print()
    print("  %-19s %5s %8s %6s %7s %8s %8s %9s"
          % ("category", "cases", "correct", "wrong", "missed", "dup-miss", "no-review",
             "accuracy"))
    print("  " + "-" * 76)
    for b in result["by_category"]:
        print("  %-19s %5d %8d %6d %7d %8d %8d %8.2f%%"
              % (b["category"], b["cases"], b["correct"], b["wrong_link"],
                 b["missed"], b["duplicate_missed"], b.get("escaped_review", 0),
                 b["accuracy"] * 100))
    print()
    if result["false_positive_pairs"]:
        print("  wrong links (first %d):" % min(10, len(result["false_positive_pairs"])))
        for lid, sid in result["false_positive_pairs"][:10]:
            print("    %s <-> %s" % (lid, sid))
        print()
    if result["false_negative_pairs"]:
        print("  missed links (first %d):" % min(10, len(result["false_negative_pairs"])))
        for lid, sid in result["false_negative_pairs"][:10]:
            print("    %s <-> %s" % (lid, sid))
        print()
