from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExperimentRecord(BaseModel):
    experiment_id: str
    skill_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    status: str = "running"  # running|success|failed|timeout
    error: Optional[str] = None
    parent_id: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class ExperimentTracker:
    """SQLite-backed experiment tracking for RPI runs."""

    def __init__(self, db_path: str | Path = ".matclaw_experiments.sqlite3") -> None:
        self._db_path = Path(db_path).expanduser().resolve()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    skill_name TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    duration_seconds REAL,
                    status TEXT NOT NULL,
                    error TEXT,
                    parent_id TEXT,
                    tags_json TEXT NOT NULL
                )
                """
            )
            # Indexes for efficient date-range and ranking queries
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_experiments_started ON experiments(started_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_experiments_skill_started ON experiments(skill_name, started_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status)"
            )

            # Consolidation tracking table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS consolidation_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_name TEXT NOT NULL,
                    consolidated_at TEXT NOT NULL,
                    experiments_processed INTEGER,
                    insights_generated INTEGER,
                    last_experiment_id TEXT
                )
                """
            )
            conn.commit()

    def start_experiment(
        self,
        *,
        skill_name: str,
        params: dict[str, Any] | None = None,
        parent_id: str | None = None,
        tags: list[str] | None = None,
    ) -> ExperimentRecord:
        rec = ExperimentRecord(
            experiment_id=uuid.uuid4().hex[:16],
            skill_name=skill_name,
            params=params or {},
            started_at=_utc_now(),
            parent_id=parent_id,
            tags=tags or [],
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO experiments (
                    experiment_id, skill_name, params_json, metrics_json, artifacts_json,
                    started_at, completed_at, duration_seconds, status, error, parent_id, tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.experiment_id,
                    rec.skill_name,
                    json.dumps(rec.params),
                    json.dumps(rec.metrics),
                    json.dumps(rec.artifacts),
                    rec.started_at.isoformat(),
                    None,
                    None,
                    rec.status,
                    None,
                    rec.parent_id,
                    json.dumps(rec.tags),
                ),
            )
            conn.commit()
        return rec

    def log_metric(self, experiment_id: str, key: str, value: Any) -> None:
        rec = self.get_experiment(experiment_id)
        if rec is None:
            return
        rec.metrics[key] = value
        self._update_record(rec)

    def log_artifact(self, experiment_id: str, path: str, artifact_type: str | None = None) -> None:
        rec = self.get_experiment(experiment_id)
        if rec is None:
            return
        rec.artifacts.append(path if artifact_type is None else f"{artifact_type}:{path}")
        self._update_record(rec)

    def finish_experiment(
        self,
        experiment_id: str,
        *,
        status: str,
        error: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        rec = self.get_experiment(experiment_id)
        if rec is None:
            return
        rec.completed_at = _utc_now()
        rec.duration_seconds = max(0.0, (rec.completed_at - rec.started_at).total_seconds())
        rec.status = status
        rec.error = error
        if metrics:
            rec.metrics.update(metrics)
        self._update_record(rec)

    def get_experiment(self, experiment_id: str) -> ExperimentRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_experiments(self, skill_name: str | None = None, last_n: int = 20) -> list[ExperimentRecord]:
        query = "SELECT * FROM experiments"
        args: list[Any] = []
        if skill_name:
            query += " WHERE skill_name = ?"
            args.append(skill_name)
        query += " ORDER BY started_at DESC LIMIT ?"
        args.append(max(1, int(last_n)))
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, tuple(args)).fetchall()
        return [self._row_to_record(r) for r in rows]

    def compare(self, experiment_ids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for eid in experiment_ids:
            rec = self.get_experiment(eid)
            if rec:
                out.append(rec.model_dump(mode="json"))
        return out

    def trend(self, skill_name: str, metric_key: str, last_n: int = 20) -> list[dict[str, Any]]:
        rows = self.list_experiments(skill_name=skill_name, last_n=last_n)
        points: list[dict[str, Any]] = []
        for r in reversed(rows):
            if metric_key in r.metrics:
                points.append(
                    {
                        "experiment_id": r.experiment_id,
                        "started_at": r.started_at.isoformat(),
                        "value": r.metrics.get(metric_key),
                    }
                )
        return points

    # ------------------------------------------------------------------
    # Long-term memory query methods
    # ------------------------------------------------------------------

    def list_experiments_in_range(
        self,
        start_date: datetime,
        end_date: datetime,
        skill_name: str | None = None,
    ) -> list[ExperimentRecord]:
        """Return experiments within a date range, optionally filtered by skill."""
        query = "SELECT * FROM experiments WHERE started_at >= ? AND started_at <= ?"
        args: list[Any] = [start_date.isoformat(), end_date.isoformat()]
        if skill_name:
            query += " AND skill_name = ?"
            args.append(skill_name)
        query += " ORDER BY started_at DESC"
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, tuple(args)).fetchall()
        return [self._row_to_record(r) for r in rows]

    def best_experiment(
        self,
        skill_name: str,
        metric_key: str,
        minimize: bool = False,
        last_n: int = 100,
    ) -> ExperimentRecord | None:
        """
        Return the experiment with the best value for *metric_key*.
        Fetches last_n successful experiments and ranks in Python
        (safe regardless of SQLite version).
        """
        query = (
            "SELECT * FROM experiments "
            "WHERE skill_name = ? AND status = 'success' "
            "ORDER BY started_at DESC LIMIT ?"
        )
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, (skill_name, max(1, last_n))).fetchall()
        records = [self._row_to_record(r) for r in rows]
        candidates = [(r, r.metrics.get(metric_key)) for r in records if metric_key in r.metrics]
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1], reverse=not minimize)
        return candidates[0][0]

    def trend_in_range(
        self,
        skill_name: str,
        metric_key: str,
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict[str, Any]]:
        """Return metric trend within a date range."""
        records = self.list_experiments_in_range(start_date, end_date, skill_name=skill_name)
        points: list[dict[str, Any]] = []
        for r in reversed(records):
            if metric_key in r.metrics:
                points.append({
                    "experiment_id": r.experiment_id,
                    "started_at": r.started_at.isoformat(),
                    "value": r.metrics[metric_key],
                })
        return points

    def skill_summary(self, skill_name: str, last_n: int = 50) -> dict[str, Any]:
        """
        Aggregate statistics for a skill: total runs, success rate,
        average duration, best/worst for each metric.
        """
        records = self.list_experiments(skill_name=skill_name, last_n=last_n)
        if not records:
            return {"skill_name": skill_name, "total_runs": 0}
        total = len(records)
        successes = [r for r in records if r.status == "success"]
        durations = [r.duration_seconds for r in records if r.duration_seconds is not None]
        # Collect all metric keys
        all_metrics: dict[str, list[float]] = {}
        for r in successes:
            for k, v in r.metrics.items():
                if isinstance(v, (int, float)):
                    all_metrics.setdefault(k, []).append(v)
        metric_stats: dict[str, dict[str, float]] = {}
        for k, vals in all_metrics.items():
            metric_stats[k] = {
                "min": min(vals),
                "max": max(vals),
                "avg": sum(vals) / len(vals),
                "count": len(vals),
            }
        return {
            "skill_name": skill_name,
            "total_runs": total,
            "success_count": len(successes),
            "success_rate": round(len(successes) / total, 3) if total else 0,
            "avg_duration_seconds": round(sum(durations) / len(durations), 2) if durations else None,
            "metric_stats": metric_stats,
        }

    def log_consolidation(
        self,
        skill_name: str,
        experiments_processed: int,
        insights_generated: int,
        last_experiment_id: str = "",
    ) -> None:
        """Record a consolidation run in the consolidation_log table."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO consolidation_log
                    (skill_name, consolidated_at, experiments_processed, insights_generated, last_experiment_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    skill_name,
                    _utc_now().isoformat(),
                    experiments_processed,
                    insights_generated,
                    last_experiment_id,
                ),
            )
            conn.commit()

    def last_consolidation(self, skill_name: str) -> dict[str, Any] | None:
        """Return the most recent consolidation log for a skill."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM consolidation_log WHERE skill_name = ? ORDER BY consolidated_at DESC LIMIT 1",
                (skill_name,),
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def _update_record(self, rec: ExperimentRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE experiments
                SET
                    params_json = ?,
                    metrics_json = ?,
                    artifacts_json = ?,
                    started_at = ?,
                    completed_at = ?,
                    duration_seconds = ?,
                    status = ?,
                    error = ?,
                    parent_id = ?,
                    tags_json = ?
                WHERE experiment_id = ?
                """,
                (
                    json.dumps(rec.params),
                    json.dumps(rec.metrics),
                    json.dumps(rec.artifacts),
                    rec.started_at.isoformat(),
                    rec.completed_at.isoformat() if rec.completed_at else None,
                    rec.duration_seconds,
                    rec.status,
                    rec.error,
                    rec.parent_id,
                    json.dumps(rec.tags),
                    rec.experiment_id,
                ),
            )
            conn.commit()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ExperimentRecord:
        return ExperimentRecord(
            experiment_id=row["experiment_id"],
            skill_name=row["skill_name"],
            params=json.loads(row["params_json"] or "{}"),
            metrics=json.loads(row["metrics_json"] or "{}"),
            artifacts=json.loads(row["artifacts_json"] or "[]"),
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            duration_seconds=row["duration_seconds"],
            status=row["status"],
            error=row["error"],
            parent_id=row["parent_id"],
            tags=json.loads(row["tags_json"] or "[]"),
        )


__all__ = ["ExperimentRecord", "ExperimentTracker"]
