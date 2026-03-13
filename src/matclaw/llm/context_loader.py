"""
Lab context loader: workspace audit + recent memory for Nemotron and NL router.
Used by app.py (Streamlit) and main.py (daemon) to inject Current Lab Context.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_lab_context(
    *,
    matlab_bridge: Any = None,
    memory_manager: Any = None,
    memory_n_results: int = 3,
    persist_directory: str = ".matclaw_chromadb",
) -> dict[str, Any]:
    """
    Load Current Lab Context: workspace_audit summary + last N memory items.

    Returns dict with:
      - workspace_audit: summary string, or error/status if unavailable
      - workspace_has_error: True if audit shows error (scalar struct, license, etc.)
      - workspace_empty: True if 0 variables
      - recent_memory: list of last N artifacts (metadata + document)
    """
    ctx: dict[str, Any] = {
        "workspace_audit": "",
        "workspace_has_error": False,
        "workspace_empty": False,
        "recent_memory": [],
    }

    # Workspace audit (requires MATLAB)
    if matlab_bridge and hasattr(matlab_bridge, "is_healthy") and matlab_bridge.is_healthy():
        try:
            from src.matclaw.skills.workspace_auditor.logic import run as workspace_audit_run
            result = workspace_audit_run(matlab_bridge)
            if getattr(result, "success", False) and getattr(result, "data", None):
                data = result.data
                summary = data.get("summary") or data.get("message") or ""
                variables = data.get("variables") or []
                warnings = data.get("warnings") or []
                ctx["workspace_audit"] = summary
                if warnings:
                    ctx["workspace_audit"] += "\nWarnings: " + "; ".join(warnings)
                ctx["workspace_empty"] = len(variables) == 0
                # Detect common errors in summary/warnings
                err_text = (summary + " " + " ".join(warnings)).lower()
                ctx["workspace_has_error"] = any(
                    e in err_text
                    for e in ("scalar struct", "error", "license", "failed", "exception")
                )
            else:
                err = getattr(result, "error", "Audit failed")
                ctx["workspace_audit"] = f"Audit error: {err}"
                ctx["workspace_has_error"] = True
        except Exception as exc:
            logger.warning("Workspace audit failed: %s", exc)
            ctx["workspace_audit"] = f"Workspace audit failed: {exc}"
            ctx["workspace_has_error"] = True
    else:
        ctx["workspace_audit"] = "MATLAB not connected. Workspace audit unavailable."

    # Recent memory
    if memory_manager:
        try:
            if hasattr(memory_manager, "_ensure_client"):
                memory_manager._ensure_client()
            results = memory_manager.query_context("recent artifacts parameters lessons", n_results=memory_n_results)
            ctx["recent_memory"] = [
                {
                    "metadata": r.get("metadata", {}),
                    "document": r.get("document", ""),
                }
                for r in results
            ]
        except Exception as exc:
            logger.warning("Memory query failed: %s", exc)
    else:
        try:
            from src.matclaw.memory.memory_manager import MemoryManager
            mm = MemoryManager(persist_directory=persist_directory)
            mm._ensure_client()
            results = mm.query_context("recent artifacts parameters lessons", n_results=memory_n_results)
            ctx["recent_memory"] = [
                {"metadata": r.get("metadata", {}), "document": r.get("document", "")}
                for r in results
            ]
        except Exception as exc:
            logger.warning("Memory load failed: %s", exc)

    return ctx


def format_lab_context_for_prompt(ctx: dict[str, Any]) -> str:
    """Format lab context as a string for injection into system prompt."""
    parts = []
    parts.append(f"Workspace: {ctx.get('workspace_audit', 'N/A')}")
    if ctx.get("workspace_has_error"):
        parts.append("[Lab has an error or warning - prioritize addressing it if the user asks to fix something.]")
    if ctx.get("workspace_empty"):
        parts.append("[Workspace is empty - variables may need to be created.]")
    recent = ctx.get("recent_memory") or []
    if recent:
        parts.append("Recent memory:")
        for i, r in enumerate(recent, 1):
            meta = r.get("metadata", {})
            doc = r.get("document", "") or meta.get("summary", "") or meta.get("message", "") or str(meta)[:150]
            parts.append(f"  {i}. {doc}")
    return "\n".join(parts)
