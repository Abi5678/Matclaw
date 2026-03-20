"""
Messaging gateways: Telegram, global hotkeys, and future transports.
"""

from .keystroke_manager import HybridTriggerPayload, KeystrokeManager, assemble_hybrid_payload
from .telegram_handler import TelegramHandler
from .voice_client import BufferedVoiceClient, VoiceClient

__all__ = [
    "assemble_hybrid_payload",
    "BufferedVoiceClient",
    "HybridTriggerPayload",
    "KeystrokeManager",
    "TelegramHandler",
    "VoiceClient",
]
