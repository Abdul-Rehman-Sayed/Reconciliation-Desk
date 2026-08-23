"""
Deterministic reconciliation engine.

No AI anywhere in this file. Six passes run in order, each one working only on
what the previous passes left behind:

    1  exact         reference + amount + date all line up
    2  tolerant      fee-adjusted amounts, longer settlement delays, near-identical refs
    2b refund        net-to-zero reversal pairing
    3  duplicate     same-side repeats (double webhook fire)
    4  composite     N rows on one side summing to 1 row on the other  -> LLM candidate
    5  fuzzy         damaged reference numbers                         -> LLM candidate
    6  remainder     nothing plausible found                           -> LLM hypothesis only

Passes 1-3 auto-resolve. Passes 4-6 only ever produce *candidates* - a human
approves them in the UI. Every threshold below is a named constant because they
get tuned live.
"""

from __future__ import annotations

import itertools
import re
import time
import uuid
from dataclasses import asdict, dataclass, field, fields, replace as dataclasses_replace
from datetime import date
from typing import Any, Iterable, Sequence

from rapidfuzz import fuzz, process

# ==========================================================================
# Tunable thresholds.  Everything the engine decides traces back to one of these.
# ==========================================================================

# --- amount tolerance ---------------------------------------------------
AMOUNT_EXACT_TOLERANCE = 1.00     # INR of rounding slack for the exact pass
AMOUNT_TOLERANT_BAND = 0.004      # 0.4% of the ledger amount for the tolerant pass
FEE_MATCH_TOLERANCE = 2.00        # INR slack when testing a gateway-fee formula
FEE_RATE_MIN = 0.005              # an implied deduction below this is not a fee
FEE_RATE_MAX = 0.035              # above this it is not a fee either, it is a problem
KNOWN_FEE_RATES = (0.0118, 0.0236, 0.0295)   # rate + 18% GST

# --- date tolerance -----------------------------------------------------
DATE_WINDOW_EXACT = 2             # days, pass 1
DATE_WINDOW_TOLERANT = 5          # days, pass 2
DATE_WINDOW_REFUND = 4            # days, pass 2b
DUPLICATE_DATE_WINDOW = 1         # days, pass 3
DATE_WINDOW_COMPOSITE = 4         # days, pass 4
DATE_WINDOW_FUZZY = 6             # days, pass 5
# A reference number is a unique identifier. When exactly one unmatched row on each
# side carries the same one and the amounts agree to the paisa, the date gap carries
# no information - settlements do sometimes take a fortnight. This is the only rule
# allowed to ignore the date windows, and it is safe precisely because it demands
# uniqueness on both sides.
DATE_WINDOW_LATE = 45             # days, still inside one statement period

# --- reference similarity (0-100) ---------------------------------------
REF_FUZZY_AUTO = 90.0             # strict ratio at/above this auto-resolves in pass 2
REF_FUZZY_CANDIDATE = 62.0        # blended WRatio at/above this becomes an LLM candidate

# --- composite ----------------------------------------------------------
COMPOSITE_MAX_GROUP = 4           # largest subset the engine will try to sum
COMPOSITE_TOLERANCE = 2.00        # INR slack on a summed total
COMPOSITE_PARTY_PREFIX = 10       # chars of counterparty name mined out of a narration
COMPOSITE_FALLBACK_POOL = 14      # max rows to brute-force when the narration names no party
# Brute-forcing subset sums on amount alone, with no evidence that the rows belong
# together, is off. It was built and measured: across ~1,450 candidate subsets per
# bank row it reliably finds a coincidence that lands within tolerance of the target,
# and on both test datasets every false positive the engine produced came from this
# one rule. The counterparty-mined path covers every batch either dataset contains.
# Left in place, and off, because "we tried it and it cost precision" is worth more
# than a rule that quietly deleted itself.
COMPOSITE_ALLOW_AMOUNT_ONLY = False

# --- duplicate handling -------------------------------------------------
# When two rows on the same side are genuinely indistinguishable (same reference,
# same amount), which one "is" the real transaction is arbitrary. Standard ops
# practice is that the earliest entry is the original and anything after it is the
# repeat, so the engine biases matching towards the earliest row. This is about
# answering consistently, not about one answer being more correct than the other.
DUPLICATE_ORIGINAL_PREFERENCE = 5.0

# --- pass 5 scoring -----------------------------------------------------
FUZZY_MIN_SCORE = 0.58            # blended ref/amount/date score needed to propose a pair
FUZZY_W_REF = 0.55
FUZZY_W_AMOUNT = 0.30
FUZZY_W_DATE = 0.15
# An amount inside the tolerance band is not as good as an amount that agrees to the
# paisa. Without this, two candidates a couple of rupees apart score identically and
# the engine picks between them by luck.
FUZZY_IN_BAND_PENALTY = 0.10
AMBIGUITY_MARGIN = 0.02           # runner-up this close means nobody should be sure

# --- confidence ---------------------------------------------------------
CONF_EXACT = 0.99
CONF_TOLERANT = 0.92
CONF_REFUND = 0.90
CONF_DUPLICATE = 0.95
CONF_COMPOSITE_CEIL = 0.75        # a summed match is judgement territory, never auto
CONF_FUZZY_CEIL = 0.70
CONF_UNRESOLVED = 0.15

# Two different things that were previously the same number, which is why the
# auto-resolve threshold could not be moved meaningfully.
#
# CONF_TOLERANT_FLOOR is a *measurement* floor: however much penalty a tolerant
# match accumulates, the engine does not claim to be less sure than this about a
# match whose reference is identical and whose amount is explained.
#
# AUTO_RESOLVE_FLOOR is a *policy*: the confidence a deterministic result has to
# reach before it commits with no human involved. Raising it must make the engine
# more cautious. While the two were one constant, raising it inflated the recorded
# confidence to meet itself and nothing became more cautious at all - the dial
# looked connected and was not.
CONF_TOLERANT_FLOOR = 0.85        # lowest confidence a tolerant match will report
AUTO_RESOLVE_FLOOR = 0.85         # a deterministic result at/above this needs no human


# ==========================================================================
# The same numbers again, as a value the engine carries rather than reads from
# module scope.
#
# The constants above stay the single source of truth - every default below is
# taken from one - but an Engine holds its own copy, so two engines with
# different tolerances can run in the same process without one standing on the
# other. That is what makes the threshold sliders possible: a slider move builds
# a Thresholds, runs a second engine with it, and compares. It never mutates
# global state, so the answer key and the baseline stay stable underneath.
#
# Nothing here calls the LLM. Re-classification at a new tolerance is pure
# deterministic work - it is the cheapest thing in the system, and that is
# exactly why the sliders are safe to hand to a live audience.
# ==========================================================================
@dataclass(frozen=True)
class Thresholds:
    amount_exact_tolerance: float = AMOUNT_EXACT_TOLERANCE
    amount_tolerant_band: float = AMOUNT_TOLERANT_BAND
    fee_match_tolerance: float = FEE_MATCH_TOLERANCE
    fee_rate_min: float = FEE_RATE_MIN
    fee_rate_max: float = FEE_RATE_MAX
    known_fee_rates: tuple[float, ...] = KNOWN_FEE_RATES

    date_window_exact: int = DATE_WINDOW_EXACT
    date_window_tolerant: int = DATE_WINDOW_TOLERANT
    date_window_refund: int = DATE_WINDOW_REFUND
    duplicate_date_window: int = DUPLICATE_DATE_WINDOW
    date_window_composite: int = DATE_WINDOW_COMPOSITE
    date_window_fuzzy: int = DATE_WINDOW_FUZZY
    date_window_late: int = DATE_WINDOW_LATE

    ref_fuzzy_auto: float = REF_FUZZY_AUTO
    ref_fuzzy_candidate: float = REF_FUZZY_CANDIDATE

    composite_max_group: int = COMPOSITE_MAX_GROUP
    composite_tolerance: float = COMPOSITE_TOLERANCE
    composite_party_prefix: int = COMPOSITE_PARTY_PREFIX
    composite_fallback_pool: int = COMPOSITE_FALLBACK_POOL
    composite_allow_amount_only: bool = COMPOSITE_ALLOW_AMOUNT_ONLY

    duplicate_original_preference: float = DUPLICATE_ORIGINAL_PREFERENCE

    fuzzy_min_score: float = FUZZY_MIN_SCORE
    fuzzy_w_ref: float = FUZZY_W_REF
    fuzzy_w_amount: float = FUZZY_W_AMOUNT
    fuzzy_w_date: float = FUZZY_W_DATE
    fuzzy_in_band_penalty: float = FUZZY_IN_BAND_PENALTY
    ambiguity_margin: float = AMBIGUITY_MARGIN

    conf_exact: float = CONF_EXACT
    conf_tolerant: float = CONF_TOLERANT
    conf_refund: float = CONF_REFUND
    conf_duplicate: float = CONF_DUPLICATE
    conf_composite_ceil: float = CONF_COMPOSITE_CEIL
    conf_fuzzy_ceil: float = CONF_FUZZY_CEIL
    conf_unresolved: float = CONF_UNRESOLVED

    conf_tolerant_floor: float = CONF_TOLERANT_FLOOR
    auto_resolve_floor: float = AUTO_RESOLVE_FLOOR

    def replace(self, **changes: Any) -> "Thresholds":
        """A copy with some fields moved. Unknown names are refused loudly."""
        allowed = {f.name for f in fields(Thresholds)}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError("Not a threshold: " + ", ".join(sorted(unknown)))
        return dataclasses_replace(self, **changes)

    def as_dict(self) -> dict[str, Any]:
        out = {f.name: getattr(self, f.name) for f in fields(Thresholds)}
        out["known_fee_rates"] = list(out["known_fee_rates"])
        return out

    def diff_from_default(self) -> dict[str, Any]:
        """Only what has actually been moved. This is what the UI badges."""
        base = Thresholds()
        return {k: v for k, v in self.as_dict().items() if base.as_dict()[k] != v}


# What a slider may touch, with the units and bounds the interface renders.
ADJUSTABLE_THRESHOLDS: tuple[dict[str, Any], ...] = (
    {"key": "date_window_exact", "label": "Same-day window", "unit": "days",
     "min": 0, "max": 10, "step": 1,
     "help": "How far apart the two dates may be and still count as the same day's business."},
    {"key": "date_window_tolerant", "label": "Settlement delay allowed", "unit": "days",
     "min": 0, "max": 21, "step": 1,
     "help": "The longest settlement lag pass 2 will absorb without asking anyone."},
    {"key": "amount_exact_tolerance", "label": "Rounding slack", "unit": "INR",
     "min": 0, "max": 25, "step": 0.5,
     "help": "Rupees of difference treated as rounding rather than a discrepancy."},
    {"key": "amount_tolerant_band", "label": "Amount band", "unit": "%",
     "min": 0, "max": 0.03, "step": 0.001, "display": "percent",
     "help": "Percentage of the ledger amount allowed to differ in the tolerant pass."},
    {"key": "ref_fuzzy_auto", "label": "Reference match to auto-resolve", "unit": "%",
     "min": 60, "max": 100, "step": 1,
     "help": "String similarity at which two references are treated as the same reference."},
    {"key": "fuzzy_min_score", "label": "Candidate floor", "unit": "score",
     "min": 0.3, "max": 0.95, "step": 0.01,
     "help": "Blended reference/amount/date score below which pass 5 proposes nothing."},
    {"key": "auto_resolve_floor", "label": "Auto-resolve floor", "unit": "confidence",
     "min": 0.5, "max": 1.0, "step": 0.01,
     "help": "Confidence a deterministic result must reach before it commits without a "
             "human. Raise it and matches the engine was willing to make alone move into "
             "the queue instead."},
)

DEFAULT_THRESHOLDS = Thresholds()

_NON_ALNUM = re.compile(r"[^A-Z0-9]")

# Plenty of real bank exports leave the reference column blank and bury the
# reference inside the narration instead. Mining it out is a rule, not a judgement
# call, so it belongs here rather than in the LLM's queue.
_REF_IN_NARRATION = re.compile(r"\b((?:pay|setl|txn|ref|utr|rrn)[_-][A-Za-z0-9]{6,})\b", re.I)
_LONG_TOKEN = re.compile(r"\b([A-Za-z0-9]{10,})\b")
_NARRATION_STOPWORDS = {
    "RAZORPAY", "SETTLEMENT", "MERCHANT", "PAYMENT", "REVERSAL", "TRANSFER",
    "CHARGEBACK", "INTEREST", "MAINTENANCE", "MONTHLY", "CHARGES",
}


def normalize_ref(value: str) -> str:
    """Upper-case, strip every separator. 'pay_Ab-12' and 'PAYAB12' are the same key."""
    return _NON_ALNUM.sub("", (value or "").upper())


def reference_from_narration(narration: str) -> str | None:
    """Pull a payment reference out of free-text narration, or return None."""
    if not narration:
        return None
    hit = _REF_IN_NARRATION.search(narration)
    if hit:
        return hit.group(1)
    for token in _LONG_TOKEN.findall(narration):
        if token.upper() in _NARRATION_STOPWORDS:
            continue
        # A reference mixes letters and digits; an English word does not.
        if any(c.isdigit() for c in token) and any(c.isalpha() for c in token):
            return token
    return None


def _d(value: str) -> date:
    return date.fromisoformat(value)


def _days(a: str, b: str) -> int:
    return abs((_d(a) - _d(b)).days)


# ==========================================================================
# Records
# ==========================================================================
@dataclass
class Record:
    rec_id: str
    side: str            # "ledger" | "bank"
    date: str
    amount: float
    reference_number: str
    norm_ref: str = ""
    counterparty: str = ""
    payment_method: str = ""
    status: str = ""
    narration: str = ""
    kind: str = ""       # bank CREDIT / DEBIT
    norm_text: str = ""  # narration + counterparty, normalized, for party mining
    ref_source: str = "column"   # "column" | "narration" | "none"

    def __post_init__(self) -> None:
        self.norm_ref = normalize_ref(self.reference_number)
        self.norm_text = _NON_ALNUM.sub("", (self.narration or self.counterparty or "").upper())
        if not self.norm_ref:
            mined = reference_from_narration(self.narration)
            if mined:
                self.reference_number = mined
                self.norm_ref = normalize_ref(mined)
                self.ref_source = "narration"
            else:
                self.ref_source = "none"

    def public(self) -> dict[str, Any]:
        out = {
            "id": self.rec_id,
            "side": self.side,
            "date": self.date,
            "amount": round(self.amount, 2),
            "reference_number": self.reference_number,
        }
        if self.side == "ledger":
            out.update(
                counterparty=self.counterparty,
                payment_method=self.payment_method,
                status=self.status,
            )
        else:
            out.update(narration=self.narration, type=self.kind, ref_source=self.ref_source)
        return out


@dataclass
class Link:
    """One resolved or proposed correspondence between the two sides."""

    link_id: str
    ledger_ids: list[str]
    stmt_ids: list[str]
    pass_name: str
    method: str
    confidence: float
    auto_resolved: bool
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Exception_:
    """Anything a human has to look at. Not all of these reach the LLM."""

    exception_id: str
    kind: str            # duplicate | composite_candidate | fuzzy_candidate
    #                      unmatched_ledger | unmatched_bank
    ledger_ids: list[str]
    stmt_ids: list[str]
    engine_confidence: float
    engine_note: str
    needs_llm: bool
    link_id: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    llm: dict[str, Any] | None = None
    status: str = "pending"          # pending | approved | rejected | investigating
    decided_at: str | None = None
    decided_note: str | None = None


@dataclass
class PassStat:
    name: str
    label: str
    description: str
    duration_ms: float
    links_made: int
    records_resolved: int
    exceptions_raised: int
    remaining_ledger: int
    remaining_bank: int


# ==========================================================================
# Engine
# ==========================================================================
class Engine:
    def __init__(self, ledger: Sequence[Record], bank: Sequence[Record],
                 thresholds: Thresholds | None = None):
        # Held on the instance, never read from module scope, so a second
        # engine at different tolerances cannot disturb this one.
        self.t = thresholds or DEFAULT_THRESHOLDS
        self.ledger = list(ledger)
        self.bank = list(bank)
        self.by_id: dict[str, Record] = {r.rec_id: r for r in self.ledger + self.bank}

        self.taken_l: set[str] = set()
        self.taken_b: set[str] = set()

        self.links: list[Link] = []
        self.exceptions: list[Exception_] = []
        self.passes: list[PassStat] = []
        self._seq = 0

        # 0 for the earliest row in a same-side duplicate group, 1 for the next, ...
        self.dup_rank: dict[str, int] = {}
        for rows in (self.ledger, self.bank):
            groups: dict[tuple[str, int], list[Record]] = {}
            for r in rows:
                groups.setdefault((r.norm_ref, int(round(r.amount * 100))), []).append(r)
            for members in groups.values():
                for rank, r in enumerate(sorted(members, key=lambda x: (x.date, x.rec_id))):
                    self.dup_rank[r.rec_id] = rank

    # -- helpers -----------------------------------------------------------
    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return "%s%04d" % (prefix, self._seq)

    def free_ledger(self) -> list[Record]:
        return [r for r in self.ledger if r.rec_id not in self.taken_l]

    def free_bank(self) -> list[Record]:
        return [r for r in self.bank if r.rec_id not in self.taken_b]

    def _commit(
        self,
        ledger_ids: Iterable[str],
        stmt_ids: Iterable[str],
        pass_name: str,
        method: str,
        confidence: float,
        auto: bool,
        evidence: dict[str, Any],
    ) -> Link:
        lids, sids = list(ledger_ids), list(stmt_ids)
        # The policy gate, applied once, here, because every link in the system
        # is created through this method. A pass says whether it is *willing* to
        # auto-resolve; the floor decides whether it is *allowed* to. The `and`
        # matters: raising the floor can only ever demote a link to needing a
        # human, never promote a proposal into committing on its own.
        conf = round(confidence, 3)
        demoted = auto and conf < self.t.auto_resolve_floor
        auto = auto and not demoted

        link = Link(
            link_id=self._next_id("LK"),
            ledger_ids=lids,
            stmt_ids=sids,
            pass_name=pass_name,
            method=method,
            confidence=conf,
            auto_resolved=auto,
            evidence=evidence,
        )
        self.links.append(link)
        self.taken_l.update(lids)
        self.taken_b.update(sids)

        # A demoted link needs somewhere to land. "Proposed" means a person
        # confirms it, and a person can only confirm what reaches the queue -
        # without this, raising the floor would move records out of the
        # auto-resolved count and into nothing at all, which would make the
        # headline number improve for the wrong reason.
        #
        # It is marked needs_llm=False deliberately. The engine already proved
        # this match; what changed is a policy about how sure is sure enough.
        # Paying a model to re-narrate a threshold decision would be waste.
        if demoted:
            self.exceptions.append(
                Exception_(
                    exception_id=self._next_id("EX"),
                    kind="below_auto_threshold",
                    ledger_ids=lids,
                    stmt_ids=sids,
                    engine_confidence=conf,
                    engine_note=(
                        "The %s pass matched these on %s with confidence %.2f. That is "
                        "below the auto-resolve floor of %.2f you have set, so the engine "
                        "is proposing it rather than committing it."
                        % (pass_name, method.replace("_", " "), conf,
                           self.t.auto_resolve_floor)
                    ),
                    needs_llm=False,
                    link_id=link.link_id,
                    evidence={**evidence, "demoted_by_auto_resolve_floor": True,
                              "auto_resolve_floor": self.t.auto_resolve_floor},
                )
            )
        return link

    @staticmethod
    def _greedy(candidates: list[tuple[float, str, str, dict[str, Any]]]):
        """Best-scoring pair wins; both records are then off the table."""
        used_l: set[str] = set()
        used_b: set[str] = set()
        for score, lid, sid, payload in sorted(candidates, key=lambda c: -c[0]):
            if lid in used_l or sid in used_b:
                continue
            used_l.add(lid)
            used_b.add(sid)
            yield score, lid, sid, payload

    def _dup_penalty(self, *rows: Record) -> float:
        """Push later members of a same-side duplicate group to the back of the queue."""
        return self.t.duplicate_original_preference * sum(self.dup_rank.get(r.rec_id, 0) for r in rows)

    def _fee_shape(self, gross: float, net: float) -> dict[str, Any] | None:
        """Does `net` look like `gross` minus a gateway fee?"""
        if gross <= 0 or net <= 0 or net >= gross:
            return None
        for rate in self.t.known_fee_rates:
            if abs(gross * (1 - rate) - net) <= self.t.fee_match_tolerance:
                return {
                    "fee_rate": rate,
                    "fee_amount": round(gross - net, 2),
                    "known_rate": True,
                }
        implied = 1 - (net / gross)
        if self.t.fee_rate_min <= implied <= self.t.fee_rate_max:
            return {
                "fee_rate": round(implied, 5),
                "fee_amount": round(gross - net, 2),
                "known_rate": False,
            }
        return None

    def _record_pass(
        self, name: str, label: str, desc: str, t0: float, links_before: int, exc_before: int
    ) -> None:
        made = self.links[links_before:]
        self.passes.append(
            PassStat(
                name=name,
                label=label,
                description=desc,
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
                links_made=len(made),
                records_resolved=sum(len(l.ledger_ids) + len(l.stmt_ids) for l in made),
                exceptions_raised=len(self.exceptions) - exc_before,
                remaining_ledger=len(self.free_ledger()),
                remaining_bank=len(self.free_bank()),
            )
        )

    # ======================================================================
    # Pass 1 - exact
    # ======================================================================
    def pass_exact(self) -> None:
        t0, lb, eb = time.perf_counter(), len(self.links), len(self.exceptions)

        index: dict[str, list[Record]] = {}
        for b in self.free_bank():
            index.setdefault(b.norm_ref, []).append(b)

        candidates = []
        for l in self.free_ledger():
            for b in index.get(l.norm_ref, ()):
                if (l.amount >= 0) != (b.amount >= 0):
                    continue
                d_amt = abs(l.amount - b.amount)
                d_day = _days(l.date, b.date)
                if d_amt <= self.t.amount_exact_tolerance and d_day <= self.t.date_window_exact:
                    score = 1000 - d_amt - d_day - self._dup_penalty(l, b)
                    candidates.append(
                        (score, l.rec_id, b.rec_id,
                         {"amount_delta": round(d_amt, 2), "day_delta": d_day,
                          "ref_similarity": 100.0})
                    )

        for _score, lid, sid, ev in self._greedy(candidates):
            self._commit([lid], [sid], "exact", "exact_reference_amount_date",
                         self.t.conf_exact, True, ev)

        self._record_pass(
            "exact", "Exact",
            "Reference, amount and date all agree", t0, lb, eb,
        )

    # ======================================================================
    # Pass 2 - tolerant (fee-aware, longer settlement window, near-identical refs)
    # ======================================================================
    def pass_tolerant(self) -> None:
        t0, lb, eb = time.perf_counter(), len(self.links), len(self.exceptions)

        free_l, free_b = self.free_ledger(), self.free_bank()
        if free_l and free_b:
            bank_refs = [b.norm_ref for b in free_b]
            sim = process.cdist(
                [l.norm_ref for l in free_l], bank_refs,
                scorer=fuzz.ratio, score_cutoff=self.t.ref_fuzzy_auto, workers=-1,
            )

            candidates = []
            for i, l in enumerate(free_l):
                for j, b in enumerate(free_b):
                    ref_score = float(sim[i][j])
                    if ref_score < self.t.ref_fuzzy_auto:
                        continue
                    if (l.amount >= 0) != (b.amount >= 0):
                        continue
                    d_day = _days(l.date, b.date)
                    if d_day > self.t.date_window_tolerant:
                        continue

                    d_amt = abs(l.amount - b.amount)
                    band = max(self.t.amount_exact_tolerance, abs(l.amount) * self.t.amount_tolerant_band)
                    ev: dict[str, Any] = {
                        "amount_delta": round(d_amt, 2),
                        "day_delta": d_day,
                        "ref_similarity": round(ref_score, 1),
                    }

                    if d_amt <= band:
                        method = "date_delay" if d_day > self.t.date_window_exact else "amount_rounding"
                        penalty = 0.0
                    else:
                        fee = self._fee_shape(abs(l.amount), abs(b.amount))
                        if fee is None:
                            continue
                        method = "fee_adjusted"
                        ev.update(fee)
                        penalty = 0.0 if fee["known_rate"] else 0.03

                    penalty += 0.02 if ref_score < 100 else 0.0
                    penalty += 0.01 * max(0, d_day - self.t.date_window_exact)
                    conf = max(self.t.conf_tolerant_floor, self.t.conf_tolerant - penalty)
                    # min(d_amt, 5) keeps an exact amount ahead of a merely in-band one
                    # without letting a large gap swamp the reference score.
                    score = (ref_score + 1000 - d_day - min(d_amt, 5.0)
                             - self._dup_penalty(l, b))
                    candidates.append((score, l.rec_id, b.rec_id,
                                       {"ev": ev, "method": method, "conf": conf}))

            for _s, lid, sid, p in self._greedy(candidates):
                self._commit([lid], [sid], "tolerant", p["method"], p["conf"], True, p["ev"])

        self._match_late_settlements()

        self._record_pass(
            "tolerant", "Tolerant",
            "Gateway fee deducted, T+3 settlement delay, one-character reference drift",
            t0, lb, eb,
        )

    def _match_late_settlements(self) -> None:
        """Same reference, same amount, unique on both sides, but well outside the
        date windows. Uniqueness is what makes ignoring the date safe here."""
        l_by_ref: dict[str, list[Record]] = {}
        b_by_ref: dict[str, list[Record]] = {}
        for r in self.free_ledger():
            l_by_ref.setdefault(r.norm_ref, []).append(r)
        for r in self.free_bank():
            b_by_ref.setdefault(r.norm_ref, []).append(r)

        for ref, lrows in l_by_ref.items():
            brows = b_by_ref.get(ref)
            if not ref or len(lrows) != 1 or not brows or len(brows) != 1:
                continue
            l, b = lrows[0], brows[0]
            if (l.amount >= 0) != (b.amount >= 0):
                continue
            d_amt = abs(l.amount - b.amount)
            d_day = _days(l.date, b.date)
            if d_amt > self.t.amount_exact_tolerance or d_day > self.t.date_window_late:
                continue
            self._commit(
                [l.rec_id], [b.rec_id], "tolerant", "late_settlement",
                max(self.t.conf_tolerant_floor, self.t.conf_tolerant - 0.01 * d_day), True,
                {"amount_delta": round(d_amt, 2), "day_delta": d_day,
                 "ref_similarity": 100.0, "unique_reference_both_sides": True},
            )

    # ======================================================================
    # Pass 2b - refund / reversal net-to-zero pairing
    # ======================================================================
    def pass_refund(self) -> None:
        t0, lb, eb = time.perf_counter(), len(self.links), len(self.exceptions)

        neg_l = [l for l in self.free_ledger() if l.amount < 0]
        neg_b = [b for b in self.free_bank() if b.amount < 0]

        candidates = []
        for l in neg_l:
            for b in neg_b:
                if abs(abs(l.amount) - abs(b.amount)) > self.t.amount_exact_tolerance:
                    continue
                d_day = _days(l.date, b.date)
                if d_day > self.t.date_window_refund:
                    continue
                # One reference contains the other once separators are stripped:
                # 'pay_X-RFD' against the original 'pay_X'.
                a, c = l.norm_ref, b.norm_ref
                shared = a.startswith(c) or c.startswith(a) or fuzz.partial_ratio(a, c) >= 95
                if not shared:
                    continue
                base = c if len(c) < len(a) else a
                original = next(
                    (lk for lk in self.links
                     if any(self.by_id[x].norm_ref.startswith(base) for x in lk.stmt_ids)),
                    None,
                )
                candidates.append(
                    (100 - d_day, l.rec_id, b.rec_id,
                     {"amount_delta": round(abs(abs(l.amount) - abs(b.amount)), 2),
                      "day_delta": d_day,
                      "reversal_of": original.link_id if original else None,
                      "nets_to_zero": original is not None})
                )

        for _s, lid, sid, ev in self._greedy(candidates):
            self._commit([lid], [sid], "refund", "refund_reversal", self.t.conf_refund, True, ev)

        self._record_pass(
            "refund", "Reversals",
            "Refund paired back to the payment it cancels", t0, lb, eb,
        )

    # ======================================================================
    # Pass 3 - same-side duplicate detection
    # ======================================================================
    def pass_duplicates(self) -> None:
        t0, lb, eb = time.perf_counter(), len(self.links), len(self.exceptions)

        for side, rows in (("ledger", self.ledger), ("bank", self.bank)):
            taken = self.taken_l if side == "ledger" else self.taken_b
            groups: dict[tuple[str, int], list[Record]] = {}
            for r in rows:
                groups.setdefault((r.norm_ref, int(round(r.amount * 100))), []).append(r)

            for (_ref, _paise), members in groups.items():
                if len(members) < 2:
                    continue
                members.sort(key=lambda r: (r.date, r.rec_id))
                anchor = next((m for m in members if m.rec_id in taken), members[0])
                anchor_matched = anchor.rec_id in taken
                for m in members:
                    if m.rec_id == anchor.rec_id or m.rec_id in taken:
                        continue
                    if _days(m.date, anchor.date) > self.t.duplicate_date_window:
                        continue
                    taken.add(m.rec_id)
                    note = (
                        "Same reference %s, same amount and same date as %s%s. This %s row "
                        "is a repeat - most often a payment webhook that fired twice."
                        % (m.reference_number, anchor.rec_id,
                           ", which is already matched to the other side" if anchor_matched
                           else ", the earlier of the two", side)
                    )
                    self.exceptions.append(
                        Exception_(
                            exception_id=self._next_id("EX"),
                            kind="duplicate",
                            ledger_ids=[m.rec_id] if side == "ledger" else [],
                            stmt_ids=[m.rec_id] if side == "bank" else [],
                            engine_confidence=self.t.conf_duplicate,
                            engine_note=note,
                            needs_llm=False,
                            evidence={
                                "duplicate_of": anchor.rec_id,
                                "side": side,
                                "reference_number": m.reference_number,
                                "amount": round(m.amount, 2),
                                "day_delta": _days(m.date, anchor.date),
                                "anchor_matched": anchor_matched,
                            },
                        )
                    )

        self._record_pass(
            "duplicates", "Duplicates",
            "The same row banked twice on one side", t0, lb, eb,
        )

    # ======================================================================
    # Pass 4 - composite (many-to-one), routed to the LLM
    # ======================================================================
    def _party_candidates(self, bank_row: Record, pool: list[Record]) -> list[Record]:
        """Mine the counterparty out of the bank narration and pull that party's rows."""
        out = []
        for l in pool:
            key = _NON_ALNUM.sub("", l.counterparty.upper())[:self.t.composite_party_prefix]
            if len(key) >= 5 and key in bank_row.norm_text:
                if _days(l.date, bank_row.date) <= self.t.date_window_composite:
                    out.append(l)
        return out

    def _subset_hits(
        self, pool: list[Record], target: float, allow_fee: bool = True
    ) -> list[list[Record]]:
        """Every subset of 2..N rows whose absolute sum lands on `target`.

        `allow_fee` also accepts a sum that lands on `target` once a plausible
        gateway fee is deducted. That is only safe when the pool is already known
        to belong together (same counterparty). On an unrestricted pool a fee band
        is wide enough to make unrelated rows sum to almost anything, so the
        amount-only fallback below turns it off.
        """
        hits = []
        for size in range(2, min(self.t.composite_max_group, len(pool)) + 1):
            for combo in itertools.combinations(pool, size):
                total = sum(abs(r.amount) for r in combo)
                if abs(total - target) <= self.t.composite_tolerance:
                    hits.append(list(combo))
                elif allow_fee and self._fee_shape(total, target) is not None:
                    hits.append(list(combo))
        return hits

    def pass_composite(self) -> None:
        t0, lb, eb = time.perf_counter(), len(self.links), len(self.exceptions)

        # direction A: many ledger rows -> one bank row
        for b in list(self.free_bank()):
            if b.rec_id in self.taken_b:
                continue
            pool = self._party_candidates(b, self.free_ledger())
            hits = self._subset_hits(pool, abs(b.amount)) if len(pool) >= 2 else []
            basis = "counterparty_mined_from_narration"

            if not hits and self.t.composite_allow_amount_only:
                # Narration named no party we recognise. Brute-force on amount alone,
                # but only on an exact sum and only if the answer is unique - a fee
                # band across unrelated rows produces coincidences, not matches.
                near = [
                    l for l in self.free_ledger()
                    if _days(l.date, b.date) <= 2 and abs(l.amount) < abs(b.amount)
                ]
                if 2 <= len(near) <= self.t.composite_fallback_pool:
                    hits = self._subset_hits(near, abs(b.amount), allow_fee=False)
                    basis = "exact_sum_only"
            if len(hits) != 1:
                continue

            combo = hits[0]
            gross = sum(abs(r.amount) for r in combo)
            fee = self._fee_shape(gross, abs(b.amount))
            ev = {
                "direction": "many_ledger_to_one_bank",
                "component_count": len(combo),
                "component_total": round(gross, 2),
                "bank_amount": round(abs(b.amount), 2),
                "residual": round(gross - abs(b.amount), 2),
                "day_span": max(_days(r.date, b.date) for r in combo),
                "counterparty": combo[0].counterparty,
                "basis": basis,
            }
            if fee:
                ev.update(fee)
            link = self._commit(
                [r.rec_id for r in combo], [b.rec_id], "composite",
                "composite_many_to_one", self.t.conf_composite_ceil, False, ev,
            )
            self.exceptions.append(
                Exception_(
                    exception_id=self._next_id("EX"),
                    kind="composite_candidate",
                    ledger_ids=[r.rec_id for r in combo],
                    stmt_ids=[b.rec_id],
                    engine_confidence=self.t.conf_composite_ceil,
                    engine_note=(
                        "%d ledger rows for %s add up to %.2f, which is what the bank "
                        "settled in one line." % (len(combo), ev["counterparty"], gross)
                    ),
                    needs_llm=True,
                    link_id=link.link_id,
                    evidence=ev,
                )
            )

        # direction B: one ledger row -> many bank rows
        for l in list(self.free_ledger()):
            if l.rec_id in self.taken_l:
                continue
            key = _NON_ALNUM.sub("", l.counterparty.upper())[:self.t.composite_party_prefix]
            if len(key) < 5:
                continue
            pool = [
                b for b in self.free_bank()
                if key in b.norm_text and _days(b.date, l.date) <= self.t.date_window_composite
                and (b.amount >= 0) == (l.amount >= 0)
            ]
            hits = self._subset_hits(pool, abs(l.amount)) if len(pool) >= 2 else []
            if len(hits) != 1:
                continue

            combo = hits[0]
            ev = {
                "direction": "one_ledger_to_many_bank",
                "component_count": len(combo),
                "component_total": round(sum(abs(r.amount) for r in combo), 2),
                "ledger_amount": round(abs(l.amount), 2),
                "residual": round(sum(abs(r.amount) for r in combo) - abs(l.amount), 2),
                "day_span": max(_days(r.date, l.date) for r in combo),
                "counterparty": l.counterparty,
            }
            link = self._commit(
                [l.rec_id], [r.rec_id for r in combo], "composite",
                "composite_one_to_many", self.t.conf_composite_ceil, False, ev,
            )
            self.exceptions.append(
                Exception_(
                    exception_id=self._next_id("EX"),
                    kind="composite_candidate",
                    ledger_ids=[l.rec_id],
                    stmt_ids=[r.rec_id for r in combo],
                    engine_confidence=self.t.conf_composite_ceil,
                    engine_note=(
                        "One ledger payout of %.2f to %s appears to have left the account as "
                        "%d separate bank lines." % (abs(l.amount), l.counterparty, len(combo))
                    ),
                    needs_llm=True,
                    link_id=link.link_id,
                    evidence=ev,
                )
            )

        self._record_pass(
            "composite", "Composite",
            "Several rows on one side settling as a single row on the other", t0, lb, eb,
        )

    # ======================================================================
    # Pass 5 - fuzzy reference, routed to the LLM
    # ======================================================================
    def pass_fuzzy(self) -> None:
        t0, lb, eb = time.perf_counter(), len(self.links), len(self.exceptions)

        free_l, free_b = self.free_ledger(), self.free_bank()
        if free_l and free_b:
            sim = process.cdist(
                [l.norm_ref for l in free_l], [b.norm_ref for b in free_b],
                scorer=fuzz.WRatio, workers=-1,
            )

            candidates = []
            for i, l in enumerate(free_l):
                for j, b in enumerate(free_b):
                    if (l.amount >= 0) != (b.amount >= 0):
                        continue
                    d_day = _days(l.date, b.date)
                    if d_day > self.t.date_window_fuzzy:
                        continue
                    ref_score = float(sim[i][j])
                    d_amt = abs(abs(l.amount) - abs(b.amount))
                    amount_close = d_amt <= max(self.t.amount_exact_tolerance,
                                                abs(l.amount) * self.t.amount_tolerant_band)
                    fee = self._fee_shape(abs(l.amount), abs(b.amount))
                    if ref_score < self.t.ref_fuzzy_candidate and not (amount_close or fee):
                        continue

                    if amount_close:
                        band = max(self.t.amount_exact_tolerance, abs(l.amount) * self.t.amount_tolerant_band)
                        amt_component = 1.0 - self.t.fuzzy_in_band_penalty * min(1.0, d_amt / band)
                    elif fee:
                        amt_component = 0.85
                    else:
                        denom = max(abs(l.amount), 1.0)
                        amt_component = max(0.0, 1.0 - (d_amt / denom))
                    date_component = max(0.0, 1.0 - d_day / (self.t.date_window_fuzzy + 1))
                    score = (
                        self.t.fuzzy_w_ref * (ref_score / 100.0)
                        + self.t.fuzzy_w_amount * amt_component
                        + self.t.fuzzy_w_date * date_component
                    )
                    if score < self.t.fuzzy_min_score:
                        continue

                    ev = {
                        "ref_similarity": round(ref_score, 1),
                        "amount_delta": round(d_amt, 2),
                        "day_delta": d_day,
                        "blended_score": round(score, 3),
                        "ledger_ref": l.reference_number,
                        "bank_ref": b.reference_number,
                    }
                    if fee:
                        ev.update(fee)
                    # Reference agrees but the money does not: a short settlement.
                    # Worth calling out separately - it is the one case here where
                    # linking the rows is correct and still not the whole story.
                    if ref_score >= 99 and not amount_close and not fee:
                        ev["amount_discrepancy"] = True
                        ev["shortfall"] = round(abs(l.amount) - abs(b.amount), 2)
                        ev["shortfall_pct"] = round(
                            (abs(l.amount) - abs(b.amount)) / max(abs(l.amount), 1.0) * 100, 2
                        )
                    candidates.append((score, l.rec_id, b.rec_id, ev))

            # If a record's second-best candidate scores almost as well as its best,
            # the data does not actually decide between them. Say so rather than
            # letting a coin flip look like a match.
            runner_up: dict[str, list[float]] = {}
            for score, lid, sid, _ev in candidates:
                runner_up.setdefault(lid, []).append(score)
                runner_up.setdefault(sid, []).append(score)
            contested = set()
            for rec_id, scores in runner_up.items():
                if len(scores) > 1:
                    top, second = sorted(scores, reverse=True)[:2]
                    if top - second < self.t.ambiguity_margin:
                        contested.add(rec_id)

            for score, lid, sid, ev in self._greedy(candidates):
                if lid in contested or sid in contested:
                    ev["contested"] = True
                    ev["rival_count"] = max(len(runner_up.get(lid, [])),
                                            len(runner_up.get(sid, []))) - 1
                conf = min(self.t.conf_fuzzy_ceil, round(score, 3))
                if ev.get("contested"):
                    conf = min(conf, 0.45)
                if ev.get("contested"):
                    note = (
                        "The statement reference %s is short enough to fit %d other row(s) as "
                        "well as this one. The amount is the only thing that separates them, "
                        "and against %s it agrees %s. Worth a second pair of eyes."
                        % (ev["bank_ref"], ev["rival_count"], ev["ledger_ref"],
                           "to the paisa" if ev["amount_delta"] < 0.01
                           else "to within %.2f" % ev["amount_delta"])
                    )
                elif ev.get("amount_discrepancy"):
                    note = (
                        "Reference %s matches the statement exactly, but the bank settled "
                        "%.2f less than the ledger expected - %.1f%% short, which no fee "
                        "schedule explains." % (ev["ledger_ref"], ev["shortfall"],
                                                ev["shortfall_pct"])
                    )
                else:
                    note = (
                        "Reference %s on the ledger against %s on the statement is a %.0f%% "
                        "string match, and the amounts line up."
                        % (ev["ledger_ref"], ev["bank_ref"], ev["ref_similarity"])
                    )
                link = self._commit([lid], [sid], "fuzzy", "fuzzy_reference",
                                    conf, False, ev)
                self.exceptions.append(
                    Exception_(
                        exception_id=self._next_id("EX"),
                        kind="fuzzy_candidate",
                        ledger_ids=[lid],
                        stmt_ids=[sid],
                        engine_confidence=conf,
                        engine_note=note,
                        needs_llm=True,
                        link_id=link.link_id,
                        evidence=ev,
                    )
                )

        self._record_pass(
            "fuzzy", "Fuzzy reference",
            "Typo'd or truncated reference numbers scored against amount and date",
            t0, lb, eb,
        )

    # ======================================================================
    # Pass 6 - remainder
    # ======================================================================
    def _nearest(self, row: Record, pool: list[Record]) -> dict[str, Any] | None:
        best, best_score = None, -1.0
        for other in pool:
            d_day = _days(row.date, other.date)
            d_amt = abs(abs(row.amount) - abs(other.amount))
            ref = fuzz.WRatio(row.norm_ref, other.norm_ref) / 100.0
            score = 0.5 * ref + 0.3 * max(0.0, 1 - d_amt / max(abs(row.amount), 1.0)) \
                + 0.2 * max(0.0, 1 - d_day / 30)
            if score > best_score:
                best, best_score = other, score
        if best is None:
            return None
        return {
            "record": best.public(),
            "similarity": round(best_score, 3),
            "amount_delta": round(abs(abs(row.amount) - abs(best.amount)), 2),
            "day_delta": _days(row.date, best.date),
        }

    def pass_remainder(self) -> None:
        t0, lb, eb = time.perf_counter(), len(self.links), len(self.exceptions)

        free_l, free_b = self.free_ledger(), self.free_bank()
        for l in free_l:
            self.exceptions.append(
                Exception_(
                    exception_id=self._next_id("EX"),
                    kind="unmatched_ledger",
                    ledger_ids=[l.rec_id],
                    stmt_ids=[],
                    engine_confidence=self.t.conf_unresolved,
                    engine_note=(
                        "The ledger records %.2f from %s on %s with status '%s', and five "
                        "passes found nothing on the statement that could be it."
                        % (l.amount, l.counterparty, l.date, l.status)
                    ),
                    needs_llm=True,
                    evidence={"nearest_on_other_side": self._nearest(l, self.bank)},
                )
            )
        for b in free_b:
            self.exceptions.append(
                Exception_(
                    exception_id=self._next_id("EX"),
                    kind="unmatched_bank",
                    ledger_ids=[],
                    stmt_ids=[b.rec_id],
                    engine_confidence=self.t.conf_unresolved,
                    engine_note=(
                        "The statement shows %.2f on %s narrated '%s', and there is no ledger "
                        "entry behind it." % (b.amount, b.date, b.narration)
                    ),
                    needs_llm=True,
                    evidence={"nearest_on_other_side": self._nearest(b, self.ledger)},
                )
            )

        self._record_pass(
            "remainder", "Unresolved",
            "No plausible counterpart anywhere - a human has to look", t0, lb, eb,
        )

    # ======================================================================
    def run(self) -> None:
        self.pass_exact()
        # Reversals run before the general tolerant pass. A refund reference is a
        # near-superstring of the payment it cancels, so the tolerant pass would
        # happily match it as "same reference, rounded amount" - correct pairing,
        # but it throws away the fact that these two net to zero, which is the part
        # an audit desk actually cares about. Specific rule first, general rule after.
        self.pass_refund()
        self.pass_tolerant()
        self.pass_duplicates()
        self.pass_composite()
        self.pass_fuzzy()
        self.pass_remainder()


# ==========================================================================
# Loading + summarising
# ==========================================================================
def records_from_rows(rows: Iterable[dict[str, Any]], side: str) -> list[Record]:
    out = []
    for row in rows:
        if side == "ledger":
            out.append(
                Record(
                    rec_id=str(row["txn_id"]).strip(),
                    side="ledger",
                    date=str(row["date"]).strip()[:10],
                    amount=float(row["amount"]),
                    reference_number=str(row.get("reference_number", "")).strip(),
                    counterparty=str(row.get("counterparty", "")).strip(),
                    payment_method=str(row.get("payment_method", "")).strip(),
                    status=str(row.get("status", "")).strip(),
                )
            )
        else:
            out.append(
                Record(
                    rec_id=str(row["stmt_id"]).strip(),
                    side="bank",
                    date=str(row["date"]).strip()[:10],
                    amount=float(row["amount"]),
                    reference_number=str(row.get("reference_number", "")).strip(),
                    narration=str(row.get("narration", "")).strip(),
                    kind=str(row.get("type", "")).strip(),
                )
            )
    return out


def summarise(engine: Engine) -> dict[str, Any]:
    """Every number here is counted off the engine's own output. Nothing is asserted."""
    auto_l: set[str] = set()
    auto_b: set[str] = set()
    prop_l: set[str] = set()
    prop_b: set[str] = set()

    for link in engine.links:
        (auto_l if link.auto_resolved else prop_l).update(link.ledger_ids)
        (auto_b if link.auto_resolved else prop_b).update(link.stmt_ids)

    dup_l = {e.ledger_ids[0] for e in engine.exceptions if e.kind == "duplicate" and e.ledger_ids}
    dup_b = {e.stmt_ids[0] for e in engine.exceptions if e.kind == "duplicate" and e.stmt_ids}

    total_l, total_b = len(engine.ledger), len(engine.bank)
    total = total_l + total_b

    # Counted off unions of ids rather than by summing set lengths. Summing is
    # only correct while the buckets stay disjoint, and unresolved - derived by
    # subtraction - would absorb any double count silently, or go negative. A
    # union cannot double count, so the three buckets partition the batch by
    # construction. `accounting_overlap` reports a violation instead of hiding
    # one; it is 0 on both bundled datasets and asserted in the test suite.
    auto_ids = auto_l | auto_b | dup_l | dup_b
    proposed_ids = prop_l | prop_b
    resolved_ids = auto_ids | proposed_ids

    auto_records = len(auto_ids)
    proposed_records = len(proposed_ids)
    unresolved_records = total - len(resolved_ids)
    accounting_overlap = len(auto_ids & proposed_ids)

    value_total = sum(abs(r.amount) for r in engine.ledger + engine.bank)
    value_auto = sum(
        abs(engine.by_id[i].amount) for i in (auto_l | auto_b | dup_l | dup_b)
    )

    by_pass: dict[str, int] = {}
    for link in engine.links:
        by_pass[link.pass_name] = by_pass.get(link.pass_name, 0) + 1

    exc_by_kind: dict[str, int] = {}
    exception_ids: set[str] = set()
    for e in engine.exceptions:
        exc_by_kind[e.kind] = exc_by_kind.get(e.kind, 0) + 1
        exception_ids.update(e.ledger_ids)
        exception_ids.update(e.stmt_ids)

    return {
        "ledger_rows": total_l,
        "bank_rows": total_b,
        "total_records": total,
        "links_total": len(engine.links),
        "links_auto": sum(1 for l in engine.links if l.auto_resolved),
        "links_proposed": sum(1 for l in engine.links if not l.auto_resolved),
        "links_by_pass": by_pass,
        "duplicates_flagged": len(dup_l) + len(dup_b),
        "records_auto_resolved": auto_records,
        "records_proposed": proposed_records,
        "records_unresolved": unresolved_records,
        # auto + proposed + unresolved == total_records, always.
        "accounting_overlap": accounting_overlap,
        # The two headline numbers. The gap between them is what needs a human.
        "match_rate_auto": round(auto_records / total, 4) if total else 0.0,
        "match_rate_with_proposed": round(
            (auto_records + proposed_records) / total, 4
        ) if total else 0.0,
        "value_total": round(value_total, 2),
        "value_auto_resolved": round(value_auto, 2),
        "value_rate_auto": round(value_auto / value_total, 4) if value_total else 0.0,
        "exceptions_total": len(engine.exceptions),
        # An exception is a *group*, so this never belongs in the record
        # partition above - 52 exceptions cover 104 records on the standard
        # set. Carried explicitly so the two units are not read as one column.
        "exception_records": len(exception_ids),
        "exceptions_by_kind": exc_by_kind,
        "exceptions_needing_llm": sum(1 for e in engine.exceptions if e.needs_llm),
        "passes": [asdict(p) for p in engine.passes],
    }


def reconcile(ledger_rows, bank_rows,
              thresholds: Thresholds | None = None) -> tuple[Engine, dict[str, Any]]:
    engine = Engine(records_from_rows(ledger_rows, "ledger"),
                    records_from_rows(bank_rows, "bank"), thresholds)
    engine.run()
    return engine, summarise(engine)
