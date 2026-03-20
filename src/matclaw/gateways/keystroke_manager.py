"""
Global hotkey gateway: hybrid context (voice intent + clipboard) → ``RPIExecutor.run_flow``.

Uses ``pynput`` for ``Ctrl+Alt+M`` and ``pyperclip`` for clipboard access.
Runs the hotkey listener in a background thread so Streamlit / MATLAB are not blocked.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from pydantic import BaseModel, Field

from src.matclaw.config.base_config import MatClawSettings
from src.matclaw.core.rpi_executor import RPIExecutor
from src.matclaw.gateways.voice_client import VoiceClient

logger = logging.getLogger(__name__)

try:
    import pyperclip
except ImportError:  # pragma: no cover
    pyperclip = None  # type: ignore[assignment]

try:
    from pynput import keyboard
except ImportError:  # pragma: no cover
    keyboard = None  # type: ignore[assignment]

_DEFAULT_INTENT = "General Workspace Audit"
_HOTKEY_SPEC = "<ctrl>+<alt>+m"


class HybridTriggerPayload(BaseModel):
    """Structured hybrid context passed into the RPI flow."""

    voice_intent: str = Field(description="Voice or default intent string.")
    clipboard_text: str = Field(description="Clipboard snippet or placeholder.")
    hybrid_prompt: str = Field(description="Full prompt for run_flow.")


def assemble_hybrid_payload(
    voice_transcript: str | None,
    code_or_clipboard: str | None,
) -> HybridTriggerPayload:
    """
    Build the same hybrid prompt shape as :class:`KeystrokeManager` / global hotkey.

    Empty voice → *General Workspace Audit*. Empty code → ``(empty clipboard)``.
    Used by Streamlit and any manual gateway.
    """
    voice = (voice_transcript or "").strip() or _DEFAULT_INTENT
    clip_raw = code_or_clipboard if code_or_clipboard is not None else ""
    clip = (str(clip_raw)).strip()
    if not clip:
        clip = "(empty clipboard)"
    hybrid = f"Context: {voice}\nCode Snippet: {clip}"
    return HybridTriggerPayload(voice_intent=voice, clipboard_text=clip, hybrid_prompt=hybrid)


class KeystrokeManager:
    """
    Gateway-style global hotkey manager (similar lifecycle to ``TelegramHandler`` listeners).

    Args:
        executor: Shared ``RPIExecutor`` instance.
        voice_client: Source of latest voice transcript (``VoiceClient`` protocol).
        settings: Optional ``MatClawSettings`` for NL routing inside ``run_flow``.
    """

    def __init__(
        self,
        executor: RPIExecutor,
        voice_client: VoiceClient,
        *,
        settings: MatClawSettings | None = None,
    ) -> None:
        if keyboard is None:
            raise RuntimeError("pynput is not installed; install package 'pynput'.")
        if pyperclip is None:
            raise RuntimeError("pyperclip is not installed; install package 'pyperclip'.")

        self._executor = executor
        self._voice_client = voice_client
        self._settings = settings

        self._hotkeys: Any = None
        self._started = False

    def build_hybrid_prompt(self) -> HybridTriggerPayload:
        """
        Assemble voice intent + clipboard into the hybrid prompt.

        If the transcript is missing or empty, uses *General Workspace Audit*.
        If the clipboard is empty, uses ``(empty clipboard)``.
        """
        raw_voice = self._voice_client.get_latest_transcript()
        try:
            clip_raw = pyperclip.paste()
        except Exception as exc:
            logger.warning("KeystrokeManager: clipboard read failed: %s", exc)
            clip_raw = ""
        return assemble_hybrid_payload(raw_voice, str(clip_raw) if clip_raw is not None else "")

    def trigger_rpi(self) -> None:
        """
        Hotkey callback path: log hybrid context and invoke ``run_flow`` on a worker thread.
        """
        payload = self.build_hybrid_prompt()
        logger.info(
            "[KeystrokeManager] Hybrid RPI trigger (%s)\n%s",
            _HOTKEY_SPEC,
            payload.hybrid_prompt[:800] + ("…" if len(payload.hybrid_prompt) > 800 else ""),
        )

        def _worker() -> None:
            try:
                self._executor.run_flow(payload.hybrid_prompt, settings=self._settings)
            except Exception:
                logger.exception("KeystrokeManager: run_flow failed")

        threading.Thread(target=_worker, name="matclaw-hybrid-rpi", daemon=True).start()

    def start(self) -> None:
        """Start the global hotkey listener (non-blocking; uses ``pynput`` background thread)."""
        if self._started:
            return

        self._hotkeys = keyboard.GlobalHotKeys({_HOTKEY_SPEC: self.trigger_rpi})
        self._hotkeys.start()
        self._started = True
        logger.info("KeystrokeManager started (hotkey %s).", _HOTKEY_SPEC)

    def stop(self) -> None:
        """Stop the global hotkey listener."""
        if not self._started or self._hotkeys is None:
            return
        try:
            self._hotkeys.stop()
        except Exception:
            logger.exception("KeystrokeManager: failed to stop hotkey listener")
        finally:
            self._hotkeys = None
            self._started = False
            logger.info("KeystrokeManager stopped.")


__all__ = [
    "KeystrokeManager",
    "HybridTriggerPayload",
    "assemble_hybrid_payload",
    "_DEFAULT_INTENT",
]
