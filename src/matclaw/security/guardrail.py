"""
MATLAB execution guardrails.

Mandate: every MATLAB call request must pass through this module before execution.

This guardrail is intentionally conservative and focuses on preventing obviously
dangerous operations (shelling out, destructive file operations, etc.).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class GuardrailDecision(BaseModel):
    """Decision made by the guardrail before MATLAB execution."""

    allow: bool
    reason: str
    redacted_code: str | None = None


_DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    # Shell/system escape
    re.compile(r"(^|[^A-Za-z0-9_])(!\s*|system\s*\(|dos\s*\(|unix\s*\()", re.IGNORECASE),
    # Destructive filesystem ops (copyfile is intentionally excluded — benign)
    re.compile(r"\b(delete|rmdir|movefile|removefile)\b", re.IGNORECASE),
    # Network writes and non-HTTP protocols (read-only webread is allowed for public APIs)
    re.compile(r"\b(webwrite|urlwrite|ftp)\b", re.IGNORECASE),
    # Code injection patterns
    re.compile(r"\beval\s*\(", re.IGNORECASE),
    # Privileged operations often used in escapes
    re.compile(r"\b(loadobj|systemcmd|shell)\b", re.IGNORECASE),
]

# Functions that must NEVER be called directly via the MATLAB engine bridge,
# regardless of whether they appear inside eval() payloads.
# Note: eval/evalc/feval are NOT blocked here — they are the primary code
# execution path and their payloads are inspected by _extract_matlab_code +
# _DANGEROUS_PATTERNS instead.
_BLOCKED_FUNCTIONS: frozenset[str] = frozenset({
    "system", "dos", "unix", "perl", "python",
    "delete", "rmdir", "movefile", "removefile",
    "builtin", "str2func", "loadobj",
    "webwrite", "urlwrite",
})

# Lifecycle functions that bypass the guardrail (engine management only).
_LIFECYCLE_FUNCTIONS: frozenset[str] = frozenset({"quit", "exit"})


def _extract_matlab_code(request: Any) -> str:
    """
    Extract the relevant MATLAB code string from a MatlabCallRequest-like object.

    Args:
        request: MatlabCallRequest (or compatible object).

    Returns:
        The extracted code (may be empty).
    """

    try:
        fn = getattr(request, "function", "") or ""
        args = getattr(request, "args", None) or []
        if fn.lower() in {"eval", "evalc"} and args:
            return str(args[0])
        if fn.lower() in {"feval"} and args:
            return " ".join(str(a) for a in args)
        return ""
    except Exception:
        return ""


def guard_matlab_call(request: Any) -> GuardrailDecision:
    """
    Guard a MATLAB call request before execution.

    Args:
        request: A MatlabCallRequest-like object with `function` and `args`.

    Returns:
        GuardrailDecision allowing or denying execution.
    """

    fn = (getattr(request, "function", "") or "").strip()
    fn_lower = fn.lower()

    # Allow engine lifecycle commands unconditionally.
    if fn_lower in _LIFECYCLE_FUNCTIONS:
        return GuardrailDecision(allow=True, reason="Lifecycle function allowed.")

    # Block dangerous functions called directly (e.g. bridge.call(function="system")).
    if fn_lower in _BLOCKED_FUNCTIONS:
        reason = f"Guardrail blocked direct call to dangerous function '{fn}'."
        logger.warning("MATLAB guardrail deny: %s", reason)
        return GuardrailDecision(allow=False, reason=reason)

    code = _extract_matlab_code(request)
    if not code:
        return GuardrailDecision(allow=True, reason="No MATLAB code payload detected; allowed.")

    for pat in _DANGEROUS_PATTERNS:
        if pat.search(code):
            reason = (
                "Guardrail blocked MATLAB execution due to dangerous pattern match. "
                f"Pattern: {pat.pattern[:80]}"
            )
            logger.warning("MATLAB guardrail deny: %s", reason)
            return GuardrailDecision(allow=False, reason=reason, redacted_code=None)

    return GuardrailDecision(allow=True, reason="Guardrail check passed; allowed.")

