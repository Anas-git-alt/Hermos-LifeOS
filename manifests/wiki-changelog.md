# Wiki Changelog

All meaningful wiki updates are logged here with sources and affected pages.

---

## 2026-05-03

### Invoice Task Cancellation Cleanup
- **Pages updated**: 
  - `wiki/current-state.md`
  - `wiki/domains/work.md`
  - `wiki/commitments.md`
  - `wiki/open-questions.md`
- **Sources**: 
  - `state/tasks.md` (task-20260430-invoice cancelled 2026-05-02)
  - `state/commitments.md` (9or3a financing active)
- **Changes**:
  - Updated all relevant pages to reflect cancelled invoice task (task-20260430-invoice)
  - Removed overdue invoice references from current-state, work, commitments wikis
  - Closed open question q-20260430-invoice-steps (resolved as task cancelled)
  - Added active 9or3a financing commitment to wiki/commitments.md
  - Updated last_updated dates to 2026-05-03
- **Missing files noted**:
  - `memory/ledger/latest` not found
  - `reports/nightly/latest` not found
- **Confidence**: high
- **User action**: None required

### Wiki Updates from 2026-05-03 Nightly Reports
- **Pages updated**: 
  - `wiki/open-questions.md`
  - `wiki/domains/money.md`
  - `wiki/current-state.md`
- **Sources**: 
  - `reports/nightly/2026-05-03-finance-processing.md`
  - `reports/nightly/2026-05-03-finance.md`
  - `reports/nightly/2026-05-03-memory-review.md`
  - `inbox/needs-answer/2026-05-03-finance.md`
  - `inbox/needs-answer/2026-05-03-memory.md`
- **Changes**:
  - Added two pending questions to open-questions.md (finance review:3, 9or3a placement)
  - Added 9or3a financing recurring item to wiki/domains/money.md
  - Updated current-state.md to include 9or3a financing commitment
  - Updated last_updated dates to 2026-05-03
- **Missing files noted**:
  - `memory/ledger/latest` not found
  - `reports/nightly/latest` not found
- **Confidence**: high
- **User action**: None required

---

## 2026-05-02

### Money Wiki Populated
- **Pages updated**: `wiki/domains/money.md`
- **Sources**: 
  - `raw/captures/2026-05-02_finance-intake.md`
  - `data/finance/2026-04-28.md` through `2026-05-01.md`
  - `state/commitments.md`
- **Changes**:
  - Added stable facts: salary (12,949 DH), rent (3,000 DH), 9or3a payments through Oct 2026
  - Added spending patterns (~130 DH/day food for family of 2)
  - Added recent activity summary (28 Apr – 1 May)
  - Added open questions for future clarification
  - Set status to active, confidence to high
- **Confidence**: high
- **User action**: None required

### Finance Data Processing
- **Files created**:
  - `data/finance/2026-04-28.md`
  - `data/finance/2026-04-29.md`
  - `data/finance/2026-04-30.md`
  - `data/finance/2026-05-01.md`
- **Structured logs**: Categorized all transactions from raw capture into income/expenses with categories (salary, housing, debt, food, family, tech, shopping, home, subscription, transfers)
- **Automation update**: Added finance review processor so Discord finance notes are agent-processed automatically; manual money commands are fallback only
- **Reporting update**: Added weekly finance report and removed normal finance rollups from daily summaries unless money is tied to a commitment, promise to pay, or deadline
- **Sources**:
  - `scripts/process_finance_reviews.py`
  - `scripts/summarize_finance_week.py`
  - `reports/weekly/2026-05-02-finance.md`

### Task Management
- **Task cancelled**: task-20260430-invoice (due 2026-05-01) - status changed to cancelled
- **Commitment recorded**: 9or3a monthly payments through October 2026 added to `state/commitments.md`

---

## Format
Each entry should include:
- Date
- Pages updated
- Sources used
- Changes made
- Confidence level
- Any user action needed

### Manual Wiki Update (2026-05-02 - Session)
- **Pages updated**: `wiki/domains/money.md`
- **Sources**: 
  - `raw/captures/2026-05-02.md` (new bills: wifi 300 DH, wife phone 100 DH, phone 65 DH)
  - `data/finance/2026-05-02.md` (processed daily log)
  - User correction via Discord (9or3a duplicate removal)
- **Changes**:
  - Corrected 9or3a duplicate: Removed erroneous second 2,500 DH entry from 28 Apr
  - Added 2 May transactions: Wifi (-300), Wife phone (-100), Phone (-65)
  - Updated total expenses: -8,162 DH (was -7,697 DH)
  - Updated net position: +4,787 DH (was +5,252 DH)
  - Added monthly bills to Stable Facts (Wifi, Wife phone, My phone)
  - Extended date range to "28 Apr – 2 May 2026"
  - Updated food spending pattern sample period to include 2 May
- **Confidence**: high
- **User action**: None required - manually triggered wiki update completed
