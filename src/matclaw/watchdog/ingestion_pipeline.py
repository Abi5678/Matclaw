"""
Ingestion pipeline: when a new .mat or .csv appears, parse it and register in memory.
Background heartbeat: uses Nemotron to summarize new files when discovered (even when UI is closed).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from src.matclaw.memory.memory import MemoryStore
from src.matclaw.watchdog.parsers import load_csv, load_mat

logger = logging.getLogger(__name__)

SESSION_ID_FILES = "matclaw:watchdog:files"


def _nemotron_summarize(path: Path, meta: dict) -> str:
    """Use Nemotron to summarize the file for lab journal. Runs in background."""
    if not os.environ.get("NVIDIA_API_KEY"):
        return ""
    try:
        from src.matclaw.llm.nemotron_client import NemotronClient
        client = NemotronClient()
        return client.summarize_file(str(path), meta)
    except Exception as exc:
        logger.debug("Nemotron summarize_file skipped: %s", exc)
        return ""


def ingest_file(path: str | Path, memory_store: MemoryStore) -> bool:
    """
    Load the file (if .mat or .csv), build a short summary, and append to session memory.
    Background heartbeat: when NVIDIA_API_KEY is set, uses Nemotron to summarize new data.
    Returns True if ingestion succeeded.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix == ".mat":
            meta = load_mat(path)
            note = f"📁 Ingested .mat: {path.name} keys={meta.get('keys', [])}"
        elif suffix == ".csv":
            meta = load_csv(path)
            note = f"📁 Ingested .csv: {path.name} rows={meta.get('rows', 0)} cols={meta.get('column_count', 0)}"
        else:
            logger.debug("Skipping non-ingested file type: %s", path)
            return False
    except Exception:
        logger.exception("Failed to parse file for ingestion: %s", path)
        return False

    # Background heartbeat: Nemotron summarizes new files
    summary = _nemotron_summarize(path, meta)
    if summary:
        note = f"📁 {summary}"

    try:
        rec = memory_store.get_or_create_session(SESSION_ID_FILES)
        rec.metadata[str(path)] = meta
        memory_store.append_note(SESSION_ID_FILES, note)
        return True
    except Exception:
        logger.exception("Failed to register file in memory: %s", path)
        return False
