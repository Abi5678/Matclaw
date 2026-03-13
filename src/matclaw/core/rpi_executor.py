"""
RPI Executor: Research (incl. query memory) → Plan (incorporate lessons) → Execute (run skills).

Used by the Sentry when a new file appears in /data_in; also invoked explicitly for skill runs.
On skill success, stores final parameters in MemoryManager for future query_context().
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from src.matclaw.matlab.matlab_bridge import MatlabBridge, MatlabCallRequest
from src.matclaw.memory.memory_manager import MemoryManager
from src.matclaw.skills import load_skill_logic

logger = logging.getLogger(__name__)


def _get_matlab_toolboxes(bridge: MatlabBridge) -> list[str]:
    """Query MATLAB for installed toolboxes via `ver`."""
    try:
        req = MatlabCallRequest(function="ver", args=[], nargout=1)
        result = bridge.call(req)
        if not result.success or result.result is None:
            return []
        v = result.result
        toolboxes: list[str] = []
        try:
            flat = list(v) if hasattr(v, "__iter__") else [v]
            for item in flat:
                name = None
                if hasattr(item, "Name"):
                    name = getattr(item, "Name", None)
                elif hasattr(item, "get"):
                    name = item.get("Name") or item.get("name")
                elif isinstance(item, (list, tuple)) and len(item) >= 1:
                    name = item[0]
                if name and str(name).strip():
                    toolboxes.append(str(name).strip())
        except Exception:
            pass
        return toolboxes[:50]
    except Exception:
        return []

SKILL_ESTIMATE_SECONDS: dict[str, float] = {
    "pid_optimizer": 900.0,
    "workspace_auditor": 10.0,
    "report_generator": 30.0,
    "simulink_runner": 120.0,  # Models can be large; Load-Compile-Run takes time
}


class RPIExecutor:
    """
    Runs the RPI loop: research (with historical context from MemoryManager),
    plan (incorporate lessons learned), execute (run MATLAB/skills).
    When a skill finishes successfully, stores the result as an artifact.
    """

    def __init__(
        self,
        matlab_bridge: MatlabBridge,
        memory_manager: MemoryManager | None = None,
        on_run_complete: Callable[[str, Path, str, list[str]], None] | None = None,
        hitl_threshold_seconds: float = 600.0,
        on_pending_hitl: Callable[[int, dict[str, Any], str, dict[str, Any], str], None] | None = None,
        on_research_complete: Callable[[dict[str, Any]], None] | None = None,
        on_plan_complete: Callable[[dict[str, Any], str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.matlab_bridge = matlab_bridge
        self.memory_manager = memory_manager
        self.on_run_complete = on_run_complete
        self._hitl_threshold = hitl_threshold_seconds
        self._on_pending_hitl = on_pending_hitl
        self._on_research_complete = on_research_complete
        self._on_plan_complete = on_plan_complete

    def research(self, context: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        """
        Gather context: query MemoryManager for historical data relevant to the current project/query.
        When MATLAB bridge is healthy, also fetches detected toolboxes.
        """
        out: dict[str, Any] = {"lessons_learned": [], "error": None, "matlab_toolboxes": []}
        if self.memory_manager is not None:
            try:
                query = context or project_id or "recent project parameters"
                results = self.memory_manager.query_context(query, n_results=5)
                out["lessons_learned"] = [r.get("metadata") or {} for r in results]
                out["documents"] = [r.get("document") for r in results]
            except Exception as exc:
                logger.exception("RPI research (query_context) failed: %s", exc)
                out["error"] = str(exc)
        if self.matlab_bridge is not None and self.matlab_bridge.is_healthy():
            try:
                out["matlab_toolboxes"] = _get_matlab_toolboxes(self.matlab_bridge)
            except Exception as exc:
                logger.exception("RPI research (toolboxes) failed: %s", exc)
        return out

    def plan(self, research_data: dict[str, Any], task_hint: str | None = None) -> dict[str, Any]:
        """
        Plan next action, incorporating research_data["lessons_learned"] into the proposed approach.
        """
        lessons = research_data.get("lessons_learned") or []
        plan_data: dict[str, Any] = {
            "task_hint": task_hint,
            "lessons_incorporated": lessons[:3],  # Top 3 for context
            "action": "run_skill",
        }
        return plan_data

    def execute(
        self,
        plan_data: dict[str, Any],
        skill_name: str,
        skill_kwargs: dict[str, Any] | None = None,
        artifact_key_prefix: str | None = None,
    ) -> Any:
        """
        Execute a skill (e.g. workspace_auditor, pid_optimizer). On success, store artifact and call on_run_complete.
        """
        mod = load_skill_logic(skill_name)
        if mod is None:
            raise ValueError(f"Skill not found or failed to load: {skill_name}")
        run_fn = getattr(mod, "run", None)
        if run_fn is None:
            raise ValueError(f"Skill {skill_name} has no run()")
        kwargs = skill_kwargs or {}
        result = run_fn(self.matlab_bridge, **kwargs)
        if getattr(result, "success", False) and self.memory_manager is not None and hasattr(result, "data") and result.data:
            key = f"{artifact_key_prefix or skill_name}:{id(result)}"
            self.memory_manager.store_artifact(
                key,
                {"skill": skill_name, "message": getattr(result, "message", ""), **result.data},
                vector=None,
            )
            logger.info("Stored artifact after %s success: %s", skill_name, key)
        return result

    def run_on_file(self, file_path: Path | str) -> tuple[str, list[str]]:
        """
        Proactive sentry entry: when a new file appears in data_in:
        - If .mat: run workspace_auditor and print summary to terminal.
        - If 'PID' in filename: suggest running pid_optimizer.
        Returns (summary_string, artifact_paths) for the lab journal.
        """
        path = Path(file_path)
        name = path.name
        summary_parts = []
        artifact_paths: list[str] = []

        if path.suffix.lower() == ".slx":
            model_name = path.stem
            summary_parts.append(
                f"Detected .slx model: {name}. I see you updated the model. "
                f"Should I run a validation simulation? (Ask: 'simulate {model_name}' or 'run {model_name} for 10 seconds')"
            )
            print(f"💡 [Sentry] .slx model detected: {name}. Suggest running validation: simulate {model_name}")  # noqa: T201
            artifact_paths.append(str(path))

        if path.suffix.lower() == ".mat":
            summary_parts.append(f"Detected .mat file: {name}.")
            try:
                mod = load_skill_logic("workspace_auditor")
                if mod and hasattr(mod, "run"):
                    result = mod.run(self.matlab_bridge)
                    if getattr(result, "success", False) and getattr(result, "data", None):
                        data = result.data
                        msg = data.get("summary") or data.get("message") or str(data)
                        summary_parts.append(msg)
                        print(f"✅ [Workspace Auditor] {msg}")  # noqa: T201
                        if data.get("warnings"):
                            for w in data["warnings"]:
                                print(f"   ⚠️ {w}")  # noqa: T201
                        artifact_paths.append(str(path))
                        if self.memory_manager:
                            self.memory_manager.store_artifact(
                                f"workspace_audit:{path.name}",
                                {"file": str(path), "summary": msg, **data},
                                vector=None,
                            )
                    else:
                        summary_parts.append("Workspace audit failed.")
                        print(f"❌ [Workspace Auditor] {getattr(result, 'error', 'Unknown error')}")  # noqa: T201
                else:
                    summary_parts.append("Workspace auditor skill not available.")
            except Exception as exc:
                summary_parts.append(f"Workspace audit error: {exc}")
                logger.exception("run_on_file workspace_auditor failed: %s", exc)
                print(f"❌ [Workspace Auditor] {exc}")  # noqa: T201

        if "PID" in name.upper():
            summary_parts.append("File name suggests PID tuning; consider running pid_optimizer skill.")
            print("💡 [Sentry] File name contains 'PID'. Suggest running pid_optimizer (e.g. run PID tuning).")  # noqa: T201
            artifact_paths.append(str(path))

        summary = " ".join(summary_parts) if summary_parts else f"Detected new file: {name}."
        if self.on_run_complete:
            self.on_run_complete(summary, path, "sentry_run", artifact_paths)
        return summary, artifact_paths

    def run_rpi(
        self,
        skill_name: str,
        context: str | None = None,
        artifact_key_prefix: str | None = None,
        chat_id: int | None = None,
        **skill_kwargs: Any,
    ) -> Any:
        """Full RPI loop. If estimate > threshold and chat_id set, triggers HITL and returns {pending: True}."""
        research_data = self.research(context=context)
        if self._on_research_complete is not None:
            try:
                self._on_research_complete(research_data)
            except Exception:
                logger.exception("on_research_complete callback failed")
        plan_data = self.plan(research_data, task_hint=skill_name)
        if self._on_plan_complete is not None:
            try:
                self._on_plan_complete(plan_data, skill_name, skill_kwargs or {})
            except Exception:
                logger.exception("on_plan_complete callback failed")
        estimate = research_data.get("estimated_duration_seconds") or plan_data.get("estimated_duration_seconds")
        if estimate is None:
            estimate = SKILL_ESTIMATE_SECONDS.get(skill_name, 0)
        plan_data["estimated_duration_seconds"] = estimate
        estimated_ram = research_data.get("estimated_ram_gb") or plan_data.get("estimated_ram_gb") or 0
        if (
            chat_id is not None
            and self._on_pending_hitl is not None
            and estimate is not None
            and estimate > self._hitl_threshold
        ):
            msg = f"This run will take ~{int(estimate // 60)} mins"
            if estimated_ram:
                msg += f" and ~{estimated_ram}GB RAM"
            msg += ". Proceed? [Yes/No]"
            self._on_pending_hitl(chat_id, plan_data, skill_name, skill_kwargs, msg)
            return {"pending": True, "estimate_seconds": estimate, "message": msg}
        return self.execute(plan_data, skill_name, skill_kwargs=skill_kwargs, artifact_key_prefix=artifact_key_prefix)
