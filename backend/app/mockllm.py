"""
Rule-based stand-in for the Groq call.

Switched on with USE_MOCK_LLM=true. It exists for one reason: the exception
handler is the only part of this system that costs anything, and the interface
around it needed dozens of iterations. Iterating against this costs nothing.

It is a stand-in, not a simulation. Every verdict it produces is stamped
source="mock" and the interface labels it as such, because a templated sentence
sitting in a field headed "what the model thinks" without that stamp would be
the most dishonest thing in this repo.

What it does share with the real call: it reads the same compact payload,
returns the same validated shape, and lands on the same category on the bundled
data. That is what makes it useful for building screens against - the UI under
test sees realistic content - and it is also, usefully, the floor the real model
has to clear. If Groq cannot beat two hundred lines of if/else at this, the LLM
layer is not earning its place in the architecture.
"""

from __future__ import annotations

from typing import Any

MODEL_NAME = "rule-based-mock"


def _money(value: float) -> str:
    return "INR " + format(round(float(value or 0), 2), ",.2f")


def _first(records: Any) -> dict[str, Any]:
    return records[0] if records else {}


def _verdict(category: str, explanation: str, confidence: float, action: str) -> dict[str, Any]:
    return {
        "category": category,
        "explanation": explanation,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "suggested_action": action,
        "source": "mock",
        "model": MODEL_NAME,
    }


def _composite(payload: dict[str, Any]) -> dict[str, Any]:
    ev = payload.get("evidence") or {}
    n = int(ev.get("component_count", 0) or 0)
    total = float(ev.get("component_total", 0) or 0)
    residual = abs(float(ev.get("residual", 0) or 0))
    party = str(ev.get("counterparty") or "the same counterparty")
    span = ev.get("day_span", 0)

    if ev.get("direction") == "one_ledger_to_many_bank":
        text = (
            "One ledger payout of %s to %s left the account as %d separate bank lines "
            "within %s days. The parts add back to the whole."
            % (_money(ev.get("ledger_amount", total)), party, n, span)
        )
    else:
        text = (
            "%d ledger entries for %s add up to %s, which is what the bank settled in a "
            "single line %s days later." % (n, party, _money(total), span)
        )

    if ev.get("fee_rate"):
        text += " The %s difference is consistent with a %.2f%% gateway fee." % (
            _money(ev.get("fee_amount", residual)),
            float(ev["fee_rate"]) * 100,
        )
        return _verdict("split_payment", text, 0.74, "approve")
    if residual <= 2.0:
        return _verdict("split_payment", text + " The totals agree to the rupee.", 0.76, "approve")
    text += " The two sides are %s apart, which nothing here explains." % _money(residual)
    return _verdict("split_payment", text, 0.55, "investigate")


def _fuzzy(payload: dict[str, Any]) -> dict[str, Any]:
    ev = payload.get("evidence") or {}
    lref = ev.get("ledger_ref") or "the ledger reference"
    bref = ev.get("bank_ref") or "the statement reference"
    sim = float(ev.get("ref_similarity", 0) or 0)
    d_amt = float(ev.get("amount_delta", 0) or 0)
    d_day = int(ev.get("day_delta", 0) or 0)

    if ev.get("contested"):
        rivals = int(ev.get("rival_count", 1) or 1)
        return _verdict(
            "reference_mismatch",
            "Reference %s fits %d other row on this statement as well as it fits this one, "
            "and the amounts do not separate them. Nobody should be confident here."
            % (bref, rivals),
            0.35,
            "investigate",
        )

    if ev.get("amount_discrepancy"):
        return _verdict(
            "other",
            "Reference %s matches the statement exactly, but the bank settled %s less than "
            "the ledger expected, %.1f%% short, which no fee schedule on file accounts for. "
            "That is a shortfall, not a matching problem."
            % (lref, _money(ev.get("shortfall")), float(ev.get("shortfall_pct", 0) or 0)),
            0.62,
            "investigate",
        )

    if ev.get("fee_rate"):
        return _verdict(
            "fee_adjustment",
            "%s and %s are the same reference with %s deducted, a %.2f%% gateway fee. The "
            "pairing holds and the gap is explained."
            % (lref, bref, _money(ev.get("fee_amount", d_amt)), float(ev["fee_rate"]) * 100),
            0.71,
            "approve",
        )

    if sim >= 99 and d_day > 2:
        return _verdict(
            "date_delay",
            "Same reference %s on both sides for the same amount, settled %d days after the "
            "ledger recorded it. A delay, not a discrepancy." % (lref, d_day),
            0.70,
            "approve",
        )

    if sim >= 85 and d_amt < 1.0:
        return _verdict(
            "reference_mismatch",
            "%s on the ledger against %s on the statement is a %.0f%% string match and the "
            "amounts agree to the paisa. This reads as a typo in one of the two references."
            % (lref, bref, sim),
            0.68,
            "approve",
        )

    return _verdict(
        "reference_mismatch",
        "%s and %s are only a %.0f%% match and the amounts are %s apart. Plausible, but not "
        "provable from what is in front of us." % (lref, bref, sim, _money(d_amt)),
        0.48,
        "investigate",
    )


def _orphan(payload: dict[str, Any], side: str) -> dict[str, Any]:
    ev = payload.get("evidence") or {}
    nearest = ev.get("nearest_on_other_side") or {}
    rec = _first(payload.get("ledger_records") if side == "ledger" else payload.get("bank_records"))
    amount = _money(rec.get("amount", 0))
    when = rec.get("date", "that date")

    if side == "ledger":
        text = (
            "The ledger records %s from %s on %s, and six passes found nothing on the "
            "statement that could be it."
            % (amount, rec.get("counterparty") or "a counterparty", when)
        )
        category = "orphan_ledger"
    else:
        text = (
            "The statement shows %s on %s narrated '%s', with no ledger entry behind it."
            % (amount, when, str(rec.get("narration", ""))[:60])
        )
        category = "orphan_bank"

    near = nearest.get("record")
    if near:
        text += (
            " The closest row on the other side is %s at %s, %s and %d days apart, which is "
            "close enough to look at and not close enough to call a match."
            % (near.get("id"), _money(near.get("amount")), _money(nearest.get("amount_delta")),
               int(nearest.get("day_delta", 0) or 0))
        )
    else:
        text += " There is nothing on the other side that resembles it at all."

    return _verdict(category, text, 0.58, "investigate")


def _duplicate(payload: dict[str, Any]) -> dict[str, Any]:
    ev = payload.get("evidence") or {}
    return _verdict(
        "duplicate",
        "Same reference %s and same amount as %s on the same side of the book. A repeat "
        "entry, almost always a payment webhook that fired twice."
        % (ev.get("reference_number", ""), ev.get("duplicate_of") or "an earlier row"),
        0.80,
        "reject",
    )


_HANDLERS = {
    "composite_candidate": _composite,
    "fuzzy_candidate": _fuzzy,
    "duplicate": _duplicate,
}


def classify(payload: dict[str, Any]) -> dict[str, Any]:
    """One compact exception payload in, one validated verdict out. Never raises."""
    kind = str(payload.get("engine_finding", ""))
    if kind == "unmatched_ledger":
        return _orphan(payload, "ledger")
    if kind == "unmatched_bank":
        return _orphan(payload, "bank")
    handler = _HANDLERS.get(kind)
    if handler is not None:
        return handler(payload)
    return _verdict(
        "other",
        "The engine raised this as '%s' and the rule-based stand-in has no template for it. "
        "Read the evidence directly." % (kind or "unknown"),
        0.30,
        "investigate",
    )
