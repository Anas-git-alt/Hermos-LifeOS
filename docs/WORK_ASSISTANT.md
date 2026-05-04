# Work Assistant

Hermis Work Assistant is integrated into the Discord tracker and the local tracker DB. It is not a standalone app.

## AI-First, Review-Gated Model

Normal owner messages in `#work-tracker` follow this flow:

1. Save the raw message in `work_captures` with source metadata.
2. Save an initial `draft_parse_json` with `confidence` and `review_reason`.
3. Ask Hermis AI to draft a `work_ai_suggestions` row immediately.
4. Mark the capture and AI draft `pending`/`unreviewed`.
5. Human review accepts, corrects, rejects, ignores with a reason, or asks clarification.
6. Only accepted review output creates rows in `work_items`.

The draft parse and AI suggestion are hints. They must not be treated as final task truth.

Finance uses the same review-first safety shape: raw capture first, AI-led review, validated JSON, then structured records.

## Schedule

- Timezone: `Africa/Casablanca`
- Work window: `14:00-23:00`

These defaults are exposed through `.env.discord-tracker`:

```bash
WORK_CHANNEL_NAME=work-tracker
WORK_START_HOUR=14
WORK_END_HOUR=23
WORK_PREP_LEAD_MINUTES=60
WORK_MID_SHIFT_CHECKIN_ENABLED=false
WORK_SHUTDOWN_REVIEW_ENABLED=true
WORK_REMINDER_LOOKAHEAD_MINUTES=30
WORK_OVERDUE_GRACE_MINUTES=15
HERMIS_WORK_AI_CMD=${HOME}/.local/bin/lifeos
HERMIS_WORK_AUTOMATION_AI_CMD=${HOME}/.local/bin/lifeos
HERMIS_REVIEW_AI_CMD=${HOME}/.local/bin/lifeos
HERMES_HOME=${HOME}/.hermes/profiles/lifeos
```

Work, finance, and generic review AI subprocesses use `HERMES_HOME=${HERMES_HOME:-$HOME/.hermes/profiles/lifeos}`, so inherited shell or systemd environment cannot accidentally switch profiles.

## Tables

`work_captures` stores every normal work tracker Discord message:

- `raw_text`
- `source`, `source_message_id`, `source_channel_id`, `source_channel_name`, `logged_by`
- `draft_parse_json`
- `confidence`, `review_reason`
- `review_status`: `unreviewed`, `confirmed`, `clarification`, or `ignored`
- `clarification_question` or `ignore_reason`

`work_items` stores only confirmed work:

- `capture_id`
- `title`, `status`, `priority`
- optional project, area, due date, scheduled date, energy, effort, context, tags, note
- source metadata copied from the capture

Time-aware fields:

- `due_at`: local `HH:MM` due time for `due_date`
- `scheduled_at`: local `HH:MM` start time for `scheduled_date`
- `next_followup_at`: UTC timestamp for waiting follow-up
- `snoozed_until_utc`: UTC timestamp suppressing item nudges

Automation tables:

- `work_automation_events`: idempotency log for prep, start, shutdown, due reminders, waiting follow-ups, and overdue blocker prompts
- `work_blocker_prompts`: structured blocker prompts created when an item becomes overdue
- `work_item_events`: item/capture event ledger
- `work_ai_suggestions`: Hermis AI drafts for capture parsing and automation messages, with `pending`, `accepted`, `corrected`, or `rejected` status

## Nightly Review

Run:

```bash
scripts/process_work_reviews.py <YYYY-MM-DD> --all-open
```

The script calls `scripts/run_work_ai_reviewer.py` by default. It validates Hermis JSON, then creates pending `work_ai_suggestions`. It does not create final `work_items` unless explicitly run with `--apply`.

Expected output:

- `reports/work/YYYY-MM-DD-parsing-review.md`
- `inbox/needs-answer/YYYY-MM-DD-work.md` when clarification is needed
- refreshed `state/work.md`

The script itself must not infer final tasks from raw text. If Hermis output is missing, malformed, or incomplete, the capture stays unconfirmed and a clarification is written.

## Discord Commands

- `!work` shows today's confirmed work and open capture review count.
- `!work add <text>` saves a capture and asks AI for a pending suggestion. It does not create confirmed work by itself.
- `!work list` shows active confirmed work.
- `!work today` shows confirmed work due or scheduled today.
- `!work focus` shows a compact focus list for the Casablanca work window.
- `!work automation` shows automation status and sent nudges.
- `!work plan` manually triggers the current work plan.
- `!work shutdown` manually writes shutdown report and shows shutdown questions.
- `!work done <id>` marks confirmed work done.
- `!work block <id> <reason>` marks confirmed work blocked. Reason is required.
- `!work wait <id> <reason>` marks confirmed work waiting. Reason is required.
- `!work reschedule <id> <date/time>` moves a task. Examples: `2026-05-04`, `2026-05-04 16:30`, `16:30`.
- `!work blocker <id> <reason>` logs a blocker prompt result and marks the item blocked.
- `!work snooze <id> <duration>` suppresses reminders for one item. Examples: `30m`, `2h`.
- `!work clarify capture:<id> <answer>` answers a clarification without creating final work directly.
- `!work review` shows confirmed active work, pending AI suggestions, and unreviewed or unclear captures.
- `!work accept suggestion:<id>` applies a pending AI suggestion.
- `!work correct suggestion:<id> <what to fix>` keeps the old suggestion, reruns AI with your correction, and creates a new pending suggestion.
- `!work reject suggestion:<id> <reason>` rejects a pending AI suggestion. Reason is required.

Normal messages and `!work add` are both AI-first and review-gated. The final task mutation happens through `!work accept`.

## Real Workflow

### 13:00 Prep

Bot posts one short AI-drafted prep message:

- P0/P1 work today
- overdue items
- blocked/waiting items
- actual clarification question if any
- one recommended first action
- one prep action, such as gather context for a scheduled item

### 14:00 Start Plan

Bot posts AI-drafted start-of-shift plan. It stays short and points at one next action. It does not create tasks.

### During Work

Bot sends only useful nudges:

- due/scheduled reminder within lookahead window
- due-date end-of-shift reminder
- waiting follow-up if `next_followup_at` is due
- overdue blocker prompt after grace period

Every automated message has a `work_automation_events` row, so restart does not resend it. AI is tried first; if Hermis is unavailable, the bot sends a deterministic fallback and marks the event payload as `message_source=fallback`.

### Overdue Blocker Prompt

If item is overdue and still open, bot asks:

```text
#42 is overdue. What blocked it: unclear next step, waiting on someone, too big, forgot, low energy, or no longer needed?
```

Useful replies:

- `!work blocker 42 unclear next step`
- `!work wait 42 waiting on Youssef`
- `!work reschedule 42 2026-05-04 16:30`
- `!work done 42`

### 23:00 Shutdown

Bot posts a compact AI-drafted shutdown review and writes:

```bash
reports/work/YYYY-MM-DD-shutdown.md
```

Questions:

- What got done?
- What is still open?
- What is blocked?
- What should be first tomorrow?
- Are clarifications still unanswered?

### Nightly Review

`scripts/process_work_reviews.py` still handles raw captures and clarification answers, but default output is pending AI suggestions for `!work review`. Automation only nudges from confirmed DB state and open review questions.

### Next-Day Focus

`state/work.md` refresh includes today focus, overdue, blocked/waiting, open clarifications, next automation events, and recommended next action.
