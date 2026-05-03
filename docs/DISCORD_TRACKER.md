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
LIFEOS_ROOT=/home/ubuntu/hermis-life-os
TRACKER_DB=/home/ubuntu/hermis-life-os/data/lifeos_tracker.db
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

Create these channels, or override the names in env:

- `#prayer-tracker`
- `#habits`
- `#finance-tracker`
- `#work-tracker`

## Run Locally

```bash
scripts/run_discord_tracker.sh
```

The script creates `.venv-discord-tracker`, installs `apps/discord_tracker/requirements.txt`, and starts the bot.

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
`draft_parse_json`, then marked unreviewed. The bot does not turn normal work
tracker messages into final tasks.

Nightly work automation should run:

```bash
scripts/process_work_reviews.py <YYYY-MM-DD> --all-open
```

`process_work_reviews.py` mirrors the finance review safety model: it fetches
unreviewed/unclear captures, calls the Hermis work reviewer, validates JSON,
then confirms, corrects/splits, ignores with an explicit reason, or writes
clarification questions. Only confirmed review output creates `work_items`.

Work window:

- Timezone: `Africa/Casablanca`
- Window: `14:00-23:00`

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
- `!work add <text>` explicitly creates confirmed work from command text.
- `!work list` shows active confirmed work.
- `!work today` shows confirmed work due or scheduled today.
- `!work focus` shows a focus list for the 14:00-23:00 Casablanca work window.
- `!work done <id>` marks a confirmed item done.
- `!work block <id> <reason>` marks a confirmed item blocked.
- `!work wait <id> <reason>` marks a confirmed item waiting.
- `!work review` shows confirmed work plus unreviewed/unclear captures.
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
10. Post `send client update tomorrow` in `#work-tracker` and confirm a work capture is created, not a confirmed work item.
11. Run `!work review` and confirm it shows confirmed work plus the unreviewed capture.
12. Check `data/prayer/`, `data/hydration/`, `data/finance/`, and `data/work/` for daily `.jsonl` and `.md` files.
13. Restart the systemd service and confirm it returns to `active (running)`.

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
- AlAdhan response parsing using a fixture

## Troubleshooting

- If commands do nothing, enable Message Content Intent in the Discord Developer Portal and confirm the bot has channel permissions.
- If reactions are ignored, confirm `DISCORD_OWNER_IDS` contains the numeric user ID of the reacting account.
- If prayer times do not load, check network access and the AlAdhan city, country, and method values.
- If the service exits immediately, inspect `journalctl -u hermis-discord-tracker -n 100 --no-pager`.
- If logs are not written, confirm the service user can write to `data/` and the SQLite database path.
