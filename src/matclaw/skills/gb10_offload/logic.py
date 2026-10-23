"""
GB10 Offload skill — dispatch simulation to a DGX Spark cluster.

RPI: research (probe cluster) → plan (pick path + split strategy) → execute.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.matclaw.skills.base import SkillResult

logger = logging.getLogger(__name__)


def _build_client(settings: Any = None) -> Any | None:
    """Lazily construct a GB10Client from config."""
    from src.matclaw.config.base_config import GB10Settings

    if settings is None:
        from src.matclaw.config.base_config import MatClawSettings

        settings = MatClawSettings().gb10

    if not settings.enabled or not settings.nodes:
        return None

    from src.matclaw.gb10.client import GB10Client

    try:
        return GB10Client(settings)
    except Exception as exc:
        logger.warning("Failed to create GB10Client: %s", exc)
        return None


def research(
    matlab_bridge: Any,
    gb10_settings: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Probe cluster connectivity and capabilities."""
    out: dict[str, Any] = {
        "cluster_reachable": False,
        "node_status": {},
        "cuda_available": False,
        "octave_available": False,
        "gpu_coder_available": False,
        "node_count": 0,
        "error": None,
    }

    client = _build_client(gb10_settings)
    if client is None:
        out["error"] = "GB10 backend not configured or disabled"
        return out

    out["node_count"] = client.node_count
    probe_results = client.probe_cluster()

    any_ok = False
    any_cuda = False
    for alias, result in probe_results.items():
        status = {
            "reachable": result.success,
            "cuda": "CUDA_OK" in result.stdout if result.success else False,
        }
        out["node_status"][alias] = status
        if result.success:
            any_ok = True
        if status["cuda"]:
            any_cuda = True

    out["cluster_reachable"] = any_ok
    out["cuda_available"] = any_cuda

    if any_ok:
        head = client.head_node
        octave_check = client.run_ssh(
            head,
            f"command -v {client.settings.octave_path} >/dev/null 2>&1 && echo OCTAVE_OK || echo OCTAVE_MISSING",
            timeout=10,
        )
        out["octave_available"] = octave_check.success and "OCTAVE_OK" in octave_check.stdout

    if gb10_settings and getattr(gb10_settings, "gpu_coder_enabled", False):
        if matlab_bridge is not None and hasattr(matlab_bridge, "is_healthy") and matlab_bridge.is_healthy():
            check_req_mod = getattr(matlab_bridge, "run_matlab_code", None)
            if check_req_mod:
                ok, output = check_req_mod("disp(exist('codegen','file'))")
                out["gpu_coder_available"] = ok and output.strip() not in ("0", "")

    return out


def plan(
    research_data: dict[str, Any],
    mode: str = "",
    work_items: list[dict] | None = None,
    multi_node: bool | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Select execution path and split strategy.

    Priority: gpu_coder (if available and requested) → octave → binary.
    """
    plan_data: dict[str, Any] = {
        "mode": mode,
        "multi_node": False,
        "strategy": "single",
        "node_count": research_data.get("node_count", 1),
        "warnings": [],
    }

    if research_data.get("error"):
        plan_data["warnings"].append(research_data["error"])
        return plan_data

    if not research_data.get("cluster_reachable"):
        plan_data["warnings"].append("No GB10 nodes reachable")
        return plan_data

    if not mode:
        if research_data.get("gpu_coder_available") and kwargs.get("function_name"):
            mode = "gpu_coder"
        elif research_data.get("octave_available"):
            mode = "octave"
        elif research_data.get("cuda_available"):
            mode = "binary"
        else:
            mode = "octave"
            plan_data["warnings"].append("Octave not confirmed; attempting anyway")

    plan_data["mode"] = mode

    if mode == "gpu_coder" and not research_data.get("gpu_coder_available"):
        plan_data["warnings"].append(
            "GPU Coder requested but not available on host; falling back to octave"
        )
        plan_data["mode"] = "octave" if research_data.get("octave_available") else "binary"

    use_multi = multi_node if multi_node is not None else bool(work_items)
    if use_multi and research_data.get("node_count", 1) > 1:
        plan_data["multi_node"] = True
        plan_data["strategy"] = "split"
    else:
        plan_data["strategy"] = "single"

    return plan_data


def execute(
    matlab_bridge: Any,
    plan_data: dict[str, Any],
    *,
    code: str = "",
    function_name: str = "",
    entry_types: str = "",
    source_dir: str = "",
    work_items: list[dict] | None = None,
    run_args: str = "",
    gb10_settings: Any = None,
    **kwargs: Any,
) -> SkillResult:
    """Run the simulation on the GB10 cluster."""
    client = _build_client(gb10_settings)
    if client is None:
        return SkillResult(success=False, error="GB10 backend not available")

    mode = plan_data.get("mode", "octave")
    multi = plan_data.get("multi_node", False)
    warnings = plan_data.get("warnings", [])

    try:
        if mode == "gpu_coder":
            from src.matclaw.gb10.codegen import codegen_offload

            ok, output = codegen_offload(
                matlab_bridge,
                client,
                function_name,
                entry_types,
                source_dir or ".",
                run_args=run_args,
            )
            return SkillResult(
                success=ok,
                message=output if ok else "",
                error="" if ok else output,
                data={"mode": "gpu_coder", "warnings": warnings},
            )

        if mode == "octave":
            if multi and work_items:
                from src.matclaw.gb10.octave_fallback import run_octave_split

                ok, output, artifacts = run_octave_split(
                    client, code, work_items,
                )
            else:
                from src.matclaw.gb10.octave_fallback import run_octave_on_node

                ok, output, artifacts = run_octave_on_node(
                    client, client.head_node, code,
                )
            return SkillResult(
                success=ok,
                message=output if ok else "",
                error="" if ok else output,
                data={"mode": "octave", "multi_node": multi, "artifacts": artifacts, "warnings": warnings},
            )

        if mode == "binary":
            if multi and work_items:
                import json

                result = client.run_split(
                    work_items=work_items,
                    command_template=code,
                )
                return SkillResult(
                    success=result.success,
                    message=result.merged_stdout if result.success else "",
                    error="" if result.success else result.merged_stdout,
                    data={
                        "mode": "binary",
                        "multi_node": True,
                        "artifacts": result.artifacts,
                        "warnings": warnings,
                    },
                )
            else:
                result = client.run_on_head(code, timeout=gb10_settings.timeout_seconds if gb10_settings else None)
                return SkillResult(
                    success=result.success,
                    message=result.stdout if result.success else "",
                    error="" if result.success else (result.stderr or result.stdout),
                    data={"mode": "binary", "multi_node": False, "artifacts": result.artifacts, "warnings": warnings},
                )

        return SkillResult(success=False, error=f"Unknown mode: {mode}")

    except Exception as exc:
        logger.exception("GB10 offload execute failed: %s", exc)
        return SkillResult(success=False, error=str(exc))


def run(
    matlab_bridge: Any,
    code: str = "",
    mode: str = "",
    function_name: str = "",
    entry_types: str = "",
    source_dir: str = "",
    work_items: list[dict] | None = None,
    multi_node: bool | None = None,
    run_args: str = "",
    gb10_settings: Any = None,
    **kwargs: Any,
) -> SkillResult:
    """One-shot RPI: research → plan → execute for GB10 offload."""
    r = research(matlab_bridge, gb10_settings=gb10_settings)
    p = plan(
        r,
        mode=mode,
        work_items=work_items,
        multi_node=multi_node,
        function_name=function_name,
        **kwargs,
    )
    return execute(
        matlab_bridge,
        p,
        code=code,
        function_name=function_name,
        entry_types=entry_types,
        source_dir=source_dir,
        work_items=work_items,
        run_args=run_args,
        gb10_settings=gb10_settings,
        **kwargs,
    )
