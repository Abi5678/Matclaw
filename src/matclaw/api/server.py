"""
MatClaw FastAPI backend — serves the React Control Plane UI.
Endpoints:
  POST /api/run          — NL → skill dispatch
  GET  /api/skills       — list available skills
  GET  /api/plots        — list saved plot files
  GET  /plots/{filename} — serve a plot image / GIF
  GET  /health           — liveness check
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

from src.matclaw.config.base_config import MatClawSettings
from src.matclaw.matlab.matlab_bridge import MatlabBridge
from src.matclaw.memory.memory_manager import MemoryManager
from src.matclaw.core.experiment import ExperimentTracker
from src.matclaw.llm.llm_client import call_chat_completion, call_chat_completion_stream, _extract_code_from_reasoning
from src.matclaw.skills import list_skills, load_skill_logic
from src.matclaw.api.nl_router import route_nl_message
from src.matclaw.api.demos import find_demo
from src.matclaw.agents.registry import AgentRegistry, AgentDefinition
from src.matclaw.core.static_analyzer import StaticAnalyzer
from src.matclaw.memory.episodic_memory import EpisodicMemoryManager, Episode
from src.matclaw.core.state_manager import AsyncStateTracker, ExecutionState
from src.matclaw.memory.session_store import SessionStore

import uuid
from datetime import datetime

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
- "project_gen": Create multi-file projects in any language (MATLAB, Python, HTML, etc.)
- "query_memory": Recall past runs and experiments
- "multi_agent_swarm": Break a complex task into a DAG and delegate to specialized agents.
- "none": Just have a conversation

Given the user's message and conversation history, respond with ONLY a valid JSON object (no markdown fences):
{
  "reply": "Your warm, conversational response. Use markdown formatting.",
  "action": "run_matlab" | "project_gen" | "query_memory" | "multi_agent_swarm" | "none",
  "code": "Raw MATLAB code if action is run_matlab. null otherwise.",
  "project": {"name": "project_name", "description": "...", "files": [{"filename": "main.m", "language": "matlab", "content": "..."}]} or null,
  "dag_plan": {"nodes": [{"id":"node_1", "description":"do x", "inputs":[], "outputs":["data"], "agent_id":"agent-id"}]} or null
}

Rules for code generation:
- Use ONLY built-in MATLAB functions (plot, surf, mesh, fft, disp, fprintf, etc.)
- Do NOT call LLM, AI, GPT, Claude, MatClaw, or any non-MATLAB function
- For animations, use drawnow inside loops
- For projects, put each function in its own .m file; first file is the entry point
"""

def _build_messages(
    user_text: str,
    system: str,
    history: list[dict[str, str]] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Build (system_arg, messages) for the LLM call. Shared by sync and streaming paths."""
    # Dynamically append registered agent context to the system prompt
    try:
        from src.matclaw.agents.registry import AgentRegistry
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
    # Try to extract the first {...} block
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    # LLM sometimes omits outer braces — wrap and retry
    if cleaned.startswith('"'):
        try:
            return json.loads("{" + cleaned + "}")
        except json.JSONDecodeError:
            pass

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

PLOTS_DIR = ROOT / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

# Connect to the running MATLAB session on startup
logger.info("Connecting to MATLAB session...")
try:
    bridge.start()
    if bridge.is_healthy():
        logger.info("MATLAB connected successfully.")
    else:
        # Try connecting to any available shared session
        import matlab.engine as _me  # type: ignore
        sessions = _me.find_matlab()
        if sessions:
            bridge.settings.session_name = sessions[0]
            bridge.start()
            logger.info("Connected to shared MATLAB session: %s", sessions[0])
        else:
            logger.warning("No MATLAB session found — bridge will retry on first request.")
except Exception as _e:
    logger.warning("MATLAB startup skipped: %s", _e)

# ── app ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="MatClaw API", version="2.0.0")

# Initialize subsystems
agent_registry = AgentRegistry()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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
    text: str
    session_id: str = "default"
    history: list[dict[str, str]] = []  # [{"role": "user", "text": "..."}, ...]
    mode: str = "auto"  # "ask" | "auto" | "plan" | "agentic"


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
    if "drawnow" not in code and ("for " not in code and "while " not in code):
        return None

    frame_dir = PLOTS_DIR / f"_frames_{base_name}"
    frame_dir.mkdir(exist_ok=True)

    frame_dir_str = str(frame_dir).replace("'", "''")
    inject = (
        f"mc_frame_dir = '{frame_dir_str}';\n"
        "mc_frame_n = 0;\n"
    )
    # Wrap every drawnow with a frame save
    instrumented = re.sub(
        r"drawnow\s*;?",
        lambda m: (
            "drawnow; mc_frame_n = mc_frame_n + 1; "
            "if mod(mc_frame_n,3)==0, "
            "exportgraphics(gcf, fullfile(mc_frame_dir, sprintf('frame_%04d.png', mc_frame_n)), 'Resolution', 72); "
            "end"
        ),
        code,
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
    from src.matclaw.matlab.matlab_bridge import MatlabCallRequest

    with tempfile.NamedTemporaryFile(suffix=".m", delete=False, mode="w",
                                     encoding="ascii", errors="replace") as f:
        f.write(code)
        script_path = f.name.replace("\\", "/")

    try:
        escaped = script_path.replace("'", "''")
        matlab_cmd = f"evalc(\"run('{escaped}')\")"
        req = MatlabCallRequest(function="eval", args=[matlab_cmd], nargout=1)
        result = bridge.call(req)
        if result.success:
            return True, str(result.result or "").strip()
        return False, result.error or "Unknown MATLAB error"
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def _run_matlab_and_collect(code: str, req_text: str) -> tuple[str, list[str]]:
    """Run MATLAB code, collect stdout + plots, return (output_text, plot_urls)."""
    ts = int(time.time())
    plot_name = f"plot_{ts}"
    plots: list[str] = []

    gif_url = _capture_animated_gif(code, plot_name)

    if gif_url:
        plots.append(gif_url)
        output = f"Animation rendered: {gif_url.split('/')[-1]}"
    else:
        # inject plot-save at the end
        save_code = (
            f"\ntry, exportgraphics(gcf, '{PLOTS_DIR}/{plot_name}.png', 'Resolution', 100); "
            f"catch, try, print(gcf, '-dpng', '-r100', '{PLOTS_DIR}/{plot_name}.png'); end; end"
        )
        ok, stdout = _run_via_script(code + save_code)
        if not ok and stdout:
            # Return concise error — strip internal temp file paths from the message
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
        os.execv(shell, [shell])
    
    loop = asyncio.get_running_loop()
    
    async def read_from_pty():
        try:
            while True:
                data = await loop.run_in_executor(None, os.read, fd, 1000000)
                if not data:
                    break
                await websocket.send_text(data.decode("utf-8", errors="replace"))
        except Exception as e:
            logger.error(f"PTY read error: {e}")
            try:
                await websocket.close()
            except:
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
                    os.write(fd, data.encode("utf-8"))
        except Exception as e:
            logger.error(f"WS read error: {e}")

    t1 = asyncio.create_task(read_from_pty())
    t2 = asyncio.create_task(read_from_ws())
    
    await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
    for t in [t1, t2]:
        if not t.done():
            t.cancel()
    try:
        os.close(fd)
    except:
        pass

@app.get("/health")
def health():
    return {"status": "ok", "matlab": bridge.is_healthy()}

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
        name = (name_match.group(1) or name_match.group(2)).strip() if name_match else req.topic[:60]

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


# ── Model Manager ────────────────────────────────────────────────────────────
MODELS_FILE = ROOT / ".matclaw_models.json"

class ModelConfig(BaseModel):
    id: str = ""
    provider: str
    model: str
    api_key: str
    base_url: str | None = None
    label: str
    active: bool = False

def _load_models() -> list[dict]:
    if MODELS_FILE.exists():
        return json.loads(MODELS_FILE.read_text())
    return []

def _save_models(models: list[dict]):
    MODELS_FILE.write_text(json.dumps(models, indent=2))

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
        raw_resp = _llm_chat(
            req.text,
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
            fallback_skill, _ = route_nl_message(req.text)
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
                code = find_demo(req.text) or ""

            # Guard against hallucinated functions
            if code:
                bad = [r"\bLLM\b", r"\bAI\s*\(", r"\bMatClaw\s*\(", r"\bClaude\s*\("]
                if any(re.search(p, code, re.IGNORECASE) for p in bad):
                    code = find_demo(req.text) or ""

            if code:
                exec_output, plots = _run_matlab_and_collect(code, req.text)
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
                    fname = f.get("filename", "untitled.m")
                    content = f.get("content", "")
                    (proj_dir / fname).write_text(content, encoding="utf-8")
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
                semantic = memory.query_context(req.text, n_results=5)
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

        try:
            # ── Phase 0: Mark as RESEARCHING ────────────────────────────
            await asyncio.to_thread(_log_episode, "researching", {"user_text": req.text, "session_id": req.session_id})
            await asyncio.to_thread(_set_state, ExecutionState.RESEARCHING, {"text": req.text})

            sys_arg, messages = _build_messages(
                req.text, MATCLAW_PERSONA,
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

            # Fallback to keyword router if JSON parsing failed
            if not plan or not reply:
                fallback_skill, _ = route_nl_message(req.text)
                if fallback_skill in ("run_matlab",):
                    action = "run_matlab"
                    plan["code"] = None
                elif fallback_skill == "query_memory":
                    action = "query_memory"
                else:
                    action = "none"
                    reply = raw if raw else "I'm not sure how to help with that."

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
                if not code:
                    code = find_demo(req.text) or ""

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
                    exec_output, plots = await asyncio.to_thread(
                        _run_matlab_and_collect, code, req.text
                    )
                    yield _sse("tool_result", {
                        "output": exec_output,
                        "plots": plots,
                        "files": [],
                    })

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
                        fname = f.get("filename", "untitled.m")
                        content = f.get("content", "")
                        (proj_dir / fname).write_text(content, encoding="utf-8")
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
                from src.matclaw.core.task_router import TaskRouter, DAGPlan
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
                            codegen_system = (
                                agent_system
                                + "\n\nRespond ONLY with a fenced MATLAB code block. "
                                  "No prose before or after.\n"
                                  "Example:\n```matlab\n% code here\n```"
                            )
                            raw = await asyncio.to_thread(
                                call_chat_completion,
                                provider=settings.llm.provider,
                                model=settings.llm.model,
                                system=codegen_system,
                                messages=[{"role": "user", "content": task_text}],
                                api_key=settings.llm.api_key,
                                base_url=getattr(settings.llm, "base_url", None),
                                max_tokens=1024,
                            )
                            code_blocks = re.findall(
                                r"```(?:matlab)?\s*\n(.*?)```", raw,
                                re.DOTALL | re.IGNORECASE,
                            )
                            generated_code = (
                                code_blocks[-1].strip() if code_blocks
                                else _extract_code_from_reasoning(raw)
                            )
                            if generated_code:
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
                    semantic = memory.query_context(req.text, n_results=5)
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

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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
