"""Background runs: submitting returns a job id at once; one worker thread runs them.

Runs are executed one at a time in submission order by a single scheduler thread, so
the server stays responsive and SQLite writes are never concurrent. A failure is caught
at the job boundary and reported on the job. Jobs live in memory: a run that finished
is saved in the run store; a job that was queued or running when the server stopped is
simply gone and can be submitted again.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

JobStatus = Literal["queued", "running", "complete", "failed"]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    kind: str
    status: JobStatus = "queued"
    submitted_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    run_ids: list[str] = field(default_factory=list)
    error: str | None = None


class JobRunner:
    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="moneylab-job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[], list[str]]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn: Callable[[], list[str]]) -> None:
        job.status, job.started_at = "running", _now()
        try:
            job.run_ids = fn()
            job.status = "complete"
        except Exception as exc:  # surfaced on the job, never crashes the server
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            job.finished_at = _now()

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._jobs[job_id]

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.submitted_at, reverse=True)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
