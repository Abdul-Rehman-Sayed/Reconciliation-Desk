from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUNS_DIR = Path(__file__).resolve().parents[1] / "data" / "runs"
_lock = threading.Lock()


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:12]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(run_id: str) -> Path:
    if not run_id.startswith("run_") or not run_id[4:].isalnum():
        raise ValueError("bad run id")
    return RUNS_DIR / (run_id + ".json")


def save(run: dict[str, Any]) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(run["run_id"])
    tmp = path.with_suffix(".tmp")
    with _lock:
        tmp.write_text(json.dumps(run, default=str), encoding="utf-8")
        os.replace(tmp, path)


def load(run_id: str) -> dict[str, Any] | None:
    path = _path(run_id)
    if not path.exists():
        return None
    with _lock:
        return json.loads(path.read_text(encoding="utf-8"))


def list_runs(limit: int = 25) -> list[dict[str, Any]]:
    if not RUNS_DIR.exists():
        return []
    out = []
    for path in sorted(RUNS_DIR.glob("run_*.json"), key=lambda p: p.stat().st_mtime,
                       reverse=True)[:limit]:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({
            "run_id": doc.get("run_id"),
            "created_at": doc.get("created_at"),
            "source": doc.get("source"),
            "dataset_profile": doc.get("dataset_profile"),
            "fingerprint": doc.get("fingerprint"),
            "match_rate_auto": doc.get("summary", {}).get("match_rate_auto"),
            "exceptions_total": doc.get("summary", {}).get("exceptions_total"),
            "llm_complete": doc.get("llm_complete", False),
            "thresholds_changed": doc.get("thresholds_changed") or {},
        })
    return out


def find_by_fingerprint(fingerprint: str) -> dict[str, Any] | None:
    if not fingerprint or not RUNS_DIR.exists():
        return None
    candidates: list[tuple[bool, float, dict[str, Any]]] = []
    for path in sorted(RUNS_DIR.glob("run_*.json"), key=lambda p: p.stat().st_mtime,
                       reverse=True):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if doc.get("fingerprint") != fingerprint:
            continue
        candidates.append((bool(doc.get("llm_complete")), path.stat().st_mtime, doc))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return candidates[0][2]


def latest_run_id() -> str | None:
    runs = list_runs(1)
    return runs[0]["run_id"] if runs else None
