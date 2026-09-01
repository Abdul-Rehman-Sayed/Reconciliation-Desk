from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from . import mockllm

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

GROQ_BASE = "https://api.groq.com/openai/v1"


MODEL_PREFERENCE = [
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "qwen/qwen3.6-27b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "groq/compound-mini",
    "gemma2-9b-it",
]


BATCH_SIZE = 12


MAX_WORKERS = 1

REQUEST_TIMEOUT = 60
MAX_RETRIES = 3


REASONING_HEADROOM = 600
MAX_TOKENS_PER_RECORD = 110
MAX_TOKENS_FLOOR = 400
MAX_TOKENS_CEILING = 3000


CHARS_PER_TOKEN = 2.5


DEFAULT_TPM = 8000


DAILY_CALL_BUDGET = int(os.getenv("GROQ_DAILY_CALL_BUDGET", "120"))

CATEGORIES = [
    "duplicate", "fee_adjustment", "date_delay", "split_payment",
    "reference_mismatch", "refund", "orphan_bank", "orphan_ledger", "other",
]
ACTIONS = ["approve", "reject", "investigate"]

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


CACHE_PATH = DATA_DIR / "llm_cache.json"
MOCK_CACHE_PATH = DATA_DIR / "llm_cache_mock.json"
BUDGET_PATH = DATA_DIR / "llm_budget.json"
_cache_lock = threading.Lock()
_budget_lock = threading.Lock()


PROMPT_VERSION = "v2"

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "explanation": {"type": "string"},
                    "confidence": {"type": "number"},
                    "action": {"type": "string", "enum": ACTIONS},
                },
                "required": ["id", "category", "explanation", "confidence", "action"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


SYSTEM_PROMPT = (
    "You are a reconciliation analyst on an Indian payments desk. Amounts are INR.\n"
    "A deterministic engine already resolved everything it could prove. You see only "
    "what it could not settle, with its evidence.\n"
    "For each item return: category, a plain one-or-two-sentence explanation under 40 "
    "words citing the actual amounts/dates/references, a confidence 0-1, and an action.\n"
    "Rules: you classify and explain, you never resolve - a human decides. Suggest "
    "approve only if the evidence supports the engine's pairing. If a record genuinely "
    "has no counterpart, say so and suggest investigate; never invent a match for an "
    "orphan. If two candidates fit equally, say so and suggest investigate. Low "
    "confidence is more useful than a confident guess. Never suggest moving, refunding "
    "or writing off money.\n"
    "Return one object per id given, and nothing else."
)


_FIELD_DOC = (
    "Fields: k=engine finding, n=engine note, c=engine confidence, "
    "L=ledger rows, B=bank rows, e=evidence. Rows are [id,date,amount,reference,party]."
)


class LLMUnavailable(RuntimeError):
    pass


class _SchemaUnsupported(RuntimeError):
    pass


class _NeedsSmallerBatch(RuntimeError):
    pass


def use_mock() -> bool:
    return os.getenv("USE_MOCK_LLM", "").strip().lower() in ("1", "true", "yes", "on")


def api_key() -> str | None:
    key = os.getenv("GROQ_API_KEY", "").strip()
    return key or None


def list_models() -> list[str]:
    key = api_key()
    if not key:
        raise LLMUnavailable("GROQ_API_KEY is not set")
    r = requests.get(
        GROQ_BASE + "/models",
        headers={"Authorization": "Bearer " + key},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []))


_resolved_model: str | None = None


def resolve_model(force: bool = False) -> str:
    global _resolved_model
    if use_mock():
        return mockllm.MODEL_NAME
    if _resolved_model and not force:
        return _resolved_model

    override = os.getenv("GROQ_MODEL", "").strip()
    available = list_models()
    if override:
        if override not in available:
            raise LLMUnavailable(
                "GROQ_MODEL=%s is not in Groq's live model list. Available: %s"
                % (override, ", ".join(available[:12]))
            )
        _resolved_model = override
        return _resolved_model

    for candidate in MODEL_PREFERENCE:
        if candidate in available:
            _resolved_model = candidate
            return _resolved_model

    chat_models = [m for m in available
                   if not any(x in m for x in ("whisper", "tts", "guard", "orpheus"))]
    if not chat_models:
        raise LLMUnavailable("Groq returned no usable chat models")
    _resolved_model = chat_models[0]
    return _resolved_model


def _row(rec: dict[str, Any]) -> list[Any]:
    party = rec.get("counterparty") or rec.get("narration") or ""
    return [
        rec.get("id"),
        str(rec.get("date", ""))[5:],
        round(float(rec.get("amount", 0) or 0), 2),
        (rec.get("reference_number") or "")[:24],
        str(party)[:38],
    ]


_EVIDENCE_KEYS = (
    "amount_delta", "day_delta", "ref_similarity", "fee_rate", "fee_amount",
    "known_rate", "shortfall", "shortfall_pct", "amount_discrepancy", "contested",
    "rival_count", "component_count", "component_total", "residual", "day_span",
    "direction", "counterparty", "bank_amount", "ledger_amount", "ledger_ref",
    "bank_ref", "duplicate_of", "reference_number", "nets_to_zero",
)


def _compact_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
    ev = dict(evidence or {})

    out = {
        k: ev[k] for k in _EVIDENCE_KEYS
        if k in ev and ev[k] is not None and ev[k] is not False and ev[k] != ""
    }

    nearest = ev.get("nearest_on_other_side")
    if isinstance(nearest, dict) and nearest.get("record"):
        out["near"] = _row(nearest["record"]) + [
            round(float(nearest.get("similarity", 0) or 0), 2),
            round(float(nearest.get("amount_delta", 0) or 0), 2),
            int(nearest.get("day_delta", 0) or 0),
        ]
    return out


def compact_payload(exception: dict[str, Any], lookup: dict[str, Any]) -> dict[str, Any]:
    return {
        "k": exception["kind"],
        "n": (exception.get("engine_note") or "")[:260],
        "c": round(float(exception.get("engine_confidence", 0) or 0), 2),
        "L": [_row(lookup[i]) for i in exception.get("ledger_ids", []) if i in lookup],
        "B": [_row(lookup[i]) for i in exception.get("stmt_ids", []) if i in lookup],
        "e": _compact_evidence(exception.get("evidence")),
    }


def _mock_payload(compact: dict[str, Any]) -> dict[str, Any]:
    def rows(items: list[list[Any]], side: str) -> list[dict[str, Any]]:
        out = []
        for r in items:
            base = {"id": r[0], "date": r[1], "amount": r[2], "reference_number": r[3]}
            base["counterparty" if side == "ledger" else "narration"] = r[4]
            out.append(base)
        return out

    ev = dict(compact.get("e") or {})
    near = ev.pop("near", None)
    if near:
        ev["nearest_on_other_side"] = {
            "record": {"id": near[0], "date": near[1], "amount": near[2],
                       "reference_number": near[3]},
            "similarity": near[5], "amount_delta": near[6], "day_delta": near[7],
        }
    return {
        "engine_finding": compact["k"],
        "engine_note": compact["n"],
        "ledger_records": rows(compact.get("L") or [], "ledger"),
        "bank_records": rows(compact.get("B") or [], "bank"),
        "evidence": ev,
    }


def cache_path() -> Path:
    return MOCK_CACHE_PATH if use_mock() else CACHE_PATH


def _load_cache() -> dict[str, Any]:
    path = cache_path()
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        try:
            path.replace(path.with_suffix(".corrupt.json"))
        except OSError:
            pass
        return {}
    except OSError:
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        merged = _load_cache()
        merged.update(cache)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def fingerprint(compact: dict[str, Any]) -> str:
    blob = json.dumps(compact, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((PROMPT_VERSION + "|" + blob).encode("utf-8")).hexdigest()[:32]


def cache_stats() -> dict[str, Any]:
    cache = _load_cache()
    by_source: dict[str, int] = {}
    for entry in cache.values():
        src = str(entry.get("source", "?"))
        by_source[src] = by_source.get(src, 0) + 1
    try:
        live = len(json.loads(CACHE_PATH.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        live = 0
    return {
        "entries": len(cache),
        "by_source": by_source,
        "path": str(cache_path()),
        "live_entries": live,
        "mock_mode": use_mock(),
        "prompt_version": PROMPT_VERSION,
    }


def _budget_state() -> dict[str, Any]:
    today = date.today().isoformat()
    try:
        state = json.loads(BUDGET_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        state = {}
    if state.get("day") != today:
        state = {"day": today, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    return state


def _budget_write(state: dict[str, Any]) -> None:
    try:
        BUDGET_PATH.parent.mkdir(parents=True, exist_ok=True)
        BUDGET_PATH.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def budget_remaining() -> int:
    with _budget_lock:
        return max(0, DAILY_CALL_BUDGET - int(_budget_state().get("calls", 0)))


def _budget_take() -> bool:
    with _budget_lock:
        state = _budget_state()
        if int(state.get("calls", 0)) >= DAILY_CALL_BUDGET:
            return False
        state["calls"] = int(state.get("calls", 0)) + 1
        _budget_write(state)
        return True


def _budget_record_tokens(prompt: int, completion: int) -> None:
    with _budget_lock:
        state = _budget_state()
        state["prompt_tokens"] = int(state.get("prompt_tokens", 0)) + prompt
        state["completion_tokens"] = int(state.get("completion_tokens", 0)) + completion
        _budget_write(state)


def budget_status() -> dict[str, Any]:
    with _budget_lock:
        state = _budget_state()
    return {
        "day": state.get("day"),
        "calls_made": int(state.get("calls", 0)),
        "calls_budget": DAILY_CALL_BUDGET,
        "calls_remaining": max(0, DAILY_CALL_BUDGET - int(state.get("calls", 0))),
        "prompt_tokens": int(state.get("prompt_tokens", 0)),
        "completion_tokens": int(state.get("completion_tokens", 0)),
    }


class _TokenBucket:
    def __init__(self, limit: int = DEFAULT_TPM):
        self.limit = float(limit)
        self.available = float(limit)
        self.updated = time.monotonic()
        self.lock = threading.Lock()
        self.waited_ms = 0.0

    def observe_limit(self, limit: int) -> None:
        with self.lock:
            if limit > 0 and abs(limit - self.limit) > 1:
                self.available = min(self.available, float(limit))
                self.limit = float(limit)

    def _refill(self) -> None:
        now = time.monotonic()
        self.available = min(self.limit, self.available + (now - self.updated) * self.limit / 60.0)
        self.updated = now

    def take(self, tokens: int) -> None:
        need = float(min(tokens, self.limit))
        t0 = time.monotonic()
        while True:
            with self.lock:
                self._refill()
                if self.available >= need:
                    self.available -= need
                    self.waited_ms += (time.monotonic() - t0) * 1000
                    return
                deficit = need - self.available
                sleep_for = max(0.05, deficit / (self.limit / 60.0))
            time.sleep(min(sleep_for, 10.0))


_bucket = _TokenBucket()


def _post(body: dict[str, Any], key: str, stats: dict[str, Any]) -> dict[str, Any]:
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(
                GROQ_BASE + "/chat/completions",
                headers={"Authorization": "Bearer " + key,
                         "Content-Type": "application/json"},
                json=body,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
            continue

        limit_tokens = r.headers.get("x-ratelimit-limit-tokens")
        if limit_tokens and limit_tokens.isdigit():
            _bucket.observe_limit(int(limit_tokens))
        for header, field in (("x-ratelimit-remaining-requests", "requests_remaining"),
                              ("x-ratelimit-remaining-tokens", "tokens_remaining")):
            value = r.headers.get(header)
            if value and value.replace(".", "").isdigit():
                stats[field] = float(value)

        if r.status_code == 429:
            stats["rate_limited"] = int(stats.get("rate_limited", 0)) + 1
            wait = float(r.headers.get("retry-after", 2 * (attempt + 1)))
            time.sleep(min(wait, 10))
            last_error = RuntimeError("rate limited")
            continue
        if r.status_code == 400:
            code = ""
            try:
                code = str(r.json().get("error", {}).get("code", ""))
            except ValueError:
                pass

            if code == "json_validate_failed" or "expected schema" in r.text:
                raise _NeedsSmallerBatch("groq 400 %s" % (code or "schema mismatch"))
            if "response_format" in r.text:
                raise _SchemaUnsupported(r.text)
        if not r.ok:
            last_error = RuntimeError("groq %d: %s" % (r.status_code, r.text[:300]))
            time.sleep(1.0 * (attempt + 1))
            continue

        data = r.json()
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        stats["prompt_tokens"] = int(stats.get("prompt_tokens", 0)) + prompt_tokens
        stats["completion_tokens"] = int(stats.get("completion_tokens", 0)) + completion_tokens
        _budget_record_tokens(prompt_tokens, completion_tokens)
        return data
    raise LLMUnavailable(str(last_error))


def _max_tokens_for(records: int) -> int:
    return max(MAX_TOKENS_FLOOR,
               min(MAX_TOKENS_CEILING,
                   REASONING_HEADROOM + MAX_TOKENS_PER_RECORD * records))


def _ask(batch: list[tuple[str, dict[str, Any]]], model: str, key: str,
         stats: dict[str, Any]) -> dict[str, Any]:
    if not _budget_take():
        raise LLMUnavailable(
            "the local daily call budget of %d is spent (GROQ_DAILY_CALL_BUDGET)"
            % DAILY_CALL_BUDGET)
    stats["api_calls"] = int(stats.get("api_calls", 0)) + 1
    stats["new_calls"] = int(stats.get("new_calls", 0)) + 1

    items = [dict(compact, id=eid) for eid, compact in batch]
    user = _FIELD_DOC + "\n" + json.dumps(items, separators=(",", ":"), default=str)
    max_tokens = _max_tokens_for(len(batch))

    body: dict[str, Any] = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "reconciliation_exceptions",
                            "schema": RESULT_SCHEMA, "strict": True},
        },
    }

    estimate = int(len(SYSTEM_PROMPT + user) / CHARS_PER_TOKEN) + max_tokens
    _bucket.take(estimate)

    try:
        raw = _post(body, key, stats)
    except _SchemaUnsupported:
        body["response_format"] = {"type": "json_object"}
        body["messages"][0]["content"] += (
            '\nReply as {"results":[{"id":"...","category":"one of %s",'
            '"explanation":"...","confidence":0.0,"action":"one of %s"}]}'
            % ("|".join(CATEGORIES), "|".join(ACTIONS))
        )
        _bucket.take(estimate)
        raw = _post(body, key, stats)

    choice = raw["choices"][0]

    if choice.get("finish_reason") == "length":
        raise _NeedsSmallerBatch(
            "answer hit max_tokens=%d and was cut off" % max_tokens)
    try:
        parsed = json.loads(choice["message"]["content"])
    except json.JSONDecodeError as exc:
        raise _NeedsSmallerBatch("unparseable JSON from the model: %s" % exc)
    return {"results": parsed.get("results", [])}


def _ask_split(batch: list[tuple[str, dict[str, Any]]], model: str, key: str,
               stats: dict[str, Any]) -> dict[str, Any]:
    try:
        out = _ask(batch, model, key, stats)
    except _NeedsSmallerBatch:
        if len(batch) == 1:
            raise
        stats["batch_splits"] = int(stats.get("batch_splits", 0)) + 1
        mid = len(batch) // 2
        merged: list[dict[str, Any]] = []
        failures: list[str] = []
        for half in (batch[:mid], batch[mid:]):
            try:
                merged.extend(_ask_split(half, model, key, stats)["results"])
            except Exception as exc:
                failures.append(str(exc))
        if not merged and failures:
            raise _NeedsSmallerBatch("; ".join(failures)[:200])
        return {"results": merged}

    answered = {str(r.get("id") or r.get("exception_id")) for r in out["results"]}
    missing = [pair for pair in batch if pair[0] not in answered]
    if missing and len(missing) < len(batch):
        stats["rows_refetched"] = int(stats.get("rows_refetched", 0)) + len(missing)

        try:
            out["results"].extend(_ask_split(missing, model, key, stats)["results"])
        except Exception as exc:
            stats["errors"].append("refetch of %d row(s): %s" % (len(missing), str(exc)[:160]))
    return out


def _validate(item: dict[str, Any], model: str) -> dict[str, Any]:
    category = str(item.get("category", "other"))
    action = str(item.get("action") or item.get("suggested_action") or "investigate")
    try:
        confidence = float(item.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "category": category if category in CATEGORIES else "other",
        "explanation": str(item.get("explanation", "")).strip(),
        "confidence": max(0.0, min(1.0, confidence)),
        "suggested_action": action if action in ACTIONS else "investigate",
        "source": "groq",
        "model": model,
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "category": "other",
        "explanation": (
            "The language model was not reachable for this exception, so there is no "
            "generated explanation. The engine's own finding above still stands and a "
            "human still has to decide. Reason: " + reason
        ),
        "confidence": 0.0,
        "suggested_action": "investigate",
        "source": "unavailable",
        "model": None,
    }


def _blank_stats(requested: int) -> dict[str, Any]:
    return {
        "requested": requested,
        "answered": 0,
        "from_cache": 0,
        "new_calls": 0,
        "api_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "tokens_saved_by_cache": 0,
        "rate_limited": 0,

        "batch_splits": 0,
        "rows_refetched": 0,
        "bucket_wait_ms": 0.0,
        "mode": "mock" if use_mock() else "groq",
        "model": None,
        "budget": budget_status(),
        "errors": [],
    }


def explain(
    payloads: list[tuple[str, dict[str, Any]]], progress: Any = None
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    stats = _blank_stats(len(payloads))
    if not payloads:
        return {}, stats

    cache = _load_cache()
    results: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, dict[str, Any]]] = []

    for eid, compact in payloads:
        hit = cache.get(fingerprint(compact))
        if hit:
            results[eid] = dict(hit, cached=True)
            stats["from_cache"] += 1

            stats["tokens_saved_by_cache"] += int(
                len(json.dumps(compact, separators=(",", ":"), default=str)) / 3.6
            ) + MAX_TOKENS_PER_RECORD
        else:
            pending.append((eid, compact))

    if not pending:
        stats["answered"] = sum(1 for r in results.values()
                                if r.get("source") in ("groq", "mock"))
        stats["model"] = next((r.get("model") for r in results.values() if r.get("model")), None)
        return results, stats

    if use_mock():
        stats["model"] = mockllm.MODEL_NAME
        for eid, compact in pending:
            verdict = mockllm.classify(_mock_payload(compact))
            results[eid] = verdict
            cache[fingerprint(compact)] = verdict
        _save_cache(cache)
        stats["answered"] = sum(1 for r in results.values()
                                if r.get("source") in ("groq", "mock"))
        if progress:
            progress(len(pending), len(pending))
        return results, stats

    key = api_key()
    if not key:
        stats["errors"].append("GROQ_API_KEY is not set")
        for eid, _ in pending:
            results[eid] = _unavailable("GROQ_API_KEY is not set")
        return results, stats

    try:
        model = resolve_model()
    except (LLMUnavailable, requests.RequestException) as exc:
        stats["errors"].append(str(exc))
        for eid, _ in pending:
            results[eid] = _unavailable(str(exc))
        return results, stats
    stats["model"] = model

    batches = [pending[i:i + BATCH_SIZE] for i in range(0, len(pending), BATCH_SIZE)]
    done = 0

    for batch in batches:
        try:
            out = _ask_split(batch, model, key, stats)
            err = ""
        except Exception as exc:
            out, err = {"results": []}, str(exc)[:300]
            stats["errors"].append(err)

        by_id = {str(r.get("id") or r.get("exception_id")): r for r in out.get("results", [])}
        for eid, compact in batch:
            item = by_id.get(eid)
            if item is None:
                results[eid] = _unavailable(err or "model returned no row for this id")
                continue
            clean = _validate(item, model)
            results[eid] = clean
            with _cache_lock:
                cache[fingerprint(compact)] = clean

        done += len(batch)
        if progress:
            progress(done, len(pending))

    _save_cache(cache)
    stats["bucket_wait_ms"] = round(_bucket.waited_ms, 1)
    stats["answered"] = sum(1 for r in results.values() if r.get("source") in ("groq", "mock"))
    stats["budget"] = budget_status()
    return results, stats


def explain_exceptions(
    payloads: list[dict[str, Any]], progress: Any = None
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    pairs = []
    for p in payloads:
        eid = p["exception_id"]
        compact = {
            "k": p.get("engine_finding", ""),
            "n": (p.get("engine_note") or "")[:260],
            "c": round(float(p.get("engine_confidence", 0) or 0), 2),
            "L": [_row(r) for r in p.get("ledger_records", [])],
            "B": [_row(r) for r in p.get("bank_records", [])],
            "e": _compact_evidence(p.get("evidence")),
        }
        pairs.append((eid, compact))
    return explain(pairs, progress)
