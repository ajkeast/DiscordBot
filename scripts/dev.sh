#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi

source .venv/bin/activate
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ] || ! grep -qE '^DISCORD_TOKEN=.+$' .env; then
  echo "Missing .env — copy .env.example to .env and paste values from your hosting dashboard."
  exit 1
fi

echo "Reminder: stop the hosted bot before running locally."
.venv/bin/python bot.py
