# MatClaw — Setup Guide

MatClaw is an AI assistant for MATLAB engineers. It lets you write, run, and debug MATLAB code through a chat interface, orchestrate multi-agent workflows, and manage long-running experiments — all from your browser.

---

## What you need

| Requirement | Notes |
|---|---|
| A computer running macOS, Linux, or Windows | Windows users should use Docker (see below) |
| An LLM API key | NVIDIA, Anthropic, OpenAI, or Google |
| MATLAB (optional) | Only needed if you want to execute MATLAB code directly |
| Docker (optional) | Easiest install path if you don't need MATLAB |

---

## Option A — Docker (recommended if you don't need MATLAB)

**Requires:** Docker Desktop — download at [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)

### 1. Download MatClaw

```bash
git clone https://github.com/Abi5678/Matclaw.git
cd Matclaw
```

Or download and unzip the release from the GitHub Releases page.

### 2. Add your API key

```bash
cp .env.example .env
```

Open `.env` in any text editor and fill in your key. Example for NVIDIA:

```
MATCLAW_LLM__PROVIDER=nvidia
MATCLAW_LLM__MODEL=nvidia/llama-3.1-nemotron-ultra-253b-v1
NVIDIA_API_KEY=nvapi-xxxxxxxxxxxxxxxx
```

See [Choosing an LLM provider](#choosing-an-llm-provider) for other options.

### 3. Start

```bash
docker compose up
```

First run takes 2–5 minutes to build. Subsequent starts are instant.

### 4. Open

Go to **http://localhost:8000** in your browser.

### Stopping

```bash
docker compose down        # stop (keeps your data)
docker compose down -v     # stop and wipe all stored data
```

---

## Option B — Native install (required for MATLAB integration)

**Requires:** Python 3.11+, Node.js 18+

### 1. Download MatClaw

```bash
git clone https://github.com/Abi5678/Matclaw.git
cd Matclaw
```

### 2. Run the installer

```bash
chmod +x install.sh && ./install.sh
```

This will:
- Create a Python virtual environment
- Install all dependencies
- Build the web interface
- Create a `matclaw` command on your system

### 3. Add your API key

The installer creates a `.env` file. Open it and fill in your key:

```
MATCLAW_LLM__PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-xxxxxxxxxxxxxxxx
```

For MATLAB integration, also set:

```
MATCLAW_MATLAB__ENABLED=true
```

### 4. Start

```bash
matclaw
```

Your browser will open automatically at **http://localhost:8000**.

If the `matclaw` command isn't found, run `./start.sh` from the project folder instead.

---

## Choosing an LLM provider

Open `.env` and uncomment **one** of the following blocks:

### NVIDIA (default — best for engineering tasks)
```
MATCLAW_LLM__PROVIDER=nvidia
MATCLAW_LLM__MODEL=nvidia/llama-3.1-nemotron-ultra-253b-v1
NVIDIA_API_KEY=nvapi-...
```
Get a key at [build.nvidia.com](https://build.nvidia.com)

### Anthropic Claude
```
MATCLAW_LLM__PROVIDER=anthropic
MATCLAW_LLM__MODEL=claude-sonnet-4-6
ANTHROPIC_API_KEY=sk-ant-...
```
Get a key at [console.anthropic.com](https://console.anthropic.com)

### OpenAI
```
MATCLAW_LLM__PROVIDER=openai-compatible
MATCLAW_LLM__MODEL=gpt-4o
MATCLAW_LLM__BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
```
Get a key at [platform.openai.com](https://platform.openai.com)

### Google Gemini
```
MATCLAW_LLM__PROVIDER=google
MATCLAW_LLM__MODEL=gemini-1.5-pro
GOOGLE_API_KEY=AIza...
```
Get a key at [aistudio.google.com](https://aistudio.google.com)

You can also add and switch models at any time from **Settings → Models** inside the app — no restart needed.

---

## What MatClaw can do

### Chat
Ask anything about MATLAB, engineering, or your data. MatClaw writes and runs code, generates plots, and explains results.

> *"Plot a Bode diagram for a second-order system with ωn = 10 and ζ = 0.5"*
> *"Why is my PID controller oscillating?"*
> *"Generate a unit test for this function"*

### Agents
Create specialised AI sub-agents with custom system prompts and tool access. Use **Smart Generate** to create an agent from a brief description, or **Optimize Prompt** to expand a rough idea into a detailed system prompt.

### Swarm / DAG
Send a complex task to a multi-agent swarm. MatClaw breaks it into a dependency graph and routes each step to the right specialist agent.

### Code Editor
Open generated `.m` files directly in the built-in Monaco editor. Edit and click **Run** to execute in MATLAB without leaving the browser.

### Settings → Models
Add any OpenAI-compatible API endpoint as a custom model. Click a model to make it active — takes effect immediately for the next message.

---

## MATLAB setup (native install only)

MatClaw connects to MATLAB via the MATLAB Engine for Python. Requirements:

- MATLAB R2021a or later
- MATLAB Engine for Python installed:
  ```bash
  cd "$(matlab -batch "disp(matlabroot)")/extern/engines/python"
  python setup.py install
  ```
- `.env` must have `MATCLAW_MATLAB__ENABLED=true`

When MATLAB is connected, the status indicator in the top bar turns **green**. If it shows red, click it to reconnect.

---

## Data and privacy

All your session history, agent definitions, and model configurations are stored **locally** on your machine:

| File | Contents |
|---|---|
| `.matclaw_sessions.sqlite3` | Chat history |
| `.matclaw_episodic.sqlite3` | Task memory |
| `.matclaw_chromadb/` | Long-term semantic memory |
| `.matclaw_models.json` | Saved model configurations |

Nothing is sent anywhere except your chosen LLM provider's API.

---

## Troubleshooting

**The app opens but shows "LLM error"**
→ Check your API key in `.env`. Make sure there are no extra spaces or quotes around the value.

**MATLAB shows as offline (red dot)**
→ Make sure MATLAB is running before starting MatClaw. Click the red dot in the app to reconnect.

**`matclaw` command not found after install**
→ Run `./start.sh` from the project folder, or open a new terminal tab and try again.

**Docker build fails on first run**
→ Make sure Docker Desktop is running. Try `docker compose build --no-cache` then `docker compose up`.

**Port 8000 already in use**
→ Change the port in `.env`: set `MATCLAW_PORT=8080`, then access at http://localhost:8080. For Docker, change the port mapping in `docker-compose.yml` to `"8080:8000"`.

---

## Getting help

Open an issue at [github.com/Abi5678/Matclaw/issues](https://github.com/Abi5678/Matclaw/issues)
