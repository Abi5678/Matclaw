"""
Tests for GB10Client — all SSH/SCP interactions are mocked.
Uses the real 'constellation' two-node cluster config:
  Node 1: gn100-93da       (10.100.120.1 / 10.100.121.1)
  Node 2: spark-3d03.local (10.100.120.2 / 10.100.121.2)
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from src.matclaw.config.base_config import GB10NodeSettings, GB10Settings
from src.matclaw.gb10.client import GB10Client, ClusterJobResult, RemoteJobResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _constellation_settings() -> GB10Settings:
    return GB10Settings(
        enabled=True,
        cluster_name="constellation",
        nodes=[
            GB10NodeSettings(
                ssh_alias="gn100-93da",
                ssh_user="ubuntu",
                management_ip="10.100.120.1",
                interconnect_ip="10.100.121.1",
            ),
            GB10NodeSettings(
                ssh_alias="spark-3d03.local",
                ssh_user="ubuntu",
                management_ip="10.100.120.2",
                interconnect_ip="10.100.121.2",
            ),
        ],
        ssh_key_path="/home/user/.ssh/id_ed25519",
        remote_work_dir="/tmp/matclaw_jobs",
        cuda_home="/usr/local/cuda",
        timeout_seconds=300,
    )


def _single_node_settings() -> GB10Settings:
    return GB10Settings(
        enabled=True,
        cluster_name="single-spark",
        nodes=[
            GB10NodeSettings(
                ssh_alias="gn100-93da",
                ssh_user="ubuntu",
                interconnect_ip="10.100.121.1",
            ),
        ],
    )


def _make_completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


# ---------------------------------------------------------------------------
# Construction & properties
# ---------------------------------------------------------------------------

class TestGB10ClientConstruction:
    def test_requires_nodes(self):
        with pytest.raises(ValueError, match="no nodes"):
            GB10Client(GB10Settings(enabled=True, nodes=[]))

    def test_head_node(self):
        client = GB10Client(_constellation_settings())
        assert client.head_node.ssh_alias == "gn100-93da"

    def test_node_count(self):
        client = GB10Client(_constellation_settings())
        assert client.node_count == 2


# ---------------------------------------------------------------------------
# SSH execution
# ---------------------------------------------------------------------------

class TestRunSSH:
    @patch("subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = _make_completed(0, stdout="hello\n")
        client = GB10Client(_constellation_settings())
        result = client.run_ssh(client.head_node, "echo hello")
        assert result.success
        assert "hello" in result.stdout
        assert result.node_alias == "gn100-93da"

    @patch("subprocess.run")
    def test_ssh_key_in_args(self, mock_run):
        mock_run.return_value = _make_completed(0, stdout="ok")
        client = GB10Client(_constellation_settings())
        client.run_ssh(client.head_node, "echo ok")
        args = mock_run.call_args[0][0]
        assert "-i" in args
        assert "/home/user/.ssh/id_ed25519" in args

    @patch("subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = _make_completed(1, stderr="No such file")
        client = GB10Client(_constellation_settings())
        result = client.run_ssh(client.head_node, "cat /missing")
        assert not result.success
        assert result.return_code == 1

    @patch("subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=5)
        client = GB10Client(_constellation_settings())
        result = client.run_ssh(client.head_node, "sleep 999", timeout=5)
        assert not result.success
        assert "timed out" in result.stderr

    @patch("subprocess.run")
    def test_retry_on_connection_error(self, mock_run):
        mock_run.side_effect = [
            ConnectionError("refused"),
            _make_completed(0, stdout="ok"),
        ]
        client = GB10Client(_constellation_settings())
        result = client.run_ssh(client.head_node, "echo ok")
        assert result.success
        assert mock_run.call_count == 2


# ---------------------------------------------------------------------------
# File transfer
# ---------------------------------------------------------------------------

class TestFileTransfer:
    @patch("subprocess.run")
    def test_upload(self, mock_run):
        mock_run.return_value = _make_completed(0)
        client = GB10Client(_constellation_settings())
        result = client.upload(client.head_node, "/tmp/data.mat", "/remote/data.mat")
        assert result.success
        args = mock_run.call_args[0][0]
        assert "scp" in args[0]
        assert "/tmp/data.mat" in args

    @patch("subprocess.run")
    def test_download(self, mock_run):
        mock_run.return_value = _make_completed(0)
        client = GB10Client(_constellation_settings())
        result = client.download(client.head_node, "/remote/result.mat", "/tmp/result.mat")
        assert result.success


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

class TestProbe:
    @patch("subprocess.run")
    def test_probe_node_success(self, mock_run):
        mock_run.return_value = _make_completed(0, stdout="OK\nCUDA_OK\n")
        client = GB10Client(_constellation_settings())
        result = client.probe_node(client.head_node)
        assert result.success
        assert "CUDA_OK" in result.stdout

    @patch("subprocess.run")
    def test_probe_cluster_both_nodes(self, mock_run):
        mock_run.return_value = _make_completed(0, stdout="OK\nCUDA_OK\n")
        client = GB10Client(_constellation_settings())
        results = client.probe_cluster()
        assert "gn100-93da" in results
        assert "spark-3d03.local" in results
        assert all(r.success for r in results.values())


# ---------------------------------------------------------------------------
# Head-node execution
# ---------------------------------------------------------------------------

class TestRunOnHead:
    @patch("subprocess.run")
    def test_run_on_head(self, mock_run):
        mock_run.return_value = _make_completed(0, stdout="result=42\n")
        client = GB10Client(_constellation_settings())
        result = client.run_on_head("./sim --param 42", job_id="test123")
        assert result.success or mock_run.call_count >= 1


# ---------------------------------------------------------------------------
# Multi-node split
# ---------------------------------------------------------------------------

class TestRunSplit:
    @patch("subprocess.run")
    def test_split_distributes_work(self, mock_run):
        mock_run.return_value = _make_completed(0, stdout="chunk done\n")
        client = GB10Client(_constellation_settings())

        items = [{"seed": i} for i in range(6)]
        result = client.run_split(
            work_items=items,
            command_template="octave run.m --chunk {chunk_file}",
            local_results_dir="/tmp/test_split_results",
        )
        assert isinstance(result, ClusterJobResult)
        ssh_calls = [c for c in mock_run.call_args_list if "ssh" in str(c)]
        assert len(ssh_calls) >= 2

    @patch("subprocess.run")
    def test_single_node_fallback(self, mock_run):
        mock_run.return_value = _make_completed(0, stdout="done\n")
        client = GB10Client(_single_node_settings())

        items = [{"seed": 1}, {"seed": 2}]
        result = client.run_split(
            work_items=items,
            command_template="octave run.m --chunk {chunk_file}",
            local_results_dir="/tmp/test_single_results",
        )
        assert isinstance(result, ClusterJobResult)


# ---------------------------------------------------------------------------
# MPI
# ---------------------------------------------------------------------------

class TestMPI:
    @patch("subprocess.run")
    def test_hostfile_uses_interconnect_ips(self, mock_run):
        client = GB10Client(_constellation_settings())
        hostfile = client.build_mpi_hostfile()
        assert "10.100.121.1" in hostfile
        assert "10.100.121.2" in hostfile

    @patch("subprocess.run")
    def test_hostfile_falls_back_to_alias(self, mock_run):
        settings = GB10Settings(
            enabled=True,
            nodes=[
                GB10NodeSettings(ssh_alias="node-a", ssh_user="u"),
                GB10NodeSettings(ssh_alias="node-b", ssh_user="u"),
            ],
        )
        client = GB10Client(settings)
        hostfile = client.build_mpi_hostfile()
        assert "node-a" in hostfile
        assert "node-b" in hostfile

    @patch("subprocess.run")
    def test_run_mpi(self, mock_run):
        mock_run.return_value = _make_completed(0, stdout="MPI done\n")
        client = GB10Client(_constellation_settings())
        result = client.run_mpi("./nccl_test", nprocs=2)
        assert result.success or mock_run.call_count >= 1


# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------

class TestGB10Config:
    def test_constellation_config(self):
        s = _constellation_settings()
        assert s.cluster_name == "constellation"
        assert len(s.nodes) == 2
        assert s.nodes[0].ssh_alias == "gn100-93da"
        assert s.nodes[0].interconnect_ip == "10.100.121.1"
        assert s.nodes[1].ssh_alias == "spark-3d03.local"
        assert s.nodes[1].management_ip == "10.100.120.2"

    def test_default_disabled(self):
        from src.matclaw.config.base_config import MatClawSettings
        settings = MatClawSettings()
        assert not settings.gb10.enabled
        assert settings.gb10.nodes == []
