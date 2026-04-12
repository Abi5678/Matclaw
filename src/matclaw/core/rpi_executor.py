"""
RPI Executor: Research (incl. query memory) → Plan (incorporate lessons) → Execute (run skills).

Used by the Sentry when a new file appears in /data_in; also invoked explicitly for skill runs.
On skill success, stores final parameters in MemoryManager for future query_context().
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from matclaw.config.base_config import MatClawSettings
    from matclaw.core.experiment import ExperimentTracker

from matclaw.matlab.matlab_bridge import MatlabBridge, MatlabCallRequest
from matclaw.memory.memory_manager import MemoryManager
from matclaw.skills import load_skill_logic
from matclaw.skills.vision_analyst import VisionAnalyst

logger = logging.getLogger(__name__)

_MAX_VERIFY_RECURSION = 1


def _get_matlab_toolboxes(bridge: MatlabBridge) -> list[str]:
    """Query MATLAB for installed toolboxes without corrupting bridge health."""
    try:
        # Use run_matlab_code (evalc-based) to avoid the "only scalar struct" error
        # that occurs when calling ver() directly with nargout=1 through matlab.engine.
        was_healthy = bridge.is_healthy()
        ok, output = bridge.run_matlab_code("v=ver; disp(strjoin({v.Name},'|'))")
        # Restore healthy state — ver failure is non-fatal for the bridge
        if was_healthy and not bridge.is_healthy():
            bridge._state.healthy = True
        if not ok or not output:
            return []
        toolboxes = [t.strip() for t in output.strip().split("|") if t.strip()]
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
        experiment_tracker: "ExperimentTracker | None" = None,
        knowledge_base: Any | None = None,
        consolidation_engine: Any | None = None,
        on_run_complete: Callable[[str, Path, str, list[str]], None] | None = None,
        hitl_threshold_seconds: float = 600.0,
        on_pending_hitl: Callable[[int, dict[str, Any], str, dict[str, Any], str], None] | None = None,
        on_research_complete: Callable[[dict[str, Any]], None] | None = None,
        on_plan_complete: Callable[[dict[str, Any], str, dict[str, Any]], None] | None = None,
        on_start: Callable[[str, str], None] | None = None,
        on_phase_change: Callable[[str, str], None] | None = None,
        on_complete: Callable[[str, str], None] | None = None,
    ) -> None:
        self.matlab_bridge = matlab_bridge
        self.memory_manager = memory_manager
        self.experiment_tracker = experiment_tracker
        self.knowledge_base = knowledge_base
        self.consolidation_engine = consolidation_engine
        self.on_run_complete = on_run_complete
        self._hitl_threshold = hitl_threshold_seconds
        self._on_pending_hitl = on_pending_hitl
        self._on_research_complete = on_research_complete
        self._on_plan_complete = on_plan_complete
        self._on_start = on_start
        self._on_phase_change = on_phase_change
        self._on_complete = on_complete

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
        experiment_id: str | None = None,
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
        kwargs = dict(skill_kwargs or {})
        # Inject shared resources so skills like report_generator can use them
        kwargs.setdefault("memory_manager", self.memory_manager)
        result = run_fn(self.matlab_bridge, **kwargs)
        if getattr(result, "success", False) and self.memory_manager is not None and hasattr(result, "data") and result.data:
            key = f"{artifact_key_prefix or skill_name}:{id(result)}"
            self.memory_manager.store_artifact(
                key,
                {"skill": skill_name, "message": getattr(result, "message", ""), "experiment_id": experiment_id or "", **result.data},
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

        if path.suffix.lower() == ".m":
            summary_parts.append(f"Detected .m file: {name}.")
            try:
                from matclaw.debug.debug_agent import DebugAgent

                debug_agent = DebugAgent(
                    self.matlab_bridge,
                    matlab_root=path.parent,
                    memory_manager=self.memory_manager,
                    knowledge_base=self.knowledge_base,
                )
                result = debug_agent.analyze_and_fix_file(path, action="fix_and_run")
                if result.fixed:
                    msg = f"Fixed and ran successfully. {result.suggested_changes or ''}"
                    summary_parts.append(msg)
                    print(f"✅ [File Doctor] {msg}")  # noqa: T201
                elif result.fix_error:
                    msg = f"Fix attempted but failed: {result.fix_error[:200]}"
                    summary_parts.append(msg)
                    print(f"❌ [File Doctor] {msg}")  # noqa: T201
                else:
                    msg = f"Analysis: {result.suggested_changes or 'No issues found.'}"
                    summary_parts.append(msg)
                    print(f"ℹ️ [File Doctor] {msg}")  # noqa: T201
                artifact_paths.append(str(path))
            except Exception as exc:
                summary_parts.append(f"File analysis error: {exc}")
                logger.exception("run_on_file .m analysis failed: %s", exc)
                print(f"❌ [File Doctor] {exc}")  # noqa: T201

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
        _verify_depth: int = 0,
        **skill_kwargs: Any,
    ) -> Any:
        """Full RPI loop: Research → Plan → Implement → Verify (vision). If estimate > threshold and chat_id set, triggers HITL and returns {pending: True}."""
        experiment_id: str | None = None
        if self.experiment_tracker is not None and _verify_depth == 0:
            try:
                rec = self.experiment_tracker.start_experiment(
                    skill_name=skill_name,
                    params={"context": (context or "")[:500], "skill_kwargs": skill_kwargs},
                    tags=["rpi"],
                )
                experiment_id = rec.experiment_id
            except Exception:
                logger.exception("Experiment start failed.")
        if self._on_start is not None:
            try:
                self._on_start("run_rpi", skill_name)
            except Exception:
                logger.exception("on_start callback failed")
        if self._on_phase_change is not None:
            try:
                self._on_phase_change("research", skill_name)
            except Exception:
                logger.exception("on_phase_change callback failed (research)")
        research_data = self.research(context=context)
        if self._on_research_complete is not None:
            try:
                self._on_research_complete(research_data)
            except Exception:
                logger.exception("on_research_complete callback failed")
        plan_data = self.plan(research_data, task_hint=skill_name)
        if self._on_phase_change is not None:
            try:
                self._on_phase_change("plan", skill_name)
            except Exception:
                logger.exception("on_phase_change callback failed (plan)")
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
        if self._on_phase_change is not None:
            try:
                self._on_phase_change("execute", skill_name)
            except Exception:
                logger.exception("on_phase_change callback failed (execute)")
        result = self.execute(
            plan_data,
            skill_name,
            skill_kwargs=skill_kwargs,
            artifact_key_prefix=artifact_key_prefix,
            experiment_id=experiment_id,
        )

        # Verify: multimodal analysis of post-implement plot; oscillations → recursive research / gain tuning
        if not getattr(result, "success", False):
            if experiment_id and self.experiment_tracker is not None:
                try:
                    self.experiment_tracker.finish_experiment(
                        experiment_id,
                        status="failed",
                        error=getattr(result, "error", None) or getattr(result, "message", "Skill failed."),
                    )
                except Exception:
                    logger.exception("Experiment finish failed (failed status).")
            # Store failure in knowledge base
            if self.knowledge_base is not None:
                try:
                    err_msg = getattr(result, "error", None) or getattr(result, "message", "Skill failed.")
                    self.knowledge_base.store_failure(
                        skill_name=skill_name,
                        description=f"Skill {skill_name} failed during RPI loop.",
                        params=skill_kwargs,
                        error=str(err_msg)[:500],
                    )
                except Exception:
                    logger.exception("Knowledge base failure storage failed.")
            if self._on_complete is not None:
                try:
                    self._on_complete("failed", skill_name)
                except Exception:
                    logger.exception("on_complete callback failed")
            return result

        if _verify_depth < _MAX_VERIFY_RECURSION and self.matlab_bridge is not None and self.matlab_bridge.is_healthy():
            try:
                analyst = VisionAnalyst(self.matlab_bridge)
                verdict = analyst.capture_and_analyze()
                if verdict is not None and verdict.oscillations:
                    logger.info("RPI Verify: oscillations detected; triggering recursive research for PID/gain tuning.")
                    tune_ctx = (
                        (context or "")
                        + " [VERIFY: plot shows oscillations after implement; prioritize retuning control gains via pid_optimizer]"
                    )
                    self.research(context=tune_ctx)
                    recursive_result = self.run_rpi(
                        "pid_optimizer",
                        context=tune_ctx.strip(),
                        artifact_key_prefix=artifact_key_prefix,
                        chat_id=chat_id,
                        _verify_depth=_verify_depth + 1,
                        **skill_kwargs,
                    )
                    if experiment_id and self.experiment_tracker is not None:
                        try:
                            self.experiment_tracker.log_metric(experiment_id, "recursive_verify", True)
                        except Exception:
                            logger.exception("Experiment metric logging failed.")
                    if self._on_complete is not None:
                        try:
                            self._on_complete("done", skill_name)
                        except Exception:
                            logger.exception("on_complete callback failed")
                    return recursive_result
            except Exception:
                logger.exception("RPI Verify stage failed; returning implement result.")

        if self._on_complete is not None:
            try:
                self._on_complete("done", skill_name)
            except Exception:
                logger.exception("on_complete callback failed")
        if experiment_id and self.experiment_tracker is not None:
            try:
                data = getattr(result, "data", None) or {}
                metrics: dict[str, Any] = {}
                for k in ("overshoot", "settling_time", "steady_state_error", "score", "cost"):
                    if k in data:
                        metrics[k] = data[k]
                self.experiment_tracker.finish_experiment(
                    experiment_id,
                    status="success",
                    metrics=metrics,
                )
                for p in data.get("artifact_paths", []) if isinstance(data, dict) else []:
                    if p:
                        self.experiment_tracker.log_artifact(experiment_id, str(p), artifact_type="artifact")
            except Exception:
                logger.exception("Experiment finish failed (success status).")

        # Store result in knowledge base and auto-extract lessons
        if experiment_id and self.knowledge_base is not None:
            try:
                data = getattr(result, "data", None) or {}
                metrics_for_kb: dict[str, Any] = {}
                for k in ("overshoot", "settling_time", "steady_state_error", "score", "cost"):
                    if k in data:
                        metrics_for_kb[k] = data[k]
                self.knowledge_base.store_result(
                    skill_name=skill_name,
                    params=skill_kwargs,
                    metrics=metrics_for_kb,
                    experiment_id=experiment_id,
                )
            except Exception:
                logger.exception("Knowledge base result storage failed.")

        if experiment_id and self.consolidation_engine is not None:
            try:
                lessons = self.consolidation_engine.auto_extract_lessons(experiment_id)
                if lessons:
                    logger.info("Auto-extracted %d lessons from experiment %s", len(lessons), experiment_id)
            except Exception:
                logger.exception("Auto lesson extraction failed.")

        return result

    def run_flow(
        self,
        hybrid_prompt: str,
        *,
        chat_id: int | None = None,
        conversation_buffer: list[dict[str, str]] | None = None,
        settings: MatClawSettings | None = None,
    ) -> Any:
        """
        Research–Plan–Implement from a hybrid natural-language prompt (e.g. voice + clipboard).

        Routes via the NL router (Nemotron / Gemini / Claude) when an API key is configured;
        otherwise runs ``workspace_auditor`` with the hybrid text as context.

        Args:
            hybrid_prompt: Full prompt, e.g. "Context: ...\\nCode Snippet: ...".
            chat_id: Optional Telegram/chat id for HITL gating (same as ``run_rpi``).
            conversation_buffer: Optional prior turns for ``route_nl_message``.
            settings: Application settings; if omitted, loads default ``MatClawSettings``.

        Returns:
            Skill result, MATLAB output string, memory query string, or pending-HITL dict.
        """
        from matclaw.config.base_config import MatClawSettings
        from matclaw.gateways.nl_router import resolve_skill_from_nl, route_nl_message
        from matclaw.skills import list_skills

        cfg = settings or MatClawSettings()
        if self._on_start is not None:
            try:
                self._on_start("run_flow", "nl_router")
            except Exception:
                logger.exception("on_start callback failed")
        skills = list_skills()
        buf = list(conversation_buffer or [])
        llm = cfg.llm
        api_key = (
            llm.api_key
            or os.environ.get("NVIDIA_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
        )

        if not api_key:
            logger.info("run_flow: no LLM API key; defaulting to workspace_auditor")
            result = self.run_rpi("workspace_auditor", context=hybrid_prompt, chat_id=chat_id)
            if self._on_complete is not None:
                try:
                    self._on_complete("done", "workspace_auditor")
                except Exception:
                    logger.exception("on_complete callback failed")
            return result

        lab_context_str: str | None = None
        try:
            from matclaw.llm.context_loader import format_lab_context_for_prompt, load_lab_context

            lab_ctx = load_lab_context(
                matlab_bridge=self.matlab_bridge,
                memory_manager=self.memory_manager,
                memory_n_results=3,
            )
            lab_context_str = format_lab_context_for_prompt(lab_ctx)
        except Exception:
            pass

        intent_result = route_nl_message(
            hybrid_prompt,
            buf,
            skills,
            api_key=api_key,
            model=llm.model,
            provider=llm.provider,
            lab_context=lab_context_str or None,
        )

        if intent_result.intent == "run_skill":
            skill_name = resolve_skill_from_nl(intent_result.skill_name, skills)
            if skill_name:
                result = self.run_rpi(skill_name, context=hybrid_prompt, chat_id=chat_id)
                if self._on_complete is not None:
                    try:
                        self._on_complete("done", skill_name)
                    except Exception:
                        logger.exception("on_complete callback failed")
                return result
            logger.warning("run_flow: could not resolve skill from NL; falling back to workspace_auditor")
            return self.run_rpi("workspace_auditor", context=hybrid_prompt, chat_id=chat_id)

        if intent_result.intent == "analyze_file" and intent_result.file_path:
            from matclaw.debug.debug_agent import DebugAgent
            from matclaw.security.file_access import guard_file_access

            decision = guard_file_access(intent_result.file_path)
            if not decision.allow:
                return {"success": False, "error": decision.reason, "intent": "analyze_file"}
            debug_agent = DebugAgent(
                self.matlab_bridge,
                matlab_root=decision.resolved_path.parent,
                memory_manager=self.memory_manager,
                knowledge_base=self.knowledge_base,
            )
            result = debug_agent.analyze_and_fix_file(
                decision.resolved_path,
                action=intent_result.file_action or "fix_and_run",
            )
            return {
                "success": result.fixed,
                "output": result.suggested_changes,
                "error": result.fix_error,
                "intent": "analyze_file",
                "file_path": str(decision.resolved_path),
            }

        if intent_result.intent == "execute_code" and intent_result.matlab_code:
            success, output = self.matlab_bridge.run_matlab_code(intent_result.matlab_code)
            return {"success": success, "output": output, "intent": "execute_code"}

        if intent_result.intent == "ask_question" or intent_result.query:
            if self.memory_manager is None:
                return "Memory manager not configured."
            results = self.memory_manager.query_context(intent_result.query or hybrid_prompt, n_results=5)
            if not results:
                return "No relevant results in memory."
            parts: list[str] = []
            for i, r in enumerate(results[:5], 1):
                meta = r.get("metadata") or {}
                doc = r.get("document") or meta.get("summary") or meta.get("message") or str(meta)[:200]
                parts.append(f"{i}. {doc}")
            return "\n".join(parts)

        return {"intent": intent_result.intent, "message": "No action taken.", "raw": intent_result.raw_response}

    async def run_rpi_async(self, skill_name: str, **kwargs: Any) -> Any:
        """Async wrapper for non-blocking orchestration callers."""
        return await asyncio.to_thread(self.run_rpi, skill_name, **kwargs)

    async def run_flow_async(self, hybrid_prompt: str, **kwargs: Any) -> Any:
        """Async wrapper for non-blocking NL/hybrid callers."""
        return await asyncio.to_thread(self.run_flow, hybrid_prompt, **kwargs)
