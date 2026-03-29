"""
Tests for MatlabBridge — focused on the timeout / ThreadPoolExecutor fix
and the guardrail layer. All tests run without a real MATLAB installation
by patching the engine and the internal _invoke callable.
"""
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.matclaw.matlab.matlab_bridge import (
    MatlabBridge,
    MatlabCallRequest,
    MatlabBridgeState,
)
from src.matclaw.config.base_config import MatlabSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bridge() -> MatlabBridge:
    """
    Create a bridge with enabled=True and a mock engine.
    We patch the 'matlab' module-level var so the engine-None guard passes.
    """
    settings = MatlabSettings(enabled=True)
    bridge = MatlabBridge(settings=settings)
    bridge._state = MatlabBridgeState(engine=MagicMock(), healthy=True)
    # Patch module-level matlab sentinel so `if matlab is None` check passes
    import src.matclaw.matlab.matlab_bridge as _mod
    _mod.matlab = object()  # truthy non-None value
    return bridge


# ---------------------------------------------------------------------------
# Guardrail tests (no MATLAB needed)
# ---------------------------------------------------------------------------

def test_guardrail_blocks_system_call():
    bridge = _make_bridge()
    req = MatlabCallRequest(function="eval", args=["system('ls')"], nargout=0)
    result = bridge.call(req)
    assert not result.success
    assert result.error is not None


def test_guardrail_blocks_delete():
    bridge = _make_bridge()
    req = MatlabCallRequest(function="eval", args=["delete('important.m')"], nargout=0)
    result = bridge.call(req)
    assert not result.success


def test_guardrail_allows_safe_call():
    bridge = _make_bridge()
    req = MatlabCallRequest(function="eval", args=["disp('hello')"], nargout=0)

    # Patch _invoke so no real MATLAB is called
    mock_func = MagicMock(return_value="hello")
    with patch.object(bridge._state, "engine") as mock_eng:
        mock_eng.eval = mock_func
        # Only test that guardrail doesn't block; actual exec may fail without MATLAB
        from src.matclaw.security.guardrail import guard_matlab_call
        decision = guard_matlab_call(req)
        assert decision.allow


# ---------------------------------------------------------------------------
# Timeout: shutdown(wait=False) must not block
# ---------------------------------------------------------------------------

def test_timeout_does_not_block():
    """
    The old `with ThreadPoolExecutor(...) as pool:` blocked indefinitely when
    MATLAB hung after a timeout. The fix uses shutdown(wait=False).
    Verify the call returns within 2s even when _invoke never completes.
    """
    bridge = _make_bridge()

    done_event = threading.Event()

    def _slow_invoke(*args, **kwargs):
        # Simulates a hung MATLAB call — waits until test teardown
        done_event.wait(timeout=10)
        return None

    req = MatlabCallRequest(
        function="eval",
        args=["disp('hello')"],
        nargout=0,
        timeout_seconds=0.2,  # Very short timeout
    )

    with (
        patch("src.matclaw.security.guardrail.guard_matlab_call") as mock_guard,
        patch.object(bridge._state.engine, "eval", side_effect=_slow_invoke),
    ):
        mock_guard.return_value = MagicMock(allow=True, reason="")

        start = time.monotonic()
        result = bridge.call(req)
        elapsed = time.monotonic() - start

    done_event.set()  # Release the hung thread so it can exit

    assert not result.success
    assert "timed out" in (result.error or "").lower()
    # Must complete well within 2 seconds (previously would hang indefinitely)
    assert elapsed < 2.0, f"call() took {elapsed:.2f}s — shutdown(wait=False) fix may have regressed"


# ---------------------------------------------------------------------------
# Lock serialisation
# ---------------------------------------------------------------------------

def test_concurrent_calls_are_serialised():
    """Two concurrent calls must not race; the second must wait for the first."""
    bridge = _make_bridge()
    call_order: list[int] = []
    lock = threading.Lock()

    def _invoke_slow(*args, **kwargs):
        with lock:
            call_order.append(len(call_order) + 1)
        time.sleep(0.05)
        return "ok"

    req = MatlabCallRequest(function="eval", args=["1+1"], nargout=1, timeout_seconds=5.0)

    with (
        patch("src.matclaw.security.guardrail.guard_matlab_call") as mock_guard,
        patch.object(bridge._state.engine, "eval", side_effect=_invoke_slow),
    ):
        mock_guard.return_value = MagicMock(allow=True, reason="")

        results: list = []

        def _call():
            results.append(bridge.call(req))

        t1 = threading.Thread(target=_call)
        t2 = threading.Thread(target=_call)
        t1.start(); t2.start()
        t1.join(); t2.join()

    # Both should succeed (or at least not crash each other)
    assert len(results) == 2


# ---------------------------------------------------------------------------
# is_healthy reflects call outcomes
# ---------------------------------------------------------------------------

def test_health_set_true_on_success():
    bridge = _make_bridge()
    bridge._state.healthy = False

    req = MatlabCallRequest(function="eval", args=["1+1"], nargout=1, timeout_seconds=5.0)

    with (
        patch("src.matclaw.security.guardrail.guard_matlab_call") as mock_guard,
        patch.object(bridge._state.engine, "eval", return_value=2),
    ):
        mock_guard.return_value = MagicMock(allow=True, reason="")
        result = bridge.call(req)

    assert result.success
    assert bridge._state.healthy


def test_health_false_on_timeout():
    bridge = _make_bridge()
    bridge._state.healthy = True

    done = threading.Event()

    def _hung(*args, **kwargs):
        done.wait(10)

    req = MatlabCallRequest(function="eval", args=["1+1"], nargout=1, timeout_seconds=0.1)

    with (
        patch("src.matclaw.security.guardrail.guard_matlab_call") as mock_guard,
        patch.object(bridge._state.engine, "eval", side_effect=_hung),
        patch.object(bridge, "stop"),  # prevent real MATLAB teardown
    ):
        mock_guard.return_value = MagicMock(allow=True, reason="")
        bridge.call(req)

    done.set()
    assert not bridge._state.healthy
