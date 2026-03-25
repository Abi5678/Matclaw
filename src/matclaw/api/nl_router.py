"""
Natural language → skill router.
Maps intent keywords/phrases to (skill_name, extra_kwargs).
"""
from __future__ import annotations
import re

# ── intent → skill mapping ───────────────────────────────────────────────────
NL_MAP: list[tuple[set[str], str]] = [
    # query_memory — recall / search past experiments (must come before run_matlab
    # so "what did I run recently?" doesn't match "run" → run_matlab first)
    ({"remember", "recall", "memory", "what did", "last time", "previous",
      "recently", "recent runs", "history", "past", "experiment", "when did",
      "which run", "best result", "find experiment", "search memory",
      "what was", "what were", "log", "journal", "ran recently",
      "run recently", "did i run"}, "query_memory"),

    # run_matlab — compute / plot / draw / simulate
    ({"plot", "draw", "graph", "chart", "visuali", "surf", "mesh", "scatter",
      "histogram", "bar chart", "pie chart", "contour", "heatmap",
      "animation", "animate", "gif", "lorenz", "3d", "surface",
      "compute", "calculate", "eval", "run", "execute", "solve",
      "integrate", "differentiate", "fft", "filter",
      "simulate", "ode", "matrix", "array", "vector",
      "print", "disp", "show me", "generate", "create array",
      "fibonacci", "sort", "find roots", "eigenvalue"}, "run_matlab"),

    # pid_optimizer
    ({"pid", "tune", "controller", "ziegler", "imc", "settling time",
      "overshoot", "rise time", "closed.loop", "feedback control",
      "proportional", "integral", "derivative"}, "pid_optimizer"),

    # signal_analyzer
    ({"signal", "fft", "spectrum", "frequency", "noise", "snr",
      "butterworth", "bandpass", "lowpass", "highpass", "filter design",
      "spectral", "waveform", "sampling", "nyquist"}, "signal_analyzer"),

    # workspace_auditor
    ({"workspace", "audit", "variable", "inspect", "what's in",
      "list variables", "whos", "workspace health", "undefined",
      "check workspace"}, "workspace_auditor"),

    # code_reviewer
    ({"review", "code review", "check my code", "improve code",
      "optimize code", "refactor", "clean up", "lint"}, "code_reviewer"),

    # report_generator
    ({"report", "summary", "document", "write up", "generate report",
      "pdf", "export results", "compile"}, "report_generator"),

    # simulink_runner
    ({"simulink", "block diagram", "model", "simulation model",
      "slx", "mdl", "run model", "sim("}, "simulink_runner"),

    # file_doctor
    ({"mat file", "corrupt", "repair", "recover", "broken file",
      "fix file", "load error"}, "file_doctor"),

    # gitlab_reporter
    ({"gitlab", "issue", "merge request", "mr ", "bug report",
      "create ticket", "commit message"}, "gitlab_reporter"),
]


def route_nl_message(text: str) -> tuple[str, dict]:
    """
    Return (skill_name, extra_kwargs) for the given NL text.
    Falls back to 'run_matlab' for compute/ambiguous requests,
    then 'workspace_auditor' as the last resort.
    """
    lower = text.lower()

    for keywords, skill in NL_MAP:
        if any(kw in lower for kw in keywords):
            return skill, {}

    # keyword-set fallback: strong compute hints
    compute_hints = {"write", "make", "do", "perform", "show", "give me", "try"}
    if any(h in lower for h in compute_hints):
        return "run_matlab", {}

    return "workspace_auditor", {}
