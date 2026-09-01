from __future__ import annotations

import csv
import io
from typing import Any


RULES: dict[str, dict[str, Any]] = {
    "exact_reference_amount_date": {
        "pass": "1 - exact",
        "title": "Reference, amount and date all agree",
        "asserts": "These are the same transaction.",
        "requires": [
            "Normalised reference numbers identical",
            "Amounts within the rounding tolerance",
            "Dates within the same-day window",
            "Both rows still unmatched when the rule fired",
        ],
        "auto": True,
    },
    "amount_rounding": {
        "pass": "3 - tolerant",
        "title": "Same reference, amount differs only by rounding",
        "asserts": "Same transaction; the difference is a rounding artefact.",
        "requires": [
            "Reference similarity at or above the auto-resolve threshold",
            "Amount inside the tolerant band",
            "Dates inside the settlement window",
        ],
        "auto": True,
    },
    "date_delay": {
        "pass": "3 - tolerant",
        "title": "Same reference and amount, settled later",
        "asserts": "Same transaction, delayed in settlement.",
        "requires": [
            "Reference similarity at or above the auto-resolve threshold",
            "Amount inside the tolerant band",
            "Date gap beyond same-day but inside the settlement window",
        ],
        "auto": True,
    },
    "fee_adjusted": {
        "pass": "3 - tolerant",
        "title": "Amount short by a recognisable gateway fee",
        "asserts": "Same transaction; the bank credited the amount net of fee.",
        "requires": [
            "Reference similarity at or above the auto-resolve threshold",
            "The gap fits a known fee rate, or an implied rate inside the plausible band",
            "Dates inside the settlement window",
        ],
        "auto": True,
    },
    "late_settlement": {
        "pass": "3 - tolerant",
        "title": "Same reference and amount, well outside the date window",
        "asserts": "Same transaction; the settlement took unusually long.",
        "requires": [
            "Reference identical after normalisation",
            "Amounts agree to the paisa",
            "That reference appears exactly once on each side - this is the "
            "condition that makes ignoring the date safe",
        ],
        "auto": True,
    },
    "refund_reversal": {
        "pass": "2 - reversals",
        "title": "Refund paired to the payment it cancels",
        "asserts": "These two net to zero against an earlier payment.",
        "requires": [
            "Both rows negative",
            "Absolute amounts agree",
            "One reference contains the other once separators are stripped",
            "Dates inside the refund window",
        ],
        "auto": True,
    },
    "composite_many_to_one": {
        "pass": "5 - composite",
        "title": "Several ledger rows settled as one bank line",
        "asserts": "Proposed only. These rows sum to the bank credit.",
        "requires": [
            "The counterparty was mined out of the bank narration",
            "Exactly one subset of that party's rows sums to the target",
            "Every component inside the composite date window",
        ],
        "auto": False,
    },
    "composite_one_to_many": {
        "pass": "5 - composite",
        "title": "One ledger payout left as several bank lines",
        "asserts": "Proposed only. These bank rows sum to the ledger payout.",
        "requires": [
            "The counterparty name appears in each bank narration",
            "Exactly one subset sums to the ledger amount",
            "Every component inside the composite date window",
        ],
        "auto": False,
    },
    "fuzzy_reference": {
        "pass": "6 - fuzzy",
        "title": "Damaged reference scored against amount and date",
        "asserts": "Proposed only. Best available candidate, not a proof.",
        "requires": [
            "Blended reference/amount/date score above the candidate floor",
            "Dates inside the fuzzy window",
            "No equally good rival, or the pair is flagged contested",
        ],
        "auto": False,
    },
}


KIND_RULES: dict[str, dict[str, Any]] = {
    "duplicate": {
        "pass": "4 - duplicates",
        "title": "Same-side repeat",
        "asserts": "This row repeats an earlier row on its own side of the book.",
        "requires": [
            "Identical normalised reference and amount to the anchor row",
            "Inside the duplicate date window",
            "The earliest row of the group is treated as the original",
        ],
        "auto": True,
    },
    "below_auto_threshold": {
        "pass": "policy",
        "title": "Matched, but below your auto-resolve floor",
        "asserts": "The pairing is proven; the confidence is under the floor you set.",
        "requires": [
            "A deterministic pass was willing to commit this match",
            "Its confidence fell below the configured auto-resolve floor",
        ],
        "auto": False,
    },
    "unmatched_ledger": {
        "pass": "remainder",
        "title": "Recorded but never settled",
        "asserts": "Nothing on the statement could be this ledger row.",
        "requires": ["Survived all six preceding passes"],
        "auto": False,
    },
    "unmatched_bank": {
        "pass": "remainder",
        "title": "Settled but unexplained",
        "asserts": "No ledger entry stands behind this bank row.",
        "requires": ["Survived all six preceding passes"],
        "auto": False,
    },
}


def _evidence_lines(evidence: dict[str, Any]) -> list[str]:
    ev = evidence or {}
    out: list[str] = []

    if "ref_similarity" in ev:
        sim = float(ev["ref_similarity"])
        out.append("Reference similarity %.0f%%%s"
                   % (sim, " - identical" if sim >= 100 else ""))
    if "amount_delta" in ev:
        delta = float(ev["amount_delta"])
        out.append("Amounts %s"
                   % ("agree to the paisa" if delta < 0.005 else "differ by INR %.2f" % delta))
    if "day_delta" in ev:
        days = int(ev["day_delta"])
        out.append("Dates %s" % ("the same day" if days == 0 else "%d day(s) apart" % days))
    if ev.get("fee_rate"):
        out.append("Implied fee %.2f%% (INR %.2f)%s"
                   % (float(ev["fee_rate"]) * 100, float(ev.get("fee_amount", 0) or 0),
                      " - a rate on file" if ev.get("known_rate") else " - not a rate on file"))
    if ev.get("unique_reference_both_sides"):
        out.append("That reference appears exactly once on each side")
    if "component_count" in ev:
        out.append("%d components totalling INR %.2f against INR %.2f, residual INR %.2f"
                   % (int(ev["component_count"]), float(ev.get("component_total", 0) or 0),
                      float(ev.get("bank_amount") or ev.get("ledger_amount") or 0),
                      float(ev.get("residual", 0) or 0)))
    if ev.get("basis"):
        out.append("Grouped by: %s" % str(ev["basis"]).replace("_", " "))
    if "blended_score" in ev:
        out.append("Blended score %.3f" % float(ev["blended_score"]))
    if ev.get("contested"):
        out.append("Contested: %d rival candidate(s) scored within the ambiguity margin"
                   % int(ev.get("rival_count", 0) or 0))
    if ev.get("amount_discrepancy"):
        out.append("Reference matches exactly but the bank settled INR %.2f less (%.1f%% short)"
                   % (float(ev.get("shortfall", 0) or 0), float(ev.get("shortfall_pct", 0) or 0)))
    if ev.get("nets_to_zero"):
        out.append("Nets to zero against link %s" % ev.get("reversal_of"))
    return out


def provenance(run: dict[str, Any], record_id: str) -> dict[str, Any] | None:
    lookup = {r["id"]: r for r in run["records"]["ledger"] + run["records"]["bank"]}
    record = lookup.get(record_id)
    if record is None:
        return None

    link = next(
        (l for l in run["links"]
         if record_id in l["ledger_ids"] or record_id in l["stmt_ids"]),
        None,
    )
    exception = next(
        (e for e in run["exceptions"]
         if record_id in e["ledger_ids"] or record_id in e["stmt_ids"]),
        None,
    )

    out: dict[str, Any] = {
        "record_id": record_id,
        "record": record,
        "outcome": "unresolved",
        "link": None,
        "exception": None,
        "rule": None,
        "why": [],
        "counterparts": [],
        "passes_that_declined": [],
    }

    if link is not None:
        rule = RULES.get(link["method"], {})
        counterpart_ids = (link["stmt_ids"] if record["side"] == "ledger"
                           else link["ledger_ids"])
        out.update({
            "outcome": "auto_resolved" if link["auto_resolved"] else "proposed",
            "link": link,
            "rule": {"method": link["method"], **rule},
            "why": _evidence_lines(link.get("evidence", {})),
            "counterparts": [lookup[i] for i in counterpart_ids if i in lookup],
        })

        order = ["exact", "refund", "tolerant", "duplicates", "composite", "fuzzy", "remainder"]
        if link["pass_name"] in order:
            out["passes_that_declined"] = order[: order.index(link["pass_name"])]

    if exception is not None:
        kind_rule = KIND_RULES.get(exception["kind"])
        out["exception"] = {
            "exception_id": exception["exception_id"],
            "kind": exception["kind"],
            "engine_note": exception["engine_note"],
            "engine_confidence": exception["engine_confidence"],
            "needs_llm": exception["needs_llm"],
            "status": exception["status"],
            "llm": exception.get("llm"),
            "decided_at": exception.get("decided_at"),
            "decided_note": exception.get("decided_note"),
        }
        if out["rule"] is None and kind_rule:
            out["outcome"] = ("flagged" if exception["kind"] == "duplicate" else "unresolved")
            out["rule"] = {"method": exception["kind"], **kind_rule}
            out["why"] = _evidence_lines(exception.get("evidence", {}))
            if exception["kind"] in ("unmatched_ledger", "unmatched_bank"):
                out["passes_that_declined"] = ["exact", "refund", "tolerant", "duplicates",
                                               "composite", "fuzzy"]

    return out


AUDIT_COLUMNS = [
    "run_id", "dataset", "event", "at", "record_ids", "ledger_ids", "bank_ids",
    "amount", "pass", "method", "rule_asserts", "confidence", "auto_resolved",
    "exception_id", "exception_kind", "llm_source", "llm_model", "llm_category",
    "llm_confidence", "llm_suggested_action", "human_action", "human_note",
]


def audit_rows(run: dict[str, Any]) -> list[dict[str, Any]]:
    lookup = {r["id"]: r for r in run["records"]["ledger"] + run["records"]["bank"]}
    dataset = run.get("dataset_profile", "")
    run_id = run.get("run_id", "")
    created = run.get("created_at", "")
    rows: list[dict[str, Any]] = []

    def amount_of(ids: list[str]) -> float:
        return round(sum(abs(float(lookup[i]["amount"])) for i in ids if i in lookup), 2)

    exception_by_link = {e["link_id"]: e for e in run["exceptions"] if e.get("link_id")}

    for link in run["links"]:
        rule = RULES.get(link["method"], {})
        exc = exception_by_link.get(link["link_id"])
        rows.append({
            "run_id": run_id,
            "dataset": dataset,
            "event": "auto_resolved" if link["auto_resolved"] else "proposed",
            "at": created,
            "record_ids": " ".join(link["ledger_ids"] + link["stmt_ids"]),
            "ledger_ids": " ".join(link["ledger_ids"]),
            "bank_ids": " ".join(link["stmt_ids"]),
            "amount": amount_of(link["ledger_ids"]) or amount_of(link["stmt_ids"]),
            "pass": rule.get("pass", link["pass_name"]),
            "method": link["method"],
            "rule_asserts": rule.get("asserts", ""),
            "confidence": link["confidence"],
            "auto_resolved": link["auto_resolved"],
            "exception_id": exc["exception_id"] if exc else "",
            "exception_kind": exc["kind"] if exc else "",
            "llm_source": (exc.get("llm") or {}).get("source", "") if exc else "",
            "llm_model": (exc.get("llm") or {}).get("model", "") if exc else "",
            "llm_category": (exc.get("llm") or {}).get("category", "") if exc else "",
            "llm_confidence": (exc.get("llm") or {}).get("confidence", "") if exc else "",
            "llm_suggested_action": (exc.get("llm") or {}).get("suggested_action", "") if exc else "",
            "human_action": "",
            "human_note": "",
        })

    for exc in run["exceptions"]:
        if exc.get("link_id"):
            continue
        kind_rule = KIND_RULES.get(exc["kind"], {})
        ids = exc["ledger_ids"] + exc["stmt_ids"]
        llm_block = exc.get("llm") or {}
        rows.append({
            "run_id": run_id,
            "dataset": dataset,
            "event": "exception",
            "at": created,
            "record_ids": " ".join(ids),
            "ledger_ids": " ".join(exc["ledger_ids"]),
            "bank_ids": " ".join(exc["stmt_ids"]),
            "amount": amount_of(ids),
            "pass": kind_rule.get("pass", ""),
            "method": exc["kind"],
            "rule_asserts": kind_rule.get("asserts", ""),
            "confidence": exc["engine_confidence"],
            "auto_resolved": False,
            "exception_id": exc["exception_id"],
            "exception_kind": exc["kind"],
            "llm_source": llm_block.get("source", ""),
            "llm_model": llm_block.get("model", ""),
            "llm_category": llm_block.get("category", ""),
            "llm_confidence": llm_block.get("confidence", ""),
            "llm_suggested_action": llm_block.get("suggested_action", ""),
            "human_action": "",
            "human_note": "",
        })

    for event in run.get("audit_events", []):
        exc = next((e for e in run["exceptions"]
                    if e["exception_id"] == event.get("exception_id")), None)
        ids = (exc["ledger_ids"] + exc["stmt_ids"]) if exc else []
        llm_block = (exc.get("llm") or {}) if exc else {}
        rows.append({
            "run_id": run_id,
            "dataset": dataset,
            "event": "human_action",
            "at": event.get("at", ""),
            "record_ids": " ".join(ids),
            "ledger_ids": " ".join(exc["ledger_ids"]) if exc else "",
            "bank_ids": " ".join(exc["stmt_ids"]) if exc else "",
            "amount": amount_of(ids),
            "pass": "",
            "method": "",
            "rule_asserts": "",
            "confidence": "",
            "auto_resolved": False,
            "exception_id": event.get("exception_id", ""),
            "exception_kind": exc["kind"] if exc else "",
            "llm_source": llm_block.get("source", ""),
            "llm_model": llm_block.get("model", ""),
            "llm_category": llm_block.get("category", ""),
            "llm_confidence": llm_block.get("confidence", ""),
            "llm_suggested_action": llm_block.get("suggested_action", ""),
            "human_action": event.get("action", ""),
            "human_note": event.get("note") or "",
        })

    return rows


def audit_csv(run: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=AUDIT_COLUMNS, extrasaction="ignore",
                            lineterminator="\n")
    writer.writeheader()
    for row in audit_rows(run):
        writer.writerow(row)
    return buf.getvalue()


def audit_summary(run: dict[str, Any]) -> dict[str, Any]:
    rows = audit_rows(run)
    by_event: dict[str, int] = {}
    for r in rows:
        by_event[r["event"]] = by_event.get(r["event"], 0) + 1
    return {
        "rows": len(rows),
        "by_event": by_event,
        "human_actions": len(run.get("audit_events", [])),
        "columns": AUDIT_COLUMNS,
    }
