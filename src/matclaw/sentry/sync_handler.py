"""
Digital Twin sync: pull files from laptop (rsync) or watch a cloud folder (Dropbox/OneDrive).

Synced files land in data_in (or configured dest) and are processed by the standard Sentry/Watchdog pipeline.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from matclaw.config.base_config import SyncSettings

logger = logging.getLogger(__name__)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    Observer = None
    FileSystemEventHandler = object  # type: ignore


class SyncHandler:
    """
    Rsync mode: pull from remote source into rsync_dest (e.g. data_in).
    Cloud mode: watch cloud_path for new files (same as Sentry; optionally run both).
    """

    def __init__(
        self,
        settings: SyncSettings,
        on_file_synced: Callable[[Path], None] | None = None,
    ) -> None:
        self.settings = settings
        self.on_file_synced = on_file_synced  # Called for each new file so caller can run Sentry pipeline
        self._observer = None

    def run_rsync(self) -> tuple[int, list[Path]]:
        """
        Run rsync pull from settings.rsync_source (or RSYNC_SOURCE env) to rsync_dest.
        Returns (exit_code, list of paths that were updated/created).
        """
        source = self.settings.rsync_source or os.environ.get("RSYNC_SOURCE", "")
        dest = Path(self.settings.rsync_dest or "data_in").resolve()
        if not source:
            logger.warning("Rsync source not set (RSYNC_SOURCE or sync.rsync_source).")
            return 1, []
        dest.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                ["rsync", "-avz", "--out-format=%n", source, str(dest) + "/"],
                capture_output=True,
                text=True,
                timeout=300,
            )
            paths = []
            for line in (result.stdout or "").strip().splitlines():
                line = line.strip()
                if line and not line.endswith("/"):
                    p = dest / line
                    if p.exists():
                        paths.append(p)
            if self.on_file_synced:
                for p in paths:
                    self.on_file_synced(p)
            return result.returncode, paths
        except subprocess.TimeoutExpired:
            logger.error("Rsync timed out.")
            return -1, []
        except FileNotFoundError:
            logger.error("rsync not found; install rsync.")
            return -1, []
        except Exception as exc:
            logger.exception("Rsync failed: %s", exc)
            return -1, []

    def start_cloud_watch(self) -> None:
        """Start watching cloud_path (Dropbox/OneDrive folder) for new files."""
        if Observer is None or not self.settings.enabled or self.settings.mode != "cloud":
            return
        cloud_path = self.settings.cloud_path or os.environ.get("SYNC_CLOUD_PATH", "")
        if not cloud_path:
            logger.info("Cloud path not set; skipping cloud watch.")
            return
        path = Path(cloud_path).resolve()
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
        dest_dir = Path(self.settings.rsync_dest or "data_in").resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)

        class Handler(FileSystemEventHandler):
            def __init__(self, dest: Path, callback: Callable[[Path], None] | None) -> None:
                self.dest_dir = dest
                self.callback = callback

            def on_created(self, event: object) -> None:
                if getattr(event, "is_directory", True):
                    return
                p = Path(getattr(event, "src_path", ""))
                if not p.is_file():
                    return
                try:
                    target = self.dest_dir / p.name
                    shutil.copy2(p, target)
                    if self.callback:
                        self.callback(target)
                except Exception as e:
                    logger.warning("Copy from cloud to %s failed: %s", self.dest_dir, e)

        handler = Handler(dest_dir, self.on_file_synced)
        self._observer = Observer()
        self._observer.schedule(handler, str(path), recursive=True)  # type: ignore
        self._observer.start()  # type: ignore
        logger.info("Sync cloud watch started: %s", path)

    def stop_cloud_watch(self) -> None:
        if self._observer is None:
            return
        try:
            self._observer.stop()  # type: ignore
            self._observer.join(timeout=5)  # type: ignore
        except Exception:
            pass
        self._observer = None
