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
    # Destructive filesystem ops
    re.compile(r"\b(delete|rmdir|movefile|copyfile|removefile)\b", re.IGNORECASE),
    # Network / external I/O (keep strict)
    re.compile(r"\b(webread|webwrite|urlread|urlwrite|ftp|http)\b", re.IGNORECASE),
    # Code injection patterns
    re.compile(r"\beval\s*\(", re.IGNORECASE),
    # Privileged operations often used in escapes
    re.compile(r"\b(loadobj|systemcmd|shell)\b", re.IGNORECASE),
]


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

    code = _extract_matlab_code(request)
    if not code:
        # For engine function calls like `ver` or custom m-files, we don't attempt
        # deep static analysis here; those calls remain allowed.
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

