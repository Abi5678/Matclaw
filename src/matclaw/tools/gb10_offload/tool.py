"""
MCP / LLM tool: offload a simulation to the GB10 (DGX Spark) cluster.

The agent can call this tool to dispatch MATLAB-compatible code (Octave),
GPU Coder compiled kernels, or arbitrary binaries to the remote cluster,
optionally splitting work across multiple nodes.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from src.matclaw.tools.base import BaseTool, ToolContext, ToolManifest, ToolParam, ToolResult

logger = logging.getLogger(__name__)


class GB10OffloadTool(BaseTool):
    manifest = ToolManifest(
        name="gb10_offload",
        description=(
            "Offload a simulation to the NVIDIA GB10 (DGX Spark) cluster. "
            "Supports Octave (.m scripts), GPU Coder (CUDA from MATLAB functions), "
            "and binary execution. Can split Monte Carlo / parameter sweeps across "
            "multiple nodes."
        ),
        version="0.1.0",
        runtime="gb10",
        inputs={
            "code": ToolParam(
                type="string",
                description="MATLAB/Octave code or shell command to execute on the GB10.",
            ),
            "mode": ToolParam(
                type="string",
                description="Execution mode: 'octave' (default), 'gpu_coder', or 'binary'.",
                required=False,
                default="octave",
            ),
            "work_items": ToolParam(
                type="array",
                description=(
                    "JSON array of work items to split across nodes. "
                    "Each item is a dict of parameters for one run."
                ),
                required=False,
            ),
            "multi_node": ToolParam(
                type="boolean",
                description="Use all cluster nodes (default: true when work_items given).",
                required=False,
            ),
            "function_name": ToolParam(
                type="string",
                description="MATLAB function name for GPU Coder path.",
                required=False,
            ),
            "entry_types": ToolParam(
                type="string",
                description="MATLAB type spec for GPU Coder (e.g. '{zeros(1000,1), 0.01}').",
                required=False,
            ),
            "source_dir": ToolParam(
                type="string",
                description="Path to .m source directory on host (for GPU Coder).",
                required=False,
            ),
        },
        outputs={
            "output": ToolParam(type="string", description="Simulation output text."),
            "artifacts": ToolParam(type="array", description="Downloaded result file paths."),
        },
        tags=["gb10", "gpu", "simulation", "offload", "cluster"],
    )

    async def run(self, inputs: dict[str, Any], context: ToolContext) -> ToolResult:
        code = inputs.get("code", "")
        mode = inputs.get("mode", "octave")
        work_items_raw = inputs.get("work_items")
        multi_node = inputs.get("multi_node")
        function_name = inputs.get("function_name", "")
        entry_types = inputs.get("entry_types", "")
        source_dir = inputs.get("source_dir", "")

        work_items: list[dict] | None = None
        if work_items_raw:
            if isinstance(work_items_raw, str):
                try:
                    work_items = json.loads(work_items_raw)
                except json.JSONDecodeError:
                    return ToolResult(success=False, error="work_items must be valid JSON array")
            elif isinstance(work_items_raw, list):
                work_items = work_items_raw

        if not code and mode != "gpu_coder":
            return ToolResult(success=False, error="'code' is required for octave and binary modes")

        if mode == "gpu_coder" and not function_name:
            return ToolResult(success=False, error="'function_name' required for gpu_coder mode")

        try:
            from src.matclaw.skills.gb10_offload.logic import run as gb10_run

            result = gb10_run(
                matlab_bridge=None,
                code=code,
                mode=mode,
                function_name=function_name,
                entry_types=entry_types,
                source_dir=source_dir,
                work_items=work_items,
                multi_node=multi_node,
            )

            if result.success:
                artifacts = (result.data or {}).get("artifacts", [])
                return ToolResult(
                    success=True,
                    output=result.message,
                    artifacts=artifacts,
                )
            else:
                return ToolResult(success=False, error=result.error or "GB10 offload failed")

        except Exception as exc:
            logger.exception("GB10 offload tool error: %s", exc)
            return ToolResult(success=False, error=str(exc))


tool = GB10OffloadTool()
