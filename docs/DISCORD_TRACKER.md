# Discord Tracker Sidecar

This sidecar is a standalone Discord bot for Hermis Life OS. It does not depend on Hermes or the old FastAPI LifeOS backend.

## Files

- Bot code: `apps/discord_tracker/`
- Runtime database: `data/lifeos_tracker.db`
- Prayer logs: `data/prayer/YYYY-MM-DD.jsonl` and `data/prayer/YYYY-MM-DD.md`
- Hydration logs: `data/hydration/YYYY-MM-DD.jsonl` and `data/hydration/YYYY-MM-DD.md`
- Finance logs: `data/finance/YYYY-MM-DD.jsonl` and `data/finance/YYYY-MM-DD.md`
- Work logs: `data/work/YYYY-MM-DD.jsonl` and `data/work/YYYY-MM-DD.md`

## Setup

Create `.env.discord-tracker` in the workspace root:

```dotenv
DISCORD_BOT_TOKEN=
DISCORD_GUILD_ID=
DISCORD_OWNER_IDS=
PRAYER_CHANNEL_NAME=prayer-tracker
HYDRATION_CHANNEL_NAME=habits
FINANCE_CHANNEL_NAME=finance-tracker
WORK_CHANNEL_NAME=work-tracker
DAILY_PLAN_CHANNEL_NAME=daily-plan
REVIEW_CHANNEL_NAME=approval-queue
LIFEOS_ROOT=${HOME}/hermis-life-os
TRACKER_DB=${LIFEOS_ROOT}/data/lifeos_tracker.db
TIMEZONE=Africa/Casablanca
PRAYER_CITY=Casablanca
PRAYER_COUNTRY=Morocco
PRAYER_METHOD=21
PRAYER_CLOSE_NUDGE_MINUTES=10
HYDRATION_START_HOUR=9
HYDRATION_END_HOUR=22
HYDRATION_INTERVAL_MINUTES=90
HYDRATION_TARGET_COUNT=8
WORK_START_HOUR=14
WORK_END_HOUR=23
WORK_PREP_LEAD_MINUTES=60
WORK_MID_SHIFT_CHECKIN_ENABLED=false
WORK_SHUTDOWN_REVIEW_ENABLED=true
WORK_REMINDER_LOOKAHEAD_MINUTES=30
WORK_OVERDUE_GRACE_MINUTES=15
HERMES_HOME=${HOME}/.hermes/profiles/lifeos
HERMIS_WORK_AI_CMD=${HOME}/.local/bin/lifeos
HERMIS_WORK_AUTOMATION_AI_CMD=${HOME}/.local/bin/lifeos
HERMIS_REVIEW_AI_CMD=${HOME}/.local/bin/lifeos
MORNING_REVIEW_ENABLED=true
MORNING_REVIEW_HOUR=7
MORNING_REVIEW_MINUTE=40
REVIEW_ITEM_EXPIRY_HOURS=18
```

`DISCORD_OWNER_IDS` accepts comma-separated or space-separated numeric Discord user IDs. Only those users can log prayer and hydration reactions.
Only those users can use hydration, finance, and work logging/summary commands.

## Discord Permissions

The bot needs:

- View Channels
- Send Messages
- Embed Links
- Add Reactions
- Read Message History
- Use Message Content Intent for `!prayertoday`, `!water`, and `!hydration`
- Use Message Content Intent for finance channel capture and `!money` commands
- Use Message Content Intent for work channel capture and `!work` commands
- Use Message Content Intent for review-card replies and `!review` / `!morning`

Create these channels, or override the names in env:

Recommended server map:

```text
TEXT CHANNELS
  #general

HERMIS HOME
  #dashboard        High-level Hermis status and future dashboard summaries
  #daily-plan       Daily plan and morning summary
  #approval-queue   Today's Review Inbox, review cards, reactions, replies

HERMIS TRACKERS
  #prayer-tracker   Prayer reminders and reactions
  #habits           Hydration reminders and water logging
  #work-tracker     Work captures, reminders, review-gated AI suggestions
  #finance-tracker  Finance captures and review-first AI-led processing

LIFE AREAS
  #daily-adhkar
  #fitness-log
  #family-calendar
  #wife-commitments
  #ai-content
  #analytics
  #weekly-review

SYSTEM
  #system-notifications
  #audit-log
```

The active baked-in channels are `#daily-plan`, `#approval-queue`,
`#prayer-tracker`, `#habits`, `#work-tracker`, and `#finance-tracker`. The life
area channels are intentionally documented now even when dormant, so future
automation has stable places to land.

Sync categories, missing channels, and channel topics:

```bash
scripts/sync_discord_layout.py --dry-run
scripts/sync_discord_layout.py
```

## Run Locally

```bash
scripts/run_discord_tracker.sh
```

The script creates `.venv-discord-tracker`, installs `apps/discord_tracker/requirements.txt`, and starts the bot.

Work, finance, and generic review AI subprocesses use `HERMES_HOME=${HERMES_HOME:-$HOME/.hermes/profiles/lifeos}`, so systemd or shell profile drift cannot switch the Hermes profile unexpectedly.

## Install systemd Service

```bash
scripts/install_discord_tracker_service.sh
```

Useful service commands:

```bash
sudo systemctl status hermis-discord-tracker --no-pager
sudo journalctl -u hermis-discord-tracker -f
sudo systemctl restart hermis-discord-tracker
```

## Behavior

## Discord-First Review Workflow

The wiki and filesystem remain the durable source of truth. Discord is the main
review inbox:

```text
reports/questions/reviews
  -> review_items table + data/review logs + state/review-items.md
  -> Discord review cards
  -> owner reactions/replies
  -> AI interpretation + AI validation
  -> approved/rejected/clarification/pending fallback
```

Generic review items can represent morning report questions, memory candidates,
finance review items, work suggestions, open questions, commitment reviews,
unclear AI interpretations, and report follow-ups. Discord message bindings map
bot messages back to source items so direct replies attach to the right Life OS
item.

Review cards use simple reactions:

- `✅` approves the review item. For work AI suggestions, this calls the same
  safe `accept_work_ai_suggestion` path used by `!work accept`.
- `❌` rejects the review item. For pending work AI suggestions, this also
  rejects the suggestion.
- `❓` marks the item `needs_clarification` and posts a follow-up prompt.
- `📝` asks you to reply with details.

Only `DISCORD_OWNER_IDS` can mutate review state. Unknown reactions are ignored.

Replies to review cards go through `AIInputInterpreter` and then
`AIValidationPass`. Important changes are not silently persisted from raw AI
output; validation must pass, and durable writes still go through an explicit
approval or the existing safe automation flow.

Morning reports continue to be written to `reports/morning/`. The cron-delivered
Discord summary owns the normal morning report message. The bot's morning
publisher now posts one compact `Today's Review Inbox`, grouped by urgency, and
only posts the top actionable individual cards by default:

```text
!morning
!morning 2026-05-04
```

The daily morning review publisher is controlled by:

```dotenv
MORNING_REVIEW_ENABLED=true
MORNING_REVIEW_HOUR=7
MORNING_REVIEW_MINUTE=40
```

The existing cron-delivered Discord morning summary can remain in place without
duplicating the bot's review inbox; this layer adds durable item bindings and
reaction/reply handling.

Nightly fallback:

```bash
scripts/process_review_fallback.py <YYYY-MM-DD>
```

This safely auto-processes only high-confidence explicitly safe items, expires
unanswered low-risk items, writes
`reports/nightly/YYYY-MM-DD-review-fallback.md`, writes unresolved items to
`inbox/needs-answer/YYYY-MM-DD-review.md`, and lets the next morning report and
Discord review queue resurface anything still unclear.

Weekly automation health:

```bash
scripts/build_automation_health_report.py <YYYY-MM-DD>
```

This writes `reports/weekly/YYYY-MM-DD-automation-health.md` with counts for
auto-processed, approved, rejected, expired, resurfaced, and still-pending review
items plus top friction sources.

Prayer times are fetched daily from AlAdhan:

`https://api.aladhan.com/v1/timingsByCity/<DD-MM-YYYY>?city=<city>&country=<country>&method=<method>`

The bot stores Fajr, Dhuhr, Asr, Maghrib, and Isha. Each prayer window runs until the next prayer, with Isha running until next day Fajr.

Prayer reminders use this embed shape:

```text
title: 🕌 <Prayer> Reminder
description:
Prayer window: until `<YYYY-MM-DD HH:MM> UTC`
React now:
✅ on-time | 🕒 late | ❌ missed
footer:
prayer:<local_date>:<PrayerName>:<window_id>
```

Prayer reactions:

- `✅` logs `on_time`
- `🕒` logs `late`
- `❌` logs `missed`

Repeating the same owner status on the same prayer reminder is ignored. Changing
to a different status updates the stored prayer status.

Hydration reminders run between `HYDRATION_START_HOUR` and `HYDRATION_END_HOUR` every `HYDRATION_INTERVAL_MINUTES`.

Hydration reactions:

- `💧` increments 1
- `🥤` increments 2
- `💤` snoozes reminders for 30 minutes
- `❌` skips the reminder

The first owner reaction on a hydration reminder is logged. Repeated owner reactions
on the same reminder are ignored so hydration cannot double-count after remove/readd
or emoji changes.

Finance capture watches owner messages in `#finance-tracker` (or `FINANCE_CHANNEL_NAME`).
Normal text is captured as a raw source and placed in the finance review queue for
Hermis/nightly processing. The bot does not auto-parse Discord finance messages
into ledger transactions.

Nightly automation should run:

```bash
scripts/process_finance_reviews.py <YYYY-MM-DD> --all-open
```

Weekly finance rollup should run:

```bash
scripts/summarize_finance_week.py <week-ending-YYYY-MM-DD>
```

`process_finance_reviews.py` is plumbing only: it fetches review rows, calls the
Hermis AI finance resolver, validates the returned JSON, applies entries, and
writes clarification questions only for unclear notes. It must not infer
amounts/categories from raw human finance text. Manual `!money edit` is fallback,
not normal workflow.

Finance examples:

```text
spent 45 lunch
paid Netflix 12 USD
saved 300 emergency fund
salary 15000 MAD
```

Multiple entries in one Discord message are allowed. They stay together as one
review item until Hermis or `!money edit review:<id> ...` resolves them into
one or more ledger transactions.

Default currency is `MAD`. Non-MAD entries keep original currency and are not
normalized to MAD unless a later correction provides the MAD amount.

Finance categories:

`groceries`, `eating_out`, `transport`, `rent`, `utilities`, `subscriptions`,
`shopping`, `health`, `family`, `deen_charity`, `work_tools`, `education`,
`travel`, `fees_taxes`, `entertainment`, `savings`, `income`, `transfer`,
`unknown`.

Finance memory policy:

- Raw finance messages, tracker DB rows, and detailed daily logs stay local.
- Hermes/OpenViking should use `wiki/domains/money.md`, nightly/weekly finance summaries, and approved curated money memories.
- Durable money patterns go through `memory/review` before `memory/curated`; safe high-confidence facts may be auto-promoted by the nightly memory review.
- Weekly finance reports carry normal spend rollups. Daily reports mention finance only for commitments, promises to pay, or deadlines.

Work capture watches owner messages in `#work-tracker` (or `WORK_CHANNEL_NAME`).
Normal text is saved as a raw `work_captures` row with source metadata and
`draft_parse_json`, then Hermis drafts a pending `work_ai_suggestions` row.
The bot does not turn normal work tracker messages into final tasks.

Nightly work automation should run:

```bash
scripts/process_work_reviews.py <YYYY-MM-DD> --all-open
```

`process_work_reviews.py` mirrors the finance review safety model: it fetches
unreviewed/unclear captures, calls the Hermis work reviewer, validates JSON,
then creates pending AI suggestions for confirmed/split, ignored, or
clarification outcomes. Only `!work accept suggestion:<id>` creates `work_items`
or changes capture review state. `--apply` exists for explicit direct application.

Work window:

- Timezone: `Africa/Casablanca`
- Window: `14:00-23:00`

Work automation runs inside the same sidecar:

- `13:00`: prep nudge, idempotent per day
- `14:00`: start-of-shift plan, idempotent per day
- During work: due/scheduled reminders, overdue blocker prompts, waiting follow-ups
- `23:00`: shutdown review + `reports/work/YYYY-MM-DD-shutdown.md`, idempotent per day
- Reminder identity is stored in `work_automation_events`; overdue blocker prompts also write `work_blocker_prompts`.
- Automation messages call Hermis AI first. If AI fails, the sidecar sends the deterministic fallback and records `message_source=fallback`.

See `docs/WORK_ASSISTANT.md` for the full work assistant policy.

## Commands

- `!prayertoday` shows today's prayer windows.
- `!water [count] [note]` manually logs hydration. Example: `!water 2 after walk`.
- `!hydration` shows today's hydration count for owners.
- `!money today` shows today's finance totals.
- `!money month [YYYY-MM]` shows month finance totals.
- `!money review` lists finance captures waiting for Hermis/user review.
- `!money edit <id|tx:id|review:id> <corrected text>` updates a transaction or resolves a review. For reviews, corrected text can contain multiple lines.
- `!money void <id|tx:id|review:id>` voids a transaction or review item.
- `!work` shows today's confirmed work and open capture review count.
- `!work add <text>` saves a capture and creates a pending AI suggestion.
- `!work list` shows active confirmed work.
- `!work today` shows confirmed work due or scheduled today.
- `!work focus` shows a focus list for the 14:00-23:00 Casablanca work window.
- `!work automation` shows automation settings and nudges sent today.
- `!work plan` manually renders the start plan.
- `!work shutdown` manually renders shutdown review and writes the shutdown report.
- `!work done <id>` marks a confirmed item done.
- `!work block <id> <reason>` marks a confirmed item blocked.
- `!work wait <id> <reason>` marks a confirmed item waiting.
- `!work reschedule <id> <date/time>` moves a confirmed item. Examples: `2026-05-04`, `2026-05-04 16:30`, `16:30`.
- `!work blocker <id> <reason>` logs a structured blocker and marks the item blocked.
- `!work snooze <id> <duration>` suppresses nudges for an item. Examples: `30m`, `2h`.
- `!work clarify capture:<id> <answer>` answers a capture clarification for Hermis re-review.
- `!work review` shows confirmed work, pending AI suggestions, and unreviewed/unclear captures.
- `!work accept suggestion:<id>` accepts a pending AI suggestion and applies it if it changes work state.
- `!work correct suggestion:<id> <what to fix>` reruns AI with your correction and keeps the old suggestion as corrected.
- `!work reject suggestion:<id> <reason>` rejects a pending AI suggestion. Reason is required.
- `!review` lists open generic review items.
- `!review publish` posts unbound review cards to the current channel.
- `!morning [YYYY-MM-DD]` publishes the morning report summary and review cards.
- `!testprayer [PrayerName]` posts a short test prayer embed for smoke testing reactions.

## Smoke Tests

1. Start the bot without Hermes running.
2. Run `!prayertoday` in Discord and confirm today's schedule posts.
3. Run `!testprayer Fajr` in `#prayer-tracker`.
4. React `✅` as an owner and confirm the bot posts `Logged \`Fajr\` for YYYY-MM-DD: on_time.`
5. React from a non-owner account and confirm no log or confirmation is created.
6. Run `!water 2 after walk` and confirm hydration increments by 2.
7. React to a hydration reminder with `💧` or `🥤` and confirm the count updates.
8. Post `spent 45 lunch` in `#finance-tracker` and confirm a Hermis review item is created.
9. Post two lines of expenses in one message and confirm they stay as one review item.
10. Post `send client update tomorrow` in `#work-tracker` and confirm a work capture plus pending AI suggestion is created, not a confirmed work item.
11. Run `!work review` and confirm it shows pending AI suggestions plus the unreviewed capture.
12. Run `!review publish` and confirm review cards have `✅`, `❌`, `❓`, and `📝`.
13. Reply to a review card and confirm the bot acknowledges the reply instead of dropping it.
14. Run `scripts/process_review_fallback.py <YYYY-MM-DD>` with an expired item and confirm `inbox/needs-answer/YYYY-MM-DD-review.md` is written.
15. Check `data/prayer/`, `data/hydration/`, `data/finance/`, `data/work/`, and `data/review/` for daily `.jsonl` and `.md` files.
16. Restart the systemd service and confirm it returns to `active (running)`.

## Tests

```bash
.venv-discord-tracker/bin/python -m unittest discover apps/discord_tracker/tests
```

Covered areas:

- Footer parsing
- Owner validation
- Reaction mapping
- Hydration count updates and log file creation
- Finance review-first capture, multi-entry resolution, storage, idempotency, edit, void, and summaries
- Work review-first capture, draft parse isolation, multi-item confirmation, clarifications, ignored reasons, and commands storage
- Work automation idempotency, due reminders, overdue blocker prompts, waiting follow-ups, clarification surfacing, and Casablanca work-window checks
- Generic review item storage, Discord message bindings, review reactions, review replies, morning review digest batching, safe auto-processing, weekly automation health reporting, AI validation, and nightly fallback resurfacing
- AlAdhan response parsing using a fixture

## Troubleshooting

- If commands do nothing, enable Message Content Intent in the Discord Developer Portal and confirm the bot has channel permissions.
- If reactions are ignored, confirm `DISCORD_OWNER_IDS` contains the numeric user ID of the reacting account.
- If review replies are ignored, confirm the reply targets a bot review card and that `discord_message_bindings` contains the card message id.
- If review cards do not appear, run `!review publish` and inspect `state/review-items.md`.
- If morning review does not post, confirm `MORNING_REVIEW_ENABLED`, `DAILY_PLAN_CHANNEL_NAME`, and `REVIEW_CHANNEL_NAME`.
- If prayer times do not load, check network access and the AlAdhan city, country, and method values.
- If the service exits immediately, inspect `journalctl -u hermis-discord-tracker -n 100 --no-pager`.
- If logs are not written, confirm the service user can write to `data/` and the SQLite database path.
