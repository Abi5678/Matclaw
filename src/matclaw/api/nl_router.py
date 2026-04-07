"""
Natural language → skill router.
Maps intent keywords/phrases to (skill_name, extra_kwargs).

Keywords are matched as full words (word-boundary search) to avoid false
positives from substring matching (e.g. "run" matching "grunting").
Multi-word phrases are matched as-is (substring).
"""
from __future__ import annotations
import re
from functools import lru_cache

# ── intent → skill mapping ───────────────────────────────────────────────────
# Each entry is (set_of_keywords_or_phrases, skill_name).
NL_MAP: list[tuple[set[str], str]] = [
    # query_memory — recall / search past experiments
    ({"remember", "recall", "memory", "what did", "last time", "previous",
      "recently", "recent runs", "history", "past experiment", "when did",
      "which run", "best result", "find experiment", "search memory",
      "what was", "what were", "journal", "ran recently",
      "run recently", "did i run"}, "query_memory"),

    # run_python — Python execution
    ({"python script", "python code", "run python", "execute python",
      "write python", "import pandas", "import numpy", "import torch",
      "pip install", "virtualenv", "django", "flask", "fastapi",
      "machine learning", "scikit", "sklearn", "tensorflow", "pytorch",
      "data science", "jupyter", "notebook"}, "run_python"),

    # run_shell — shell / terminal commands
    ({"bash script", "shell command", "terminal command", "run shell",
      "run bash", "chmod", "chown", "grep -r", "find .", "ls -la",
      "ssh ", "rsync", "curl ", "wget ", "cat /", "echo $",
      "cron", "systemctl", "docker run", "kubectl"}, "run_shell"),

    # run_matlab — compute / plot / draw / simulate (removed overly broad single words)
    ({"plot", "draw", "graph", "chart", "visuali", "surf", "mesh", "scatter",
      "histogram", "bar chart", "pie chart", "contour", "heatmap",
      "animation", "animate", "gif", "lorenz", "3d surface",
      "compute", "calculate", "run matlab", "execute matlab", "solve",
      "integrate", "differentiate", "fft", "filter design",
      "simulate", "ode45", "ode23", "matrix", "eigenvalue",
      "disp(", "show me", "create array",
      "fibonacci", "find roots"}, "run_matlab"),

    # pid_optimizer
    ({"pid", "tune controller", "ziegler", "imc tuning", "settling time",
      "overshoot", "rise time", "closed loop", "feedback control",
      "proportional integral", "pid controller"}, "pid_optimizer"),

    # signal_analyzer
    ({"signal analysis", "fft", "spectrum", "frequency response", "noise filter",
      "snr", "butterworth", "bandpass", "lowpass", "highpass", "filter design",
      "spectral", "waveform", "sampling rate", "nyquist"}, "signal_analyzer"),

    # workspace_auditor
    ({"workspace", "audit", "inspect variables", "what's in",
      "list variables", "whos", "workspace health",
      "check workspace"}, "workspace_auditor"),

    # code_reviewer
    ({"review", "code review", "check my code", "improve code",
      "optimize code", "refactor", "clean up", "lint"}, "code_reviewer"),

    # report_generator
    ({"report", "write up", "generate report",
      "pdf export", "export results"}, "report_generator"),

    # simulink_runner
    ({"simulink", "block diagram", "simulation model",
      "slx", "mdl", "run model", "sim("}, "simulink_runner"),

    # file_doctor
    ({"mat file", "corrupt", "repair file", "recover file", "broken file",
      "fix file", "load error"}, "file_doctor"),

    # gitlab_reporter
    ({"gitlab", "merge request", "bug report",
      "create ticket", "commit message"}, "gitlab_reporter"),
]


@lru_cache(maxsize=256)
def _compile_keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Compile a keyword into a word-boundary regex for accurate matching."""
    escaped = re.escape(keyword)
    if " " in keyword or keyword.endswith("("):
        return re.compile(escaped, re.IGNORECASE)
    return re.compile(rf"\b{escaped}\b", re.IGNORECASE)


def route_nl_message(text: str) -> tuple[str, dict]:
    """
    Return (skill_name, extra_kwargs) for the given NL text.
    Falls back to 'run_matlab' for compute/ambiguous requests,
    then 'chat' as the last resort.
    """
    lower = text.lower()

    for keywords, skill in NL_MAP:
        if any(_compile_keyword_pattern(kw).search(lower) for kw in keywords):
            return skill, {}

    # project generation hints
    project_hints = {"create a project", "build a game", "make a game", "make a project",
                     "generate project", "snake game", "tic tac toe", "pong game",
                     "dashboard app", "web app", "flask app", "html page"}
    if any(h in lower for h in project_hints):
        return "project_gen", {}

    # Compute-hinting phrases (multi-word to avoid false positives)
    compute_hints = {"run this", "execute this", "compute this", "calculate this",
                     "show me how", "give me a plot", "plot this"}
    if any(h in lower for h in compute_hints):
        return "run_matlab", {}

    # Default: treat as conversation
    return "chat", {}
