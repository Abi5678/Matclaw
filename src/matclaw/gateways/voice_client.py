"""
Voice client contract for hybrid (voice + clipboard) gateways.

Implementations provide the latest speech-to-text transcript for intent assembly.
"""

from __future__ import annotations

import threading
from typing import Protocol, runtime_checkable


@runtime_checkable
class VoiceClient(Protocol):
    """Protocol for objects that expose the latest voice transcript."""

    def get_latest_transcript(self) -> str | None:
        """Return the most recent transcript, or ``None`` if none is available."""
        ...


class BufferedVoiceClient:
    """
    Thread-safe transcript buffer for wiring ASR / VoiceChat pipelines.

    Your voice stack should call :meth:`set_transcript` when new text arrives.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._transcript: str | None = None

    def set_transcript(self, text: str | None) -> None:
        """Store the latest transcript (``None`` clears)."""
        with self._lock:
            self._transcript = text.strip() if text else None

    def get_latest_transcript(self) -> str | None:
        """Return the latest transcript, or ``None``."""
        with self._lock:
            return self._transcript


__all__ = ["VoiceClient", "BufferedVoiceClient"]
