#!/usr/bin/env bash
set -euo pipefail

ROOT="${LIFEOS_ROOT:-/home/ubuntu/hermis-life-os}"
DAY="${1:-$(date +%F)}"

mkdir -p \
  "$ROOT/raw/captures" \
  "$ROOT/memory/ledger" \
  "$ROOT/memory/review" \
  "$ROOT/memory/curated" \
  "$ROOT/wiki" \
  "$ROOT/state" \
  "$ROOT/reports/morning" \
  "$ROOT/reports/nightly" \
  "$ROOT/reports/weekly" \
  "$ROOT/research/nightly" \
  "$ROOT/data/prayer" \
  "$ROOT/data/hydration" \
  "$ROOT/data/finance" \
  "$ROOT/logs" \
  "$ROOT/backups"

ensure_file() {
  local path="$1"
  local title="$2"
  if [ ! -f "$path" ]; then
    printf '# %s\n\n' "$title" > "$path"
  fi
}

ensure_file "$ROOT/raw/captures/$DAY.md" "Raw Captures $DAY"
ensure_file "$ROOT/memory/ledger/$DAY.md" "Memory Ledger $DAY"
ensure_file "$ROOT/reports/nightly/$DAY-triage.md" "Triage Report $DAY"
ensure_file "$ROOT/research/nightly/$DAY.md" "Nightly Research - $DAY"

echo "prepared Life OS day: $DAY"
