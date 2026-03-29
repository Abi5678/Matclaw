"""
Messaging gateways: Telegram, global hotkeys, and future transports.
"""

from .telegram_handler import TelegramHandler
from .voice_client import BufferedVoiceClient, VoiceClient

__all__ = [
    "BufferedVoiceClient",
    "TelegramHandler",
    "VoiceClient",
]
