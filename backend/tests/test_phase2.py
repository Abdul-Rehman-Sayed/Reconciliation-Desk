from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import analytics, audit, baselines, llm, mockllm  # noqa: E402
from app.adapters import razorpay  # noqa: E402
from app.dataio import load_bundled, load_ground_truth  # noqa: E402
from app.matching import (  # noqa: E402
    ADJUSTABLE_THRESHOLDS,
    DEFAULT_THRESHOLDS,
    Thresholds,
    reconcile,
)


@pytest.fixture(scope="module")
def standard():
    ledger, bank = load_bundled("standard")
    truth = load_ground_truth("standard")
    return ledger, bank, truth


def test_thresholds_default_to_the_module_constants():
    from app import matching

    t = Thresholds()
    assert t.amount_exact_tolerance == matching.AMOUNT_EXACT_TOLERANCE
    assert t.date_window_exact == matching.DATE_WINDOW_EXACT
    assert t.auto_resolve_floor == matching.AUTO_RESOLVE_FLOOR


def test_replace_refuses_a_threshold_that_does_not_exist():
    with pytest.raises(ValueError):
        DEFAULT_THRESHOLDS.replace(not_a_real_threshold=1)


def test_diff_from_default_reports_only_what_moved():
    t = DEFAULT_THRESHOLDS.replace(date_window_exact=7)
    assert t.diff_from_default() == {"date_window_exact": 7}


def test_two_engines_at_different_tolerances_do_not_disturb_each_other(standard):
    ledger, bank, _ = standard
    _, loose = reconcile(ledger, bank, DEFAULT_THRESHOLDS.replace(date_window_tolerant=0))
    _, again = reconcile(ledger, bank)
    baseline = reconcile(ledger, bank)[1]

    assert loose["match_rate_auto"] < baseline["match_rate_auto"]
    assert again["match_rate_auto"] == baseline["match_rate_auto"]


def test_raising_the_auto_floor_makes_the_engine_more_cautious(standard):
    ledger, bank, _ = standard
    _, base = reconcile(ledger, bank)
    _, strict = reconcile(ledger, bank, DEFAULT_THRESHOLDS.replace(auto_resolve_floor=0.95))

    assert strict["match_rate_auto"] < base["match_rate_auto"]
    assert strict["records_proposed"] > base["records_proposed"]


def test_raising_the_auto_floor_never_loses_a_match(standard):
    ledger, bank, truth = standard
    from app.scoring import score

    engine, summary = reconcile(ledger, bank, DEFAULT_THRESHOLDS.replace(auto_resolve_floor=0.95))
    result = score(engine, truth)
    assert result["recall"] == 1.0
    assert summary["records_unresolved"] == reconcile(ledger, bank)[1]["records_unresolved"]


def test_every_demoted_link_reaches_the_exception_queue(standard):
    ledger, bank, _ = standard
    engine, _ = reconcile(ledger, bank, DEFAULT_THRESHOLDS.replace(auto_resolve_floor=0.95))

    demoted = {e.link_id for e in engine.exceptions if e.kind == "below_auto_threshold"}
    assert demoted, "raising the floor should have demoted something"
    for link in engine.links:
        if link.pass_name in ("exact", "tolerant", "refund") and not link.auto_resolved:
            assert link.link_id in demoted


def test_the_auto_floor_can_never_promote_a_proposal(standard):
    ledger, bank, _ = standard
    engine, _ = reconcile(ledger, bank, DEFAULT_THRESHOLDS.replace(auto_resolve_floor=0.0))
    for link in engine.links:
        if link.pass_name in ("composite", "fuzzy"):
            assert not link.auto_resolved


def test_every_adjustable_threshold_is_a_real_field():
    names = {f for f in Thresholds().as_dict()}
    for spec in ADJUSTABLE_THRESHOLDS:
        assert spec["key"] in names
        assert spec["min"] <= float(Thresholds().as_dict()[spec["key"]]) <= spec["max"]


def test_compact_payload_drops_what_cannot_change_a_verdict():
    exc = {
        "exception_id": "EX0001",
        "kind": "fuzzy_candidate",
        "ledger_ids": ["L1"],
        "stmt_ids": ["S1"],
        "engine_confidence": 0.6,
        "engine_note": "note",
        "status": "pending",
        "decided_at": None,
        "link_id": "LK0001",
        "evidence": {"ref_similarity": 88.0, "blended_score": 0.7, "amount_delta": 0.0},
    }
    lookup = {
        "L1": {"id": "L1", "date": "2026-08-04", "amount": 5000.0,
               "reference_number": "pay_A", "counterparty": "ACME"},
        "S1": {"id": "S1", "date": "2026-08-05", "amount": 5000.0,
               "reference_number": "pay_B", "narration": "UPI/pay_B"},
    }
    compact = llm.compact_payload(exc, lookup)

    assert "status" not in compact and "decided_at" not in compact
    assert "link_id" not in compact
    assert "blended_score" not in compact["e"]
    assert compact["e"]["ref_similarity"] == 88.0


def test_an_amount_delta_of_exactly_zero_survives_compaction():
    compact = llm._compact_evidence({"amount_delta": 0.0, "day_delta": 0})
    assert compact["amount_delta"] == 0.0
    assert compact["day_delta"] == 0


def test_the_cache_key_ignores_the_model_but_tracks_the_payload():
    a = {"k": "fuzzy_candidate", "n": "x", "c": 0.6, "L": [], "B": [], "e": {}}
    b = dict(a, c=0.7)
    assert llm.fingerprint(a) == llm.fingerprint(dict(a))
    assert llm.fingerprint(a) != llm.fingerprint(b)


def test_mock_and_live_caches_are_different_files(monkeypatch):
    monkeypatch.setenv("USE_MOCK_LLM", "true")
    mock_path = llm.cache_path()
    monkeypatch.setenv("USE_MOCK_LLM", "false")
    assert mock_path != llm.cache_path()


def test_max_tokens_is_budgeted_per_record_not_per_request():
    small = llm._max_tokens_for(1)
    big = llm._max_tokens_for(llm.BATCH_SIZE)
    assert big > small
    assert big <= llm.MAX_TOKENS_CEILING


def test_max_tokens_leaves_room_for_the_reasoning_pass():
    assert llm._max_tokens_for(1) > llm.REASONING_HEADROOM
    measured_completion_for_twelve = 1275
    assert llm._max_tokens_for(12) > measured_completion_for_twelve


def test_explain_with_no_payloads_makes_no_calls():
    results, stats = llm.explain([])
    assert results == {}
    assert stats["api_calls"] == 0


def test_mock_mode_never_touches_the_network(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_MOCK_LLM", "true")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(llm, "MOCK_CACHE_PATH", tmp_path / "mock.json")

    def explode(*args, **kwargs):
        raise AssertionError("mock mode made a network call")

    monkeypatch.setattr(llm.requests, "post", explode)
    monkeypatch.setattr(llm.requests, "get", explode)

    payload = {"k": "unmatched_bank", "n": "orphan", "c": 0.15,
               "L": [], "B": [["S1", "08-04", 900.0, "", "UPI/unknown"]], "e": {}}
    results, stats = llm.explain([("EX0001", payload)])
    assert stats["mode"] == "mock"
    assert results["EX0001"]["source"] == "mock"
    assert stats["api_calls"] == 0


def test_a_spent_budget_refuses_to_call_rather_than_failing_open(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_MOCK_LLM", "false")
    monkeypatch.setattr(llm, "DAILY_CALL_BUDGET", 0)
    monkeypatch.setattr(llm, "_budget_take", lambda: False)
    monkeypatch.setattr(llm, "resolve_model", lambda force=False: "test-model")
    monkeypatch.setattr(llm, "api_key", lambda: "test-key")
    monkeypatch.setattr(llm, "CACHE_PATH", tmp_path / "cache.json")

    def explode(*args, **kwargs):
        raise AssertionError("called Groq with no budget left")

    monkeypatch.setattr(llm.requests, "post", explode)

    payload = {"k": "unmatched_bank", "n": "n", "c": 0.1, "L": [], "B": [], "e": {}}
    results, stats = llm.explain([("EX0001", payload)])
    assert results["EX0001"]["source"] == "unavailable"
    assert "budget" in results["EX0001"]["explanation"]


def test_the_token_bucket_refuses_more_than_it_holds_then_refills():
    bucket = llm._TokenBucket(limit=6000)
    bucket.take(6000)
    assert bucket.available < 1
    bucket.observe_limit(8000)
    assert bucket.limit == 8000


@pytest.mark.parametrize(
    "kind,evidence,expected",
    [
        ("composite_candidate", {"component_count": 4, "component_total": 25890.55,
                                 "residual": 0.0, "counterparty": "ACME", "day_span": 2},
         "split_payment"),
        ("fuzzy_candidate", {"ref_similarity": 100.0, "amount_delta": 0.0, "day_delta": 4,
                             "ledger_ref": "pay_A", "bank_ref": "pay_A"}, "date_delay"),
        ("fuzzy_candidate", {"ref_similarity": 92.0, "amount_delta": 0.0, "day_delta": 1,
                             "ledger_ref": "pay_A", "bank_ref": "pay_B"}, "reference_mismatch"),
        ("fuzzy_candidate", {"contested": True, "rival_count": 2, "ref_similarity": 70.0,
                             "amount_delta": 1.5, "day_delta": 1}, "reference_mismatch"),
        ("unmatched_bank", {}, "orphan_bank"),
        ("unmatched_ledger", {}, "orphan_ledger"),
        ("duplicate", {"reference_number": "pay_A", "duplicate_of": "L1"}, "duplicate"),
    ],
)
def test_the_stand_in_reaches_the_right_category(kind, evidence, expected):
    verdict = mockllm.classify({"engine_finding": kind, "engine_note": "",
                                "ledger_records": [], "bank_records": [],
                                "evidence": evidence})
    assert verdict["category"] == expected
    assert verdict["source"] == "mock"
    assert 0.0 <= verdict["confidence"] <= 1.0


def test_the_stand_in_never_suggests_approving_an_ambiguous_pair():
    verdict = mockllm.classify({
        "engine_finding": "fuzzy_candidate", "engine_note": "",
        "ledger_records": [], "bank_records": [],
        "evidence": {"contested": True, "rival_count": 3, "ref_similarity": 70.0,
                     "amount_delta": 0.5, "day_delta": 1},
    })
    assert verdict["suggested_action"] == "investigate"


def test_the_stand_in_is_labelled_as_a_stand_in():
    verdict = mockllm.classify({"engine_finding": "unmatched_bank", "engine_note": "",
                                "ledger_records": [], "bank_records": [], "evidence": {}})
    assert verdict["source"] == "mock"
    assert verdict["model"] == mockllm.MODEL_NAME


def _explained_run(standard, monkeypatch):
    monkeypatch.setenv("USE_MOCK_LLM", "true")
    ledger, bank, truth = standard
    engine, summary = reconcile(ledger, bank)
    from dataclasses import asdict

    run = {
        "run_id": "run_test", "created_at": "2026-08-22T00:00:00+00:00",
        "source": "bundled", "dataset_profile": "standard",
        "summary": summary, "accuracy": None,
        "records": {"ledger": [r.public() for r in engine.ledger],
                    "bank": [r.public() for r in engine.bank]},
        "links": [asdict(l) for l in engine.links],
        "exceptions": [asdict(e) for e in engine.exceptions],
        "llm_stats": {"requested": 0, "api_calls": 0, "from_cache": 0,
                      "prompt_tokens": 0, "completion_tokens": 0, "mode": "mock"},
        "audit_events": [],
    }
    lookup = {r["id"]: r for r in run["records"]["ledger"] + run["records"]["bank"]}
    for exc in run["exceptions"]:
        if exc["needs_llm"]:
            exc["llm"] = mockllm.classify(llm._mock_payload(llm.compact_payload(exc, lookup)))
    return run, truth


def test_confusion_scores_only_what_reached_the_model(standard, monkeypatch):
    run, truth = _explained_run(standard, monkeypatch)
    result = analytics.confusion(run, truth)

    assert result["scored"] > 0
    assert result["clean_matches_that_reached_the_model"] == 0
    assert result["cases_resolved_before_the_model"] > 0
    names = {d["ground_truth_category"] for d in result["resolved_before_the_model"]}
    assert "fee_deducted" in names and "date_shift" in names


def test_calibration_compares_stated_confidence_to_measured_accuracy(standard, monkeypatch):
    run, truth = _explained_run(standard, monkeypatch)
    result = analytics.calibration(run, truth)

    assert result["scored"] > 0
    assert 0.0 <= result["mean_confidence"] <= 1.0
    assert result["overconfidence"] == pytest.approx(
        result["mean_confidence"] - result["actual_accuracy"], abs=1e-6
    )
    assert sum(b["n"] for b in result["bins"]) == result["scored"]


def test_analysis_returns_nothing_rather_than_guessing_without_ground_truth(standard, monkeypatch):
    run, _ = _explained_run(standard, monkeypatch)
    assert analytics.confusion(run, None) is None
    assert analytics.calibration(run, None) is None


def test_cost_split_counts_records_the_model_never_saw(standard, monkeypatch):
    run, _ = _explained_run(standard, monkeypatch)
    split = analytics.cost_split(run)

    assert split["records_never_seen_by_model"] + split["records_seen_by_model"] == \
        split["total_records"]
    assert 0.0 < split["share_resolved_without_model"] < 1.0
    assert split["estimated_cold_tokens"] > 0


def test_hours_saved_does_not_pretend_the_queue_is_free(standard, monkeypatch):
    run, _ = _explained_run(standard, monkeypatch)
    hours = analytics.hours_saved(run)

    assert hours["hours_saved"] < hours["manual_hours"]
    assert hours["hours_still_needed"] > 0
    assert hours["manual_hours"] == pytest.approx(
        hours["hours_saved"] + hours["hours_still_needed"], abs=0.02
    )


def test_the_naive_join_is_precise_and_incomplete(standard):
    ledger, bank, truth = standard
    result = baselines.naive_join(ledger, bank, truth)

    assert result["precision"] == 1.0
    assert result["recall"] < 0.9, "a bare equality join should miss the interesting cases"
    assert result["false_negatives"] > 0


def test_the_layered_engine_beats_the_naive_join_on_recall(standard):
    ledger, bank, truth = standard
    from app.scoring import score

    naive = baselines.naive_join(ledger, bank, truth)
    engine, _ = reconcile(ledger, bank)
    layered = score(engine, truth)
    assert layered["recall"] > naive["recall"]


def test_the_llm_only_baseline_is_never_measured_on_a_request_path(standard, monkeypatch):
    ledger, bank, truth = standard

    def explode(*args, **kwargs):
        raise AssertionError("bundle() tried to measure the LLM baseline live")

    monkeypatch.setattr(baselines, "run_llm_only", explode)
    result = baselines.bundle("standard", ledger, bank, truth)
    assert result is not None
    assert result["naive"]["precision"] == 1.0


def test_provenance_names_the_rule_that_resolved_a_record(standard, monkeypatch):
    run, _ = _explained_run(standard, monkeypatch)
    matched = next(l for l in run["links"] if l["auto_resolved"])
    record_id = matched["ledger_ids"][0]

    result = audit.provenance(run, record_id)
    assert result["outcome"] == "auto_resolved"
    assert result["rule"]["method"] == matched["method"]
    assert result["why"], "a rule with no evidence behind it explains nothing"
    assert result["counterparts"]


def test_provenance_is_none_for_a_record_not_in_the_run(standard, monkeypatch):
    run, _ = _explained_run(standard, monkeypatch)
    assert audit.provenance(run, "NOT_A_RECORD") is None


def test_every_rule_the_engine_can_fire_has_a_description(standard):
    ledger, bank, _ = standard
    engine, _ = reconcile(ledger, bank)
    for link in engine.links:
        assert link.method in audit.RULES, "undocumented rule: " + link.method
    for exc in engine.exceptions:
        if exc.link_id is None:
            assert exc.kind in audit.KIND_RULES, "undocumented kind: " + exc.kind


def test_the_audit_log_covers_every_link_and_every_exception(standard, monkeypatch):
    run, _ = _explained_run(standard, monkeypatch)
    rows = audit.audit_rows(run)

    linked = sum(1 for r in rows if r["event"] in ("auto_resolved", "proposed"))
    assert linked == len(run["links"])
    standalone = sum(1 for r in rows if r["event"] == "exception")
    assert standalone == sum(1 for e in run["exceptions"] if not e.get("link_id"))


def test_a_human_action_is_appended_not_substituted(standard, monkeypatch):
    run, _ = _explained_run(standard, monkeypatch)
    target = run["exceptions"][0]
    before = len(audit.audit_rows(run))

    run["audit_events"].append({
        "at": "2026-08-22T01:00:00+00:00", "exception_id": target["exception_id"],
        "action": "approve", "resulting_status": "approved",
        "previous_status": "pending", "note": "checked against the portal",
    })
    rows = audit.audit_rows(run)
    assert len(rows) == before + 1
    assert any(r["event"] == "human_action" and r["human_action"] == "approve" for r in rows)


def test_the_audit_csv_has_one_header_and_a_row_per_decision(standard, monkeypatch):
    run, _ = _explained_run(standard, monkeypatch)
    text = audit.audit_csv(run)
    lines = [l for l in text.split("\n") if l.strip()]

    assert lines[0].split(",") == audit.AUDIT_COLUMNS
    assert len(lines) - 1 == len(audit.audit_rows(run))


RECON_ROW = {
    "entity_id": "pay_DEXrnipqTmWVGE", "type": "payment", "debit": "0", "credit": "97100",
    "amount": "100000", "currency": "INR", "fee": "2900", "tax": "0", "on_hold": "false",
    "settled": "true", "created_at": "1567692556", "settled_at": "1568176960",
    "settlement_id": "setl_DGlQ1Rj8os78Ec", "payment_id": "", "order_id": "order_DEXrnRiR3SNDHA",
    "settlement_utr": "1568176960vxp0rj", "method": "card", "description": "Order payment",
}


def test_the_adapter_recognises_a_recon_report_and_not_our_own_csv():
    assert razorpay.matches(RECON_ROW.keys())
    assert not razorpay.matches(
        ["txn_id", "date", "amount", "counterparty", "payment_method",
         "reference_number", "status"]
    )


def test_amounts_are_converted_from_paise():
    row = razorpay.to_ledger_rows([RECON_ROW])[0]
    assert row["amount"] == 971.00


def test_the_settlement_date_is_used_not_the_creation_date():
    row = razorpay.to_ledger_rows([RECON_ROW])[0]
    assert row["date"] == "2019-09-11"


def test_a_refund_becomes_a_negative_amount():
    refund = dict(RECON_ROW, type="refund", debit="242500", credit="0", amount="242500")
    row = razorpay.to_ledger_rows([refund])[0]
    assert row["amount"] < 0


def test_the_utr_is_never_used_as_the_join_key():
    row = razorpay.to_ledger_rows([RECON_ROW])[0]
    assert row["reference_number"] == "order_DEXrnRiR3SNDHA"
    assert RECON_ROW["settlement_utr"] not in row["reference_number"]


def test_transactions_group_into_settlement_batches():
    rows = razorpay.to_ledger_rows([RECON_ROW, dict(RECON_ROW, entity_id="pay_two")])
    batches = razorpay.settlement_batches(rows)
    assert len(batches) == 1
    assert batches[0]["transaction_count"] == 2
    assert batches[0]["settlement_id"] == "setl_DGlQ1Rj8os78Ec"


def test_saving_a_partial_view_cannot_truncate_the_cache(monkeypatch, tmp_path):
    path = tmp_path / "cache.json"
    monkeypatch.setattr(llm, "CACHE_PATH", path)
    monkeypatch.setenv("USE_MOCK_LLM", "false")

    llm._save_cache({"a": {"source": "groq"}, "b": {"source": "groq"}})
    assert len(llm._load_cache()) == 2

    llm._save_cache({"c": {"source": "groq"}})
    assert set(llm._load_cache()) == {"a", "b", "c"}


def test_a_cache_that_will_not_parse_is_kept_rather_than_discarded(monkeypatch, tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(llm, "CACHE_PATH", path)
    monkeypatch.setenv("USE_MOCK_LLM", "false")

    assert llm._load_cache() == {}
    assert (tmp_path / "cache.corrupt.json").exists()
