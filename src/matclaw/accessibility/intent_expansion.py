"""
Intent Expansion: infer a long-horizon goal for vague prompts.

Mandate:
  - use context from current MATLAB workspace and ChromaDB
  - avoid leaking workspace variable values (use metadata only)
  - keep the expansion local-first; do not call external telemetry
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from matclaw.memory.memory_manager import MemoryManager
from matclaw.matlab.matlab_bridge import MatlabBridge
from matclaw.skills import load_skill_logic

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntentExpansionResult:
    """Result of expanding a user prompt into an inferred long-horizon goal."""

    expanded_prompt: str
    inferred_skill: str | None = None
    confidence: float = 0.5


_VAGUE_PATTERNS = [
    re.compile(r"\b(fix|fix it|fix that|improve|address)\b", re.IGNORECASE),
    re.compile(r"\b(what do i do|help|tweak|something|maybe)\b", re.IGNORECASE),
]


def _is_vague_prompt(prompt: str) -> bool:
    p = prompt.strip()
    if len(p) < 12:
        return True
    if any(r.search(p) for r in _VAGUE_PATTERNS):
        return True
    # If it lacks common intent verbs, treat as vague.
    if not re.search(r"\b(run|plot|tune|optimize|simulate|audit|report|fix|generate)\b", p, flags=re.IGNORECASE):
        return True
    return False


def _infer_skill_from_keywords(prompt: str) -> str | None:
    p = prompt.lower()
    if "pid" in p or "gain" in p or "tune" in p or "optimize" in p:
        return "pid_optimizer"
    if "report" in p or "summary" in p or "generate report" in p:
        return "report_generator"
    if "audit" in p or "workspace" in p or "variables" in p:
        return "workspace_auditor"
    if "simulate" in p or ".slx" in p:
        return "simulink_runner"
    # Default: if user mentions nothing specific, run workspace audit first.
    return None


def expand_prompt_to_goal(
    prompt: str,
    *,
    matlab_bridge: MatlabBridge,
    memory_manager: MemoryManager,
    n_memory: int = 3,
) -> IntentExpansionResult:
    """
    Expand vague prompts by combining workspace metadata and memory context.

    Args:
        prompt: Raw user prompt (may be vague).
        matlab_bridge: Active MATLAB bridge (used for metadata-only workspace audit).
        memory_manager: Chroma-backed memory manager.
        n_memory: How many memory items to use.

    Returns:
        IntentExpansionResult with expanded_prompt and inferred_skill (if any).
    """

    prompt = prompt.strip()
    if not _is_vague_prompt(prompt):
        return IntentExpansionResult(expanded_prompt=prompt, inferred_skill=_infer_skill_from_keywords(prompt), confidence=0.75)

    inferred = _infer_skill_from_keywords(prompt)
    if inferred is None:
        try:
            results = memory_manager.query_context(f"last long-horizon goal near: {prompt}", n_results=n_memory)
        except Exception:
            results = []

        # Prefer a skill inferred from the most recent artifact metadata.
        for r in results:
            meta = r.get("metadata") or {}
            skill = meta.get("skill")
            if isinstance(skill, str) and skill:
                inferred = skill
                break

    # Workspace metadata only: call workspace_auditor to learn what exists.
    workspace_summary = ""
    try:
        auditor = load_skill_logic("workspace_auditor")
        if auditor and hasattr(auditor, "run"):
            res = auditor.run(matlab_bridge)
            if getattr(res, "success", False) and getattr(res, "data", None):
                data = res.data or {}
                names = data.get("variables") or []
                n_vars = len(names)
                total_mb = (data.get("total_bytes") or 0) / 1e6
                workspace_summary = f"Workspace metadata: variables={n_vars}, total~{total_mb:.1f}MB."
    except Exception as exc:
        logger.debug("Workspace intent expansion audit failed: %s", exc)

    try:
        mem_results = memory_manager.query_context(f"goal inference for: {prompt}", n_results=n_memory)
    except Exception:
        mem_results = []

    memory_snips: list[str] = []
    for r in mem_results:
        meta = r.get("metadata") or {}
        doc = r.get("document") or ""
        summary = meta.get("summary") or meta.get("message") or doc[:180]
        if summary:
            memory_snips.append(summary)
        if len(memory_snips) >= n_memory:
            break

    long_horizon = inferred or "workspace_auditor"
    expanded = (
        f"User request (possibly vague): {prompt}\n\n"
        f"Long-horizon goal inferred: {long_horizon}\n"
        f"{workspace_summary}\n"
        f"Recent memory hints: " + ("; ".join(memory_snips) if memory_snips else "(none)") + "\n\n"
        f"Please execute the inferred goal using MatClaw's RPI loop."
    )

    confidence = 0.6 if inferred else 0.45
    return IntentExpansionResult(expanded_prompt=expanded, inferred_skill=inferred, confidence=confidence)

