from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

JobCallable = Callable[[], Any] | Callable[[], Awaitable[Any]]


@dataclass
class JobRecord:
    job_id: str
    skill_name: str
    status: str
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None
    result: Any | None = None
    error: str | None = None


class JobManager:
    """
    Asyncio-backed job queue with configurable concurrency.

    Runs an event loop in a background thread so sync daemon code can submit jobs
    without blocking. Default concurrency is 1 for MATLAB engine safety.
    """

    def __init__(
        self,
        concurrency: int = 1,
        on_status_change: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._concurrency = max(1, int(concurrency))
        self._jobs: dict[str, JobRecord] = {}
        self._cancel_flags: set[str] = set()
        self._running_tasks: dict[str, asyncio.Task[Any]] = {}
        self._jobs_lock = threading.Lock()
        self._queue: asyncio.Queue[tuple[str, JobCallable, float | None]] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._workers_started = threading.Event()
        self._on_status_change = on_status_change

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._shutdown.clear()
        self._thread = threading.Thread(target=self._run_loop, name="matclaw-job-manager", daemon=True)
        self._thread.start()
        self._workers_started.wait(timeout=3)

    def stop(self) -> None:
        self._shutdown.set()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None
        self._loop = None
        self._queue = None
        self._workers_started.clear()

    def submit_job(
        self,
        skill_name: str,
        job_callable: JobCallable,
        *,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        self.start()
        job_id = uuid.uuid4().hex[:12]
        rec = JobRecord(
            job_id=job_id,
            skill_name=skill_name,
            status="queued",
            created_at=self._now(),
            metadata=metadata or {},
        )
        with self._jobs_lock:
            self._jobs[job_id] = rec
        if self._loop is None or self._queue is None:
            raise RuntimeError("JobManager loop is not running.")
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (job_id, job_callable, timeout_seconds))
        return job_id

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return {"job_id": job_id, "status": "not_found"}
            return {
                "job_id": rec.job_id,
                "skill_name": rec.skill_name,
                "status": rec.status,
                "created_at": rec.created_at,
                "started_at": rec.started_at,
                "completed_at": rec.completed_at,
                "result": rec.result,
                "error": rec.error,
                "metadata": rec.metadata,
            }

    def list_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._jobs_lock:
            records = list(self._jobs.values())
        records.sort(key=lambda r: r.created_at, reverse=True)
        return [self.get_job_status(r.job_id) for r in records[: max(1, limit)]]

    def get_summary(self) -> dict[str, Any]:
        with self._jobs_lock:
            total = len(self._jobs)
            statuses = [r.status for r in self._jobs.values()]
            queued = sum(1 for s in statuses if s == "queued")
            running = sum(1 for s in statuses if s == "running")
            done = sum(1 for s in statuses if s == "done")
            failed = sum(1 for s in statuses if s == "failed")
            timeout = sum(1 for s in statuses if s == "timeout")
            cancelled = sum(1 for s in statuses if s == "cancelled")
        return {
            "total": total,
            "queued": queued,
            "running": running,
            "done": done,
            "failed": failed,
            "timeout": timeout,
            "cancelled": cancelled,
        }

    def cancel_job(self, job_id: str) -> bool:
        with self._jobs_lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return False
            self._cancel_flags.add(job_id)
            if rec.status == "queued":
                rec.status = "cancelled"
                rec.completed_at = self._now()
            running = self._running_tasks.get(job_id)
        if running is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(running.cancel)
        return True

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._queue = asyncio.Queue()
        workers = [loop.create_task(self._worker(i)) for i in range(self._concurrency)]
        self._workers_started.set()
        try:
            loop.run_forever()
        finally:
            for w in workers:
                w.cancel()
            try:
                loop.run_until_complete(asyncio.gather(*workers, return_exceptions=True))
            except Exception:
                pass
            loop.close()

    async def _worker(self, worker_idx: int) -> None:
        assert self._queue is not None
        while not self._shutdown.is_set():
            try:
                job_id, fn, timeout_seconds = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            await self._execute_job(job_id, fn, timeout_seconds, worker_idx)
            self._queue.task_done()

    async def _execute_job(self, job_id: str, fn: JobCallable, timeout_seconds: float | None, worker_idx: int) -> None:
        with self._jobs_lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return
            if job_id in self._cancel_flags:
                rec.status = "cancelled"
                rec.completed_at = self._now()
                return
            rec.status = "running"
            rec.started_at = self._now()
            snapshot = self._snapshot_locked(rec)
        logger.info("Job started: %s (%s) [worker=%s]", job_id, rec.skill_name, worker_idx)
        self._emit_status(snapshot)

        async def _run() -> Any:
            if inspect.iscoroutinefunction(fn):
                return await fn()  # type: ignore[misc]
            result = await asyncio.to_thread(fn)
            if inspect.isawaitable(result):
                return await result
            return result

        try:
            task = asyncio.create_task(_run())
            with self._jobs_lock:
                self._running_tasks[job_id] = task
            result = await asyncio.wait_for(task, timeout=timeout_seconds) if timeout_seconds else await task
            with self._jobs_lock:
                rec.status = "done"
                rec.completed_at = self._now()
                rec.result = result
                snapshot = self._snapshot_locked(rec)
            logger.info("Job done: %s", job_id)
            self._emit_status(snapshot)
        except asyncio.TimeoutError:
            with self._jobs_lock:
                rec.status = "timeout"
                rec.completed_at = self._now()
                rec.error = f"Job timed out after {timeout_seconds:.1f}s" if timeout_seconds else "Job timed out."
                snapshot = self._snapshot_locked(rec)
            logger.warning("Job timeout: %s", job_id)
            self._emit_status(snapshot)
        except asyncio.CancelledError:
            with self._jobs_lock:
                rec.status = "cancelled"
                rec.completed_at = self._now()
                snapshot = self._snapshot_locked(rec)
            logger.info("Job cancelled: %s", job_id)
            self._emit_status(snapshot)
        except Exception as exc:
            logger.exception("Job failed: %s", job_id)
            with self._jobs_lock:
                rec.status = "failed"
                rec.completed_at = self._now()
                rec.error = str(exc)
                snapshot = self._snapshot_locked(rec)
            self._emit_status(snapshot)
        finally:
            with self._jobs_lock:
                self._running_tasks.pop(job_id, None)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _emit_status(self, snapshot: dict[str, Any]) -> None:
        if self._on_status_change is None:
            return
        try:
            self._on_status_change(snapshot)
        except Exception:
            logger.exception("JobManager on_status_change callback failed.")

    @staticmethod
    def _snapshot_locked(rec: JobRecord) -> dict[str, Any]:
        return {
            "job_id": rec.job_id,
            "skill_name": rec.skill_name,
            "status": rec.status,
            "created_at": rec.created_at,
            "started_at": rec.started_at,
            "completed_at": rec.completed_at,
            "result": rec.result,
            "error": rec.error,
            "metadata": rec.metadata,
        }


__all__ = ["JobManager", "JobRecord"]
