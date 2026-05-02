#!/usr/bin/env bash
set -euo pipefail

ROOT="${LIFEOS_ROOT:-/home/ubuntu/hermis-life-os}"
FAIL=0

check_ok() {
  echo "OK: $1"
}

check_warn() {
  echo "WARN: $1"
}

check_fail() {
  echo "FAIL: $1"
  FAIL=1
}

need_dir() {
  if [ -d "$ROOT/$1" ]; then
    check_ok "dir exists: $1"
  else
    check_fail "missing dir: $1"
  fi
}

for dir in \
  raw/captures memory/ledger memory/review memory/curated wiki state \
  reports/morning reports/nightly reports/weekly research scripts \
  apps/discord_tracker data/prayer data/hydration data/finance data/daily-summary logs backups
do
  need_dir "$dir"
done

if [ -f "$ROOT/.env.discord-tracker" ]; then
  check_ok ".env.discord-tracker exists"
  for key in DISCORD_BOT_TOKEN DISCORD_OWNER_IDS PRAYER_CHANNEL_NAME HYDRATION_CHANNEL_NAME TIMEZONE PRAYER_CITY PRAYER_COUNTRY PRAYER_METHOD; do
    if grep -Eq "^[[:space:]]*$key=" "$ROOT/.env.discord-tracker"; then
      check_ok "$key present"
    else
      check_fail "$key missing"
    fi
  done
  if grep -Eq "^[[:space:]]*FINANCE_CHANNEL_NAME=" "$ROOT/.env.discord-tracker"; then
    check_ok "FINANCE_CHANNEL_NAME present"
  else
    check_warn "FINANCE_CHANNEL_NAME missing; default finance-tracker will be used"
  fi
else
  check_fail ".env.discord-tracker missing"
fi

if systemctl is-active --quiet hermis-discord-tracker.service; then
  check_ok "hermis-discord-tracker active"
else
  check_warn "hermis-discord-tracker not active"
fi

if systemctl is-active --quiet openviking.service; then
  check_ok "openviking active"
else
  check_warn "openviking not active"
fi

if curl -fsS --max-time 5 http://127.0.0.1:1933/health >/dev/null 2>&1; then
  check_ok "OpenViking health endpoint OK"
else
  check_warn "OpenViking health endpoint not responding"
fi

if [ -f "$ROOT/data/lifeos_tracker.db" ]; then
  check_ok "tracker DB exists"
  LIFEOS_ROOT="$ROOT" python3 - <<'PY'
import os
import sqlite3
from pathlib import Path

path = Path(os.environ["LIFEOS_ROOT"]) / "data" / "lifeos_tracker.db"
required = {
    "prayer_schedule",
    "posted_reminders",
    "prayer_events",
    "hydration_daily",
    "hydration_events",
    "hydration_reaction_events",
    "hydration_snoozes",
    "finance_transactions",
    "finance_recurring_items",
    "finance_savings_goals",
    "finance_parse_reviews",
}
con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
try:
    found = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
finally:
    con.close()
missing = sorted(required - found)
if missing:
    print("FAIL: missing tracker DB tables: " + ", ".join(missing))
    raise SystemExit(1)
print("OK: tracker DB schema has required tables")
PY
else
  check_warn "tracker DB missing"
fi

if [ "${1:-}" = "--tests" ]; then
  PYTHONDONTWRITEBYTECODE=1 "$ROOT/.venv-discord-tracker/bin/python" -m unittest discover "$ROOT/apps/discord_tracker/tests"
fi

exit "$FAIL"
