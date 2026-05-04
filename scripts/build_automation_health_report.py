#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "apps" / "discord_tracker"
sys.path.insert(0, str(APP_DIR))

from store import TrackerStore  # noqa: E402


@dataclass(frozen=True)
class Config:
    root: Path
    db_path: Path


def load_config() -> Config:
    root = Path(os.environ.get("LIFEOS_ROOT", str(ROOT))).expanduser()
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
    parser = argparse.ArgumentParser(description="Write weekly review automation health report.")
    parser.add_argument("day", nargs="?", default=date.today().isoformat())
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def event_rows(db: sqlite3.Connection, start_utc: str, end_utc: str) -> list[dict[str, Any]]:
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """
        SELECT review_item_id, event, payload_json, created_at_utc
        FROM review_item_events
        WHERE created_at_utc >= ? AND created_at_utc < ?
        ORDER BY created_at_utc ASC
        """,
        (start_utc, end_utc),
    ).fetchall()
    output = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        output.append(
            {
                "review_item_id": row["review_item_id"],
                "event": row["event"],
                "payload": payload if isinstance(payload, dict) else {},
                "created_at_utc": row["created_at_utc"],
            }
        )
    return output


def current_items(db: sqlite3.Connection) -> list[dict[str, Any]]:
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM review_items").fetchall()
    return [dict(row) for row in rows]


def build_report(day: str, events: list[dict[str, Any]], items: list[dict[str, Any]]) -> str:
    counts = Counter()
    failed_validations = []
    for event in events:
        name = event["event"]
        payload = event["payload"]
        status = payload.get("status")
        if name == "surfaced":
            counts["resurfaced"] += 1
        if name == "expired" or status == "expired":
            counts["expired"] += 1
        if name in {"status:auto_processed", "auto_processed"} or status == "auto_processed":
            counts["auto_processed"] += 1
        if name == "status:approved" or status == "approved":
            counts["approved"] += 1
        if name == "status:rejected" or status == "rejected":
            counts["rejected"] += 1
        validation = payload.get("ai_validation") or {}
        if isinstance(validation, dict) and (
            validation.get("decision") == "ask_clarification"
            or validation.get("valid") is False
            or validation.get("contradictions")
            or validation.get("unsafe_assumptions")
        ):
            failed_validations.append(event)

    counts["pending"] = sum(1 for item in items if item.get("status") in {"pending", "needs_clarification", "expired"})

    repeated_questions = Counter()
    stale_kinds = Counter()
    delivery_failures = Counter()
    now = datetime.now(timezone.utc)
    for item in items:
        if item.get("status") in {"pending", "needs_clarification", "expired"}:
            body = " ".join(str(item.get("body") or "").split())[:100]
            if body:
                repeated_questions[body] += 1
            created = _parse_dt(item.get("created_at_utc"))
            if created and now - created >= timedelta(days=2):
                stale_kinds[str(item.get("kind") or "unknown")] += 1
            if item.get("surface_count") and not item.get("discord_message_id") and not item.get("parent_discord_message_id"):
                delivery_failures[str(item.get("source_kind") or item.get("kind") or "unknown")] += 1
    for event in events:
        if event["event"] == "discord_delivery_failed":
            delivery_failures[str(event["payload"].get("source_kind") or "unknown")] += 1

    lines = [
        f"# Automation Health - {day}",
        "",
        "## Counts",
        f"- Auto-processed: {counts['auto_processed']}",
        f"- Approved: {counts['approved']}",
        f"- Rejected: {counts['rejected']}",
        f"- Expired: {counts['expired']}",
        f"- Resurfaced: {counts['resurfaced']}",
        f"- Still pending: {counts['pending']}",
        "",
        "## Top Friction Sources",
        "",
        "### Repeated Questions",
    ]
    lines.extend(_counter_lines(repeated_questions, min_count=2))
    lines.extend(["", "### Stale Review Kinds"])
    lines.extend(_counter_lines(stale_kinds))
    lines.extend(["", "### Failed AI Validations"])
    if failed_validations:
        for event in failed_validations[:5]:
            lines.append(f"- {event['review_item_id'] or 'unknown'} at {event['created_at_utc']}")
    else:
        lines.append("- none")
    lines.extend(["", "### Discord Delivery Failures"])
    lines.extend(_counter_lines(delivery_failures))
    lines.extend(
        [
            "",
            "## Policy",
            "- Safe automation only handles high-confidence, explicitly safe, reversible review items.",
            "- Sensitive finance, health, family, legal, and durable identity/memory claims stay behind approval.",
            "",
        ]
    )
    return "\n".join(lines)


async def main_async() -> int:
    args = parse_args()
    config = load_config()
    report_day = date.fromisoformat(args.day)
    output = (
        Path(args.output).expanduser()
        if args.output
        else config.root / "reports" / "weekly" / f"{report_day.isoformat()}-automation-health.md"
    )
    store = TrackerStore(config.db_path, config.root)
    await store.init()

    end_utc = datetime.combine(report_day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    start_utc = end_utc - timedelta(days=7)
    with sqlite3.connect(config.db_path) as db:
        events = event_rows(db, start_utc.isoformat(), end_utc.isoformat())
        items = current_items(db)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_report(report_day.isoformat(), events, items), encoding="utf-8")
    print(f"wrote automation health report: {output}")
    return 0


def _counter_lines(counter: Counter, *, min_count: int = 1) -> list[str]:
    values = [(label, count) for label, count in counter.most_common(5) if count >= min_count]
    if not values:
        return ["- none"]
    return [f"- {label}: {count}" for label, count in values]


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
