#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


PRAYERS = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")
STATUSES = ("on_time", "late", "missed")
EXPENSE_KINDS = ("expense", "bill", "subscription")


@dataclass(frozen=True)
class Config:
    root: Path
    db_path: Path
    target_hydration: int


def load_config() -> Config:
    root = Path(os.environ.get("LIFEOS_ROOT", str(Path.home() / "hermis-life-os"))).expanduser()
    db_path = Path(
        os.environ.get("TRACKER_DB", str(root / "data" / "lifeos_tracker.db"))
    ).expanduser()
    target_hydration = int(os.environ.get("HYDRATION_TARGET_COUNT", "8"))
    env_file = root / ".env.discord-tracker"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == "TRACKER_DB":
                db_path = Path(value.strip().strip('"')).expanduser()
            elif key.strip() == "HYDRATION_TARGET_COUNT":
                target_hydration = int(value.strip().strip('"'))
    return Config(root=root, db_path=db_path, target_hydration=target_hydration)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a clean daily prayer/hydration summary from tracker DB."
    )
    parser.add_argument(
        "day",
        nargs="?",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Local date to summarize, default: yesterday",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output markdown path, default: data/daily-summary/YYYY-MM-DD.md",
    )
    return parser.parse_args()


def fetch_prayer(con: sqlite3.Connection, day: str) -> dict[str, str | None]:
    rows = con.execute(
        """
        SELECT prayer_name, status FROM prayer_events
        WHERE local_date = ?
        """,
        (day,),
    ).fetchall()
    statuses: dict[str, str | None] = {name: None for name in PRAYERS}
    for prayer_name, status in rows:
        if prayer_name in statuses:
            statuses[prayer_name] = status
    return statuses


def fetch_hydration(con: sqlite3.Connection, day: str) -> dict[str, int]:
    daily = con.execute(
        "SELECT count FROM hydration_daily WHERE local_date = ?",
        (day,),
    ).fetchone()
    events = con.execute(
        """
        SELECT action, count(*), COALESCE(SUM(count_delta), 0)
        FROM hydration_events
        WHERE local_date = ?
        GROUP BY action
        """,
        (day,),
    ).fetchall()
    summary = {
        "total": int(daily[0]) if daily else 0,
        "drink_events": 0,
        "large_drink_events": 0,
        "manual_events": 0,
        "snoozes": 0,
        "skips": 0,
    }
    for action, count, _delta in events:
        if action == "drink":
            summary["drink_events"] = int(count)
        elif action == "large_drink":
            summary["large_drink_events"] = int(count)
        elif action == "manual":
            summary["manual_events"] = int(count)
        elif action == "snooze":
            summary["snoozes"] = int(count)
        elif action == "skip":
            summary["skips"] = int(count)
    return summary


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def fetch_finance(con: sqlite3.Connection, day: str) -> dict:
    summary = {
        "transactions": 0,
        "expense_mad": 0.0,
        "income_mad": 0.0,
        "savings_mad": 0.0,
        "transfer_mad": 0.0,
        "by_category": {},
        "non_mad": 0,
        "needs_review": 0,
    }
    if not table_exists(con, "finance_transactions"):
        return summary

    rows = con.execute(
        """
        SELECT kind, amount, currency, amount_mad, category
        FROM finance_transactions
        WHERE local_date = ? AND status = 'parsed'
        """,
        (day,),
    ).fetchall()
    summary["transactions"] = len(rows)
    for kind, _amount, currency, amount_mad, category in rows:
        if amount_mad is None:
            summary["non_mad"] += 1
            continue
        value = float(amount_mad)
        if kind in EXPENSE_KINDS:
            summary["expense_mad"] += value
            summary["by_category"][category] = summary["by_category"].get(category, 0.0) + value
        elif kind == "income":
            summary["income_mad"] += value
        elif kind == "savings_contribution":
            summary["savings_mad"] += value
        elif kind == "transfer":
            summary["transfer_mad"] += value

    if table_exists(con, "finance_parse_reviews"):
        row = con.execute(
            """
            SELECT COUNT(*)
            FROM finance_parse_reviews
            WHERE local_date = ? AND status = 'open'
            """,
            (day,),
        ).fetchone()
        summary["needs_review"] = int(row[0]) if row else 0
    return summary


def money(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def render(
    day: str,
    prayer: dict[str, str | None],
    hydration: dict[str, int],
    target: int,
) -> str:
    logged = sum(1 for status in prayer.values() if status)
    counts = {status: sum(1 for value in prayer.values() if value == status) for status in STATUSES}
    missing_names = [name for name, status in prayer.items() if status is None]

    lines = [
        f"# Daily Summary - {day}",
        "",
        "## Prayer",
    ]
    for name in PRAYERS:
        lines.append(f"- {name}: {prayer[name] or 'not_logged'}")
    lines.extend(
        [
            (
                f"- Total: {logged}/5 logged, {counts['on_time']} on_time, "
                f"{counts['late']} late, {counts['missed']} missed"
            ),
            f"- Not logged: {', '.join(missing_names) if missing_names else 'none'}",
            "",
            "## Hydration",
            f"- Total: {hydration['total']}/{target}",
            (
                f"- Drinks: {hydration['drink_events']} normal, "
                f"{hydration['large_drink_events']} large, {hydration['manual_events']} manual"
            ),
            f"- Snoozes: {hydration['snoozes']}",
            f"- Skips: {hydration['skips']}",
            "",
            "## Morning Report Guidance",
            "- Use this as the primary prayer/hydration summary for the next morning report.",
            "- Omit normal finance rollups from daily reports; include money only for commitments, promises to pay, or deadlines.",
            "- Do not read raw tracker logs unless this summary is missing or appears inconsistent.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    config = load_config()
    day = date.fromisoformat(args.day).isoformat()
    output = Path(args.output).expanduser() if args.output else config.root / "data" / "daily-summary" / f"{day}.md"

    if not config.db_path.exists():
        raise SystemExit(f"tracker DB missing: {config.db_path}")

    with sqlite3.connect(f"file:{config.db_path}?mode=ro", uri=True) as con:
        prayer = fetch_prayer(con, day)
        hydration = fetch_hydration(con, day)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(day, prayer, hydration, config.target_hydration), encoding="utf-8")
    print(f"wrote summary: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
