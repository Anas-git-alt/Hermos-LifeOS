# Hermis Life OS User Guide

Hermis Life OS is a local-first personal operating system. It helps you capture messy life input, turn it into reviewed knowledge, keep current state visible, and support daily action without trusting raw notes too much.

The system is built around one simple rule:

> Capture first. Review before truth. Act from current state.

## 1. What This System Is For

Use Hermis Life OS to:

- Capture tasks, reminders, habits, commitments, ideas, research topics, finance notes, work notes, and emotional signals.
- Keep raw input safe and unchanged.
- Turn important raw input into structured state, durable memory, wiki pages, and reports.
- Get morning, nightly, weekly, work, finance, prayer, and hydration support.
- Ask questions later and get answers from compiled context instead of scattered notes.

Hermis is not meant to be a dumping ground that blindly remembers everything. It is meant to be a thinking layer: raw notes stay local, useful patterns get reviewed, and only trusted summaries become long-term context.

## 2. Core Mental Model

Hermis has five main layers:

1. `raw/`
   - Source truth.
   - Messy captures, transcripts, documents, and web clips.
   - Raw files are append-only. Do not edit or delete them.

2. `memory/ledger/`
   - Daily timeline of extracted facts, events, and signals.
   - Still source-linked.
   - Useful for reconstructing what happened on a day.

3. `memory/review/`
   - Candidate durable memories.
   - Items waiting for approval, rejection, correction, or safe auto-promotion.

4. `memory/curated/`
   - Approved durable memory.
   - Safe, high-confidence facts and patterns.
   - Can be indexed for retrieval.

5. `wiki/` and `state/`
   - `wiki/` is compiled understanding.
   - `state/` is current operational truth: active tasks, commitments, habits, reminders, work focus, and research queue.

Reports live in `reports/`. They explain what changed and what to do next.

## 3. Folder Map

Use this map when you need to find something:

| Folder | Purpose |
| --- | --- |
| `raw/captures/` | Manual raw notes and daily captures |
| `raw/transcripts/` | Chat or meeting transcripts |
| `raw/documents/` | Source documents |
| `raw/web-clips/` | Saved web source material |
| `memory/ledger/` | Append-only daily life timeline |
| `memory/review/` | Memory candidates waiting for review |
| `memory/curated/` | Approved durable memories |
| `wiki/` | Compiled life understanding |
| `wiki/domains/` | Deen, health, work, family, money, planning |
| `wiki/patterns/` | Sleep, energy, focus, avoidance patterns |
| `state/` | Active tasks, reminders, commitments, habits, work state |
| `reports/morning/` | Morning briefings |
| `reports/nightly/` | Nightly processing output |
| `reports/work/` | Work parsing, automation, and shutdown reports |
| `reports/weekly/` | Weekly audits and rollups |
| `research/nightly/` | Research summaries |
| `data/` | Local tracker data and structured logs |
| `scripts/` | Helper scripts |
| `docs/` | System documentation |

## 4. Daily Workflow

### Morning

Start by reading the latest morning report:

```bash
ls reports/morning
```

Open the newest file, for example:

```bash
sed -n '1,220p' reports/morning/2026-05-03.md
```

Use the morning report to answer:

- What matters today?
- What is overdue?
- What is blocked?
- What needs a decision?
- What is the next clear action?

If there is no morning report yet, read:

- `wiki/current-state.md`
- `state/tasks.md`
- `state/commitments.md`
- `state/work.md`
- `wiki/open-questions.md`

### During Day

Capture quickly. Do not organize too early.

Use CLI capture:

```bash
scripts/raw_capture.sh "Need to call client about invoice steps"
```

Good capture examples:

```text
remind me tomorrow 16:00 to send invoice
slept late again, low focus until afternoon
idea: research better energy tracker
paid 2500 DH for 9or3a installment
work: follow up with Youssef about API access
```

Do not worry about perfect formatting. Hermis can classify later.

### Evening / Night

Nightly processing should:

- Read raw captures.
- Extract tasks, commitments, habits, facts, questions, and patterns.
- Update `state/`.
- Update `wiki/`.
- Write reports.
- Put uncertain items in `inbox/needs-answer/`.
- Put candidate durable memories in `memory/review/`.

Read nightly outputs:

```bash
ls reports/nightly
```

Then open the newest relevant report.

### Weekly

Weekly review should:

- Find contradictions.
- Find stale claims.
- Check unresolved open questions.
- Summarize finance patterns.
- Summarize memory changes.
- Keep wiki concise.

Read:

```bash
ls reports/weekly
```

## 5. The Main Information Flow

Most Life OS data should move like this:

```text
User capture
  -> raw/captures
  -> nightly review
  -> memory/ledger
  -> state updates
  -> wiki updates
  -> reports
  -> optional memory/review
  -> memory/curated after approval
  -> OpenViking retrieval index if safe
```

Important rule: raw input is evidence, not final truth.

Example:

```text
Raw note:
"send invoice tomorrow, stuck because don't know first step"

Hermis extracts:
- task: send invoice
- due date: tomorrow
- blocker: unclear first step
- open question: what steps are needed?

State updates:
- task added
- open question added
- current state mentions blocker

Later correction:
"cancel invoice task"

Hermis updates:
- task status becomes cancelled
- open question becomes resolved
- wiki/current-state changes
```

## 6. How To Capture Well

Fast captures work best when they include one or more of:

- What happened.
- What needs to happen.
- Date or time.
- Person or project.
- Amount or category for finance.
- Whether it is done, blocked, waiting, cancelled, or just an idea.

Better captures:

```text
work: submit parsing review by 18:00 today, priority high
money: spent 45 DH lunch, eating_out
health: slept 3:30 to 10:45, low energy before Asr
family: call mom tomorrow after Maghrib
research: compare energy tracking systems this week
```

Less useful but still okay:

```text
invoice thing
tired again
money lunch
```

If Hermis cannot infer enough, it should create an open question instead of guessing.

## 7. Review Gates

Review gates keep the system trustworthy.

Hermis should not blindly convert every raw note into permanent truth. Sensitive or uncertain items must pass review.

### Safe To Promote Faster

Usually safe:

- Source-backed recurring patterns.
- Completed research status.
- Confirmed task status.
- Non-sensitive habits.
- Stable preferences confirmed more than once.

### Needs Review

Needs user review:

- Money terms.
- Personal commitments with financial or legal impact.
- Contradictory claims.
- Sensitive family or health claims.
- Anything inferred from vague text.
- Anything that would affect future decisions if wrong.

### Open Questions

If something is unclear, it goes to:

```text
wiki/open-questions.md
inbox/needs-answer/
```

Answer these regularly. The system gets much smarter when open questions are closed.

## 8. Wiki Workflow

The wiki is the compiled map of your life context.

Start here:

```text
wiki/index.md
```

Key pages:

- `wiki/current-state.md`
- `wiki/open-questions.md`
- `wiki/contradictions.md`
- `wiki/decisions.md`
- `wiki/commitments.md`

Domain pages:

- `wiki/domains/deen.md`
- `wiki/domains/health.md`
- `wiki/domains/work.md`
- `wiki/domains/family.md`
- `wiki/domains/money.md`
- `wiki/domains/planning.md`

Pattern pages:

- `wiki/patterns/sleep-patterns.md`
- `wiki/patterns/focus-patterns.md`
- `wiki/patterns/avoidance-patterns.md`
- `wiki/patterns/energy-patterns.md`

Every important wiki claim should have:

- Source path.
- Source date.
- Confidence.
- Status.
- Last updated date.

Do not write raw dumps into the wiki. Write short, useful synthesis.

## 9. State Workflow

`state/` is the operational dashboard.

Use:

- `state/tasks.md` for active or historical tasks.
- `state/commitments.md` for obligations and promises.
- `state/reminders.md` for reminders.
- `state/habits.md` for habit state.
- `state/research-queue.md` for research topics.
- `state/work.md` for current confirmed work and work automation status.

State should answer:

- What is active?
- What is blocked?
- What is waiting?
- What is due?
- What changed recently?
- What is the next action?

State is not meant to hold long explanations. Put explanations in wiki or reports.

## 10. Discord Tracker Workflow

The Discord sidecar supports:

- Prayer tracking.
- Hydration tracking.
- Finance capture and review.
- Work capture and review.

Main channels:

- `#prayer-tracker`
- `#habits`
- `#finance-tracker`
- `#work-tracker`

Run bot locally:

```bash
scripts/run_discord_tracker.sh
```

Install as service:

```bash
scripts/install_discord_tracker_service.sh
```

Service commands:

```bash
sudo systemctl status hermis-discord-tracker --no-pager
sudo journalctl -u hermis-discord-tracker -f
sudo systemctl restart hermis-discord-tracker
```

## 11. Prayer Workflow

Prayer reminders appear in `#prayer-tracker`.

React:

- `✅` means on time.
- `🕒` means late.
- `❌` means missed.

Check today:

```text
!prayertoday
```

Smoke test:

```text
!testprayer Fajr
```

Prayer data is stored in:

```text
data/prayer/
data/lifeos_tracker.db
```

## 12. Hydration Workflow

Hydration reminders appear during the configured day window.

React:

- `💧` adds 1 drink.
- `🥤` adds 2 drinks.
- `💤` snoozes reminders for 30 minutes.
- `❌` skips that reminder.

Manual log:

```text
!water 2 after walk
```

Check today:

```text
!hydration
```

Hydration data is stored in:

```text
data/hydration/
data/lifeos_tracker.db
```

## 13. Finance Workflow

Finance is sensitive. Hermis keeps raw finance messages local and does not index detailed finance logs by default.

Normal flow:

```text
Discord message in #finance-tracker
  -> raw finance capture
  -> finance review queue
  -> Hermis AI resolver
  -> validated transactions
  -> daily finance log
  -> nightly/weekly summaries
  -> wiki/domains/money.md only for compact durable understanding
```

Capture examples:

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

Nightly processing:

```bash
scripts/process_finance_reviews.py <YYYY-MM-DD> --all-open
```

Weekly rollup:

```bash
scripts/summarize_finance_week.py <week-ending-YYYY-MM-DD>
```

Use manual `!money edit` as fallback, not normal workflow. Normal flow is AI-led review plus validation.

## 14. Work Assistant Workflow

Work Assistant is review-gated. Normal messages do not become final tasks immediately.

Normal flow:

```text
Message in #work-tracker
  -> work_captures row
  -> draft parse JSON
  -> pending AI suggestion
  -> human review
  -> accept/correct/reject
  -> confirmed work_items only after accept
```

This prevents vague notes from becoming false tasks.

### Work Window

Timezone:

```text
Africa/Casablanca
```

Work window:

```text
14:00-23:00
```

Automation:

- `13:00`: prep nudge.
- `14:00`: start plan.
- During work: due reminders, scheduled reminders, waiting follow-ups, overdue blocker prompts.
- `23:00`: shutdown review and report.

### Work Commands

Capture:

```text
!work add follow up with Youssef about API access tomorrow 16:30
```

Review:

```text
!work review
!work accept suggestion:<id>
!work correct suggestion:<id> <what to fix>
!work reject suggestion:<id> <reason>
!work clarify capture:<id> <answer>
```

View:

```text
!work
!work list
!work today
!work focus
!work automation
```

Act:

```text
!work done <id>
!work block <id> <reason>
!work wait <id> <reason>
!work reschedule <id> 2026-05-04 16:30
!work snooze <id> 30m
```

Manual plan/shutdown:

```text
!work plan
!work shutdown
```

Nightly review:

```bash
scripts/process_work_reviews.py <YYYY-MM-DD> --all-open
```

Only accepted AI suggestions should create final `work_items`.

## 15. Reports Workflow

Reports are how you see what Hermis did.

Morning reports:

```text
reports/morning/
```

Use for daily planning.

Nightly reports:

```text
reports/nightly/
```

Use for processing results, finance review, memory review, and triage.

Work reports:

```text
reports/work/
```

Use for work parsing review, automation review, and shutdown.

Weekly reports:

```text
reports/weekly/
```

Use for larger audits, finance rollups, memory reviews, and wiki health.

## 16. Retrieval And Memory Policy

Hermis may use OpenViking for recall and retrieval, but the filesystem remains source truth.

Index by default:

- `wiki/`
- User-approved `memory/curated/`
- Safe auto-promoted curated memories

Do not index by default:

- `raw/`
- `raw/documents/`
- Tracker database rows
- Finance raw messages
- Detailed finance logs
- `.env` files
- Backups

Money is especially sensitive. Prefer:

1. `wiki/domains/money.md`
2. Nightly finance summaries
3. Weekly finance summaries
4. Raw finance logs only when auditing or correcting

## 17. Best Way To Ask Hermis Questions

Good questions:

```text
What is my current top priority?
What is overdue?
What changed in work today?
What open questions need answers?
Summarize my sleep pattern from curated memory and wiki.
What should I do first in my work window?
What finance commitments are active?
```

Even better:

```text
Answer from wiki/current-state.md and state/work.md first.
Check reports only if needed.
Do not inspect raw finance logs unless needed for audit.
```

This helps Hermis use the right layer.

## 18. Best Practices

Use short, real captures.

Review open questions often.

Trust `state/` for current action, not old raw captures.

Trust `wiki/` for compiled understanding, not single messy notes.

Use reports to understand recent changes.

Keep raw data append-only.

Do not index sensitive raw data.

Correct Hermis when it guesses wrong. Corrections make the system better.

Prefer one clear next action over a huge plan.

## 19. Common Maintenance Commands

Prepare today:

```bash
scripts/new_day.sh
```

Prepare a specific day:

```bash
scripts/new_day.sh 2026-05-04
```

Capture from CLI:

```bash
scripts/raw_capture.sh "your note here"
```

Run health check:

```bash
scripts/health_check.sh
```

Run health check with tests:

```bash
scripts/health_check.sh --tests
```

Run Discord tracker:

```bash
scripts/run_discord_tracker.sh
```

Process finance reviews:

```bash
scripts/process_finance_reviews.py 2026-05-03 --all-open
```

Process work reviews:

```bash
scripts/process_work_reviews.py 2026-05-03 --all-open
```

Summarize finance week:

```bash
scripts/summarize_finance_week.py 2026-05-03
```

Back up workspace:

```bash
scripts/backup.sh
```

## 20. Example Full Day

### 09:00

Read morning report.

Check:

- Current priority.
- Open questions.
- Work focus.
- Finance commitments.

### 10:00-13:00

Capture anything that appears:

```bash
scripts/raw_capture.sh "slept late, low focus until noon"
scripts/raw_capture.sh "research topic: better energy tracking system"
```

Use Discord for prayer and hydration.

### 13:00

Work prep nudge appears if Discord tracker is running.

Read one recommended first action.

### 14:00

Work start plan appears.

Use:

```text
!work focus
```

### During Work

Add work notes:

```text
!work add finish parser review today by 18:00
```

Review before accepting:

```text
!work review
!work accept suggestion:<id>
```

When done:

```text
!work done <id>
```

If stuck:

```text
!work block <id> unclear next step
```

### 23:00

Shutdown review appears.

Answer:

- What got done?
- What is still open?
- What is blocked?
- What should be first tomorrow?

### Night

Run or read nightly processing.

Check:

- `reports/nightly/`
- `reports/work/`
- `inbox/needs-answer/`
- `memory/review/`

## 21. Troubleshooting

If Discord bot is not responding:

```bash
scripts/health_check.sh
sudo systemctl status hermis-discord-tracker --no-pager
sudo journalctl -u hermis-discord-tracker -f
```

If work items do not appear:

- Check `!work review`.
- Accept pending suggestions.
- Run `scripts/process_work_reviews.py <YYYY-MM-DD> --all-open`.
- Remember: raw work captures do not become final tasks until accepted.

If finance entries do not appear:

- Check `!money review`.
- Run `scripts/process_finance_reviews.py <YYYY-MM-DD> --all-open`.
- Use `!money edit review:<id> <corrected text>` only when AI review needs correction.

If current state feels stale:

- Read latest nightly report.
- Check `wiki/open-questions.md`.
- Check `manifests/wiki-changelog.md`.
- Update state from confirmed sources only.

If Hermis is unsure:

- It should write an open question.
- Answer the question.
- Re-run the relevant review flow.

## 22. Golden Rules

1. Raw files are source truth. Append only.
2. Review before durable memory.
3. Work and finance are AI-first but review-gated.
4. State shows what to do now.
5. Wiki shows what Hermis believes and why.
6. Reports show what changed.
7. Open questions are not failure; they are how the system avoids bad guesses.
8. Sensitive raw data stays local and unindexed unless explicitly approved.
9. Prefer one clear next action.
10. Correct the system early when it misunderstands.

## 23. Quick Start

If you only do five things, do these:

1. Capture everything quickly:

```bash
scripts/raw_capture.sh "note here"
```

2. Read daily state:

```bash
sed -n '1,220p' wiki/current-state.md
sed -n '1,220p' state/work.md
```

3. Review open questions:

```bash
sed -n '1,220p' wiki/open-questions.md
```

4. Use Discord commands:

```text
!work review
!work focus
!money today
!hydration
!prayertoday
```

5. Read reports:

```bash
ls reports/morning reports/nightly reports/work reports/weekly
```

That is the system: capture, review, update state, learn patterns, act on the next clear thing.
