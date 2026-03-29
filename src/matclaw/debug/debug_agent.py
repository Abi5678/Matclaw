"""
Autonomous debugging loop for MATLAB failures (Level 2).

Flow: capture error → parse error → find similar .m → suggest fix →
write temp .m → addpath → test → if success and config: apply to source with .bak.
"""

from __future__ import annotations

import logging
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, List, Optional, Tuple

from pydantic import BaseModel, Field

from src.matclaw.config.base_config import DebugAgentSettings
from src.matclaw.debug.prompts import (
    ANALYZE_SYSTEM_PROMPT,
    DEBUG_SYSTEM_PROMPT,
    build_analyze_user_prompt,
    build_debug_user_prompt,
)
from src.matclaw.lab_journal import append_lab_journal
from src.matclaw.llm.llm_client import call_chat_completion, resolve_api_key
from src.matclaw.matlab.matlab_bridge import MatlabBridge, MatlabCallRequest, MatlabCallResult
from src.matclaw.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# Common MATLAB error patterns for heuristic fixes
_ERROR_PATTERNS = [
    (r"Not enough input arguments", "not_enough_inputs"),
    (r"Too many input arguments", "too_many_inputs"),
    (r"Index exceeds (?:the )?number of array elements", "index_exceeds"),
    (r"Subscript indices must be real positive integers", "invalid_index"),
    (r"Dimensions do not match|matrix dimensions must agree", "dimension_mismatch"),
    (r"Undefined function or variable ['\"]?(\w+)['\"]?", "undefined_function"),
    (r"Undefined function ['\"]?(\w+)['\"]? for input arguments", "undefined_function"),
    (r"Attempt to execute (?:[\w.]+ )?as a function", "not_a_function"),
    (r"Conversion to (?:double|single|cell) from .* is not possible", "conversion_error"),
]


class DebugAttemptResult(BaseModel):
    """Result of an autonomous debugging attempt."""

    original_error: str
    attempted_fix_file: Optional[str] = Field(
        default=None, description="Path to the temporary .m file used for testing."
    )
    candidate_functions: List[str] = Field(
        default_factory=list, description="Similar MATLAB functions discovered locally."
    )
    fixed: bool = Field(default=False, description="Whether the fix candidate executed without error.")
    fix_error: Optional[str] = None
    applied_to_source: bool = Field(
        default=False, description="Whether the fix was written back to the source file (with .bak)."
    )
    suggested_changes: Optional[str] = Field(default=None, description="Human-readable summary of the fix applied.")


class DebugAgent:
    """
    Level 2 autonomous debugging: capture error, search similar functions,
    suggest and apply a fix to a temp .m file, test it, optionally apply back with .bak.
    """

    def __init__(
        self,
        matlab_bridge: MatlabBridge,
        settings: Optional[DebugAgentSettings] = None,
        matlab_root: Optional[Path] = None,
        memory_manager: MemoryManager | None = None,
        journal_path: Path | None = None,
        knowledge_base: "Any | None" = None,
    ) -> None:
        self.matlab_bridge = matlab_bridge
        self.settings = settings or DebugAgentSettings()
        self.matlab_root = matlab_root or Path(self.settings.matlab_root).resolve()
        self.memory_manager = memory_manager
        self.journal_path = journal_path
        self.knowledge_base = knowledge_base  # KnowledgeBase instance (optional)

    def set_memory_manager(self, memory_manager: MemoryManager | None) -> None:
        self.memory_manager = memory_manager

    def handle_failure(
        self,
        request: MatlabCallRequest,
        failure: MatlabCallResult,
    ) -> DebugAttemptResult:
        """Entry point when a MATLAB call fails."""
        error_msg = failure.error or "Unknown MATLAB error."
        logger.error(
            "MATLAB command failed; entering autonomous debug loop.",
            extra={"function": request.function, "error": error_msg},
        )
        self._journal(
            f"Debug start: function={request.function} error={error_msg[:200]}",
            source="DebugAgent",
        )

        candidate_paths = self._find_similar_functions(request.function)
        temp_file: Optional[Path] = None
        temp_dir: Optional[Path] = None
        fixed = False
        fix_error: Optional[str] = None
        applied_to_source = False
        suggested_changes: Optional[str] = None

        llm_rounds = max(1, int(self.settings.debug_max_rounds))
        previous_attempt_error: str | None = None
        previous_fix_explanation: str | None = None

        for attempt in range(max(self.settings.max_fix_attempts, llm_rounds)):
            if not candidate_paths:
                logger.info("No similar functions discovered; skipping fix attempt.")
                break

            source_path_str = candidate_paths[0]
            try:
                source = Path(source_path_str)
                if not source.is_file():
                    candidate_paths.pop(0)
                    continue

                source_code = source.read_text(errors="replace")
                parsed = self._parse_error(error_msg)
                # Fast path: try local heuristics first, then LLM reasoning if needed.
                if attempt == 0:
                    patched_code = self._suggest_fix(source_code, error_msg, parsed)
                else:
                    patched_code, previous_fix_explanation = self._suggest_fix_with_llm(
                        source_path=source,
                        source_code=source_code,
                        request=request,
                        error_message=error_msg,
                        parsed=parsed,
                        previous_attempt_error=previous_attempt_error,
                        previous_fix_explanation=previous_fix_explanation,
                    )
                temp_dir, temp_file = self._create_temp_candidate(source_path_str, patched_code)

                # Ensure MATLAB sees the temp file first
                self.matlab_bridge.addpath(str(temp_dir))

                logger.info(
                    "Testing speculative fix using temporary MATLAB file.",
                    extra={"temp_file": str(temp_file), "attempt": attempt + 1},
                )
                if not self.settings.debug_sandbox:
                    test_result = MatlabCallResult(success=True, result=None, error=None)
                else:
                    test_request = MatlabCallRequest(
                        function=request.function,
                        args=request.args,
                        kwargs=request.kwargs,
                        nargout=request.nargout,
                        timeout_seconds=request.timeout_seconds,
                    )
                    test_result = self.matlab_bridge.call(test_request)

                if test_result.success:
                    fixed = True
                    suggested_changes = self._summarize_changes(parsed, patched_code != source_code)
                    logger.info("Speculative fix executed successfully during debug loop.")
                    if self.settings.apply_fix_with_bak:
                        applied_to_source = self._apply_fix_to_source(source, source_code, patched_code)
                    self._store_fix_pattern(
                        request=request,
                        source_path=source,
                        original_error=failure.error or "Unknown MATLAB error.",
                        parsed=parsed,
                        explanation=suggested_changes or previous_fix_explanation or "Autonomous fix applied.",
                    )
                    self._journal(
                        f"Debug success: function={request.function} fixed=True applied={applied_to_source}",
                        source="DebugAgent",
                        artifact_paths=[str(source)] if applied_to_source else None,
                    )
                    break
                else:
                    fix_error = test_result.error or "Unknown error while testing fix."
                    logger.warning("Speculative fix did not resolve the error.", extra={"fix_error": fix_error})
                    error_msg = fix_error  # Retry with new error message
                    previous_attempt_error = fix_error
                    self._journal(
                        f"Debug attempt failed: function={request.function} attempt={attempt + 1} error={fix_error[:200]}",
                        source="DebugAgent",
                    )
            except Exception as exc:
                fix_error = str(exc)
                logger.exception("Error during autonomous MATLAB debug attempt: %s", exc)
                self._journal(
                    f"Debug exception: function={request.function} error={fix_error[:200]}",
                    source="DebugAgent",
                )
                break

        return DebugAttemptResult(
            original_error=failure.error or "Unknown MATLAB error.",
            attempted_fix_file=str(temp_file) if temp_file else None,
            candidate_functions=candidate_paths,
            fixed=fixed,
            fix_error=fix_error,
            applied_to_source=applied_to_source,
            suggested_changes=suggested_changes,
        )

    # ------------------------------------------------------------------
    # Proactive file analysis (the core "fix test.m and run it" method)
    # ------------------------------------------------------------------

    def analyze_and_fix_file(
        self,
        file_path: Path | str,
        *,
        action: str = "fix_and_run",  # "analyze" | "fix" | "run" | "fix_and_run"
        max_rounds: int | None = None,
    ) -> DebugAttemptResult:
        """
        Read a .m file, analyse/fix/run it autonomously.

        This is the **proactive** counterpart to :meth:`handle_failure` which
        only fires reactively when a MATLAB call fails.

        Args:
            file_path: Path to the ``.m`` file.
            action: What to do — ``"analyze"``, ``"fix"``, ``"run"``, or ``"fix_and_run"``.
            max_rounds: Max LLM fix-test-retry rounds (defaults to settings).

        Returns:
            :class:`DebugAttemptResult` with diagnosis and/or execution output.
        """
        from src.matclaw.security.file_access import guard_file_access

        path = Path(file_path)
        rounds = max_rounds or int(self.settings.debug_max_rounds)

        # --- validate path -------------------------------------------------
        decision = guard_file_access(str(path))
        if not decision.allow:
            return DebugAttemptResult(
                original_error=f"File access denied: {decision.reason}",
                fixed=False,
                fix_error=decision.reason,
            )
        path = decision.resolved_path  # type: ignore[assignment]

        # --- read source ----------------------------------------------------
        try:
            source_code = path.read_text(errors="replace")
        except Exception as exc:
            return DebugAttemptResult(
                original_error=f"Cannot read file: {exc}",
                fixed=False,
                fix_error=str(exc),
            )

        self._journal(f"Analyze start: file={path.name} action={action}", source="DebugAgent")

        # --- query memory for past fixes ------------------------------------
        memory_hints: list[str] = []
        if self.memory_manager is not None:
            try:
                results = self.memory_manager.query_context(
                    f"matlab debug fix {path.stem}",
                    n_results=3,
                )
                for r in results:
                    meta = r.get("metadata") or {}
                    hint = meta.get("debug_explanation") or meta.get("summary") or r.get("document")
                    if hint:
                        memory_hints.append(str(hint)[:280])
            except Exception:
                logger.exception("Memory query failed during file analysis.")

        # --- try running as-is (if action includes run) ---------------------
        run_error: str | None = None
        run_output: str | None = None
        if action in ("run", "fix_and_run"):
            try:
                # Ensure MATLAB sees the file's parent directory
                self.matlab_bridge.addpath(str(path.parent))
                success, output = self.matlab_bridge.run_matlab_code(f"feval('{path.stem}')")
                if success:
                    self._journal(
                        f"Analyze: {path.name} ran successfully (no fix needed).",
                        source="DebugAgent",
                    )
                    return DebugAttemptResult(
                        original_error="",
                        fixed=True,
                        suggested_changes=f"Code ran successfully. Output: {output[:500]}",
                    )
                run_error = output
            except Exception as exc:
                run_error = str(exc)

        # --- if action is just "run" and it failed, report -----------------
        if action == "run":
            return DebugAttemptResult(
                original_error=run_error or "Unknown runtime error.",
                fixed=False,
                fix_error=run_error,
                suggested_changes=f"Runtime error: {run_error}",
            )

        # --- LLM-powered analysis / fix ------------------------------------
        synthetic_request = MatlabCallRequest(function=path.stem, args=[], nargout=0)
        temp_file: Optional[Path] = None
        fixed = False
        fix_error_out: Optional[str] = None
        applied_to_source = False
        suggested_changes: Optional[str] = None

        for attempt in range(rounds):
            try:
                # Build prompt
                user_prompt = build_analyze_user_prompt(
                    file_path=str(path),
                    source_code=source_code,
                    action=action,
                    run_error=run_error,
                    memory_hints=memory_hints,
                )

                provider = self.settings.debug_llm_provider
                model = self.settings.debug_llm_model
                key = self.settings.debug_llm_api_key or resolve_api_key(provider)

                if not key:
                    # Fallback to heuristic if no LLM key
                    parsed = self._parse_error(run_error or "")
                    patched_code = self._suggest_fix(source_code, run_error or "", parsed)
                    explanation = "Heuristic fix (no LLM key available)."
                else:
                    text = call_chat_completion(
                        provider=provider,
                        model=model,
                        system=ANALYZE_SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": user_prompt}],
                        api_key=key,
                        max_tokens=2000,
                    )
                    payload = self._parse_json_payload(text)
                    has_issues = payload.get("has_issues", True)

                    # If analysis only, return the diagnosis
                    if action == "analyze":
                        diagnosis = payload.get("diagnosis") or payload.get("explanation") or "No issues found."
                        return DebugAttemptResult(
                            original_error=run_error or "",
                            fixed=not has_issues,
                            suggested_changes=diagnosis,
                        )

                    patched_code = payload.get("fixed_code") or source_code
                    explanation = payload.get("explanation") or "LLM-generated patch."

                    if not has_issues and patched_code == source_code:
                        return DebugAttemptResult(
                            original_error=run_error or "",
                            fixed=True,
                            suggested_changes="No issues found in the code.",
                        )

                # Sandbox test
                temp_dir, temp_file = self._create_temp_candidate(str(path), patched_code)
                self.matlab_bridge.addpath(str(temp_dir))

                if not self.settings.debug_sandbox:
                    test_success = True
                    test_output = ""
                else:
                    test_success, test_output = self.matlab_bridge.run_matlab_code(
                        f"run('{path.stem}')"
                    )

                if test_success:
                    fixed = True
                    suggested_changes = explanation
                    if self.settings.apply_fix_with_bak and patched_code != source_code:
                        applied_to_source = self._apply_fix_to_source(path, source_code, patched_code)
                    self._store_fix_pattern(
                        request=synthetic_request,
                        source_path=path,
                        original_error=run_error or "proactive analysis",
                        parsed=self._parse_error(run_error or ""),
                        explanation=explanation,
                    )
                    self._journal(
                        f"Analyze success: file={path.name} fixed=True applied={applied_to_source}",
                        source="DebugAgent",
                        artifact_paths=[str(path)] if applied_to_source else None,
                    )
                    break
                else:
                    fix_error_out = test_output
                    run_error = test_output  # Feed back for next round
                    self._journal(
                        f"Analyze fix attempt {attempt + 1} failed: {test_output[:200]}",
                        source="DebugAgent",
                    )

            except Exception as exc:
                fix_error_out = str(exc)
                logger.exception("Error during file analysis attempt %d: %s", attempt + 1, exc)
                break

        return DebugAttemptResult(
            original_error=run_error or "proactive analysis",
            attempted_fix_file=str(temp_file) if temp_file else None,
            fixed=fixed,
            fix_error=fix_error_out,
            applied_to_source=applied_to_source,
            suggested_changes=suggested_changes,
        )

    def _parse_error(self, error_msg: str) -> dict:
        """Extract error type and hints from common MATLAB error strings."""
        out = {"type": "unknown", "hint": None}
        for pattern, err_type in _ERROR_PATTERNS:
            m = re.search(pattern, error_msg, re.IGNORECASE)
            if m:
                out["type"] = err_type
                if m.lastindex:
                    out["hint"] = m.group(1)
                break
        return out

    def _suggest_fix_with_llm(
        self,
        *,
        source_path: Path,
        source_code: str,
        request: MatlabCallRequest,
        error_message: str,
        parsed: dict,
        previous_attempt_error: str | None,
        previous_fix_explanation: str | None,
    ) -> tuple[str, str]:
        provider = self.settings.debug_llm_provider
        model = self.settings.debug_llm_model
        key = self.settings.debug_llm_api_key or resolve_api_key(provider)
        if not key:
            logger.info("Debug LLM key missing; falling back to heuristic patch.")
            return self._suggest_fix(source_code, error_message, parsed), "LLM key missing; heuristic fallback."

        memory_hints: list[str] = []
        if self.memory_manager is not None:
            try:
                results = self.memory_manager.query_context(
                    f"matlab debug fix {request.function} {parsed.get('type', '')} {parsed.get('hint', '')}",
                    n_results=3,
                )
                for r in results:
                    meta = r.get("metadata") or {}
                    hint = meta.get("debug_explanation") or meta.get("summary") or r.get("document")
                    if hint:
                        memory_hints.append(str(hint)[:280])
            except Exception:
                logger.exception("Debug memory query failed.")

        user_prompt = build_debug_user_prompt(
            error_message=error_message,
            function_name=request.function,
            source_path=str(source_path),
            source_code=source_code,
            parsed_type=str(parsed.get("type", "unknown")),
            parsed_hint=parsed.get("hint"),
            previous_attempt_error=previous_attempt_error,
            prior_fix_explanation=previous_fix_explanation,
            memory_hints=memory_hints,
        )
        text = call_chat_completion(
            provider=provider,
            model=model,
            system=DEBUG_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            api_key=key,
            max_tokens=1800,
        )
        payload = self._parse_json_payload(text)
        fixed_code = payload.get("fixed_code") or source_code
        explanation = payload.get("explanation") or "LLM-generated patch."
        return fixed_code, explanation

    def _parse_json_payload(self, text: str) -> dict:
        clean = text.strip()
        if "```" in clean:
            start = clean.find("```")
            if "json" in clean[start : start + 10].lower():
                start = clean.find("\n", start) + 1
            end = clean.find("```", start)
            if end > start:
                clean = clean[start:end]
        i = clean.find("{")
        j = clean.rfind("}")
        if i >= 0 and j > i:
            clean = clean[i : j + 1]
        try:
            data = json.loads(clean)
            return data if isinstance(data, dict) else {}
        except Exception:
            logger.warning("Debug LLM response parse failed; using heuristic fallback.")
            return {}

    def _suggest_fix(self, source_code: str, error_msg: str, parsed: dict) -> str:
        """
        Propose a patched version of the MATLAB code based on the error.
        Heuristic edits only; designed to be augmented by an LLM later.
        """
        code = source_code
        err_type = parsed.get("type", "unknown")

        # Insert input-argument checks after the first function line
        if err_type == "not_enough_inputs":
            inferred = self._infer_required_inputs_from_signature(code)
            arg_names = self._infer_input_names_from_signature(code)
            if inferred and inferred > 1 and arg_names:
                code = self._insert_default_arg_guards(code, arg_names)
            else:
                code = self._insert_nargin_check(code, min_args=inferred or 1)
        elif err_type == "too_many_inputs":
            code = self._insert_nargin_check(code, max_args=True)

        # Optional: wrap main body in try/catch for clearer errors (lightweight)
        if err_type in ("index_exceeds", "invalid_index", "dimension_mismatch", "conversion_error"):
            code = self._wrap_in_try_catch(code)

        return code

    def _insert_nargin_check(self, code: str, min_args: Optional[int] = None, max_args: bool = False) -> str:
        """Insert nargin check right after the first line that looks like 'function ...'."""
        lines = code.split("\n")
        insert_at = -1
        for i, line in enumerate(lines):
            if re.match(r"\s*function\b", line):
                insert_at = i + 1
                break
        if insert_at < 0:
            return code
        if min_args is not None:
            check = f"    if nargin < {min_args}, error('MatClaw:NotEnoughInputs', 'Not enough input arguments.'); end\n"
        elif max_args:
            # We don't know max; just add a comment so user can fix
            check = "    % MatClaw: consider validating nargin to avoid too many inputs\n"
        else:
            return code
        lines.insert(insert_at, check)
        return "\n".join(lines)

    def _infer_required_inputs_from_signature(self, code: str) -> int | None:
        """
        Infer number of required inputs from first MATLAB function signature.
        Example: `function y = foo(a,b,c)` -> 3
        """
        for line in code.split("\n"):
            m = re.match(r"\s*function\b.*?\((.*?)\)", line)
            if not m:
                continue
            inside = (m.group(1) or "").strip()
            if not inside:
                return 0
            parts = [p.strip() for p in inside.split(",") if p.strip()]
            return len(parts)
        return None

    def _infer_input_names_from_signature(self, code: str) -> list[str]:
        for line in code.split("\n"):
            m = re.match(r"\s*function\b.*?\((.*?)\)", line)
            if not m:
                continue
            inside = (m.group(1) or "").strip()
            if not inside:
                return []
            return [p.strip() for p in inside.split(",") if p.strip()]
        return []

    def _insert_default_arg_guards(self, code: str, arg_names: list[str]) -> str:
        """
        For missing-input errors, assign safe defaults for optional trailing args.
        Example:
            if nargin < 2, b = 0; end
            if nargin < 3, c = 0; end
        """
        lines = code.split("\n")
        insert_at = -1
        for i, line in enumerate(lines):
            if re.match(r"\s*function\b", line):
                insert_at = i + 1
                break
        if insert_at < 0:
            return code
        guards: list[str] = []
        for idx, arg in enumerate(arg_names[1:], start=2):
            guards.append(f"    if nargin < {idx}, {arg} = 0; end")
        if guards:
            lines[insert_at:insert_at] = guards + [""]
        return "\n".join(lines)

    def _wrap_in_try_catch(self, code: str) -> str:
        """No-op: wrapping arbitrary .m in try/catch is fragile; leave for LLM or manual fix."""
        return code

    def _summarize_changes(self, parsed: dict, code_changed: bool) -> str:
        if not code_changed:
            return "Re-ran existing implementation (no code change)."
        parts = [f"Error type: {parsed.get('type', 'unknown')}"]
        if parsed.get("hint"):
            parts.append(f"Hint: {parsed['hint']}")
        parts.append("Applied heuristic fix (nargin check and/or try/catch).")
        return " ".join(parts)

    def _create_temp_candidate(self, source_path_str: str, patched_content: Optional[str] = None) -> Tuple[Path, Path]:
        """Create a temporary .m file (patched or copied). Returns (temp_dir, temp_file)."""
        source = Path(source_path_str)
        if not source.is_file():
            raise FileNotFoundError(f"Source MATLAB file not found: {source}")

        temp_dir = Path(tempfile.mkdtemp(prefix="matclaw_debug_"))
        dest = temp_dir / source.name

        if patched_content is not None:
            dest.write_text(patched_content, encoding="utf-8")
        else:
            shutil.copy2(source, dest)

        # Append MatClaw debug note
        try:
            with dest.open("a", encoding="utf-8") as fh:
                fh.write("\n% MatClaw autonomous debug copy. Original: " + str(source) + "\n")
        except Exception:
            logger.exception("Failed to annotate temporary MATLAB debug file: %s", dest)

        return temp_dir, dest

    def _apply_fix_to_source(self, source: Path, original_content: str, patched_content: str) -> bool:
        """Write source.m.bak with original, then write patched content to source. Returns True if done."""
        bak_path = source.with_suffix(source.suffix + ".bak")
        try:
            bak_path.write_text(original_content, encoding="utf-8")
            source.write_text(patched_content, encoding="utf-8")
            logger.info("Applied fix to source with .bak backup.", extra={"source": str(source), "bak": str(bak_path)})
            return True
        except Exception:
            logger.exception("Failed to apply fix to source (bak at %s).", bak_path)
            return False

    def _store_fix_pattern(
        self,
        *,
        request: MatlabCallRequest,
        source_path: Path,
        original_error: str,
        parsed: dict,
        explanation: str,
    ) -> None:
        # Store in ChromaDB artifacts (existing behaviour)
        if self.memory_manager is not None:
            try:
                key = f"debug_fix:{source_path.name}:{request.function}:{abs(hash(original_error)) % 1000000}"
                self.memory_manager.store_artifact(
                    key,
                    {
                        "skill": "debug_agent",
                        "source_file": str(source_path),
                        "function": request.function,
                        "error": original_error,
                        "error_type": parsed.get("type"),
                        "error_hint": parsed.get("hint"),
                        "debug_explanation": explanation,
                        "summary": explanation,
                    },
                    vector=None,
                )
            except Exception:
                logger.exception("Failed to store debug fix pattern in artifacts.")

        # Also store in KnowledgeBase for long-term memory
        if self.knowledge_base is not None:
            try:
                self.knowledge_base.store_debug_fix(
                    skill_name="debug_agent",
                    description=f"Fixed {source_path.name}: {explanation}",
                    source_file=str(source_path),
                    error_type=parsed.get("type", "unknown"),
                    explanation=explanation,
                )
            except Exception:
                logger.exception("Failed to store debug fix in knowledge base.")

    def _journal(self, summary: str, source: str = "DebugAgent", artifact_paths: list[str] | None = None) -> None:
        if self.journal_path is None:
            return
        try:
            append_lab_journal(self.journal_path, summary, source=source, artifact_paths=artifact_paths)
        except Exception:
            logger.exception("Failed to append debug journal entry.")

    def _find_similar_functions(self, failed_function: str) -> List[str]:
        """Search local MATLAB project files for functions with matching or similar names."""
        matches: List[str] = []
        if not self.matlab_root.is_dir():
            logger.warning(
                "MATLAB root directory does not exist; skipping function search.",
                extra={"matlab_root": str(self.matlab_root)},
            )
            return matches

        target_file = f"{failed_function}.m"
        for mfile in self.matlab_root.rglob("*.m"):
            if mfile.name.lower() == target_file.lower():
                matches.append(str(mfile))
                continue
            try:
                text = mfile.read_text(errors="ignore")
            except Exception:
                logger.exception("Failed to read MATLAB file during debug search: %s", mfile)
                continue
            if re.search(rf"\bfunction\b[^\n]*\b{re.escape(failed_function)}\b", text, re.IGNORECASE):
                matches.append(str(mfile))

        logger.info(
            "Discovered candidate MATLAB functions for debug.",
            extra={"failed_function": failed_function, "candidates": matches},
        )
        return matches
