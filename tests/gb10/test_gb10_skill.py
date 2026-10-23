"""
Tests for the gb10_offload skill — all remote execution is mocked.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.matclaw.config.base_config import GB10NodeSettings, GB10Settings
from src.matclaw.skills.gb10_offload.logic import execute, plan, research, run


def _mock_settings() -> GB10Settings:
    return GB10Settings(
        enabled=True,
        cluster_name="constellation",
        nodes=[
            GB10NodeSettings(
                ssh_alias="gn100-93da",
                ssh_user="ubuntu",
                interconnect_ip="10.100.121.1",
                management_ip="10.100.120.1",
            ),
            GB10NodeSettings(
                ssh_alias="spark-3d03.local",
                ssh_user="ubuntu",
                interconnect_ip="10.100.121.2",
                management_ip="10.100.120.2",
            ),
        ],
    )


def _mock_client():
    """Create a mock GB10Client with successful probe results."""
    client = MagicMock()
    client.settings = _mock_settings()
    client.head_node = client.settings.nodes[0]
    client.node_count = 2

    ok_result = MagicMock()
    ok_result.success = True
    ok_result.stdout = "OK\nCUDA_OK\n"
    ok_result.stderr = ""
    ok_result.node_alias = "gn100-93da"

    ok_result_2 = MagicMock()
    ok_result_2.success = True
    ok_result_2.stdout = "OK\nCUDA_OK\n"
    ok_result_2.stderr = ""
    ok_result_2.node_alias = "spark-3d03.local"

    client.probe_node.return_value = ok_result
    client.probe_cluster.return_value = {
        "gn100-93da": ok_result,
        "spark-3d03.local": ok_result_2,
    }

    octave_result = MagicMock()
    octave_result.success = True
    octave_result.stdout = "OCTAVE_OK\n"
    client.run_ssh.return_value = octave_result

    return client


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------

class TestResearch:
    def test_disabled_backend(self):
        r = research(None, gb10_settings=GB10Settings(enabled=False))
        assert r["error"] is not None
        assert not r["cluster_reachable"]

    @patch("src.matclaw.skills.gb10_offload.logic._build_client")
    def test_cluster_reachable(self, mock_build):
        mock_build.return_value = _mock_client()
        r = research(None, gb10_settings=_mock_settings())
        assert r["cluster_reachable"]
        assert r["node_count"] == 2
        assert "gn100-93da" in r["node_status"]
        assert "spark-3d03.local" in r["node_status"]

    @patch("src.matclaw.skills.gb10_offload.logic._build_client")
    def test_cuda_detection(self, mock_build):
        mock_build.return_value = _mock_client()
        r = research(None, gb10_settings=_mock_settings())
        assert r["cuda_available"]


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

class TestPlan:
    def test_auto_selects_octave(self):
        research_data = {
            "cluster_reachable": True,
            "cuda_available": True,
            "octave_available": True,
            "gpu_coder_available": False,
            "node_count": 2,
            "error": None,
        }
        p = plan(research_data)
        assert p["mode"] == "octave"

    def test_multi_node_with_work_items(self):
        research_data = {
            "cluster_reachable": True,
            "cuda_available": True,
            "octave_available": True,
            "gpu_coder_available": False,
            "node_count": 2,
            "error": None,
        }
        p = plan(research_data, work_items=[{"seed": 1}, {"seed": 2}])
        assert p["multi_node"]
        assert p["strategy"] == "split"

    def test_single_node_fallback(self):
        research_data = {
            "cluster_reachable": True,
            "cuda_available": True,
            "octave_available": True,
            "gpu_coder_available": False,
            "node_count": 1,
            "error": None,
        }
        p = plan(research_data, work_items=[{"seed": 1}], multi_node=True)
        assert p["strategy"] == "single"

    def test_gpu_coder_fallback(self):
        research_data = {
            "cluster_reachable": True,
            "cuda_available": True,
            "octave_available": True,
            "gpu_coder_available": False,
            "node_count": 2,
            "error": None,
        }
        p = plan(research_data, mode="gpu_coder", function_name="my_sim")
        assert p["mode"] in ("octave", "binary")
        assert len(p["warnings"]) > 0

    def test_unreachable_cluster(self):
        research_data = {
            "cluster_reachable": False,
            "node_count": 0,
            "error": None,
        }
        p = plan(research_data)
        assert "No GB10 nodes reachable" in p["warnings"]


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

class TestExecute:
    @patch("src.matclaw.skills.gb10_offload.logic._build_client")
    def test_octave_single_node(self, mock_build):
        client = _mock_client()
        mock_build.return_value = client

        with patch("src.matclaw.gb10.octave_fallback.run_octave_on_node") as mock_oct:
            mock_oct.return_value = (True, "ans = 42\n", [])
            plan_data = {"mode": "octave", "multi_node": False, "warnings": []}
            result = execute(
                None, plan_data,
                code="disp(42)",
                gb10_settings=_mock_settings(),
            )
            assert result.success
            assert "42" in result.message

    @patch("src.matclaw.skills.gb10_offload.logic._build_client")
    def test_octave_multi_node(self, mock_build):
        client = _mock_client()
        mock_build.return_value = client

        with patch("src.matclaw.gb10.octave_fallback.run_octave_split") as mock_split:
            mock_split.return_value = (True, "node0: done\nnode1: done", [])
            plan_data = {"mode": "octave", "multi_node": True, "warnings": []}
            items = [{"seed": i} for i in range(4)]
            result = execute(
                None, plan_data,
                code="disp('sim')",
                work_items=items,
                gb10_settings=_mock_settings(),
            )
            assert result.success
            assert result.data["multi_node"]

    @patch("src.matclaw.skills.gb10_offload.logic._build_client")
    def test_backend_not_available(self, mock_build):
        mock_build.return_value = None
        plan_data = {"mode": "octave", "multi_node": False, "warnings": []}
        result = execute(None, plan_data, code="1+1", gb10_settings=GB10Settings(enabled=False))
        assert not result.success
        assert "not available" in result.error


# ---------------------------------------------------------------------------
# Run (end-to-end RPI)
# ---------------------------------------------------------------------------

class TestRun:
    @patch("src.matclaw.skills.gb10_offload.logic._build_client")
    def test_run_octave_e2e(self, mock_build):
        client = _mock_client()
        mock_build.return_value = client

        with patch("src.matclaw.gb10.octave_fallback.run_octave_on_node") as mock_oct:
            mock_oct.return_value = (True, "Monte Carlo result: mean=3.14159\n", [])
            result = run(
                None,
                code=(
                    "N = 100000;\n"
                    "x = rand(N,1); y = rand(N,1);\n"
                    "inside = sum(x.^2 + y.^2 <= 1);\n"
                    "pi_est = 4 * inside / N;\n"
                    "fprintf('Monte Carlo result: mean=%f\\n', pi_est);\n"
                ),
                gb10_settings=_mock_settings(),
            )
            assert result.success
            assert "Monte Carlo" in result.message
