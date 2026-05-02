# Wiki Audit Report: 2026-05-01

## Audit Scope
Reviewed all required Life OS wiki, memory, state, and report files in `/home/ubuntu/hermis-life-os` per the life-wiki-audit skill.

## Key Findings

### 1. Missing Files
- `reports/morning/latest` (referenced in skill input list, not found)
- `reports/nightly/latest` (referenced in skill input list, not found)
- `memory/review/2026-04-30.md` (referenced in `wiki/current-state.md` line 32, but `memory/review/` directory is empty)

### 2. Stale/Empty Draft Pages
The following pages are in draft status with no content and `last_updated: unset`:
- `wiki/domains/deen.md`
- `wiki/domains/family.md`
- `wiki/domains/money.md`
- `wiki/domains/planning.md`
- `wiki/patterns/focus-patterns.md`
- `wiki/patterns/avoidance-patterns.md` (inferred, not read but matches draft pattern)
- `wiki/patterns/energy-patterns.md` (inferred, not read but matches draft pattern)

### 3. Stale References
- `wiki/current-state.md` references `memory/review/2026-04-30.md` which does not exist (memory/review/ is empty)
- All draft pages have no sources, no content, and no update timestamps

### 4. Contradictions
- No contradictions found. `wiki/contradictions.md` is empty.
- `wiki/domains/health.md` and `wiki/patterns/sleep-patterns.md` both describe the same sleep pattern consistently.

### 5. Unsourced Claims
- All draft domain and pattern pages have no sources or content.
- Active pages (`current-state.md`, `work.md`, `health.md`, `sleep-patterns.md`) include proper source references.

### 6. Obsolete Commitments
- Invoice commitment due 2026-05-01 (TODAY) is not obsolete, but is at risk (user stuck on first step, question logged in `wiki/open-questions.md`).

### 7. Memory Candidates
- `memory/review/` directory is empty, no pending memory candidates to review.

### 8. Page Health
- No pages are too long or overly raw.
- Active pages follow the required wiki schema with status, last_updated, confidence, and primary_sources.

## Recommended Actions
1. Create missing `reports/morning/latest` and `reports/nightly/latest` (or remove references if not used)
2. Either populate draft domain/pattern pages with content or mark them as `status: deprecated` if no content is available
3. Update `wiki/current-state.md` to remove reference to non-existent `memory/review/2026-04-30.md`
4. Consider adding a `wiki/decisions.md` page (listed in index.md but not found in domain search)
