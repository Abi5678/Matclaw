"""
Proactive Sentry: monitor /data_in and trigger RPI on new files.

When a new file is detected, initiates the RPIExecutor:
- .mat -> run workspace_auditor and print summary to terminal.
- Filename contains 'PID' -> suggest running pid_optimizer.
Appends each run to LAB_JOURNAL.md (Million-Dollar feature).
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.matclaw.config.base_config import SentrySettings, LabJournalSettings
from src.matclaw.core.rpi_executor import RPIExecutor
from src.matclaw.lab_journal import append_lab_journal

logger = logging.getLogger(__name__)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    Observer = None  # type: ignore[assignment]
    FileSystemEventHandler = object  # type: ignore[assignment]


class SentryFileHandler(FileSystemEventHandler):
    """Handle new files in data_in: run RPI and optionally append to lab journal."""

    def __init__(
        self,
        executor: RPIExecutor,
        journal_path: Path | None = None,
        patterns: list[str] | None = None,
    ) -> None:
        self.executor = executor
        self.journal_path = journal_path
        self.patterns = patterns or ["*.mat", "*.csv", "*.slx"]
        self.suffixes = [p.lstrip("*").lower() if p.startswith("*") else p.lower() for p in self.patterns]

    def _should_handle(self, path: Path) -> bool:
        return path.suffix.lower() in self.suffixes or any(s in path.name.upper() for s in ("PID",))

    def on_created(self, event: object) -> None:
        if getattr(event, "is_directory", True):
            return
        path = Path(getattr(event, "src_path", ""))
        if not path.is_file():
            return
        if not self._should_handle(path):
            return
        logger.info("Sentry: new file detected, initiating RPI — %s", path)
        try:
            summary, artifact_paths = self.executor.run_on_file(path)
            # Background heartbeat: Nemotron summarizes new data files (even when UI closed)
            try:
                import os
                if os.environ.get("NVIDIA_API_KEY"):
                    from src.matclaw.llm.nemotron_client import NemotronClient
                    from src.matclaw.watchdog.parsers import load_mat, load_csv
                    meta = {}
                    if path.suffix.lower() == ".mat":
                        meta = load_mat(path)
                    elif path.suffix.lower() == ".csv":
                        meta = load_csv(path)
                    if meta:
                        client = NemotronClient()
                        ai_summary = client.summarize_file(str(path), meta)
                        if ai_summary:
                            summary = f"{summary} | Nemotron: {ai_summary}"
            except Exception:
                pass
            if self.journal_path:
                append_lab_journal(
                    self.journal_path,
                    summary,
                    source=f"Sentry:{path.name}",
                    artifact_paths=artifact_paths,
                )
        except Exception:
            logger.exception("Sentry RPI run failed for %s", path)

    def on_modified(self, event: object) -> None:
        """On .slx modification: suggest validation simulation."""
        if getattr(event, "is_directory", True):
            return
        path = Path(getattr(event, "src_path", ""))
        if not path.is_file() or path.suffix.lower() != ".slx":
            return
        logger.info("Sentry: .slx model modified — %s", path)
        try:
            summary, artifact_paths = self.executor.run_on_file(path)
            if self.journal_path:
                append_lab_journal(
                    self.journal_path,
                    summary,
                    source=f"Sentry:modified:{path.name}",
                    artifact_paths=artifact_paths,
                )
        except Exception:
            logger.exception("Sentry RPI run failed for modified %s", path)


class SentryWatchdog:
    """Watch /data_in and trigger RPIExecutor on new files; append runs to LAB_JOURNAL.md."""

    def __init__(
        self,
        settings: SentrySettings,
        lab_settings: LabJournalSettings,
        executor: RPIExecutor,
    ) -> None:
        self.settings = settings
        self.lab_settings = lab_settings
        self.executor = executor
        self._observer: object = None

    def start(self) -> None:
        if not self.settings.enabled:
            logger.info("Sentry watchdog disabled.")
            return
        if Observer is None:
            logger.warning("watchdog library not installed; sentry disabled.")
            return
        data_in = Path(self.settings.data_in_path).resolve()
        if not data_in.is_dir():
            data_in.mkdir(parents=True, exist_ok=True)
            logger.info("Created sentry watch directory: %s", data_in)
        journal_path = Path(self.lab_settings.path).resolve() if self.lab_settings.enabled else None
        if journal_path and self.lab_settings.enabled and not journal_path.is_file():
            try:
                journal_path.parent.mkdir(parents=True, exist_ok=True)
                journal_path.write_text(
                    "# MatClaw Lab Journal\n\nAuto-generated log of sentry-triggered runs and artifacts.\n\n",
                    encoding="utf-8",
                )
            except Exception:
                logger.exception("Failed to create lab journal file: %s", journal_path)
        handler = SentryFileHandler(
            self.executor,
            journal_path=journal_path,
            patterns=["*.mat", "*.csv", "*.slx"],
        )
        self._observer = Observer()
        self._observer.schedule(handler, str(data_in), recursive=True)  # type: ignore[attr-defined]
        self._observer.start()  # type: ignore[attr-defined]
        logger.info("Sentry watchdog started for %s (lab journal: %s)", data_in, journal_path)

    def stop(self) -> None:
        if self._observer is None:
            return
        try:
            self._observer.stop()  # type: ignore[attr-defined]
            self._observer.join(timeout=5)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("Error stopping sentry observer.")
        self._observer = None
        logger.info("Sentry watchdog stopped.")
