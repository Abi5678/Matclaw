"""
Tests for GB10Runtime in core/runtimes.py — mocked SSH.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.matclaw.config.base_config import GB10NodeSettings, GB10Settings
from src.matclaw.core.runtimes import GB10Runtime, RuntimeRegistry


def _mock_client():
    client = MagicMock()
    client.settings = GB10Settings(
        enabled=True,
        cluster_name="constellation",
        nodes=[
            GB10NodeSettings(ssh_alias="gn100-93da", ssh_user="ubuntu"),
        ],
    )
    client.head_node = client.settings.nodes[0]

    probe_ok = MagicMock()
    probe_ok.success = True
    probe_ok.stdout = "OK\n"
    client.probe_node.return_value = probe_ok
    return client


class TestGB10Runtime:
    def test_is_available_when_probe_succeeds(self):
        rt = GB10Runtime(_mock_client())
        assert rt.is_available()

    def test_not_available_when_client_none(self):
        rt = GB10Runtime(None)
        assert not rt.is_available()

    def test_not_available_when_probe_fails(self):
        client = _mock_client()
        bad_probe = MagicMock()
        bad_probe.success = False
        client.probe_node.return_value = bad_probe
        rt = GB10Runtime(client)
        assert not rt.is_available()

    def test_execute_octave_mode(self):
        client = _mock_client()
        rt = GB10Runtime(client)

        with patch("src.matclaw.gb10.octave_fallback.run_octave_on_node") as mock_oct:
            mock_oct.return_value = (True, "ans = 7\n", [])
            result = rt.execute("disp(7)", {"gb10_mode": "octave"})
            assert result.success
            assert result.runtime == "gb10"
            assert "7" in result.output

    def test_execute_binary_mode(self):
        client = _mock_client()
        run_result = MagicMock()
        run_result.success = True
        run_result.stdout = "binary output"
        run_result.stderr = ""
        run_result.artifacts = []
        client.run_on_head.return_value = run_result

        rt = GB10Runtime(client)
        result = rt.execute("./my_sim --seed 42", {"gb10_mode": "binary"})
        assert result.success
        assert "binary output" in result.output

    def test_execute_unknown_mode(self):
        client = _mock_client()
        rt = GB10Runtime(client)
        result = rt.execute("code", {"gb10_mode": "unknown_mode"})
        assert not result.success
        assert "Unknown GB10 mode" in result.error

    def test_register_in_registry(self):
        registry = RuntimeRegistry()
        rt = GB10Runtime(_mock_client())
        registry.register(rt)
        assert registry.get("gb10") is rt
        names = [r["name"] for r in registry.list_runtimes()]
        assert "gb10" in names
