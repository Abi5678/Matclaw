"""Accessibility helpers (hotkeys, intent expansion)."""

from .intent_expansion import IntentExpansionResult, expand_prompt_to_goal
from .keystroke_manager import HotkeyBindings, StatusHotkeyManager

__all__ = [
    "HotkeyBindings",
    "IntentExpansionResult",
    "StatusHotkeyManager",
    "expand_prompt_to_goal",
]

