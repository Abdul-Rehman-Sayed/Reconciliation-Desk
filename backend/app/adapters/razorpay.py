"""
Razorpay settlement recon report -> the engine's ledger shape.

Field list verified against Razorpay's current docs for the combined recon
report, GET /v1/settlements/recon/combined?year=yyyy&month=mm (checked August
2026, because this schema does move):

    entity_id, type, debit, credit, amount, currency, fee, tax, on_hold,
    settled, created_at, settled_at, settlement_id, description, notes,
    payment_id, settlement_utr, order_id, order_receipt, method, card_network,
    card_issuer, card_type, dispute_id

Three things about that schema that this adapter has to handle, and that are
easy to get wrong:

**Amounts are in paise.** Every money field - amount, fee, tax, debit, credit -
is an integer in currency subunits. A recon tool that treats them as rupees is
out by a factor of 100 and will match nothing.

**Timestamps are Unix integers**, not dates, and `settled_at` is the one that
matters for reconciliation. `created_at` is when the payment happened, which is
often days earlier - matching on it manufactures a settlement delay that is not
real.

**`type` decides the sign.** payment and transfer are money in; refund and
adjustment are money out. The engine's refund pass keys off negative amounts, so
getting this wrong hides every reversal.

-------------------------------------------------------------------------------
The UTR problem, which is the actual domain detail
-------------------------------------------------------------------------------
The obvious reconciliation key is `settlement_utr`, and it does not work.

Razorpay assigns a UTR to the settlement it initiates. The money then travels
through the banking system, and the correspondent bank that finally credits the
merchant account issues its *own* reference, which is what lands in the bank
statement narration. The two are different strings for the same movement of
money. Joining Razorpay's `settlement_utr` against the statement's narration
finds nothing, and the natural next move - loosening the match until something
sticks - is how a reconciliation system starts producing confident wrong
answers.

What actually works is two-level:

    level 1   the bank credit  <->  the settlement batch, matched on amount and
              settlement date. One bank line, one settlement_id. This is the
              join the bank statement can actually support.
    level 2   each transaction <->  its settlement batch, matched on
              settlement_id within the report, then identified by order_id or
              payment_id. This join never touches the bank statement at all.

So it is batch-to-bank, then transaction-to-batch. Not a flat 1:1 join, which is
what almost every naive implementation assumes.

This adapter does the mapping and exposes the batch structure through
`settlement_batches()`. The matching engine still runs its normal passes over
the result - rearchitecting the engine around two-level settlement is a real
piece of work and is written up as future work rather than half-done here. What
this file guarantees is that the data arrives in the right shape, with the right
signs, in rupees, on the right date, and that the batch grouping is available to
whatever consumes it next.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

NAME = "razorpay_settlement_recon"
LABEL = "Razorpay settlement recon report"

# Enough of the schema to be sure, without demanding optional columns.
REQUIRED = {"entity_id", "type", "amount"}
STRONG_HINTS = {"settlement_id", "settlement_utr", "settled_at", "order_id"}

# payment and transfer credit the merchant. refund and adjustment debit them.
# An adjustment can go either way in principle; the debit/credit columns are
# authoritative when present, and this is only the fallback.
MONEY_IN = {"payment", "transfer"}
MONEY_OUT = {"refund", "adjustment"}


def matches(columns) -> bool:
    """True when this looks like a Razorpay recon export rather than our own CSV."""
    cols = {str(c).strip().lower() for c in columns}
    return REQUIRED.issubset(cols) and bool(STRONG_HINTS & cols)


def _paise(value: Any) -> float:
    """Razorpay money fields are integer subunits. Rupees are what we reconcile in."""
    if value in (None, "", "null"):
        return 0.0
    try:
        return round(float(value) / 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _date(value: Any) -> str:
    """Unix seconds -> YYYY-MM-DD. Already-formatted dates pass through."""
    if value in (None, "", "null"):
        return ""
    text = str(value).strip()
    if "-" in text and len(text) >= 10:
        return text[:10]
    try:
        return datetime.fromtimestamp(int(float(text)), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _signed_amount(row: dict[str, Any]) -> float:
    """Money in is positive, money out negative.

    debit/credit are authoritative when either carries a value - they are what
    the report itself asserts about direction. `type` is the fallback.
    """
    debit = _paise(row.get("debit"))
    credit = _paise(row.get("credit"))
    if credit:
        return credit
    if debit:
        return -debit

    gross = _paise(row.get("amount"))
    kind = str(row.get("type", "")).strip().lower()
    if kind in MONEY_OUT:
        return -abs(gross)
    return abs(gross)


def to_ledger_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recon report rows -> the ledger shape the engine reads.

    Note which field becomes the reference: `order_id` where there is one,
    falling back to `entity_id`. Deliberately not `settlement_utr` - see the
    module docstring. The UTR is carried through in the counterparty text so it
    is visible to a human without ever being used as a join key.
    """
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(rows, start=1):
        row = {str(k).strip().lower(): v for k, v in raw.items()}
        entity = str(row.get("entity_id", "") or "").strip()
        order = str(row.get("order_id", "") or "").strip()
        settlement = str(row.get("settlement_id", "") or "").strip()
        kind = str(row.get("type", "") or "").strip().lower()

        amount = _signed_amount(row)
        fee = _paise(row.get("fee"))
        tax = _paise(row.get("tax"))

        # settled_at is the date the money reached the bank, which is the date
        # the bank statement will show. created_at is when the payment was
        # taken, which is a different and usually earlier day.
        when = _date(row.get("settled_at")) or _date(row.get("created_at"))

        descriptor = " ".join(
            part for part in (
                str(row.get("description", "") or "").strip(),
                ("via " + str(row.get("method")).strip()) if row.get("method") else "",
                ("settlement " + settlement) if settlement else "",
                ("utr " + str(row.get("settlement_utr")).strip())
                if row.get("settlement_utr") else "",
            ) if part
        )

        out.append({
            "txn_id": entity or ("RZP%06d" % i),
            "date": when,
            "amount": amount,
            "counterparty": descriptor[:120] or "Razorpay settlement",
            "payment_method": str(row.get("method", "") or "").strip() or kind,
            "reference_number": order or entity,
            "status": "settled" if str(row.get("settled", "")).strip().lower() in
                      ("true", "1", "yes") else (kind or "captured"),
            # Carried for the batch view; the engine ignores unknown keys.
            "_settlement_id": settlement,
            "_settlement_utr": str(row.get("settlement_utr", "") or "").strip(),
            "_type": kind,
            "_fee": fee,
            "_tax": tax,
            "_payment_id": str(row.get("payment_id", "") or "").strip(),
        })
    return out


def settlement_batches(ledger_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group mapped rows by settlement_id - level one of the two-level match.

    This is the shape a batch-to-bank match needs: one row per settlement, with
    the net amount that should appear as a single line on the bank statement,
    and the transaction ids that make it up.
    """
    batches: dict[str, dict[str, Any]] = {}
    for row in ledger_rows:
        key = row.get("_settlement_id") or ""
        if not key:
            continue
        batch = batches.setdefault(key, {
            "settlement_id": key,
            "settlement_utr": row.get("_settlement_utr", ""),
            "settled_on": row.get("date", ""),
            "transaction_count": 0,
            "gross": 0.0,
            "fees": 0.0,
            "tax": 0.0,
            "net": 0.0,
            "by_type": {},
            "transaction_ids": [],
        })
        batch["transaction_count"] += 1
        batch["gross"] += abs(float(row.get("amount", 0) or 0))
        batch["fees"] += float(row.get("_fee", 0) or 0)
        batch["tax"] += float(row.get("_tax", 0) or 0)
        batch["net"] += float(row.get("amount", 0) or 0)
        kind = row.get("_type") or "unknown"
        batch["by_type"][kind] = batch["by_type"].get(kind, 0) + 1
        batch["transaction_ids"].append(row["txn_id"])

    for batch in batches.values():
        for key in ("gross", "fees", "tax", "net"):
            batch[key] = round(batch[key], 2)

    return sorted(batches.values(), key=lambda b: (b["settled_on"], b["settlement_id"]))


def describe(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """What the adapter found, for the upload screen to show back to the user."""
    mapped = to_ledger_rows(rows)
    batches = settlement_batches(mapped)
    by_type: dict[str, int] = {}
    for row in mapped:
        kind = row.get("_type") or "unknown"
        by_type[kind] = by_type.get(kind, 0) + 1
    return {
        "adapter": NAME,
        "label": LABEL,
        "rows": len(mapped),
        "by_type": by_type,
        "settlement_batches": len(batches),
        "date_range": [
            min((r["date"] for r in mapped if r["date"]), default=""),
            max((r["date"] for r in mapped if r["date"]), default=""),
        ],
        "reference_field": "order_id, falling back to entity_id",
        "utr_note": (
            "settlement_utr is carried through for display but is never used as a "
            "join key: the bank statement carries the correspondent bank's own "
            "reference, not Razorpay's."
        ),
    }
