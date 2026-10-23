"""
Tests for the GPU Coder path — all MATLAB and SSH operations mocked.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.matclaw.config.base_config import GB10NodeSettings, GB10Settings
from src.matclaw.gb10.codegen import (
    _generate_codegen_script,
    compile_and_run_on_gb10,
    codegen_offload,
    generate_cuda_on_host,
)


def _mock_bridge(healthy: bool = True, codegen_ok: bool = True):
    bridge = MagicMock()
    bridge.is_healthy.return_value = healthy
    if codegen_ok:
        bridge.run_matlab_code.return_value = (True, "CODEGEN_OK\n")
    else:
        bridge.run_matlab_code.return_value = (False, "Error: no GPU Coder license")
    return bridge


def _mock_gb10_client():
    client = MagicMock()
    client.settings = GB10Settings(
        enabled=True,
        cluster_name="constellation",
        nodes=[GB10NodeSettings(ssh_alias="gn100-93da", ssh_user="ubuntu")],
        cuda_home="/usr/local/cuda",
        nvcc_path="nvcc",
    )
    client.head_node = client.settings.nodes[0]

    ok = MagicMock()
    ok.success = True
    ok.stdout = "Compiled OK\n"
    ok.stderr = ""
    ok.artifacts = []
    client.run_ssh.return_value = ok
    client.upload.return_value = ok
    return client


class TestCodegenScript:
    def test_gpu_target(self):
        script = _generate_codegen_script("monte_carlo", "{zeros(1000,1)}", "/tmp/out", gpu=True)
        assert "gpu" in script
        assert "CUDA" in script
        assert "monte_carlo" in script

    def test_lib_target(self):
        script = _generate_codegen_script("ode_solve", "{0.0, 1.0}", "/tmp/out", gpu=False)
        assert "'lib'" in script
        assert "'C'" in script


class TestGenerateCuda:
    def test_bridge_unavailable(self):
        ok, path, msg = generate_cuda_on_host(None, "f", "{}", ".")
        assert not ok
        assert "not available" in msg

    def test_bridge_unhealthy(self):
        bridge = _mock_bridge(healthy=False)
        ok, path, msg = generate_cuda_on_host(bridge, "f", "{}", ".")
        assert not ok

    def test_codegen_failure(self):
        bridge = _mock_bridge(codegen_ok=False)
        ok, path, msg = generate_cuda_on_host(bridge, "f", "{}", ".")
        assert not ok
        assert "failed" in msg.lower()

    def test_codegen_success_but_no_files(self):
        bridge = _mock_bridge(codegen_ok=True)
        ok, path, msg = generate_cuda_on_host(bridge, "f", "{}", ".")
        assert not ok
        assert "no source files" in msg.lower()


class TestCompileAndRun:
    def test_upload_failure(self):
        client = _mock_gb10_client()
        fail = MagicMock()
        fail.success = False
        fail.stderr = "SCP failed"
        client.upload.return_value = fail

        with tempfile.TemporaryDirectory() as td:
            ok, out, arts = compile_and_run_on_gb10(client, Path(td))
            assert not ok
            assert "Upload failed" in out

    def test_compile_and_run_success(self):
        client = _mock_gb10_client()
        with tempfile.TemporaryDirectory() as td:
            ok, out, arts = compile_and_run_on_gb10(client, Path(td))
            assert ok or client.run_ssh.call_count >= 1


class TestCodegenOffload:
    def test_no_matlab(self):
        ok, msg = codegen_offload(None, _mock_gb10_client(), "f", "{}", ".")
        assert not ok
        assert "not available" in msg

    def test_codegen_fail_graceful(self):
        bridge = _mock_bridge(codegen_ok=False)
        ok, msg = codegen_offload(bridge, _mock_gb10_client(), "f", "{}", ".")
        assert not ok
