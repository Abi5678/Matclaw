"""
Simulink Runner skill: Load-Compile-Run pattern for .slx models.

RPI: research (check license, model exists) -> plan (params) -> execute (load_system, set_param, sim).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from matclaw.matlab.matlab_bridge import MatlabBridge, MatlabCallRequest
from matclaw.skills.base import SkillResult

logger = logging.getLogger(__name__)

_SKILL_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _SKILL_DIR / "templates"


def research(
    matlab_bridge: MatlabBridge,
    model_name: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Check Simulink license and if model file exists."""
    out: dict[str, Any] = {"license_ok": False, "model_found": False, "model_path": "", "error": None}
    if not model_name:
        out["error"] = "model_name is required"
        return out
    try:
        matlab_bridge.addpath(str(_TEMPLATES_DIR))
        # Check Simulink license
        req = MatlabCallRequest(function="license", args=["test", "Simulink"], nargout=1)
        result = matlab_bridge.call(req)
        if result.success and result.result is not None:
            out["license_ok"] = bool(result.result)
        else:
            out["license_ok"] = False
        # Check if model exists via exist('model.slx', 'file') -> 2 if file exists
        model_base = model_name.replace(".slx", "").replace(".mdl", "")
        check_cmd = f"exist('{model_base}.slx','file')"
        req2 = MatlabCallRequest(function="eval", args=[check_cmd], nargout=1)
        result2 = matlab_bridge.call(req2)
        if result2.success and result2.result is not None:
            val = int(result2.result) if hasattr(result2.result, "__int__") else 0
            out["model_found"] = val >= 2  # 2=file, 8=model
    except Exception as exc:
        logger.exception("Simulink Runner research failed: %s", exc)
        out["error"] = str(exc)
    return out


def plan(
    research_data: dict[str, Any],
    model_name: str = "",
    stop_time: float = 10.0,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build plan: model name, stop time."""
    plan_data: dict[str, Any] = {
        "model_name": model_name or research_data.get("model_name", ""),
        "stop_time": float(stop_time),
        "action": "run_simulink",
    }
    if research_data.get("error"):
        plan_data["warnings"] = [f"Research: {research_data['error']}"]
    elif not research_data.get("license_ok"):
        plan_data["warnings"] = ["Simulink license may not be available."]
    elif not research_data.get("model_found"):
        plan_data["warnings"] = ["Model file not found on path. Will attempt load_system anyway."]
    return plan_data


def execute(
    matlab_bridge: MatlabBridge,
    plan_data: dict[str, Any],
    **kwargs: Any,
) -> SkillResult:
    """Run Simulink: load_system -> set_param -> sim via template."""
    model_name = plan_data.get("model_name", "")
    stop_time = float(plan_data.get("stop_time", 10.0))
    if not model_name:
        return SkillResult(success=False, error="model_name is required")
    try:
        matlab_bridge.addpath(str(_TEMPLATES_DIR))
        model_clean = model_name.replace(".slx", "").replace(".mdl", "")
        req = MatlabCallRequest(
            function="simulink_run",
            args=[model_clean, stop_time],
            nargout=1,
        )
        result = matlab_bridge.call(req)
        if not result.success:
            return SkillResult(success=False, error=result.error or "Simulink run failed")
        raw = result.result
        if raw is None:
            return SkillResult(success=False, error="simulink_run returned nothing")
        d = dict(raw) if hasattr(raw, "keys") else {"status": "Unknown"}
        status = str(d.get("status", "Error"))
        error_log = str(d.get("error_log", ""))
        final_state = str(d.get("final_state", ""))
        st = float(d.get("stop_time", stop_time))
        success = status.lower() == "success"
        msg = f"Simulink {model_clean}: {status}" + (f" — {error_log}" if error_log else "")
        data = {
            "status": status,
            "stop_time": st,
            "final_state": final_state,
            "error_log": error_log,
            "model_name": model_clean,
        }
        return SkillResult(success=success, message=msg, data=data)
    except Exception as exc:
        logger.exception("Simulink Runner execute failed: %s", exc)
        return SkillResult(success=False, error=str(exc))


def run(
    matlab_bridge: MatlabBridge,
    model_name: str = "",
    stop_time: float = 10.0,
    **kwargs: Any,
) -> SkillResult:
    """One-shot: research -> plan -> execute. Entry point for MCP or daemon."""
    r = research(matlab_bridge, model_name=model_name, **kwargs)
    p = plan(r, model_name=model_name, stop_time=stop_time, **kwargs)
    return execute(matlab_bridge, p, **kwargs)
