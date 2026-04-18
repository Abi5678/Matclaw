STRUCTURE.md
============

This file mirrors the high-level architecture and layout planned for MatClaw. Implementation will evolve from this scaffold.

## 1. High-Level Architecture

MatClaw is an always-on Python daemon that:

- Exposes an MCP-compliant interface to LLMs.
- Bridges to a local MATLAB instance via the MATLAB Engine for Python.
- Maintains long-term memory in a vector database.
- Monitors a filesystem "dropbox" for `.mat` / `.csv` files.
- Sends remote updates via messaging gateways (WhatsApp/Telegram).

Core responsibilities:

- **mcp-layer**: Request/response protocol with LLM clients (tools, resources).
- **agent-core**: Planning, orchestration, and routing between subsystems.
- **matlab-bridge**: Robust interface to MATLAB, including session lifecycle.
- **memory**: Vector-based long-term memory + metadata store.
- **watchdog**: Filesystem monitoring for data ingestion.
- **messaging**: Outbound/inbound remote notifications and control.

## Level 2 (Implemented)

- **Self-correct**: On MATLAB failure, the debug agent captures the error, parses it (e.g. not enough inputs, index exceeds), finds similar local `.m` files, suggests a heuristic fix (e.g. `nargin` check), writes a temp `.m` file, adds it to the MATLAB path, re-runs the call. If the fix succeeds and `apply_fix_with_bak` is true, the original file is backed up as `*.m.bak` and the fix is written to the source.
- **Manages files**: The watchdog monitors a configurable directory (`watch_path`, default `data/`) for new or modified `.mat` and `.csv` files, parses them (scipy/pandas), and registers metadata and a short note in the memory store (session `matclaw:watchdog:files`).

## Skills (Implemented)

Every skill lives under `src/matclaw/skills/[skill-name]/`:

- **SKILL.md**: Instruction manual for the agent (when to use, I/O, constraints, RPI loop).
- **logic.py**: Python bridge with `research()`, `plan()`, `execute()`, and a `run()` one-shot; uses `MatlabBridge` and Pydantic.
- **templates/**: Pre-defined `.m` scripts (e.g. workspace_audit.m, pid_eval.m) that the skill adds to the MATLAB path and calls.

**Installed skills:**

- **workspace_auditor**: Scans `whos` and `license`; returns variable list, total bytes, license info, and OOM/license warnings. Use before heavy runs or when the user asks about memory/license.
- **pid_optimizer**: Observation loop to tune Kp, Ki, Kd: run simulation → get rise time and overshoot → plan new gains → repeat until stable or max iterations. Uses a 2nd-order plant + PID template (Control System Toolbox); can be swapped for a Simulink model.

Skill discovery: `skills.list_skills()`, `skills.get_skill_instructions(name)`, `skills.load_skill_logic(name)`.

## Memory Database (Step 1)

- **chromadb** added to dependencies; **MemoryManager** in `src/matclaw/memory/memory_manager.py`.
- **store_artifact(key, metadata, vector)**: stores an artifact in ChromaDB (key, metadata flattened for ChromaDB, optional vector or auto-embed from summary).
- **query_context(query_string)**: returns historical artifacts relevant to the query (for "lessons learned" in RPI).
- When a skill (e.g. PID Optimizer) finishes successfully, **RPIExecutor.execute()** and **run_on_file()** automatically call **MemoryManager.store_artifact()** with the final parameters/summary.

## Proactive Sentry (Step 2)

- **src/matclaw/sentry/watchdog.py**: **SentryWatchdog** monitors **/data_in** (config: `sentry.data_in_path`).
- When a **new file** is detected, it initiates **RPIExecutor.run_on_file(path)**:
  - **.mat**: runs **workspace_auditor** and prints a summary of variables to the terminal.
  - If filename contains **'PID'**: suggests running **pid_optimizer** (printed to terminal).

## RPI + Memory (Step 3)

- **RPIExecutor** lives in **src/matclaw/core/rpi_executor.py** (uses MatlabBridge; not inside matlab_bridge.py to keep the bridge thin).
- **Research**: calls **MemoryManager.query_context()** with the current project/query to load historical data.
- **Plan**: incorporates **lessons_learned** (top results from query_context) into **plan_data["lessons_incorporated"]** for use in proposed MATLAB code or skill runs.
- **Execute**: runs the requested skill; on success stores the result as an artifact.
- **run_rpi(skill_name, context=..., **kwargs)**: full loop Research → Plan → Execute for a skill.

## Autonomous Lab Log (Million-Dollar Feature)

- **LAB_JOURNAL.md** (config: `lab_journal.path`): every time the **Sentry** triggers a run, MatClaw appends a line.
- **Format**: `- **YYYY-MM-DD HH:MM AM/PM** — {summary} — [artifact1](path) [artifact2](path)`
- **Summary**: e.g. "Detected .mat file: data.mat. Variables: 5, Total: 12.00 MB" or "File name suggests PID tuning; consider running pid_optimizer."
- **Artifact linking**: the journal line links to the `.mat` (and in future `.png` plots) used in that run.


## 2. Repository Folder Hierarchy (Planned)

```text
matclaw/
  ├─ src/
  │   ├─ matclaw/
  │   │   ├─ __init__.py
  │   │   │
  │   │   ├─ config/
  │   │   │   ├─ __init__.py
  │   │   │   ├─ base_config.py
  │   │   │   ├─ env_loader.py
  │   │   │   └─ logging_config.py
  │   │   │
  │   │   ├─ mcp/
  │   │   │   ├─ __init__.py
  │   │   │   ├─ server.py
  │   │   │   ├─ tools/
  │   │   │   │   ├─ __init__.py
  │   │   │   │   ├─ matlab_tools.py
  │   │   │   │   ├─ memory_tools.py
  │   │   │   │   └─ system_tools.py
  │   │   │   └─ resources/
  │   │   │       ├─ __init__.py
  │   │   │       └─ status_resource.py
  │   │   │
  │   │   ├─ core/
  │   │   │   ├─ __init__.py
  │   │   │   ├─ agent_loop.py
  │   │   │   ├─ task_router.py
  │   │   │   ├─ models.py
  │   │   │   └─ state_manager.py
  │   │   │
  │   │   ├─ matlab/
  │   │   │   ├─ __init__.py
  │   │   │   ├─ engine_manager.py
  │   │   │   ├─ session_pool.py
  │   │   │   ├─ call_wrappers.py
  │   │   │   └─ data_bridge.py
  │   │   │
  │   │   ├─ memory/
  │   │   │   ├─ __init__.py
  │   │   │   ├─ memory.py
  │   │   │   └─ schemas.py
  │   │   │
  │   │   ├─ watchdog/
  │   │   │   ├─ __init__.py
  │   │   │   ├─ watcher.py
  │   │   │   ├─ parsers.py
  │   │   │   └─ ingestion_pipeline.py
  │   │   │
  │   │   ├─ messaging/
  │   │   │   ├─ __init__.py
  │   │   │   ├─ base_client.py
  │   │   │   ├─ telegram_client.py
  │   │   │   ├─ whatsapp_client.py
  │   │   │   ├─ router.py
  │   │   │   └─ commands.py
  │   │   │
  │   │   ├─ api/
  │   │   │   ├─ __init__.py
  │   │   │   └─ http_status_endpoint.py
  │   │   │
  │   │   ├─ util/
  │   │   │   ├─ __init__.py
  │   │   │   ├─ time_utils.py
  │   │   │   ├─ error_handling.py
  │   │   │   └─ serialization.py
  │   │   │
  │   │   └─ daemon.py
  │   │
  │   └─ cli/
  │       ├─ __init__.py
  │       └─ matclaw_cli.py
  │
  ├─ matlab/
  │   ├─ +matclaw/
  │   │   ├─ init.m
  │   │   ├─ run_task.m
  │   │   └─ utils/
  │   └─ examples/
  │
  ├─ configs/
  │   ├─ default.yaml
  │   ├─ dev.yaml
  │   └─ prod.yaml
  │
  ├─ scripts/
  │   ├─ dev_start.sh
  │   ├─ install_matlab_engine.sh
  │   └─ migrate_memory_schema.py
  │
  ├─ .env.example
  ├─ pyproject.toml or requirements.txt
  ├─ README.md
  └─ STRUCTURE.md
```

