#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Review:
    ok: bool
    issues: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ask Hermes LifeOS to write and self-review Discord morning summary.")
    parser.add_argument("day", nargs="?", default=date.today().isoformat())
    parser.add_argument("--max-attempts", type=int, default=3)
    return parser.parse_args()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def section(text: str, heading: str) -> str:
    match = re.search(rf"(?ms)^## {re.escape(heading)}\s*(.*?)(?:^## |\Z)", text)
    return match.group(1).strip() if match else ""


def resolved_needs_answer_paths() -> list[str]:
    paths: list[str] = []
    for path in sorted((ROOT / "inbox" / "needs-answer").glob("*.md")):
        text = read(path)
        if re.search(r"(?im)^status:\s*resolved\b|^-\s*Status:\s*resolved\b", text):
            paths.append(str(path.relative_to(ROOT)))
    return paths


def review_summary(day: date, text: str) -> Review:
    issues: list[str] = []
    report = read(ROOT / "reports" / "morning" / f"{day.isoformat()}.md")
    if len(text) > 1500:
        issues.append("summary longer than 1500 chars")
    for label in ("Overnight System Status", "Top 3 Priorities", "Deen Anchor", "Health Anchor", "Prayer / Hydration", "Next Action"):
        if label not in text:
            issues.append(f"missing `{label}`")

    prayer = section(report, "Prayer / Hydration")
    for expected in re.findall(r"Total:\s*\d+/5 logged|Total:\s*\d+/8", prayer):
        if expected not in text:
            issues.append(f"misses tracker total `{expected}`")

    if "9or3a" in report.lower() and "9or3a" not in text.lower():
        issues.append("misses 9or3a commitment/recurring item from morning report")

    for rel in resolved_needs_answer_paths():
        if rel in text and re.search(r"unresolved|needs answer|next action|question", text, re.IGNORECASE):
            issues.append(f"mentions resolved needs-answer path as unresolved: `{rel}`")
    return Review(ok=not issues, issues=issues)


def hermes_cmd() -> list[str]:
    lifeos = Path("/home/ubuntu/.local/bin/lifeos")
    hermes = os.environ.get("HERMIS_MORNING_AI_CMD", str(lifeos) if lifeos.exists() else "hermes")
    return [hermes, "-z"]


def run_hermes(prompt: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HERMES_HOME"] = "/home/ubuntu/.hermes/profiles/lifeos"
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
    return f"""You are Hermis LifeOS, writing the Discord morning summary for the user.

Work in `{ROOT}` using your normal Hermes file/tool skills.
Read `reports/morning/{day.isoformat()}.md` first, plus latest finance-processing, finance, memory-review reports only if needed.

Task:
Create a concise Discord-friendly summary under 1500 characters.
Return summary text only. Do not call send_message or any Discord tool; cron delivery handles that.

Required content:
- 🌙 Overnight System Status: finance review result, memory review result, morning report status.
- 🎯 Top 3 Priorities from morning report.
- 📅 Due/overdue commitments.
- 🕌 Deen Anchor.
- 💪 Health Anchor.
- 💧 Prayer / Hydration, including prayer total and hydration total from morning report.
- ❓ Unresolved question only if morning report has unresolved memory review needed.
- ➡️ Next Action.

Rules:
- Do not mention resolved `inbox/needs-answer` files as blockers.
- If morning report says Memory Review Needed: None, do not include unresolved memory questions.
- Preserve important specifics: prayer total, hydration total, 9or3a if present, pending work review if top priority.
- Self-review before final. If missing required content, revise before final.
"""


def retry_prompt(day: date, previous: str, issues: list[str]) -> str:
    return f"""Your previous Discord morning summary failed automated review.

Fix it using `reports/morning/{day.isoformat()}.md`.
Return corrected summary only, under 1500 characters.

Failures:
{chr(10).join(f"- {issue}" for issue in issues)}

Previous summary:
```text
{previous}
```
"""


def main() -> int:
    args = parse_args()
    day = date.fromisoformat(args.day)
    prompt = base_prompt(day)
    last_output = ""
    last_issues: list[str] = []

    for attempt in range(1, args.max_attempts + 1):
        completed = run_hermes(prompt)
        if completed.returncode != 0:
            print(completed.stderr.strip() or f"Hermes exited {completed.returncode}", file=sys.stderr)
            return completed.returncode or 1

        last_output = completed.stdout.strip()
        review = review_summary(day, last_output)
        if review.ok:
            print(last_output)
            return 0

        last_issues = review.issues
        prompt = retry_prompt(day, last_output, review.issues)
        print(f"attempt {attempt} failed review: {'; '.join(review.issues)}", file=sys.stderr)

    print("Hermes Discord morning summary failed review after retries:", file=sys.stderr)
    for issue in last_issues:
        print(f"- {issue}", file=sys.stderr)
    if last_output:
        print(last_output)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
