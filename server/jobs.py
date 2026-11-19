"""Background job runner with progress reporting, used for analysis and export."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable

from . import config, store

_ACTIVE: dict[str, dict] = {}
_LOCK = threading.RLock()


def new_id(prefix: str = "") -> str:
    return (prefix + uuid.uuid4().hex[:12]) if prefix else uuid.uuid4().hex[:12]


def job_file(job_id: str) -> Path:
    return config.JOBS / job_id / "job.json"


def job_dir(job_id: str) -> Path:
    d = config.JOBS / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def create(kind: str, title: str = "", **extra) -> dict:
    job = {
        "id": new_id(kind[:2]),
        "kind": kind,
        "title": title,
        "status": "queued",       # queued | running | done | error
        "stage": "Queued",
        "progress": 0.0,
        "message": "",
        "error": None,
        "created": time.time(),
        "updated": time.time(),
        "log": [],
        **extra,
    }
    with _LOCK:
        _ACTIVE[job["id"]] = job
    save(job)
    return job


def get(job_id: str) -> dict | None:
    with _LOCK:
        if job_id in _ACTIVE:
            return _ACTIVE[job_id]
    data = store.read_json(job_file(job_id))
    return data


def save(job: dict) -> None:
    job["updated"] = time.time()
    store.write_json(job_file(job["id"]), job)


def patch(job_id: str, **fields) -> dict | None:
    with _LOCK:
        job = _ACTIVE.get(job_id) or store.read_json(job_file(job_id))
        if not job:
            return None
        job.update(fields)
        _ACTIVE[job_id] = job
    save(job)
    return job


def update(job_id: str, progress: float | None = None, stage: str | None = None,
           message: str | None = None) -> None:
    with _LOCK:
        job = _ACTIVE.get(job_id)
        if not job:
            return
        if progress is not None:
            job["progress"] = max(0.0, min(1.0, float(progress)))
        if stage is not None:
            job["stage"] = stage
        if message is not None:
            job["message"] = message
    save(job)


def start(job_id: str, fn: Callable[[str], object]) -> None:
    """Run fn(job_id) on a daemon thread and record the outcome."""

    def runner() -> None:
        patch(job_id, status="running", stage="Starting", progress=0.01)
        try:
            fn(job_id)
        except Exception as exc:  # surfaced in the UI
            traceback.print_exc()
            patch(job_id, status="error", error=f"{type(exc).__name__}: {exc}",
                  stage="Failed", message=str(exc)[:400])
        else:
            patch(job_id, status="done", progress=1.0, stage="Done")

    threading.Thread(target=runner, daemon=True, name=f"job-{job_id}").start()


def running_for(project_id: str, kinds: tuple[str, ...] = ("export", "batch")) -> list[dict]:
    """Jobs of these kinds still queued/running for a project."""
    out = []
    for f in config.JOBS.glob("*/job.json"):
        data = read_json(f) if "read_json" in globals() else store.read_json(f)
        if not data:
            continue
        if (data.get("project_id") == project_id and data.get("kind") in kinds
                and data.get("status") in ("queued", "running")):
            out.append(data)
    return out


def public(job: dict) -> dict:
    keys = ("id", "kind", "title", "status", "stage", "progress", "message",
            "error", "created", "updated", "log")
    out = {k: job.get(k) for k in keys}
    for key in ("duration", "width", "height", "language", "clip_count", "files", "clip_id"):
        if key in job:
            out[key] = job[key]
    return out
