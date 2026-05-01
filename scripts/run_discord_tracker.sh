#!/usr/bin/env bash
set -euo pipefail

ROOT="${LIFEOS_ROOT:-/home/ubuntu/hermis-life-os}"
VENV="${DISCORD_TRACKER_VENV:-$ROOT/.venv-discord-tracker}"
REQ="$ROOT/apps/discord_tracker/requirements.txt"
STAMP="$VENV/.discord_tracker_requirements.sha256"

cd "$ROOT"

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi

CURRENT_REQUIREMENTS="$(sha256sum "$REQ" | awk '{print $1}')"
INSTALLED_REQUIREMENTS=""
if [ -f "$STAMP" ]; then
  INSTALLED_REQUIREMENTS="$(cat "$STAMP")"
fi

if [ "$CURRENT_REQUIREMENTS" != "$INSTALLED_REQUIREMENTS" ]; then
  "$VENV/bin/python" -m pip install -r "$REQ"
  printf '%s\n' "$CURRENT_REQUIREMENTS" > "$STAMP"
fi

exec "$VENV/bin/python" "$ROOT/apps/discord_tracker/bot.py"
