# Work Domain

---
status: active
last_updated: 2026-05-03
confidence: high
primary_sources:
  - state/tasks.md
  - state/work.md
  - docs/WORK_ASSISTANT.md
  - memory/ledger/2026-04-30.md
  - inbox/needs-answer/2026-04-30.md
---

## Current Understanding
User cancelled the invoice task (due 2026-05-01) on 2026-05-02. No active work items related to this invoice.

Hermis Work Assistant now uses AI-first, review-gated capture plus proactive automation inside the Discord tracker. Normal `#work-tracker` messages save raw/draft truth, then Hermis creates a pending AI suggestion. Confirmed `work_items` are created only after user accept/correct/reject review. Automation drafts messages with AI first and falls back only if AI is unavailable.

## Active Work Items
None currently active.

## Automation
- Work window: 14:00-23:00 Africa/Casablanca.
- Prep nudge: 13:00.
- Start plan: 14:00.
- During work: due reminders, overdue blocker prompts, waiting follow-ups.
- Shutdown review: 23:00, writes `reports/work/YYYY-MM-DD-shutdown.md`.
- Idempotency source: `work_automation_events`; AI drafts live in `work_ai_suggestions`; blocker prompts also write `work_blocker_prompts`.

## Open Questions
None currently.

## Links
- [[commitments]]
- [[open-questions]]
- [[current-state]]
