from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict

from pydantic import ValidationError

from matclaw.config.base_config import MemorySettings
from .schemas import SessionRecord

logger = logging.getLogger(__name__)


class MemoryStore:
    """
    Simple pluggable memory backend focused on session persistence.

    For v1 this uses a local JSON file as persistence. It can later be
    replaced by or backed with a vector database while keeping this
    interface stable for the rest of the system.
    """

    def __init__(self, settings: MemorySettings) -> None:
        self.settings = settings
        self._sessions: Dict[str, SessionRecord] = {}
        self._path = Path(self.settings.persistence_path).expanduser()

    # Lifecycle -----------------------------------------------------------------

    def initialize(self) -> None:
        logger.info("Initializing memory store.", extra={"backend": self.settings.backend})
        if self._path.is_file():
            try:
                raw = json.loads(self._path.read_text())
                for sid, payload in raw.items():
                    try:
                        self._sessions[sid] = SessionRecord.model_validate(payload)
                    except ValidationError as exc:
                        logger.error(
                            "Failed to validate session record from disk.",
                            extra={"session_id": sid, "error": str(exc)},
                        )
                logger.info("Loaded %d session records from disk.", len(self._sessions))
            except Exception:
                logger.exception("Failed to load memory store from %s", self._path)

    def close(self) -> None:
        try:
            self._flush_to_disk()
        except Exception:
            logger.exception("Failed to flush memory store to disk.")

    def is_healthy(self) -> bool:
        # For now, if we can write to disk path, consider it healthy.
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            return True
        except Exception:
            logger.exception("Memory store health check failed.")
            return False

    # Session API ---------------------------------------------------------------

    def get_or_create_session(self, session_id: str) -> SessionRecord:
        if session_id not in self._sessions:
            logger.debug("Creating new session record.", extra={"session_id": session_id})
            self._sessions[session_id] = SessionRecord(session_id=session_id)
            self._flush_to_disk()
        return self._sessions[session_id]

    def append_note(self, session_id: str, note: str) -> SessionRecord:
        rec = self.get_or_create_session(session_id)
        rec.notes.append(note)
        rec.touch()
        self._flush_to_disk()
        return rec

    # Internal ------------------------------------------------------------------

    def _flush_to_disk(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {sid: rec.model_dump(mode="json") for sid, rec in self._sessions.items()}
            self._path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        except Exception:
            logger.exception("Error while flushing memory store to disk.")

