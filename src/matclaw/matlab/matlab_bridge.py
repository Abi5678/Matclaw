from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel

from src.matclaw.config.base_config import MatlabSettings
from src.matclaw.security.guardrail import guard_matlab_call

logger = logging.getLogger(__name__)
DEFAULT_MATLAB_CALL_TIMEOUT_SECONDS = 300.0


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
        self._debug_agent = debug_agent
        self._before_call: Callable[[MatlabCallRequest], None] | None = None

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

        with self._lock:
            if self._state.engine is not None:
                logger.debug("MATLAB engine already started; skipping.")
                return

            try:
                logger.info("Starting MATLAB engine session...")
                if self.settings.session_name:
                    req = MatlabCallRequest(
                        function="connect_matlab",
                        args=[self.settings.session_name],
                        nargout=0,
                    )
                    decision = guard_matlab_call(req)
                    if not decision.allow:
                        logger.warning("Guardrail blocked connect_matlab: %s", decision.reason)
                        self._state.engine = None
                        self._state.healthy = False
                        return
                    self._state.engine = matlab.engine.connect_matlab(self.settings.session_name)
                else:
                    req = MatlabCallRequest(function="start_matlab", args=[], nargout=0)
                    decision = guard_matlab_call(req)
                    if not decision.allow:
                        logger.warning("Guardrail blocked start_matlab: %s", decision.reason)
                        self._state.engine = None
                        self._state.healthy = False
                        return
                    self._state.engine = matlab.engine.start_matlab()
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
                    # Shared session: just release the reference without killing MATLAB.
                    # The MATLAB desktop continues running for the user.
                    logger.info("Releasing shared MATLAB session '%s' (not quitting).", self.settings.session_name)
                    # No quit() — just drop the reference
                else:
                    logger.info("Shutting down MATLAB engine.")
                    req = MatlabCallRequest(function="quit", args=[], nargout=0)
                    decision = guard_matlab_call(req)
                    if not decision.allow:
                        logger.warning("Guardrail blocked engine quit: %s", decision.reason)
                    else:
                        self._state.engine.quit()
            except Exception:  # pragma: no cover - environment-specific
                logger.exception("Error while shutting down MATLAB engine.")
            finally:
                self._state.engine = None
                self._state.healthy = False

    def restart(self) -> None:
        logger.info("Restarting MATLAB engine.")
        self.stop()
        self.start()

    # Health --------------------------------------------------------------------

    def is_healthy(self) -> bool:
        return self._state.healthy and self._state.engine is not None

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

        if matlab is None or self._state.engine is None:
            msg = "MATLAB engine is not available."
            logger.error(msg)
            self._state.healthy = False
            return MatlabCallResult(success=False, error=msg)

        if self._before_call is not None:
            try:
                self._before_call(request)
            except Exception:
                logger.exception("before_call callback failed")

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

        with self._lock:
            try:
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
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(_invoke)
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
                    self._state.healthy = False
                    logger.error("MATLAB call timed out: function=%s timeout=%.1fs", request.function, timeout_seconds)
                    # Cleanup must happen outside the bridge lock.
                    pass
                else:
                    self._state.healthy = True
                    return MatlabCallResult(success=True, result=result)

            except Exception as exc:  # pragma: no cover - MATLAB dependent
                logger.exception("MATLAB call failed: %s", exc)
                # MatlabExecutionError means MATLAB ran but the *code* failed —
                # the engine itself is still alive.  Only mark unhealthy for true
                # connection failures (timeout handled above, other errors below).
                exc_type = type(exc).__name__
                is_code_error = exc_type in (
                    "MatlabExecutionError", "MatlabExecutionException",
                    "MatlabRuntimeError",
                )
                if not is_code_error:
                    self._state.healthy = False
                failure = MatlabCallResult(success=False, error=str(exc))

        if timed_out:
            try:
                self.stop()
            except Exception:
                logger.exception("Failed stopping MATLAB engine after timeout.")

        # Outside the lock, optionally invoke the autonomous debugging loop.
        if self._debug_agent is not None and failure.error:
            from src.matclaw.debug.debug_agent import DebugAttemptResult
            debug_result: DebugAttemptResult = self._debug_agent.handle_failure(request, failure)
            logger.info(
                "Autonomous MATLAB debug attempt complete.",
                extra=debug_result.model_dump(),
            )

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
