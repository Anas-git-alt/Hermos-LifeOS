#!/usr/bin/env bash
set -euo pipefail

ROOT="${LIFEOS_ROOT:-/home/ubuntu/hermis-life-os}"
BACKUP_DIR="${LIFEOS_BACKUP_DIR:-$ROOT/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/hermis-life-os-$STAMP.tar.gz"

mkdir -p "$BACKUP_DIR"

tar -C "$ROOT" -czf "$OUT" \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='.venv-discord-tracker' \
  --exclude='.codex' \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='logs/*.log' \
  --exclude='backups/*.tar.gz' \
  raw memory wiki state reports research scripts manifests docs apps data

echo "backup created: $OUT"
