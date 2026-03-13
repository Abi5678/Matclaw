#!/usr/bin/env bash
# One-click launch: check .env, pull images, start MatClaw daemon with docker compose.
# Usage: ./scripts/launch.sh   (run from repo root)

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f .env ]]; then
  echo "ERROR: .env file not found in $REPO_ROOT"
  echo "Create .env with at least TELEGRAM_BOT_TOKEN (and optionally TELEGRAM_CHAT_ID)."
  echo "See .env.example or the project README for variables."
  exit 1
fi

echo "Pulling latest images..."
docker compose pull 2>/dev/null || true

echo "Building and starting MatClaw daemon..."
docker compose up -d --build

echo ""
echo "✅ MatClaw daemon is starting."
echo ""
echo "Next steps:"
echo "  1. Open Telegram and find your bot (search by the bot username from @BotFather)."
echo "  2. Send /start to the bot, then run:  /status"
echo "  3. Use /audit, /report [project_id], /run <skill_name>, or /sync as needed."
echo ""
echo "View logs:  docker compose logs -f matclaw-daemon"
echo "Stop:       docker compose down"
