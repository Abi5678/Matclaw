"""
MatClaw MCP Server: exposes MATLAB bridge, skills, and RPI tools to LLM clients.

Tools:
- run_matlab_code: Execute MATLAB code string, return output.
- trigger_skill: Map natural language (e.g. "optimize PID") to skills and run.
- run_workspace_auditor, run_pid_optimizer, run_report_generator: Direct skill invocation.

All tool outputs are logged to LAB_JOURNAL.md via the shared lab journal.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from src.matclaw.config.base_config import MatClawSettings
from src.matclaw.config.logging_config import configure_logging
from src.matclaw.core.rpi_executor import RPIExecutor
from src.matclaw.lab_journal import append_lab_journal
from src.matclaw.matlab.matlab_bridge import MatlabBridge
from src.matclaw.memory.memory import MemoryStore
from src.matclaw.memory.memory_manager import MemoryManager
from src.matclaw.skills import get_skill_instructions, list_skills, load_skill_logic

logger = logging.getLogger(__name__)

# Natural language -> skill name mapping for trigger_skill
NL_TO_SKILL: dict[str, str] = {
    "optimize pid": "pid_optimizer",
    "pid optimization": "pid_optimizer",
    "tune pid": "pid_optimizer",
    "pid tuning": "pid_optimizer",
    "tune gains": "pid_optimizer",
    "optimize gains": "pid_optimizer",
    "audit workspace": "workspace_auditor",
    "workspace audit": "workspace_auditor",
    "check memory": "workspace_auditor",
    "check workspace": "workspace_auditor",
    "memory audit": "workspace_auditor",
    "generate report": "report_generator",
    "report": "report_generator",
    "project report": "report_generator",
    "simulate": "simulink_runner",
    "run model": "simulink_runner",
    "simulink": "simulink_runner",
}


@dataclass
class MatClawContext:
    """Application context with MATLAB bridge, RPI executor, and lab journal path."""

    matlab_bridge: MatlabBridge
    memory_manager: MemoryManager
    rpi_executor: RPIExecutor
    journal_path: Path


def _get_app_ctx(ctx: Context[ServerSession, MatClawContext]) -> MatClawContext:
    """Extract MatClawContext from MCP context."""
    return ctx.request_context.lifespan_context


def _log_to_journal(app_ctx: MatClawContext, summary: str, artifact_paths: list[str] | None = None) -> None:
    """Append tool result to LAB_JOURNAL.md."""
    if app_ctx.journal_path:
        append_lab_journal(
            app_ctx.journal_path,
            summary,
            source="MCP",
            artifact_paths=artifact_paths or [],
        )


def _resolve_skill_from_nl(request: str) -> str | None:
    """Map natural language request to skill name."""
    lower = request.lower().strip()
    # Exact match
    if lower in NL_TO_SKILL:
        return NL_TO_SKILL[lower]
    # Substring match
    for phrase, skill in NL_TO_SKILL.items():
        if phrase in lower:
            return skill
    # Keyword match
    if any(w in lower for w in ("pid", "tune", "optimize", "gains")):
        return "pid_optimizer"
    if any(w in lower for w in ("audit", "workspace", "memory", "variables")):
        return "workspace_auditor"
    if any(w in lower for w in ("report", "summary")):
        return "report_generator"
    if any(w in lower for w in ("simulate", "simulink", "run model", ".slx")):
        return "simulink_runner"
    return None


@asynccontextmanager
async def matclaw_lifespan(server: FastMCP):
    """Initialize MATLAB bridge, memory, RPI executor on startup."""
    settings = MatClawSettings()
    configure_logging(settings.logging)

    matlab_bridge = MatlabBridge(settings=settings.matlab)
    memory_store = MemoryStore(settings=settings.memory)
    memory_manager = MemoryManager(persist_directory=".matclaw_chromadb")

    memory_store.initialize()
    matlab_bridge.start()

    rpi_executor = RPIExecutor(
        matlab_bridge=matlab_bridge,
        memory_manager=memory_manager,
    )

    journal_path = Path(settings.lab_journal.path).resolve() if settings.lab_journal.enabled else None
    if journal_path and not journal_path.is_file():
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_text(
            "# MatClaw Lab Journal\n\nAuto-generated log of MCP and sentry-triggered runs.\n\n",
            encoding="utf-8",
        )

    app_ctx = MatClawContext(
        matlab_bridge=matlab_bridge,
        memory_manager=memory_manager,
        rpi_executor=rpi_executor,
        journal_path=journal_path or Path("LAB_JOURNAL.md"),
    )

    logger.info("MatClaw MCP server initialized with %d tools.", len(list_skills()))

    try:
        yield app_ctx
    finally:
        matlab_bridge.stop()
        memory_store.close()
        logger.info("MatClaw MCP server shutdown complete.")


mcp = FastMCP(
    "MatClaw",
    json_response=True,
    lifespan=matclaw_lifespan,
    host="127.0.0.1",
    port=9901,
    streamable_http_path="/mcp",
)


@mcp.tool()
def run_matlab_code(code: str, ctx: Context[ServerSession, MatClawContext]) -> str:
    """
    Execute a string of MATLAB code and return the output.

    Use evalc internally to capture disp() and printed output. For expressions
    that return a value, end your code with a variable assignment or disp(result).
    Example: "x = roots([1 -5 6]); disp(x)"
    """
    app_ctx = _get_app_ctx(ctx)
    success, output = app_ctx.matlab_bridge.run_matlab_code(code)
    summary = f"run_matlab_code: {'OK' if success else 'FAIL'} — {output[:200]}..."
    _log_to_journal(app_ctx, summary)
    if success:
        return output
    return f"Error: {output}"


@mcp.tool()
def trigger_skill(request: str, ctx: Context[ServerSession, MatClawContext]) -> str:
    """
    Map a natural language request to a skill and run it.

    Examples:
    - "optimize PID" or "tune pid" -> pid_optimizer
    - "audit workspace" or "check memory" -> workspace_auditor
    - "generate report" or "report" -> report_generator

    Returns the skill output or an error message.
    """
    app_ctx = _get_app_ctx(ctx)
    skill_name = _resolve_skill_from_nl(request)
    if not skill_name:
        available = ", ".join(list_skills())
        msg = f"No skill matched '{request}'. Available: {available}"
        _log_to_journal(app_ctx, f"trigger_skill: no match — {request}")
        return msg

    return _run_skill_and_log(app_ctx, skill_name, f"trigger_skill:{request}")


def _run_skill_and_log(
    app_ctx: MatClawContext,
    skill_name: str,
    log_source: str,
    skill_kwargs: dict | None = None,
) -> str:
    """Run a skill via RPI executor and log to lab journal."""
    try:
        kwargs = skill_kwargs or {}
        if skill_name == "simulink_runner":
            # Try to extract model_name from log_source (e.g. "trigger_skill:simulate flight_control")
            import re
            req = log_source.replace("trigger_skill:", "").strip().lower()
            m = re.search(r"simulate\s+(\w+)", req) or re.search(r"run\s+(\w+)(?:\s+for|\s+model)?", req)
            if m and not kwargs.get("model_name"):
                kwargs["model_name"] = m.group(1)
            if not kwargs.get("model_name"):
                return "Simulink runner requires model_name. Use run_simulink_runner(model_name='...', stop_time=10) or say 'simulate flight_control'."
        result = app_ctx.rpi_executor.run_rpi(skill_name, **kwargs)
        if isinstance(result, dict) and result.get("pending"):
            return f"Approval requested: {result.get('message', 'Proceed?')}"

        success = getattr(result, "success", False)
        msg = getattr(result, "message", str(result))
        data = getattr(result, "data", None) or {}
        artifact_paths: list[str] = []
        if data.get("report_path"):
            artifact_paths.append(data["report_path"])

        summary = f"{log_source} → {skill_name}: {'OK' if success else 'FAIL'} — {msg[:150]}"
        _log_to_journal(app_ctx, summary, artifact_paths=artifact_paths)

        if success:
            return msg
        return f"Error: {getattr(result, 'error', msg)}"
    except Exception as exc:
        logger.exception("Skill %s failed: %s", skill_name, exc)
        _log_to_journal(app_ctx, f"{log_source} → {skill_name}: ERROR — {exc}")
        return f"Error: {exc}"


@mcp.tool()
def run_workspace_auditor(ctx: Context[ServerSession, MatClawContext]) -> str:
    """
    Run the workspace auditor skill: scan MATLAB workspace and license state.

    Returns variable list, total bytes, license info, and any OOM/license warnings.
    """
    app_ctx = _get_app_ctx(ctx)
    return _run_skill_and_log(app_ctx, "workspace_auditor", "run_workspace_auditor")


@mcp.tool()
def run_pid_optimizer(
    ctx: Context[ServerSession, MatClawContext],
    Kp: float = 1.0,
    Ki: float = 0.5,
    Kd: float = 0.1,
) -> str:
    """
    Run the PID optimizer skill: tune Kp, Ki, Kd for a 2nd-order plant.

    Uses an observation loop to reach target rise time and overshoot.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        result = app_ctx.rpi_executor.run_rpi(
            "pid_optimizer",
            skill_kwargs={"Kp": Kp, "Ki": Ki, "Kd": Kd},
        )
        if isinstance(result, dict) and result.get("pending"):
            return f"Approval requested: {result.get('message', 'Proceed?')}"

        success = getattr(result, "success", False)
        msg = getattr(result, "message", str(result))
        data = getattr(result, "data", None) or {}
        artifact_paths = [str(p) for p in data.get("artifact_paths", []) if p]

        summary = f"run_pid_optimizer: {'OK' if success else 'FAIL'} — {msg[:150]}"
        _log_to_journal(app_ctx, summary, artifact_paths=artifact_paths)

        if success:
            return msg
        return f"Error: {getattr(result, 'error', msg)}"
    except Exception as exc:
        logger.exception("PID optimizer failed: %s", exc)
        _log_to_journal(app_ctx, f"run_pid_optimizer: ERROR — {exc}")
        return f"Error: {exc}"


@mcp.tool()
def run_simulink_runner(
    ctx: Context[ServerSession, MatClawContext],
    model_name: str,
    stop_time: float = 10.0,
) -> str:
    """
    Run a Simulink (.slx) model simulation. Load-Compile-Run pattern.

    Checks license, loads model, sets StopTime, runs sim().
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        result = app_ctx.rpi_executor.run_rpi(
            "simulink_runner",
            skill_kwargs={"model_name": model_name, "stop_time": stop_time},
        )
        if isinstance(result, dict) and result.get("pending"):
            return f"Approval requested: {result.get('message', 'Proceed?')}"

        success = getattr(result, "success", False)
        msg = getattr(result, "message", str(result))
        data = getattr(result, "data", None) or {}

        summary = f"run_simulink_runner: {'OK' if success else 'FAIL'} — {msg[:150]}"
        _log_to_journal(app_ctx, summary)

        if success:
            return msg
        return f"Error: {getattr(result, 'error', msg)}"
    except Exception as exc:
        logger.exception("Simulink runner failed: %s", exc)
        _log_to_journal(app_ctx, f"run_simulink_runner: ERROR — {exc}")
        return f"Error: {exc}"


@mcp.tool()
def run_report_generator(
    ctx: Context[ServerSession, MatClawContext],
    project_id: str = "report",
) -> str:
    """
    Run the report generator skill: compile lessons learned + latest plot into Markdown.

    Optionally sends the report via Telegram if configured.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        mod = load_skill_logic("report_generator")
        if not mod or not hasattr(mod, "run"):
            return "Report generator skill not available."
        result = mod.run(
            app_ctx.matlab_bridge,
            memory_manager=app_ctx.memory_manager,
            telegram_handler=None,
            project_id=project_id,
        )
        success = getattr(result, "success", False)
        msg = getattr(result, "message", str(result))
        data = getattr(result, "data", None) or {}
        artifact_paths = [data["report_path"]] if data.get("report_path") else []

        summary = f"run_report_generator: {'OK' if success else 'FAIL'} — {msg[:150]}"
        _log_to_journal(app_ctx, summary, artifact_paths=artifact_paths)

        if success:
            return msg
        return f"Error: {getattr(result, 'error', msg)}"
    except Exception as exc:
        logger.exception("Report generator failed: %s", exc)
        _log_to_journal(app_ctx, f"run_report_generator: ERROR — {exc}")
        return f"Error: {exc}"


@mcp.tool()
def list_available_skills(ctx: Context[ServerSession, MatClawContext]) -> str:
    """
    List all installed MatClaw skills with their descriptions.

    Use this to discover which skills are available before calling trigger_skill.
    """
    skills = list_skills()
    lines = []
    for name in skills:
        instr = get_skill_instructions(name) or ""
        first_line = next((l.strip() for l in instr.split("\n") if l.strip() and not l.startswith("#")), "")[:80]
        lines.append(f"- **{name}**: {first_line}")
    return "\n".join(lines) if lines else "No skills installed."


def main() -> None:
    """Run the MCP server with stdio transport (default for CLI clients)."""
    mcp.run(transport="stdio")


def run_http() -> None:
    """Run the MCP server with streamable-http for NAT/LLM clients (port 9901)."""
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    import sys
    if "--http" in sys.argv or "-H" in sys.argv:
        run_http()
    else:
        main()
