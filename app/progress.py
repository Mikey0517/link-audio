"""In-memory extraction progress shared by the API and the browser polling."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

ProgressReporter = Callable[[int, str], None]


@dataclass
class ProgressJob:
    job_id: str
    percent: int = 0
    stage: str = "排队中"
    status: str = "running"
    error: str | None = None
    updated_at: float = field(default_factory=time.monotonic)

    def report(self, percent: int, stage: str) -> None:
        if self.status != "running":
            return
        try:
            value = int(percent)
        except (TypeError, ValueError):
            return
        self.percent = max(self.percent, min(99, max(0, value)))
        self.stage = str(stage or self.stage)
        self.updated_at = time.monotonic()

    def complete(self) -> None:
        self.percent = 100
        self.stage = "完成"
        self.status = "completed"
        self.updated_at = time.monotonic()

    def fail(self, code: str, message: str) -> None:
        self.status = "failed"
        self.error = f"{code}: {message}" if code else message
        self.stage = "失败"
        self.updated_at = time.monotonic()

    def snapshot(self) -> dict:
        return {
            "job_id": self.job_id,
            "percent": self.percent,
            "stage": self.stage,
            "status": self.status,
            "error": self.error,
        }


class ProgressStore:
    """Small TTL store; progress is intentionally process-local."""

    def __init__(self, ttl_seconds: float = 900.0) -> None:
        self._jobs: dict[str, ProgressJob] = {}
        self._ttl = ttl_seconds

    def create(self, job_id: str) -> ProgressJob:
        self.cleanup()
        job = ProgressJob(job_id=job_id)
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> ProgressJob | None:
        self.cleanup()
        return self._jobs.get(job_id)

    def cleanup(self) -> None:
        now = time.monotonic()
        stale = [job_id for job_id, job in self._jobs.items() if now - job.updated_at > self._ttl]
        for job_id in stale:
            self._jobs.pop(job_id, None)
