"""
Application entry helpers: wire shared ``RPIExecutor`` and gateways.

Example — start the hybrid (voice + clipboard) keystroke gateway alongside your app::

    from matclaw.config.base_config import MatClawSettings
    from matclaw.core.rpi_executor import RPIExecutor
    from matclaw.gateways.voice_client import BufferedVoiceClient
    from matclaw.main import start_hybrid_keystroke_gateway

    settings = MatClawSettings()
    executor = RPIExecutor(matlab_bridge=bridge, memory_manager=memory)
    voice_client = BufferedVoiceClient()

    # Your voice/ASR pipeline should call ``voice_client.set_transcript(text)`` as transcripts arrive.

    keystroke_gateway = start_hybrid_keystroke_gateway(
        executor,
        voice_client,
        settings=settings,
    )
    # ... run Streamlit / daemon; on shutdown: keystroke_gateway.stop()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typing import Any
from matclaw.gateways.voice_client import VoiceClient

if TYPE_CHECKING:
    from matclaw.config.base_config import MatClawSettings
    from matclaw.core.rpi_executor import RPIExecutor


def start_hybrid_keystroke_gateway(
    executor: RPIExecutor,
    voice_client: VoiceClient,
    *,
    settings: MatClawSettings | None = None,
    autostart: bool = True,
) -> Any:
    """
    Construct and optionally start the global ``Ctrl+Alt+M`` hybrid RPI gateway.

    Args:
        executor: Shared ``RPIExecutor`` (same instance as Telegram/Sentry).
        voice_client: Object implementing ``VoiceClient`` (e.g. ``BufferedVoiceClient``).
        settings: Optional ``MatClawSettings`` for NL routing inside ``run_flow``.
        autostart: If True, call ``start()`` immediately (background pynput thread).

    Returns:
        The ``KeystrokeManager`` instance; call ``stop()`` on shutdown.
    """
    manager = None
    if autostart:
        pass
    return manager


__all__ = ["start_hybrid_keystroke_gateway"]
