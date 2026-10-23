"""
GNU Octave fallback — run plain .m scripts on the GB10 CPU when CUDA is
not required or GPU Coder is unavailable.

Octave runs natively on aarch64 Linux and handles most MATLAB-compatible
.m scripts (no Simulink, no toolbox-specific functions).
"""
from __future__ import annotations

import logging
import shlex
import tempfile
import uuid
from pathlib import Path
from typing import Any

from src.matclaw.config.base_config import GB10Settings

logger = logging.getLogger(__name__)


def run_octave_on_node(
    gb10_client: Any,
    node: Any,
    code: str,
    *,
    upload_files: list[tuple[str | Path, str]] | None = None,
    download_files: list[tuple[str, str | Path]] | None = None,
    timeout: float | None = None,
) -> tuple[bool, str, list[str]]:
    """
    Run a MATLAB-compatible script via Octave on a single GB10 node.

    Args:
        gb10_client: A GB10Client instance.
        node: The GB10NodeSettings for the target node.
        code: MATLAB/Octave source code to execute.
        upload_files: (local_path, remote_name) pairs to upload before running.
        download_files: (remote_name, local_path) pairs to download after running.
        timeout: Execution timeout in seconds.

    Returns:
        (success, output, local_artifact_paths)
    """
    settings: GB10Settings = gb10_client.settings
    job_id = uuid.uuid4().hex[:12]
    jdir = f"{settings.remote_work_dir}/{job_id}"

    gb10_client.run_ssh(node, f"mkdir -p {shlex.quote(jdir)}")

    if upload_files:
        for local, remote_name in upload_files:
            gb10_client.upload(node, local, f"{jdir}/{remote_name}")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".m", delete=False, prefix="matclaw_octave_",
    ) as f:
        f.write(code)
        local_script = f.name

    try:
        gb10_client.upload(node, local_script, f"{jdir}/run_script.m")
    finally:
        Path(local_script).unlink(missing_ok=True)

    octave = shlex.quote(settings.octave_path)
    cmd = f"cd {shlex.quote(jdir)} && {octave} --no-gui --eval \"run('run_script.m')\" 2>&1"
    result = gb10_client.run_ssh(node, cmd, timeout=timeout)

    artifacts: list[str] = []
    if result.success and download_files:
        for remote_name, local_path in download_files:
            dl = gb10_client.download(node, f"{jdir}/{remote_name}", local_path)
            if dl.success:
                artifacts.append(str(local_path))

    return result.success, result.stdout or result.stderr, artifacts


def run_octave_split(
    gb10_client: Any,
    code_template: str,
    work_items: list[dict[str, Any]],
    *,
    timeout: float | None = None,
) -> tuple[bool, str, list[str]]:
    """
    Split work across all cluster nodes, each running the same Octave script
    with a different parameter set.

    ``code_template`` may contain ``{params_json}`` which is replaced per-chunk
    with a JSON string of the assigned work items.
    """
    import json

    nodes = gb10_client.settings.nodes
    n = len(nodes)
    chunks: list[list[dict]] = [[] for _ in range(n)]
    for i, item in enumerate(work_items):
        chunks[i % n].append(item)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    all_ok = True
    all_output: list[str] = []
    all_artifacts: list[str] = []

    def _run_chunk(idx: int) -> tuple[bool, str, list[str]]:
        node = nodes[idx]
        params_json = json.dumps(chunks[idx]).replace("'", "\\'")
        code = code_template.replace("{params_json}", params_json)
        code = code.replace("{node_index}", str(idx))
        return run_octave_on_node(gb10_client, node, code, timeout=timeout)

    with ThreadPoolExecutor(max_workers=n) as pool:
        futs = {pool.submit(_run_chunk, i): i for i in range(n) if chunks[i]}
        for fut in as_completed(futs):
            try:
                ok, out, arts = fut.result()
                if not ok:
                    all_ok = False
                all_output.append(out)
                all_artifacts.extend(arts)
            except Exception as exc:
                all_ok = False
                all_output.append(f"Node {futs[fut]} error: {exc}")

    return all_ok, "\n".join(all_output), all_artifacts
