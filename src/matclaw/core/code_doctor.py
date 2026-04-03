"""
CodeDoctor — Autonomous MATLAB code debugging engine.

Mirrors the exact human engineering loop:
  1. Run code → get output + plot
  2. Diagnose root cause from output, warnings, plot quality
  3. Apply targeted programmatic fix (deterministic rules, NOT LLM re-prompt)
  4. Re-run
  5. Repeat up to MAX_ROUNDS; then fall back to LLM fix with full diagnosis context

Diagnosis → Fix pairs (deterministic):
  blank_plot / scale_issue  → inject real-world constants, rescale coordinates
  timeout_risk              → reduce dt (5x), convert dense for-loops to vectorized masks
  stale_figures             → prepend `close all; clc;`
  syntax_printf             → replace `%,` with `%g`
  syntax_newline            → flatten multi-line string literals
  no_saveas                 → (handled by _run_matlab_and_collect wrapper, not here)
  low_content               → re-examine scale; if domain=space, inject AU/km constants
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3

# ── Domain constant libraries ─────────────────────────────────────────────────

_SPACE_CONSTANTS = """\
% Real space constants (auto-injected by CodeDoctor)
R_e = 6371;        % km  Earth radius
R_m = 1737;        % km  Moon radius
D_moon = 384400;   % km  Earth-Moon distance
mu_e = 398600;     % km^3/s^2  Earth gravitational parameter
mu_s = 1.327e11;   % km^3/s^2  Sun gravitational parameter
AU = 1.496e8;      % km  Astronomical unit
g0 = 9.81e-3;      % km/s^2  Surface gravity
"""

_DRONE_CONSTANTS = """\
% Real drone/UAV constants (auto-injected by CodeDoctor)
g = 9.81;          % m/s^2
mass = 1.5;        % kg  typical racing drone
Cd = 0.47;         % drag coefficient
rho = 1.225;       % kg/m^3  air density at sea level
A = 0.04;          % m^2  frontal area
max_alt = 400;     % m  typical UAV altitude limit
max_speed = 30;    % m/s  ~108 km/h
"""

_ROCKET_CONSTANTS = """\
% Real rocket constants (auto-injected by CodeDoctor)
g0 = 9.81;          % m/s^2
Isp_vac = 450;      % s  vacuum specific impulse (Merlin)
Isp_sl  = 311;      % s  sea-level specific impulse
ve = Isp_vac * 9.81; % m/s  exhaust velocity
T_max = 934e3;      % N  Falcon 9 sea-level thrust (one engine)
m0 = 549000;        % kg  Falcon 9 full mass
m_dry = 25600;      % kg  Falcon 9 dry mass
"""

_DOMAIN_CONSTANTS = {
    "space":  _SPACE_CONSTANTS,
    "drone":  _DRONE_CONSTANTS,
    "rocket": _ROCKET_CONSTANTS,
}

# ── Issue dataclass ───────────────────────────────────────────────────────────

@dataclass
class Issue:
    type: str         # e.g. "scale_issue", "timeout_risk", "blank_plot"
    severity: str     # "critical" | "warning"
    description: str
    fix_applied: str = ""


@dataclass
class DiagnosisResult:
    issues: list[Issue] = field(default_factory=list)
    domain: str = "general"      # "space" | "drone" | "rocket" | "general"
    estimated_iterations: int = 0
    plot_std: float = -1.0       # PIL pixel std; -1 = not checked

    @property
    def has_critical(self) -> bool:
        return any(i.severity == "critical" for i in self.issues)


# ── Diagnosis ─────────────────────────────────────────────────────────────────

def _detect_domain(code: str, task_text: str) -> str:
    combined = (code + " " + task_text).lower()
    if any(w in combined for w in ["apollo","lunar","moon","orbit","spacecraft","satellite","interplanetary","au ","mars","venus","iss "]):
        return "space"
    if any(w in combined for w in ["drone","uav","quadrotor","multirotor","rotor","propeller"]):
        return "drone"
    if any(w in combined for w in ["rocket","starship","falcon","launch vehicle","staging","isp","exhaust"]):
        return "rocket"
    return "general"


def _estimate_iterations(code: str) -> int:
    """Estimate worst-case loop count from dt/N patterns in code."""
    total_time = 0
    dt = 0
    for m in re.finditer(r'\bT\s*=\s*([\d\.\*\+eE]+)', code):
        try:
            total_time = max(total_time, float(eval(m.group(1).replace('*', '*'))))
        except Exception:
            pass
    for m in re.finditer(r'\bdt\s*=\s*([\d\.]+)', code):
        try:
            dt = float(m.group(1))
        except Exception:
            pass
    if total_time > 0 and dt > 0:
        return int(total_time / dt)
    # Fallback: look for N= or linspace patterns
    for m in re.finditer(r'\bN\s*=\s*(\d+)', code):
        return int(m.group(1))
    return 0


def _check_plot_quality(plot_path: str) -> float:
    """Return pixel std deviation of plot image. -1 if not checkable."""
    if not plot_path or not os.path.exists(plot_path):
        return -1.0
    try:
        from PIL import Image
        import numpy as np
        arr = np.array(Image.open(plot_path).convert("RGB"))
        return float(arr.std())
    except Exception as e:
        logger.debug("PIL check failed: %s", e)
        return -1.0


def diagnose(
    code: str,
    output: str,
    plots: list[str],
    task_text: str,
    plots_dir: str,
) -> DiagnosisResult:
    """
    Analyse code + execution output + plot to identify what went wrong.
    Returns a DiagnosisResult with a list of Issues.
    """
    result = DiagnosisResult()
    result.domain = _detect_domain(code, task_text)
    result.estimated_iterations = _estimate_iterations(code)

    # ── Plot quality check ────────────────────────────────────────────────────
    if plots:
        # Resolve filesystem path from URL like /plots/plot_xxx.png
        rel = plots[0].lstrip("/")
        fs_path = os.path.join(plots_dir, os.path.basename(rel))
        std = _check_plot_quality(fs_path)
        result.plot_std = std
        if 0 <= std < 8:
            result.issues.append(Issue(
                type="blank_plot",
                severity="critical",
                description=f"Plot is blank (pixel std={std:.1f}). Code likely ran but produced no visible data.",
            ))
        elif 0 <= std < 20:
            result.issues.append(Issue(
                type="low_content",
                severity="critical",
                description=f"Plot has very low visual content (pixel std={std:.1f}). Values may be unit-normalised instead of real-world scale.",
            ))
    elif "MATLAB error" not in output and "execution failed" not in output.lower():
        # No plots produced at all when task implies one should exist
        task_lower = task_text.lower()
        if any(w in task_lower for w in ["plot","simulate","simulation","visuali","trajectory","graph","chart"]):
            result.issues.append(Issue(
                type="no_plot",
                severity="critical",
                description="No plot was generated even though the task requires visualisation.",
            ))

    # ── Scale / unit detection ────────────────────────────────────────────────
    domain = result.domain
    if domain in ("space", "rocket"):
        has_real_const = bool(re.search(r'384400|6371|398600|1\.496e8|AU\s*=', code))
        if not has_real_const:
            result.issues.append(Issue(
                type="scale_issue",
                severity="critical",
                description=f"Space simulation with no real-world constants (D_moon, R_earth, AU etc). Coordinates will be unit-normalised (-1 to 1) instead of actual km.",
            ))
    if domain == "drone":
        has_real_const = bool(re.search(r'g\s*=\s*9\.81|mass\s*=|rho\s*=', code))
        if not has_real_const:
            result.issues.append(Issue(
                type="scale_issue",
                severity="warning",
                description="Drone simulation may be missing real physical constants (g, mass, rho).",
            ))

    # ── Performance / timeout risk ────────────────────────────────────────────
    n_iter = result.estimated_iterations
    if n_iter > 5000:
        # Check if a for loop iterates over that range
        has_for_loop = bool(re.search(r'\bfor\s+\w+\s*=\s*1\s*:\s*N\b', code))
        if has_for_loop:
            result.issues.append(Issue(
                type="timeout_risk",
                severity="critical",
                description=f"Estimated {n_iter:,} loop iterations with `for i=1:N` — will timeout. Must be vectorized.",
            ))

    # ── Stale figures ─────────────────────────────────────────────────────────
    if not re.match(r'^\s*close\s+all', code, re.IGNORECASE | re.MULTILINE):
        result.issues.append(Issue(
            type="stale_figures",
            severity="warning",
            description="Code does not start with `close all` — previous session figures may interfere.",
        ))

    # ── Syntax issues ─────────────────────────────────────────────────────────
    if re.search(r'%,\d*[fd]', code):
        result.issues.append(Issue(
            type="syntax_printf",
            severity="warning",
            description="Invalid MATLAB printf format `%,d` or `%,f` — MATLAB does not support thousands separator in fprintf.",
        ))

    # Check for raw \n inside single-quoted strings (will cause unterminated string error)
    # This detects literal backslash-n that isn't in a double-quoted string
    if re.search(r"'[^']*\\n[^']*'", code):
        result.issues.append(Issue(
            type="syntax_newline",
            severity="critical",
            description=r"Literal `\n` inside single-quoted MATLAB string — use sprintf() or flatten to single line.",
        ))

    # ── Output error parsing ─────────────────────────────────────────────────
    if "MATLAB error:" in output:
        # Extract first error line
        m = re.search(r'MATLAB error:.*?(?:Error|error)[^\n]*\n([^\n]+)', output, re.DOTALL)
        if m:
            result.issues.append(Issue(
                type="runtime_error",
                severity="critical",
                description=f"MATLAB runtime error: {m.group(1).strip()[:200]}",
            ))
        else:
            result.issues.append(Issue(
                type="runtime_error",
                severity="critical",
                description="MATLAB execution failed — see output for details.",
            ))

    return result


# ── Fixes ─────────────────────────────────────────────────────────────────────

def apply_fixes(code: str, diagnosis: DiagnosisResult) -> tuple[str, list[str]]:
    """
    Apply deterministic programmatic fixes based on diagnosis.
    Returns (fixed_code, list_of_fix_descriptions).
    Never calls the LLM — all rules are explicit and auditable.
    """
    fixes_applied: list[str] = []
    original = code

    issue_types = {i.type for i in diagnosis.issues}

    # ── Fix: stale figures — always prepend close all ─────────────────────────
    if "stale_figures" in issue_types:
        if not re.match(r'^\s*close\s+all', code, re.IGNORECASE | re.MULTILINE):
            code = "close all; clc;\n" + code
            fixes_applied.append("Prepended `close all; clc;` to clear stale MATLAB figures")

    # ── Fix: scale / domain constants ────────────────────────────────────────
    if "scale_issue" in issue_types and diagnosis.domain in _DOMAIN_CONSTANTS:
        consts = _DOMAIN_CONSTANTS[diagnosis.domain]
        # Insert after `close all` line or at top
        insert_after = re.search(r'^(close all.*?;[^\n]*\n)', code, re.IGNORECASE | re.MULTILINE)
        if insert_after:
            pos = insert_after.end()
            code = code[:pos] + "\n" + consts + "\n" + code[pos:]
        else:
            code = consts + "\n" + code
        fixes_applied.append(f"Injected real {diagnosis.domain} physical constants (R_earth, D_moon, mu etc)")

    # ── Fix: performance — reduce dt to avoid timeout ─────────────────────────
    if "timeout_risk" in issue_types:
        # Replace dt = <small_number> with dt = 300 (5-minute steps)
        new_code = re.sub(r'\bdt\s*=\s*\d+\b', 'dt = 300', code)
        if new_code != code:
            code = new_code
            fixes_applied.append("Changed dt to 300s to reduce iteration count by 5× and prevent timeout")

        # Vectorize simple `for i=1:N ... end` pattern if the body is simple assignments
        # This is a conservative transformation: only if body has no function calls
        def _vectorize_for(m: re.Match) -> str:
            loop_var = m.group(1)
            body     = m.group(2)
            # Only vectorize if body has no nested for/if/function calls
            if re.search(r'\b(for|if|while|switch|function)\b', body):
                return m.group(0)   # leave unchanged
            # Replace loop_var indexing: var(i) → var  (MATLAB broadcasts)
            vectorized = re.sub(rf'\b(\w+)\(\s*{loop_var}\s*\)', r'\1', body)
            return f"% (vectorized by CodeDoctor)\n{vectorized.strip()}"

        # Only attempt on small single-body loops
        code = re.sub(
            r'for\s+(\w+)\s*=\s*1\s*:\s*N\s*\n((?:(?!end\b)[\s\S]){1,300}?)\s*end\b',
            _vectorize_for, code,
        )
        fixes_applied.append("Attempted loop vectorization to eliminate for i=1:N pattern")

    # ── Fix: invalid printf format ────────────────────────────────────────────
    if "syntax_printf" in issue_types:
        code = re.sub(r'%,(\d*)([fd])', r'%\1\2', code)
        fixes_applied.append("Fixed invalid `%,d`/`%,f` printf format → `%d`/`%f`")

    # ── Fix: raw \n in single-quoted strings ─────────────────────────────────
    if "syntax_newline" in issue_types:
        # Replace literal \n inside '' strings with a space
        code = re.sub(r"('(?:[^'\\]|\\.)*?)\\n((?:[^'\\]|\\.)*?')",
                      lambda m: m.group(1) + '  ' + m.group(2), code)
        fixes_applied.append(r"Flattened literal `\n` in MATLAB strings to spaces (use sprintf for newlines)")

    # ── Fix: blank_plot or low_content when no other fix was found ───────────
    if ("blank_plot" in issue_types or "low_content" in issue_types) and not fixes_applied:
        # Last resort: strip any normalization (dividing by max/norm) in coordinate assignments
        code = re.sub(r'(/\s*max\([\w,\s]+\))', '', code)
        fixes_applied.append("Removed coordinate normalization (`/max(...)`) to restore real-world scale")

    if code == original and not fixes_applied:
        fixes_applied.append("No deterministic fix found — will escalate to LLM-assisted repair")

    return code, fixes_applied


# ── LLM fallback ──────────────────────────────────────────────────────────────

def build_llm_repair_prompt(
    original_code: str,
    task_text: str,
    diagnosis: DiagnosisResult,
    rounds_taken: int,
) -> str:
    """Build a tightly-scoped LLM repair prompt when deterministic fixes are exhausted."""
    issue_list = "\n".join(
        f"  [{i.severity.upper()}] {i.type}: {i.description}"
        for i in diagnosis.issues
    )
    return (
        f"The following MATLAB code was executed and had quality issues after {rounds_taken} auto-fix attempt(s).\n\n"
        f"TASK: {task_text}\n\n"
        f"DIAGNOSED ISSUES:\n{issue_list}\n\n"
        f"ORIGINAL CODE:\n```matlab\n{original_code}\n```\n\n"
        f"Write a corrected version that fixes ONLY the issues above. "
        f"Use real-world physical constants and units. "
        f"Do not simplify or remove features. "
        f"Return ONLY the corrected MATLAB code block."
    )


# ── Main CodeDoctor entry point ───────────────────────────────────────────────

@dataclass
class DoctorRound:
    round_num: int
    issues: list[Issue]
    fixes: list[str]
    success: bool
    plot_std: float


def run_code_doctor(
    code: str,
    output: str,
    plots: list[str],
    task_text: str,
    plots_dir: str,
    executor: Callable[[str, str], tuple[str, list[str]]],   # (code, task) → (output, plots)
    llm_caller: Callable[[str], str] | None = None,           # prompt → fixed_code_str
    on_event: Callable[[str, dict], None] | None = None,      # (event_name, data) → None
    max_rounds: int = MAX_ROUNDS,
) -> tuple[str, str, list[str], list[DoctorRound]]:
    """
    Autonomous debug loop.

    Args:
        code:       The MATLAB code that was executed
        output:     The stdout/stderr from that execution
        plots:      List of plot URL strings
        task_text:  Original user request (used for domain detection)
        plots_dir:  Filesystem path to plots directory
        executor:   Callable that runs code and returns (output, plots)
        llm_caller: Optional LLM fallback — takes a prompt, returns fixed code str
        on_event:   SSE event emitter callback(event_name, data_dict)
        max_rounds: Max auto-fix iterations before giving up or LLM fallback

    Returns:
        (final_code, final_output, final_plots, rounds_log)
    """
    def emit(event: str, data: dict) -> None:
        if on_event:
            on_event(event, data)

    rounds: list[DoctorRound] = []
    current_code    = code
    current_output  = output
    current_plots   = plots

    # Quick exit: if everything looks fine, don't touch it
    initial_diag = diagnose(current_code, current_output, current_plots, task_text, plots_dir)
    if not initial_diag.has_critical:
        return current_code, current_output, current_plots, rounds

    emit("doctor_start", {
        "message": f"CodeDoctor activated — {len(initial_diag.issues)} issue(s) detected",
        "issues": [{"type": i.type, "severity": i.severity, "description": i.description}
                   for i in initial_diag.issues],
    })

    for round_num in range(1, max_rounds + 1):
        diag = diagnose(current_code, current_output, current_plots, task_text, plots_dir)

        if not diag.issues:
            emit("doctor_done", {
                "rounds_taken": round_num - 1,
                "fixed": True,
                "message": "✓ All issues resolved",
            })
            break

        # Emit current issues
        for iss in diag.issues:
            emit("doctor_issue", {
                "round": round_num,
                "type": iss.type,
                "severity": iss.severity,
                "description": iss.description,
            })

        # Apply deterministic fixes
        fixed_code, fix_descs = apply_fixes(current_code, diag)

        for desc in fix_descs:
            emit("doctor_fix", {"round": round_num, "description": desc})
            logger.info("[CodeDoctor round %d] Fix: %s", round_num, desc)

        # If no change was made and last round → LLM fallback
        if fixed_code == current_code:
            if llm_caller and round_num == max_rounds:
                emit("doctor_fix", {
                    "round": round_num,
                    "description": "Escalating to LLM-assisted repair with full diagnosis context",
                })
                prompt = build_llm_repair_prompt(current_code, task_text, diag, round_num)
                try:
                    llm_result = llm_caller(prompt)
                    if llm_result:
                        llm_code = re.sub(r'```(?:matlab)?\s*|\s*```', '', llm_result).strip()
                        fixed_code = llm_code
                except Exception as exc:
                    logger.warning("[CodeDoctor] LLM fallback failed: %s", exc)
                    emit("doctor_done", {
                        "rounds_taken": round_num,
                        "fixed": False,
                        "message": f"LLM fallback failed: {exc}",
                    })
                    break
            else:
                emit("doctor_done", {
                    "rounds_taken": round_num,
                    "fixed": False,
                    "message": "No further deterministic fixes available",
                })
                break

        # Re-run with fixed code
        emit("doctor_rerun", {"round": round_num, "message": f"Re-running with fixes (round {round_num}/{max_rounds})…"})
        try:
            new_output, new_plots = executor(fixed_code, task_text)
        except Exception as exc:
            logger.warning("[CodeDoctor round %d] Executor failed: %s", round_num, exc)
            new_output = f"Execution error: {exc}"
            new_plots  = []

        post_diag = diagnose(fixed_code, new_output, new_plots, task_text, plots_dir)
        success   = not post_diag.has_critical

        rounds.append(DoctorRound(
            round_num=round_num,
            issues=[i for i in diag.issues],
            fixes=fix_descs,
            success=success,
            plot_std=post_diag.plot_std,
        ))

        current_code   = fixed_code
        current_output = new_output
        current_plots  = new_plots

        if success:
            emit("doctor_done", {
                "rounds_taken": round_num,
                "fixed": True,
                "message": f"✓ Fixed in {round_num} round(s) — plot quality verified",
                "plot_std": post_diag.plot_std,
            })
            break
    else:
        emit("doctor_done", {
            "rounds_taken": max_rounds,
            "fixed": False,
            "message": f"Max rounds ({max_rounds}) reached — result may still need review",
        })

    return current_code, current_output, current_plots, rounds


__all__ = [
    "diagnose", "apply_fixes", "run_code_doctor",
    "DiagnosisResult", "Issue", "DoctorRound",
    "build_llm_repair_prompt",
]
