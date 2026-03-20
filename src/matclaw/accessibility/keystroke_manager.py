"""
Accessibility & Keystroke Manager (global hotkeys).

Mandate:
  - use pynput to register global hotkeys
  - Ctrl+Shift+S -> MatClaw Status
  - provide intent expansion for vague prompts by combining workspace + memory
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

from src.matclaw.accessibility.intent_expansion import IntentExpansionResult, expand_prompt_to_goal
from src.matclaw.matlab.matlab_bridge import MatlabBridge
from src.matclaw.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

try:
    from pynput import keyboard  # type: ignore[import-untyped]
except Exception:  # pragma: no cover
    keyboard = None  # type: ignore[assignment]


@dataclass(frozen=True)
class HotkeyBindings:
    """Keystroke bindings for common MatClaw actions."""

    status: str = "ctrl+shift+s"


class StatusHotkeyManager:
    """
    Global hotkey manager for MatClaw status / intent expansion (Ctrl+Shift+S).

    For hybrid voice+clipboard RPI triggers use :class:`src.matclaw.gateways.keystroke_manager.KeystrokeManager`.

    This manager is designed to be embedded in a local Python process running MatClaw UI/control.
    It does not assume Telegram availability.
    """

    def __init__(
        self,
        *,
        bindings: HotkeyBindings | None = None,
        matlab_bridge: MatlabBridge,
        memory_manager: MemoryManager,
        on_status: Callable[[], str] | None = None,
        prompt_provider: Callable[[], str | None] | None = None,
        on_inferred_prompt: Callable[[str, IntentExpansionResult], None] | None = None,
    ) -> None:
        if keyboard is None:
            raise RuntimeError("pynput not installed; cannot use StatusHotkeyManager.")

        self.bindings = bindings or HotkeyBindings()
        self.matlab_bridge = matlab_bridge
        self.memory_manager = memory_manager

        self.on_status = on_status
        self.prompt_provider = prompt_provider
        self.on_inferred_prompt = on_inferred_prompt

        self._listener: keyboard.Listener | None = None  # type: ignore[type-arg]
        self._keys_down: set[str] = set()

    def _normalize_key(self, key: object) -> str:
        # pynput uses Key.shift, Key.ctrl_l, etc.
        s = str(key)
        return s.lower()

    def _match_ctrl_shift_s(self) -> bool:
        # Detect ctrl + shift + 's' while pressing.
        return {"ctrl", "shift", "s"}.issubset(self._keys_down)

    def _on_press(self, key: object) -> None:
        normalized = self._normalize_key(key)
        # Update set of pressed keys.
        if normalized.endswith("shift") or "shift" in normalized:
            self._keys_down.add("shift")
        elif "ctrl" in normalized:
            self._keys_down.add("ctrl")
        elif normalized.endswith("'s'") or normalized.endswith("s"):
            self._keys_down.add("s")

        try:
            if self._match_ctrl_shift_s():
                logger.info("Keystroke: Ctrl+Shift+S (MatClaw Status)")
                if self.on_status is not None:
                    msg = self.on_status()
                    # Default behavior: log.
                    logger.info("MatClaw Status: %s", msg)
        except Exception as exc:
            logger.exception("Keystroke handler failed: %s", exc)

        # Optional: run intent expansion when prompt_provider exists and on_inferred_prompt exists.
        if self.prompt_provider is not None and self.on_inferred_prompt is not None:
            # Re-use status hotkey as a trigger for expansion when prompt is available.
            # (If you want separate hotkeys, wire a dedicated binding.)
            if self._match_ctrl_shift_s():
                prompt = None
                try:
                    prompt = self.prompt_provider()
                except Exception:
                    prompt = None
                if prompt:
                    expanded = expand_prompt_to_goal(
                        prompt,
                        matlab_bridge=self.matlab_bridge,
                        memory_manager=self.memory_manager,
                    )
                    self.on_inferred_prompt(prompt, expanded)

    def _on_release(self, key: object) -> None:
        normalized = self._normalize_key(key)
        if "shift" in normalized:
            self._keys_down.discard("shift")
        elif "ctrl" in normalized:
            self._keys_down.discard("ctrl")
        elif normalized.endswith("'s'") or normalized.endswith("s"):
            self._keys_down.discard("s")

    def start(self) -> None:
        """Start listening for global hotkeys in a background thread."""

        if self._listener is not None:
            return

        self._listener = keyboard.Listener(  # type: ignore[call-arg]
            on_press=self._on_press,
            on_release=self._on_release,
        )
        thread = threading.Thread(target=self._listener.start, daemon=True)
        thread.start()

    def stop(self) -> None:
        """Stop the keystroke listener."""

        if self._listener is None:
            return
        try:
            self._listener.stop()
        except Exception:
            pass
        self._listener = None

