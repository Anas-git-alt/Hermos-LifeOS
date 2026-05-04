#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from summarize_tracker_day import EXPENSE_KINDS, money, table_exists


@dataclass(frozen=True)
class Config:
    root: Path
    db_path: Path


def load_config() -> Config:
    root = Path(os.environ.get("LIFEOS_ROOT", str(Path.home() / "hermis-life-os"))).expanduser()
    db_path = Path(os.environ.get("TRACKER_DB", str(root / "data" / "lifeos_tracker.db"))).expanduser()
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
    parser = argparse.ArgumentParser(description="Write weekly finance rollup.")
    parser.add_argument(
        "week_ending",
        nargs="?",
        default=date.today().isoformat(),
        help="Week ending date, inclusive. Default: today.",
    )
    parser.add_argument("--output", default=None, help="Default: reports/weekly/YYYY-MM-DD-finance.md")
    return parser.parse_args()


def fetch_week(con: sqlite3.Connection, start: str, end: str) -> dict:
    summary = {
        "transactions": 0,
        "expense_mad": 0.0,
        "income_mad": 0.0,
        "savings_mad": 0.0,
        "transfer_mad": 0.0,
        "by_category": {},
        "by_day": {},
        "non_mad": [],
        "open_reviews": [],
    }
    if not table_exists(con, "finance_transactions"):
        return summary
    rows = con.execute(
        """
        SELECT local_date, kind, amount, currency, amount_mad, category, description
        FROM finance_transactions
        WHERE local_date BETWEEN ? AND ? AND status = 'parsed'
        ORDER BY local_date, id
        """,
        (start, end),
    ).fetchall()
    summary["transactions"] = len(rows)
    for local_date, kind, amount, currency, amount_mad, category, description in rows:
        if amount_mad is None:
            summary["non_mad"].append(
                {
                    "local_date": local_date,
                    "kind": kind,
                    "amount": amount,
                    "currency": currency,
                    "description": description,
                }
            )
            continue
        value = float(amount_mad)
        if kind in EXPENSE_KINDS:
            summary["expense_mad"] += value
            summary["by_category"][category] = summary["by_category"].get(category, 0.0) + value
            summary["by_day"][local_date] = summary["by_day"].get(local_date, 0.0) + value
        elif kind == "income":
            summary["income_mad"] += value
        elif kind == "savings_contribution":
            summary["savings_mad"] += value
        elif kind == "transfer":
            summary["transfer_mad"] += value

    if table_exists(con, "finance_parse_reviews"):
        review_rows = con.execute(
            """
            SELECT id, local_date, reason, raw_text
            FROM finance_parse_reviews
            WHERE local_date BETWEEN ? AND ? AND status = 'open'
            ORDER BY local_date, id
            """,
            (start, end),
        ).fetchall()
        summary["open_reviews"] = [
            {"id": row[0], "local_date": row[1], "reason": row[2], "raw_text": row[3]}
            for row in review_rows
        ]
    return summary


def render(start: str, end: str, summary: dict) -> str:
    lines = [
        f"# Weekly Finance Report - {start} to {end}",
        "",
        "## Summary",
        f"- Transactions: {summary['transactions']}",
        f"- Expenses: {money(summary['expense_mad'])} MAD",
        f"- Income: {money(summary['income_mad'])} MAD",
        f"- Savings: {money(summary['savings_mad'])} MAD",
        f"- Transfers: {money(summary['transfer_mad'])} MAD",
        f"- Net tracked cash flow: {money(summary['income_mad'] - summary['expense_mad'] - summary['savings_mad'])} MAD",
        "",
        "## Spend By Category",
    ]
    if summary["by_category"]:
        for category, amount in sorted(summary["by_category"].items(), key=lambda item: item[1], reverse=True):
            lines.append(f"- {category}: {money(amount)} MAD")
    else:
        lines.append("- none")

    lines.extend(["", "## Spend By Day"])
    if summary["by_day"]:
        for local_date, amount in sorted(summary["by_day"].items()):
            lines.append(f"- {local_date}: {money(amount)} MAD")
    else:
        lines.append("- none")

    lines.extend(["", "## Non-MAD Entries"])
    if summary["non_mad"]:
        for item in summary["non_mad"]:
            lines.append(f"- {item['local_date']}: {item['amount']} {item['currency']} {item['description']}")
    else:
        lines.append("- none")

    lines.extend(["", "## Open Review Items"])
    if summary["open_reviews"]:
        for item in summary["open_reviews"]:
            snippet = item["raw_text"] if len(item["raw_text"]) <= 100 else item["raw_text"][:97] + "..."
            lines.append(f"- review:{item['id']} ({item['local_date']}, {item['reason']}): {snippet}")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Daily Report Policy",
            "- Normal finance rollups stay weekly.",
            "- Daily reports should mention money only when tied to a promise to pay, commitment, or deadline.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    config = load_config()
    end_day = date.fromisoformat(args.week_ending)
    start_day = end_day - timedelta(days=6)
    output = Path(args.output).expanduser() if args.output else config.root / "reports" / "weekly" / f"{end_day.isoformat()}-finance.md"

    if not config.db_path.exists():
        raise SystemExit(f"tracker DB missing: {config.db_path}")
    with sqlite3.connect(f"file:{config.db_path}?mode=ro", uri=True) as con:
        summary = fetch_week(con, start_day.isoformat(), end_day.isoformat())

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(start_day.isoformat(), end_day.isoformat(), summary), encoding="utf-8")
    print(f"wrote weekly finance report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
