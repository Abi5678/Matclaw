"""
GB10 SSH client — execute commands and transfer files on a multi-node DGX Spark cluster.

Supports:
- Single-node execution on the head node
- Split-mode: divide work items across N nodes, run in parallel, gather results
- MPI-mode: launch an MPI job across nodes using their direct-link interconnect IPs

All SSH operations go through the system ``ssh`` / ``scp`` commands so they
honour the user's ``~/.ssh/config`` aliases (e.g. ``gn100-93da``).
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from src.matclaw.config.base_config import GB10NodeSettings, GB10Settings

logger = logging.getLogger(__name__)


@dataclass
class RemoteJobResult:
    """Result of a single remote command execution."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    return_code: int = -1
    node_alias: str = ""
    artifacts: list[str] = field(default_factory=list)


@dataclass
class ClusterJobResult:
    """Aggregated result across all nodes."""
    success: bool
    node_results: list[RemoteJobResult] = field(default_factory=list)
    merged_stdout: str = ""
    artifacts: list[str] = field(default_factory=list)


def _ssh_base_args(node: GB10NodeSettings, settings: GB10Settings) -> list[str]:
    """Build the common ssh/scp argument prefix for a node."""
    args: list[str] = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        "-p", str(node.ssh_port),
    ]
    if settings.ssh_key_path:
        args.extend(["-i", settings.ssh_key_path])
    return args


def _ssh_dest(node: GB10NodeSettings) -> str:
    return f"{node.ssh_user}@{node.ssh_alias}"


class GB10Client:
    """
    Manages SSH connections to a GB10 cluster and dispatches jobs.

    The client never holds persistent connections; each operation is a fresh
    ssh/scp subprocess so there is no state to clean up.
    """

    def __init__(self, settings: GB10Settings) -> None:
        self.settings = settings
        if not settings.nodes:
            raise ValueError("GB10 cluster has no nodes configured")

    @property
    def head_node(self) -> GB10NodeSettings:
        return self.settings.nodes[0]

    @property
    def node_count(self) -> int:
        return len(self.settings.nodes)

    def _job_dir(self, job_id: str) -> str:
        return f"{self.settings.remote_work_dir}/{job_id}"

    # -- low-level SSH helpers ------------------------------------------------

    def run_ssh(
        self,
        node: GB10NodeSettings,
        command: str,
        *,
        timeout: float | None = None,
    ) -> RemoteJobResult:
        """Run a single command on *node* over SSH."""
        timeout = timeout or self.settings.timeout_seconds
        ssh_args = ["ssh"] + _ssh_base_args(node, self.settings) + [
            _ssh_dest(node),
            command,
        ]
        logger.info("SSH → %s: %s", node.ssh_alias, command[:120])
        retries = self.settings.max_retries
        last_exc: Exception | None = None
        for attempt in range(1 + retries):
            try:
                proc = subprocess.run(
                    ssh_args,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                return RemoteJobResult(
                    success=proc.returncode == 0,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    return_code=proc.returncode,
                    node_alias=node.ssh_alias,
                )
            except subprocess.TimeoutExpired:
                return RemoteJobResult(
                    success=False,
                    stderr=f"SSH command timed out after {timeout}s",
                    node_alias=node.ssh_alias,
                )
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    logger.warning(
                        "SSH attempt %d/%d to %s failed: %s — retrying",
                        attempt + 1, 1 + retries, node.ssh_alias, exc,
                    )
        return RemoteJobResult(
            success=False,
            stderr=f"SSH failed after {1 + retries} attempts: {last_exc}",
            node_alias=node.ssh_alias,
        )

    def upload(
        self,
        node: GB10NodeSettings,
        local_path: str | Path,
        remote_path: str,
    ) -> RemoteJobResult:
        """SCP a local file or directory to *node*."""
        local = str(local_path)
        scp_args = ["scp"] + _ssh_base_args(node, self.settings)
        if Path(local).is_dir():
            scp_args.append("-r")
        scp_args += [local, f"{_ssh_dest(node)}:{remote_path}"]
        logger.info("SCP upload → %s:%s", node.ssh_alias, remote_path)
        try:
            proc = subprocess.run(scp_args, capture_output=True, text=True, timeout=120)
            return RemoteJobResult(
                success=proc.returncode == 0,
                stdout=proc.stdout,
                stderr=proc.stderr,
                return_code=proc.returncode,
                node_alias=node.ssh_alias,
            )
        except Exception as exc:
            return RemoteJobResult(success=False, stderr=str(exc), node_alias=node.ssh_alias)

    def download(
        self,
        node: GB10NodeSettings,
        remote_path: str,
        local_path: str | Path,
    ) -> RemoteJobResult:
        """SCP a remote file from *node* to a local path."""
        scp_args = ["scp"] + _ssh_base_args(node, self.settings) + [
            f"{_ssh_dest(node)}:{remote_path}",
            str(local_path),
        ]
        logger.info("SCP download ← %s:%s", node.ssh_alias, remote_path)
        try:
            proc = subprocess.run(scp_args, capture_output=True, text=True, timeout=120)
            return RemoteJobResult(
                success=proc.returncode == 0,
                stdout=proc.stdout,
                stderr=proc.stderr,
                return_code=proc.returncode,
                node_alias=node.ssh_alias,
            )
        except Exception as exc:
            return RemoteJobResult(success=False, stderr=str(exc), node_alias=node.ssh_alias)

    # -- high-level job interface ---------------------------------------------

    def probe_node(self, node: GB10NodeSettings) -> RemoteJobResult:
        """Quick connectivity + capability check."""
        cmd = (
            "echo OK && "
            f"test -x $(command -v {shlex.quote(self.settings.nvcc_path)} 2>/dev/null || echo /nonexistent) "
            "&& echo CUDA_OK || echo CUDA_MISSING"
        )
        return self.run_ssh(node, cmd, timeout=15)

    def probe_cluster(self) -> dict[str, RemoteJobResult]:
        """Probe every node in parallel; returns {alias: result}."""
        results: dict[str, RemoteJobResult] = {}
        with ThreadPoolExecutor(max_workers=len(self.settings.nodes)) as pool:
            futs = {pool.submit(self.probe_node, n): n for n in self.settings.nodes}
            for fut in as_completed(futs):
                node = futs[fut]
                try:
                    results[node.ssh_alias] = fut.result()
                except Exception as exc:
                    results[node.ssh_alias] = RemoteJobResult(
                        success=False, stderr=str(exc), node_alias=node.ssh_alias,
                    )
        return results

    def run_on_head(
        self,
        command: str,
        *,
        upload_files: list[tuple[str | Path, str]] | None = None,
        download_files: list[tuple[str, str | Path]] | None = None,
        job_id: str | None = None,
        timeout: float | None = None,
    ) -> RemoteJobResult:
        """Execute *command* on the head node with optional file transfers."""
        jid = job_id or uuid.uuid4().hex[:12]
        node = self.head_node
        job_dir = self._job_dir(jid)
        self.run_ssh(node, f"mkdir -p {shlex.quote(job_dir)}")

        if upload_files:
            for local, remote_name in upload_files:
                self.upload(node, local, f"{job_dir}/{remote_name}")

        result = self.run_ssh(
            node,
            f"cd {shlex.quote(job_dir)} && {command}",
            timeout=timeout,
        )
        result.artifacts = []

        if download_files and result.success:
            for remote_name, local in download_files:
                dl = self.download(node, f"{job_dir}/{remote_name}", local)
                if dl.success:
                    result.artifacts.append(str(local))

        return result

    def run_split(
        self,
        *,
        work_items: Sequence[dict[str, Any]],
        command_template: str,
        upload_files: list[tuple[str | Path, str]] | None = None,
        download_pattern: str = "result_*.mat",
        local_results_dir: str | Path = "/tmp/matclaw_results",
        timeout: float | None = None,
    ) -> ClusterJobResult:
        """
        Split *work_items* across cluster nodes and run in parallel.

        ``command_template`` is a shell command with ``{chunk_file}`` and
        ``{node_index}`` placeholders.  Each node receives a JSON chunk file
        listing its share of *work_items*.

        Returns aggregated results.
        """
        import json

        nodes = self.settings.nodes
        n = len(nodes)
        chunks: list[list[dict]] = [[] for _ in range(n)]
        for i, item in enumerate(work_items):
            chunks[i % n].append(item)

        job_id = uuid.uuid4().hex[:12]
        results_dir = Path(local_results_dir)
        results_dir.mkdir(parents=True, exist_ok=True)
        node_results: list[RemoteJobResult] = []

        def _run_node(idx: int, node: GB10NodeSettings, chunk: list[dict]) -> RemoteJobResult:
            jdir = self._job_dir(job_id)
            self.run_ssh(node, f"mkdir -p {shlex.quote(jdir)}")

            if upload_files:
                for local, remote_name in upload_files:
                    self.upload(node, local, f"{jdir}/{remote_name}")

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, prefix=f"chunk_{idx}_",
            ) as f:
                json.dump(chunk, f)
                chunk_path = f.name
            try:
                self.upload(node, chunk_path, f"{jdir}/chunk.json")
            finally:
                os.unlink(chunk_path)

            cmd = command_template.format(chunk_file="chunk.json", node_index=idx)
            result = self.run_ssh(node, f"cd {shlex.quote(jdir)} && {cmd}", timeout=timeout)

            if result.success:
                node_dir = results_dir / f"node_{idx}"
                node_dir.mkdir(exist_ok=True)
                dl = self.download(node, f"{jdir}/{download_pattern}", str(node_dir) + "/")
                if dl.success:
                    result.artifacts = [str(p) for p in node_dir.iterdir()]

            return result

        with ThreadPoolExecutor(max_workers=n) as pool:
            futs = {
                pool.submit(_run_node, i, node, chunks[i]): i
                for i, node in enumerate(nodes) if chunks[i]
            }
            for fut in as_completed(futs):
                try:
                    node_results.append(fut.result())
                except Exception as exc:
                    idx = futs[fut]
                    node_results.append(RemoteJobResult(
                        success=False,
                        stderr=str(exc),
                        node_alias=nodes[idx].ssh_alias,
                    ))

        all_ok = all(r.success for r in node_results)
        merged = "\n".join(r.stdout for r in node_results if r.stdout)
        all_artifacts = [a for r in node_results for a in r.artifacts]

        return ClusterJobResult(
            success=all_ok,
            node_results=node_results,
            merged_stdout=merged,
            artifacts=all_artifacts,
        )

    def build_mpi_hostfile(self) -> str:
        """
        Generate an MPI hostfile using interconnect IPs for NCCL/MPI traffic.
        Falls back to SSH aliases when no interconnect IPs are configured.
        """
        lines: list[str] = []
        for node in self.settings.nodes:
            host = node.interconnect_ip or node.ssh_alias
            lines.append(f"{host} slots=1")
        return "\n".join(lines)

    def run_mpi(
        self,
        command: str,
        *,
        nprocs: int | None = None,
        upload_files: list[tuple[str | Path, str]] | None = None,
        job_id: str | None = None,
        timeout: float | None = None,
        extra_mpi_args: str = "",
    ) -> RemoteJobResult:
        """
        Launch an MPI job from the head node across the cluster using
        the direct-link interconnect IPs for inter-node traffic.
        """
        jid = job_id or uuid.uuid4().hex[:12]
        node = self.head_node
        jdir = self._job_dir(jid)

        self.run_ssh(node, f"mkdir -p {shlex.quote(jdir)}")
        if upload_files:
            for local, remote_name in upload_files:
                self.upload(node, local, f"{jdir}/{remote_name}")

        hostfile_content = self.build_mpi_hostfile()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".hosts", delete=False) as f:
            f.write(hostfile_content)
            hf_local = f.name
        try:
            self.upload(node, hf_local, f"{jdir}/hostfile")
        finally:
            os.unlink(hf_local)

        np = nprocs or len(self.settings.nodes)
        mpi_cmd = (
            f"cd {shlex.quote(jdir)} && "
            f"mpirun --hostfile hostfile -np {np} {extra_mpi_args} {command}"
        )
        return self.run_ssh(node, mpi_cmd, timeout=timeout)

    def cleanup_job(self, node: GB10NodeSettings, job_id: str) -> None:
        """Remove a job directory on a node."""
        jdir = self._job_dir(job_id)
        self.run_ssh(node, f"rm -rf {shlex.quote(jdir)}", timeout=30)
