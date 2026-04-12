"""
Archival and cleanup utilities for MatClaw long-term memory.

Policies:
- Raw experiments older than N days → moved to archive SQLite.
- ChromaDB artifacts that have been consolidated → pruned after N days.
- Near-duplicate knowledge entries → deduplicated by embedding distance.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from matclaw.core.experiment import ExperimentTracker
    from matclaw.memory.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)


class ArchivalManager:
    """Manages cleanup, deduplication, and archival of MatClaw memory stores."""

    def __init__(
        self,
        experiment_tracker: "ExperimentTracker",
        knowledge_base: "KnowledgeBase",
        archive_db_path: str | Path = ".matclaw_archive.sqlite3",
    ) -> None:
        self._tracker = experiment_tracker
        self._kb = knowledge_base
        self._archive_path = Path(archive_db_path).expanduser().resolve()

    # ------------------------------------------------------------------
    # Archive old experiments
    # ------------------------------------------------------------------

    def archive_old_experiments(self, older_than_days: int = 180) -> int:
        """
        Move experiments older than *older_than_days* to the archive
        SQLite database.  Returns the number of experiments archived.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()

        # Ensure archive DB has the same schema
        self._archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_conn = sqlite3.connect(str(self._archive_path), check_same_thread=False)
        archive_conn.execute(
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
        archive_conn.commit()

        # Fetch old records from main DB
        main_conn = self._tracker._connect()
        rows = main_conn.execute(
            "SELECT * FROM experiments WHERE started_at < ? AND status IN ('success', 'failed', 'timeout')",
            (cutoff,),
        ).fetchall()

        if not rows:
            archive_conn.close()
            main_conn.close()
            return 0

        archived = 0
        for row in rows:
            try:
                archive_conn.execute(
                    """
                    INSERT OR REPLACE INTO experiments
                        (experiment_id, skill_name, params_json, metrics_json, artifacts_json,
                         started_at, completed_at, duration_seconds, status, error, parent_id, tags_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["experiment_id"], row["skill_name"], row["params_json"],
                        row["metrics_json"], row["artifacts_json"], row["started_at"],
                        row["completed_at"], row["duration_seconds"], row["status"],
                        row["error"], row["parent_id"], row["tags_json"],
                    ),
                )
                main_conn.execute(
                    "DELETE FROM experiments WHERE experiment_id = ?",
                    (row["experiment_id"],),
                )
                archived += 1
            except Exception:
                logger.exception("Failed to archive experiment %s", row["experiment_id"])

        archive_conn.commit()
        main_conn.commit()
        archive_conn.close()
        main_conn.close()

        logger.info("Archived %d experiments older than %d days.", archived, older_than_days)
        return archived

    # ------------------------------------------------------------------
    # Deduplicate knowledge
    # ------------------------------------------------------------------

    def deduplicate_knowledge(self, distance_threshold: float = 0.05) -> int:
        """
        Remove near-duplicate entries from the knowledge base collection.
        Two entries are considered duplicates if their embedding cosine
        distance is below *distance_threshold* and they share the same type.

        Returns the number of entries removed.
        """
        self._kb._ensure_collection()
        collection = self._kb._collection

        all_entries = collection.get(include=["metadatas", "embeddings"])
        ids = all_entries.get("ids") or []
        metas = all_entries.get("metadatas") or []
        embeddings = all_entries.get("embeddings") or []

        if len(ids) < 2 or not embeddings:
            return 0

        to_remove: set[str] = set()
        # O(n^2) but knowledge base should stay small (hundreds, not millions)
        for i in range(len(ids)):
            if ids[i] in to_remove:
                continue
            for j in range(i + 1, len(ids)):
                if ids[j] in to_remove:
                    continue
                # Same type check
                type_i = (metas[i] or {}).get("type", "")
                type_j = (metas[j] or {}).get("type", "")
                if type_i != type_j:
                    continue
                # Cosine distance
                dist = self._cosine_distance(embeddings[i], embeddings[j])
                if dist < distance_threshold:
                    to_remove.add(ids[j])

        if to_remove:
            collection.delete(ids=list(to_remove))
            logger.info("Deduplicated %d near-duplicate knowledge entries.", len(to_remove))

        return len(to_remove)

    @staticmethod
    def _cosine_distance(a: list[float], b: list[float]) -> float:
        """Compute cosine distance between two vectors."""
        if not a or not b or len(a) != len(b):
            return 1.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 1.0
        return 1.0 - (dot / (norm_a * norm_b))

    # ------------------------------------------------------------------
    # Prune old artifacts
    # ------------------------------------------------------------------

    def prune_old_artifacts(self, older_than_days: int = 90) -> int:
        """
        Remove raw artifacts from the ``matclaw_artifacts`` collection
        that are older than *older_than_days*.  Knowledge entries in
        ``matclaw_knowledge`` are never pruned by this method.

        Returns the number of artifacts removed.
        """
        mm = self._kb._mm
        mm._ensure_client()
        collection = mm._collection
        if collection is None:
            return 0

        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()

        # ChromaDB doesn't support date comparison natively.
        # Fetch all and filter in Python.
        all_entries = collection.get(include=["metadatas"])
        ids = all_entries.get("ids") or []
        metas = all_entries.get("metadatas") or []

        to_remove: list[str] = []
        for entry_id, meta in zip(ids, metas):
            created = (meta or {}).get("created_at", "")
            if created and created < cutoff:
                to_remove.append(entry_id)

        if to_remove:
            collection.delete(ids=to_remove)
            logger.info("Pruned %d old artifacts (older than %d days).", len(to_remove), older_than_days)

        return len(to_remove)
