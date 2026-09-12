#!/usr/bin/env python3
"""Bounded background job queue for Sprite Lab maintenance routes.

Why this exists: the historical ``POST /api/reindex``,
``POST /api/relationships[/delete]`` and ``POST /api/env-atlas`` (sync mode)
ran Blender/catalog rebuilds *inside the HTTP handler thread*, blocking it
for minutes. Under SaaS load that exhausts handler threads and couples one
slow client to every other user.

Contract (shared by all three routes):

- ``POST`` validates the cheap part, enqueues, answers ``202`` immediately;
- the heavy work runs in a bounded ``ThreadPoolExecutor``;
- the frontend polls ``GET .../jobs/{id}`` until ``done``/``error``.

Only stdlib is used so the project keeps its no-dependency deploy story.
Tune via environment:

- ``SPRITE_LAB_JOB_WORKERS``: max background workers (default 4);
- ``SPRITE_LAB_MAINTENANCE_JOBS_PATH``: JSON store for maintenance jobs.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

BASE = Path(__file__).resolve().parent
DEFAULT_JOBS_PATH = BASE / "state" / "maintenance_jobs.json"


def jobs_path() -> Path:
    configured = os.environ.get("SPRITE_LAB_MAINTENANCE_JOBS_PATH", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_JOBS_PATH


def max_workers() -> int:
    try:
        return max(1, int(os.environ.get("SPRITE_LAB_JOB_WORKERS", "") or 4))
    except (TypeError, ValueError):
        return 4


_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None
_interrupt = threading.Event()


def _executor_instance() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=max_workers(), thread_name_prefix="sprite-lab-jobs")
        return _executor


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_jobs(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _write_jobs(path: Path, jobs: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(jobs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def list_jobs(path: Path | None = None) -> list[dict[str, Any]]:
    return _read_jobs(path or jobs_path())


def get_job(job_id: str, path: Path | None = None) -> dict[str, Any] | None:
    return next((job for job in list_jobs(path) if job.get("id") == job_id), None)


def _update_job(path: Path, job_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    with _lock:
        jobs = _read_jobs(path)
        for job in jobs:
            if job.get("id") == job_id:
                job.update(patch)
                _write_jobs(path, jobs)
                return job
    return None


def enqueue(kind: str, payload: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """Persist a ``queued`` job and return it (the HTTP layer answers 202)."""
    target = path or jobs_path()
    job = {
        "id": f"job_{uuid.uuid4().hex[:12]}",
        "kind": kind,
        "status": "queued",
        "created_at": utc_now(),
        "payload": payload,
    }
    with _lock:
        jobs = _read_jobs(target)
        jobs.append(job)
        _write_jobs(target, jobs)
    return job


def _run_job(job_id: str, target: Path, func: Callable[[], dict[str, Any]]) -> None:
    _update_job(target, job_id, {"status": "running", "started_at": utc_now()})
    try:
        result = func()
    except Exception as exc:  # noqa: BLE001 - job errors are returned via polling.
        if _interrupt.is_set():
            _update_job(
                target, job_id, {"status": "cancelled", "finished_at": utc_now(), "error": str(exc)}
            )
        else:
            _update_job(
                target, job_id, {"status": "error", "finished_at": utc_now(), "error": str(exc)}
            )
        return
    if not isinstance(result, dict):
        result = {"result": result}
    _update_job(target, job_id, {"status": "done", "finished_at": utc_now(), "result": result})


def submit(
    kind: str,
    payload: dict[str, Any],
    func: Callable[[], dict[str, Any]],
    path: Path | None = None,
) -> dict[str, Any]:
    """Enqueue ``func`` on the bounded executor and return the ``queued`` job."""
    target = path or jobs_path()
    job = enqueue(kind, payload, target)
    _executor_instance().submit(_run_job, job["id"], target, func)
    return job


def run_in_background(func: Callable, *args: Any, **kwargs: Any) -> None:
    """Run ``func`` on the shared bounded executor (fire-and-forget).

    Used by routes that keep their own job store (env-atlas) but should not
    spawn unbounded OS threads per request.
    """
    _executor_instance().submit(func, *args, **kwargs)


def reset_executor() -> None:
    """Drop the shared executor. Used by unit tests only."""
    global _executor
    with _lock:
        _executor = None
    _interrupt.clear()


def interrupt_event() -> threading.Event:
    """Cooperative interrupt flag polled by cancellable workers."""
    return _interrupt


def interrupt_all(path: Path | None = None) -> int:
    """Flag running jobs for interruption and mark queued jobs cancelled."""
    target = path or jobs_path()
    _interrupt.set()
    cancelled = 0
    with _lock:
        jobs = _read_jobs(target)
        for job in jobs:
            if job.get("status") == "queued":
                job.update({"status": "cancelled", "finished_at": utc_now()})
                cancelled += 1
        _write_jobs(target, jobs)
    return cancelled


def clear_interrupt() -> None:
    _interrupt.clear()
