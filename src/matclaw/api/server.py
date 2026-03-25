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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
from src.matclaw.llm.llm_client import call_chat_completion
from src.matclaw.skills import list_skills, load_skill_logic
from src.matclaw.api.nl_router import route_nl_message
from src.matclaw.api.demos import find_demo

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── global singletons ───────────────────────────────────────────────────────
settings = MatClawSettings()
bridge = MatlabBridge(settings=settings.matlab)
memory = MemoryManager(persist_directory=str(ROOT / ".matclaw_chromadb"))
tracker = ExperimentTracker(str(ROOT / ".matclaw_experiments.sqlite3"))

PERSONA = (
    "You are MatClaw, an expert MATLAB AI agent. "
    "Respond concisely in plain text — no JSON, no code blocks unless generating MATLAB code. "
    "When asked to write MATLAB code, return ONLY the raw code with no markdown fences."
)

def _llm_chat(user_text: str, system: str = PERSONA) -> str:
    """
    Call the LLM. For NVIDIA provider, embed the system prompt in the user
    message to avoid the tool-call bleed that returns None content.
    """
    try:
        provider = settings.llm.provider.lower()
        if provider == "nvidia":
            # NVIDIA Nemotron returns None content when given a separate system role
            # Embed system instructions directly in the user message instead
            combined = f"{system}\n\n{user_text}"
            messages = [{"role": "user", "content": combined}]
            sys_arg = ""
        else:
            messages = [{"role": "user", "content": user_text}]
            sys_arg = system

        result = call_chat_completion(
            provider=settings.llm.provider,
            model=settings.llm.model,
            system=sys_arg,
            messages=messages,
            api_key=None,
            max_tokens=4096,
        )
        return result or ""
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return ""

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/plots", StaticFiles(directory=str(PLOTS_DIR)), name="plots")


# ── request / response models ────────────────────────────────────────────────
class RunRequest(BaseModel):
    text: str
    session_id: str = "default"


class RunResponse(BaseModel):
    skill: str
    output: str
    plots: list[str] = []
    metrics: dict[str, Any] = {}
    elapsed_ms: int = 0


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
@app.get("/health")
def health():
    return {"status": "ok", "matlab": bridge.is_healthy()}


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
    skill_name, skill_kwargs = route_nl_message(req.text)
    output = ""
    plots: list[str] = []
    metrics: dict[str, Any] = {}

    try:
        # ── run_matlab: execute code directly ──────────────────────────────
        if skill_name == "run_matlab":
            code = skill_kwargs.get("code", "")
            if not code:
                # Try curated demo library first (reliable for known animations)
                code = find_demo(req.text) or ""

            if not code:
                matlab_system = (
                    "You are a MATLAB code generator. Output ONLY valid, runnable MATLAB code. "
                    "Rules: "
                    "1. Use ONLY built-in MATLAB functions (plot, surf, mesh, fft, disp, fprintf, etc). "
                    "2. Do NOT call any function named LLM, AI, GPT, Claude, MatClaw, or any non-MATLAB function. "
                    "3. Do NOT include explanations, comments, or markdown. "
                    "4. Do NOT use code fences. "
                    "5. Output raw MATLAB code only — the first character must be valid MATLAB syntax."
                )
                code_resp = _llm_chat(req.text, system=matlab_system)
                # strip markdown fences if present
                code = re.sub(r"```(?:matlab)?\s*|\s*```", "", code_resp).strip()
                # guard: reject if LLM failed or hallucinated non-MATLAB content
                bad_patterns = [r"\bLLM\b", r"\bAI\s*\(", r"\bMatClaw\s*\(", r"\bClaude\s*\("]
                llm_failed = not code
                if any(re.search(p, code, re.IGNORECASE) for p in bad_patterns) or llm_failed:
                    logger.warning("LLM failed to generate valid MATLAB. Raw: %s", code_resp[:300])
                    return RunResponse(
                        skill=skill_name,
                        output="Could not generate MATLAB code — the LLM API may be unavailable or the request was unclear. Try rephrasing, e.g. 'plot sin(x) from 0 to 2*pi'.",
                        plots=[], metrics={}, elapsed_ms=int((time.time() - t0) * 1000)
                    )
            output, plots = _run_matlab_and_collect(code, req.text)

        # ── query_memory ────────────────────────────────────────────────────
        elif skill_name == "query_memory":
            query = skill_kwargs.get("query", req.text)
            lines: list[str] = []

            # 1. Semantic recall from ChromaDB
            try:
                semantic = memory.query_context(query, n_results=5)
                for r in semantic:
                    meta = r.get("metadata", {})
                    req_text = meta.get("request", r.get("document", ""))
                    skill = meta.get("skill", "")
                    ts = meta.get("ts", "")
                    if req_text:
                        badge = f"[{skill}] " if skill else ""
                        lines.append(f"• {badge}{req_text}  ({ts[:10] if ts else ''})")
            except Exception as e:
                logger.warning("ChromaDB query failed: %s", e)

            # 2. Recent runs from ExperimentTracker
            recent = tracker.list_experiments(last_n=10)
            seen = {l for l in lines}
            for exp in recent[:5]:
                req_t = (exp.params or {}).get("request", "")
                if req_t and req_t not in seen:
                    started = str(exp.started_at)[:10] if exp.started_at else ''
                    lines.append(f"• [{exp.skill_name}] {req_t}  ({started})")
                    seen.add(req_t)

            if lines:
                output = f"Found {len(lines)} past runs matching your query:\n\n" + "\n".join(lines[:10])
            else:
                output = "No relevant memories found. Run some MATLAB commands first to build history."

        # ── all other skills ────────────────────────────────────────────────
        else:
            skill_fn = load_skill_logic(skill_name)
            if skill_fn is None:
                raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

            kwargs: dict[str, Any] = {
                "text": req.text,
                "matlab_bridge": bridge,
                "memory_manager": memory,
                "experiment_tracker": tracker,
                **skill_kwargs,
            }
            raw = skill_fn(**kwargs)

            # sanitise: if LLM bled a JSON tool-call into the response, replace it
            raw_str = str(raw) if raw is not None else ""
            if raw_str.lstrip().startswith(("{", "[")):
                raw_str = _llm_chat(req.text)

            output = raw_str
            plots = _save_plots_from_matlab()

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Skill %s failed", skill_name)
        output = f"Error running {skill_name}: {exc}"
        # log failure to tracker
        try:
            _exp = tracker.start_experiment(
                skill_name=skill_name,
                params={"request": req.text, "session_id": req.session_id},
                tags=[skill_name],
            )
            tracker.finish_experiment(_exp.experiment_id, status="failed", error=str(exc))
        except Exception:
            pass

    # final fallback
    if not output:
        output = _llm_chat(req.text)

    elapsed = int((time.time() - t0) * 1000)

    # ── persist every successful run to tracker + memory ─────────────────
    if output and not output.startswith("Error running"):
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
            logger.warning("Failed to log run to tracker/memory: %s", _log_exc)

    return RunResponse(skill=skill_name, output=output, plots=plots,
                       metrics=metrics, elapsed_ms=elapsed)
