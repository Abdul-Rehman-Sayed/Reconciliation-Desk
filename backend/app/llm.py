"""
Groq exception handler.

This module only ever sees what six deterministic passes could not resolve on
their own. It never sees a clean match, and on the bundled dataset it is asked
about roughly 8% of the records rather than all 856 of them.

Three rules the rest of the system depends on:

  1. Structured output only. The model is given a JSON schema and its reply is
     validated before anything downstream touches it. Nothing is regexed out of prose.
  2. It classifies and explains. It never resolves. Its `suggested_action` is a
     suggestion sitting in a queue waiting for a person.
  3. When there is genuinely no counterpart, it has to say so. The prompt makes
     "there is nothing to match this to" a first-class answer, because inventing a
     match for an orphan is the single most damaging thing it could do here.

-------------------------------------------------------------------------------
Token discipline
-------------------------------------------------------------------------------
The free tier is a token bucket, not a daily allowance, and the bucket is what
this module is built around. Measured off the live response headers on this
account (see scripts/check_limits.py, which reads them rather than trusting the
docs):

    openai/gpt-oss-20b     1,000 requests/day    8,000 tokens/minute
    groq/compound-mini       250 requests/day   70,000 tokens/minute
    allam-2-7b             7,000 requests/day    6,000 tokens/minute

The first version of this file sent one request per five exceptions, three at a
time, with max_tokens=2000. Groq reserves max_tokens against the token bucket at
admission, so three concurrent calls asked for 3 x (1,060 prompt + 2,000
reserved) = 9,180 tokens against a bucket of 8,000. Four of seven calls came
back 429. The completion length was never the problem - the reservation was.

So, in order of how much each one actually saved:

  1. Cache on disk, keyed by a hash of the exact fields sent. After the bundled
     dataset has been run once, every later run is free. This is what makes it
     safe to rehearse a demo thirty times.
  2. A client-side token bucket that mirrors Groq's own. Requests wait for
     capacity here rather than being rejected there. A 429 costs a request out
     of the daily allowance and returns nothing; waiting 400ms costs nothing.
  3. Batch 12 exceptions per request instead of 5, cutting request count ~2.5x
     against the daily ceiling.
  4. Send only the fields a verdict actually turns on, as compact JSON. Cut the
     per-exception payload from ~210 tokens to ~70.
  5. max_tokens budgeted per record rather than per request, so the reservation
     tracks the work asked for.
  6. A hard daily call budget, tracked on disk, that refuses to call at all past
     a ceiling you set. Belt and braces for a live demo.

And USE_MOCK_LLM=true for all UI work, which skips the network entirely.
"""

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

# Tried in order against the live model list; the first one actually available wins.
# Groq's free lineup changes, so nothing here is hardcoded as a hard requirement.
# gpt-oss-20b leads because on this account it carries 1,000 requests/day against
# compound-mini's 250, and classification at this difficulty does not need the
# larger model. Run scripts/check_limits.py to re-measure before changing the order.
MODEL_PREFERENCE = [
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "qwen/qwen3.6-27b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "groq/compound-mini",
    "gemma2-9b-it",
]

# 12 exceptions per request. The whole standard dataset is 32 exceptions, so a
# cold run is 3 requests. Above ~15 the model starts dropping rows from the
# array; below ~8 the fixed system prompt stops amortising.
BATCH_SIZE = 12

# One request at a time. The bucket refills at limit/60 tokens per second, so
# concurrency buys nothing here except 429s - two parallel calls simply drain the
# same bucket twice as fast and then both wait. Serial with a bucket in front is
# both faster end to end and cheaper in requests.
MAX_WORKERS = 1

REQUEST_TIMEOUT = 60
MAX_RETRIES = 3

# Budgeted per record, not per request, because the request holds N records -
# plus a flat allowance for the reasoning pass that happens before the first
# character of the answer is written.
#
# gpt-oss models think before they answer. Those reasoning tokens are billed as
# completion tokens and are drawn from max_tokens like any other, so a budget
# sized only for the visible answer leaves the model no room to reach it.
# Measured on the heaviest real payload rather than the lightest: a batch of 12
# composite_candidate exceptions, the ones that carry a full evidence dict, spent
# 366 tokens reasoning and 909 writing the answer - 1,275 completion tokens for
# 12 records, or ~76 per record on top of a reasoning pass that scales with how
# tangled the batch is. A single simple record is ~96 reasoning and ~240 total.
# Size for the expensive case: the cheap one is not what breaks.
#
# The previous numbers (70/record, floor 200, ceiling 900) were calibrated on a
# non-reasoning model and had no such allowance, which produced two distinct
# failures. A single record got max_tokens=200, spent ~96 of it reasoning, and
# came back 400 json_validate_failed. A batch of 12 got 900, ran out mid-array,
# and came back 200 OK carrying 9 rows for 12 records - the 3 missing ones were
# then marked unavailable downstream as though the model had declined them.
# The margin here is deliberate. Reasoning length varies run to run on identical
# input, and the failure when it overruns is not a shorter answer - it is a 400
# with an empty failed_generation, because Groq validates the truncated JSON and
# finds nothing. An earlier pass at 1,470 for a batch of 12 sat only 15% above
# the observed 1,275 and failed every time. These leave ~50%.
REASONING_HEADROOM = 600
MAX_TOKENS_PER_RECORD = 110
MAX_TOKENS_FLOOR = 400
MAX_TOKENS_CEILING = 3000

# Measured against real prompt_tokens rather than assumed: 5,798 characters of
# system prompt plus compacted JSON was billed as 2,140 prompt tokens, or 2.71
# chars/token. The previous 3.6 was a prose-shaped ratio and undercounted this
# payload by ~30%, which is why a run could report bucket_wait_ms=0.0 while Groq
# returned 12 rate-limit errors: the local bucket believed it had headroom it did
# not have, so it never paced anything. 2.5 keeps the error on the safe side.
CHARS_PER_TOKEN = 2.5

# Assumed bucket size when a model has not told us its own yet. Replaced by the
# real value from x-ratelimit-limit-tokens on the first response.
DEFAULT_TPM = 8000

# Refuse to make more than this many live calls in a day, whatever else happens.
# Set GROQ_DAILY_CALL_BUDGET=0 to block live calls entirely.
DAILY_CALL_BUDGET = int(os.getenv("GROQ_DAILY_CALL_BUDGET", "120"))

CATEGORIES = [
    "duplicate", "fee_adjustment", "date_delay", "split_payment",
    "reference_mismatch", "refund", "orphan_bank", "orphan_ledger", "other",
]
ACTIONS = ["approve", "reject", "investigate"]

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
# Two caches, never one. A templated mock answer written into the real cache
# would be served silently on the next live run and never re-asked - the flag
# meant for saving quota would quietly become the thing that fakes the demo.
# Separate files make switching modes switch the whole body of answers with it.
CACHE_PATH = DATA_DIR / "llm_cache.json"
MOCK_CACHE_PATH = DATA_DIR / "llm_cache_mock.json"
BUDGET_PATH = DATA_DIR / "llm_budget.json"
_cache_lock = threading.Lock()
_budget_lock = threading.Lock()

# Bump this when the prompt or the payload shape changes in a way that would
# make an old cached answer wrong. It is part of the cache key, so bumping it
# invalidates the cache deliberately rather than by accident.
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

# Short and fixed. The previous version re-explained the whole domain on every
# call at ~450 tokens; batched 12-up that was 450 tokens of overhead per 12
# records instead of per 5, but it was still 450 tokens of the same paragraph
# every time. This says the same things in a third of the space.
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

# Compact field names on the wire. Sent hundreds of times; spelled out in
# _compact()'s docstring so nothing is lost by shortening them here.
_FIELD_DOC = (
    "Fields: k=engine finding, n=engine note, c=engine confidence, "
    "L=ledger rows, B=bank rows, e=evidence. Rows are [id,date,amount,reference,party]."
)


class LLMUnavailable(RuntimeError):
    pass


class _SchemaUnsupported(RuntimeError):
    """This model will not take a json_schema response_format. Fall back to json_object."""


class _NeedsSmallerBatch(RuntimeError):
    """The answer did not survive the request - truncated, malformed, or schema-invalid.

    Not a dead batch. Every cause of this gets better with fewer records in the
    request, so the caller halves it and retries rather than writing the whole
    batch off as unavailable.
    """


# --------------------------------------------------------------------------
# Mode
# --------------------------------------------------------------------------
def use_mock() -> bool:
    return os.getenv("USE_MOCK_LLM", "").strip().lower() in ("1", "true", "yes", "on")


def api_key() -> str | None:
    key = os.getenv("GROQ_API_KEY", "").strip()
    return key or None


# --------------------------------------------------------------------------
# Model discovery
# --------------------------------------------------------------------------
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
    """Ask Groq what it actually serves today, then take our first preference from it."""
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


# --------------------------------------------------------------------------
# Payload compaction
# --------------------------------------------------------------------------
def _row(rec: dict[str, Any]) -> list[Any]:
    """One record as a positional list. Named fields cost ~4x this in tokens."""
    party = rec.get("counterparty") or rec.get("narration") or ""
    return [
        rec.get("id"),
        str(rec.get("date", ""))[5:],          # drop the year, every row shares it
        round(float(rec.get("amount", 0) or 0), 2),
        (rec.get("reference_number") or "")[:24],
        str(party)[:38],
    ]


# Evidence keys that actually change a verdict. Everything else the engine
# gathers is for the interface and the audit log, not for the model.
_EVIDENCE_KEYS = (
    "amount_delta", "day_delta", "ref_similarity", "fee_rate", "fee_amount",
    "known_rate", "shortfall", "shortfall_pct", "amount_discrepancy", "contested",
    "rival_count", "component_count", "component_total", "residual", "day_span",
    "direction", "counterparty", "bank_amount", "ledger_amount", "ledger_ref",
    "bank_ref", "duplicate_of", "reference_number", "nets_to_zero",
)


def _compact_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
    ev = dict(evidence or {})
    # `ev[k] not in (None, False, "")` would be wrong here: 0.0 == False in
    # Python, so an amount_delta of exactly zero - "the amounts agree to the
    # paisa", one of the more decisive facts on the page - would be dropped.
    out = {
        k: ev[k] for k in _EVIDENCE_KEYS
        if k in ev and ev[k] is not None and ev[k] is not False and ev[k] != ""
    }

    # The nearest-candidate block is the whole point of an orphan exception - it
    # is the evidence that nothing fits - but the full record is wasteful.
    nearest = ev.get("nearest_on_other_side")
    if isinstance(nearest, dict) and nearest.get("record"):
        out["near"] = _row(nearest["record"]) + [
            round(float(nearest.get("similarity", 0) or 0), 2),
            round(float(nearest.get("amount_delta", 0) or 0), 2),
            int(nearest.get("day_delta", 0) or 0),
        ]
    return out


def compact_payload(exception: dict[str, Any], lookup: dict[str, Any]) -> dict[str, Any]:
    """The exact object that gets hashed for the cache key and sent to the model.

    Deliberately not the exception as stored. The stored exception carries ids,
    status, timestamps, link ids and the full evidence dict - none of which
    change what the verdict should be, and all of which would churn the cache
    key. What is here is what a verdict actually turns on.
    """
    return {
        "k": exception["kind"],
        "n": (exception.get("engine_note") or "")[:260],
        "c": round(float(exception.get("engine_confidence", 0) or 0), 2),
        "L": [_row(lookup[i]) for i in exception.get("ledger_ids", []) if i in lookup],
        "B": [_row(lookup[i]) for i in exception.get("stmt_ids", []) if i in lookup],
        "e": _compact_evidence(exception.get("evidence")),
    }


def _mock_payload(compact: dict[str, Any]) -> dict[str, Any]:
    """Re-expand a compact payload into what mockllm.classify expects."""
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


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------
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
        # A cache that will not parse is still a cache somebody paid for. Moving
        # it aside rather than returning {} means the next save cannot quietly
        # overwrite it with a fresh empty one, and the bytes stay recoverable.
        try:
            path.replace(path.with_suffix(".corrupt.json"))
        except OSError:
            pass
        return {}
    except OSError:
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    """Merge into whatever is on disk. Never replace it wholesale.

    The caller holds the entries it happens to have looked at this run, which is
    almost never the whole file. Writing that view directly makes every save a
    potential truncation - and this file holds verdicts that cost real quota, is
    committed to the repo, and is what makes a rehearsal free. One process
    holding a partial view must not be able to destroy it.

    Learned the hard way: a test that stubbed _load_cache to return {} and left
    _save_cache alone emptied the real cache on the next call.
    """
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
    """Hash of exactly the bytes that go into the prompt, plus the prompt version.

    Deliberately not keyed on the model. An explanation of a fee deduction does
    not become wrong because the account switched from gpt-oss-20b to 120b, and
    keying on the model would throw the whole cache away the day Groq retires
    one. Which model produced a given verdict is recorded inside the value, so
    provenance survives without the cache paying for it.
    """
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


# --------------------------------------------------------------------------
# Daily call budget - a hard stop that does not depend on Groq saying no
# --------------------------------------------------------------------------
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
    """Claim one call. False means the budget is spent and we must not call."""
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


# --------------------------------------------------------------------------
# Token bucket - mirrors Groq's, so we wait here instead of 429ing there
# --------------------------------------------------------------------------
class _TokenBucket:
    """Groq's TPM limit is a bucket refilling at limit/60 per second, and it
    reserves max_tokens at admission rather than charging actual usage. This
    models the same thing locally and blocks until there is room."""

    def __init__(self, limit: int = DEFAULT_TPM):
        self.limit = float(limit)
        self.available = float(limit)
        self.updated = time.monotonic()
        self.lock = threading.Lock()
        self.waited_ms = 0.0

    def observe_limit(self, limit: int) -> None:
        """Adopt the real limit once a response header has told us what it is."""
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


# --------------------------------------------------------------------------
# The call
# --------------------------------------------------------------------------
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
            except ValueError:                        # not JSON; fall through
                pass
            # Both of these mean "the answer did not come out whole", and both
            # are retried by the caller with fewer records rather than here with
            # the same request - a second identical call fails identically.
            if code == "json_validate_failed" or "expected schema" in r.text:
                raise _NeedsSmallerBatch("groq 400 %s" % (code or "schema mismatch"))
            if "response_format" in r.text:
                raise _SchemaUnsupported(r.text)
        if not r.ok:
            last_error = RuntimeError("groq %d: %s" % (r.status_code, r.text[:300]))
            time.sleep(1.0 * (attempt + 1))
            continue

        # Count tokens here rather than at the call site, so a response that is
        # about to be rejected for being truncated still gets charged for what
        # it actually spent. It cost the bucket the same either way.
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
    """Room for the reasoning pass plus one answer per record, within the ceiling."""
    return max(MAX_TOKENS_FLOOR,
               min(MAX_TOKENS_CEILING,
                   REASONING_HEADROOM + MAX_TOKENS_PER_RECORD * records))


def _ask(batch: list[tuple[str, dict[str, Any]]], model: str, key: str,
         stats: dict[str, Any]) -> dict[str, Any]:
    # Claimed here rather than per batch in explain(), because a batch that gets
    # split makes more than one call and each of those is a real request.
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

    # Reserve against the local bucket the same way Groq does: prompt estimate
    # plus the full max_tokens, because Groq reserves at admission rather than
    # charging actual usage.
    estimate = int(len(SYSTEM_PROMPT + user) / CHARS_PER_TOKEN) + max_tokens
    _bucket.take(estimate)

    try:
        raw = _post(body, key, stats)
    except _SchemaUnsupported:
        # Model does not support json_schema; json_object is supported everywhere.
        body["response_format"] = {"type": "json_object"}
        body["messages"][0]["content"] += (
            '\nReply as {"results":[{"id":"...","category":"one of %s",'
            '"explanation":"...","confidence":0.0,"action":"one of %s"}]}'
            % ("|".join(CATEGORIES), "|".join(ACTIONS))
        )
        _bucket.take(estimate)
        raw = _post(body, key, stats)

    choice = raw["choices"][0]
    # A truncated array is not a partial success. Groq returns 200 with whatever
    # fitted, so without this check the missing rows look like records the model
    # chose not to answer, and get written off as unavailable.
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
    """Ask about a batch, halving it to recover whatever did not come back whole.

    Two things are retried smaller rather than written off: a request the model
    could not fit an answer into, and a well-formed answer that simply left rows
    out. In both cases the records are fine and the request was too big, so
    marking them unavailable would blame the data for a budgeting mistake.
    """
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
            # One half failing must not throw away the other half's answers.
            # Records with nothing to show for them are marked unavailable
            # individually by the caller, which is the honest granularity.
            try:
                merged.extend(_ask_split(half, model, key, stats)["results"])
            except Exception as exc:                  # noqa: BLE001
                failures.append(str(exc))
        if not merged and failures:
            raise _NeedsSmallerBatch("; ".join(failures)[:200])
        return {"results": merged}

    answered = {str(r.get("id") or r.get("exception_id")) for r in out["results"]}
    missing = [pair for pair in batch if pair[0] not in answered]
    if missing and len(missing) < len(batch):
        stats["rows_refetched"] = int(stats.get("rows_refetched", 0)) + len(missing)
        # Same rule as above: failing to recover the stragglers is not a reason
        # to discard the rows that did come back.
        try:
            out["results"].extend(_ask_split(missing, model, key, stats)["results"])
        except Exception as exc:                      # noqa: BLE001
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
        # Batches the model could not answer whole, and the rows recovered by
        # asking again in smaller pieces. Both should normally be 0; a run where
        # they climb is a run where MAX_TOKENS_PER_RECORD wants re-measuring.
        "batch_splits": 0,
        "rows_refetched": 0,
        "bucket_wait_ms": 0.0,
        "mode": "mock" if use_mock() else "groq",
        "model": None,
        "budget": budget_status(),
        "errors": [],
    }


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def explain(
    payloads: list[tuple[str, dict[str, Any]]], progress: Any = None
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """payloads: (exception_id, compact_payload) pairs, compacted by compact_payload().

    Returns (results_by_exception_id, stats). Never raises: if Groq is
    unreachable every exception comes back marked source='unavailable' rather
    than invented.
    """
    stats = _blank_stats(len(payloads))
    if not payloads:
        return {}, stats

    cache = _load_cache()
    results: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, dict[str, Any]]] = []

    # Cache first, always, and before anything that could fail. A cached answer
    # is served whether or not there is a key, a network, or a budget left.
    for eid, compact in payloads:
        hit = cache.get(fingerprint(compact))
        if hit:
            results[eid] = dict(hit, cached=True)
            stats["from_cache"] += 1
            # What that hit would have cost had we sent it.
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

    # ---- mock mode: no network, no key, no budget ------------------------
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

    # ---- live ------------------------------------------------------------
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
        # The call budget and the token counters are both claimed inside _ask
        # now, because a split batch is more than one request and each half has
        # to be accounted for on its own.
        try:
            out = _ask_split(batch, model, key, stats)
            err = ""
        except Exception as exc:            # noqa: BLE001 - the API surface is wide
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
    """Backwards-compatible entry point taking verbose payloads with exception_id.

    Kept so scripts written against the first version still run. New callers
    should use explain() with compact_payload().
    """
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
