"""
Autonomous debugging loop for MATLAB failures (Level 2).

Flow: capture error → parse error → find similar .m → suggest fix →
write temp .m → addpath → test → if success and config: apply to source with .bak.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from src.matclaw.config.base_config import DebugAgentSettings
from src.matclaw.matlab.matlab_bridge import MatlabBridge, MatlabCallRequest, MatlabCallResult

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
    ) -> None:
        self.matlab_bridge = matlab_bridge
        self.settings = settings or DebugAgentSettings()
        self.matlab_root = matlab_root or Path(self.settings.matlab_root).resolve()

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

        candidate_paths = self._find_similar_functions(request.function)
        temp_file: Optional[Path] = None
        temp_dir: Optional[Path] = None
        fixed = False
        fix_error: Optional[str] = None
        applied_to_source = False
        suggested_changes: Optional[str] = None

        for attempt in range(self.settings.max_fix_attempts):
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
                patched_code = self._suggest_fix(source_code, error_msg, parsed)
                temp_dir, temp_file = self._create_temp_candidate(source_path_str, patched_code)

                # Ensure MATLAB sees the temp file first
                self.matlab_bridge.addpath(str(temp_dir))

                logger.info(
                    "Testing speculative fix using temporary MATLAB file.",
                    extra={"temp_file": str(temp_file), "attempt": attempt + 1},
                )
                test_request = MatlabCallRequest(
                    function=request.function,
                    args=request.args,
                    kwargs=request.kwargs,
                    nargout=request.nargout,
                )
                test_result = self.matlab_bridge.call(test_request)

                if test_result.success:
                    fixed = True
                    suggested_changes = self._summarize_changes(parsed, patched_code != source_code)
                    logger.info("Speculative fix executed successfully during debug loop.")
                    if self.settings.apply_fix_with_bak:
                        applied_to_source = self._apply_fix_to_source(source, source_code, patched_code)
                    break
                else:
                    fix_error = test_result.error or "Unknown error while testing fix."
                    logger.warning("Speculative fix did not resolve the error.", extra={"fix_error": fix_error})
                    error_msg = fix_error  # Retry with new error message
            except Exception as exc:
                fix_error = str(exc)
                logger.exception("Error during autonomous MATLAB debug attempt: %s", exc)
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

    def _suggest_fix(self, source_code: str, error_msg: str, parsed: dict) -> str:
        """
        Propose a patched version of the MATLAB code based on the error.
        Heuristic edits only; designed to be augmented by an LLM later.
        """
        code = source_code
        err_type = parsed.get("type", "unknown")

        # Insert input-argument checks after the first function line
        if err_type == "not_enough_inputs":
            code = self._insert_nargin_check(code, min_args=1)
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
