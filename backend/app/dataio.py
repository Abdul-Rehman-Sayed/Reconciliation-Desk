"""CSV loading and light validation for both sides of the reconciliation."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .adapters import detect as detect_adapter

LEDGER_COLUMNS = ["txn_id", "date", "amount", "counterparty", "payment_method",
                  "reference_number", "status"]
BANK_COLUMNS = ["stmt_id", "date", "amount", "reference_number", "narration", "type"]

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# Two bundled datasets ship with the repo. "standard" is the flaw mix the brief
# specifies. "stress" keeps every one of those categories and spends 12 points of
# the clean share on adversarial ones the thresholds were never designed around.
# Running both is the point: the auto-resolve rate drops on the harder data while
# precision holds, which is how a reconciliation system is supposed to degrade.
PROFILES = ("standard", "stress")


class CsvShapeError(ValueError):
    pass


def _frame_to_rows(df: pd.DataFrame, required: list[str], label: str) -> list[dict[str, Any]]:
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise CsvShapeError(
            "%s file is missing required column(s): %s. Expected: %s"
            % (label, ", ".join(missing), ", ".join(required))
        )
    df = df[required].copy()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    bad = int(df["amount"].isna().sum())
    if bad:
        raise CsvShapeError("%s file has %d row(s) with an unreadable amount." % (label, bad))
    df["date"] = pd.to_datetime(df["date"], errors="coerce", format="mixed")
    bad = int(df["date"].isna().sum())
    if bad:
        raise CsvShapeError("%s file has %d row(s) with an unreadable date." % (label, bad))
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    for col in required:
        if col not in ("amount", "date"):
            df[col] = df[col].fillna("").astype(str)
    return df.to_dict("records")


def read_ledger(source: str | Path | bytes) -> list[dict[str, Any]]:
    rows, _ = read_ledger_described(source)
    return rows


def read_ledger_described(
    source: str | Path | bytes,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Ledger rows, plus a note if an adapter had to translate them first.

    A settlement export from a real gateway does not have our column names, and
    telling someone "missing required column: txn_id" when they have handed us a
    perfectly good Razorpay recon report is a bad answer. Adapters are tried
    before the shape check, and what they did is reported back so the interface
    can say so rather than silently reinterpreting the file.
    """
    buf = io.BytesIO(source) if isinstance(source, bytes) else source
    frame = pd.read_csv(buf, dtype=str)
    frame.columns = [str(c).strip().lower() for c in frame.columns]

    adapter = detect_adapter(frame.columns)
    if adapter is not None:
        raw = frame.to_dict("records")
        described = adapter.describe(raw)
        mapped = adapter.to_ledger_rows(raw)
        return _frame_to_rows(pd.DataFrame(mapped), LEDGER_COLUMNS, "Ledger"), described

    return _frame_to_rows(frame, LEDGER_COLUMNS, "Ledger"), None


def read_bank(source: str | Path | bytes) -> list[dict[str, Any]]:
    buf = io.BytesIO(source) if isinstance(source, bytes) else source
    return _frame_to_rows(pd.read_csv(buf, dtype=str), BANK_COLUMNS, "Bank statement")


def profile_dir(profile: str) -> Path:
    if profile not in PROFILES:
        raise ValueError("Unknown dataset '%s'. Available: %s"
                         % (profile, ", ".join(PROFILES)))
    return DATA_DIR / profile


def load_bundled(profile: str = "standard") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    d = profile_dir(profile)
    return read_ledger(d / "ledger.csv"), read_bank(d / "bank_statement.csv")


def load_ground_truth(profile: str = "standard") -> dict[str, Any] | None:
    path = profile_dir(profile) / "ground_truth.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def describe_profiles() -> list[dict[str, Any]]:
    """What the start screen offers, read off the actual files on disk."""
    out = []
    for name in PROFILES:
        truth = load_ground_truth(name)
        if truth is None:
            continue
        out.append({
            "profile": name,
            "seed": truth["seed"],
            "cases": truth["case_count"],
            "ledger_rows": truth["ledger_rows"],
            "bank_rows": truth["bank_rows"],
            "categories": truth["cases_by_category"],
        })
    return out
