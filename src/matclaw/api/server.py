"""
MatClaw FastAPI backend — serves the React Control Plane UI.
Endpoints:
  POST /api/run          — NL → skill dispatch
  GET  /api/skills       — list available skills
  GET  /api/plots        — list saved plot files
  GET  /plots/{filename} — serve a plot image / GIF
  GET  /health           — liveness + matlab session (matlab) and busy (matlab_busy)
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import pty
from fastapi import WebSocket, WebSocketDisconnect

# ── path bootstrap ──────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from matclaw.config.base_config import MatClawSettings
from matclaw.matlab.matlab_bridge import MatlabBridge
from matclaw.memory.memory_manager import MemoryManager
from matclaw.core.experiment import ExperimentTracker
from matclaw.llm.llm_client import call_chat_completion, call_chat_completion_stream, _extract_code_from_reasoning
from matclaw.skills import list_skills, load_skill_logic
from matclaw.api.nl_router import route_nl_message
from matclaw.api.demos import find_demo
from matclaw.agents.registry import AgentRegistry, AgentDefinition
from matclaw.core.static_analyzer import StaticAnalyzer
from matclaw.memory.episodic_memory import EpisodicMemoryManager, Episode
from matclaw.core.state_manager import AsyncStateTracker, ExecutionState
from matclaw.memory.session_store import SessionStore
from matclaw.core.pipeline_store import PipelineStore
from matclaw.core.task_router import TaskRouter, pipeline_to_dag_plan
from matclaw.core.runtimes import RuntimeRegistry, MatlabRuntime, PythonRuntime, ShellRuntime
from matclaw.core.scheduler import Scheduler, ScheduledTask
from matclaw.core.heartbeat import Heartbeat
from matclaw.core.job_manager import JobManager
from matclaw.api.auth import APIKeyStore
from matclaw.api.middleware import EnterpriseMiddleware, MetricsStore
from matclaw.tools.registry import tool_registry
from matclaw.core.agentic_loop import run_agentic_loop
from matclaw.core.code_doctor import run_code_doctor
from matclaw.gateways.webhook import WebhookRequest, WebhookResponse

import uuid
from datetime import datetime
from contextlib import asynccontextmanager, suppress

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── global singletons ───────────────────────────────────────────────────────
settings = MatClawSettings()
bridge = MatlabBridge(settings=settings.matlab)
memory = MemoryManager(persist_directory=str(ROOT / ".matclaw_chromadb"))
tracker = ExperimentTracker(str(ROOT / ".matclaw_experiments.sqlite3"))
analyzer = StaticAnalyzer(bridge)
episodic_memory = EpisodicMemoryManager(str(ROOT / ".matclaw_episodic.sqlite3"))
state_tracker = AsyncStateTracker(str(ROOT / ".matclaw_async_state.json"))
session_store = SessionStore(str(ROOT / ".matclaw_sessions.sqlite3"))
pipeline_store = PipelineStore(str(ROOT / ".matclaw_pipelines.sqlite3"))
_active_runs: dict[str, asyncio.Queue] = {}  # run_id → SSE event queue
_headless_figure_task: asyncio.Task[None] | None = None  # deferred MATLAB GUI setup (must not block lifespan yield)

# ── Enterprise singletons ────────────────────────────────────────────────────
_key_store = APIKeyStore(str(ROOT / ".matclaw_auth.sqlite3"))
_metrics = MetricsStore()
_job_manager = JobManager(concurrency=2)
_agentic_slot_semaphore = asyncio.Semaphore(max(1, settings.production.agentic_max_concurrent))

# Runtime registry — populated after MATLAB bridge starts (see startup below)
_runtime_registry = RuntimeRegistry()

_scheduler = Scheduler(
    db_path=str(ROOT / ".matclaw_scheduler.sqlite3"),
    job_manager=_job_manager,
    runtime_registry=_runtime_registry,
    heartbeat_seconds=settings.daemon.heartbeat_interval_seconds,
)
_heartbeat = Heartbeat(scheduler=_scheduler, job_manager=_job_manager)

import json

# ── MatClaw Persona ────────────────────────────────────────────────────────
MATCLAW_PERSONA = """\
You are MatClaw, a brilliant and friendly MATLAB & engineering AI assistant.

Personality:
- Warm, enthusiastic about engineering — like a favorite TA who genuinely loves the subject
- Use casual but professional language. Say "Let me" not "I will proceed to"
- Show excitement for cool visualizations: "Oh nice, this is going to look great!"
- When something fails, be honest and helpful: "Hmm, that didn't work — let me try another approach."
- Reference earlier conversation naturally: "Building on that surface plot from earlier..."

Communication style:
- Always explain what you're about to do BEFORE showing code/results
- After execution, summarize what happened and what the user should notice
- Use short paragraphs, not walls of text
- Format responses with markdown: **bold** for emphasis, `code` for inline references, code blocks for snippets

You have access to these actions:
- "run_matlab": Execute MATLAB code (plots, simulations, animations, computations)
- "run_python": Execute Python code (data science, scripting, pandas/numpy/matplotlib tasks)
- "run_shell": Execute a shell/bash command (file ops, system info, CLI tools)
- "project_gen": Create multi-file projects in any language (MATLAB, Python, HTML, etc.)
- "query_memory": Recall past runs and experiments
- "multi_agent_swarm": Break a complex task into a DAG and delegate to specialized agents.
- "none": Just have a conversation

Given the user's message and conversation history, respond with ONLY a valid JSON object (no markdown fences):
{
  "reply": "Your warm, conversational response. Use markdown formatting.",
  "action": "run_matlab" | "run_python" | "run_shell" | "project_gen" | "query_memory" | "multi_agent_swarm" | "none",
  "code": "Raw code for the chosen runtime. null for non-execution actions.",
  "project": {"name": "project_name", "description": "...", "files": [{"filename": "main.m", "language": "matlab", "content": "..."}]} or null,
  "dag_plan": {"nodes": [{"id":"node_1", "description":"do x", "inputs":[], "outputs":["data"], "agent_id":"agent-id"}]} or null
}

Runtime selection rules (STRICT — never override these):
- Any request mentioning plot / graph / visualize / simulate / compute / calculate / solve / matrix / fft / ode / surf / mesh / contour / animate → ALWAYS use run_matlab
- Python/pandas/numpy/ML/sklearn/torch keywords → ALWAYS use run_python
- Shell/bash/ls/curl/grep/system keywords → ALWAYS use run_shell
- Use "none" ONLY for pure Q&A with NO computation requested (e.g. "what is a PID controller?")
- When in doubt between run_matlab and none → use run_matlab

IMPORTANT: If a user asks you to "plot", "draw", "compute", "run", "simulate", or "generate" anything — you MUST use an execution action, never "none".

Rules for code generation:
- Use ONLY built-in MATLAB functions (plot, surf, mesh, fft, disp, fprintf, etc.)
- Do NOT call LLM, AI, GPT, Claude, MatClaw, or any non-MATLAB function
- For animations, use drawnow inside loops
- For projects, put each function in its own .m file; first file is the entry point

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CODE QUALITY STANDARDS — always follow these
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

VISUALISATION — every plot must:
1. Use figure('Position',[50 50 1100 750]) for a large canvas
2. Use subplot() grids — never just one panel for multi-aspect requests
   - Simulations: 3D path + top-view XY + altitude profile + speed/velocity panels
   - Signal work: time-domain + frequency-domain + spectrogram side by side
   - Control: step response + Bode + pole-zero map
3. Colour-code trajectories by speed/time using a loop + colormap:
   cmap = jet(N);
   for i = 1:N-1
     plot3([x(i) x(i+1)],[y(i) y(i+1)],[z(i) z(i+1)],'Color',cmap(i,:),'LineWidth',2);
     hold on;
   end
4. Add sgtitle() with key stats (max value, duration, units)
5. Label EVERY axis with units: xlabel('Time (s)'), ylabel('Altitude (m)')
6. Use grid on; on every subplot
7. Mark key events: scatter3 for waypoints, xline() for phase boundaries, text() for labels
8. Use area() or fill() for shaded profiles instead of plain plot()
9. Use yyaxis for dual-unit panels (e.g. altitude + speed on same time axis)

PHYSICS & SIMULATION — always:
1. Break missions/scenarios into named phases with if/elseif blocks
2. Compute velocity with gradient(position, dt) — never assume constant speed
3. Smooth noisy derivatives: movmean(signal, window)
4. Pre-allocate arrays: x = zeros(N,1) before loops
5. Print a stats summary with fprintf at the end:
   fprintf('Max altitude: %.2f m | Max speed: %.2f m/s | Duration: %.0f s\n', ...)
6. Use realistic parameters scaled to the domain:
   - drone/quadrotor: radius 3-5m, altitude 5-10m, speed 5-15 m/s, T=20s
   - rocket/launch vehicle/Starship: altitude 0-400km, speed 0-8000 m/s, T=600s, Isp~360s
   - satellite orbit: altitude 400km, speed 7800 m/s, orbital period 5500s
   - aircraft: altitude 0-12000m, speed 0-280 m/s, T=300s
   - pendulum/robot: angles in radians, length 0.5-2m, period based on sqrt(L/g)

EXAMPLE — drone simulation structure (use this as a template):
  t = (0:dt:T)';  N = numel(t);
  x=zeros(N,1); y=zeros(N,1); z=zeros(N,1);
  for i=1:N
    ti=t(i);
    if ti<5          % Phase 1: takeoff
      z(i)=0.3*ti^2; x(i)=0; y(i)=0;
    elseif ti<12     % Phase 2: helical ascent
      ph=(ti-5)*0.8; r=3;
      x(i)=r*cos(ph); y(i)=r*sin(ph); z(i)=4+(ti-5)*0.5;
    elseif ti<18     % Phase 3: orbit
      ph=(ti-12)*1.1; r=4;
      x(i)=r*cos(ph); y(i)=r*sin(ph); z(i)=7.5;
    else             % Phase 4: land
      frac=(ti-18)/4; x(i)=(1-frac)*4; y(i)=0; z(i)=7.5*(1-frac)^2;
    end
  end
  vx=gradient(x,dt); vy=gradient(y,dt); vz=gradient(z,dt);
  speed=movmean(sqrt(vx.^2+vy.^2+vz.^2),5);

EXAMPLE — Starship/rocket launch to orbit (use for any rocket/space simulation):
  dt=1; T=600; t=(0:dt:T)'; N=numel(t);
  alt=zeros(N,1); vr=zeros(N,1);
  for i=1:N
    ti=t(i);
    if ti<90          % Phase 1: launch & max-q  (0-90s, alt 0-40km)
      alt(i)=0.5*4.0*(ti)^2;          % constant 4 m/s^2 accel
    elseif ti<180     % Phase 2: booster sep     (90-180s, alt 40-120km)
      alt(i)=40000 + 0.5*6*(ti-90)^2;
    elseif ti<420     % Phase 3: vacuum burn     (180-420s, alt 120-400km)
      frac=(ti-180)/240;
      alt(i)=120000 + 280000*frac;
    else              % Phase 4: orbit insertion (420-600s, stable orbit)
      alt(i)=400000 + 500*sin((ti-420)*2*pi/180);
    end
  end
  vr=gradient(alt,dt); speed=movmean(abs(vr),5);
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

_SIM_KEYWORDS = {
    "simulation", "simulate", "drone", "quadrotor", "robot", "trajectory",
    "pid", "controller", "ode", "dynamics", "pendulum", "vehicle", "aircraft",
    "missile", "satellite", "orbit", "kinematics", "motion", "path planning",
    "flight", "autopilot", "control system", "state space", "transfer function",
}

_VIZ_KEYWORDS = {
    "plot", "graph", "visualize", "visualise", "draw", "show", "display",
    "surface", "surf", "mesh", "contour", "heatmap", "scatter", "histogram",
    "animation", "animate", "3d", "dashboard", "chart",
}

def _classify_intent(text: str) -> str:
    """
    Classify the user's request to decide if extra quality context should be injected.
    Returns: 'simulation' | 'visualization' | 'general'
    """
    lower = text.lower()
    if any(kw in lower for kw in _SIM_KEYWORDS):
        return "simulation"
    if any(kw in lower for kw in _VIZ_KEYWORDS):
        return "visualization"
    return "general"


_SIMULATION_BOOST = """
[SIMULATION REQUEST DETECTED — MANDATORY QUALITY REQUIREMENTS]
This is a dynamics/simulation task. You MUST:
1. Implement multiple mission phases (at least 3-4) with if/elseif blocks — NOT a single parametric formula
2. Use gradient(position, dt) to compute velocity — NEVER assume constant speed
3. Use movmean() to smooth velocity/speed signals (window=5 minimum)
4. Scale parameters REALISTICALLY for the domain:
   - drone/quadrotor: altitude 5-10m, speed 5-15 m/s, T=20s
   - rocket/Starship/launch vehicle: altitude 0-400km (400000m), speed 0-7800 m/s, T=600s (10 min)
   - satellite: altitude 400km, orbital speed 7800 m/s, period 5500s
   - aircraft: altitude 0-12000m, speed 0-280 m/s, T=300s
5. Generate a multi-panel figure (minimum 4 subplots):
   - Panel 1 (large, left): 3D trajectory, colour-coded by speed using a loop+colormap
   - Panel 2: top-view XY plot with start/end markers
   - Panel 3: altitude profile using area() fill, with xline() phase markers + text labels
   - Panel 4: speed profile using fill() or area() shading
6. Call sgtitle() with key stats: max altitude, max speed, total duration
7. Print stats with fprintf at the end
8. Use figure('Position',[50 50 1100 780]) for a large canvas

CRITICAL — THE CODE IS NOT COMPLETE WITHOUT THESE EXACT LINES:
  figure('Position',[50 50 1100 780]);
  % ... subplot and plot/plot3 calls ...
  sgtitle('...');
Your code MUST contain at least one figure() call AND at least one plot(), plot3(), or subplot() call.
A simulation that only computes arrays and never calls figure/plot IS WRONG and will be rejected.
The LAST section of your code must always be the visualization block, not the ODE solver.

DO NOT use tiny scale numbers. DO NOT generate a simple parametric helix. Generate REAL phased mission dynamics with correct engineering units.
DO NOT call external functions — all code MUST be self-contained in a single script. No function calls to undefined helpers.
"""

_VISUALIZATION_BOOST = """
[VISUALIZATION REQUEST DETECTED — MANDATORY QUALITY REQUIREMENTS]
This is a visualization task. You MUST:
1. Use figure('Position',[50 50 1100 750]) — large canvas
2. Use multiple subplots showing different aspects/projections
3. Use colormaps and colour-coded data where applicable
4. Label all axes with units, add grid on, add a descriptive title
5. Use area(), fill(), or patch() for shaded regions instead of plain plot lines
6. Add annotations: text(), legend(), colorbar() where appropriate
"""


def _build_messages(
    user_text: str,
    system: str,
    history: list[dict[str, str]] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Build (system_arg, messages) for the LLM call. Shared by sync and streaming paths."""
    # Inject intent-specific quality boost into the system prompt
    # Simulations get BOTH boosts: physics requirements + mandatory figure/plot rules
    intent = _classify_intent(user_text)
    if intent == "simulation":
        system = system + _SIMULATION_BOOST + _VISUALIZATION_BOOST
    elif intent == "visualization":
        system = system + _VISUALIZATION_BOOST

    # Dynamically append registered agent context to the system prompt
    try:
        from matclaw.agents.registry import AgentRegistry
        agents = AgentRegistry().list_agents()
        if agents:
            agent_lines = "\n".join([f"- {a.id}: {a.name} — {a.trigger_condition}" for a in agents])
            system = system + f"\n\n[Available Agents for Swarm]\n{agent_lines}"
    except Exception:
        pass  # Don't crash message building if registry is unavailable

    provider = settings.llm.provider.lower()
    if provider == "nvidia":
        messages: list[dict[str, str]] = []
        if history:
            for i, m in enumerate(history):
                content = m.get("text", m.get("content", ""))
                role = m.get("role", "user")
                if i == 0 and role == "user":
                    content = f"{system}\n\n{content}"
                messages.append({"role": role, "content": content})
            messages.append({"role": "user", "content": user_text})
        else:
            messages = [{"role": "user", "content": f"{system}\n\n{user_text}"}]
        return "", messages
    else:
        messages = []
        if history:
            for m in history:
                content = m.get("text", m.get("content", ""))
                messages.append({"role": m.get("role", "user"), "content": content})
        messages.append({"role": "user", "content": user_text})
        return system, messages


def _llm_chat(user_text: str, system: str = MATCLAW_PERSONA,
              history: list[dict[str, str]] | None = None,
              max_tokens: int = 4096) -> str:
    """Call the LLM synchronously. Returns plain text response."""
    try:
        sys_arg, messages = _build_messages(user_text, system, history)
        result = call_chat_completion(
            provider=settings.llm.provider,
            model=settings.llm.model,
            system=sys_arg,
            messages=messages,
            api_key=None,
            max_tokens=max_tokens,
        )
        return result or ""
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return ""


def _escape_json_control_chars(s: str) -> str:
    """
    Escape raw control characters (newlines, tabs, etc.) that appear literally
    inside JSON string values. The LLM sometimes emits actual newlines within a
    quoted value, which makes json.loads raise 'Invalid control character'.
    """
    result: list[str] = []
    in_string = False
    escape_next = False
    _ctrl = {'\n': '\\n', '\r': '\\r', '\t': '\\t', '\b': '\\b', '\f': '\\f'}
    for ch in s:
        if escape_next:
            result.append(ch)
            escape_next = False
            continue
        if ch == '\\':
            result.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string and ord(ch) < 0x20:
            result.append(_ctrl.get(ch, f'\\u{ord(ch):04x}'))
        else:
            result.append(ch)
    return ''.join(result)


def _parse_llm_response(raw: str) -> dict[str, Any]:
    """Parse the LLM's JSON response, handling markdown fences and malformed output."""
    if not raw:
        return {}
    # Strip markdown fences (but not code fences in the middle of text)
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()).strip()
    # Fix JS-style string concatenation: "...\n" +\n  "..." → "...\n..."
    cleaned = re.sub(r'"\s*\+\s*\n\s*"', "", cleaned)
    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Escape literal control chars inside string values and retry
    try:
        return json.loads(_escape_json_control_chars(cleaned))
    except json.JSONDecodeError:
        pass
    # Try to extract the first {...} block (handles trailing garbage)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(_escape_json_control_chars(match.group()))
        except json.JSONDecodeError:
            pass
    # LLM sometimes omits outer braces — wrap and retry
    if cleaned.startswith('"'):
        try:
            return json.loads("{" + cleaned + "}")
        except json.JSONDecodeError:
            pass
    # ── Truncated JSON recovery ───────────────────────────────────────────
    # NVIDIA streaming sometimes cuts off the response mid-code-string.
    # Rescue what we can: extract "reply", "action", "code" via targeted regex
    # even from malformed/incomplete JSON.
    rescued: dict[str, Any] = {}
    _reply_m = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned, re.DOTALL)
    if _reply_m:
        try:
            rescued["reply"] = json.loads('"' + _reply_m.group(1) + '"')
        except Exception:
            rescued["reply"] = _reply_m.group(1).replace('\\"', '"')
    _action_m = re.search(r'"action"\s*:\s*"(\w+)"', cleaned)
    if _action_m:
        rescued["action"] = _action_m.group(1)
    # Code: grab everything after "code": " up to end (truncated string ok)
    _code_m = re.search(r'"code"\s*:\s*"(.*)', cleaned, re.DOTALL)
    if _code_m:
        raw_code = _code_m.group(1)
        # Strip trailing: ",\n  "project"... or just end of string
        raw_code = re.sub(r'",?\s*\n?\s*"(?:project|dag_plan)".*$', '', raw_code, flags=re.DOTALL).rstrip('",')
        try:
            rescued["code"] = json.loads('"' + raw_code + '"')
        except Exception:
            # Manual unescape of \n \t \\
            rescued["code"] = raw_code.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\')
    if rescued:
        return rescued

    # ── Plain-text fallback: LLM didn't produce JSON ──────────────────────
    # Extract code blocks (```matlab ... ```) from plain text
    code_blocks = re.findall(r"```(?:matlab)?\s*\n(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    # Detect "Action: run_matlab" or similar at end of text
    action_match = re.search(r"(?:^|\n)\s*Action:\s*([\w_]+)", raw, re.IGNORECASE)
    action = action_match.group(1).strip().lower() if action_match else "none"
    # Build reply: strip the Action: line and code fences from reply text
    reply_text = raw
    if action_match:
        reply_text = raw[:action_match.start()].strip()
    # Remove fenced code blocks from the reply (they'll go in "code")
    reply_text = re.sub(r"```(?:matlab)?\s*\n.*?```", "", reply_text, flags=re.DOTALL | re.IGNORECASE).strip()
    # Clean up trailing lines like "Here's the MATLAB code:" if code was extracted
    if code_blocks:
        reply_text = re.sub(r"\s*Here(?:'|')s the (?:MATLAB )?code:?\s*$", "", reply_text, flags=re.IGNORECASE).strip()

    code = code_blocks[-1].strip() if code_blocks else ""
    if action == "none" and code:
        action = "run_matlab"

    return {"reply": reply_text, "action": action, "code": code} if reply_text else {}


def _extract_matlab_for_node(raw: str) -> str:
    """
    Extract executable MATLAB code from an LLM response in any format:
      1. Fenced ```matlab ... ``` block  (ideal)
      2. JSON persona format with a "code" field  (MATCLAW_PERSONA bleed-through)
      3. Reasoning-block embedded code  (thinking models)
      4. Raw code with no fences  (fallback)
    """
    if not raw:
        return ""

    # 1. Fenced code block
    blocks = re.findall(r"```(?:matlab)?\s*\n(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if blocks:
        return blocks[-1].strip()

    # 2. JSON persona response — extract the "code" field
    try:
        parsed = _parse_llm_response(raw)
        code = parsed.get("code") or ""
        if code and len(code) > 10:
            return code.strip()
    except Exception:
        pass

    # 3. Reasoning-block embedded code
    extracted = _extract_code_from_reasoning(raw)
    if extracted and len(extracted) > 10:
        return extracted.strip()

    # 4. Raw fallback — use entire response as code only if it looks like MATLAB
    stripped = raw.strip()
    matlab_signals = ("=", ";", "for ", "while ", "fprintf", "plot", "figure", "%")
    if any(sig in stripped for sig in matlab_signals) and len(stripped) > 10:
        return stripped

    return ""


PLOTS_DIR = ROOT / "plots"
PLOTS_DIR.mkdir(exist_ok=True)


def _check_plot_quality(plot_path: str, matlab_output: str) -> dict:
    """Check plot quality using PIL pixel statistics. No API key needed."""
    issues: list[str] = []

    # Check A: MATLAB error in output
    if "MATLAB error:" in matlab_output or "execution failed" in matlab_output.lower():
        return {"status": "error", "issues": ["MATLAB execution error"]}

    # Check B: PIL-based blank detection
    if plot_path and os.path.exists(plot_path):
        try:
            from PIL import Image
            import numpy as np
            arr = np.array(Image.open(plot_path).convert("RGB"))
            std = float(arr.std())
            if std < 8:
                issues.append(f"plot is blank or nearly empty (pixel std={std:.1f})")
            elif std < 15:
                issues.append(f"plot has very low visual content (pixel std={std:.1f}) — may be missing data")
        except Exception as exc:
            logger.debug("PIL quality check failed: %s", exc)

    # Check C: No meaningful output
    stripped = matlab_output.strip()
    if not stripped or (stripped.startswith("MATLAB executed successfully") and len(stripped) < 50):
        issues.append("no meaningful output or stats printed")

    return {"status": "poor" if issues else "good", "issues": issues}


def _sentry_retry_code(original_request: str, original_code: str, issues: list[str]) -> str:
    """Ask the LLM to fix the code given a list of detected issues."""
    issue_summary = "; ".join(issues)
    prompt = (
        f"The previous MATLAB code had quality issues: {issue_summary}.\n\n"
        f"Original request: {original_request}\n\n"
        f"Original code:\n```matlab\n{original_code}\n```\n\n"
        "Fix these specific issues and return ONLY the corrected MATLAB code block. "
        "Do not include any explanation — just the fixed code."
    )
    return _llm_chat(prompt, system=MATCLAW_PERSONA)



# ── lifespan — starts/stops all background services ──────────────────────────
async def _apply_matlab_headless_figures() -> None:
    """
    Suppress figure windows for the server session. Runs **after** the HTTP server
    is listening — a slow or stuck MATLAB eval must not block page load.
    """
    try:
        if not bridge.is_healthy():
            return
        from matclaw.matlab.matlab_bridge import MatlabCallRequest

        req = MatlabCallRequest(
            function="eval",
            args=["set(0, 'DefaultFigureVisible', 'off'); set(0, 'DefaultFigureWindowStyle', 'docked');"],
            nargout=0,
        )
        await asyncio.wait_for(
            asyncio.to_thread(bridge.call, req),
            timeout=120.0,
        )
        logger.info("MATLAB figure visibility: OFF (headless mode active)")
    except asyncio.TimeoutError:
        logger.warning("Headless MATLAB figure setup timed out after 120s; UI and API remain available.")
    except Exception as _fig_e:
        logger.debug("Could not set headless mode: %s", _fig_e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _headless_figure_task
    # Connect to MATLAB in a background thread to prevent startup hangs
    logger.info("Connecting to MATLAB session (non-blocking)...")
    try:
        await asyncio.to_thread(bridge.start)
        if bridge.is_healthy():
            logger.info("MATLAB connected successfully.")
        else:
            # Try connecting to any available shared session
            import matlab.engine as _me  # type: ignore
            sessions = await asyncio.to_thread(_me.find_matlab)
            if sessions:
                bridge.settings.session_name = sessions[0]
                await asyncio.to_thread(bridge.start)
                logger.info("Connected to shared MATLAB session: %s", sessions[0])
            else:
                logger.warning("No MATLAB session found — bridge will retry on first request.")
    except Exception as _e:
        logger.warning("MATLAB startup skipped or failed: %s", _e)

    # Register runtimes now that bridge is live
    _runtime_registry.register(MatlabRuntime(bridge, _run_matlab_and_collect))
    _runtime_registry.register(PythonRuntime(cwd=str(ROOT), venv_python=sys.executable))
    _runtime_registry.register(ShellRuntime(cwd=str(ROOT)))
    # Auto-discover pluggable tools
    tool_registry.discover()
    # Start daemon
    _heartbeat.start()
    logger.info("MatClaw enterprise daemon started. Runtimes: %s",
                [r["name"] for r in _runtime_registry.list_runtimes()])

    # Defer headless MATLAB eval until after yield — Uvicorn does not accept connections until yield.
    async def _headless_after_bind() -> None:
        await asyncio.sleep(0.05)
        await _apply_matlab_headless_figures()

    _headless_figure_task = asyncio.create_task(_headless_after_bind())

    yield

    if _headless_figure_task and not _headless_figure_task.done():
        _headless_figure_task.cancel()
        with suppress(asyncio.CancelledError):
            await _headless_figure_task
    _headless_figure_task = None
    _heartbeat.stop()
    logger.info("MatClaw daemon stopped.")


# ── app ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="MatClaw API", version="2.0.0", lifespan=lifespan)

# Initialize subsystems
agent_registry = AgentRegistry()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enterprise middleware (auth off by default — enable via MATCLAW_AUTH__ENABLED=true)
_auth_enabled = os.environ.get("MATCLAW_AUTH__ENABLED", "false").lower() == "true"
app.add_middleware(
    EnterpriseMiddleware,
    key_store=_key_store,
    auth_enabled=_auth_enabled,
    metrics=_metrics,
)

app.mount("/plots", StaticFiles(directory=str(PLOTS_DIR)), name="plots")


# ── projects dir ─────────────────────────────────────────────────────────────
PROJECTS_DIR = ROOT / "projects"
PROJECTS_DIR.mkdir(exist_ok=True)
app.mount("/projects", StaticFiles(directory=str(PROJECTS_DIR)), name="projects")

# ── built React frontend (served when running via Docker / install.sh) ────────
_FRONTEND_DIST = ROOT / "web" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="frontend-assets")


# ── request / response models ────────────────────────────────────────────────
class RunRequest(BaseModel):
    text: str = ""
    message: str = ""      # alias used by CLI / webhook
    session_id: str = "default"
    history: list[dict[str, str]] = []  # [{"role": "user", "text": "..."}, ...]
    mode: str = "auto"  # "ask" | "auto" | "plan" | "agentic"
    force_runtime: str | None = None  # "matlab" | "python" | "shell" — bypasses NL routing
    sentry_mode: bool = False   # auto-check result quality and retry on failure
    doctor_mode: bool = False   # run CodeDoctor auto-debug loop (opt-in; can block server during retries)

    @property
    def effective_text(self) -> str:
        return self.text or self.message


class RunResponse(BaseModel):
    skill: str
    output: str
    plots: list[str] = []
    metrics: dict[str, Any] = {}
    elapsed_ms: int = 0
    files: list[dict[str, str]] = []  # project files: {path, filename, language, content, url}


# ── helpers ──────────────────────────────────────────────────────────────────
def _save_plots_from_matlab() -> list[str]:
    """Collect any new plots MATLAB wrote to PLOTS_DIR and return their URLs."""
    saved: list[str] = []
    for f in sorted(PLOTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix.lower() in {".png", ".jpg", ".gif"} and f.stat().st_mtime > (time.time() - 15):
            saved.append(f"/plots/{f.name}")
    return saved


def _capture_animated_gif(code: str, base_name: str) -> str | None:
    """
    If code contains animation commands (drawnow / pause inside a loop),
    inject frame-capture scaffolding and assemble a GIF via Pillow.
    """
    # Only treat as animation if drawnow is explicitly present — bare for/while loops
    # are extremely common in ODE solvers and must NOT trigger the GIF path.
    if "drawnow" not in code:
        return None

    safe_base = re.sub(r'[^a-zA-Z0-9_-]', '_', base_name)[:60]
    frame_dir = PLOTS_DIR / f"_frames_{safe_base}"
    frame_dir.mkdir(exist_ok=True)

    frame_dir_str = str(frame_dir).replace("'", "''")
    sanitized_code = _sanitize_matlab_code(code)
    inject = (
        f"mc_frame_dir = '{frame_dir_str}';\n"
        "mc_frame_n = 0;\n"
    )
    instrumented = re.sub(
        r"drawnow\s*;?",
        lambda m: (
            "drawnow; mc_frame_n = mc_frame_n + 1; "
            "if mod(mc_frame_n,3)==0, "
            "exportgraphics(gcf, fullfile(mc_frame_dir, sprintf('frame_%04d.png', mc_frame_n)), 'Resolution', 72); "
            "end"
        ),
        sanitized_code,
    )
    _run_via_script(inject + instrumented)

    frames = sorted(frame_dir.glob("frame_*.png"))
    if not frames:
        return None

    try:
        from PIL import Image
        imgs = [Image.open(str(f)).convert("RGBA") for f in frames]
        gif_path = PLOTS_DIR / f"{base_name}.gif"
        imgs[0].save(
            str(gif_path),
            save_all=True,
            append_images=imgs[1:],
            loop=0,
            duration=80,
        )
        # cleanup frames
        for f in frames:
            f.unlink()
        frame_dir.rmdir()
        return f"/plots/{gif_path.name}"
    except Exception as exc:
        logger.warning("GIF assembly failed: %s", exc)
        return None


def _run_via_script(code: str) -> tuple[bool, str]:
    """
    Write code to a temp .m script and run it via evalc('run(script)').
    This avoids evalc('...inline...') which breaks on multi-line code.
    """
    import tempfile
    from matclaw.matlab.matlab_bridge import MatlabCallRequest

    # Write as ASCII-only — MATLAB parser rejects non-ASCII characters
    # (smart quotes, em-dashes, pi symbol, arrows, etc.) in .m files,
    # even when inside string literals. Strip the entire code block.
    ascii_code = (
        code
        .replace('\u2014', '-')   # em-dash -> hyphen
        .replace('\u2013', '-')   # en-dash -> hyphen
        .replace('\u2018', "'")   # left single quote -> apostrophe
        .replace('\u2019', "'")   # right single quote -> apostrophe
        .replace('\u201c', '"')   # left double quote -> quote
        .replace('\u201d', '"')   # right double quote -> quote
        .replace('\u03c0', 'pi')  # π -> pi
        .replace('\u03c9', 'omega')  # ω -> omega
        .replace('\u03b1', 'alpha')  # α -> alpha
        .replace('\u2192', '->')  # → -> ->
        .replace('\u00b2', '^2')  # ² -> ^2
        .replace('\u00b3', '^3')  # ³ -> ^3
        .encode("ascii", errors="replace").decode("ascii")
    )
    with tempfile.NamedTemporaryFile(suffix=".m", delete=False, mode="w",
                                     encoding="ascii", errors="replace") as f:
        f.write(ascii_code)
        script_path = f.name.replace("\\", "/")

    try:
        escaped = script_path.replace("'", "''")
        matlab_cmd = f"evalc(\"run('{escaped}')\")"
        req = MatlabCallRequest(
            function="eval",
            args=[matlab_cmd],
            nargout=1,
            timeout_seconds=90.0,  # Hard per-script cap — CodeDoctor will simplify if needed
        )
        result = bridge.call(req)
        if result.success:
            return True, str(result.result or "").strip()
        # If it timed out, surface a clear actionable message
        err = result.error or "Unknown MATLAB error"
        if "timed out" in err.lower():
            return False, (
                "MATLAB script timed out (90s limit). "
                "The simulation has too many iterations — reduce dt, shorten T, or vectorize loops."
            )
        return False, err
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def _sanitize_matlab_code(code: str) -> str:
    """
    Auto-repair common LLM MATLAB code mistakes before execution.

    Fixed patterns:
    1. jet(N)(i,:)        -> cmap = jet(N) pre-declared, cmap(i,:)
    2. \\end{...}          -> remove LaTeX remnants
    3. scatter(x,y,'c','Label') -> scatter(x,y,80,'c','filled')
    4. xline(v,'Label')   -> xline(v,'--','Label')
    5. area(t,z,'b')      -> area(t,z,'FaceColor','b','FaceAlpha',0.5)
    6. fill(t,y,'b')      -> closed polygon fill
    7. fprintf with literal newline inside single-quoted string
    """
    import re as _re

    # 1: jet(N)(i,:) -> cmap pre-declared
    if _re.search(r'jet\(\w+\)\(\w+,:\)', code):
        n_var = _re.search(r'jet\((\w+)\)', code)
        n_arg = n_var.group(1) if n_var else "N"
        code = _re.sub(r'jet\(\w+\)\((\w+),:\)', lambda m: f'cmap({m.group(1)},:)', code)
        code = _re.sub(r'(for\s+\w+\s*=\s*1\s*:\s*\w+-1)',
                      f'cmap = jet({n_arg});\\n\\1', code, count=1)

    # 2: Remove LaTeX \end{...} remnants
    code = _re.sub(r'\\\\end\{[^}]*\}\s*', '', code)

    # 3: scatter(x,y,'color','Label') -> scatter(x,y,80,'color','filled')
    code = _re.sub(
        r"scatter\(([^,]+),\s*([^,]+),\s*'([a-z])'\s*,\s*'[^']*'\)",
        r"scatter(\1, \2, 80, '\3', 'filled')",
        code
    )

    # 4: xline(v,'Label') -> xline(v,'--','Label')
    code = _re.sub(
        r"xline\(([^,)]+),\s*'([^'-][^']*)'\)",
        r"xline(\1, '--', '\2')",
        code
    )

    # 5: area(t,z,'b') -> area(t,z,'FaceColor','b')
    code = _re.sub(
        r"\barea\(([^,]+),\s*([^,]+),\s*'([a-z])'\)",
        r"area(\1, \2, 'FaceColor', '\3', 'FaceAlpha', 0.5)",
        code
    )

    # 6: fill(t,speed,'b') -> closed polygon
    def _fix_fill(m):
        xa, ya, ca = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        return (f"fill([{xa}; flipud({xa})], [{ya}; zeros(size({xa}))], "
                f"'{ca}', 'FaceAlpha', 0.2, 'EdgeColor', 'none')")
    code = _re.sub(r"\bfill\(([^,)]+),\s*([^,)]+),\s*'([a-z])'\)", _fix_fill, code)

    # 7: Raw literal newline inside fprintf single-quoted string
    def _fix_fprintf(m):
        return m.group(0).replace('\n', '\\n')
    code = _re.sub(r"fprintf\('[^']*\n[^']*'\)", _fix_fprintf, code)

    # 8: scatter3(x,y,z,'c','o','MarkerSize',N) -> scatter3(x,y,z,N,'c','filled')
    #    scatter3(x,y,z,'c','o') -> scatter3(x,y,z,80,'c','filled')
    def _fix_scatter3(m):
        x, y, z, rest = m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), m.group(4)
        # Extract color char and optional MarkerSize
        color_m = _re.search(r"'([a-z])'", rest)
        size_m = _re.search(r"'MarkerSize'\s*,\s*(\d+)", rest, _re.IGNORECASE)
        color = color_m.group(1) if color_m else 'b'
        sz = size_m.group(1) if size_m else '80'
        return f"scatter3({x}, {y}, {z}, {sz}, '{color}', 'filled')"
    code = _re.sub(
        r"\bscatter3\(([^,]+),\s*([^,]+),\s*([^,]+),\s*([^)]+)\)",
        lambda m: _fix_scatter3(m) if _re.search(r"'[a-z]'", m.group(4)) else m.group(0),
        code
    )

    # 9: scatter(x,y,'c','o','MarkerSize',N) and scatter(x,y,'c','o')
    def _fix_scatter2(m):
        x, y, rest = m.group(1).strip(), m.group(2).strip(), m.group(3)
        color_m = _re.search(r"'([a-z])'", rest)
        size_m = _re.search(r"'MarkerSize'\s*,\s*(\d+)", rest, _re.IGNORECASE)
        color = color_m.group(1) if color_m else 'b'
        sz = size_m.group(1) if size_m else '80'
        return f"scatter({x}, {y}, {sz}, '{color}', 'filled')"
    code = _re.sub(
        r"\bscatter\(([^,]+),\s*([^,)]+),\s*([^)]+)\)",
        lambda m: _fix_scatter2(m) if _re.search(r"(?:'[a-z]'.*'MarkerSize'|'[a-z]'\s*,\s*'o')", m.group(3)) else m.group(0),
        code
    )

    # 10: Fix 'FaceColor', 'gray' -> [0.5 0.5 0.5]
    code = _re.sub(r"'(FaceColor|EdgeColor|Color|MarkerFaceColor|MarkerEdgeColor)'\s*,\s*'gray'", r"'\1', [0.5 0.5 0.5]", code, flags=_re.IGNORECASE)

    return code


def _should_use_matlab_batch(code: str) -> bool:
    """
    Whether to run via `matlab -batch` subprocess vs the shared engine.

    Batch spawns a **new MATLAB process** (cold start often 30–120s) — it must
    not be used for routine plotting. The old heuristic treated almost every plot
    as "complex" (multi-line, `figure`, `subplot`, len>200), which caused simple
    surf/mesh plots to hang or time out while users saw "MATLAB offline" / busy.

    Reserve batch for long scripts, loops, ODEs, and Simulink-style runs.
    Set MATCLAW_MATLAB_FORCE_BATCH=1 to always batch (debug).
    """
    if os.environ.get("MATCLAW_MATLAB_FORCE_BATCH", "").lower() in ("1", "true", "yes"):
        return True
    # Keeps FastAPI responsive: in-process Engine can hold the GIL during eval.
    if os.environ.get("MATCLAW_MATLAB_SUBPROCESS_ONLY", "").lower() in ("1", "true", "yes"):
        return True
    c = code.strip()
    if not c:
        return False
    nl = c.count("\n")
    if nl > 80 or len(c) > 20000:
        return True
    if re.search(r"\bfor\s+", c) or re.search(r"\bwhile\s+", c):
        return True
    if re.search(r"\bode45\s*\(", c, re.IGNORECASE) or re.search(r"\bode\d+\s*\(", c, re.IGNORECASE):
        return True
    if re.search(r"\bsim\s*\(", c) or "simulink" in c.lower():
        return True
    if "parfor" in c:
        return True
    return False


def _ensure_visualization(code: str, req_text: str) -> str:
    """
    Safety net: if the LLM produced code that computes data but never opens a figure,
    append a minimal auto-plot block so the user always gets something visual.

    Detection: no 'figure' call AND no 'plot' / 'surf' / 'mesh' / 'bar' / 'histogram'
    call anywhere in the code.
    """
    lower = code.lower()
    has_figure = "figure" in lower
    has_plot = any(kw in lower for kw in (
        "plot(", "plot3(", "surf(", "mesh(", "contour(", "bar(", "histogram(",
        "scatter(", "scatter3(", "imagesc(", "pcolor(", "fill(", "area(",
        "subplot(", "polarplot(", "semilogx(", "semilogy(", "loglog(",
    ))
    if has_figure or has_plot:
        return code  # already has visualization — leave untouched

    # Detect numeric variables by scanning for assignment patterns like x = ...
    # Build a fallback that plots whatever arrays were computed
    logger.warning(
        "_ensure_visualization: code has no figure/plot call — appending auto-plot fallback. "
        "Request: %.80s", req_text
    )
    fallback = (
        "\n% ── Auto-plot fallback (no figure call detected in generated code) ──\n"
        "mc_vars = whos;\n"
        "mc_plotted = false;\n"
        "figure('Position',[50 50 1100 750]);\n"
        "mc_ax = 1;\n"
        "for mc_i = 1:numel(mc_vars)\n"
        "    mc_v = mc_vars(mc_i);\n"
        "    if ~strcmp(mc_v.class,'double'), continue; end\n"
        "    mc_data = eval(mc_v.name);\n"
        "    mc_sz = size(mc_data);\n"
        "    if min(mc_sz) == 1 && max(mc_sz) > 1 && max(mc_sz) <= 100000\n"
        "        subplot(2,3,min(mc_ax,6)); mc_ax = mc_ax+1;\n"
        "        plot(mc_data,'LineWidth',1.5);\n"
        "        title(mc_v.name,'Interpreter','none'); grid on;\n"
        "        xlabel('Index'); ylabel(mc_v.name,'Interpreter','none');\n"
        "        mc_plotted = true;\n"
        "    elseif min(mc_sz) > 1 && max(mc_sz) <= 500\n"
        "        subplot(2,3,min(mc_ax,6)); mc_ax = mc_ax+1;\n"
        "        imagesc(mc_data); colorbar; axis equal tight;\n"
        "        title(mc_v.name,'Interpreter','none'); grid on;\n"
        "        mc_plotted = true;\n"
        "    end\n"
        "    if mc_ax > 6, break; end\n"
        "end\n"
        "if mc_plotted\n"
        "    sgtitle('Computed Results (auto-generated plot)');\n"
        "else\n"
        "    close;\n"
        "end\n"
        "clear mc_vars mc_ax mc_i mc_v mc_data mc_sz mc_plotted;\n"
    )
    return code + fallback


def _run_matlab_and_collect(code: str, req_text: str) -> tuple[str, list[str]]:
    """
    Run MATLAB code, collect stdout + plots, return (output_text, plot_urls).

    Execution strategy (two-tier):
    1. BATCH MODE  — `matlab -batch` subprocess for heavy / risky work (loops, ODEs,
                     very long scripts). Isolated; slow cold start per run.
    2. ENGINE MODE — shared MATLAB Engine for Python (default for plots & short scripts).
    """
    from matclaw.matlab.batch_runner import (
        MATLAB_BIN,
        run_batch,
        split_matlab_script_and_local_functions,
    )

    ts = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    plot_name = f"plot_{ts}"
    plots: list[str] = []

    # Auto-repair common LLM MATLAB code mistakes first
    code = _sanitize_matlab_code(code)

    # Ensure visualization BEFORE animation check — fallback may add plot calls
    code = _ensure_visualization(code, req_text)

    # Animation path: only when drawnow is present (pure compute loops must not trigger this)
    gif_url = _capture_animated_gif(code, plot_name)
    if gif_url:
        plots.append(gif_url)
        return f"Animation rendered: {gif_url.split('/')[-1]}", plots

    # ── Tier 1: Batch subprocess (heavy simulations only) ─────────────────────
    if MATLAB_BIN and _should_use_matlab_batch(code):
        logger.info("MATLAB batch mode: executing in isolated subprocess (no desktop risk)")
        with bridge.external_busy():
            ok, output, batch_plots = run_batch(
                code=code,
                plots_dir=str(PLOTS_DIR),
                timeout=240.0,   # 4 min — kills subprocess only, never the engine
                plot_name=plot_name,
            )
        plots.extend(batch_plots)
        if not ok:
            # Prepend indicator so CodeDoctor knows this is a batch error
            output = f"MATLAB error: {output}"
        return output, plots

    # ── Tier 2: Shared engine (fast, for simple calls) ────────────────────────
    # If the engine was just restarted from a previous timeout, wait a moment and
    # fall back to batch (engine may not be fully up yet).
    if bridge.needs_restart() and MATLAB_BIN:
        logger.info("MATLAB engine still restarting after timeout — using batch fallback")
        with bridge.external_busy():
            ok, output, batch_plots = run_batch(
                code=code,
                plots_dir=str(PLOTS_DIR),
                timeout=240.0,
                plot_name=plot_name,
            )
        plots.extend(batch_plots)
        if not ok:
            output = f"MATLAB error: {output}"
        return output, plots

    # ── Auto-reconnect if bridge dropped (e.g. after a crash or non-ASCII error) ─
    if not bridge.is_healthy():
        logger.warning("MATLAB bridge unhealthy — attempting reconnect before execution...")
        try:
            bridge.start()
            if bridge.is_healthy():
                logger.info("MATLAB bridge reconnected successfully.")
            else:
                import matlab.engine as _me
                sessions = _me.find_matlab()
                if sessions:
                    bridge.settings.session_name = sessions[0]
                    bridge.start()
                    logger.info("Reconnected to shared session: %s", sessions[0])
        except Exception as _reconnect_err:
            logger.warning("MATLAB reconnect failed: %s", _reconnect_err)
        if not bridge.is_healthy():
            return "MATLAB is offline — please reconnect the MATLAB bridge.", []

    logger.info("MATLAB engine mode: simple call via shared session")
    save_path = f"{PLOTS_DIR}/{plot_name}.png"
    save_path_esc = save_path.replace("'", "''")
    # Use saveas + print (more reliable headless on macOS); exportgraphics can hang
    # waiting for display context even with DefaultFigureVisible=off.
    # NOTE: Use ASCII-safe quotes only to prevent MATLAB parsing errors
    # Local functions must stay at EOF — insert capture boilerplate before them only.
    main_body, local_fns = split_matlab_script_and_local_functions(
        code.encode("ascii", errors="replace").decode("ascii")
    )
    engine_footer = (
        "\ntry\n"
        f"  figs = get(0, 'Children');\n"
        f"  if ~isempty(figs)\n"
        f"    saveas(figs(1), '{save_path_esc}', 'png');\n"
        f"    if ~exist('{save_path_esc}', 'file')\n"
        f"      print(figs(1), '-dpng', '-r100', '{save_path_esc}');\n"
        f"    end\n"
        f"  end\n"
        f"catch matclaw_err\n"
        f"  try; print(gcf, '-dpng', '-r100', '{save_path_esc}'); catch; end;\n"
        f"end\n"
        f"close all;\n"
    )
    # Wrap main body in try/catch so the saveas footer ALWAYS runs even when
    # the user code errors mid-way (e.g. undefined variable, wrong dimension).
    # Any figure drawn before the error will still be captured.
    wrapped_code = (
        "set(0, 'DefaultFigureVisible', 'off');\n"
        "matclaw_user_error = '';\n"
        "try\n"
        + main_body + "\n"
        + "catch matclaw_user_exc\n"
        + "  matclaw_user_error = matclaw_user_exc.message;\n"
        + "  fprintf('MATLAB error in user code: %s\\n', matclaw_user_error);\n"
        + "end\n"
        + engine_footer
        + (("\n" + local_fns) if local_fns else "")
    )
    ok, stdout = _run_via_script(wrapped_code)
    if not ok and stdout:
        m_line = re.search(r'Line:\s*(\d+)', stdout)
        if m_line:
            err_line = int(m_line.group(1))
            lines = wrapped_code.splitlines()
            snippet_start = max(0, err_line - 3)
            snippet = "\n".join(
                f"{snippet_start+i+1}: {l}" for i, l in enumerate(lines[snippet_start:err_line+1])
            )
            logger.warning("MATLAB error at line %d. Code snippet:\n%s", err_line, snippet)
        clean_err = re.sub(r"File /[^\n]+\.m[^\n]*\n?", "", stdout).strip()
        clean_err = re.sub(r"File /Applications/MATLAB[^\n]*\n?", "", clean_err).strip()
        output = f"MATLAB error: {clean_err[:300]}" if clean_err else "MATLAB execution failed."
    else:
        output = stdout if stdout else f"MATLAB executed successfully: {req_text}"

    new_plots = _save_plots_from_matlab()
    plots.extend(new_plots)
    return output, plots




# ── endpoints ────────────────────────────────────────────────────────────────
@app.websocket("/api/terminal")
async def terminal_ws(websocket: WebSocket):
    await websocket.accept()
    
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        shell = os.environ.get("SHELL", "/bin/bash")
        try:
            os.execv(shell, [shell])
        except Exception:
            os._exit(127)
    
    loop = asyncio.get_running_loop()
    
    async def read_from_pty():
        try:
            while True:
                data = await loop.run_in_executor(None, os.read, fd, 1000000)
                if not data:
                    break
                await websocket.send_text(data.decode("utf-8", errors="replace"))
        except Exception as e:
            logger.debug("PTY read ended: %s", e)
            try:
                await websocket.close()
            except Exception:
                pass

    async def read_from_ws():
        try:
            while True:
                data = await websocket.receive_text()
                if data.startswith('{"type":"resize"'):
                    try:
                        import json, struct, fcntl, termios
                        payload = json.loads(data)
                        winsize = struct.pack("HHHH", payload["rows"], payload["cols"], 0, 0)
                        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
                    except Exception as resize_e:
                        logger.error(f"Resize error: {resize_e}")
                else:
                    await loop.run_in_executor(None, os.write, fd, data.encode("utf-8"))
        except Exception as e:
            logger.debug("WS read ended: %s", e)

    t1 = asyncio.create_task(read_from_pty())
    t2 = asyncio.create_task(read_from_ws())
    
    await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
    for t in [t1, t2]:
        if not t.done():
            t.cancel()
    try:
        os.close(fd)
    except Exception:
        pass
    try:
        os.waitpid(pid, os.WNOHANG)
    except Exception:
        pass

@app.get("/health")
async def health():
    """
    Liveness + MATLAB session/busy. Reads are O(1); do not use a thread pool so
    health checks stay accurate under load (no false 'offline' from executor delay).

    When MATLAB is offline or the LLM has no API key, `degraded` is true — callers
    should route traffic away or show limited mode (graceful degradation).
    """
    try:
        matlab_status = bridge.get_status_detail()
    except Exception as exc:
        logger.error(f"Health check failed: {exc}")
        matlab_status = {"healthy": False, "error": str(exc)}

    from matclaw.llm.llm_client import resolve_api_key

    prov = (settings.llm.provider or "").lower().strip()
    llm_key = resolve_api_key(settings.llm.provider, settings.llm.api_key)
    llm_status = {
        "provider": prov,
        "api_key_configured": bool(llm_key),
    }
    matlab_ok = bool(matlab_status.get("healthy"))
    llm_ok = llm_status["api_key_configured"]
    degraded = (settings.matlab.enabled and not matlab_ok) or not llm_ok
    return {
        "status": "degraded" if degraded else "ok",
        "degraded": degraded,
        "matlab": matlab_status,
        "llm": llm_status,
        "long_term_memory_enabled": bool(getattr(settings, "long_term_memory", None) and settings.long_term_memory.enabled),
    }


# ── Vision Analysis Endpoint ───────────────────────────────────────────────────

@app.post("/api/vision/analyze")
async def analyze_plot_endpoint(body: dict):
    """Run LLM-based analysis on a plot image using the existing vision analyst."""
    plot_path: str = body.get("plot_path", "")
    # Accept URLs like "/plots/plot_123.png" or just a filename — use basename to prevent traversal
    basename = os.path.basename(plot_path)
    full_path = (PLOTS_DIR / basename).resolve()
    if not full_path.is_relative_to(PLOTS_DIR.resolve()):
        return {"analysis": "Invalid plot path."}
    if not full_path.exists():
        return {"analysis": f"Plot file not found: {basename}"}
    try:
        from matclaw.vision.analyst import analyze_plot
        analysis = await asyncio.to_thread(analyze_plot, str(full_path))
        return {"analysis": analysis or "Analysis not available (no API key configured)"}
    except Exception as exc:
        logger.warning("Vision analysis failed: %s", exc)
        return {"analysis": f"Analysis failed: {exc}"}


# ── Session Persistence Endpoints ─────────────────────────────────────────────

@app.get("/api/sessions")
def list_sessions_endpoint():
    return session_store.list_sessions()

@app.get("/api/sessions/{session_id}")
def get_session_endpoint(session_id: str):
    from fastapi import HTTPException
    s = session_store.get_session(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return s

@app.post("/api/sessions")
def upsert_session_endpoint(body: dict):
    session_store.upsert_session(body)
    return {"status": "ok"}

@app.delete("/api/sessions/{session_id}")
def delete_session_endpoint(session_id: str):
    deleted = session_store.delete_session(session_id)
    return {"status": "deleted" if deleted else "not_found"}

# ── Agent REST Endpoints ───────────────────────────────────────────────────────
@app.get("/api/agents")
def get_agents():
    return agent_registry.list_agents()

@app.post("/api/agents")
def create_agent(agent: AgentDefinition):
    return agent_registry.save_agent(agent)

@app.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: str):
    from fastapi import HTTPException
    if not agent_registry.delete_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "deleted"}

class SmartGenRequest(BaseModel):
    topic: str

@app.post("/api/agents/generate")
async def smart_generate(req: SmartGenRequest):
    from fastapi import HTTPException
    import json, asyncio, re as _re

    # Ask LLM for a free-text description — avoid JSON schema completion traps
    system = (
        "You are a Master AI Agent Architect. Given a request, describe a specialized AI sub-agent in plain English. "
        "Include: its exact name (a 2-4 word title), its core role and capabilities, "
        "the specific triggering scenarios when it should be invoked, "
        "and which tools (from: run_matlab, read_file, edit_file, run_command, web_search) it needs."
    )
    user_msg = f"Design an AI sub-agent for: {req.topic}"

    def _call_llm():
        return call_chat_completion(
            provider=settings.llm.provider,
            model=settings.llm.model,
            system=system,
            messages=[{"role": "user", "content": user_msg}]
        )

    try:
        description = await asyncio.to_thread(_call_llm)

        # Derive a slug id from the topic
        slug = _re.sub(r'[^a-z0-9]+', '-', req.topic.lower()).strip('-')[:40]

        # Extract agent name: first capitalized phrase or fallback to topic
        name_match = _re.search(r'\*\*([A-Z][A-Za-z ]{3,40})\*\*|^([A-Z][A-Za-z ]{3,40}):', description, _re.MULTILINE)
        name = ((name_match.group(1) or name_match.group(2) or "").strip() if name_match else "") or req.topic[:60]

        # Infer allowed_tools from description
        tool_map = {
            "run_matlab": ["matlab", "simulation", "plot", "fft", "signal", "matrix", "compute", "calculate"],
            "read_file": ["read", "file", "document", "code", "script"],
            "edit_file": ["edit", "write", "modify", "create file", "generate code"],
            "run_command": ["command", "terminal", "shell", "execute", "run"],
            "web_search": ["search", "web", "lookup", "find online", "internet"],
        }
        desc_lower = description.lower()
        allowed_tools = [t for t, kws in tool_map.items() if any(k in desc_lower for k in kws)]
        if not allowed_tools:
            allowed_tools = ["run_matlab", "read_file"]

        # Build trigger condition from description (first sentence mentioning "when")
        trig_match = _re.search(r'([Ww]hen[^.]{10,200}\.)', description)
        trigger = trig_match.group(1).strip() if trig_match else f"When the task requires {req.topic.lower()}."

        result = {
            "id": slug,
            "name": name,
            "system_prompt": description.strip(),
            "trigger_condition": trigger,
            "callable_by_others": True,
            "allowed_tools": allowed_tools
        }
        return result
    except Exception as e:
        logger.error(f"Smart Generate failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Prompt Optimizer ──────────────────────────────────────────────────────────
class OptimizePromptReq(BaseModel):
    prompt: str

@app.post("/api/agents/optimize-prompt")
async def optimize_prompt(req: OptimizePromptReq):
    import asyncio
    system = (
        "You are a prompt engineering expert. The user will give you a vague agent description. "
        "Expand it into a detailed, well-structured system prompt that defines the agent's role, "
        "capabilities, constraints, output format, and behavior. Return ONLY the optimized prompt text, "
        "no explanations or commentary."
    )
    def _call():
        return call_chat_completion(
            provider=settings.llm.provider,
            model=settings.llm.model,
            system=system,
            messages=[{"role": "user", "content": req.prompt}],
            api_key=settings.llm.api_key,
        )
    try:
        result = await asyncio.to_thread(_call)
        return {"optimized": result}
    except Exception as e:
        logger.error("Prompt optimization failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Pipeline endpoints ───────────────────────────────────────────────────────

class PipelineValidateReq(BaseModel):
    nodes: list = []
    edges: list = []

@app.get("/api/pipelines")
def list_pipelines_endpoint():
    return pipeline_store.list_pipelines()

@app.post("/api/pipelines")
def upsert_pipeline_endpoint(body: dict):
    return pipeline_store.upsert_pipeline(body)

@app.get("/api/pipelines/{pipeline_id}")
def get_pipeline_endpoint(pipeline_id: str):
    p = pipeline_store.get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return p

@app.delete("/api/pipelines/{pipeline_id}")
def delete_pipeline_endpoint(pipeline_id: str):
    if not pipeline_store.delete_pipeline(pipeline_id):
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {"ok": True}

@app.post("/api/pipelines/validate")
def validate_pipeline_endpoint(body: dict):
    router = TaskRouter()
    valid, error = router.validate_pipeline(body)
    return {"valid": valid, "error": error}

@app.post("/api/pipelines/{pipeline_id}/run")
async def run_pipeline_endpoint(pipeline_id: str):
    pipeline = pipeline_store.get_pipeline(pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    run_id = pipeline_store.create_run(pipeline_id)
    queue: asyncio.Queue = asyncio.Queue()
    _active_runs[run_id] = queue

    async def _execute():
        try:
            router = TaskRouter()
            dag, edges = pipeline_to_dag_plan(pipeline)
            router.build_dag(dag, edges=edges)
            try:
                order = router.get_execution_order()
            except ValueError as e:
                await queue.put(_sse("pipeline_error", {"error": str(e)}))
                await queue.put(None)
                pipeline_store.update_run(run_id, "failed", {"error": str(e)})
                return

            await queue.put(_sse("pipeline_start", {"run_id": run_id, "total_nodes": len(order)}))

            node_results: dict[str, str] = {}

            for node_id in order:
                node = router.nodes[node_id]
                agent_def = None
                if node.agent_id:
                    try:
                        agent_def = AgentRegistry().get_agent(node.agent_id)
                    except Exception:
                        logger.exception("Failed to load agent '%s'", node.agent_id)
                    if not agent_def:
                        logger.warning("Agent '%s' not found for node '%s'; using default persona.", node.agent_id, node_id)
                        await queue.put(_sse("node_warning", {"node_id": node_id, "warning": f"Agent '{node.agent_id}' not found, using default."}))

                agent_system = agent_def.system_prompt if agent_def else MATCLAW_PERSONA
                task_text = node.execution_payload.get("task", node.description)
                node_tool = node.execution_payload.get("tool", "")
                node_code = (node.execution_payload.get("code") or "").strip()
                node_timeout = node.execution_payload.get("timeout_seconds") or 300  # default 5 min per node

                # Inject upstream context using graph topology
                predecessors = [src_id for src_id, targets in router.graph.items() if node_id in targets]
                upstream_ctx = ""
                for pred_id in predecessors:
                    if pred_id in node_results:
                        upstream_ctx += f"\n\nOutput from upstream node '{pred_id}':\n{node_results[pred_id]}"
                if upstream_ctx:
                    task_text = task_text + upstream_ctx

                await queue.put(_sse("node_start", {"node_id": node_id, "label": node.description, "agent_id": node.agent_id or ""}))
                router.mark_status(node_id, "running")

                try:
                    agent_result = ""
                    node_plots: list[str] = []

                    async def _execute_node() -> tuple[str, list[str]]:
                        """Execute a single pipeline node and return (result, plots)."""
                        _result = ""
                        _plots: list[str] = []

                        if node_tool == "run_matlab" and node_code:
                            # Path A: direct execution
                            exec_output, _plots = await asyncio.to_thread(_run_matlab_and_collect, node_code, task_text)
                            _result = exec_output
                        elif node_tool == "run_matlab":
                            # Path B: LLM generates then executes
                            intent = _classify_intent(task_text)
                            node_boost = ""
                            if intent == "simulation":
                                node_boost = _SIMULATION_BOOST + _VISUALIZATION_BOOST
                            elif intent == "visualization":
                                node_boost = _VISUALIZATION_BOOST

                            # Use a code-only system prompt (NOT MATCLAW_PERSONA which
                            # instructs the LLM to return JSON — that bleeds through and
                            # produces {"reply":..., "code":...} instead of a code block).
                            node_system = (
                                "You are an expert MATLAB engineer writing self-contained scripts.\n"
                                + node_boost
                                + "\nRespond with ONLY a fenced MATLAB code block:\n"
                                "```matlab\n% your code here\n```"
                            )
                            raw = await asyncio.to_thread(
                                call_chat_completion,
                                provider=settings.llm.provider,
                                model=settings.llm.model,
                                system=node_system,
                                messages=[{"role": "user", "content": task_text}],
                                api_key=settings.llm.api_key,
                                max_tokens=2048,
                            )
                            generated_code = _extract_matlab_for_node(raw)
                            if generated_code:
                                exec_output, _plots = await asyncio.to_thread(_run_matlab_and_collect, generated_code, task_text)
                                _result = exec_output
                            else:
                                raise RuntimeError("LLM did not generate executable MATLAB code")
                        else:
                            # Path C: LLM text only
                            _result = await asyncio.to_thread(
                                call_chat_completion,
                                provider=settings.llm.provider,
                                model=settings.llm.model,
                                system=agent_system,
                                messages=[{"role": "user", "content": task_text}],
                                api_key=settings.llm.api_key,
                            )

                        return _result, _plots

                    agent_result, node_plots = await asyncio.wait_for(
                        _execute_node(),
                        timeout=float(node_timeout),
                    )

                    node_results[node_id] = agent_result
                    router.mark_status(node_id, "completed", agent_result)
                    await queue.put(_sse("node_complete", {
                        "node_id": node_id,
                        "output": agent_result[:2000],
                        "plots": node_plots,
                    }))

                except asyncio.TimeoutError:
                    error_msg = f"Node timed out after {node_timeout}s"
                    router.mark_status(node_id, "failed")
                    await queue.put(_sse("node_failed", {"node_id": node_id, "error": error_msg}))
                    await queue.put(_sse("pipeline_error", {"error": f"Node '{node_id}' failed: {error_msg}"}))
                    pipeline_store.update_run(run_id, "failed", {"error": error_msg, "failed_node": node_id})
                    return  # stop pipeline execution

                except Exception as exc:
                    router.mark_status(node_id, "failed")
                    await queue.put(_sse("node_failed", {"node_id": node_id, "error": str(exc)}))
                    await queue.put(_sse("pipeline_error", {"error": f"Node '{node_id}' failed: {exc}"}))
                    pipeline_store.update_run(run_id, "failed", {"error": str(exc), "failed_node": node_id})
                    return  # stop pipeline execution

            await queue.put(_sse("pipeline_complete", {"run_id": run_id}))
            pipeline_store.update_run(run_id, "completed", {"node_results": node_results})

        except Exception as exc:
            await queue.put(_sse("pipeline_error", {"error": str(exc)}))
            pipeline_store.update_run(run_id, "failed", {"error": str(exc)})
        finally:
            await queue.put(None)  # sentinel
            _active_runs.pop(run_id, None)

    asyncio.create_task(_execute())
    return {"run_id": run_id}

@app.get("/api/pipelines/runs/{run_id}/stream")
async def stream_pipeline_run(run_id: str):
    queue = _active_runs.get(run_id)
    if not queue:
        run = pipeline_store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        # Already finished — return status as a single SSE event
        async def _done():
            yield _sse("pipeline_complete", {"run_id": run_id, "status": run["status"]})
        return StreamingResponse(_done(), media_type="text/event-stream")

    async def _stream():
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item

    return StreamingResponse(_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

@app.delete("/api/pipelines/runs/{run_id}")
async def cancel_pipeline_run(run_id: str):
    queue = _active_runs.get(run_id)
    if queue:
        await queue.put(_sse("pipeline_error", {"error": "Cancelled by user"}))
        await queue.put(None)
    pipeline_store.update_run(run_id, "cancelled")
    return {"ok": True}

@app.get("/api/pipelines/{pipeline_id}/runs")
def list_pipeline_runs(pipeline_id: str):
    return pipeline_store.list_runs_for_pipeline(pipeline_id)


# ── Model Manager ────────────────────────────────────────────────────────────
MODELS_FILE = ROOT / ".matclaw_models.json"
import threading as _threading
_models_lock = _threading.Lock()

class ModelConfig(BaseModel):
    id: str = ""
    provider: str
    model: str
    api_key: str
    base_url: str | None = None
    label: str
    active: bool = False

def _load_models() -> list[dict]:
    with _models_lock:
        if MODELS_FILE.exists():
            try:
                return json.loads(MODELS_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                return []
        return []

def _save_models(models: list[dict]):
    import tempfile
    with _models_lock:
        data = json.dumps(models, indent=2)
        tmp = MODELS_FILE.with_suffix(".tmp")
        tmp.write_text(data)
        tmp.replace(MODELS_FILE)

@app.get("/api/settings/models")
def list_models():
    return _load_models()

@app.post("/api/settings/models")
def add_model(req: ModelConfig):
    models = _load_models()
    if not req.id:
        req.id = str(uuid.uuid4())
    # If this is the first model, make it active
    if not models:
        req.active = True
    models.append(req.model_dump())
    _save_models(models)
    # If active, hot-swap runtime settings
    if req.active:
        _activate_runtime(req)
    return req.model_dump()

@app.delete("/api/settings/models/{model_id}")
def delete_model(model_id: str):
    models = _load_models()
    target = next((m for m in models if m["id"] == model_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Model not found")
    if target.get("active"):
        raise HTTPException(status_code=400, detail="Cannot delete the active model")
    models = [m for m in models if m["id"] != model_id]
    _save_models(models)
    return {"status": "deleted"}

@app.post("/api/settings/models/{model_id}/activate")
def activate_model(model_id: str):
    models = _load_models()
    target = None
    for m in models:
        if m["id"] == model_id:
            m["active"] = True
            target = m
        else:
            m["active"] = False
    if not target:
        raise HTTPException(status_code=404, detail="Model not found")
    _save_models(models)
    _activate_runtime(ModelConfig(**target))
    return target

def _activate_runtime(m: ModelConfig):
    """Hot-swap the runtime LLM settings so subsequent calls use the new model."""
    settings.llm.provider = m.provider
    settings.llm.model = m.model
    settings.llm.api_key = m.api_key

@app.get("/api/settings/active-model")
def get_active_model():
    models = _load_models()
    active = next((m for m in models if m.get("active")), None)
    if active:
        return {"label": active["label"], "model": active["model"], "provider": active["provider"]}
    # Fallback to default from settings
    return {"label": settings.llm.model, "model": settings.llm.model, "provider": settings.llm.provider}


@app.post("/api/connect")
def connect_matlab():
    """Attempt to (re)connect to a running MATLAB shared session."""
    if bridge.is_healthy():
        return {"status": "already_connected"}
    try:
        import matlab.engine as _me  # type: ignore
        sessions = _me.find_matlab()
        if sessions:
            bridge.settings.session_name = sessions[0]
        bridge.start()
        if bridge.is_healthy():
            return {"status": "connected", "session": bridge.settings.session_name}
        return {"status": "failed", "detail": "Bridge started but not healthy"}
    except Exception as exc:
        return {"status": "failed", "detail": str(exc)}


@app.get("/api/history")
def get_history(last_n: int = 20):
    """Return recent runs from ExperimentTracker."""
    try:
        exps = tracker.list_experiments(last_n=last_n)
        return {"runs": [
            {
                "id": e.experiment_id,
                "skill": e.skill_name,
                "request": (e.params or {}).get("request", ""),
                "status": e.status,
                "started_at": e.started_at,
                "duration_seconds": e.duration_seconds,
                "metrics": e.metrics or {},
            }
            for e in exps
        ]}
    except Exception as exc:
        return {"runs": [], "error": str(exc)}


@app.get("/api/skills")
def get_skills():
    return {"skills": list_skills()}


@app.get("/api/plots")
def list_plots():
    files = [
        {"name": f.name, "url": f"/plots/{f.name}", "mtime": f.stat().st_mtime}
        for f in sorted(PLOTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        if f.suffix.lower() in {".png", ".jpg", ".gif"} and not f.name.startswith("_")
    ]
    return {"plots": files}


@app.post("/api/run", response_model=RunResponse)
async def run_nl(req: RunRequest):
    t0 = time.time()
    output = ""
    plots: list[str] = []
    files: list[dict[str, str]] = []
    metrics: dict[str, Any] = {}
    skill_name = "chat"

    try:
        # ── Phase 1: LLM decides action + generates conversational reply ──
        raw_resp = await asyncio.to_thread(
            _llm_chat,
            req.effective_text,
            system=MATCLAW_PERSONA,
            history=req.history[-10:] if req.history else None,
            max_tokens=8192,
        )
        logger.info("LLM raw response (first 500 chars): %s", raw_resp[:500] if raw_resp else "<empty>")
        plan = _parse_llm_response(raw_resp)
        logger.info("Parsed plan: %s", {k: (v[:100] if isinstance(v, str) else v) for k, v in plan.items()} if plan else "<empty>")
        reply = plan.get("reply", "")
        action = plan.get("action", "none")

        # ── Fallback: if LLM didn't return valid JSON, use keyword router ──
        if not plan or not reply:
            logger.info("LLM routing failed, falling back to keyword router")
            fallback_skill, _ = route_nl_message(req.effective_text)
            if fallback_skill in ("run_matlab",):
                action = "run_matlab"
                reply = ""
                # Use the old code-generation approach
                plan["code"] = None
            elif fallback_skill == "query_memory":
                action = "query_memory"
                reply = ""
            else:
                # Pure fallback: just use raw LLM text as reply
                action = "none"
                reply = raw_resp if raw_resp else "I'm not sure how to help with that. Could you rephrase?"

        skill_name = action if action != "none" else "chat"

        # ── Phase 2: Execute the action ───────────────────────────────────
        if action == "run_matlab":
            code = plan.get("code") or ""
            # Strip markdown fences if LLM wrapped them
            code = re.sub(r"```(?:matlab)?\s*|\s*```", "", code).strip()

            # Fallback to curated demo library
            if not code:
                code = find_demo(req.effective_text) or ""

            # Guard against hallucinated functions
            if code:
                bad = [r"\bLLM\b", r"\bAI\s*\(", r"\bMatClaw\s*\(", r"\bClaude\s*\("]
                if any(re.search(p, code, re.IGNORECASE) for p in bad):
                    code = find_demo(req.effective_text) or ""

            if code:
                exec_output, plots = await asyncio.to_thread(
                    _run_matlab_and_collect, code, req.effective_text
                )
                # Blend conversational reply with execution result
                if reply and exec_output and not exec_output.startswith("MATLAB error"):
                    output = reply
                elif reply and exec_output:
                    output = f"{reply}\n\n{exec_output}"
                else:
                    output = reply or exec_output
            else:
                output = reply or "I wasn't able to generate MATLAB code for that. Could you try rephrasing?"

        elif action == "project_gen":
            skill_name = "project_gen"
            project = plan.get("project") or {}
            proj_files = project.get("files", [])

            if proj_files:
                proj_name = re.sub(r"[^a-zA-Z0-9_-]", "_", project.get("name", "project"))
                proj_dir = PROJECTS_DIR / proj_name
                proj_dir.mkdir(parents=True, exist_ok=True)

                for f in proj_files:
                    fname = os.path.basename(f.get("filename", "untitled.m"))
                    content = f.get("content", "")
                    safe_path = (proj_dir / fname).resolve()
                    if not safe_path.is_relative_to(proj_dir.resolve()):
                        logger.warning("Skipping file with path traversal: %s", fname)
                        continue
                    safe_path.write_text(content, encoding="utf-8")
                    files.append({
                        "path": f"{proj_name}/{fname}",
                        "filename": fname,
                        "language": f.get("language", "matlab"),
                        "content": content[:5000],
                        "url": f"/projects/{proj_name}/{fname}",
                    })
                output = reply or project.get("description", f"Created project **{proj_name}** with {len(files)} files.")
            else:
                output = reply or "I wasn't able to generate the project structure. Try being more specific."

        elif action == "query_memory":
            skill_name = "query_memory"
            # Gather memory data
            mem_lines: list[str] = []
            try:
                semantic = memory.query_context(req.effective_text, n_results=5)
                for r in semantic:
                    meta = r.get("metadata", {})
                    req_text = meta.get("request", r.get("document", ""))
                    skill = meta.get("skill", "")
                    ts = meta.get("ts", "")
                    if req_text:
                        badge = f"[{skill}] " if skill else ""
                        mem_lines.append(f"- {badge}{req_text}  ({ts[:10] if ts else ''})")
            except Exception as e:
                logger.warning("ChromaDB query failed: %s", e)

            recent = tracker.list_experiments(last_n=10)
            seen = set(mem_lines)
            for exp in recent[:5]:
                req_t = (exp.params or {}).get("request", "")
                if req_t and req_t not in seen:
                    started = str(exp.started_at)[:10] if exp.started_at else ""
                    mem_lines.append(f"- [{exp.skill_name}] {req_t}  ({started})")
                    seen.add(req_t)

            if reply and mem_lines:
                output = f"{reply}\n\n" + "\n".join(mem_lines[:10])
            elif mem_lines:
                output = f"Here's what I found — {len(mem_lines)} past runs:\n\n" + "\n".join(mem_lines[:10])
            else:
                output = reply or "No past runs found yet. Run some commands first and I'll remember them!"

        else:
            # action == "none" — pure conversation
            output = reply

    except Exception as exc:
        logger.exception("run_nl failed")
        output = f"Something went wrong: {exc}"
        try:
            _exp = tracker.start_experiment(
                skill_name=skill_name,
                params={"request": req.text, "session_id": req.session_id},
                tags=[skill_name],
            )
            tracker.finish_experiment(_exp.experiment_id, status="failed", error=str(exc))
        except Exception:
            pass

    if not output:
        output = "Hmm, I didn't get a response. Could you try again?"

    elapsed = int((time.time() - t0) * 1000)

    # ── persist to tracker + memory ──────────────────────────────────────
    if not output.startswith("Something went wrong"):
        try:
            _exp = tracker.start_experiment(
                skill_name=skill_name,
                params={"request": req.text, "session_id": req.session_id},
                tags=[skill_name],
            )
            tracker.finish_experiment(
                _exp.experiment_id,
                status="success",
                metrics={"elapsed_ms": elapsed, "plots": len(plots)},
            )
            memory.store_artifact(
                key=_exp.experiment_id,
                metadata={
                    "skill": skill_name,
                    "request": req.text,
                    "output_summary": output[:300],
                    "plots": plots,
                    "ts": _exp.started_at,
                    "experiment_id": _exp.experiment_id,
                },
            )
        except Exception as _log_exc:
            logger.warning("Failed to log run: %s", _log_exc)

    return RunResponse(skill=skill_name, output=output, plots=plots,
                       metrics=metrics, elapsed_ms=elapsed, files=files)


# ── SSE streaming endpoint ─────────────────────────────────────────────────
def _sse(event: str, data: dict) -> str:
    """Format a single SSE event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/run/stream")
async def run_nl_stream(req: RunRequest):
    """Streaming variant of /api/run — returns SSE events progressively."""

    async def event_generator():
        t0 = time.time()
        accumulated_text = ""
        accumulated_thinking = ""
        skill_name = "chat"
        task_id = str(uuid.uuid4())

        def _log_episode(stage: str, context: dict) -> None:
            try:
                episodic_memory.log_episode(Episode(
                    id=str(uuid.uuid4()),
                    task_id=task_id,
                    timestamp=datetime.utcnow().isoformat(),
                    state_machine_stage=stage,
                    short_term_context=json.dumps(context),
                ))
            except Exception as _e:
                logger.debug("episodic_memory log failed: %s", _e)

        def _set_state(state: ExecutionState, payload: dict) -> None:
            try:
                state_tracker.save_task_state(task_id, state, payload)
            except Exception as _e:
                logger.debug("state_tracker update failed: %s", _e)

        # ── force_runtime shortcut (bypasses LLM routing) ─────────────────
        if req.force_runtime:
            rt_name = req.force_runtime
            code = req.effective_text
            yield _sse("tool_start", {"action": f"run_{rt_name}", "label": f"Running {rt_name}..."})
            try:
                result = await asyncio.to_thread(
                    _runtime_registry.execute, rt_name, code, {"task": code[:120]}
                )
                if result.success:
                    yield _sse("tool_result", {"output": result.output, "plots": result.plots, "files": []})
                    yield _sse("done", {"skill": f"run_{rt_name}", "elapsed_ms": result.elapsed_ms or 0, "reply": result.output, "plots": result.plots, "files": []})
                else:
                    yield _sse("tool_result", {"output": result.error, "plots": [], "files": []})
                    yield _sse("done", {"skill": f"run_{rt_name}", "elapsed_ms": 0, "reply": result.error, "plots": [], "files": []})
            except Exception as _rt_exc:
                yield _sse("error", {"message": str(_rt_exc)})
                yield _sse("done", {"skill": f"run_{rt_name}", "elapsed_ms": 0, "reply": str(_rt_exc), "plots": [], "files": []})
            return

        # ── Agentic mode: iterative tool-use loop ─────────────────────────────
        if req.mode == "agentic":
            async def _agentic_runtime_dispatch(tool_name: str, inputs: dict):
                """Dispatcher used by the agentic loop to run tools and runtimes."""
                # Virtual runtime tools
                if tool_name == "run_matlab":
                    code = inputs.get("code", "")
                    output, plots = await asyncio.to_thread(
                        _run_matlab_and_collect, code, req.effective_text
                    )
                    # Run CodeDoctor on agentic MATLAB results too
                    if req.doctor_mode:
                        doctor_events_ag: list[tuple[str, dict]] = []
                        def _on_ag_event(ev: str, d: dict) -> None:
                            doctor_events_ag.append((ev, d))
                        code, output, plots, _ = await asyncio.to_thread(
                            run_code_doctor,
                            code, output, plots,
                            req.effective_text, str(PLOTS_DIR),
                            lambda c, t: _run_matlab_and_collect(c, t),
                            _llm_chat, _on_ag_event,
                        )
                        # Emit doctor events into the SSE stream
                        # (they will be yielded by the agentic loop's caller)
                        for _ev, _d in doctor_events_ag:
                            pass   # agentic loop doesn't have a yield here;
                                   # events are stored and can be forwarded via agent_result
                    return output, plots
                if tool_name in ("run_python", "run_shell"):
                    rt = "python" if tool_name == "run_python" else "shell"
                    code = inputs.get("code") or inputs.get("command", "")
                    result = await asyncio.to_thread(
                        _runtime_registry.execute, rt, code, {"task": req.effective_text[:120]}
                    )
                    out = result.output if result.success else result.error
                    return out, getattr(result, "plots", [])
                # Pluggable tools (web_fetch, file_ops, …)
                plugin = tool_registry.get(tool_name)
                if plugin:
                    from matclaw.tools.base import ToolContext
                    ctx = ToolContext(tenant_id="default", request_id=req.session_id)
                    tr = await plugin.run(inputs, ctx)
                    out_str = str(tr.output) if tr.success else tr.error
                    plots = getattr(tr, "artifacts", [])
                    return out_str, plots
                return f"Unknown tool: {tool_name}", []

            memory_preamble = ""
            prod = getattr(settings, "production", None)
            if (
                settings.long_term_memory.enabled
                and prod
                and getattr(prod, "memory_inject_enabled", True)
            ):
                try:
                    n_res = int(getattr(prod, "memory_n_results", 5))
                    max_ch = int(getattr(prod, "memory_max_chars", 4000))
                    hits = memory.query_context(req.effective_text, n_results=max(1, min(20, n_res)))
                    lines: list[str] = []
                    for h in hits:
                        doc = (h.get("document") or "").strip()
                        if doc:
                            lines.append(doc[:1200])
                    blob = "\n\n".join(lines)
                    if blob.strip():
                        memory_preamble = (
                            "Relevant prior context from long-term memory "
                            "(past tasks, preferences, or lessons — verify before relying on it):\n"
                            + blob[:max_ch]
                        )
                except Exception as _mem_q:
                    logger.debug("Memory preamble skipped: %s", _mem_q)

            # Wrap agentic loop with a keepalive ping every 20s
            # so the browser SSE connection doesn't drop during long MATLAB runs.
            async with _agentic_slot_semaphore:
                agentic_gen = run_agentic_loop(
                    user_text=req.effective_text,
                    history=req.history,
                    settings=settings,
                    tool_registry=tool_registry,
                    runtime_dispatcher=_agentic_runtime_dispatch,
                    memory_preamble=memory_preamble or None,
                    session_id=req.session_id,
                    memory_manager=memory,
                )
                last_ping = time.time()
                agentic_done = False
                async for event in agentic_gen:
                    yield event
                    if 'event: done' in event:
                        agentic_done = True
                    if time.time() - last_ping > 20:
                        yield ": keepalive\n\n"
                        last_ping = time.time()
            if not agentic_done:
                elapsed = int((time.time() - t0) * 1000)
                prod = getattr(settings, "production", None)
                budget_ms = int((getattr(prod, "max_agentic_wall_seconds", 600.0) * 1000)) if prod else 600_000
                cost_cap_fb = float(getattr(prod, "soft_cost_cap_usd_per_task", 0.0) or 0.0) if prod else 0.0
                yield _sse("done", {
                    "skill": "agentic",
                    "elapsed_ms": elapsed,
                    "reply": "",
                    "plots": [],
                    "files": [],
                    "execution": {
                        "run_success": False,
                        "spec_satisfied": False,
                        "violations": ["Stream ended before final agentic done event."],
                        "notes": [],
                    },
                    "budget": {
                        "wall_clock_ms": elapsed,
                        "wall_clock_budget_ms": budget_ms,
                        "within_wall_budget": elapsed <= budget_ms,
                        "wall_abort": False,
                        "soft_cost_cap_usd": cost_cap_fb,
                        "cost_abort": False,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "estimated_cost_usd": 0.0,
                        "within_soft_cost_cap": True,
                    },
                })
            return

        try:
            # ── Phase 0: Mark as RESEARCHING ────────────────────────────
            await asyncio.to_thread(_log_episode, "researching", {"user_text": req.text, "session_id": req.session_id})
            await asyncio.to_thread(_set_state, ExecutionState.RESEARCHING, {"text": req.text})

            sys_arg, messages = _build_messages(
                req.effective_text, MATCLAW_PERSONA,
                req.history[-10:] if req.history else None,
            )

            # ── Phase 1: Stream LLM tokens ──────────────────────────────
            # Thinking tokens stream live; text tokens are accumulated silently
            # because the LLM outputs structured JSON — we parse it and emit
            # only the clean "reply" as text after parsing.
            try:
                async for chunk in call_chat_completion_stream(
                    provider=settings.llm.provider,
                    model=settings.llm.model,
                    system=sys_arg,
                    messages=messages,
                    api_key=None,
                    max_tokens=8192,
                ):
                    if chunk["type"] == "thinking":
                        accumulated_thinking += chunk["token"]
                        yield _sse("thinking", chunk)
                    elif chunk["type"] == "text":
                        accumulated_text += chunk["token"]
                        # Don't stream raw text — it's JSON, parsed below
            except Exception as llm_exc:
                logger.warning("Streaming LLM failed: %s", llm_exc)
                yield _sse("error", {"message": f"LLM error: {llm_exc}"})

            # ── Phase 2: Parse and execute ───────────────────────────────
            # For reasoning models: content may be empty with everything
            # in reasoning_content. Apply same extraction as sync path.
            if not accumulated_text.strip() and accumulated_thinking.strip():
                # Try extracting a JSON block with "reply" from thinking
                json_match = re.search(r"\{[^{}]*\"reply\"[^{}]*\}", accumulated_thinking, re.DOTALL)
                if json_match:
                    accumulated_text = json_match.group()
                else:
                    # Try code extraction
                    code = _extract_code_from_reasoning(accumulated_thinking)
                    if code:
                        accumulated_text = code
                    else:
                        # Last resort: use the last substantial paragraph as reply
                        paragraphs = [p.strip() for p in accumulated_thinking.split("\n\n") if p.strip()]
                        last_para = paragraphs[-1] if paragraphs else accumulated_thinking
                        accumulated_text = json.dumps({"reply": last_para, "action": "none"})

            raw = accumulated_text or accumulated_thinking
            logger.debug("Stream phase2: text_len=%d think_len=%d",
                         len(accumulated_text), len(accumulated_thinking))
            plan = _parse_llm_response(raw)
            reply = plan.get("reply", "")
            logger.debug("Stream phase2 parsed: keys=%s reply_len=%d action=%s",
                         list(plan.keys()), len(reply), plan.get("action", "none"))

            # Emit the parsed reply as text
            if reply:
                yield _sse("text", {"token": reply})
            action = plan.get("action", "none")

            # Override: if LLM chose "none" but NL router strongly signals an execution,
            # promote the action so the user gets a result (not just a chat response).
            if action == "none" and plan:
                _nl_signal, _ = route_nl_message(req.effective_text)
                if _nl_signal in ("run_matlab", "run_python", "run_shell"):
                    action = _nl_signal
                    plan.setdefault("code", None)

            # Fallback to keyword router if JSON parsing failed
            if not plan or not reply:
                fallback_skill, _ = route_nl_message(req.effective_text)
                if fallback_skill in ("run_matlab",):
                    action = "run_matlab"
                    plan.setdefault("code", None)

                elif fallback_skill == "run_python":
                    action = "run_python"
                    plan["code"] = None
                elif fallback_skill == "run_shell":
                    action = "run_shell"
                    plan["code"] = None
                elif fallback_skill == "query_memory":
                    action = "query_memory"
                else:
                    action = "none"
                    # Safety: if raw looks like JSON, try extracting "reply" field
                    # rather than dumping the entire JSON string as the chat reply
                    _fallback_raw = raw if raw else "I'm not sure how to help with that."
                    if _fallback_raw.strip().startswith('{'):
                        try:
                            _fb = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", _fallback_raw.strip()))
                            reply = _fb.get("reply") or _fallback_raw
                        except Exception:
                            reply = _fallback_raw
                    else:
                        reply = _fallback_raw
                    if not reply:
                        reply = "I'm not sure how to help with that."

            skill_name = action if action != "none" else "chat"
            plots: list[str] = []
            files: list[dict[str, str]] = []
            exec_output = ""

            # ── Mark PLANNING ─────────────────────────────────────────────
            await asyncio.to_thread(_log_episode, "planning", {"action": action, "reply_len": len(reply)})
            await asyncio.to_thread(_set_state, ExecutionState.PLANNING, {"action": action})

            # ── Plan Mode: emit plan before executing ─────────────────────
            if req.mode == "plan" and action != "none":
                code_preview = plan.get("code", "")
                if code_preview:
                    code_preview = re.sub(r"```(?:matlab)?\s*|\s*```", "", code_preview).strip()
                plan_steps = []
                plan_steps.append(f"**Action:** `{action}`")
                if reply:
                    plan_steps.append(f"**Reasoning:** {reply}")
                if code_preview:
                    plan_steps.append(f"**Code to execute:**\n```matlab\n{code_preview}\n```")
                dag = plan.get("dag_plan")
                if dag:
                    plan_steps.append(f"**DAG Plan:** {json.dumps(dag, indent=2)}")
                plan_summary = "\n\n".join(plan_steps)
                yield _sse("tool_start", {"action": "plan_preview", "label": "Plan"})
                yield _sse("tool_result", {
                    "output": plan_summary,
                    "plots": [],
                    "files": [],
                })

            if action == "run_matlab":
                code = plan.get("code") or ""
                code = re.sub(r"```(?:matlab)?\s*|\s*```", "", code).strip()
                if code:
                    code = _sanitize_matlab_code(code)
                if not code:
                    code = find_demo(req.effective_text) or ""

                if code:
                    # ── StaticAnalyzer pre-check ──────────────────────────
                    issues = await asyncio.to_thread(analyzer.check_code, code)
                    if issues:
                        issue_summary = "; ".join(
                            i.get("message", str(i)) for i in issues[:3]
                        )
                        yield _sse("tool_start", {
                            "action": "static_check",
                            "label": f"⚠ {len(issues)} potential issue(s): {issue_summary}",
                        })

                    # ── Mark IMPLEMENTING ─────────────────────────────────
                    await asyncio.to_thread(_log_episode, "implementing", {"code_len": len(code)})
                    await asyncio.to_thread(_set_state, ExecutionState.IMPLEMENTING, {})

                    yield _sse("tool_start", {"action": "run_matlab", "label": "Running MATLAB code..."})
                    _mat_task = asyncio.create_task(
                        asyncio.to_thread(_run_matlab_and_collect, code, req.effective_text)
                    )
                    while not _mat_task.done():
                        _done, _ = await asyncio.wait([_mat_task], timeout=12.0)
                        if _mat_task in _done:
                            break
                        yield ": keepalive\n\n"
                    exec_output, plots = await _mat_task
                    yield _sse("tool_result", {
                        "output": exec_output,
                        "plots": plots,
                        "files": [],
                        "code": code,
                    })

                    # ── CodeDoctor: autonomous debug loop ─────────────────
                    if req.doctor_mode:
                        doctor_events: list[tuple[str, dict]] = []

                        def _on_doctor_event(event: str, data: dict) -> None:
                            doctor_events.append((event, data))

                        def _doctor_executor(fixed_code: str, task: str) -> tuple[str, list[str]]:
                            return _run_matlab_and_collect(fixed_code, task)

                        _doc_task = asyncio.create_task(
                            asyncio.to_thread(
                                run_code_doctor,
                                code, exec_output, plots,
                                req.effective_text,
                                str(PLOTS_DIR),
                                _doctor_executor,
                                _llm_chat,
                                _on_doctor_event,
                            )
                        )
                        while not _doc_task.done():
                            _d2, _ = await asyncio.wait([_doc_task], timeout=12.0)
                            if _doc_task in _d2:
                                break
                            yield ": keepalive\n\n"
                        code, exec_output, plots, _rounds = await _doc_task

                        # Flush collected doctor events as SSE
                        for ev_name, ev_data in doctor_events:
                            yield _sse(ev_name, ev_data)

                        # If doctor changed the code/plots, emit updated tool_result
                        if _rounds:
                            yield _sse("tool_result", {
                                "output": exec_output,
                                "plots": plots,
                                "files": [],
                                "code": code,
                            })

                    # ── Sentry Mode: quality check + auto-retry ───────────
                    if req.sentry_mode and plots:
                        yield _sse("sentry_start", {"message": "Checking result quality..."})
                        retried = False
                        for attempt in range(1, 3):  # max 2 retries
                            plot_fs_path = str(PLOTS_DIR / plots[0].lstrip("/").replace("plots/", ""))
                            quality = _check_plot_quality(plot_fs_path, exec_output)
                            if quality["status"] == "good":
                                yield _sse("sentry_done", {
                                    "quality": "good",
                                    "message": "✓ Result verified",
                                })
                                retried = True
                                break
                            yield _sse("sentry_issue", {
                                "attempt": attempt,
                                "max_attempts": 2,
                                "issues": quality["issues"],
                                "message": f"Attempt {attempt}: {'; '.join(quality['issues'])}. Retrying...",
                            })
                            fixed_code = await asyncio.to_thread(
                                _sentry_retry_code, req.effective_text, code, quality["issues"]
                            )
                            if not fixed_code:
                                break
                            # Extract code block if LLM wrapped it in markdown
                            fixed_code = re.sub(r"```(?:matlab)?\s*|\s*```", "", fixed_code).strip()
                            code = fixed_code
                            yield _sse("tool_start", {
                                "action": "run_matlab",
                                "label": f"Sentry retry {attempt}/2...",
                            })
                            exec_output, plots = await asyncio.to_thread(
                                _run_matlab_and_collect, code, req.effective_text
                            )
                            yield _sse("tool_result", {
                                "output": exec_output,
                                "plots": plots,
                                "files": [],
                                "code": code,
                            })
                        if not retried:
                            yield _sse("sentry_done", {
                                "quality": "poor",
                                "message": "Max retries reached — result may need manual review",
                            })

            elif action in ("run_python", "run_shell"):
                rt_name = "python" if action == "run_python" else "shell"
                code = plan.get("code") or ""
                code = re.sub(r"```(?:python|bash|sh|shell)?\s*|\s*```", "", code).strip()
                if code:
                    await asyncio.to_thread(_log_episode, "implementing", {"runtime": rt_name, "code_len": len(code)})
                    await asyncio.to_thread(_set_state, ExecutionState.IMPLEMENTING, {})
                    yield _sse("tool_start", {"action": action, "label": f"Running {rt_name}..."})
                    result = await asyncio.to_thread(
                        _runtime_registry.execute, rt_name, code, {"task": req.effective_text[:120]}
                    )
                    exec_output = result.output if result.success else result.error
                    yield _sse("tool_result", {
                        "output": exec_output,
                        "plots": result.plots,
                        "files": [],
                    })
                    plots.extend(result.plots)

            elif action == "project_gen":
                skill_name = "project_gen"
                project = plan.get("project") or {}
                proj_files = project.get("files", [])

                if proj_files:
                    yield _sse("tool_start", {"action": "project_gen", "label": "Generating project files..."})
                    proj_name = re.sub(r"[^a-zA-Z0-9_-]", "_", project.get("name", "project"))
                    proj_dir = PROJECTS_DIR / proj_name
                    proj_dir.mkdir(parents=True, exist_ok=True)

                    for f in proj_files:
                        fname = os.path.basename(f.get("filename", "untitled.m"))
                        content = f.get("content", "")
                        safe_path = (proj_dir / fname).resolve()
                        if not safe_path.is_relative_to(proj_dir.resolve()):
                            logger.warning("Skipping file with path traversal: %s", fname)
                            continue
                        safe_path.write_text(content, encoding="utf-8")
                        files.append({
                            "path": f"{proj_name}/{fname}",
                            "filename": fname,
                            "language": f.get("language", "matlab"),
                            "content": content[:5000],
                            "url": f"/projects/{proj_name}/{fname}",
                        })
                    yield _sse("tool_result", {
                        "output": f"Created project **{proj_name}** with {len(files)} files.",
                        "plots": [],
                        "files": files,
                    })

            elif action == "multi_agent_swarm":
                skill_name = "multi_agent_swarm"
                dag_data = plan.get("dag_plan", {})
                from matclaw.core.task_router import TaskRouter, DAGPlan
                router = TaskRouter()
                try:
                    target_plan = DAGPlan(**dag_data)
                    router.build_dag(target_plan)
                    order = router.get_execution_order()

                    # node_id → agent output text (fed as context to downstream nodes)
                    context_chain: dict[str, str] = {}

                    for node_id in order:
                        node = router.nodes[node_id]
                        agent_def = (
                            router.agent_registry.get_agent(node.agent_id)
                            if node.agent_id else None
                        )
                        agent_label = agent_def.name if agent_def else (node.agent_id or "MatClaw")
                        yield _sse("tool_start", {
                            "action": "swarm_step",
                            "label": f"[{agent_label}] {node.description}",
                        })

                        # Build task prompt — include upstream outputs as context
                        upstream_ctx = ""
                        for inp in node.inputs:
                            for prev_id, prev_out in context_chain.items():
                                prev_node = router.nodes.get(prev_id)
                                if prev_node and inp in prev_node.outputs:
                                    upstream_ctx += f"\n\nOutput from upstream node '{prev_id}' ({inp}):\n{prev_out}"

                        node_tool = node.execution_payload.get("tool", "")
                        node_code = (node.execution_payload.get("code") or "").strip()
                        task_text = node.execution_payload.get("task", node.description)
                        if upstream_ctx:
                            task_text = f"{task_text}{upstream_ctx}"

                        agent_system = agent_def.system_prompt if agent_def else MATCLAW_PERSONA

                        # ── Path A: direct MATLAB execution ──────────────────
                        if node_tool == "run_matlab" and node_code:
                            exec_output, node_plots = await asyncio.to_thread(
                                _run_matlab_and_collect, node_code, task_text
                            )
                            agent_result = exec_output
                            plots.extend(node_plots)
                            router.mark_status(node_id, "completed", result=agent_result)
                            context_chain[node_id] = agent_result
                            yield _sse("tool_result", {
                                "output": f"**[{agent_label}]** {agent_result[:800]}",
                                "plots": node_plots,
                                "files": [],
                            })

                        # ── Path B: LLM generates MATLAB code, then execute ──
                        elif node_tool == "run_matlab":
                            _node_intent = _classify_intent(task_text)
                            _node_boost = ""
                            if _node_intent == "simulation":
                                _node_boost = _SIMULATION_BOOST + _VISUALIZATION_BOOST
                            elif _node_intent == "visualization":
                                _node_boost = _VISUALIZATION_BOOST
                            codegen_system = (
                                "You are an expert MATLAB engineer writing self-contained scripts.\n"
                                + _node_boost
                                + "\nRespond with ONLY a fenced MATLAB code block:\n"
                                "```matlab\n% your code here\n```"
                            )
                            raw = await asyncio.to_thread(
                                call_chat_completion,
                                provider=settings.llm.provider,
                                model=settings.llm.model,
                                system=codegen_system,
                                messages=[{"role": "user", "content": task_text}],
                                api_key=settings.llm.api_key,
                                base_url=getattr(settings.llm, "base_url", None),
                                max_tokens=2048,
                            )
                            generated_code = _extract_matlab_for_node(raw)
                            if generated_code and len(generated_code) >= 10:
                                exec_output, node_plots = await asyncio.to_thread(
                                    _run_matlab_and_collect, generated_code, task_text
                                )
                                agent_result = exec_output
                                plots.extend(node_plots)
                            else:
                                agent_result = "Could not generate MATLAB code for this node."
                                node_plots = []
                            router.mark_status(node_id, "completed", result=agent_result)
                            context_chain[node_id] = agent_result
                            yield _sse("tool_result", {
                                "output": f"**[{agent_label}]** {agent_result[:800]}",
                                "plots": node_plots,
                                "files": [],
                            })

                        # ── Path C: LLM-only (text output) ───────────────────
                        else:
                            agent_result = await asyncio.to_thread(
                                call_chat_completion,
                                provider=settings.llm.provider,
                                model=settings.llm.model,
                                system=agent_system,
                                messages=[{"role": "user", "content": task_text}],
                                api_key=settings.llm.api_key,
                                base_url=getattr(settings.llm, "base_url", None),
                                max_tokens=512,
                            )
                            router.mark_status(node_id, "completed", result=agent_result)
                            context_chain[node_id] = agent_result
                            yield _sse("tool_result", {
                                "output": f"**[{agent_label}]** {agent_result[:800]}",
                                "plots": [],
                                "files": [],
                            })

                    output = f"Multi-agent swarm completed: {len(order)} node(s) executed across the DAG."
                except Exception as e:
                    logger.error("Swarm failure", exc_info=True)
                    yield _sse("tool_start", {"action": "error", "label": "Swarm Routing Failed"})
                    yield _sse("tool_result", {"output": str(e), "plots": [], "files": []})
                    output = f"Failed to execute DAG: {e}"

            elif action == "query_memory":
                skill_name = "query_memory"
                yield _sse("tool_start", {"action": "query_memory", "label": "Searching memory..."})
                mem_lines: list[str] = []
                try:
                    semantic = memory.query_context(req.effective_text, n_results=5)
                    for r in semantic:
                        meta = r.get("metadata", {})
                        req_text = meta.get("request", r.get("document", ""))
                        if req_text:
                            mem_lines.append(f"- [{meta.get('skill', '')}] {req_text}")
                except Exception:
                    pass
                recent = tracker.list_experiments(last_n=5)
                for exp in recent:
                    req_t = (exp.params or {}).get("request", "")
                    if req_t:
                        mem_lines.append(f"- [{exp.skill_name}] {req_t}")
                yield _sse("tool_result", {
                    "output": "\n".join(mem_lines[:10]) if mem_lines else "No past runs found.",
                    "plots": [],
                    "files": [],
                })

            # ── Phase 3: Done ────────────────────────────────────────────
            elapsed = int((time.time() - t0) * 1000)
            await asyncio.to_thread(_log_episode, "completed", {"elapsed_ms": elapsed, "skill": skill_name, "plots": plots})
            await asyncio.to_thread(_set_state, ExecutionState.COMPLETED, {"elapsed_ms": elapsed})
            yield _sse("done", {
                "skill": skill_name,
                "elapsed_ms": elapsed,
                "reply": reply,
                "plots": plots,
                "files": files,
            })

        except Exception as exc:
            logger.exception("run_nl_stream failed")
            try:
                await asyncio.to_thread(_set_state, ExecutionState.FAILED, {"error": str(exc)})
            except Exception:
                pass
            yield _sse("error", {"message": str(exc)})
            yield _sse("done", {"skill": "error", "elapsed_ms": int((time.time() - t0) * 1000), "reply": str(exc), "plots": [], "files": []})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Enterprise: Metrics ──────────────────────────────────────────────────────

@app.get("/metrics")
def get_metrics():
    """Prometheus-style metrics snapshot + production SLO hints (read-only)."""
    snap = _metrics.snapshot()
    prod = getattr(settings, "production", None)
    snap["production"] = {
        "max_agentic_wall_seconds": getattr(prod, "max_agentic_wall_seconds", None) if prod else None,
        "agentic_max_concurrent": getattr(prod, "agentic_max_concurrent", None) if prod else None,
        "soft_cost_cap_usd_per_task": getattr(prod, "soft_cost_cap_usd_per_task", None) if prod else None,
        "memory_inject_enabled": getattr(prod, "memory_inject_enabled", None) if prod else None,
    }
    return snap


@app.get("/api/daemon/status")
def daemon_status():
    """Daemon heartbeat status."""
    return _heartbeat.status()


# ── Enterprise: Runtimes ──────────────────────────────────────────────────────

@app.get("/api/runtimes")
def list_runtimes():
    """List all registered execution runtimes and their availability."""
    return _runtime_registry.list_runtimes()


@app.post("/api/runtimes/{runtime_name}/execute")
async def execute_runtime(runtime_name: str, body: dict):
    """Directly execute code on a named runtime. Body: {code: str}"""
    code = body.get("code", "")
    if not code:
        raise HTTPException(status_code=400, detail="code is required")
    result = await asyncio.to_thread(
        _runtime_registry.execute, runtime_name, code, {"task": code[:120]}
    )
    return {
        "success": result.success,
        "output": result.output,
        "plots": result.plots,
        "elapsed_ms": result.elapsed_ms,
        "error": result.error,
    }


# ── Enterprise: Tools ─────────────────────────────────────────────────────────

@app.get("/api/tools")
def list_tools():
    """List all registered tools with their manifests."""
    return [t.model_dump() for t in tool_registry.list_tools()]


@app.post("/api/tools/{tool_name}/run")
async def run_tool(tool_name: str, inputs: dict):
    """Execute a registered tool by name."""
    from matclaw.tools.base import ToolContext
    tool = tool_registry.get(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    result = await tool.run(inputs, ToolContext())
    return {"success": result.success, "output": result.output,
            "error": result.error, "artifacts": result.artifacts}


# ── Enterprise: Scheduler ─────────────────────────────────────────────────────

@app.get("/api/scheduler/tasks")
def list_scheduled_tasks():
    return [t.model_dump() for t in _scheduler.list_tasks()]


@app.post("/api/scheduler/tasks")
def create_scheduled_task(body: dict):
    import time as _t
    task = ScheduledTask(
        id=str(uuid.uuid4())[:8],
        name=body.get("name", "unnamed"),
        cron_expression=body.get("cron_expression", "@hourly"),
        runtime=body.get("runtime", "shell"),
        command=body.get("command", ""),
        enabled=body.get("enabled", True),
        tenant_id=body.get("tenant_id", "default"),
        created_at=int(_t.time()),
    )
    return _scheduler.add_task(task).model_dump()


@app.delete("/api/scheduler/tasks/{task_id}")
def delete_scheduled_task(task_id: str):
    if not _scheduler.remove_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}


# ── Enterprise: Auth / API Keys ───────────────────────────────────────────────

class CreateKeyRequest(BaseModel):
    label: str
    role: str = "developer"
    tenant_id: str = "default"
    rate_limit_rpm: int = 60


@app.post("/api/auth/keys")
def create_api_key(req: CreateKeyRequest):
    from matclaw.api.auth import Role
    raw_key, ak = _key_store.create_key(
        label=req.label,
        role=req.role,  # type: ignore[arg-type]
        tenant_id=req.tenant_id,
        rate_limit_rpm=req.rate_limit_rpm,
    )
    return {"raw_key": raw_key, **ak.safe_dict()}


@app.get("/api/auth/keys")
def list_api_keys():
    return [k.safe_dict() for k in _key_store.list_keys()]


@app.delete("/api/auth/keys/{key_id}")
def revoke_api_key(key_id: str):
    if not _key_store.revoke_key(key_id):
        raise HTTPException(status_code=404, detail="Key not found")
    return {"ok": True}


@app.get("/api/audit")
def get_audit_log(limit: int = 100):
    return _key_store.get_audit_log(limit=limit)


# ── Enterprise: Gateway Webhook ───────────────────────────────────────────────

@app.post("/api/gateway/webhook")
async def webhook_gateway(req: WebhookRequest):
    """
    Generic inbound webhook — routes any text message through the MatClaw NL pipeline.
    Slack slash commands, Discord webhooks, n8n, Zapier can all POST here.
    """
    request_id = str(uuid.uuid4())
    output = ""
    plots: list[str] = []

    t0 = time.time()
    try:
        skill, _ = route_nl_message(req.text)
        if skill in ("run_matlab",):
            result = await asyncio.to_thread(
                _runtime_registry.execute, "matlab", req.text, {"task": req.text}
            )
            output = result.output
            plots = getattr(result, "plots", [])
        else:
            raw = await asyncio.to_thread(
                _llm_chat, req.text, MATCLAW_PERSONA, None, 2048
            )
            plan = _parse_llm_response(raw)
            output = plan.get("reply") or raw[:500]
    except Exception as exc:
        output = f"Error: {exc}"

    return WebhookResponse(
        text=output,
        channel=req.channel,
        chat_id=req.chat_id,
        plots=plots,
        request_id=request_id,
    )


# ── SPA catch-all — must be LAST route ───────────────────────────────────────
@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa(full_path: str):
    """Serve the React SPA for any non-API path when frontend is built."""
    index = _FRONTEND_DIST / "index.html"
    if index.is_file():
        return FileResponse(str(index))
    return JSONResponse(
        {"error": "Frontend not built. Run: cd web && npm run build"},
        status_code=404,
    )
