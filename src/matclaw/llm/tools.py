"""
Structured tool definitions for Nemotron (OpenAI-compatible function calling).
Don't let Nemotron guess — provide explicit tools it can call.
"""

from __future__ import annotations

from src.matclaw.skills import list_skills

# OpenAI-compatible tool schema for Nemotron (Function Calling)
# Intent mapping: "What happened last time?" -> query_memory | "Is MATLAB running?" -> workspace_auditor | "Plot y=sin(x)" -> run_matlab
MATCLAW_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_matlab",
            "description": "Write and run MATLAB code. Use for: plots (e.g. 'Plot y=sin(x)'), computations, simulations, Simulink. Triggers: plot, graph, compute, simulate, draw, run code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Valid MATLAB code to execute (e.g. figure; plot(x,y); title('My Plot');)",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_auditor",
            "description": "Check MATLAB engine and workspace. Use when user asks: 'Is MATLAB running?', 'Check workspace', 'Audit variables', 'List workspace variables'. Returns variable list, total bytes, license status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pid_optimizer",
            "description": "Run the PID Optimizer skill: tune Kp, Ki, Kd gains for a 2nd-order plant via observation loop. Use when user asks to tune PID, optimize gains, or run PID optimization.",
            "parameters": {
                "type": "object",
                "properties": {
                    "Kp": {"type": "number", "description": "Initial proportional gain", "default": 1.0},
                    "Ki": {"type": "number", "description": "Initial integral gain", "default": 0.5},
                    "Kd": {"type": "number", "description": "Initial derivative gain", "default": 0.1},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_generator",
            "description": "Run the Report Generator skill: generate a project report from recent artifacts and parameters.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulink_runner",
            "description": "Run a Simulink (.slx) model simulation. Use when user says 'simulate [model]', 'run model', 'run flight_control for 20 seconds', or mentions a .slx file. Load-Compile-Run pattern: checks license, loads model, sets StopTime, runs sim().",
            "parameters": {
                "type": "object",
                "properties": {
                    "model_name": {
                        "type": "string",
                        "description": "Name of the .slx model (with or without extension, e.g. flight_control or flight_control.slx)",
                    },
                    "stop_time": {
                        "type": "number",
                        "description": "Simulation stop time in seconds",
                        "default": 10.0,
                    },
                },
                "required": ["model_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_file",
            "description": "Read, analyze, fix, and/or run a MATLAB .m file. Use when user mentions a .m filename and wants to: check it, fix it, run it, debug it, or analyze it. Triggers: 'check test.m', 'fix controller.m', 'run my_script.m', 'analyze pid_eval.m', 'fix test.m and run it', 'can you access test.m'. Do NOT use run_matlab for .m file operations — use this tool instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the .m file (e.g. 'test.m', 'matlab/controller.m')",
                    },
                    "action": {
                        "type": "string",
                        "description": "What to do: 'analyze' (diagnose only), 'fix' (diagnose + patch), 'run' (just execute), 'fix_and_run' (full pipeline). Default: 'fix_and_run'.",
                        "enum": ["analyze", "fix", "run", "fix_and_run"],
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_memory",
            "description": "Search memory for past results. Use when user asks: 'What happened last time?', 'Previous gains?', 'What did we do?', 'Last run?', 'Past results'. Do NOT use for plots or code execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def get_tools_for_nemotron() -> list[dict]:
    """Return tool definitions, optionally filtered by available skills."""
    available = set(list_skills())
    tools = []
    for t in MATCLAW_TOOLS:
        name = t["function"]["name"]
        if name in ("run_matlab", "query_memory", "analyze_file"):
            tools.append(t)
        elif name in available:
            tools.append(t)
    return tools


def tool_name_to_skill(tool_name: str) -> str | None:
    """Map tool/function name to skill name (for trigger_skill compatibility)."""
    if tool_name in ("workspace_auditor", "pid_optimizer", "report_generator", "simulink_runner"):
        return tool_name
    return None
