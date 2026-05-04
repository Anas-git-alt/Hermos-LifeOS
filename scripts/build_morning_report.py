#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SECTIONS = (
    "Top 3 Priorities",
    "Due or Overdue Commitments",
    "Deen Anchor",
    "Health Anchor",
    "Prayer / Hydration",
    "Work / Money Anchor",
    "Overnight Research",
    "Memory Review Needed",
    "One Next Action",
)


@dataclass(frozen=True)
class Review:
    ok: bool
    issues: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ask Hermes LifeOS to write and self-review morning report.")
    parser.add_argument("day", nargs="?", default=date.today().isoformat())
    parser.add_argument("--max-attempts", type=int, default=3)
    return parser.parse_args()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def has_populated_tracker_summary(day: date) -> bool:
    summary = read(ROOT / "data" / "daily-summary" / f"{(day - timedelta(days=1)).isoformat()}.md")
    if not summary:
        return False
    return bool(
        re.search(r"Total:\s*[1-5]/5 logged", summary)
        or re.search(r"Total:\s*[1-9]\d*/8", summary)
    )


def pending_questions_none() -> bool:
    text = read(ROOT / "wiki" / "open-questions.md")
    pending = re.search(r"(?ms)^## Pending Questions\s*(.*?)(?:^## |\Z)", text)
    return bool(pending and re.search(r"(?im)^\s*None\.\s*$", pending.group(1)))


def resolved_needs_answer_paths() -> list[str]:
    paths: list[str] = []
    for path in sorted((ROOT / "inbox" / "needs-answer").glob("*.md")):
        text = read(path)
        if re.search(r"(?im)^status:\s*resolved\b|^-\s*Status:\s*resolved\b", text):
            paths.append(str(path.relative_to(ROOT)))
    return paths


def review_report(day: date, text: str) -> Review:
    issues: list[str] = []
    title = f"# Morning Report — {day.isoformat()}"
    if title not in text:
        issues.append(f"missing exact title `{title}`")
    for section in REQUIRED_SECTIONS:
        if f"## {section}" not in text:
            issues.append(f"missing section `{section}`")
    if re.search(r"^\s*[{\[]|\"event\"|\"logged_at_utc\"|\"message_id\"", text, re.MULTILINE):
        issues.append("contains raw JSON/log fields")
    if has_populated_tracker_summary(day) and re.search(
        r"No deen anchor found|No health anchor found|Prayer entries:\s*0|Hydration daily count:\s*0",
        text,
        re.IGNORECASE,
    ):
        issues.append("ignored populated yesterday tracker summary")
    if pending_questions_none() and re.search(r"needs-answer|unresolved question|answer memory", text, re.IGNORECASE):
        issues.append("mentions unresolved needs-answer even though pending questions are None")
    for rel in resolved_needs_answer_paths():
        if rel in text and re.search(r"unresolved|needs answer|next action|question", text, re.IGNORECASE):
            issues.append(f"mentions resolved needs-answer path as unresolved: `{rel}`")
    money = read(ROOT / "wiki" / "domains" / "money.md")
    if "9or3a" in money.lower() and "9or3a" not in text.lower():
        issues.append("misses confirmed 9or3a recurring item/commitment")
    state_work = read(ROOT / "state" / "work.md")
    if "capture:" in state_work and not re.search(r"!work review|work capture|capture:", text, re.IGNORECASE):
        issues.append("misses pending work review captures")
    return Review(ok=not issues, issues=issues)


def hermes_cmd() -> list[str]:
    lifeos = Path.home() / ".local" / "bin" / "lifeos"
    hermes = os.environ.get("HERMIS_MORNING_AI_CMD", str(lifeos) if lifeos.exists() else "hermes")
    return [hermes, "--skills", "life-morning-report", "-z"]


def run_hermes(prompt: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HERMES_HOME"] = os.environ.get("HERMES_HOME", str(Path.home() / ".hermes" / "profiles" / "lifeos"))
    env.setdefault("LIFEOS_ROOT", str(ROOT))
    return subprocess.run(
        [*hermes_cmd(), prompt],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def base_prompt(day: date) -> str:
    yesterday = day - timedelta(days=1)
    return f"""You are Hermis LifeOS, the user's dedicated Life OS agent.

Work in `{ROOT}` using your normal file/tool skills. You are NOT a deterministic formatter.
Use judgment, but ground every claim in repo files.

Task:
Create `reports/morning/{day.isoformat()}.md` for {day.isoformat()}.
Write the file, then return the exact report content as final answer.

Critical source rules:
- Before writing, make sure yesterday tracker summary exists and is fresh:
  run `./scripts/summarize_tracker_day.py {yesterday.isoformat()}` if needed.
- For prayer/hydration, use `data/daily-summary/{yesterday.isoformat()}.md` first.
- Treat morning as review of yesterday plus today planning. Do not use incomplete same-day tracker data as yesterday.
- Read `wiki/open-questions.md`; if Pending Questions is `None.`, do not show needs-answer blockers.
- Read `inbox/needs-answer/*.md`, but ignore files with `Status: resolved`.
- Read `wiki/domains/money.md`; include confirmed manual recurring 9or3a item if present.
- Read `state/work.md` and work reports/state; pending review-gated captures should become priority/context, not confirmed tasks.
- Use finance processing/summary reports, not raw finance logs or tracker DB rows.
- Do not dump raw logs/JSON.

Required report shape:
# Morning Report — {day.isoformat()}

## Top 3 Priorities
## Due or Overdue Commitments
## Deen Anchor
## Health Anchor
## Prayer / Hydration
## Work / Money Anchor
## Overnight Research
## Memory Review Needed
## One Next Action

Self-review before final:
1. Does report include yesterday prayer total and hydration total when tracker summary has them?
2. Does report avoid resolved needs-answer questions?
3. Does report mention 9or3a if money wiki has it?
4. Does report surface pending work review captures if state/work has them?
5. Is final answer exactly file content?

If any check fails, revise file before final.
"""


def retry_prompt(day: date, previous: str, issues: list[str]) -> str:
    return f"""Your previous morning report failed automated review.

Fix the file `reports/morning/{day.isoformat()}.md`, then return the full corrected report only.

Review failures:
{chr(10).join(f"- {issue}" for issue in issues)}

Previous output:
```markdown
{previous}
```

Re-read sources with your tools. Do not guess. Do not mention resolved needs-answer items as unresolved.
"""


def main() -> int:
    args = parse_args()
    day = date.fromisoformat(args.day)
    report_path = ROOT / "reports" / "morning" / f"{day.isoformat()}.md"
    prompt = base_prompt(day)
    last_output = ""
    last_issues: list[str] = []

    for attempt in range(1, args.max_attempts + 1):
        completed = run_hermes(prompt)
        if completed.returncode != 0:
            print(completed.stderr.strip() or f"Hermes exited {completed.returncode}", file=sys.stderr)
            return completed.returncode or 1

        last_output = read(report_path).strip() or completed.stdout.strip()
        review = review_report(day, last_output)
        if review.ok:
            if not report_path.exists():
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(last_output + "\n", encoding="utf-8")
            print(last_output)
            return 0

        last_issues = review.issues
        prompt = retry_prompt(day, last_output, review.issues)
        print(f"attempt {attempt} failed review: {'; '.join(review.issues)}", file=sys.stderr)

    print("Hermes morning report failed review after retries:", file=sys.stderr)
    for issue in last_issues:
        print(f"- {issue}", file=sys.stderr)
    if last_output:
        print(last_output)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
