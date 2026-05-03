# Work Automation Implementation - 2026-05-03

## Summary
- Added proactive work scheduler inside Discord tracker sidecar.
- Upgraded to AI-first, review-gated model: raw captures get pending AI suggestions, and final work changes require `!work accept suggestion:<id>`.
- Automation drafts Discord messages with Hermis AI first; deterministic text is fallback only.
- Work window: 14:00-23:00 Africa/Casablanca.

## Schedule
- 13:00 prep nudge.
- 14:00 start-of-shift plan.
- During work: due reminders, overdue blocker prompts, waiting follow-ups.
- 23:00 shutdown review and `reports/work/YYYY-MM-DD-shutdown.md`.

## Idempotency
- `work_automation_events` logs each automated message.
- `work_ai_suggestions` logs capture parse drafts and automation message drafts.
- `work_blocker_prompts` logs structured overdue blocker prompts.
- Restarts do not resend already logged nudges.

## Remaining Choices
- Mid-shift check-in is configurable but disabled by default.
- Direct nightly apply still exists behind `scripts/process_work_reviews.py --apply`; default creates pending suggestions only.
