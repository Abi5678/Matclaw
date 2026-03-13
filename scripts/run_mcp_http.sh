#!/usr/bin/env bash
# Start MatClaw MCP server with streamable-http for NAT/LLM clients.
# Usage: ./scripts/run_mcp_http.sh   (from repo root)
# Server: http://127.0.0.1:9901/mcp

set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

source .venv/bin/activate 2>/dev/null || true
export PYTHONPATH="$REPO_ROOT"

echo "Starting MatClaw MCP server at http://127.0.0.1:9901/mcp"
echo "Use with NAT: nat run --config_file workflows/nat_matclaw_mcp.yml --input 'Plot a sine wave'"
echo ""
python -m src.matclaw.mcp.server --http
