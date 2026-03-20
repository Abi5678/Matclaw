from __future__ import annotations

# ---------------------------------------------------------------------------
# Proactive analysis prompt (used by DebugAgent.analyze_and_fix_file)
# ---------------------------------------------------------------------------

ANALYZE_SYSTEM_PROMPT = """You are a senior MATLAB code analyst for MatClaw.
Given a .m file, analyze it for: syntax errors, undeclared variables, missing
semicolons (output suppression), incorrect matrix dimensions, missing function
files, type mismatches, and logic issues.

If the user wants the code fixed, return ONLY JSON:
{
  "has_issues": true|false,
  "diagnosis": "<what is wrong — be specific>",
  "fixed_code": "<full fixed file contents, or null if no issues>",
  "explanation": "<short explanation of changes>"
}

If the user only wants analysis (no fix), set fixed_code to null.
Rules:
- Keep changes minimal and focused on the actual problem.
- Preserve function signatures unless strictly necessary.
- Avoid introducing dependencies outside base MATLAB.
- If the code looks correct, set has_issues to false.
""".strip()


def build_analyze_user_prompt(
    *,
    file_path: str,
    source_code: str,
    action: str,  # "analyze" | "fix" | "run" | "fix_and_run"
    run_error: str | None = None,
    memory_hints: list[str] | None = None,
) -> str:
    """Build user prompt for proactive file analysis / fix."""
    memory_block = "\n".join(f"- {h}" for h in (memory_hints or [])[:5]) or "(none)"
    error_block = f"\nMATLAB runtime error:\n{run_error}\n" if run_error else ""
    action_instruction = {
        "analyze": "Analyze the code for issues. Do NOT provide fixed_code.",
        "fix": "Analyze and provide fixed_code if there are issues.",
        "run": "The user wants to run this code. Check for obvious errors before execution.",
        "fix_and_run": "Analyze, fix any issues, and provide fixed_code so it can be run.",
    }.get(action, "Analyze and fix if needed.")

    return f"""File: {file_path}
Action requested: {action_instruction}
{error_block}
Relevant past fixes (memory):
{memory_block}

Source code:
```matlab
{source_code}
```""".strip()


# ---------------------------------------------------------------------------
# Reactive debug prompt (used by DebugAgent.handle_failure)
# ---------------------------------------------------------------------------

DEBUG_SYSTEM_PROMPT = """
You are a senior MATLAB debugging agent for MatClaw.
Given an error, source code, and optional workspace/memory hints, produce a safe targeted patch.

Return ONLY JSON with this shape:
{
  "file": "<filename or null>",
  "original_code": "<snippet before>",
  "fixed_code": "<full fixed file contents>",
  "explanation": "<short explanation>"
}

Rules:
- Keep changes minimal and focused on the reported failure.
- Preserve function signatures unless strictly necessary.
- Avoid introducing dependencies outside base MATLAB.
- If uncertain, still provide best-effort fixed_code and explain assumptions.
""".strip()


def build_debug_user_prompt(
    *,
    error_message: str,
    function_name: str,
    source_path: str,
    source_code: str,
    parsed_type: str,
    parsed_hint: str | None,
    previous_attempt_error: str | None = None,
    prior_fix_explanation: str | None = None,
    memory_hints: list[str] | None = None,
) -> str:
    memory_block = "\n".join(f"- {h}" for h in (memory_hints or [])[:5]) or "(none)"
    prev = (
        f"\nPrevious fix failed with:\n{previous_attempt_error}\n"
        f"Previous fix rationale:\n{prior_fix_explanation or '(none)'}\n"
        if previous_attempt_error
        else ""
    )
    return f"""
Error message:
{error_message}

Function:
{function_name}

Source file:
{source_path}

Parsed error type:
{parsed_type}
Hint:
{parsed_hint or '(none)'}

Relevant past fixes (memory):
{memory_block}
{prev}
Current source code:
```matlab
{source_code}
```
""".strip()

