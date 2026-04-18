#!/usr/bin/env bash
# MatClaw launcher — run from repo root (or use ./install.sh to regenerate).
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"
if [[ ! -d .venv ]]; then
  echo "No .venv found. Run:  chmod +x install.sh && ./install.sh"
  exit 1
fi
source .venv/bin/activate
exec python -m matclaw.api.main "$@"
