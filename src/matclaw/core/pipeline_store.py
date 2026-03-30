"""
SQLite-backed pipeline and pipeline-run persistence.
Modeled after src/matclaw/memory/session_store.py.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


class PipelineStore:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._init_db()

    # ── Connection ─────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS pipelines (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL DEFAULT 'Untitled Pipeline',
                    description TEXT NOT NULL DEFAULT '',
                    nodes_json  TEXT NOT NULL DEFAULT '[]',
                    edges_json  TEXT NOT NULL DEFAULT '[]',
                    created_at  INTEGER NOT NULL,
                    updated_at  INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    id          TEXT PRIMARY KEY,
                    pipeline_id TEXT NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'pending',
                    started_at  INTEGER NOT NULL,
                    finished_at INTEGER,
                    result_json TEXT
                );
            """)

    # ── Pipeline CRUD ──────────────────────────────────────────────────────

    def list_pipelines(self) -> list[dict[str, Any]]:
        """Return all pipelines without nodes/edges (for sidebar speed)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, name, description, created_at, updated_at FROM pipelines ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_pipeline(self, pipeline_id: str) -> dict[str, Any] | None:
        """Return full pipeline including nodes and edges."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["nodes"] = json.loads(data.pop("nodes_json"))
        data["edges"] = json.loads(data.pop("edges_json"))
        return data

    def upsert_pipeline(self, pipeline: dict[str, Any]) -> dict[str, Any]:
        """Insert or replace a pipeline. Generates id/timestamps if missing."""
        now = int(time.time() * 1000)
        pipeline_id = pipeline.get("id") or str(uuid.uuid4())
        nodes_json = json.dumps(pipeline.get("nodes", []))
        edges_json = json.dumps(pipeline.get("edges", []))

        with self._conn() as conn:
            # Check if existing to preserve created_at
            existing = conn.execute(
                "SELECT created_at FROM pipelines WHERE id = ?", (pipeline_id,)
            ).fetchone()
            created_at = existing["created_at"] if existing else now

            conn.execute(
                """INSERT OR REPLACE INTO pipelines
                   (id, name, description, nodes_json, edges_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    pipeline_id,
                    pipeline.get("name", "Untitled Pipeline"),
                    pipeline.get("description", ""),
                    nodes_json,
                    edges_json,
                    created_at,
                    now,
                ),
            )

        return {**pipeline, "id": pipeline_id, "created_at": created_at, "updated_at": now}

    def delete_pipeline(self, pipeline_id: str) -> bool:
        with self._conn() as conn:
            rows = conn.execute(
                "DELETE FROM pipelines WHERE id = ?", (pipeline_id,)
            ).rowcount
        return rows > 0

    # ── Pipeline Run CRUD ──────────────────────────────────────────────────

    def create_run(self, pipeline_id: str) -> str:
        """Create a new run record and return its run_id."""
        run_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO pipeline_runs (id, pipeline_id, status, started_at) VALUES (?, ?, 'running', ?)",
                (run_id, pipeline_id, now),
            )
        return run_id

    def update_run(self, run_id: str, status: str, result: dict[str, Any] | None = None) -> None:
        now = int(time.time() * 1000)
        with self._conn() as conn:
            conn.execute(
                "UPDATE pipeline_runs SET status = ?, finished_at = ?, result_json = ? WHERE id = ?",
                (status, now, json.dumps(result) if result else None, run_id),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        if data.get("result_json"):
            data["result"] = json.loads(data.pop("result_json"))
        else:
            data.pop("result_json", None)
            data["result"] = None
        return data

    def list_runs_for_pipeline(self, pipeline_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, pipeline_id, status, started_at, finished_at FROM pipeline_runs "
                "WHERE pipeline_id = ? ORDER BY started_at DESC LIMIT 20",
                (pipeline_id,),
            ).fetchall()
        return [dict(r) for r in rows]
