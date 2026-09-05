from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matching import (
    AUTO_RESOLVE_FLOOR,
    Engine,
    normalize_ref,
    records_from_rows,
    reconcile,
    reference_from_narration,
)


def L(txn_id, date, amount, ref, party="ACME TRADERS", status="captured", method="UPI"):
    return {"txn_id": txn_id, "date": date, "amount": amount, "counterparty": party,
            "payment_method": method, "reference_number": ref, "status": status}


def B(stmt_id, date, amount, ref, narration=None, kind="CREDIT"):
    return {"stmt_id": stmt_id, "date": date, "amount": amount, "reference_number": ref,
            "narration": narration if narration is not None else "UPI/%s/ACMETRADERS" % ref,
            "type": kind}


def run(ledger, bank):
    engine, summary = reconcile(ledger, bank)
    return engine, summary


def method_for(engine, ledger_id):
    for link in engine.links:
        if ledger_id in link.ledger_ids:
            return link.method
    return None


def link_for(engine, ledger_id):
    for link in engine.links:
        if ledger_id in link.ledger_ids:
            return link
    return None


def test_exact_match_auto_resolves():
    engine, summary = run([L("L1", "2026-07-01", 5000.00, "pay_ABC123XYZ789")],
                          [B("S1", "2026-07-01", 5000.00, "pay_ABC123XYZ789")])
    link = link_for(engine, "L1")
    assert link.stmt_ids == ["S1"]
    assert link.auto_resolved is True
    assert link.pass_name == "exact"
    assert summary["records_unresolved"] == 0


def test_exact_tolerates_one_rupee_and_two_days():
    engine, _ = run([L("L1", "2026-07-01", 5000.00, "pay_ABC123XYZ789")],
                    [B("S1", "2026-07-03", 5000.50, "pay_ABC123XYZ789")])
    assert link_for(engine, "L1").pass_name == "exact"


def test_reference_separators_are_ignored():
    assert normalize_ref("pay_Ab-12") == normalize_ref("PAYAB12") == "PAYAB12"


def test_gateway_fee_is_matched_and_the_rate_recorded():
    gross = 10000.00
    net = round(gross * (1 - 0.0236), 2)
    engine, _ = run([L("L1", "2026-07-01", gross, "pay_FEE0000000001")],
                    [B("S1", "2026-07-02", net, "pay_FEE0000000001")])
    link = link_for(engine, "L1")
    assert link.method == "fee_adjusted"
    assert link.auto_resolved is True
    assert link.evidence["fee_rate"] == pytest.approx(0.0236)
    assert link.evidence["fee_amount"] == pytest.approx(236.00, abs=0.02)


def test_t_plus_three_settlement_still_auto_resolves():
    engine, _ = run([L("L1", "2026-07-01", 7500.00, "pay_DELAY000000001")],
                    [B("S1", "2026-07-04", 7500.00, "pay_DELAY000000001")])
    link = link_for(engine, "L1")
    assert link.auto_resolved is True
    assert link.confidence >= AUTO_RESOLVE_FLOOR


def test_single_character_typo_auto_resolves():
    engine, _ = run([L("L1", "2026-07-01", 3200.00, "pay_QWERTY12345678")],
                    [B("S1", "2026-07-01", 3200.00, "pay_QWERTY12345679")])
    assert link_for(engine, "L1").auto_resolved is True


def test_late_settlement_needs_a_unique_reference_on_both_sides():
    engine, _ = run([L("L1", "2026-07-01", 9000.00, "pay_LATE0000000001")],
                    [B("S1", "2026-07-12", 9000.00, "pay_LATE0000000001")])
    link = link_for(engine, "L1")
    assert link.method == "late_settlement"
    assert link.evidence["day_delta"] == 11


def test_refund_pairs_back_to_its_payment():
    ledger = [L("L1", "2026-07-01", 4000.00, "pay_RF000000000001"),
              L("L2", "2026-07-06", -4000.00, "pay_RF000000000001-RFD", status="refunded")]
    bank = [B("S1", "2026-07-01", 4000.00, "pay_RF000000000001"),
            B("S2", "2026-07-06", -4000.00, "pay_RF000000000001",
              "REVERSAL/pay_RF000000000001/ACMETRADERS/REFUND", "DEBIT")]
    engine, summary = run(ledger, bank)
    refund = link_for(engine, "L2")
    assert refund.stmt_ids == ["S2"]
    assert refund.method == "refund_reversal"
    assert refund.evidence["nets_to_zero"] is True
    assert summary["records_unresolved"] == 0


def test_a_payment_is_never_matched_to_its_own_reversal():
    engine, _ = run([L("L1", "2026-07-01", 4000.00, "pay_SIGN000000001")],
                    [B("S1", "2026-07-01", -4000.00, "pay_SIGN000000001",
                       "REVERSAL/pay_SIGN000000001/ACME", "DEBIT")])
    assert link_for(engine, "L1") is None


def test_double_webhook_keeps_the_earlier_row_and_flags_the_later_one():
    ledger = [L("L1", "2026-07-01", 2500.00, "pay_DUP0000000001"),
              L("L2", "2026-07-01", 2500.00, "pay_DUP0000000001")]
    engine, summary = run(ledger, [B("S1", "2026-07-01", 2500.00, "pay_DUP0000000001")])
    assert link_for(engine, "L1").stmt_ids == ["S1"]
    assert link_for(engine, "L2") is None
    dupes = [e for e in engine.exceptions if e.kind == "duplicate"]
    assert [e.ledger_ids for e in dupes] == [["L2"]]
    assert dupes[0].needs_llm is False
    assert summary["duplicates_flagged"] == 1


def test_batched_settlement_is_proposed_not_auto_resolved():
    ledger = [L("L1", "2026-07-01", 1000.00, "pay_B0000000000001"),
              L("L2", "2026-07-01", 2000.00, "pay_B0000000000002"),
              L("L3", "2026-07-02", 3000.00, "pay_B0000000000003")]
    bank = [B("S1", "2026-07-03", 6000.00, "setl_BATCH00000001",
              "RAZORPAY SETTLEMENT setl_BATCH00000001 BATCH OF 3 ACMETRADERS")]
    engine, summary = run(ledger, bank)
    link = link_for(engine, "L1")
    assert sorted(link.ledger_ids) == ["L1", "L2", "L3"]
    assert link.auto_resolved is False, "a summed match is judgement, not proof"
    assert summary["records_proposed"] == 4
    composite = [e for e in engine.exceptions if e.kind == "composite_candidate"]
    assert len(composite) == 1 and composite[0].needs_llm is True


def test_unrelated_rows_are_not_summed_into_a_batch():
    ledger = [L("L1", "2026-07-01", 1000.00, "pay_X0000000000001", party="ALPHA CORP"),
              L("L2", "2026-07-01", 2000.00, "pay_X0000000000002", party="BETA LTD"),
              L("L3", "2026-07-01", 3000.00, "pay_X0000000000003", party="GAMMA LLP")]
    bank = [B("S1", "2026-07-02", 6000.00, "setl_ZZZZZZZZZZZZ",
              "RAZORPAY SETTLEMENT setl_ZZZZZZZZZZZZ BATCH OF 3 DELTAENTERPRISE")]
    engine, _ = run(ledger, bank)
    assert engine.links == []


def test_truncated_reference_is_proposed_with_capped_confidence():
    engine, _ = run([L("L1", "2026-07-01", 8400.00, "pay_TRUNC12345678")],
                    [B("S1", "2026-07-01", 8400.00, "pay_TRUNC1")])
    link = link_for(engine, "L1")
    assert link.pass_name == "fuzzy"
    assert link.auto_resolved is False
    assert link.confidence < AUTO_RESOLVE_FLOOR


def test_short_settlement_is_linked_but_never_auto_resolved():
    engine, _ = run([L("L1", "2026-07-01", 10000.00, "pay_SHORT000000001")],
                    [B("S1", "2026-07-02", 8000.00, "pay_SHORT000000001")])
    link = link_for(engine, "L1")
    assert link.stmt_ids == ["S1"]
    assert link.auto_resolved is False
    assert link.evidence["amount_discrepancy"] is True
    assert link.evidence["shortfall"] == pytest.approx(2000.00)


def test_two_equally_good_candidates_are_marked_contested():
    ledger = [L("L1", "2026-07-01", 5000.00, "pay_STEM0001AAAAA"),
              L("L2", "2026-07-01", 5001.00, "pay_STEM0001BBBBB")]
    bank = [B("S1", "2026-07-02", 5000.00, "pay_STEM0001"),
            B("S2", "2026-07-02", 5001.00, "pay_STEM0001")]
    engine, _ = run(ledger, bank)
    assert link_for(engine, "L1").stmt_ids == ["S1"]
    assert link_for(engine, "L2").stmt_ids == ["S2"]
    assert all(not l.auto_resolved for l in engine.links)


def test_orphans_stay_unresolved():
    ledger = [L("L1", "2026-07-01", 12345.67, "pay_ORPHANLEDGER01")]
    bank = [B("S1", "2026-07-20", 987.65, "pay_ORPHANBANK0001",
              "INTEREST CREDIT QTR ENDING 2026-07-20")]
    engine, summary = run(ledger, bank)
    assert engine.links == []
    assert summary["records_unresolved"] == 2
    kinds = sorted(e.kind for e in engine.exceptions)
    assert kinds == ["unmatched_bank", "unmatched_ledger"]
    assert all(e.needs_llm for e in engine.exceptions)


def test_unmatched_rows_carry_their_nearest_candidate_for_the_model():
    engine, _ = run([L("L1", "2026-07-01", 12345.67, "pay_AAAAAAAAAAAAAA")],
                    [B("S1", "2026-07-02", 99999.99, "pay_BBBBBBBBBBBBBB")])
    exc = next(e for e in engine.exceptions if e.kind == "unmatched_ledger")
    assert exc.evidence["nearest_on_other_side"]["record"]["id"] == "S1"


@pytest.mark.parametrize(
    "narration,expected",
    [
        ("UPI/pay_9xQm2LtVb4Kd/ACMETRADERS/PAYMENT", "pay_9xQm2LtVb4Kd"),
        ("NEFT-HDFC-pay_ABCdef123456-SOMECO", "pay_ABCdef123456"),
        ("RAZORPAY SETTLEMENT setl_JJ4K2mQ9 BATCH OF 3 ACME", "setl_JJ4K2mQ9"),
        ("BANK CHARGES MONTHLY MAINTENANCE", None),
    ],
)
def test_reference_mined_out_of_narration(narration, expected):
    assert reference_from_narration(narration) == expected


def test_blank_reference_column_still_matches_via_narration():
    engine, _ = run([L("L1", "2026-07-01", 6600.00, "pay_INNARRATION01")],
                    [B("S1", "2026-07-02", 6600.00, "",
                       "UPI/pay_INNARRATION01/ACMETRADERS/PAYMENT")])
    link = link_for(engine, "L1")
    assert link is not None and link.auto_resolved is True
    assert engine.by_id["S1"].ref_source == "narration"


def test_every_record_lands_in_exactly_one_bucket():
    ledger = [L("L%d" % i, "2026-07-01", 1000.0 + i, "pay_ACC%011d" % i) for i in range(1, 6)]
    bank = [B("S%d" % i, "2026-07-01", 1000.0 + i, "pay_ACC%011d" % i) for i in range(1, 4)]
    _engine, summary = run(ledger, bank)
    assert (summary["records_auto_resolved"] + summary["records_proposed"]
            + summary["records_unresolved"]) == summary["total_records"] == 8


def test_no_record_is_used_twice():
    ledger = [L("L1", "2026-07-01", 5000.00, "pay_ONCE0000000001"),
              L("L2", "2026-07-01", 5000.00, "pay_ONCE0000000002")]
    bank = [B("S1", "2026-07-01", 5000.00, "pay_ONCE0000000001")]
    engine, _ = run(ledger, bank)
    used = [i for link in engine.links for i in link.ledger_ids + link.stmt_ids]
    assert len(used) == len(set(used))


def test_empty_input_does_not_explode():
    engine, summary = run([], [])
    assert engine.links == [] and summary["total_records"] == 0
    assert summary["match_rate_auto"] == 0.0
    assert len(summary["passes"]) == 7


@pytest.mark.parametrize("profile", ["standard", "stress"])
def test_record_partition_holds_on_the_bundled_data(profile):
    from app.dataio import load_bundled
    from app.matching import reconcile

    ledger, bank = load_bundled(profile)
    _engine, summary = reconcile(ledger, bank)

    assert (summary["records_auto_resolved"] + summary["records_proposed"]
            + summary["records_unresolved"]) == summary["total_records"]
    assert summary["records_unresolved"] >= 0
    assert summary["accounting_overlap"] == 0


@pytest.mark.parametrize("profile", ["standard", "stress"])
def test_exceptions_are_groups_and_are_not_part_of_the_record_partition(profile):
    from app.dataio import load_bundled
    from app.matching import reconcile

    ledger, bank = load_bundled(profile)
    engine, summary = reconcile(ledger, bank)

    covered = set()
    for exc in engine.exceptions:
        covered.update(exc.ledger_ids)
        covered.update(exc.stmt_ids)

    assert summary["exception_records"] == len(covered)
    assert summary["exception_records"] > summary["exceptions_total"]
    assert summary["duplicates_flagged"] > 0
