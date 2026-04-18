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
from pathlib import Path
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

    def __init__(
        self,
        cwd: str,
        timeout: int = 120,
        venv_python: str | None = None,
        plots_dir: str | None = None,
    ):
        self._cwd = cwd
        self._timeout = timeout
        self._python = venv_python or sys.executable
        self._plots_dir = plots_dir

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
        wall_start = time.time()
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
            plots: list[str] = []
            if self._plots_dir and result.returncode == 0:
                pdir = Path(self._plots_dir)
                if pdir.is_dir():
                    exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".html", ".svg"}
                    try:
                        for fpath in pdir.iterdir():
                            if not fpath.is_file():
                                continue
                            if fpath.suffix.lower() not in exts:
                                continue
                            if fpath.stat().st_mtime >= wall_start - 1.0:
                                plots.append(f"/plots/{fpath.name}")
                        plots.sort()
                    except OSError:
                        logger.debug("Python runtime: could not scan plots_dir", exc_info=True)
            return RuntimeResult(
                success=result.returncode == 0,
                output=output,
                plots=plots,
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
    "MatlabRuntime", "ShellRuntime", "PythonRuntime",
]
