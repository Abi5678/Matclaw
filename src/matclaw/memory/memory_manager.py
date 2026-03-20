"""
Long-term artifact and context memory using ChromaDB (and optional SQLite for indexing).

Used by the RPI loop: research phase calls query_context(); after a skill succeeds,
store_artifact() saves final parameters for future "lessons learned".
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    from chromadb.utils import embedding_functions
except ImportError:
    chromadb = None  # type: ignore[assignment]
    ChromaSettings = None  # type: ignore[assignment]
    embedding_functions = None  # type: ignore[assignment]


class ArtifactRecord(BaseModel):
    """Stored artifact: key, metadata, and optional text used for retrieval."""
    key: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


class MemoryManager:
    """
    Manages artifacts and context using a local ChromaDB instance.
    store_artifact(key, metadata, vector) and query_context(query_string) for RPI.
    """

    COLLECTION_NAME = "matclaw_artifacts"
    DEFAULT_PERSIST_DIR = ".matclaw_chromadb"

    def __init__(self, persist_directory: str | Path | None = None, embedding_model: str | None = None) -> None:
        self._persist_dir = Path(persist_directory or self.DEFAULT_PERSIST_DIR).expanduser().resolve()
        self._client = None
        self._collection = None
        self._embed_fn = None
        self._embedding_model = embedding_model or "all-MiniLM-L6-v2"

    def _ensure_client(self) -> None:
        if chromadb is None:
            raise RuntimeError("chromadb is not installed. pip install chromadb")
        if self._client is not None:
            return
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(self._persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        if embedding_functions:
            try:
                self._embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=self._embedding_model)
            except Exception:
                try:
                    self._embed_fn = embedding_functions.DefaultEmbeddingFunction()
                except Exception:
                    self._embed_fn = None
                    logger.warning("ChromaDB embedding not available; using hash-based fallback.")
        else:
            self._embed_fn = None
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            embedding_function=self._embed_fn,
            metadata={"description": "MatClaw skill artifacts and lessons learned"},
        )
        logger.info("MemoryManager ChromaDB ready at %s", self._persist_dir)

    # ------------------------------------------------------------------
    # Public properties so KnowledgeBase can share the ChromaDB client
    # ------------------------------------------------------------------

    @property
    def chroma_client(self):
        """Return the underlying ChromaDB PersistentClient (initialised lazily)."""
        self._ensure_client()
        return self._client

    @property
    def embed_fn(self):
        """Return the embedding function used by this manager (may be ``None``)."""
        self._ensure_client()
        return self._embed_fn

    def store_artifact(self, key: str, metadata: dict[str, Any], vector: list[float] | None = None) -> None:
        """
        Store an artifact by key with metadata and optional vector.
        If vector is None and an embedding function is set, we embed a summary from metadata.
        """
        self._ensure_client()
        summary = metadata.get("summary") or metadata.get("message") or json.dumps(metadata)[:2000]
        doc = summary
        # ChromaDB expects list of lists for embeddings when adding
        if vector is not None:
            embeddings = [vector]
        elif self._embed_fn is not None:
            embeddings = None  # let collection embed the document
        else:
            # Fallback: deterministic pseudo-embedding from key+summary (low quality but no extra deps)
            h = hashlib.sha256((key + summary).encode()).hexdigest()
            embeddings = [[float(int(h[i : i + 2], 16) % 256) / 256.0 for i in range(0, 32, 2)] * 8][:384]
        ids = [key]
        metadatas = [{**metadata, "key": key}]
        # ChromaDB metadata values must be str, int, float, bool
        flat = {}
        for k, v in metadatas[0].items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                flat[k] = v if v is not None else ""
            else:
                flat[k] = json.dumps(v) if not isinstance(v, str) else v
        metadatas = [flat]
        documents = [doc]
        if embeddings is not None:
            self._collection.add(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)
        else:
            self._collection.add(ids=ids, metadatas=metadatas, documents=documents)
        logger.debug("Stored artifact: %s", key)

    def query_context(self, query_string: str, n_results: int = 5) -> list[dict[str, Any]]:
        """
        Query for relevant historical artifacts (e.g. "same noise-filtering constants as January project").
        Returns list of dicts with metadata and distance.
        """
        self._ensure_client()
        if self._embed_fn is not None:
            count = self._collection.count()
            n = min(n_results, count) if count else 0
            if n == 0:
                return []
            results = self._collection.query(
                query_texts=[query_string],
                n_results=n,
                include=["metadatas", "documents", "distances"],
            )
        else:
            # No embedding fn: return recent by id (no semantic search)
            results = self._collection.get(include=["metadatas", "documents"])
            metadatas = results.get("metadatas") or []
            documents = results.get("documents") or []
            results = {
                "metadatas": [metadatas[-i] for i in range(1, min(n_results, len(metadatas)) + 1)],
                "documents": [documents[-i] for i in range(1, min(n_results, len(documents)) + 1)],
                "distances": [0.0] * min(n_results, len(metadatas)),
            }
        out = []
        metas = results.get("metadatas") or [[]]
        docs = results.get("documents") or [[]]
        dists = results.get("distances") or [[]]
        # ChromaDB returns list of lists for query: metas[0], docs[0], dists[0]
        if metas and isinstance(metas[0], list):
            metas, docs, dists = metas[0], docs[0] if docs else [], dists[0] if dists else []
        for i, meta in enumerate(metas):
            doc = docs[i] if i < len(docs) else ""
            dist = dists[i] if i < len(dists) else 0
            out.append({"metadata": meta or {}, "document": doc, "distance": dist})
        return out

    def query_experiment(self, experiment_id: str, n_results: int = 20) -> list[dict[str, Any]]:
        """
        Return artifacts linked to a specific experiment_id.
        """
        self._ensure_client()
        count = self._collection.count()
        n = min(max(1, n_results), count) if count else 0
        if n == 0:
            return []
        results = self._collection.get(
            where={"experiment_id": experiment_id},
            limit=n,
            include=["metadatas", "documents"],
        )
        metas = results.get("metadatas") or []
        docs = results.get("documents") or []
        out: list[dict[str, Any]] = []
        for i, meta in enumerate(metas):
            out.append({"metadata": meta or {}, "document": docs[i] if i < len(docs) else ""})
        return out

    def query_experiment_metric(self, metric_key: str, n_results: int = 20) -> list[dict[str, Any]]:
        """
        Lightweight helper to search memory for artifacts containing a metric key.
        Uses semantic query fallback so this works regardless of Chroma metadata schema.
        """
        return self.query_context(f"experiment metric {metric_key}", n_results=n_results)

    def close(self) -> None:
        self._client = None
        self._collection = None
