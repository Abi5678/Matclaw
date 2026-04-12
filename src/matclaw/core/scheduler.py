"""
Background cron scheduler for MatClaw.
Builds on the existing JobManager infrastructure.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from matclaw.core.job_manager import JobManager
    from matclaw.core.runtimes import RuntimeRegistry

logger = logging.getLogger(__name__)


class ScheduledTask(BaseModel):
    id: str
    name: str
    cron_expression: str    # "@hourly" | "@daily" | "*/N * * * *" | "0 */N * * *"
    runtime: str            # "matlab" | "python" | "shell"
    command: str
    enabled: bool = True
    tenant_id: str = "default"
    created_at: int
    last_run_at: int | None = None
    next_run_at: int | None = None
    run_count: int = 0
    last_status: str | None = None  # "success" | "failed"


def _cron_to_interval(expr: str) -> int:
    """
    Parse a minimal subset of cron expressions to a repeat interval in seconds.
    Supports: @hourly, @daily, @weekly, "* * * * *", "*/N * * * *", "0 */N * * *"
    """
    expr = expr.strip()
    _aliases = {
        "@hourly":  3600,
        "@daily":   86400,
        "@weekly":  604800,
        "@monthly": 2592000,
    }
    if expr in _aliases:
        return _aliases[expr]
    if expr == "* * * * *":
        return 60
    parts = expr.split()
    if len(parts) == 5:
        m, h = parts[0], parts[1]
        if m.startswith("*/"):
            try:
                return int(m[2:]) * 60
            except ValueError:
                pass
        if m == "0" and h.startswith("*/"):
            try:
                return int(h[2:]) * 3600
            except ValueError:
                pass
    return 3600  # default: hourly


class Scheduler:
    """
    Tick-based background scheduler.
    Wakes every heartbeat_seconds, finds due tasks, submits them to JobManager.
    """

    def __init__(
        self,
        db_path: str,
        job_manager: "JobManager",
        runtime_registry: "RuntimeRegistry",
        heartbeat_seconds: float = 30.0,
    ) -> None:
        self._db = db_path
        self._job_manager = job_manager
        self._runtime_registry = runtime_registry
        self._heartbeat = heartbeat_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    cron_expression TEXT NOT NULL,
                    runtime TEXT NOT NULL DEFAULT 'shell',
                    command TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    created_at INTEGER NOT NULL,
                    last_run_at INTEGER,
                    next_run_at INTEGER,
                    run_count INTEGER NOT NULL DEFAULT 0,
                    last_status TEXT
                )
            """)

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._tick_loop, name="matclaw-scheduler", daemon=True
        )
        self._thread.start()
        logger.info("Scheduler started (heartbeat=%.0fs)", self._heartbeat)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Scheduler stopped.")

    def add_task(self, task: ScheduledTask) -> ScheduledTask:
        interval = _cron_to_interval(task.cron_expression)
        if task.next_run_at is None:
            task.next_run_at = int(time.time()) + interval
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO scheduled_tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (task.id, task.name, task.cron_expression, task.runtime,
                 task.command, int(task.enabled), task.tenant_id,
                 task.created_at, task.last_run_at, task.next_run_at,
                 task.run_count, task.last_status),
            )
        logger.info("Task scheduled: %s (%s)", task.name, task.cron_expression)
        return task

    def remove_task(self, task_id: str) -> bool:
        with self._conn() as conn:
            r = conn.execute("DELETE FROM scheduled_tasks WHERE id=?", (task_id,))
        return r.rowcount > 0

    def list_tasks(self, tenant_id: str | None = None) -> list[ScheduledTask]:
        with self._conn() as conn:
            if tenant_id:
                rows = conn.execute(
                    "SELECT * FROM scheduled_tasks WHERE tenant_id=? ORDER BY created_at DESC",
                    (tenant_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM scheduled_tasks ORDER BY created_at DESC"
                ).fetchall()
        return [ScheduledTask(**dict(r)) for r in rows]

    def get_task(self, task_id: str) -> ScheduledTask | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)
            ).fetchone()
        return ScheduledTask(**dict(row)) if row else None

    # ── Internal ───────────────────────────────────────────────────────────

    def _tick_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._check_and_dispatch()
            except Exception:
                logger.exception("Scheduler tick error")
            self._stop.wait(timeout=self._heartbeat)

    def _check_and_dispatch(self) -> None:
        now = int(time.time())
        with self._conn() as conn:
            due = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE enabled=1 AND next_run_at<=?",
                (now,),
            ).fetchall()
            # Atomically mark all due tasks as dispatched in the same transaction
            for row in due:
                task = ScheduledTask(**dict(row))
                interval = _cron_to_interval(task.cron_expression)
                conn.execute(
                    "UPDATE scheduled_tasks SET last_run_at=?, next_run_at=?, run_count=run_count+1 WHERE id=?",
                    (now, now + interval, task.id),
                )
        # Dispatch after commit so DB lock isn't held during execution
        for row in due:
            task = ScheduledTask(**dict(row))
            self._dispatch(task)

    def _dispatch(self, task: ScheduledTask) -> None:
        rt_reg = self._runtime_registry
        db_path = self._db
        task_id = task.id
        task_name = task.name

        def _job():
            result = rt_reg.execute(
                task.runtime, task.command,
                {"task": task.name, "scheduled": True, "tenant_id": task.tenant_id},
            )
            status = "success" if result.success else "failed"
            conn = sqlite3.connect(db_path, check_same_thread=False)
            try:
                conn.execute(
                    "UPDATE scheduled_tasks SET last_status=? WHERE id=?",
                    (status, task_id),
                )
                conn.commit()
            finally:
                conn.close()
            return result.output

        self._job_manager.submit_job(
            f"scheduled:{task_name}",
            _job,
            metadata={"task_id": task_id, "tenant_id": task.tenant_id},
        )
        logger.info("Dispatched scheduled task: %s (runtime=%s)", task_name, task.runtime)


__all__ = ["Scheduler", "ScheduledTask", "_cron_to_interval"]
