"""
Daemon heartbeat — orchestrates all background services.
Started/stopped via FastAPI lifespan context manager.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from matclaw.core.scheduler import Scheduler
    from matclaw.core.job_manager import JobManager

logger = logging.getLogger(__name__)


class Heartbeat:
    """
    Top-level daemon orchestrator.
    Starts/stops all background services and exposes a status snapshot.
    """

    def __init__(
        self,
        scheduler: "Scheduler | None" = None,
        job_manager: "JobManager | None" = None,
    ) -> None:
        self._scheduler = scheduler
        self._job_manager = job_manager
        self._started_at: float | None = None
        self._tick_count = 0

    def start(self) -> None:
        self._started_at = time.time()
        if self._job_manager:
            self._job_manager.start()
        if self._scheduler:
            self._scheduler.start()
        logger.info("MatClaw daemon started.")

    def stop(self) -> None:
        if self._scheduler:
            self._scheduler.stop()
        if self._job_manager:
            self._job_manager.stop()
        logger.info("MatClaw daemon stopped.")

    def status(self) -> dict:
        job_summary = {}
        if self._job_manager:
            try:
                job_summary = self._job_manager.get_summary()
            except Exception:
                pass

        return {
            "uptime_seconds": int(time.time() - self._started_at) if self._started_at else 0,
            "scheduler_running": self._scheduler is not None,
            "job_manager_running": self._job_manager is not None,
            "jobs": job_summary,
        }


__all__ = ["Heartbeat"]
