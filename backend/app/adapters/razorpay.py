from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

NAME = "razorpay_settlement_recon"
LABEL = "Razorpay settlement recon report"


REQUIRED = {"entity_id", "type", "amount"}
STRONG_HINTS = {"settlement_id", "settlement_utr", "settled_at", "order_id"}


MONEY_IN = {"payment", "transfer"}
MONEY_OUT = {"refund", "adjustment"}


def matches(columns) -> bool:
    cols = {str(c).strip().lower() for c in columns}
    return REQUIRED.issubset(cols) and bool(STRONG_HINTS & cols)


def _paise(value: Any) -> float:
    if value in (None, "", "null"):
        return 0.0
    try:
        return round(float(value) / 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _date(value: Any) -> str:
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

            "_settlement_id": settlement,
            "_settlement_utr": str(row.get("settlement_utr", "") or "").strip(),
            "_type": kind,
            "_fee": fee,
            "_tax": tax,
            "_payment_id": str(row.get("payment_id", "") or "").strip(),
        })
    return out


def settlement_batches(ledger_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
