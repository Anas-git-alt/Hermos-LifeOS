#!/usr/bin/env bash
set -euo pipefail

ROOT="${LIFEOS_ROOT:-/home/ubuntu/hermis-life-os}"
DAY="${1:-$(date +%F)}"
REPORT="$ROOT/reports/morning/$DAY.md"
JOBS="${HERMES_LIFEOS_JOBS:-/home/ubuntu/.hermes/profiles/lifeos/cron/jobs.json}"
FAIL=0

ok() { echo "OK: $1"; }
fail() { echo "FAIL: $1"; FAIL=1; }
warn() { echo "WARN: $1"; }

if [ ! -f "$REPORT" ]; then
  fail "missing morning report: $REPORT"
else
  ok "morning report exists: $REPORT"
fi

if [ -f "$REPORT" ]; then
  for section in \
    "Top 3 Priorities" \
    "Due or Overdue Commitments" \
    "Deen Anchor" \
    "Health Anchor" \
    "Prayer / Hydration" \
    "Work / Money Anchor" \
    "Overnight Research" \
    "Memory Review Needed" \
    "One Next Action"
  do
    if grep -Fq "## $section" "$REPORT"; then
      ok "section present: $section"
    else
      fail "section missing: $section"
    fi
  done

  if grep -Eq '^\{|^\[|\"event\"|\"logged_at_utc\"|\"message_id\"' "$REPORT"; then
    fail "morning report appears to contain raw JSON/log data"
  else
    ok "no raw JSON/log dump detected"
  fi

  if grep -Fqi "latest not found" "$REPORT"; then
    fail "report contains latest-not-found text"
  else
    ok "no latest-not-found text"
  fi

  if grep -Fqi "unverified" "$REPORT" && grep -Eiq "recommend(s|ed)? .*(buy|purchase|install|deploy|subscribe)" "$REPORT"; then
    warn "unverified research appears near recommendation language; review manually"
  fi
fi

if [ -f "$JOBS" ]; then
  python3 - "$JOBS" <<'PY'
import json
import sys
from pathlib import Path

jobs = json.loads(Path(sys.argv[1]).read_text())["jobs"]
active_jobs = [j for j in jobs if j.get("enabled", True) and j.get("state") != "paused"]
job = next((j for j in jobs if j.get("id") == "a1abddcdcf79"), None)
if not job:
    print("FAIL: Discord morning summary cron job missing")
    raise SystemExit(1)
prompt = job.get("prompt", "")
if "using send_message" in prompt or "Then send this summary" in prompt:
    print("FAIL: Discord morning summary prompt still instructs manual send")
    raise SystemExit(1)
if "Do not call send_message" not in prompt:
    print("FAIL: Discord morning summary prompt lacks send_message guard")
    raise SystemExit(1)
if job.get("deliver") != "discord:#daily-plan":
    print(f"FAIL: Discord morning summary deliver target is {job.get('deliver')!r}")
    raise SystemExit(1)
if job.get("schedule", {}).get("expr") != "35 7 * * *":
    print(f"WARN: Discord morning summary schedule is {job.get('schedule', {}).get('expr')!r}")
print("OK: Discord morning summary cron prompt/delivery sane")

finance = next((j for j in jobs if j.get("id") == "finance-review-autoprocess"), None)
if not finance:
    print("FAIL: finance review auto processor cron job missing")
    raise SystemExit(1)
if not finance.get("enabled", True) or finance.get("state") == "paused":
    print("FAIL: finance review auto processor cron job is not active")
    raise SystemExit(1)
finance_prompt = finance.get("prompt", "")
required = [
    "scripts/process_finance_reviews.py",
    "--all-open",
    "scripts/summarize_finance_day.py",
]
missing = [item for item in required if item not in finance_prompt]
if missing:
    print("FAIL: finance cron prompt missing " + ", ".join(missing))
    raise SystemExit(1)
for forbidden in ["$(date +%F)", "deterministic", "parse_finance_message"]:
    if forbidden in finance_prompt:
        print(f"FAIL: finance cron prompt contains forbidden parser/date coupling: {forbidden}")
        raise SystemExit(1)
if "AI" not in finance_prompt and "ai" not in finance_prompt:
    print("FAIL: finance cron prompt does not identify AI-led review")
    raise SystemExit(1)
if finance.get("deliver") != "local":
    print(f"FAIL: finance cron deliver target is {finance.get('deliver')!r}")
    raise SystemExit(1)
if finance.get("schedule", {}).get("expr") != "0 1 * * *":
    print(f"WARN: finance cron schedule is {finance.get('schedule', {}).get('expr')!r}")
print("OK: finance review cron sane")

duplicate = next((j for j in jobs if j.get("id") == "e5aeeedaeeb1"), None)
if duplicate and duplicate.get("enabled", True) and duplicate.get("state") != "paused":
    print("FAIL: duplicate legacy finance cron e5aeeedaeeb1 is still active")
    raise SystemExit(1)
print("OK: duplicate legacy finance cron disabled")

memory = next((j for j in active_jobs if j.get("skill") == "life-memory-review" or "life-memory-review" in (j.get("skills") or [])), None)
if not memory:
    print("FAIL: nightly memory review cron job missing")
    raise SystemExit(1)
if memory.get("deliver") != "local":
    print(f"FAIL: memory review cron deliver target is {memory.get('deliver')!r}")
    raise SystemExit(1)
if memory.get("schedule", {}).get("expr") != "10 2 * * *":
    print(f"WARN: memory review cron schedule is {memory.get('schedule', {}).get('expr')!r}")
print("OK: nightly memory review cron sane")

expected = {
    "finance-review-autoprocess": "0 1 * * *",
    "180421089e9e": "30 1 * * *",
    memory.get("id"): "10 2 * * *",
    "c70e18134a87": "30 2 * * *",
    "12df41197bb9": "0 3 * * *",
    "87eefd62d1c2": "25 7 * * *",
    "4d661d5b4b5d": "30 7 * * *",
    "a1abddcdcf79": "35 7 * * *",
}
for job_id, expr in expected.items():
    scheduled = next((j for j in jobs if j.get("id") == job_id), {})
    actual = scheduled.get("schedule", {}).get("expr")
    if actual != expr:
        print(f"WARN: job {job_id} schedule is {actual!r}, expected {expr!r}")
print("OK: cron order checks complete")
PY
else
  fail "cron jobs file missing: $JOBS"
fi

exit "$FAIL"
