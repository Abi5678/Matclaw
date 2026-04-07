"""
Spec-level checks beyond "MATLAB exited cleanly": toolbox constraints, plot hints, etc.
Used for agentic quality signals (run success vs spec success).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Common Statistics / ML / Optimization Toolbox calls (non-exhaustive; extend as needed)
_TOOLBOX_IDENTIFIERS = frozenset({
    "knnsearch", "pdist2", "fitlm", "fitrgp", "lasso", "ridge", "fminunc", "fmincon",
    "ga", "particleswarm", "surrogateopt", "bayesopt", "readtable", "writetable",
    "histogram2", "dryaddownload", "mpc", "KalmanFilter",
})


def user_requests_base_matlab_only(user_text: str) -> bool:
    t = (user_text or "").lower()
    phrases = (
        "base matlab",
        "no toolbox",
        "without toolbox",
        "no toolboxes",
        "only base matlab",
        "toolbox-free",
        "statistics and machine learning",
    )
    return any(p in t for p in phrases)


def _plot_count_from_code(code: str) -> int:
    return len(re.findall(r"\bfigure\s*\(", code, re.IGNORECASE))


@dataclass
class SpecComplianceResult:
    """Whether execution matches stated constraints (independent of exit code)."""

    run_success: bool
    spec_satisfied: bool
    user_asked_base_only: bool
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_success": self.run_success,
            "spec_satisfied": self.spec_satisfied,
            "user_asked_base_only": self.user_asked_base_only,
            "violations": list(self.violations),
            "notes": list(self.notes),
        }


def analyze_matlab_run(
    *,
    code: str,
    user_text: str,
    tool_output_success: bool,
    plots: list[str],
) -> SpecComplianceResult:
    """
    tool_output_success: True if no MATLAB error markers in output / tool marked success.
    """
    violations: list[str] = []
    notes: list[str] = []
    base_only = user_requests_base_matlab_only(user_text)

    if base_only:
        for sym in sorted(_TOOLBOX_IDENTIFIERS):
            if re.search(rf"\b{re.escape(sym)}\s*\(", code):
                violations.append(f"Uses `{sym}` — likely requires a toolbox (task asked for base MATLAB only).")

    if re.match(r"^\s*function\s+\w+", (code or "").strip()):
        notes.append(
            "Code starts with a named function — in MatClaw temp files the function name "
            "must match the filename or you should use a plain script + local functions."
        )

    if tool_output_success and user_text and any(w in user_text.lower() for w in ("plot", "visual", "graph", "chart")):
        if not plots:
            violations.append("Task implied a plot but no plot artifact was returned.")

    if _plot_count_from_code(code or "") > 1:
        notes.append(
            "Multiple `figure` calls — MatClaw may only surface the first figure in the UI; "
            "prefer subplot/tiledlayout in one figure."
        )

    spec_ok = len(violations) == 0
    return SpecComplianceResult(
        run_success=tool_output_success,
        spec_satisfied=spec_ok and tool_output_success,
        user_asked_base_only=base_only,
        violations=violations,
        notes=notes,
    )


__all__ = [
    "SpecComplianceResult",
    "analyze_matlab_run",
    "user_requests_base_matlab_only",
]
