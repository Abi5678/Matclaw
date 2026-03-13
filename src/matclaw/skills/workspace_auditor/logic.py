"""
Workspace Auditor skill: scan whos and license to prevent OOM and license errors.

RPI: research (run audit) → plan (warnings) → execute (return summary).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.matclaw.matlab.matlab_bridge import MatlabBridge, MatlabCallRequest, MatlabCallResult
from src.matclaw.skills.base import SkillResult

logger = logging.getLogger(__name__)

_SKILL_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _SKILL_DIR / "templates"


def research(matlab_bridge: MatlabBridge, **kwargs: Any) -> dict[str, Any]:
    """Gather workspace and license state from MATLAB."""
    out: dict[str, Any] = {"raw": None, "audit": None, "error": None}
    try:
        matlab_bridge.addpath(str(_TEMPLATES_DIR))
        req = MatlabCallRequest(function="workspace_audit", args=[], nargout=1)
        result = matlab_bridge.call(req)
        if not result.success:
            out["error"] = result.error
            return out
        raw = result.result
        if raw is None:
            out["error"] = "workspace_audit returned nothing"
            return out
        # Convert MATLAB struct to dict (engine returns dict)
        if hasattr(raw, "keys"):
            d = dict(raw)
        else:
            d = {"error": str(raw)}
        # Parse v1.1 format: variable_names, variable_bytes, variable_classes (pipe-delimited)
        # Fallback: legacy names/bytes/classes
        def _parse_pipe(s: Any) -> list:
            if isinstance(s, str) and s:
                return [x for x in s.split("|") if x]
            if hasattr(s, "__iter__") and not isinstance(s, str):
                return list(s)
            return []

        names = _parse_pipe(d.get("variable_names") or d.get("names", ""))
        classes_list = _parse_pipe(d.get("variable_classes") or d.get("classes", ""))
        bytes_raw = d.get("variable_bytes") or ""
        if isinstance(bytes_raw, str) and bytes_raw:
            bytes_list = [float(x) for x in bytes_raw.split("|") if x]
        else:
            bytes_list = d.get("bytes", [])
            if hasattr(bytes_list, "__iter__") and not isinstance(bytes_list, list):
                bytes_list = [float(x) for x in bytes_list]
        total = float(d.get("total_bytes", 0))
        license_val = d.get("license_info") or d.get("license_inuse")
        out["audit"] = {
            "names": names,
            "bytes": bytes_list,
            "classes": classes_list,
            "total_bytes": total,
            "license_inuse": license_val,
            "error": d.get("error"),
        }
        out["raw"] = d
    except Exception as exc:
        logger.exception("Workspace Auditor research failed: %s", exc)
        out["error"] = str(exc)
    return out


def plan(research_data: dict[str, Any], warn_if_bytes_above: int | None = None, **kwargs: Any) -> dict[str, Any]:
    """Decide warnings (OOM risk, license)."""
    plan_data: dict[str, Any] = {"warnings": [], "summary": ""}
    if research_data.get("error"):
        plan_data["warnings"].append(f"Research failed: {research_data['error']}")
        return plan_data
    audit = research_data.get("audit") or {}
    total = audit.get("total_bytes", 0) or 0
    threshold = warn_if_bytes_above if warn_if_bytes_above is not None else 500_000_000  # 500 MB default
    if total > threshold:
        plan_data["warnings"].append(f"Total workspace ~{total / 1e6:.1f} MB (above {threshold / 1e6:.0f} MB). Risk of OOM.")
    names = audit.get("names") or []
    bytes_list = audit.get("bytes") or []
    for i, n in enumerate(names):
        b = bytes_list[i] if i < len(bytes_list) else 0
        if b > threshold:
            plan_data["warnings"].append(f"Variable '{n}' is ~{b / 1e6:.1f} MB.")
    plan_data["summary"] = f"Variables: {len(names)}, Total: {total / 1e6:.2f} MB"
    plan_data["audit"] = audit
    return plan_data


def execute(
    matlab_bridge: MatlabBridge,
    plan_data: dict[str, Any],
    **kwargs: Any,
) -> SkillResult:
    """Return structured summary to the user (read-only; no MATLAB call)."""
    try:
        audit = plan_data.get("audit") or {}
        warnings = plan_data.get("warnings") or []
        summary = plan_data.get("summary") or ""
        data = {
            "variables": [
                {"name": n, "bytes": b, "class": c}
                for n, b, c in zip(
                    audit.get("names", []),
                    audit.get("bytes", []),
                    audit.get("classes", []),
                )
            ],
            "total_bytes": audit.get("total_bytes", 0),
            "license_inuse": audit.get("license_inuse"),
            "warnings": warnings,
            "summary": summary,
        }
        return SkillResult(success=True, message=summary, data=data)
    except Exception as exc:
        logger.exception("Workspace Auditor execute failed: %s", exc)
        return SkillResult(success=False, error=str(exc))


def run(matlab_bridge: MatlabBridge, warn_if_bytes_above: int | None = None, **kwargs: Any) -> SkillResult:
    """One-shot: research → plan → execute. Entry point for MCP or daemon."""
    r = research(matlab_bridge, **kwargs)
    p = plan(r, warn_if_bytes_above=warn_if_bytes_above, **kwargs)
    return execute(matlab_bridge, p, **kwargs)
