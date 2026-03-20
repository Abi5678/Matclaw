"""
Typed semantic knowledge store for MatClaw long-term memory.

Uses a **separate ChromaDB collection** (``matclaw_knowledge``) that shares the
existing ``PersistentClient`` from :class:`MemoryManager`.  This keeps raw
artifacts cleanly separated from curated knowledge entries.

Knowledge types:
- result    — experiment outcome with params + metrics
- lesson    — cause→effect observation from comparing runs
- strategy  — higher-level approach distilled by LLM consolidation
- failure   — what went wrong and why
- debug_fix — successful MATLAB code fix from the debug agent
- insight   — cross-skill pattern discovered during consolidation
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """Typed semantic knowledge layer on top of ChromaDB."""

    COLLECTION_NAME = "matclaw_knowledge"
    VALID_TYPES = {"result", "lesson", "strategy", "failure", "debug_fix", "insight"}

    def __init__(self, memory_manager: Any) -> None:
        """
        Args:
            memory_manager: A :class:`MemoryManager` instance whose
                ``chroma_client`` and ``embed_fn`` properties will be reused.
        """
        self._mm = memory_manager
        self._collection = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_collection(self) -> None:
        if self._collection is not None:
            return
        client = self._mm.chroma_client
        embed_fn = self._mm.embed_fn
        self._collection = client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            embedding_function=embed_fn,
            metadata={"description": "MatClaw long-term knowledge (results, lessons, strategies)"},
        )
        logger.info("KnowledgeBase collection ready (%d entries).", self._collection.count())

    @staticmethod
    def _make_id(entry_type: str, skill_name: str, extra: str = "") -> str:
        """Generate a deterministic but unique-ish ID for a knowledge entry."""
        raw = f"{entry_type}:{skill_name}:{extra}:{time.time_ns()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    @staticmethod
    def _flatten_metadata(meta: dict[str, Any]) -> dict[str, Any]:
        """ChromaDB metadata values must be str | int | float | bool."""
        flat: dict[str, Any] = {}
        for k, v in meta.items():
            if isinstance(v, (str, int, float, bool)):
                flat[k] = v
            elif v is None:
                flat[k] = ""
            else:
                flat[k] = json.dumps(v)
        return flat

    def _add(self, entry_id: str, document: str, metadata: dict[str, Any]) -> None:
        self._ensure_collection()
        flat = self._flatten_metadata(metadata)
        try:
            self._collection.upsert(
                ids=[entry_id],
                documents=[document],
                metadatas=[flat],
            )
        except Exception:
            logger.exception("KnowledgeBase: failed to store entry %s", entry_id)

    # ------------------------------------------------------------------
    # Store methods
    # ------------------------------------------------------------------

    def store_result(
        self,
        skill_name: str,
        params: dict[str, Any],
        metrics: dict[str, Any],
        experiment_id: str = "",
        tags: list[str] | None = None,
    ) -> str:
        """Store an experiment result as a knowledge entry.  Returns the entry ID."""
        parts = [f"{k}={v}" for k, v in params.items()]
        metric_parts = [f"{k}={v}" for k, v in metrics.items()]
        doc = f"[{skill_name}] params: {', '.join(parts)}; metrics: {', '.join(metric_parts)}"
        entry_id = self._make_id("result", skill_name, experiment_id)
        meta = {
            "type": "result",
            "skill_name": skill_name,
            "experiment_id": experiment_id,
            "params": params,
            "metrics": metrics,
            "tags": tags or [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._add(entry_id, doc, meta)
        logger.debug("KnowledgeBase: stored result %s for %s", entry_id, skill_name)
        return entry_id

    def store_lesson(
        self,
        skill_name: str,
        lesson_text: str,
        cause: str = "",
        effect: str = "",
        confidence: float = 1.0,
    ) -> str:
        """Store a cause→effect lesson.  Returns the entry ID."""
        doc = lesson_text
        entry_id = self._make_id("lesson", skill_name, cause[:30])
        meta = {
            "type": "lesson",
            "skill_name": skill_name,
            "cause": cause,
            "effect": effect,
            "confidence": confidence,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._add(entry_id, doc, meta)
        logger.debug("KnowledgeBase: stored lesson %s for %s", entry_id, skill_name)
        return entry_id

    def store_strategy(
        self,
        skill_name: str,
        strategy_text: str,
        conditions: str = "",
        confidence: float = 1.0,
    ) -> str:
        """Store a higher-level strategy.  Returns the entry ID."""
        entry_id = self._make_id("strategy", skill_name)
        meta = {
            "type": "strategy",
            "skill_name": skill_name,
            "conditions": conditions,
            "confidence": confidence,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._add(entry_id, strategy_text, meta)
        return entry_id

    def store_failure(
        self,
        skill_name: str,
        description: str,
        params: dict[str, Any] | None = None,
        error: str = "",
    ) -> str:
        """Store a failure record.  Returns the entry ID."""
        doc = f"[FAILURE] {skill_name}: {description}"
        if error:
            doc += f" Error: {error}"
        entry_id = self._make_id("failure", skill_name, error[:30])
        meta = {
            "type": "failure",
            "skill_name": skill_name,
            "params": params or {},
            "error": error[:500],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._add(entry_id, doc, meta)
        return entry_id

    def store_debug_fix(
        self,
        skill_name: str,
        description: str,
        source_file: str = "",
        error_type: str = "",
        explanation: str = "",
    ) -> str:
        """Store a successful debug fix as knowledge.  Returns the entry ID."""
        doc = f"[DEBUG FIX] {description}"
        entry_id = self._make_id("debug_fix", skill_name, source_file)
        meta = {
            "type": "debug_fix",
            "skill_name": skill_name,
            "source_file": source_file,
            "error_type": error_type,
            "explanation": explanation,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._add(entry_id, doc, meta)
        return entry_id

    def store_insight(
        self,
        skill_name: str,
        insight_text: str,
        confidence: float = 1.0,
    ) -> str:
        """Store a cross-skill insight from consolidation.  Returns the entry ID."""
        entry_id = self._make_id("insight", skill_name)
        meta = {
            "type": "insight",
            "skill_name": skill_name,
            "confidence": confidence,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._add(entry_id, insight_text, meta)
        return entry_id

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def query(
        self,
        question: str,
        types: list[str] | None = None,
        skill_name: str | None = None,
        n_results: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Semantic search across knowledge entries, optionally filtered by
        entry type and/or skill name.

        Returns list of dicts with ``metadata``, ``document``, ``distance``.
        """
        self._ensure_collection()
        count = self._collection.count()
        if count == 0:
            return []
        n = min(n_results, count)

        # Build ChromaDB where-filter
        where_clauses: list[dict[str, Any]] = []
        if types:
            valid = [t for t in types if t in self.VALID_TYPES]
            if len(valid) == 1:
                where_clauses.append({"type": valid[0]})
            elif len(valid) > 1:
                where_clauses.append({"type": {"$in": valid}})
        if skill_name:
            where_clauses.append({"skill_name": skill_name})

        where: dict[str, Any] | None = None
        if len(where_clauses) == 1:
            where = where_clauses[0]
        elif len(where_clauses) > 1:
            where = {"$and": where_clauses}

        embed_fn = self._mm.embed_fn
        if embed_fn is not None:
            kwargs: dict[str, Any] = {
                "query_texts": [question],
                "n_results": n,
                "include": ["metadatas", "documents", "distances"],
            }
            if where:
                kwargs["where"] = where
            results = self._collection.query(**kwargs)
        else:
            # Fallback: return recent entries (no semantic search)
            get_kwargs: dict[str, Any] = {
                "include": ["metadatas", "documents"],
                "limit": n,
            }
            if where:
                get_kwargs["where"] = where
            results = self._collection.get(**get_kwargs)
            metas = results.get("metadatas") or []
            docs = results.get("documents") or []
            return [
                {"metadata": metas[i], "document": docs[i] if i < len(docs) else "", "distance": 0.0}
                for i in range(len(metas))
            ]

        out: list[dict[str, Any]] = []
        metas = results.get("metadatas") or [[]]
        docs = results.get("documents") or [[]]
        dists = results.get("distances") or [[]]
        if metas and isinstance(metas[0], list):
            metas, docs, dists = metas[0], docs[0] if docs else [], dists[0] if dists else []
        for i, meta in enumerate(metas):
            out.append({
                "metadata": meta or {},
                "document": docs[i] if i < len(docs) else "",
                "distance": dists[i] if i < len(dists) else 0.0,
            })
        return out

    def count(self, entry_type: str | None = None) -> int:
        """Return the number of knowledge entries, optionally filtered by type."""
        self._ensure_collection()
        if entry_type is None:
            return self._collection.count()
        try:
            results = self._collection.get(where={"type": entry_type}, include=[])
            return len(results.get("ids") or [])
        except Exception:
            return 0
