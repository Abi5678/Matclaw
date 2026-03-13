"""
MatClaw LLM clients: Nemotron (NVIDIA NIM), tools, and self-healing.
"""

from src.matclaw.llm.nemotron_client import (
    NemotronClient,
    OrchestratorAction,
    OrchestratorThoughts,
)
from src.matclaw.llm.tools import get_tools_for_nemotron, MATCLAW_TOOLS

__all__ = [
    "NemotronClient",
    "OrchestratorAction",
    "OrchestratorThoughts",
    "get_tools_for_nemotron",
    "MATCLAW_TOOLS",
]
