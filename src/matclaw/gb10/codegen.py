"""
GPU Coder path — generate CUDA/C from MATLAB functions on the host, ship to
the GB10, compile with nvcc, run, and return results.

Prerequisites on the host (Mac / x86):
  - MATLAB with GPU Coder or MATLAB Coder installed
  - The target MATLAB function annotated with coder-compatible types

Flow:
  1. User provides a MATLAB function and entry-point types
  2. MATLAB Coder on the host generates CUDA (or C) source
  3. Sources are uploaded to the GB10 via SSH/SCP
  4. nvcc compiles on the GB10 (ARM64 CUDA 13 target)
  5. Binary is executed; stdout/files are returned
"""
from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any

from src.matclaw.config.base_config import GB10Settings

logger = logging.getLogger(__name__)


def _generate_codegen_script(
    function_name: str,
    entry_types: str,
    output_dir: str,
    gpu: bool = True,
) -> str:
    """Build a MATLAB script that invokes codegen / gpucoder for *function_name*."""
    target = "gpu" if gpu else "lib"
    return (
        f"cfg = coder.config('{target}');\n"
        f"cfg.TargetLang = '{'CUDA' if gpu else 'C'}';\n"
        f"cfg.GenerateReport = false;\n"
        f"cfg.GenCodeOnly = true;\n"
        f"codegen -config cfg -d '{output_dir}' {function_name} -args {entry_types};\n"
        f"fprintf('CODEGEN_OK\\n');\n"
    )


def generate_cuda_on_host(
    matlab_bridge: Any,
    function_name: str,
    entry_types: str,
    source_dir: str | Path,
    *,
    gpu: bool = True,
) -> tuple[bool, Path | None, str]:
    """
    Run MATLAB Coder / GPU Coder on the host to produce CUDA sources.

    Args:
        matlab_bridge: A live MatlabBridge instance (host-side MATLAB).
        function_name: Name of the MATLAB function to compile.
        entry_types: MATLAB-syntax type spec (e.g. ``{zeros(1000,1), 0.01}``).
        source_dir: Directory containing the .m source file.
        gpu: True for GPU Coder (CUDA), False for MATLAB Coder (C).

    Returns:
        (success, codegen_output_dir | None, message)
    """
    if matlab_bridge is None or not matlab_bridge.is_healthy():
        return False, None, "MATLAB bridge not available for code generation"

    output_dir = Path(tempfile.mkdtemp(prefix="matclaw_codegen_"))
    script = _generate_codegen_script(function_name, entry_types, str(output_dir), gpu=gpu)

    matlab_bridge.addpath(str(source_dir))
    ok, output = matlab_bridge.run_matlab_code(script)
    if not ok or "CODEGEN_OK" not in output:
        return False, None, f"Code generation failed: {output}"

    cu_files = list(output_dir.rglob("*.cu")) + list(output_dir.rglob("*.c"))
    if not cu_files:
        return False, None, "Code generation produced no source files"

    logger.info("GPU Coder generated %d source files in %s", len(cu_files), output_dir)
    return True, output_dir, f"Generated {len(cu_files)} source files"


def compile_and_run_on_gb10(
    gb10_client: Any,
    codegen_dir: Path,
    binary_name: str = "sim_kernel",
    run_args: str = "",
    *,
    timeout: float | None = None,
) -> tuple[bool, str, list[str]]:
    """
    Upload generated sources to the GB10 head node, compile with nvcc, and run.

    Returns:
        (success, stdout_output, artifact_local_paths)
    """
    job_id = uuid.uuid4().hex[:12]
    node = gb10_client.head_node
    settings: GB10Settings = gb10_client.settings
    jdir = f"{settings.remote_work_dir}/{job_id}"

    gb10_client.run_ssh(node, f"mkdir -p {jdir}")
    up = gb10_client.upload(node, codegen_dir, f"{jdir}/src")
    if not up.success:
        return False, f"Upload failed: {up.stderr}", []

    nvcc = settings.nvcc_path
    cuda_home = settings.cuda_home
    compile_cmd = (
        f"cd {jdir}/src && "
        f"export PATH={cuda_home}/bin:$PATH && "
        f"{nvcc} -O2 -arch=sm_100 -o {jdir}/{binary_name} *.cu -lm 2>&1"
    )
    comp = gb10_client.run_ssh(node, compile_cmd, timeout=120)
    if not comp.success:
        return False, f"Compilation failed:\n{comp.stderr}\n{comp.stdout}", []

    run_cmd = f"{jdir}/{binary_name} {run_args}"
    result = gb10_client.run_ssh(node, run_cmd, timeout=timeout)
    artifacts: list[str] = []

    return result.success, result.stdout or result.stderr, artifacts


def codegen_offload(
    matlab_bridge: Any,
    gb10_client: Any,
    function_name: str,
    entry_types: str,
    source_dir: str | Path,
    *,
    gpu: bool = True,
    run_args: str = "",
    timeout: float | None = None,
) -> tuple[bool, str]:
    """
    End-to-end: generate code on host → compile on GB10 → run → return output.

    Falls back gracefully:
      - If GPU Coder is unavailable, returns an error message.
      - If compilation fails, returns the compiler output for debugging.
    """
    ok, codegen_dir, msg = generate_cuda_on_host(
        matlab_bridge, function_name, entry_types, source_dir, gpu=gpu,
    )
    if not ok:
        return False, msg

    assert codegen_dir is not None
    ok, output, _ = compile_and_run_on_gb10(
        gb10_client, codegen_dir, run_args=run_args, timeout=timeout,
    )
    return ok, output
