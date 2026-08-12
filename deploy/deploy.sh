#!/usr/bin/env bash
# Production deploy for DiscordBot on the VPS.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/apps/discordbot}"
cd "$APP_DIR"

echo "==> Fetching origin/main"
git fetch --depth=1 origin main
git checkout -B main origin/main

if [[ ! -f .env ]]; then
  echo "ERROR: .env missing in $APP_DIR (not in git — restore from backup)" >&2
  exit 1
fi

echo "==> Building and starting discord-bot"
# Force-recreate so bind-mounted Lavalink config (application.yml) is picked up.
docker compose -f docker-compose.prod.yml up --build -d --force-recreate

echo "==> Container status"
docker compose -f docker-compose.prod.yml ps
echo "==> Deploy complete ($(git rev-parse --short HEAD))"
