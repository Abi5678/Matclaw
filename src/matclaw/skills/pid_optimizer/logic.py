"""
PID Optimizer skill: tune Kp, Ki, Kd via an observation loop (run sim → evaluate → iterate).

RPI: research (run sim, get metrics) → plan (new gains) → execute (run again).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from matclaw.matlab.matlab_bridge import MatlabBridge, MatlabCallRequest, MatlabCallResult
from matclaw.skills.base import SkillResult

logger = logging.getLogger(__name__)

_SKILL_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _SKILL_DIR / "templates"


def _to_numpy(arr: Any) -> np.ndarray:
    """Convert MATLAB array to numpy for plotting."""
    if arr is None:
        return np.array([])
    if isinstance(arr, np.ndarray):
        return np.asarray(arr).flatten()
    try:
        return np.asarray(arr).flatten()
    except Exception:
        return np.array([])


def research(
    matlab_bridge: MatlabBridge,
    Kp: float = 1.0,
    Ki: float = 0.5,
    Kd: float = 0.0,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run PID simulation with given gains and return rise time and overshoot."""
    out: dict[str, Any] = {"metrics": None, "error": None}
    try:
        matlab_bridge.addpath(str(_TEMPLATES_DIR))
        req = MatlabCallRequest(
            function="pid_eval",
            args=[float(Kp), float(Ki), float(Kd)],
            nargout=1,
        )
        result = matlab_bridge.call(req)
        if not result.success:
            out["error"] = result.error
            return out
        raw = result.result
        if raw is None:
            out["error"] = "pid_eval returned nothing"
            return out
        d = dict(raw) if hasattr(raw, "keys") else {"error": str(raw)}
        rt = float(d.get("rise_time", float("nan")) if d.get("rise_time") is not None else float("nan"))
        os = float(d.get("overshoot_pct", 0) if d.get("overshoot_pct") is not None else 0)
        stable = bool(d.get("stable", False))
        out["metrics"] = {"rise_time": rt, "overshoot_pct": os, "stable": stable}
        out["gains"] = {"Kp": Kp, "Ki": Ki, "Kd": Kd}
        if "t" in d and "y" in d:
            out["t"] = _to_numpy(d["t"])
            out["y"] = _to_numpy(d["y"])
    except Exception as exc:
        logger.exception("PID Optimizer research failed: %s", exc)
        out["error"] = str(exc)
    return out


def run_single_eval(
    matlab_bridge: MatlabBridge,
    Kp: float = 1.0,
    Ki: float = 0.5,
    Kd: float = 0.0,
    **kwargs: Any,
) -> tuple[dict[str, Any], np.ndarray, np.ndarray]:
    """
    Run a single PID evaluation and return metrics plus time/output arrays for plotting.
    Returns (research_dict, t, y) where t and y are numpy arrays for step response.
    """
    r = research(matlab_bridge, Kp=Kp, Ki=Ki, Kd=Kd, **kwargs)
    t = r.get("t", np.array([]))
    y = r.get("y", np.array([]))
    return r, np.asarray(t), np.asarray(y)


def plan(
    research_data: dict[str, Any],
    target_rise_time: float | None = None,
    target_overshoot_pct: float | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Decide new Kp, Ki, Kd from current metrics vs targets (simple heuristic)."""
    plan_data: dict[str, Any] = {"gains": research_data.get("gains", {"Kp": 1, "Ki": 0.5, "Kd": 0}), "done": False}
    if research_data.get("error"):
        return plan_data
    metrics = research_data.get("metrics") or {}
    Kp = plan_data["gains"]["Kp"]
    Ki = plan_data["gains"]["Ki"]
    Kd = plan_data["gains"]["Kd"]
    rise_time = metrics.get("rise_time") or float("nan")
    overshoot_pct = metrics.get("overshoot_pct") or 0
    stable = metrics.get("stable", False)
    target_rt = target_rise_time if target_rise_time is not None else 1.0
    target_os = target_overshoot_pct if target_overshoot_pct is not None else 15.0
    rt_ok = rise_time <= target_rt or (rise_time != rise_time)  # NaN
    os_ok = overshoot_pct <= target_os
    if stable and rt_ok and os_ok:
        plan_data["done"] = True
        plan_data["metrics"] = metrics
        return plan_data
    # Heuristic: slow rise -> increase Kp; high overshoot -> increase Kd, slight Kp reduction
    if not rt_ok and rise_time == rise_time:
        Kp = min(Kp * 1.2, 50)
    if not os_ok:
        Kd = min(Kd + 0.1, 10)
        Kp = max(Kp * 0.95, 0.1)
    plan_data["gains"] = {"Kp": Kp, "Ki": Ki, "Kd": Kd}
    plan_data["metrics"] = metrics
    return plan_data


def execute(
    matlab_bridge: MatlabBridge,
    plan_data: dict[str, Any],
    **kwargs: Any,
) -> SkillResult:
    """Run simulation with planned gains (delegates to research for the actual run)."""
    gains = plan_data.get("gains") or {}
    r = research(
        matlab_bridge,
        Kp=gains.get("Kp", 1),
        Ki=gains.get("Ki", 0.5),
        Kd=gains.get("Kd", 0),
        **kwargs,
    )
    if r.get("error"):
        return SkillResult(success=False, error=r["error"])
    metrics = r.get("metrics") or {}
    return SkillResult(
        success=True,
        message=f"Kp={gains.get('Kp')} Ki={gains.get('Ki')} Kd={gains.get('Kd')} → rise_time={metrics.get('rise_time')} overshoot={metrics.get('overshoot_pct')}%",
        data={"gains": gains, "metrics": metrics, "stable": metrics.get("stable")},
    )


def run(
    matlab_bridge: MatlabBridge,
    Kp: float = 1.0,
    Ki: float = 0.5,
    Kd: float = 0.0,
    target_rise_time: float | None = None,
    target_overshoot_pct: float | None = None,
    max_iterations: int = 10,
    **kwargs: Any,
) -> SkillResult:
    """Observation loop: run simulation → evaluate → plan new gains → repeat until stable or max_iterations."""
    target_rt = target_rise_time if target_rise_time is not None else 1.0
    target_os = target_overshoot_pct if target_overshoot_pct is not None else 15.0
    gains = {"Kp": Kp, "Ki": Ki, "Kd": Kd}
    last_metrics: dict[str, Any] = {}
    for it in range(max_iterations):
        r = research(matlab_bridge, Kp=gains["Kp"], Ki=gains["Ki"], Kd=gains["Kd"], **kwargs)
        if r.get("error"):
            return SkillResult(success=False, error=r["error"], data={"iterations": it, "gains": gains})
        p = plan(r, target_rise_time=target_rt, target_overshoot_pct=target_os, **kwargs)
        last_metrics = p.get("metrics") or r.get("metrics") or {}
        if p.get("done"):
            return SkillResult(
                success=True,
                message=f"Stable after {it + 1} iterations. Rise time={last_metrics.get('rise_time')} s, Overshoot={last_metrics.get('overshoot_pct')}%",
                data={
                    "gains": p.get("gains", gains),
                    "metrics": last_metrics,
                    "iterations": it + 1,
                    "stable": True,
                },
            )
        gains = p.get("gains", gains)
    return SkillResult(
        success=last_metrics.get("stable", False),
        message=f"Stopped after {max_iterations} iterations. Last: rise_time={last_metrics.get('rise_time')} s, overshoot={last_metrics.get('overshoot_pct')}%",
        data={"gains": gains, "metrics": last_metrics, "iterations": max_iterations, "stable": last_metrics.get("stable", False)},
    )
