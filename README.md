# MatClaw — Autonomous Agentic OS for MATLAB

**MatClaw is the first Autonomous Agentic OS for MATLAB engineers.** It runs a persistent Research–Plan–Implement (RPI) loop: it researches context from long-term memory, plans actions, and executes them against a live MATLAB kernel—with self-correction, file-triggered sentry, and remote control via Telegram. All autonomous actions are recorded in `LAB_JOURNAL.md`, the single source of truth for the lab.

---

## Key Innovations

| Innovation | Description |
|------------|-------------|
| **Persistent RPI Loop** | Research (ChromaDB + context), Plan (lessons learned + targets), Execute (skill run). Autonomous self-correction via the debug agent and environment auditing before heavy runs. |
| **Digital Twin Sync** | Rsync or cloud-folder sync between laptop and server. `/sync` in Telegram pulls from a remote; synced files flow through the Sentry pipeline into `data_in`. |
| **Mobile Gateway** | Remote orchestration and plot delivery via Telegram: `/status`, `/audit`, `/report`, `/run <skill>`, `/sync`, and HITL "Proceed? [Yes/No]" for long-running tasks. Vision analyst summarizes `.png` artifacts before sending. |
| **Long-Term Memory** | ChromaDB-backed MemoryManager stores artifacts and embeddings. Skills feed "lessons learned" into every RPI cycle; reports and PID runs are persisted for future context. |

---

## Architecture

MatClaw is built for a **multi-agent** workflow: an **Architect** (orchestrator), **Researcher** (context + memory), **Coder** (MATLAB execution), and **Reviewer** (validation and rollback). The **Model Context Protocol (MCP)** layer exposes tools and resources so LLM clients can drive the MATLAB kernel—run code, query memory, trigger skills, and read status. The daemon keeps the MATLAB bridge warm, the Sentry watching `data_in`, and the RPI executor ready for skill runs and HITL-gated long jobs. `LAB_JOURNAL.md` logs every Sentry-triggered run with timestamps and artifact links; it is the authoritative record of what MatClaw did and when.

---

## Quick Start

1. **Configure `.env`** with your Telegram bot token and optional chat ID:
   ```bash
   TELEGRAM_BOT_TOKEN=your_bot_token_from_BotFather
   TELEGRAM_CHAT_ID=optional_default_chat_id
   ```

2. **Build the MATLAB Engine for Python wheel** (required for the MATLAB bridge). On a machine with MATLAB installed:
   ```bash
   cd $MATLABROOT/extern/engines/python
   python setup.py bdist_wheel
   cp dist/matlab_engine*.whl /path/to/MatClaw/wheels/
   ```
   **Note:** `$MATLABROOT` is your MATLAB installation root (e.g. `/usr/local/MATLAB/R2024a`). The wheel is under `extern/engines/python`. Uncomment the wheel `COPY` and `pip install` block in the Dockerfile if you run MatClaw in Docker.

3. **Launch the stack:**
   ```bash
   ./scripts/launch.sh
   ```
   Then open Telegram, find your bot, send `/start`, and run `/status` for the first check. Use `/audit`, `/report`, `/run <skill_name>`, or `/sync` as needed.

---

## Skill Extensibility

Skills live under **`src/matclaw/skills/`**. Each skill is a folder:

- **`SKILL.md`** — Instruction manual for the agent: when to use, inputs/outputs, constraints, and how the RPI loop applies (Research → Plan → Execute).
- **`logic.py`** — Python bridge exposing `research()`, `plan()`, `execute()`, and a one-shot `run()`; uses `MatlabBridge` and Pydantic.
- **`templates/`** — Predefined `.m` (or `.mlx`) scripts the skill adds to the MATLAB path and invokes.

To teach MatClaw a new engineering domain: add a folder `src/matclaw/skills/<skill_name>/`, write **SKILL.md** (purpose, when to use, I/O, constraints, RPI steps), implement **logic.py** with `research` / `plan` / `execute` / `run`, and drop any MATLAB templates into **templates/**. The daemon discovers skills via `list_skills()` and loads instructions with `get_skill_instructions(skill_name)`; the RPI executor and Sentry can then invoke the new skill by name. No core code changes required—extend by adding a new skill directory and its SKILL.md.

---

## Lab Journal

All autonomous actions (Sentry-triggered runs, artifact links) are appended to **`LAB_JOURNAL.md`** (config: `lab_journal.path`). Each line follows: `- **YYYY-MM-DD HH:MM AM/PM** — {summary} — [artifact1](path) [artifact2](path)`. Treat it as the source of truth for what MatClaw did and which files it used.

---

## NVIDIA Nemotron & NeMo Agent Toolkit

MatClaw supports **NVIDIA Nemotron** for NL routing (Telegram natural language) and **NVIDIA NeMo Agent Toolkit (NAT)** for powerful ReAct-style agents that use MatClaw's MCP tools.

### Nemotron LLM (default)
Set `NVIDIA_API_KEY` in `.env`. The NL router uses Nemotron for intent classification. Configure via `MATCLAW_LLM__PROVIDER=nvidia` and `MATCLAW_LLM__MODEL=nvidia/nvidia-nemotron-nano-9b-v2`.

### NeMo Agent Toolkit (NAT)
Use NAT as an MCP client to drive MatClaw with ReAct reasoning:

1. **Start MatClaw MCP server** (HTTP mode):
   ```bash
   python -m src.matclaw.mcp.server --http
   # Server: http://127.0.0.1:9901/mcp
   ```

2. **Install NAT with MCP**: `pip install "nvidia-nat[mcp]"`

3. **Run NAT workflow**:
   ```bash
   export NVIDIA_API_KEY=your_nvapi_key
   nat run --config_file workflows/nat_matclaw_mcp.yml --input "Plot a sine wave"
   nat run --config_file workflows/nat_matclaw_mcp.yml --input "Check my workspace"
   ```

4. **Or launch NAT UI** for interactive chat:
   ```bash
   nat serve --config_file workflows/nat_matclaw_mcp.yml
   ```

The workflow (`workflows/nat_matclaw_mcp.yml`) connects NAT to MatClaw's MCP tools: `run_matlab_code`, `trigger_skill`, `run_workspace_auditor`, `run_pid_optimizer`, `run_report_generator`, `list_available_skills`.

---

## Status

🚀 RPI loop, ChromaDB memory, Sentry, HITL gate, Vision analyst, Digital Twin sync, Docker + one-click launch.  
🧪 MCP server with tools; NVIDIA Nemotron + NAT integration for agentic MATLAB workflows.

---

## Project Progress & Next Updates

- **Current progress**
  - Core MatClaw architecture is in place: multi-agent RPI loop, MATLAB bridge, Sentry, HITL, and lab journal wiring.
  - Streamlit UI (`src/matclaw/ui/app.py`) is running against the Python 3.11 virtual environment.
  - MCP server and NVIDIA Nemotron / NeMo Agent Toolkit integration are wired via `pyproject.toml` and `workflows/nat_matclaw_mcp.yml`.
- **Next near-term updates**
  - Harden and document a few reference skills under `src/matclaw/skills/` (e.g., PID tuning, workspace auditing, reporting).
  - Add automated tests and a lightweight CI workflow (lint, type-check, and smoke tests for the MCP server and UI).
  - Publish more detailed setup and troubleshooting docs for MATLAB Engine, Docker, and remote (Telegram) orchestration.
