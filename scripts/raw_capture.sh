#!/usr/bin/env bash
set -euo pipefail

ROOT="${LIFEOS_ROOT:-$HOME/hermis-life-os}"
TEXT="${*:-}"

if [ -z "$TEXT" ]; then
  echo "Usage: raw_capture.sh <anything you want to capture>" >&2
  exit 1
fi

DAY="$(date +%F)"
TS="$(date -Is)"
ID="$(date +%Y%m%dT%H%M%S%z)"
FILE="$ROOT/raw/captures/$DAY.md"

mkdir -p "$ROOT/raw/captures"

cat >> "$FILE" <<CAPTURE

---

capture_id: capture-$ID
timestamp: $TS
source: cli
status: raw
processed: false

$TEXT

CAPTURE

echo "saved raw capture: capture-$ID"
echo "file: $FILE"
