"""
General-purpose execution runtimes for MatClaw.
MATLAB, Python, and Shell as peer runtimes — the core of OpenClaw-style versatility.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class RuntimeResult:
    success: bool
    output: str
    plots: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    runtime: str = ""
    error: str = ""


class BaseRuntime(ABC):
    name: str = "base"

    @abstractmethod
    def execute(self, code: str, context: dict[str, Any] | None = None) -> RuntimeResult:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...


# ── MATLAB Runtime ─────────────────────────────────────────────────────────────

class MatlabRuntime(BaseRuntime):
    name = "matlab"

    def __init__(
        self,
        bridge: Any,
        collect_fn: Callable[[str, str], tuple[str, list[str]]],
    ):
        self._bridge = bridge
        self._collect = collect_fn

    def is_available(self) -> bool:
        try:
            return self._bridge is not None and self._bridge.is_healthy()
        except Exception:
            return False

    def execute(self, code: str, context: dict[str, Any] | None = None) -> RuntimeResult:
        req_text = (context or {}).get("task", code[:80])
        t0 = time.monotonic()
        try:
            output, plots = self._collect(code, req_text)
            elapsed = int((time.monotonic() - t0) * 1000)
            success = not output.startswith("MATLAB error:")
            return RuntimeResult(
                success=success,
                output=output,
                plots=plots,
                elapsed_ms=elapsed,
                runtime=self.name,
                error="" if success else output,
            )
        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            return RuntimeResult(
                success=False, output="", elapsed_ms=elapsed,
                runtime=self.name, error=str(exc),
            )


# ── Shell Runtime ──────────────────────────────────────────────────────────────

_SHELL_DENY = [
    "rm -rf /", "rm -rf ~", "mkfs", ":(){:|:&};:",
    "dd if=/dev/zero", "sudo rm", "chmod -R 777 /",
    "> /dev/sda", "kill -9 1",
]


class ShellRuntime(BaseRuntime):
    name = "shell"

    def __init__(self, cwd: str, timeout: int = 30):
        self._cwd = cwd
        self._timeout = timeout

    def is_available(self) -> bool:
        return True

    def execute(self, code: str, context: dict[str, Any] | None = None) -> RuntimeResult:
        lowered = code.lower()
        for pattern in _SHELL_DENY:
            if pattern in lowered:
                return RuntimeResult(
                    success=False, output="", runtime=self.name,
                    error=f"Shell guardrail blocked dangerous pattern: '{pattern}'"
                )
        t0 = time.monotonic()
        try:
            result = subprocess.run(
                code, shell=True, capture_output=True, text=True,
                cwd=self._cwd, timeout=self._timeout,
                env={**os.environ, "TERM": "dumb"},
            )
            elapsed = int((time.monotonic() - t0) * 1000)
            output = (result.stdout + result.stderr).strip() or "(no output)"
            return RuntimeResult(
                success=result.returncode == 0,
                output=output,
                elapsed_ms=elapsed,
                runtime=self.name,
                error="" if result.returncode == 0 else f"Exit {result.returncode}: {result.stderr[:200]}",
            )
        except subprocess.TimeoutExpired:
            return RuntimeResult(
                success=False, output="", runtime=self.name,
                error=f"Command timed out after {self._timeout}s",
            )
        except Exception as exc:
            return RuntimeResult(success=False, output="", runtime=self.name, error=str(exc))


# ── Python Runtime ─────────────────────────────────────────────────────────────

_PYTHON_DENY = [
    "__import__('os').system",
    "subprocess.call(['bash",
    "eval(compile(",
    "exec(compile(",
]


class PythonRuntime(BaseRuntime):
    name = "python"

    def __init__(self, cwd: str, timeout: int = 30, venv_python: str | None = None):
        self._cwd = cwd
        self._timeout = timeout
        self._python = venv_python or sys.executable

    def is_available(self) -> bool:
        return True

    def execute(self, code: str, context: dict[str, Any] | None = None) -> RuntimeResult:
        lowered = code.lower()
        for pattern in _PYTHON_DENY:
            if pattern in lowered:
                return RuntimeResult(
                    success=False, output="", runtime=self.name,
                    error="Python guardrail blocked dangerous pattern"
                )
        t0 = time.monotonic()
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".py", mode="w", delete=False, encoding="utf-8"
            ) as f:
                f.write(code)
                tmp = f.name

            result = subprocess.run(
                [self._python, tmp],
                capture_output=True, text=True,
                cwd=self._cwd, timeout=self._timeout,
            )
            elapsed = int((time.monotonic() - t0) * 1000)
            output = (result.stdout + result.stderr).strip() or "(no output)"
            return RuntimeResult(
                success=result.returncode == 0,
                output=output,
                elapsed_ms=elapsed,
                runtime=self.name,
                error="" if result.returncode == 0 else f"Exit {result.returncode}: {result.stderr[:300]}",
            )
        except subprocess.TimeoutExpired:
            return RuntimeResult(
                success=False, output="", runtime=self.name,
                error=f"Python script timed out after {self._timeout}s",
            )
        except Exception as exc:
            return RuntimeResult(success=False, output="", runtime=self.name, error=str(exc))
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


# ── GB10 Runtime ──────────────────────────────────────────────────────────────


class GB10Runtime(BaseRuntime):
    """
    Remote execution on a GB10 (DGX Spark) cluster.

    Dispatches code to the GB10 over SSH.  Supports three execution strategies:

    - **cuda**: compile & run CUDA/C code produced by GPU Coder
    - **octave**: run .m scripts via GNU Octave on the GB10 CPU
    - **binary**: run a pre-compiled binary or shell command

    The ``mode`` is chosen per-call via ``context["gb10_mode"]`` and defaults
    to ``"octave"`` for .m code.
    """

    name = "gb10"

    def __init__(self, gb10_client: Any) -> None:
        self._client = gb10_client

    def is_available(self) -> bool:
        if self._client is None:
            return False
        try:
            result = self._client.probe_node(self._client.head_node)
            return result.success
        except Exception:
            return False

    def execute(self, code: str, context: dict[str, Any] | None = None) -> RuntimeResult:
        ctx = context or {}
        mode = ctx.get("gb10_mode", "octave")
        timeout = ctx.get("timeout")
        t0 = time.monotonic()

        try:
            if mode == "octave":
                from src.matclaw.gb10.octave_fallback import run_octave_on_node

                ok, output, artifacts = run_octave_on_node(
                    self._client,
                    self._client.head_node,
                    code,
                    timeout=timeout,
                )
            elif mode == "binary":
                result = self._client.run_on_head(code, timeout=timeout)
                ok = result.success
                output = result.stdout or result.stderr
                artifacts = result.artifacts
            else:
                ok = False
                output = ""
                artifacts = []
                return RuntimeResult(
                    success=False,
                    output="",
                    runtime=self.name,
                    error=f"Unknown GB10 mode: {mode}. Use 'octave' or 'binary'.",
                )

            elapsed = int((time.monotonic() - t0) * 1000)
            return RuntimeResult(
                success=ok,
                output=output,
                plots=artifacts,
                elapsed_ms=elapsed,
                runtime=self.name,
                error="" if ok else output,
            )
        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            return RuntimeResult(
                success=False,
                output="",
                elapsed_ms=elapsed,
                runtime=self.name,
                error=str(exc),
            )


# ── Runtime Registry ──────────────────────────────────────────────────────────

class RuntimeRegistry:
    def __init__(self) -> None:
        self._runtimes: dict[str, BaseRuntime] = {}

    def register(self, runtime: BaseRuntime) -> None:
        self._runtimes[runtime.name] = runtime
        logger.info("Runtime registered: %s", runtime.name)

    def get(self, name: str) -> BaseRuntime | None:
        return self._runtimes.get(name)

    def list_runtimes(self) -> list[dict]:
        return [
            {"name": r.name, "available": r.is_available()}
            for r in self._runtimes.values()
        ]

    def execute(
        self, name: str, code: str, context: dict[str, Any] | None = None
    ) -> RuntimeResult:
        rt = self.get(name)
        if rt is None:
            return RuntimeResult(
                success=False, output="", runtime=name,
                error=f"Runtime '{name}' not registered",
            )
        if not rt.is_available():
            return RuntimeResult(
                success=False, output="", runtime=name,
                error=f"Runtime '{name}' is not available right now",
            )
        return rt.execute(code, context)


__all__ = [
    "RuntimeResult", "BaseRuntime", "RuntimeRegistry",
    "MatlabRuntime", "ShellRuntime", "PythonRuntime", "GB10Runtime",
]
