#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from summarize_tracker_day import fetch_finance, money, table_exists


@dataclass(frozen=True)
class Config:
    root: Path
    db_path: Path


def load_config() -> Config:
    root = Path(os.environ.get("LIFEOS_ROOT", "/home/ubuntu/hermis-life-os")).expanduser()
    db_path = Path(
        os.environ.get("TRACKER_DB", str(root / "data" / "lifeos_tracker.db"))
    ).expanduser()
    env_file = root / ".env.discord-tracker"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == "TRACKER_DB":
                db_path = Path(value.strip().strip('"')).expanduser()
    return Config(root=root, db_path=db_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write finance nightly summary and refresh money wiki.")
    parser.add_argument(
        "day",
        nargs="?",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Local date to summarize, default: yesterday",
    )
    parser.add_argument("--output", default=None, help="Default: reports/nightly/YYYY-MM-DD-finance.md")
    parser.add_argument("--no-wiki", action="store_true", help="Do not update wiki/domains/money.md")
    return parser.parse_args()


def fetch_recurring(con: sqlite3.Connection) -> list[tuple[str, str, str, str, str]]:
    if not table_exists(con, "finance_recurring_items"):
        return []
    return con.execute(
        """
        SELECT name, kind, amount, currency, category
        FROM finance_recurring_items
        WHERE status = 'active'
        ORDER BY name
        """
    ).fetchall()


def fetch_open_reviews(con: sqlite3.Connection) -> list[tuple[int, str, str, str]]:
    if not table_exists(con, "finance_parse_reviews"):
        return []
    return con.execute(
        """
        SELECT id, local_date, reason, raw_text
        FROM finance_parse_reviews
        WHERE status = 'open'
        ORDER BY created_at_utc DESC
        LIMIT 20
        """
    ).fetchall()


def extract_section(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    out: list[str] = []
    in_section = False
    marker = f"## {heading}"
    for line in lines:
        if line.strip() == marker:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and line.strip():
            out.append(line.rstrip())
    return out


def fetch_manual_recurring_items(wiki_path: Path) -> list[str]:
    if not wiki_path.exists():
        return []
    return [
        line
        for line in extract_section(wiki_path.read_text(encoding="utf-8"), "Manual Recurring Items")
        if line.startswith("- ")
    ]


def render_report(
    day: str,
    finance: dict,
    recurring: list[tuple],
    reviews: list[tuple],
    manual_recurring: list[str],
) -> str:
    lines = [
        f"# Finance Nightly Summary - {day}",
        "",
        "## Totals",
        f"- Transactions: {finance['transactions']}",
        f"- Expenses: {money(finance['expense_mad'])} MAD",
        f"- Income: {money(finance['income_mad'])} MAD",
        f"- Savings: {money(finance['savings_mad'])} MAD",
        f"- Transfers: {money(finance['transfer_mad'])} MAD",
        f"- Non-MAD entries not normalized: {finance['non_mad']}",
        f"- Needs review today: {finance['needs_review']}",
        "",
        "## Category Spend",
    ]
    if finance["by_category"]:
        for category, amount in sorted(finance["by_category"].items()):
            lines.append(f"- {category}: {money(amount)} MAD")
    else:
        lines.append("- none")

    lines.extend(["", "## Recurring Items"])
    if recurring:
        for name, kind, amount, currency, category in recurring:
            lines.append(f"- {name}: {kind}, {amount} {currency}, {category}")
    else:
        lines.append("- none")

    lines.extend(["", "## Manual Recurring Items"])
    lines.extend(manual_recurring or ["- none"])

    lines.extend(["", "## Open Review Items"])
    if reviews:
        for review_id, local_date, reason, raw_text in reviews:
            snippet = raw_text if len(raw_text) <= 100 else raw_text[:97] + "..."
            lines.append(f"- review:{review_id} ({local_date}, {reason}): {snippet}")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Retrieval Guidance",
            "- Use this summary and wiki/domains/money.md for Hermes answers first.",
            "- Do not index raw finance logs, tracker DB rows, or Discord message captures by default.",
            "",
        ]
    )
    return "\n".join(lines)


def render_money_wiki(
    day: str,
    report_path: Path,
    finance: dict,
    recurring: list[tuple],
    reviews: list[tuple],
    manual_recurring: list[str],
) -> str:
    recurring_text = "\n".join(
        f"- {name}: {kind}, {amount} {currency}, {category}"
        for name, kind, amount, currency, category in recurring
    ) or "- none"
    category_text = "\n".join(
        f"- {category}: {money(amount)} MAD"
        for category, amount in sorted(finance["by_category"].items())
    ) or "- none"
    review_text = f"{len(reviews)} open review item(s)" if reviews else "none"
    manual_recurring_text = "\n".join(manual_recurring) or "- none"
    return f"""---
status: active
last_updated: {day}
confidence: medium
primary_sources:
  - {report_path.relative_to(report_path.parents[2])}
---

# Money

## Current Understanding

Hermis tracks finance through the Discord finance tracker, the local SQLite tracker DB, and compact daily/nightly summaries. Answers should use summaries first and only inspect raw finance logs when correcting or auditing entries.

## Current Totals

- Expenses: {money(finance['expense_mad'])} MAD
- Income: {money(finance['income_mad'])} MAD
- Savings: {money(finance['savings_mad'])} MAD
- Transfers: {money(finance['transfer_mad'])} MAD
- Non-MAD entries not normalized: {finance['non_mad']}
- Review backlog: {review_text}

## Category Spend

{category_text}

## Recurring Items

{recurring_text}

## Manual Recurring Items

{manual_recurring_text}

## Sources

- {report_path.relative_to(report_path.parents[2])}
"""


def main() -> int:
    args = parse_args()
    config = load_config()
    day = date.fromisoformat(args.day).isoformat()
    output = Path(args.output).expanduser() if args.output else config.root / "reports" / "nightly" / f"{day}-finance.md"

    if not config.db_path.exists():
        raise SystemExit(f"tracker DB missing: {config.db_path}")

    with sqlite3.connect(f"file:{config.db_path}?mode=ro", uri=True) as con:
        finance = fetch_finance(con, day)
        recurring = fetch_recurring(con)
        reviews = fetch_open_reviews(con)

    wiki_path = config.root / "wiki" / "domains" / "money.md"
    manual_recurring = fetch_manual_recurring_items(wiki_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_report(day, finance, recurring, reviews, manual_recurring), encoding="utf-8")
    if not args.no_wiki:
        wiki_path.write_text(
            render_money_wiki(day, output, finance, recurring, reviews, manual_recurring),
            encoding="utf-8",
        )
    print(f"wrote finance summary: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
