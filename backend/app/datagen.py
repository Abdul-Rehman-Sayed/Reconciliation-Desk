"""
Synthetic reconciliation dataset generator.

Produces three artefacts from one set of ground-truth "cases":

    ledger.csv          the merchant's internal record
    bank_statement.csv  the bank / gateway settlement record
    ground_truth.json   what actually happened  (the engine NEVER reads this)

Every flaw in the data is injected deliberately and recorded in ground_truth.json,
which is what makes the accuracy number in the demo defensible rather than asserted.
"""

from __future__ import annotations

import csv
import json
import random
import string
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from faker import Faker

# --------------------------------------------------------------------------
# Flaw mix. Shares are of the total case count.
# --------------------------------------------------------------------------
FLAW_MIX: dict[str, float] = {
    "clean_exact": 0.60,
    "date_shift": 0.15,
    "fee_deducted": 0.08,
    "duplicate_ledger": 0.05,
    "split_batch": 0.04,
    "reference_typo": 0.04,
    "refund_reversal": 0.02,
    "orphan_bank": 0.01,
    "orphan_ledger": 0.01,
}

# The stress mix keeps every category above and spends 12 points of the clean
# share on four adversarial ones. These exist because a matching engine scored
# only against textbook versions of its own flaw categories will score suspiciously
# well. Each of these is a failure mode the thresholds were not designed around.
STRESS_MIX: dict[str, float] = {
    "clean_exact": 0.48,
    "date_shift": 0.15,
    "fee_deducted": 0.08,
    "duplicate_ledger": 0.05,
    "split_batch": 0.04,
    "reference_typo": 0.04,
    "refund_reversal": 0.02,
    "orphan_bank": 0.01,
    "orphan_ledger": 0.01,
    "ambiguous_decoy": 0.03,
    "partial_settlement": 0.03,
    "narration_only_ref": 0.03,
    "late_settlement": 0.03,
}

# Gateway fee rates actually seen in Indian PG settlements:
# base rate + 18% GST on the fee.  2% -> 2.36%, 1% -> 1.18%, 2.5% -> 2.95%.
FEE_RATES = (0.0118, 0.0236, 0.0295)

PAYMENT_METHODS = ("UPI", "CARD", "NETBANKING", "IMPS", "NEFT", "WALLET")
BANK_CODES = ("HDFC", "ICIC", "SBIN", "AXIS", "KKBK", "YESB")

WINDOW_START = date(2026, 7, 1)
WINDOW_DAYS = 46

REF_ALPHABET = string.ascii_uppercase + string.ascii_lowercase + string.digits


# --------------------------------------------------------------------------
# Row containers
# --------------------------------------------------------------------------
@dataclass
class LedgerRow:
    txn_id: str
    date: str
    amount: float
    counterparty: str
    payment_method: str
    reference_number: str
    status: str


@dataclass
class BankRow:
    stmt_id: str
    date: str
    amount: float
    reference_number: str
    narration: str
    type: str


@dataclass
class Case:
    """One ground-truth event, plus how it was distorted across the two files."""

    case_id: str
    category: str
    expected_links: list[list[str]] = field(default_factory=list)
    duplicate_ids: list[str] = field(default_factory=list)
    unresolved_ids: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    # True when linking the records is not enough: the discrepancy is material and
    # the engine is expected to route it to a human rather than auto-resolve it.
    require_human: bool = False


class Generator:
    def __init__(self, n_cases: int = 400, seed: int = 20260822, stress: bool = False):
        self.n = n_cases
        self.seed = seed
        self.stress = stress
        self.mix = STRESS_MIX if stress else FLAW_MIX
        self.rng = random.Random(seed)
        self.fake = Faker("en_IN")
        Faker.seed(seed)

        self.ledger: list[LedgerRow] = []
        self.bank: list[BankRow] = []
        self.cases: list[Case] = []

        self._lseq = 0
        self._sseq = 0
        self._cseq = 0
        self._used_refs: set[str] = set()

    # -- primitives --------------------------------------------------------
    def _ledger_id(self) -> str:
        self._lseq += 1
        return "L%05d" % self._lseq

    def _stmt_id(self) -> str:
        self._sseq += 1
        return "S%05d" % self._sseq

    def _case_id(self) -> str:
        self._cseq += 1
        return "C%04d" % self._cseq

    def _ref(self, prefix: str = "pay") -> str:
        while True:
            body = "".join(self.rng.choice(REF_ALPHABET) for _ in range(14))
            ref = prefix + "_" + body
            if ref not in self._used_refs:
                self._used_refs.add(ref)
                return ref

    def _amount(self) -> float:
        """Right-skewed amounts: lots of small payments, a few large ones."""
        base = self.rng.lognormvariate(8.6, 1.05)
        return round(min(max(base, 249.0), 486000.0), 2)

    def _date(self) -> date:
        return WINDOW_START + timedelta(days=self.rng.randrange(WINDOW_DAYS))

    def _counterparty(self) -> str:
        return self.fake.company().upper()

    def _fee(self, amount: float) -> tuple[float, float]:
        rate = self.rng.choice(FEE_RATES)
        return round(amount * rate, 2), rate

    def _short(self, party: str, n: int = 18) -> str:
        return party.replace(" ", "")[:n]

    def _narration(self, ref: str, party: str, method: str) -> str:
        short = self._short(party)
        bank = self.rng.choice(BANK_CODES)
        return self.rng.choice(
            [
                "RAZORPAY SETTLEMENT/" + ref + "/" + short,
                "UPI/" + ref + "/" + short + "/PAYMENT",
                "NEFT-" + bank + "-" + ref + "-" + short,
                "IMPS/" + ref + "/" + short,
                "MERCHANT CR " + ref + " " + short + " " + method,
            ]
        )

    def _emit_ledger(
        self,
        d: date,
        amount: float,
        party: str,
        ref: str,
        method: str | None = None,
        status: str = "captured",
    ) -> LedgerRow:
        row = LedgerRow(
            txn_id=self._ledger_id(),
            date=d.isoformat(),
            amount=round(amount, 2),
            counterparty=party,
            payment_method=method or self.rng.choice(PAYMENT_METHODS),
            reference_number=ref,
            status=status,
        )
        self.ledger.append(row)
        return row

    def _emit_bank(
        self, d: date, amount: float, ref: str, narration: str, kind: str = "CREDIT"
    ) -> BankRow:
        row = BankRow(
            stmt_id=self._stmt_id(),
            date=d.isoformat(),
            amount=round(amount, 2),
            reference_number=ref,
            narration=narration,
            type=kind,
        )
        self.bank.append(row)
        return row

    # -- reference corruption ---------------------------------------------
    def _typo(self, ref: str) -> tuple[str, str]:
        """Corrupt a reference the way a human or a legacy system would.

        Returns (corrupted_ref, kind).  Kinds are split deliberately between
        mild damage (still auto-resolvable by the tolerant pass) and heavy
        damage (has to survive to the fuzzy pass and get an LLM opinion).
        """
        prefix, body = ref.split("_", 1)
        kind = self.rng.choice(
            ["transpose", "substitute", "drop_char", "truncate", "truncate", "prefix_lost"]
        )
        chars = list(body)
        if kind == "transpose":
            i = self.rng.randrange(len(chars) - 1)
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
            return prefix + "_" + "".join(chars), kind
        if kind == "substitute":
            i = self.rng.randrange(len(chars))
            swap = {"0": "O", "O": "0", "1": "l", "l": "1", "5": "S", "S": "5", "8": "B", "B": "8"}
            chars[i] = swap.get(chars[i], self.rng.choice(REF_ALPHABET))
            return prefix + "_" + "".join(chars), kind
        if kind == "drop_char":
            i = self.rng.randrange(len(chars))
            del chars[i]
            return prefix + "_" + "".join(chars), kind
        if kind == "truncate":
            keep = self.rng.randint(7, 9)
            return prefix + "_" + "".join(chars[:keep]), kind
        # prefix_lost: the bank drops the pay_ prefix and keeps a partial body
        return "".join(chars[: self.rng.randint(9, 12)]), kind

    # -- case builders -----------------------------------------------------
    def case_clean_exact(self) -> Case:
        d, amt, party, ref = self._date(), self._amount(), self._counterparty(), self._ref()
        m = self.rng.choice(PAYMENT_METHODS)
        lr = self._emit_ledger(d, amt, party, ref, m)
        # Even a "clean" match can land a day later; both sit inside pass 1's window.
        bd = d + timedelta(days=self.rng.choice([0, 0, 0, 1]))
        br = self._emit_bank(bd, amt, ref, self._narration(ref, party, m))
        return Case(
            self._case_id(),
            "clean_exact",
            [[lr.txn_id, br.stmt_id]],
            detail={"day_shift": (bd - d).days},
        )

    def case_date_shift(self) -> Case:
        d, amt, party, ref = self._date(), self._amount(), self._counterparty(), self._ref()
        m = self.rng.choice(PAYMENT_METHODS)
        shift = self.rng.choice([1, 2, 2, 3, 3])
        lr = self._emit_ledger(d, amt, party, ref, m)
        br = self._emit_bank(d + timedelta(days=shift), amt, ref, self._narration(ref, party, m))
        return Case(
            self._case_id(), "date_shift", [[lr.txn_id, br.stmt_id]], detail={"day_shift": shift}
        )

    def case_fee_deducted(self) -> Case:
        d, amt, party, ref = self._date(), self._amount(), self._counterparty(), self._ref()
        m = self.rng.choice(PAYMENT_METHODS)
        fee, rate = self._fee(amt)
        lr = self._emit_ledger(d, amt, party, ref, m)
        bd = d + timedelta(days=self.rng.choice([0, 1, 1, 2]))
        br = self._emit_bank(bd, amt - fee, ref, self._narration(ref, party, m))
        return Case(
            self._case_id(),
            "fee_deducted",
            [[lr.txn_id, br.stmt_id]],
            detail={
                "gross": round(amt, 2),
                "fee": fee,
                "rate": rate,
                "net": round(amt - fee, 2),
            },
        )

    def case_duplicate_ledger(self) -> Case:
        """Webhook fired twice: two ledger rows, one real bank credit."""
        d, amt, party, ref = self._date(), self._amount(), self._counterparty(), self._ref()
        m = self.rng.choice(PAYMENT_METHODS)
        first = self._emit_ledger(d, amt, party, ref, m)
        dup = self._emit_ledger(d + timedelta(days=self.rng.choice([0, 0, 1])), amt, party, ref, m)
        br = self._emit_bank(
            d + timedelta(days=self.rng.choice([0, 1])), amt, ref, self._narration(ref, party, m)
        )
        return Case(
            self._case_id(),
            "duplicate_ledger",
            [[first.txn_id, br.stmt_id]],
            duplicate_ids=[dup.txn_id],
            detail={"kept": first.txn_id, "duplicate": dup.txn_id},
        )

    def case_split_batch(self) -> Case:
        """N ledger rows settle as one bank credit, or one payout leaves as N debits."""
        party = self._counterparty()
        reverse = self.rng.random() < 0.25
        d = self._date()

        if not reverse:
            n = self.rng.randint(2, 4)
            rows: list[LedgerRow] = []
            for _ in range(n):
                rows.append(
                    self._emit_ledger(
                        d + timedelta(days=self.rng.choice([0, 0, 1])),
                        self._amount(),
                        party,
                        self._ref(),
                    )
                )
            gross = sum(r.amount for r in rows)
            charged_fee = self.rng.random() < 0.5
            fee, rate = self._fee(gross)
            net = gross - fee if charged_fee else gross
            setl = self._ref("setl")
            last = max(date.fromisoformat(r.date) for r in rows)
            br = self._emit_bank(
                last + timedelta(days=1),
                net,
                setl,
                "RAZORPAY SETTLEMENT "
                + setl
                + " BATCH OF "
                + str(n)
                + " "
                + self._short(party, 14),
            )
            return Case(
                self._case_id(),
                "split_batch",
                [[r.txn_id, br.stmt_id] for r in rows],
                detail={
                    "direction": "many_ledger_to_one_bank",
                    "n": n,
                    "gross": round(gross, 2),
                    "fee": fee if charged_fee else 0.0,
                    "rate": rate if charged_fee else 0.0,
                },
            )

        # reverse: one ledger payout arrives as two bank debits
        amt = self._amount() + 20000
        ref = self._ref()
        lr = self._emit_ledger(d, amt, party, ref, "NEFT")
        part1 = round(amt * self.rng.uniform(0.4, 0.6), 2)
        part2 = round(amt - part1, 2)
        brs = []
        for i, p in enumerate((part1, part2), start=1):
            sref = self._ref("setl")
            brs.append(
                self._emit_bank(
                    d + timedelta(days=i - 1),
                    p,
                    sref,
                    "RAZORPAY PAYOUT "
                    + sref
                    + " PART "
                    + str(i)
                    + " OF 2 "
                    + self._short(party, 14),
                    "DEBIT",
                )
            )
        return Case(
            self._case_id(),
            "split_batch",
            [[lr.txn_id, b.stmt_id] for b in brs],
            detail={"direction": "one_ledger_to_many_bank", "n": 2, "gross": round(amt, 2)},
        )

    def case_reference_typo(self) -> Case:
        d, amt, party, ref = self._date(), self._amount(), self._counterparty(), self._ref()
        m = self.rng.choice(PAYMENT_METHODS)
        lr = self._emit_ledger(d, amt, party, ref, m)
        bad, kind = self._typo(ref)
        bd = d + timedelta(days=self.rng.choice([0, 0, 1, 2]))
        br = self._emit_bank(bd, amt, bad, self._narration(bad, party, m))
        return Case(
            self._case_id(),
            "reference_typo",
            [[lr.txn_id, br.stmt_id]],
            detail={"ledger_ref": ref, "bank_ref": bad, "corruption": kind},
        )

    def case_refund_reversal(self) -> Case:
        """A captured payment and its later reversal. Should net to zero, both sides."""
        d, amt, party, ref = self._date(), self._amount(), self._counterparty(), self._ref()
        m = self.rng.choice(PAYMENT_METHODS)
        pay_l = self._emit_ledger(d, amt, party, ref, m, status="captured")
        pay_b = self._emit_bank(
            d + timedelta(days=self.rng.choice([0, 1])), amt, ref, self._narration(ref, party, m)
        )
        gap = self.rng.randint(2, 9)
        rfd_ref = ref + "-RFD"
        rfd_l = self._emit_ledger(
            d + timedelta(days=gap), -amt, party, rfd_ref, m, status="refunded"
        )
        rfd_b = self._emit_bank(
            d + timedelta(days=gap + self.rng.choice([0, 1])),
            -amt,
            ref,
            "REVERSAL/" + ref + "/" + self._short(party, 16) + "/REFUND",
            "DEBIT",
        )
        return Case(
            self._case_id(),
            "refund_reversal",
            [[pay_l.txn_id, pay_b.stmt_id], [rfd_l.txn_id, rfd_b.stmt_id]],
            detail={
                "payment_ref": ref,
                "refund_ref": rfd_ref,
                "gap_days": gap,
                "amount": round(amt, 2),
            },
        )

    def case_orphan_bank(self) -> Case:
        """Money in the bank with nothing behind it in the ledger. Stays unresolved."""
        d, amt = self._date(), self._amount()
        flavour = self.rng.choice(["unknown_credit", "chargeback", "bank_interest", "misc_fee"])
        if flavour == "bank_interest":
            ref = self._ref("int")
            amt = round(self.rng.uniform(180, 3400), 2)
            narr, kind = "INTEREST CREDIT QTR ENDING " + d.isoformat(), "CREDIT"
        elif flavour == "chargeback":
            ref = self._ref("cbk")
            narr, kind, amt = "CHARGEBACK DEBIT " + ref + " DISPUTE RAISED", "DEBIT", -amt
        elif flavour == "misc_fee":
            ref = self._ref("chg")
            amt = -round(self.rng.uniform(300, 5600), 2)
            narr, kind = "BANK CHARGES " + ref + " MONTHLY MAINTENANCE", "DEBIT"
        else:
            ref = self._ref()
            narr = (
                "NEFT-"
                + self.rng.choice(BANK_CODES)
                + "-"
                + ref
                + "-"
                + self._short(self._counterparty(), 14)
            )
            kind = "CREDIT"
        br = self._emit_bank(d, amt, ref, narr, kind)
        return Case(
            self._case_id(),
            "orphan_bank",
            [],
            unresolved_ids=[br.stmt_id],
            detail={"flavour": flavour},
        )

    def case_orphan_ledger(self) -> Case:
        """Ledger says the money came in. The bank never saw it. Stays unresolved."""
        d, amt, party, ref = self._date(), self._amount(), self._counterparty(), self._ref()
        status = self.rng.choice(["captured", "captured", "captured", "initiated"])
        lr = self._emit_ledger(d, amt, party, ref, status=status)
        return Case(
            self._case_id(),
            "orphan_ledger",
            [],
            unresolved_ids=[lr.txn_id],
            detail={"ledger_status": status},
        )

    # -- adversarial cases (stress mix only) -------------------------------
    def case_ambiguous_decoy(self) -> Case:
        """Two payments from one counterparty whose references share a prefix, and a
        bank that truncated both. Only the amounts tell them apart."""
        party = self._counterparty()
        d = self._date()
        stem = "".join(self.rng.choice(REF_ALPHABET) for _ in range(8))
        ref_a = "pay_" + stem + "".join(self.rng.choice(REF_ALPHABET) for _ in range(6))
        ref_b = "pay_" + stem + "".join(self.rng.choice(REF_ALPHABET) for _ in range(6))
        self._used_refs.update({ref_a, ref_b})

        amt_a = self._amount()
        amt_b = round(amt_a + self.rng.choice([-2.0, 2.0, -1.5, 1.5]), 2)

        la = self._emit_ledger(d, amt_a, party, ref_a, "UPI")
        lb = self._emit_ledger(d + timedelta(days=1), amt_b, party, ref_b, "UPI")
        trunc = "pay_" + stem
        sa = self._emit_bank(d + timedelta(days=1), amt_a, trunc,
                             "UPI/" + trunc + "/" + self._short(party) + "/PAYMENT")
        sb = self._emit_bank(d + timedelta(days=1), amt_b, trunc,
                             "UPI/" + trunc + "/" + self._short(party) + "/PAYMENT")
        return Case(
            self._case_id(), "ambiguous_decoy",
            [[la.txn_id, sa.stmt_id], [lb.txn_id, sb.stmt_id]],
            detail={"shared_prefix": trunc, "amount_gap": round(abs(amt_a - amt_b), 2)},
        )

    def case_partial_settlement(self) -> Case:
        """The bank paid less than the ledger says, and not by any fee formula.
        Linking the two is right; auto-resolving without telling anyone is not."""
        d, amt, party, ref = self._date(), self._amount() + 8000, self._counterparty(), self._ref()
        m = self.rng.choice(PAYMENT_METHODS)
        shortfall = round(abs(amt) * self.rng.uniform(0.08, 0.30), 2)
        lr = self._emit_ledger(d, amt, party, ref, m)
        br = self._emit_bank(d + timedelta(days=self.rng.choice([0, 1, 2])),
                             amt - shortfall, ref, self._narration(ref, party, m))
        return Case(
            self._case_id(), "partial_settlement", [[lr.txn_id, br.stmt_id]],
            require_human=True,
            detail={"expected": round(amt, 2), "received": round(amt - shortfall, 2),
                    "shortfall": shortfall,
                    "shortfall_pct": round(shortfall / amt * 100, 2)},
        )

    def case_narration_only_ref(self) -> Case:
        """The statement's reference column is blank. The reference is buried in the
        narration text, which is where a lot of real bank exports put it."""
        d, amt, party, ref = self._date(), self._amount(), self._counterparty(), self._ref()
        m = self.rng.choice(PAYMENT_METHODS)
        lr = self._emit_ledger(d, amt, party, ref, m)
        br = self._emit_bank(d + timedelta(days=self.rng.choice([0, 1, 2])), amt, "",
                             self._narration(ref, party, m))
        return Case(
            self._case_id(), "narration_only_ref", [[lr.txn_id, br.stmt_id]],
            detail={"reference_in_narration": ref},
        )

    def case_late_settlement(self) -> Case:
        """T+8 to T+12. Everything else matches perfectly. This sits outside every
        date window the engine uses, on purpose."""
        d, amt, party, ref = self._date(), self._amount(), self._counterparty(), self._ref()
        m = self.rng.choice(PAYMENT_METHODS)
        shift = self.rng.randint(8, 12)
        lr = self._emit_ledger(d, amt, party, ref, m)
        br = self._emit_bank(d + timedelta(days=shift), amt, ref,
                             self._narration(ref, party, m))
        return Case(
            self._case_id(), "late_settlement", [[lr.txn_id, br.stmt_id]],
            detail={"day_shift": shift},
        )

    # -- driver ------------------------------------------------------------
    def build(self) -> None:
        plan: list[str] = []
        for category, share in self.mix.items():
            plan.extend([category] * round(self.n * share))
        while len(plan) < self.n:
            plan.append("clean_exact")
        plan = plan[: self.n]
        self.rng.shuffle(plan)

        builders = {
            "clean_exact": self.case_clean_exact,
            "date_shift": self.case_date_shift,
            "fee_deducted": self.case_fee_deducted,
            "duplicate_ledger": self.case_duplicate_ledger,
            "split_batch": self.case_split_batch,
            "reference_typo": self.case_reference_typo,
            "refund_reversal": self.case_refund_reversal,
            "orphan_bank": self.case_orphan_bank,
            "orphan_ledger": self.case_orphan_ledger,
            "ambiguous_decoy": self.case_ambiguous_decoy,
            "partial_settlement": self.case_partial_settlement,
            "narration_only_ref": self.case_narration_only_ref,
            "late_settlement": self.case_late_settlement,
        }
        for category in plan:
            self.cases.append(builders[category]())

        # Files arrive sorted by date, the way a real export would.
        self.ledger.sort(key=lambda r: (r.date, r.txn_id))
        self.bank.sort(key=lambda r: (r.date, r.stmt_id))

    def write(self, out_dir: Path) -> dict[str, Any]:
        out_dir.mkdir(parents=True, exist_ok=True)

        with (out_dir / "ledger.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(LedgerRow.__dataclass_fields__))
            w.writeheader()
            for r in self.ledger:
                w.writerow(asdict(r))

        with (out_dir / "bank_statement.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(BankRow.__dataclass_fields__))
            w.writeheader()
            for r in self.bank:
                w.writerow(asdict(r))

        by_cat: dict[str, int] = {}
        for c in self.cases:
            by_cat[c.category] = by_cat.get(c.category, 0) + 1

        truth = {
            "seed": self.seed,
            "profile": "stress" if self.stress else "standard",
            "case_count": len(self.cases),
            "ledger_rows": len(self.ledger),
            "bank_rows": len(self.bank),
            "cases_by_category": by_cat,
            "cases": [asdict(c) for c in self.cases],
        }
        (out_dir / "ground_truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
        return truth


def generate(
    out_dir: Path, n_cases: int = 400, seed: int = 20260822, stress: bool = False
) -> dict[str, Any]:
    g = Generator(n_cases=n_cases, seed=seed, stress=stress)
    g.build()
    return g.write(out_dir)
