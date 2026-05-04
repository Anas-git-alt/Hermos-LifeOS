from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any


def read_morning_report(root: Path, local_date: str) -> str:
    path = root / "reports" / "morning" / f"{local_date}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def build_morning_discord_summary(report_text: str, local_date: str, limit: int = 1500) -> str:
    lines = [f"Morning Report - {local_date}"]
    for heading in (
        "Top 3 Priorities",
        "Due or Overdue Commitments",
        "Deen Anchor",
        "Health Anchor",
        "Prayer / Hydration",
        "One Next Action",
    ):
        body = section(report_text, heading)
        if body:
            lines.extend(["", f"{heading}:", _compact(body, 360)])
    text = "\n".join(lines).strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def morning_review_candidates(root: Path, local_date: str, report_text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    report_path = f"reports/morning/{local_date}.md"
    for heading in ("Memory Review Needed", "Overnight Research", "Work / Money Anchor"):
        body = section(report_text, heading)
        if not body or re.search(r"(?im)^\s*(?:-\s*)?none\.?\s*$", body):
            continue
        for index, line in enumerate(_question_lines(body), start=1):
            candidates.append(
                {
                    "kind": "morning_question",
                    "title": f"{heading}: review needed",
                    "body": line,
                    "source_path": report_path,
                    "source_record_id": f"{heading.lower().replace(' ', '-')}-{index}",
                    "source_kind": "morning_report",
                    "confidence": "medium",
                    "missing_context": ["user answer"],
                }
            )
    for item in unresolved_needs_answer_items(root, local_date):
        candidates.append(item)
    return candidates


def unresolved_needs_answer_items(root: Path, local_date: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    needs_dir = root / "inbox" / "needs-answer"
    if not needs_dir.exists():
        return output
    for path in sorted(needs_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"(?im)^status:\s*resolved\b|^-\s*Status:\s*resolved\b", text):
            continue
        rel = str(path.relative_to(root))
        for index, line in enumerate(_question_lines(text), start=1):
            output.append(
                {
                    "kind": "open_question",
                    "title": f"Open question from {path.name}",
                    "body": line,
                    "source_path": rel,
                    "source_record_id": f"{path.stem}-{index}",
                    "source_kind": "needs_answer",
                    "confidence": "medium",
                    "missing_context": ["user answer"],
                }
            )
    return output


def section(text: str, heading: str) -> str:
    match = re.search(rf"(?ms)^## {re.escape(heading)}\s*(.*?)(?:^## |\Z)", text)
    return match.group(1).strip() if match else ""


def today_iso() -> str:
    return date.today().isoformat()


def _question_lines(text: str) -> list[str]:
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.search(r"\?|needs answer|clarify|review|unresolved|capture:|review:", line, re.IGNORECASE):
            lines.append(line)
    return lines


def _compact(text: str, limit: int) -> str:
    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.strip().split())
        if line:
            lines.append(line)
    compact = "\n".join(lines)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
