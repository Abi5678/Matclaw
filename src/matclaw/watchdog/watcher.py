"""
Filesystem watcher for .mat and .csv files (Level 2 file management).
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.matclaw.config.base_config import WatchdogSettings
from src.matclaw.memory.memory import MemoryStore
from src.matclaw.watchdog.ingestion_pipeline import ingest_file

logger = logging.getLogger(__name__)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent
except ImportError:
    Observer = None  # type: ignore
    FileSystemEventHandler = object  # type: ignore
    FileCreatedEvent = object  # type: ignore
    FileModifiedEvent = object  # type: ignore


class MatCsvHandler(FileSystemEventHandler):
    """Handle new or modified .mat / .csv under the watched directory."""

    def __init__(self, memory_store: MemoryStore, patterns: list[str]) -> None:
        self.memory_store = memory_store
        # Normalize: "*.mat" -> ".mat", "*.csv" -> ".csv"
        self.suffixes = []
        for p in patterns:
            if p.startswith("*"):
                self.suffixes.append(p[1:].lower())
            else:
                self.suffixes.append(p.lower() if p.startswith(".") else f".{p}".lower())

    def _should_ingest(self, path: Path) -> bool:
        return path.suffix.lower() in self.suffixes

    def on_created(self, event: object) -> None:
        if getattr(event, "is_directory", True):
            return
        path = Path(getattr(event, "src_path", ""))
        if self._should_ingest(path):
            logger.info("New file detected: %s", path)
            ingest_file(path, self.memory_store)

    def on_modified(self, event: object) -> None:
        if getattr(event, "is_directory", True):
            return
        path = Path(getattr(event, "src_path", ""))
        if self._should_ingest(path):
            logger.info("Modified file detected: %s", path)
            ingest_file(path, self.memory_store)


class WatchdogService:
    """Start/stop the file watcher and route events to ingestion."""

    def __init__(self, settings: WatchdogSettings, memory_store: MemoryStore) -> None:
        self.settings = settings
        self.memory_store = memory_store
        self._observer: object = None

    def start(self) -> None:
        if not self.settings.enabled:
            logger.info("Watchdog disabled via configuration.")
            return
        if Observer is None:
            logger.warning("watchdog library not installed; file watcher disabled.")
            return

        watch_path = Path(self.settings.watch_path).resolve()
        if not watch_path.is_dir():
            watch_path.mkdir(parents=True, exist_ok=True)
            logger.info("Created watch directory: %s", watch_path)

        handler = MatCsvHandler(self.memory_store, self.settings.patterns)
        self._observer = Observer()
        self._observer.schedule(handler, str(watch_path), recursive=True)  # type: ignore[attr-defined]
        self._observer.start()  # type: ignore[attr-defined]
        logger.info("Watchdog started for %s (patterns: %s)", watch_path, self.settings.patterns)

    def stop(self) -> None:
        if self._observer is None:
            return
        try:
            self._observer.stop()  # type: ignore[attr-defined]
            self._observer.join(timeout=5)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("Error stopping watchdog observer.")
        self._observer = None
        logger.info("Watchdog stopped.")
