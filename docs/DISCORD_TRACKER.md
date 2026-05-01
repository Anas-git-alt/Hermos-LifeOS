# Discord Tracker Sidecar

This sidecar is a standalone Discord bot for Hermis Life OS. It does not depend on Hermes or the old FastAPI LifeOS backend.

## Files

- Bot code: `apps/discord_tracker/`
- Runtime database: `data/lifeos_tracker.db`
- Prayer logs: `data/prayer/YYYY-MM-DD.jsonl` and `data/prayer/YYYY-MM-DD.md`
- Hydration logs: `data/hydration/YYYY-MM-DD.jsonl` and `data/hydration/YYYY-MM-DD.md`

## Setup

Create `.env.discord-tracker` in the workspace root:

```dotenv
DISCORD_BOT_TOKEN=
DISCORD_GUILD_ID=
DISCORD_OWNER_IDS=
PRAYER_CHANNEL_NAME=prayer-tracker
HYDRATION_CHANNEL_NAME=habits
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
```

`DISCORD_OWNER_IDS` accepts comma-separated or space-separated numeric Discord user IDs. Only those users can log prayer and hydration reactions.

## Discord Permissions

The bot needs:

- View Channels
- Send Messages
- Embed Links
- Add Reactions
- Read Message History
- Use Message Content Intent for `!prayertoday`, `!water`, and `!hydration`

Create these channels, or override the names in env:

- `#prayer-tracker`
- `#habits`

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

Hydration reminders run between `HYDRATION_START_HOUR` and `HYDRATION_END_HOUR` every `HYDRATION_INTERVAL_MINUTES`.

Hydration reactions:

- `💧` increments 1
- `🥤` increments 2
- `💤` snoozes reminders for 30 minutes
- `❌` skips the reminder

## Commands

- `!prayertoday` shows today's prayer windows.
- `!water [count] [note]` manually logs hydration. Example: `!water 2 after walk`.
- `!hydration` shows today's hydration count.
- `!testprayer [PrayerName]` posts a short test prayer embed for smoke testing reactions.

## Smoke Tests

1. Start the bot without Hermes running.
2. Run `!prayertoday` in Discord and confirm today's schedule posts.
3. Run `!testprayer Fajr` in `#prayer-tracker`.
4. React `✅` as an owner and confirm the bot posts `Logged \`Fajr\` for YYYY-MM-DD: on_time.`
5. React from a non-owner account and confirm no log or confirmation is created.
6. Run `!water 2 after walk` and confirm hydration increments by 2.
7. React to a hydration reminder with `💧` or `🥤` and confirm the count updates.
8. Check `data/prayer/` and `data/hydration/` for daily `.jsonl` and `.md` files.
9. Restart the systemd service and confirm it returns to `active (running)`.

## Tests

```bash
.venv-discord-tracker/bin/python -m unittest discover apps/discord_tracker/tests
```

Covered areas:

- Footer parsing
- Owner validation
- Reaction mapping
- Hydration count updates and log file creation
- AlAdhan response parsing using a fixture

## Troubleshooting

- If commands do nothing, enable Message Content Intent in the Discord Developer Portal and confirm the bot has channel permissions.
- If reactions are ignored, confirm `DISCORD_OWNER_IDS` contains the numeric user ID of the reacting account.
- If prayer times do not load, check network access and the AlAdhan city, country, and method values.
- If the service exits immediately, inspect `journalctl -u hermis-discord-tracker -n 100 --no-pager`.
- If logs are not written, confirm the service user can write to `data/` and the SQLite database path.
