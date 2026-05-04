# Hermis Life OS User Guide

Hermis Life OS is a local-first personal operating system with a Discord-first
review interface.

The filesystem and wiki remain the durable source of truth. Discord is the
place where you usually see reports, answer questions, review suggestions,
clarify uncertain items, and approve or reject proposed updates.

Core rule:

> Capture first. Review before truth. Discord for interaction. Filesystem for durability.

## 1. What Hermis Does

Use Hermis to:

- Capture raw life input quickly.
- Preserve raw evidence without rewriting it.
- Turn useful input into reviewed state, memory, wiki pages, and reports.
- Receive morning reports and review cards in Discord.
- Reply naturally in Discord instead of editing files by hand.
- Let AI interpret messy replies, then validate them before durable changes.
- Fall back safely to nightly processing when Discord items go unanswered.

Hermis should not blindly remember everything. Raw notes are evidence. The wiki,
state files, reports, and curated memory are reviewed understanding.

## 2. The New Mental Model

Hermis now has three cooperating layers:

```text
Filesystem: durable truth
raw input -> memory/ledger -> state -> wiki -> reports

Discord: user-facing review inbox
reports/questions/reviews -> cards -> reactions/replies -> review items

AI: interpreter and reviewer
natural language -> structured interpretation -> validation -> pending update
```

Discord is not replacing the wiki. Discord is the front door.

If Discord delivery fails, reports and wiki output should still be written to
the filesystem, and the failure should be visible in logs or reports.

## 3. Important Paths

Default paths:

```bash
HERMES_HOME=${HERMES_HOME:-$HOME/.hermes/profiles/lifeos}
LIFEOS_ROOT=${LIFEOS_ROOT:-$HOME/hermis-life-os}
```

Main folders:

| Path | Purpose |
| --- | --- |
| `raw/captures/` | Raw captures and Discord-derived source blocks |
| `memory/ledger/` | Daily extracted timeline and source-linked facts |
| `memory/review/` | Durable memory candidates waiting for review |
| `memory/curated/` | Approved durable memory |
| `memory/approved/` | Approved memory records from review runs |
| `wiki/` | Durable compiled understanding |
| `state/` | Current operational state |
| `state/review-items.md` | Current generic review inbox snapshot |
| `reports/morning/` | Morning reports |
| `reports/nightly/` | Nightly processing and fallback reports |
| `reports/work/` | Work parsing and shutdown reports |
| `reports/weekly/` | Weekly audits and rollups |
| `research/nightly/` | Research summaries |
| `inbox/needs-answer/` | Questions needing user input |
| `data/review/` | Review item event logs |
| `data/work/` | Work logs |
| `data/finance/` | Finance logs |
| `data/prayer/` | Prayer logs |
| `data/hydration/` | Hydration logs |
| `scripts/` | Local automation scripts |
| `docs/` | System documentation |

The tracker database is usually:

```bash
${LIFEOS_ROOT}/data/lifeos_tracker.db
```

## 4. Discord Server Map

The Discord layout separates active automation from future life-area surfaces.
Discord is the front door; the filesystem and wiki remain durable truth.

```text
TEXT CHANNELS
  #general

HERMIS HOME
  #dashboard
    High-level Hermis status and future dashboard summaries.
  #daily-plan
    Daily plan and morning summary.
  #approval-queue
    Today's Review Inbox, approval cards, reactions, and natural-language replies.

HERMIS TRACKERS
  #prayer-tracker
    Prayer reminders, prayer reactions, !prayertoday, !testprayer.
  #habits
    Hydration reminders, !water, !hydration.
  #work-tracker
    Work captures, work reminders, !work, !work review, !work accept.
  #finance-tracker
    Finance captures and review-first money notes.

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

Channel roles:

| Category | Channel | Use | Durable truth |
| --- | --- | --- | --- |
| HERMIS HOME | `#dashboard` | High-level status and future dashboard summaries | `state/`, `reports/weekly/` |
| HERMIS HOME | `#daily-plan` | Morning summary and daily plan | `reports/morning/` |
| HERMIS HOME | `#approval-queue` | Today’s Review Inbox, generic review cards, approvals | `review_items`, `state/review-items.md`, `data/review/` |
| HERMIS TRACKERS | `#prayer-tracker` | Prayer reminders and reaction logging | `data/prayer/` |
| HERMIS TRACKERS | `#habits` | Hydration reminders and water logging | `data/hydration/` |
| HERMIS TRACKERS | `#work-tracker` | Work capture and work assistant review flow | `data/work/`, `state/work.md`, `reports/work/` |
| HERMIS TRACKERS | `#finance-tracker` | Finance notes and AI-led review-first processing | `data/finance/`, `reports/nightly/` |
| LIFE AREAS | `#daily-adhkar` | Future adhkar prompts/logging | not baked in yet |
| LIFE AREAS | `#fitness-log` | Future fitness and training logs | not baked in yet |
| LIFE AREAS | `#family-calendar` | Future family schedule coordination | not baked in yet |
| LIFE AREAS | `#wife-commitments` | Future sensitive family commitment tracking | not baked in yet |
| LIFE AREAS | `#ai-content` | Future content ideas/drafts | not baked in yet |
| LIFE AREAS | `#analytics` | Future personal analytics and trends | not baked in yet |
| LIFE AREAS | `#weekly-review` | Weekly review prompts and summaries | `reports/weekly/` |
| SYSTEM | `#system-notifications` | Automation status, failures, deployments | logs and reports |
| SYSTEM | `#audit-log` | Future audit trail for sensitive durable changes | `review_item_events`, `data/review/` |

`DAILY_PLAN_CHANNEL_NAME` should stay `daily-plan`. `REVIEW_CHANNEL_NAME` should
be `approval-queue`, so the morning plan and the approval inbox do not blur
together.

Sync the live server layout and channel topics with:

```bash
scripts/sync_discord_layout.py
```

Use `--dry-run` first if you only want to print the intended changes.

## 5. Daily Workflow

### Morning

Normal morning flow:

```text
Nightly jobs update wiki/state/reports
  -> morning report is written to reports/morning/YYYY-MM-DD.md
  -> Discord summary is posted
  -> unresolved questions become review cards
  -> you react or reply in Discord
```

In Discord, check `#daily-plan` for the plan and `#approval-queue` for decisions.

Useful commands:

```text
!morning
!morning 2026-05-04
!review
!review publish
```

The morning report file remains the durable record:

```bash
ls reports/morning
sed -n '1,220p' reports/morning/YYYY-MM-DD.md
```

Use the morning output to answer:

- What matters today?
- What is overdue?
- What is blocked?
- What needs a decision?
- What needs review?
- What is the next clear action?

### During The Day

Use Discord for normal interaction:

- `#work-tracker` for work captures.
- `#finance-tracker` for finance notes.
- `#prayer-tracker` for prayer reminders.
- `#habits` for hydration reminders.
- `#daily-plan` or review channel for reports and review cards.

You can still capture from the CLI:

```bash
scripts/raw_capture.sh "Need to call client about invoice steps"
```

Do not over-format. Hermis is designed to accept messy input and ask questions
when something is unclear.

### Evening

Work shutdown can be posted and written with:

```text
!work shutdown
```

The durable report goes to:

```text
reports/work/YYYY-MM-DD-shutdown.md
```

### Night

Nightly processing should:

- Process raw captures.
- Run finance and work review processors.
- Expire unanswered review cards.
- Write unresolved review items to `inbox/needs-answer/`.
- Update memory, state, wiki, and reports.
- Resurface still-unclear items the next morning.

Key scripts:

```bash
scripts/process_finance_reviews.py --all-open
scripts/process_work_reviews.py --all-open
scripts/process_review_fallback.py YYYY-MM-DD
```

## 6. Review Item Lifecycle

Generic review items are the bridge between reports/wiki/state and Discord.

A review item can represent:

- Morning report questions.
- Memory candidates.
- Finance review items.
- Work suggestions.
- Commitment reviews.
- Open questions.
- Unclear AI interpretations.
- Report follow-ups.

Lifecycle:

```text
created
  -> pending
  -> approved
  -> rejected
  -> needs_clarification
  -> expired
  -> auto_processed
```

Where review data lives:

```text
review_items table
discord_message_bindings table
review_item_events table
data/review/YYYY-MM-DD.jsonl
data/review/YYYY-MM-DD.md
state/review-items.md
```

Each review item stores:

- Stable id.
- Kind, title, and body.
- Source path and source record id.
- Source kind.
- AI interpretation JSON.
- AI validation JSON.
- Status, confidence, priority, and automation policy.
- Missing context.
- Surface count and last surfaced timestamp.
- Discord channel/message/thread binding.
- Created, updated, and expiry timestamps.

## 7. Discord Review Cards

Review cards are the normal interface for approvals and clarifications.

Each card includes:

- What needs review.
- Enough context to answer.
- A stable review id.
- Source reference.
- Reaction instructions.

Reactions:

- `✅` approve or accept.
- `❌` reject.
- `❓` mark as needing clarification.
- `📝` add details by replying.

Only configured owner IDs can mutate review state.

Unknown reactions are ignored or logged safely.

## 8. Replying In Discord

When you reply directly to a bot review card, Hermis uses the Discord message
binding to find the correct review item.

Reply flow:

```text
Discord reply
  -> binding lookup
  -> AIInputInterpreter
  -> AIValidationPass
  -> pending structured update or clarification
  -> Discord acknowledgement
```

Replies can:

- Answer a question.
- Add details.
- Correct an AI interpretation.
- Clarify a memory candidate.
- Improve a report item.
- Approve with extra context.
- Reject with a reason.

Hermis should not silently drop replies. If the AI cannot safely interpret the
reply, it should ask a clarification or leave the item pending.

## 9. AI Interpretation And Validation

Hermis uses AI for unstructured natural language.

`AIInputInterpreter`:

- Accepts raw user text plus review context.
- Identifies intent.
- Links the reply to the source item when possible.
- Extracts entities, dates, commitments, tasks, notes, corrections, answers, and proposed updates.
- Returns structured JSON with confidence and missing context.

`AIValidationPass`:

- Checks ambiguity.
- Finds missing context.
- Looks for contradictions or unsafe assumptions.
- Improves the structured update when possible.
- Decides whether to ask a clarification question.
- Returns validated structured JSON.

Important rule:

```text
Raw AI output is not durable truth.
Validated AI output is still pending unless safely automated or approved.
```

## 10. Wiki And Filesystem Truth

The wiki is the durable compiled map of your life context.

Start here:

```text
wiki/index.md
```

Important pages:

- `wiki/current-state.md`
- `wiki/open-questions.md`
- `wiki/contradictions.md`
- `wiki/decisions.md`
- `wiki/commitments.md`
- `wiki/domains/deen.md`
- `wiki/domains/health.md`
- `wiki/domains/work.md`
- `wiki/domains/family.md`
- `wiki/domains/money.md`
- `wiki/domains/planning.md`

State files answer what is operationally true right now:

- `state/tasks.md`
- `state/commitments.md`
- `state/reminders.md`
- `state/habits.md`
- `state/research-queue.md`
- `state/work.md`
- `state/review-items.md`

Every important wiki claim should have source context, confidence, status, and
last-updated information when practical.

Do not put raw dumps into the wiki. Use short synthesis.

## 11. Capturing Well

Good captures include one or more of:

- What happened.
- What needs to happen.
- Date or time.
- Person, project, or domain.
- Amount or category for finance.
- Whether the thing is done, blocked, waiting, cancelled, or just an idea.

Examples:

```text
work: submit parsing review by 18:00 today, priority high
money: spent 45 DH lunch, eating_out
health: slept 3:30 to 10:45, low energy before Asr
family: call mom tomorrow after Maghrib
research: compare energy tracking systems this week
```

Messy is still acceptable:

```text
invoice thing
tired again
money lunch
```

When the input is vague, Hermis should create a review card or open question
instead of guessing.

## 12. Finance Workflow

Finance is sensitive. Raw finance messages stay local and detailed finance logs
should not be indexed as general memory.

Normal flow:

```text
Discord message in #finance-tracker
  -> raw capture
  -> finance_parse_reviews
  -> generic review item
  -> Discord review card
  -> AI finance resolver
  -> validated transactions
  -> data/finance logs
  -> nightly/weekly summaries
  -> compact durable money wiki updates
```

Examples:

```text
spent 45 lunch
paid Netflix 12 USD
saved 300 emergency fund
salary 15000 MAD
```

Multiple entries in one message are allowed:

```text
spent 45 lunch
spent 80 transport
saved 500 emergency fund
```

Commands:

```text
!money today
!money month 2026-05
!money review
!money edit review:<id> <corrected text>
!money void review:<id>
```

Nightly finance processing:

```bash
scripts/process_finance_reviews.py YYYY-MM-DD --all-open
scripts/summarize_finance_day.py YYYY-MM-DD
```

Weekly finance rollup:

```bash
scripts/summarize_finance_week.py 2026-05-08
```

Manual `!money edit` is a fallback, not the preferred path.

## 13. Work Workflow

Work is review-gated. Normal work messages do not become confirmed tasks
immediately.

Normal flow:

```text
Message in #work-tracker
  -> work_captures row
  -> draft_parse_json
  -> work_ai_suggestions row
  -> generic review item
  -> Discord review card
  -> accept/correct/reject
  -> confirmed work_items only after approval
```

Work window:

```text
Africa/Casablanca
14:00-23:00
```

Automation:

- `13:00`: prep nudge.
- `14:00`: start plan.
- During work: due reminders, scheduled reminders, waiting follow-ups, overdue blocker prompts.
- `23:00`: shutdown review and report.

Commands:

```text
!work
!work add follow up with Youssef about API access tomorrow 16:30
!work review
!work accept suggestion:<id>
!work correct suggestion:<id> <what to fix>
!work reject suggestion:<id> <reason>
!work clarify capture:<id> <answer>
!work list
!work today
!work focus
!work automation
!work done <id>
!work block <id> <reason>
!work wait <id> <reason>
!work reschedule <id> 2026-05-04 16:30
!work snooze <id> 30m
!work shutdown
```

Nightly work processing:

```bash
scripts/process_work_reviews.py YYYY-MM-DD --all-open
```

Without `--apply`, the processor creates pending suggestions rather than final
work items. That is the safer default.

## 14. Morning Reports

Morning reports must always be saved to:

```text
reports/morning/YYYY-MM-DD.md
```

Then Discord receives a summary and review cards.

Morning report generation:

```bash
scripts/build_morning_report.py YYYY-MM-DD
```

Discord morning summary:

```bash
scripts/build_discord_morning_summary.py YYYY-MM-DD
```

Discord bot command:

```text
!morning
!morning YYYY-MM-DD
```

The Discord morning message should include:

- Summary.
- Important items.
- Review items.
- Open questions.
- Instructions to react or reply.

If Discord delivery fails, the report file should still exist.

## 15. Nightly Fallback

Unanswered review items are not lost.

Fallback flow:

```text
pending review item reaches expiry
  -> marked expired
  -> written to reports/nightly/YYYY-MM-DD-review-fallback.md
  -> written to inbox/needs-answer/YYYY-MM-DD-review.md
  -> resurfaced in next morning report and Discord review queue
```

Run:

```bash
scripts/process_review_fallback.py YYYY-MM-DD
```

Still-unclear items should remain visible until they are answered, rejected, or
resolved through a safe automation path.

## 16. Memory Workflow

Durable memory should be conservative.

Flow:

```text
raw evidence
  -> memory/ledger
  -> memory/review
  -> review item / Discord card when needed
  -> memory/approved or memory/curated
  -> wiki update
  -> retrieval index only when safe
```

Usually safe to promote faster:

- Source-backed recurring patterns.
- Completed research status.
- Confirmed task status.
- Non-sensitive habits.
- Stable preferences confirmed more than once.

Needs review:

- Money terms.
- Personal commitments with financial or legal impact.
- Contradictory claims.
- Sensitive family or health claims.
- Anything inferred from vague text.
- Anything that would affect future decisions if wrong.

## 17. Prayer Workflow

Prayer reminders appear in `#prayer-tracker`.

Reactions:

- `✅` means on time.
- `🕒` means late.
- `❌` means missed.

Commands:

```text
!prayertoday
!testprayer Fajr
```

Durable data:

```text
data/prayer/
data/lifeos_tracker.db
```

## 18. Hydration Workflow

Hydration reminders appear during the configured day window.

Reactions:

- `💧` adds 1 drink.
- `🥤` adds 2 drinks.
- `💤` snoozes reminders for 30 minutes.
- `❌` skips that reminder.

Commands:

```text
!water 2 after walk
!hydration
```

Durable data:

```text
data/hydration/
data/lifeos_tracker.db
```

## 19. Reports

Reports explain what Hermis did.

| Path | Use |
| --- | --- |
| `reports/morning/` | Daily plan and review surface |
| `reports/nightly/` | Nightly processing, fallback, finance, memory |
| `reports/work/` | Work parsing and shutdown |
| `reports/weekly/` | Wiki audits and finance rollups |
| `research/nightly/` | Research output |

Reports should be human-readable. They should not dump raw JSON or tracker rows.

## 20. Setup

Create or update `.env.discord-tracker`:

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
HERMES_HOME=${HOME}/.hermes/profiles/lifeos
HERMIS_WORK_AI_CMD=${HOME}/.local/bin/lifeos
HERMIS_WORK_AUTOMATION_AI_CMD=${HOME}/.local/bin/lifeos
HERMIS_REVIEW_AI_CMD=${HOME}/.local/bin/lifeos
TIMEZONE=Africa/Casablanca
MORNING_REVIEW_ENABLED=true
MORNING_REVIEW_HOUR=7
MORNING_REVIEW_MINUTE=40
REVIEW_ITEM_EXPIRY_HOURS=18
```

Run locally:

```bash
scripts/run_discord_tracker.sh
```

Install as systemd service:

```bash
scripts/install_discord_tracker_service.sh
```

Service commands:

```bash
sudo systemctl status hermis-discord-tracker --no-pager
sudo journalctl -u hermis-discord-tracker -f
sudo systemctl restart hermis-discord-tracker
```

## 21. Discord Permissions

The bot needs:

- View Channels.
- Send Messages.
- Embed Links.
- Add Reactions.
- Read Message History.
- Message Content Intent.

Message Content Intent is required for:

- Commands.
- Finance capture.
- Work capture.
- Review-card replies.

Only `DISCORD_OWNER_IDS` should be allowed to mutate Life OS state.

## 22. Verification

Run tests:

```bash
.venv-discord-tracker/bin/python -m unittest discover apps/discord_tracker/tests
```

Run health check:

```bash
scripts/health_check.sh --tests
```

Smoke test in Discord:

1. Run `!prayertoday`.
2. Run `!testprayer Fajr` and react.
3. Run `!water 1`.
4. Post a finance note in `#finance-tracker`.
5. Post a work note in `#work-tracker`.
6. Run `!review publish`.
7. Confirm review cards appear with `✅`, `❌`, `❓`, and `📝`.
8. Reply directly to a review card.
9. Confirm `state/review-items.md` updates.
10. Confirm `data/review/YYYY-MM-DD.jsonl` is written.
11. Run `scripts/process_review_fallback.py YYYY-MM-DD` with an expired item.
12. Confirm `inbox/needs-answer/YYYY-MM-DD-review.md` is written.

## 23. Troubleshooting

If Discord commands do nothing:

- Confirm the bot is running.
- Confirm Message Content Intent is enabled.
- Confirm channel permissions.
- Confirm your user id is in `DISCORD_OWNER_IDS`.

If review replies are ignored:

- Make sure you replied directly to a bot review card.
- Check `state/review-items.md`.
- Inspect `discord_message_bindings` in the tracker DB.

If morning report Discord delivery fails:

- Check that `reports/morning/YYYY-MM-DD.md` exists.
- Check `DAILY_PLAN_CHANNEL_NAME`.
- Check bot channel permissions.
- Read service logs with `journalctl`.

If AI interpretation fails:

- Check `HERMIS_REVIEW_AI_CMD`.
- Check `HERMES_HOME`.
- The reply should remain attached as low-confidence context or trigger a clarification.

If nightly fallback is not resurfacing items:

- Run `scripts/process_review_fallback.py YYYY-MM-DD`.
- Check `reports/nightly/YYYY-MM-DD-review-fallback.md`.
- Check `inbox/needs-answer/YYYY-MM-DD-review.md`.

## 24. Operating Principles

- Discord is the inbox, not the archive.
- Filesystem and wiki remain durable truth.
- Raw input stays evidence.
- AI drafts and interpretations are not final truth.
- Important updates need validation and approval or an existing safe automation path.
- Unanswered items should resurface, not disappear.
- Additive changes are preferred over rewrites.
- When unsure, ask a question instead of guessing.
