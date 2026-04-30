# Weekly Wiki Audit Report
Date: 2026-04-30
Workspace: /home/ubuntu/hermis-life-os

## Audit Summary
Focused check for stale, contradictory, duplicated, unsourced claims and sync issues. No full rewrites performed.

## Issues Found

### 1. Empty/Stale Wiki Pages (No Content, No Sources)
- **wiki/domains/deen.md**: Empty, status=draft, last_updated=unset. Missing core deen domain content.
- **wiki/decisions.md**: Empty, no last_updated metadata. No logged decisions yet.
- **wiki/patterns/energy-patterns.md**: Empty, last_updated=unset. No energy pattern data despite research queue entry.
- **wiki/patterns/avoidance-patterns.md**: Empty, last_updated=unset. No avoidance pattern data.
- **wiki/patterns/focus-patterns.md**: Empty (inferred from peer pages), last_updated=unset.

### 2. Sync Mismatch: State vs Wiki
- **state/commitments.md**: Empty, but **wiki/commitments.md** has active invoice commitment (due 2026-05-01). State file not synced with wiki.

### 3. Missing Report Symlinks/Files
- **reports/morning/latest**: File not found.
- **reports/nightly/latest**: File not found.

### 4. Empty Memory Curated Store
- **memory/curated/**: No curated durable memories yet. All candidates remain in memory/review/ pending user approval.

## Clean Items (No Action Needed)
- No contradictions found (wiki/contradictions.md empty, no conflicting claims across pages).
- No duplicated facts: Cross-linked content (sleep pattern in health.md, sleep-patterns.md, ledger) is intentional linking, not duplication.
- All active wiki pages (current-state.md, health.md, commitments.md, open-questions.md) have valid primary_sources and proper metadata.
- memory/review/2026-04-30.md correctly holds 2 pending candidates (sleep pattern, invoice awareness) awaiting user approval — no unauthorized promotion.

## Targeted Fix Recommendations (User Approval Needed)
1. Populate or mark empty domain/pattern pages as `status: stale` if no data is expected soon.
2. Sync state/commitments.md with wiki/commitments.md content.
3. Create reports/morning/latest and reports/nightly/latest symlinks or copies from dated reports.
4. Approve/reject pending memory candidates in memory/review/2026-04-30.md before moving to curated.

## Next Action
Review this audit report and approve targeted fixes or provide input on empty pages.
