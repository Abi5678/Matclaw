from __future__ import annotations

import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from contextlib import contextmanager
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, Optional

from pydantic import BaseModel

from matclaw.config.base_config import MatlabSettings
from matclaw.security.guardrail import guard_matlab_call

logger = logging.getLogger(__name__)
DEFAULT_MATLAB_CALL_TIMEOUT_SECONDS = 90.0  # 90s hard cap per call; CodeDoctor simplifies if needed

# Only these exceptions imply the MATLAB engine session is likely dead — not user/code errors.
_INFRA_FAILURE_TYPES = frozenset({
    "ConnectionError",
    "BrokenPipeError",
    "ConnectionResetError",
    "BlockingIOError",
})


def _is_infra_failure(exc: BaseException) -> bool:
    if type(exc).__name__ in _INFRA_FAILURE_TYPES:
        return True
    # MATLAB Engine for Python sometimes wraps transport errors
    msg = str(exc).lower()
    if "connection" in msg and ("refused" in msg or "reset" in msg or "closed" in msg):
        return True
    return False


try:  # Pragmatic import guard so the project works without MATLAB installed.
    import matlab.engine  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - environment-dependent
    matlab = None  # type: ignore[assignment]
else:
    matlab = matlab  # type: ignore[assignment]


class MatlabCallRequest(BaseModel):
    """
    Pydantic-validated request for a MATLAB call.
    This shape is compatible with MCP-style tool invocation arguments.
    """

    function: str
    args: list[Any] = []
    kwargs: Dict[str, Any] = {}
    nargout: int = 1
    timeout_seconds: Optional[float] = None


class MatlabCallResult(BaseModel):
    success: bool
    result: Any | None = None
    error: Optional[str] = None


@dataclass
class MatlabBridgeState:
    engine: Any | None = None
    healthy: bool = False
    needs_restart: bool = False  # set True after a timeout; cleared on successful call


class MatlabBridge:
    """
    Thin, robust wrapper around the MATLAB Engine for Python.

    This class is intentionally synchronous for now. Higher-level async orchestration
    should wrap calls into threads or process pools where required.
    """

    def __init__(self, settings: MatlabSettings, debug_agent: Any = None) -> None:
        self.settings = settings
        self._state = MatlabBridgeState()
        self._lock = threading.RLock()
        self._busy_lock = threading.Lock()
        self._busy_depth: int = 0
        self._debug_agent = debug_agent
        self._before_call: Callable[[MatlabCallRequest], None] | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="matlab-call")
        self._restart_pending = False

    def set_before_call(self, cb: Callable[[MatlabCallRequest], None] | None) -> None:
        """Set callback invoked before each MATLAB call (e.g. for Agent Activity logging)."""
        self._before_call = cb

    def set_debug_agent(self, agent: Any | None) -> None:
        """Attach or detach the autonomous debug agent (e.g. after daemon creates it)."""
        self._debug_agent = agent

    # Lifecycle -----------------------------------------------------------------

    def start(self) -> None:
        if not self.settings.enabled:
            logger.info("MATLAB bridge is disabled via configuration.")
            return

        if matlab is None:
            logger.error(
                "MATLAB engine for Python is not available. "
                "Ensure 'matlab.engine' is installed and MATLAB is configured."
            )
            self._state.healthy = False
            return

        # Tests may patch this module's `matlab` to a truthy sentinel without `.engine`;
        # avoid raising AttributeError inside the locked startup path.
        if not hasattr(matlab, "engine"):
            # Common in unit tests that patch ``matlab`` to a truthy sentinel without ``.engine``.
            logger.debug(
                "MATLAB bridge: matlab module has no 'engine' (skipped start)."
            )
            self._state.healthy = False
            return

        with self._lock:
            if self._state.engine is not None:
                logger.debug("MATLAB engine already started; skipping.")
                return

            try:
                logger.info("Starting MATLAB engine session...")
                
                # A: Specific shared session
                if self.settings.session_name:
                    logger.info("Connecting to named session: %s", self.settings.session_name)
                    self._state.engine = matlab.engine.connect_matlab(self.settings.session_name)
                
                # B: Auto-discover any shared session
                else:
                    sessions = matlab.engine.find_matlab()
                    if sessions:
                        logger.info("Auto-discovered available sessions: %s. Connecting to: %s", sessions, sessions[0])
                        self._state.engine = matlab.engine.connect_matlab(sessions[0])
                    
                    # C: Fallback – startup a new instance
                    else:
                        logger.info("No shared sessions found. Starting new MATLAB instance...")
                        # Try to find matlab binary if not in path
                        bin_path = _find_matlab_binary()
                        if bin_path:
                            logger.info("Using MATLAB binary at: %s", bin_path)
                            # Passing custom matlab path to start_matlab is not directly supported by engine API
                            # But we can update the PATH for this process temporarily.
                            old_path = os.environ.get("PATH", "")
                            os.environ["PATH"] = f"{os.path.dirname(bin_path)}:{old_path}"
                        
                        try:
                            if getattr(self.settings, "show_figure_windows", False):
                                # Allow desktop / figure windows (local pilot). Omit -nodesktop.
                                self._state.engine = matlab.engine.start_matlab("-nosplash")
                            else:
                                self._state.engine = matlab.engine.start_matlab("-nodesktop -nosplash")
                        finally:
                            if bin_path:
                                os.environ["PATH"] = old_path

                self._state.healthy = True
                logger.info("MATLAB engine started successfully.")
            except Exception as exc:  # pragma: no cover - environment-specific
                logger.exception("Failed to start MATLAB engine: %s", exc)
                self._state.engine = None
                self._state.healthy = False

    def stop(self) -> None:
        with self._lock:
            if self._state.engine is None:
                return
            try:
                if self.settings.session_name:
                    logger.info("Releasing shared MATLAB session '%s' (not quitting).", self.settings.session_name)
                else:
                    logger.info("Shutting down MATLAB engine.")
                    self._state.engine.quit()
            except Exception:  # pragma: no cover - environment-specific
                logger.exception("Error while shutting down MATLAB engine.")
            finally:
                self._state.engine = None
                self._state.healthy = False

    def restart(self) -> None:
        logger.info("Restarting MATLAB engine.")
        with self._lock:
            # Inline stop logic (avoid releasing+reacquiring the lock).
            if self._state.engine is not None:
                try:
                    if not self.settings.session_name:
                        self._state.engine.quit()
                except Exception:
                    logger.debug("Error during restart-stop; continuing.", exc_info=True)
                finally:
                    self._state.engine = None
                    self._state.healthy = False
        self.start()

    # Health / activity ---------------------------------------------------------

    def is_healthy(self) -> bool:
        """True when the MATLAB engine session is considered connected (session alive)."""
        return self._state.healthy and self._state.engine is not None

    def needs_restart(self) -> bool:
        """True if a previous call timed out and the engine should be restarted before next use."""
        return self._state.needs_restart

    def get_status_detail(self) -> dict:
        """Return a dictionary with detailed health information for diagnostics."""
        return {
            "healthy": self.is_healthy(),
            "enabled": self.settings.enabled,
            "session_name": self.settings.session_name,
            "has_engine": self._state.engine is not None,
            "needs_restart": self._state.needs_restart,
            "busy": self.is_busy(),
            "show_figure_windows": bool(getattr(self.settings, "show_figure_windows", False)),
        }

    def is_busy(self) -> bool:
        """True while MATLAB engine calls or tracked external MATLAB work (e.g. batch) are in flight."""
        with self._busy_lock:
            return self._busy_depth > 0

    def _begin_busy(self) -> None:
        with self._busy_lock:
            self._busy_depth += 1

    def _end_busy(self) -> None:
        with self._busy_lock:
            self._busy_depth = max(0, self._busy_depth - 1)

    @contextmanager
    def external_busy(self) -> Iterator[None]:
        """
        Context manager for MATLAB work that does not go through call() (e.g. matlab -batch subprocess).
        Keeps /health matlab_busy accurate for long simulations.
        """
        self._begin_busy()
        try:
            yield
        finally:
            self._end_busy()

    def addpath(self, path: str) -> None:
        """Add a directory to the MATLAB path so scripts there can be run."""
        if matlab is None or self._state.engine is None:
            return
        with self._lock:
            try:
                # Guardrail mandate: even non-`eval` MATLAB operations should pass.
                req = MatlabCallRequest(function="addpath", args=[path], nargout=0)
                decision = guard_matlab_call(req)
                if not decision.allow:
                    logger.warning("Guardrail blocked addpath: %s", decision.reason)
                    return
                self._state.engine.addpath(path, nargout=0)
            except Exception:
                logger.exception("Failed to addpath in MATLAB: %s", path)

    # Call interface ------------------------------------------------------------

    def call(self, request: MatlabCallRequest) -> MatlabCallResult:
        """
        Execute a MATLAB function call in a validated and logged manner.

        This method is designed to be used directly by MCP tools: they can
        construct a `MatlabCallRequest` from tool arguments, invoke this,
        and surface the `MatlabCallResult` back to the LLM.
        """
        if not self.settings.enabled:
            msg = "MATLAB bridge is disabled; refusing call."
            logger.warning(msg)
            return MatlabCallResult(success=False, error=msg)

        if self._state.needs_restart:
            msg = "MATLAB engine needs restart (previous call timed out); refusing call until restart completes."
            logger.warning(msg)
            return MatlabCallResult(success=False, error=msg)

        if matlab is None or self._state.engine is None:
            msg = "MATLAB engine is not available."
            logger.error(msg)
            self._state.healthy = False
            return MatlabCallResult(success=False, error=msg)

        # Guardrails: every MATLAB call request must pass before execution.
        try:
            decision = guard_matlab_call(request)
            if not decision.allow:
                logger.warning(
                    "Guardrail blocked MATLAB call: function=%s reason=%s",
                    request.function,
                    decision.reason,
                )
                return MatlabCallResult(success=False, error=decision.reason)
        except Exception as exc:
            # Fail-closed for safety if guardrail itself errors.
            logger.exception("Guardrail failed (fail-closed): %s", exc)
            return MatlabCallResult(success=False, error="Guardrail failure: refusing MATLAB execution.")

        timed_out = False
        timeout_seconds = (
            float(request.timeout_seconds)
            if request.timeout_seconds is not None
            else DEFAULT_MATLAB_CALL_TIMEOUT_SECONDS
        )

        failure: MatlabCallResult | None = None
        self._begin_busy()
        try:
            with self._lock:
                try:
                    if self._before_call is not None:
                        try:
                            self._before_call(request)
                        except Exception:
                            logger.exception("before_call callback failed")

                    logger.info(
                        "Invoking MATLAB function.",
                        extra={
                            "function": request.function,
                            "nargs": len(request.args),
                            "nargout": request.nargout,
                        },
                    )
                    eng = self._state.engine
                    func = getattr(eng, request.function)

                    def _invoke() -> Any:
                        return func(
                            *request.args,
                            nargout=request.nargout,
                            **request.kwargs,
                        )

                    if timeout_seconds > 0:
                        future = self._pool.submit(_invoke)
                        try:
                            result = future.result(timeout=timeout_seconds)
                        except FuturesTimeoutError:
                            timed_out = True
                            failure = MatlabCallResult(
                                success=False,
                                error=f"MATLAB call timed out after {timeout_seconds:.1f}s: {request.function}",
                            )
                    else:
                        result = _invoke()

                    if timed_out:
                        # The engine thread is still running inside MATLAB — mark for restart
                        # so the next caller gets a fresh session instead of another hang.
                        self._state.needs_restart = True
                        logger.error(
                            "MATLAB call timed out: function=%s timeout=%.1fs — engine will restart",
                            request.function,
                            timeout_seconds,
                        )
                    else:
                        self._state.healthy = True
                        self._state.needs_restart = False
                        return MatlabCallResult(success=True, result=result)

                except Exception as exc:  # pragma: no cover - MATLAB dependent
                    logger.exception("MATLAB call failed: %s", exc)
                    if _is_infra_failure(exc):
                        self._state.healthy = False
                    failure = MatlabCallResult(success=False, error=str(exc))
        finally:
            self._end_busy()

        # After a timeout, restart the engine in the background so the next call
        # gets a clean session (the hung thread may run forever otherwise).
        if timed_out and not self._restart_pending:
            self._restart_pending = True
            old_pool = self._pool  # capture reference before replacement
            def _bg_restart() -> None:
                import time as _t
                _t.sleep(0.5)
                try:
                    logger.info("Background MATLAB restart triggered after timeout...")
                    old_pool.shutdown(wait=True, cancel_futures=True)
                except Exception:
                    logger.warning("Old thread pool cleanup incomplete; thread may be leaked.", exc_info=True)
                try:
                    self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="matlab-call")
                    self.restart()
                    logger.info("MATLAB engine restarted successfully after timeout.")
                except Exception as _re:
                    logger.warning("Background MATLAB restart failed: %s", _re)
                finally:
                    self._restart_pending = False
            threading.Thread(target=_bg_restart, name="matlab-restart-after-timeout", daemon=True).start()

        # Outside the lock, optionally invoke the autonomous debugging loop.
        if self._debug_agent is not None and failure is not None and failure.error:
            from matclaw.debug.debug_agent import DebugAttemptResult
            debug_result: DebugAttemptResult = self._debug_agent.handle_failure(request, failure)
            logger.info(
                "Autonomous MATLAB debug attempt complete.",
                extra=debug_result.model_dump(),
            )

        assert failure is not None
        return failure

    def run_matlab_code(self, code: str) -> tuple[bool, str]:
        """
        Run MATLAB code via evalc to capture output.
        Returns (success, output_or_error).
        """
        if not self.is_healthy():
            return False, "MATLAB bridge is not available."
        escaped = code.replace("\\", "\\\\").replace("'", "''").replace("\n", "; ")
        matlab_cmd = f"evalc('{escaped}')"
        req = MatlabCallRequest(function="eval", args=[matlab_cmd], nargout=1)
        result = self.call(req)
        if result.success:
            out = result.result
            return True, str(out) if out is not None else ""
        return False, result.error or "Unknown MATLAB error"

    def capture_figure(self, output_path: str | Path) -> bool:
        """
        Save the current MATLAB figure (gcf) to output_path.
        Returns True if a figure existed and was saved successfully.
        """
        if not self.is_healthy():
            return False
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        # MATLAB path: use forward slashes
        matlab_path = str(path).replace("\\", "/")
        # saveas(gcf, path) - works for .png; escape single quotes in path
        escaped = matlab_path.replace("'", "''")
        cmd = f"try; f=gcf; if ~isempty(f)&&isvalid(f); saveas(f,'{escaped}'); end; catch; end"
        req = MatlabCallRequest(function="eval", args=[cmd], nargout=0)
        result = self.call(req)
        return result.success and path.is_file()

    def run_unittest(self, test_file_path: str | Path) -> tuple[bool, str]:
        """
        Programmatically invoke MATLAB's matlab.unittest.TestSuite for a specific file.
        Returns a tuple of (success_boolean, console_output) to feed back to the LLM agent.
        """
        if not self.is_healthy():
            return False, "MATLAB bridge is not available."
            
        path = Path(test_file_path).resolve()
        matlab_path = str(path).replace("\\", "/").replace("'", "''")
        
        # We use evalc to capture the rich text output of the unit test runner,
        # which provides excellent context for the LLM upon success/failure.
        cmd = f"runtests('{matlab_path}')"
        return self.run_matlab_code(cmd)


def _find_matlab_binary() -> Optional[str]:
    """Search for MATLAB executable in common installation paths."""
    import shutil
    
    # Already in path?
    path = shutil.which("matlab")
    if path:
        return path
        
    # Common macOS paths
    if sys.platform == "darwin":
        import glob
        apps = glob.glob("/Applications/MATLAB_R*.app/bin/matlab")
        if apps:
            # Return the latest version found
            return sorted(apps, reverse=True)[0]
            
    # Common Linux paths
    elif sys.platform == "linux":
        import glob
        paths = glob.glob("/usr/local/MATLAB/R*/bin/matlab")
        if paths:
            return sorted(paths, reverse=True)[0]
            
    return None
