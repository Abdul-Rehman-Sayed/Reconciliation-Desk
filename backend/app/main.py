from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import analytics, audit, baselines, llm, store
from .dataio import (
    PROFILES,
    CsvShapeError,
    describe_profiles,
    load_bundled,
    load_ground_truth,
    read_bank,
    read_ledger_described,
)
from .matching import (
    ADJUSTABLE_THRESHOLDS,
    DEFAULT_THRESHOLDS,
    Engine,
    Thresholds,
    records_from_rows,
    summarise,
)
from .scoring import score

app = FastAPI(title="Reconciliation Agent", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                   "http://localhost:4173", "http://127.0.0.1:4173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ActionRequest(BaseModel):
    action: Literal["approve", "reject", "investigate"]
    note: str | None = Field(default=None, max_length=500)


class ThresholdRequest(BaseModel):
    overrides: dict[str, float] = Field(default_factory=dict)
    commit: bool = False


class ReconcileResponse(BaseModel):
    run_id: str
    summary: dict[str, Any]
    accuracy: dict[str, Any] | None
    reused: bool = False
    adapter: dict[str, Any] | None = None


def _require(run_id: str) -> dict[str, Any]:
    try:
        run = store.load(run_id)
    except ValueError:
        raise HTTPException(400, "Malformed run id")
    if run is None:
        raise HTTPException(404, "No run with id " + run_id)
    return run


RUN_SCHEMA_VERSION = 2


def _fingerprint(ledger_rows, bank_rows, thresholds: Thresholds) -> str:
    blob = json.dumps(
        {"l": ledger_rows, "b": bank_rows, "t": thresholds.as_dict(),
         "v": RUN_SCHEMA_VERSION},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def _thresholds_from(overrides: dict[str, float] | None) -> Thresholds:
    if not overrides:
        return DEFAULT_THRESHOLDS
    allowed = {spec["key"] for spec in ADJUSTABLE_THRESHOLDS}
    unknown = set(overrides) - allowed
    if unknown:
        raise HTTPException(
            422,
            "Not adjustable: %s. Adjustable thresholds are: %s"
            % (", ".join(sorted(unknown)), ", ".join(sorted(allowed))),
        )
    clean: dict[str, Any] = {}
    for spec in ADJUSTABLE_THRESHOLDS:
        key = spec["key"]
        if key not in overrides:
            continue
        value = float(overrides[key])
        if not (spec["min"] <= value <= spec["max"]):
            raise HTTPException(
                422, "%s must be between %s and %s" % (key, spec["min"], spec["max"])
            )

        clean[key] = int(round(value)) if spec["unit"] == "days" else value
    try:
        return DEFAULT_THRESHOLDS.replace(**clean)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _build_run(ledger_rows, bank_rows, source: str, profile: str,
               truth: dict[str, Any] | None,
               thresholds: Thresholds = DEFAULT_THRESHOLDS,
               adapter: dict[str, Any] | None = None) -> dict[str, Any]:
    engine = Engine(records_from_rows(ledger_rows, "ledger"),
                    records_from_rows(bank_rows, "bank"), thresholds)
    engine.run()
    summary = summarise(engine)

    accuracy = None
    if truth is not None:
        try:
            accuracy = score(engine, truth)
        except (KeyError, TypeError):
            accuracy = None

    return {
        "run_id": store.new_run_id(),
        "created_at": store.now(),
        "source": source,
        "dataset_profile": profile,
        "fingerprint": _fingerprint(ledger_rows, bank_rows, thresholds),
        "thresholds": thresholds.as_dict(),
        "thresholds_changed": thresholds.diff_from_default(),
        "adapter": adapter,
        "summary": summary,
        "accuracy": accuracy,
        "records": {
            "ledger": [r.public() for r in engine.ledger],
            "bank": [r.public() for r in engine.bank],
        },
        "links": [asdict(l) for l in engine.links],
        "exceptions": [asdict(e) for e in engine.exceptions],
        "llm_complete": False,
        "llm_stats": None,
        "audit_events": [],
    }


def _record_lookup(run: dict[str, Any]) -> dict[str, Any]:
    return {r["id"]: r for r in run["records"]["ledger"] + run["records"]["bank"]}


def _payloads_for(run: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    lookup = _record_lookup(run)
    return [
        (exc["exception_id"], llm.compact_payload(exc, lookup))
        for exc in run["exceptions"] if exc.get("needs_llm")
    ]


def _decisions(run: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for exc in run["exceptions"]:
        out[exc["status"]] = out.get(exc["status"], 0) + 1
    return out


def _apply_verdicts(run: dict[str, Any], results: dict[str, Any],
                    stats: dict[str, Any]) -> int:
    for exc in run["exceptions"]:
        if exc["exception_id"] in results:
            exc["llm"] = results[exc["exception_id"]]

    missing = [exc for exc in run["exceptions"]
               if exc.get("needs_llm")
               and (exc.get("llm") or {}).get("source") not in ("groq", "mock")]
    run["llm_complete"] = not missing
    run["llm_stats"] = stats
    store.save(run)
    return len(missing)


def _truth_for(run: dict[str, Any]) -> dict[str, Any] | None:
    if run.get("source") != "bundled":
        return None
    try:
        return load_ground_truth(run["dataset_profile"])
    except ValueError:
        return None


@app.get("/api/health")
def health() -> dict[str, Any]:
    info: dict[str, Any] = {
        "status": "ok",
        "mock_mode": llm.use_mock(),
        "groq_key_present": llm.api_key() is not None,
        "cache": llm.cache_stats(),
        "budget": llm.budget_status(),
    }
    if info["mock_mode"]:
        info.update(groq_reachable=False, groq_model=llm.mockllm.MODEL_NAME,
                    groq_error="USE_MOCK_LLM is on; no live calls will be made")
        return info
    if info["groq_key_present"]:
        try:
            info["groq_model"] = llm.resolve_model()
            info["groq_reachable"] = True
        except Exception as exc:
            info["groq_reachable"] = False
            info["groq_error"] = str(exc)[:300]
    else:
        info["groq_reachable"] = False
        info["groq_error"] = "GROQ_API_KEY is not set in backend/.env"
    return info


@app.get("/api/datasets")
def datasets() -> dict[str, Any]:
    return {"datasets": describe_profiles()}


@app.get("/api/models")
def models() -> dict[str, Any]:
    if llm.use_mock():
        return {"available": [llm.mockllm.MODEL_NAME], "preference": llm.MODEL_PREFERENCE,
                "selected": llm.mockllm.MODEL_NAME, "mock_mode": True}
    try:
        available = llm.list_models()
    except Exception as exc:
        raise HTTPException(503, str(exc)[:300])
    return {"available": available, "preference": llm.MODEL_PREFERENCE,
            "selected": llm.resolve_model(), "mock_mode": False}


@app.get("/api/thresholds")
def thresholds() -> dict[str, Any]:
    return {
        "adjustable": list(ADJUSTABLE_THRESHOLDS),
        "defaults": DEFAULT_THRESHOLDS.as_dict(),
        "note": (
            "Moving these re-runs the deterministic passes only. Re-classification is "
            "free and takes about a tenth of a second; no model call is made for a "
            "threshold change."
        ),
    }


@app.post("/api/reconcile", response_model=ReconcileResponse)
async def reconcile(
    dataset: str = Query(default="standard"),
    force: bool = Query(default=False, description="recompute even if an identical run exists"),
    ledger: UploadFile | None = File(default=None),
    bank_statement: UploadFile | None = File(default=None),
) -> ReconcileResponse:
    adapter_note: dict[str, Any] | None = None

    if ledger is not None and bank_statement is not None:
        try:
            ledger_rows, adapter_note = read_ledger_described(await ledger.read())
            bank_rows = read_bank(await bank_statement.read())
        except CsvShapeError as exc:
            raise HTTPException(422, str(exc))
        except Exception as exc:
            raise HTTPException(422, "Could not read those CSVs: " + str(exc)[:200])
        source, profile, truth = "upload", "uploaded", None
    elif ledger is not None or bank_statement is not None:
        raise HTTPException(422, "Upload both files, or neither to use the bundled sample.")
    else:
        if dataset not in PROFILES:
            raise HTTPException(422, "Unknown dataset '%s'. Available: %s"
                                % (dataset, ", ".join(PROFILES)))
        ledger_rows, bank_rows = load_bundled(dataset)
        truth = load_ground_truth(dataset)
        source, profile = "bundled", dataset

    fingerprint = _fingerprint(ledger_rows, bank_rows, DEFAULT_THRESHOLDS)
    if not force:
        existing = store.find_by_fingerprint(fingerprint)
        if existing is not None:
            return ReconcileResponse(
                run_id=existing["run_id"], summary=existing["summary"],
                accuracy=existing["accuracy"], reused=True,
                adapter=existing.get("adapter"),
            )

    run = _build_run(ledger_rows, bank_rows, source, profile, truth,
                     DEFAULT_THRESHOLDS, adapter_note)
    store.save(run)
    return ReconcileResponse(run_id=run["run_id"], summary=run["summary"],
                             accuracy=run["accuracy"], reused=False,
                             adapter=adapter_note)


@app.post("/api/runs/{run_id}/explain")
def explain(run_id: str, dry_run: bool = Query(default=False)) -> dict[str, Any]:
    run = _require(run_id)
    payloads = _payloads_for(run)

    if dry_run:
        cache = llm._load_cache()
        cached = sum(1 for _, compact in payloads if llm.fingerprint(compact) in cache)
        uncached = len(payloads) - cached
        return {
            "run_id": run_id,
            "dry_run": True,
            "exceptions": len(payloads),
            "already_cached": cached,
            "would_call_for": uncached,
            "would_cost_requests": -(-uncached // llm.BATCH_SIZE),
            "batch_size": llm.BATCH_SIZE,
            "mode": "mock" if llm.use_mock() else "groq",
            "budget": llm.budget_status(),
        }

    if run.get("llm_complete"):
        return {"run_id": run_id, "llm_stats": run.get("llm_stats"), "already_done": True}

    results, stats = llm.explain(payloads)
    unanswered = _apply_verdicts(run, results, stats)
    return {"run_id": run_id, "llm_stats": stats,
            "explained": len(results), "unanswered": unanswered,
            "already_done": False}


@app.post("/api/runs/{run_id}/thresholds")
def rethreshold(run_id: str, body: ThresholdRequest) -> dict[str, Any]:
    parent = _require(run_id)
    if parent.get("source") != "bundled":
        raise HTTPException(
            422,
            "Threshold preview runs on bundled datasets, where the original rows are "
            "still on disk. This run came from an upload.",
        )

    thresholds = _thresholds_from(body.overrides)
    profile = parent["dataset_profile"]
    ledger_rows, bank_rows = load_bundled(profile)
    truth = load_ground_truth(profile)

    run = _build_run(ledger_rows, bank_rows, "bundled", profile, truth, thresholds)
    run["derived_from"] = run_id

    cache = llm._load_cache()
    lookup = _record_lookup(run)
    cached = 0
    uncached = 0
    for exc in run["exceptions"]:
        if not exc.get("needs_llm"):
            continue
        if llm.fingerprint(llm.compact_payload(exc, lookup)) in cache:
            cached += 1
            continue
        uncached += 1
    needs = cached + uncached

    base = parent["summary"]
    now = run["summary"]
    delta = {
        "match_rate_auto": round(now["match_rate_auto"] - base["match_rate_auto"], 4),
        "records_auto_resolved": now["records_auto_resolved"] - base["records_auto_resolved"],
        "records_proposed": now["records_proposed"] - base["records_proposed"],
        "records_unresolved": now["records_unresolved"] - base["records_unresolved"],
        "exceptions_total": now["exceptions_total"] - base["exceptions_total"],
        "links_auto": now["links_auto"] - base["links_auto"],
    }
    if parent.get("accuracy") and run.get("accuracy"):
        delta["precision"] = round(
            run["accuracy"]["precision"] - parent["accuracy"]["precision"], 4)
        delta["recall"] = round(run["accuracy"]["recall"] - parent["accuracy"]["recall"], 4)
        delta["auto_precision"] = round(
            run["accuracy"]["auto_precision"] - parent["accuracy"]["auto_precision"], 4)

    committed = False
    if body.commit:
        store.save(run)
        committed = True

    return {
        "run_id": run["run_id"] if committed else None,
        "derived_from": run_id,
        "committed": committed,
        "thresholds": thresholds.as_dict(),
        "changed": thresholds.diff_from_default(),
        "summary": run["summary"],
        "accuracy": run["accuracy"],
        "delta": delta,
        "llm_coverage": {
            "exceptions_needing_model": needs,
            "already_cached": cached,
            "would_need_new_calls": uncached,
            "would_cost_requests": -(-uncached // llm.BATCH_SIZE),
            "note": (
                "Nothing was sent. Re-classification at a new tolerance is deterministic "
                "and free; this is only what explaining the result would cost if asked."
            ),
        },
        "engine_ms": round(sum(p["duration_ms"] for p in run["summary"]["passes"]), 2),
    }


@app.post("/api/runs/{run_id}/thresholds/explain")
def rethreshold_explain(run_id: str, body: ThresholdRequest) -> dict[str, Any]:
    body.commit = True
    preview = rethreshold(run_id, body)
    run = _require(preview["run_id"])

    payloads = _payloads_for(run)
    results, stats = llm.explain(payloads)
    unanswered = _apply_verdicts(run, results, stats)

    return {**preview, "llm_stats": stats, "explained": len(results),
            "unanswered": unanswered}


@app.get("/api/runs/{run_id}/confusion")
def confusion(run_id: str) -> dict[str, Any]:
    run = _require(run_id)
    result = analytics.confusion(run, _truth_for(run))
    if result is None:
        raise HTTPException(
            404,
            "A confusion matrix needs both an answer key and classified exceptions. "
            "This run has no ground truth, or the model has not run over it yet.",
        )
    return {"run_id": run_id, **result}


@app.get("/api/runs/{run_id}/calibration")
def calibration(run_id: str) -> dict[str, Any]:
    run = _require(run_id)
    result = analytics.calibration(run, _truth_for(run))
    if result is None:
        raise HTTPException(
            404,
            "Calibration needs both an answer key and classified exceptions. This run "
            "has no ground truth, or the model has not run over it yet.",
        )
    return {"run_id": run_id, **result}


@app.get("/api/runs/{run_id}/cost")
def cost(run_id: str) -> dict[str, Any]:
    run = _require(run_id)
    bundle = None
    if run.get("source") == "bundled":
        ledger_rows, bank_rows = load_bundled(run["dataset_profile"])
        bundle = baselines.bundle(run["dataset_profile"], ledger_rows, bank_rows,
                                  _truth_for(run))
    return {
        "run_id": run_id,
        "split": analytics.cost_split(run, bundle),
        "hours": analytics.hours_saved(run),
    }


@app.get("/api/runs/{run_id}/baselines")
def run_baselines(run_id: str) -> dict[str, Any]:
    run = _require(run_id)
    truth = _truth_for(run)
    if truth is None:
        raise HTTPException(
            404,
            "Baselines are scored against the answer key, which only the bundled "
            "datasets have.",
        )
    ledger_rows, bank_rows = load_bundled(run["dataset_profile"])
    bundle = baselines.bundle(run["dataset_profile"], ledger_rows, bank_rows, truth)

    accuracy = run.get("accuracy") or {}
    auto_recall = (accuracy.get("auto_true_positives", 0)
                   / max(1, accuracy.get("pairs_expected", 1)))
    return {
        "run_id": run_id,
        **(bundle or {}),
        "layered": {
            "name": "layered",
            "label": "This engine",
            "precision": accuracy.get("precision"),
            "recall": accuracy.get("recall"),
            "f1": accuracy.get("f1"),
            "auto_precision": accuracy.get("auto_precision"),
            "auto_recall": round(auto_recall, 4),
            "records": run["summary"]["total_records"],
            "match_rate": run["summary"]["match_rate_auto"],
            "api_calls": (run.get("llm_stats") or {}).get("api_calls", 0),
            "total_tokens": ((run.get("llm_stats") or {}).get("prompt_tokens", 0)
                             + (run.get("llm_stats") or {}).get("completion_tokens", 0)),
        },
    }


@app.get("/api/runs/{run_id}/provenance/{record_id}")
def provenance(run_id: str, record_id: str) -> dict[str, Any]:
    run = _require(run_id)
    result = audit.provenance(run, record_id)
    if result is None:
        raise HTTPException(404, "No record %s in run %s" % (record_id, run_id))
    return {"run_id": run_id, **result}


@app.get("/api/runs/{run_id}/audit")
def audit_log(run_id: str, format: str = Query(default="json", pattern="^(json|csv)$")):
    run = _require(run_id)
    if format == "csv":
        return PlainTextResponse(
            audit.audit_csv(run),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="audit_%s.csv"' % run_id},
        )
    return {"run_id": run_id, "summary": audit.audit_summary(run),
            "rows": audit.audit_rows(run)}


@app.get("/api/runs")
def runs(limit: int = Query(default=25, ge=1, le=100)) -> dict[str, Any]:
    return {"runs": store.list_runs(limit)}


@app.get("/api/runs/{run_id}/summary")
def summary(run_id: str) -> dict[str, Any]:
    run = _require(run_id)
    return {
        "run_id": run_id,
        "created_at": run["created_at"],
        "source": run["source"],
        "dataset_profile": run["dataset_profile"],
        "llm_complete": run.get("llm_complete", False),
        "llm_stats": run.get("llm_stats"),
        "thresholds_changed": run.get("thresholds_changed") or {},
        "adapter": run.get("adapter"),
        "decisions": _decisions(run),
        **run["summary"],
    }


@app.get("/api/runs/{run_id}/accuracy")
def accuracy(run_id: str) -> dict[str, Any]:
    run = _require(run_id)
    if run.get("accuracy") is None:
        raise HTTPException(
            404,
            "No ground truth for this run. Accuracy can only be measured against the "
            "bundled synthetic dataset, where the answer key was generated alongside "
            "the data. Uploaded files have no answer key - by design, that is the "
            "honest state of real reconciliation.",
        )
    return {"run_id": run_id, **run["accuracy"]}


@app.get("/api/runs/{run_id}/exceptions")
def exceptions(
    run_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    kind: str | None = None,
    category: str | None = None,
    status: str | None = None,
    action: str | None = None,
    max_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    needs_llm: bool | None = None,
) -> dict[str, Any]:
    run = _require(run_id)
    items = list(run["exceptions"])

    if kind:
        items = [e for e in items if e["kind"] == kind]
    if status:
        items = [e for e in items if e["status"] == status]
    if needs_llm is not None:
        items = [e for e in items if bool(e["needs_llm"]) == needs_llm]
    if category:
        items = [e for e in items if (e.get("llm") or {}).get("category") == category]
    if action:
        items = [e for e in items if (e.get("llm") or {}).get("suggested_action") == action]
    if max_confidence is not None:
        items = [e for e in items if _confidence(e) <= max_confidence]

    items.sort(key=lambda e: (_confidence(e), e["exception_id"]))

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start:start + page_size]

    lookup = _record_lookup(run)
    for e in page_items:
        e["ledger_records"] = [lookup[i] for i in e["ledger_ids"] if i in lookup]
        e["bank_records"] = [lookup[i] for i in e["stmt_ids"] if i in lookup]

    facets: dict[str, dict[str, int]] = {"kind": {}, "category": {}, "status": {},
                                         "suggested_action": {}}
    for e in run["exceptions"]:
        facets["kind"][e["kind"]] = facets["kind"].get(e["kind"], 0) + 1
        facets["status"][e["status"]] = facets["status"].get(e["status"], 0) + 1
        cat = (e.get("llm") or {}).get("category")
        if cat:
            facets["category"][cat] = facets["category"].get(cat, 0) + 1
        act = (e.get("llm") or {}).get("suggested_action")
        if act:
            facets["suggested_action"][act] = facets["suggested_action"].get(act, 0) + 1

    return {"run_id": run_id, "total": total, "page": page, "page_size": page_size,
            "pages": max(1, -(-total // page_size)), "items": page_items, "facets": facets}


def _confidence(exc: dict[str, Any]) -> float:
    llm_block = exc.get("llm") or {}
    if llm_block.get("source") in ("groq", "mock"):
        return float(llm_block.get("confidence", 0.0))
    return float(exc.get("engine_confidence", 0.0))


@app.post("/api/runs/{run_id}/exceptions/{exception_id}/action")
def record_action(run_id: str, exception_id: str, body: ActionRequest) -> dict[str, Any]:
    run = _require(run_id)
    target = next((e for e in run["exceptions"] if e["exception_id"] == exception_id), None)
    if target is None:
        raise HTTPException(404, "No exception " + exception_id + " in this run")

    at = store.now()
    previous = target["status"]
    target["status"] = {"approve": "approved", "reject": "rejected",
                        "investigate": "investigating"}[body.action]
    target["decided_at"] = at
    target["decided_note"] = body.note

    run.setdefault("audit_events", []).append({
        "at": at,
        "exception_id": exception_id,
        "action": body.action,
        "resulting_status": target["status"],
        "previous_status": previous,
        "note": body.note,
        "llm_suggested": (target.get("llm") or {}).get("suggested_action"),
        "followed_suggestion": (
            (target.get("llm") or {}).get("suggested_action") == body.action
            if (target.get("llm") or {}).get("suggested_action") else None
        ),
    })
    store.save(run)

    return {"exception": target, "decisions": _decisions(run),
            "audit_events": len(run["audit_events"])}


@app.get("/api/runs/{run_id}/records")
def records(run_id: str) -> dict[str, Any]:
    run = _require(run_id)
    return {"run_id": run_id, "ledger": run["records"]["ledger"],
            "bank": run["records"]["bank"], "links": run["links"]}


@app.get("/api/runs/{run_id}")
def whole_run(run_id: str) -> dict[str, Any]:
    return _require(run_id)
