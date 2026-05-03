#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "apps" / "discord_tracker"
sys.path.insert(0, str(APP_DIR))

from finance import FINANCE_CATEGORIES, FinanceEntry  # noqa: E402
from store import TrackerStore  # noqa: E402


@dataclass(frozen=True)
class Config:
    root: Path
    db_path: Path
    agent_cmd: str | None


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
    return Config(
        root=root,
        db_path=db_path,
        agent_cmd=os.environ.get("HERMIS_FINANCE_AGENT_CMD")
        or f"{sys.executable} {root / 'scripts' / 'run_finance_ai_resolver.py'}",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automatically process finance review queue into structured ledger entries."
    )
    parser.add_argument(
        "day",
        nargs="?",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Local date to process, default: yesterday",
    )
    parser.add_argument("--all-open", action="store_true", help="Process all open finance reviews.")
    parser.add_argument("--dry-run", action="store_true", help="Write report without applying transactions.")
    parser.add_argument(
        "--output",
        default=None,
        help="Default: reports/nightly/YYYY-MM-DD-finance-processing.md",
    )
    return parser.parse_args()


def fetch_reviews(con: sqlite3.Connection, day: str, all_open: bool) -> list[dict[str, Any]]:
    where = "status = 'open'" if all_open else "status = 'open' AND local_date = ?"
    params: tuple[Any, ...] = () if all_open else (day,)
    rows = con.execute(
        f"""
        SELECT id, local_date, reason, raw_text, source, source_message_id,
               source_channel_id, logged_by, created_at_utc
        FROM finance_parse_reviews
        WHERE {where}
        ORDER BY local_date, created_at_utc, id
        """,
        params,
    ).fetchall()
    return [
        {
            "id": row[0],
            "local_date": row[1],
            "reason": row[2],
            "raw_text": row[3],
            "source": row[4],
            "source_message_id": row[5],
            "source_channel_id": row[6],
            "logged_by": row[7],
            "created_at_utc": row[8],
        }
        for row in rows
    ]


def agent_payload(day: str, reviews: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "task": "resolve_finance_reviews",
        "day": day,
        "rules": {
            "default_currency": "MAD",
            "allow_multiple_entries_per_review": True,
            "ask_only_if_unclear": True,
            "do_not_create_memory": True,
        },
        "schema": {
            "resolved": [
                {
                    "review_id": "integer",
                    "entries": [
                        {
                            "kind": "expense|bill|subscription|income|transfer|savings_contribution|savings_goal",
                            "amount": "positive number",
                            "currency": "MAD|USD|EUR|GBP|other",
                            "category": "known finance category",
                            "merchant": "optional short merchant/payee name",
                            "description": "short human text",
                        }
                    ],
                }
            ],
            "questions": [
                {
                    "review_id": "integer",
                    "question": "short clarification needed from user",
                }
            ],
        },
        "reviews": reviews,
    }


def run_agent(config: Config, payload: dict[str, Any]) -> dict[str, Any] | None:
    if not config.agent_cmd or not payload.get("reviews"):
        return None
    completed = subprocess.run(
        config.agent_cmd,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        shell=True,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        return {"error": completed.stderr.strip() or f"agent exited {completed.returncode}"}
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {"error": f"agent returned invalid JSON: {exc}"}


ALLOWED_KINDS = {
    "expense",
    "bill",
    "subscription",
    "income",
    "transfer",
    "savings_contribution",
    "savings_goal",
}


def _parse_positive_decimal(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("amount must be a positive number") from exc
    if amount <= 0:
        raise ValueError("amount must be a positive number")
    return amount


def _validate_entry(raw: Any) -> FinanceEntry:
    if not isinstance(raw, dict):
        raise ValueError("entry must be an object")

    kind = str(raw.get("kind") or "").strip()
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"invalid kind: {kind!r}")

    amount = _parse_positive_decimal(raw.get("amount"))
    currency = str(raw.get("currency") or "MAD").strip().upper()
    if not currency:
        raise ValueError("currency is required")

    category = str(raw.get("category") or "").strip()
    if category not in FINANCE_CATEGORIES:
        raise ValueError(f"invalid category: {category!r}")

    description = " ".join(str(raw.get("description") or "").split())
    if not description:
        raise ValueError("description is required")

    merchant = " ".join(str(raw.get("merchant") or description).split())
    amount_mad = amount if currency == "MAD" else None
    return FinanceEntry(
        kind=kind,
        amount=amount,
        currency=currency,
        amount_mad=amount_mad,
        category=category,
        merchant=merchant,
        description=description,
        confidence="high",
        review_reason=None,
    )


def _question(review_id: int, text: str) -> dict[str, Any]:
    return {"review_id": review_id, "question": text}


def apply_agent_result(agent_result: dict[str, Any], reviews_by_id: dict[int, dict[str, Any]]):
    if not isinstance(agent_result, dict):
        return [], [_question(review_id, "Hermis AI resolver returned invalid output.") for review_id in reviews_by_id]

    resolved = []
    questions = []

    for item in agent_result.get("questions") or []:
        try:
            review_id = int(item["review_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if review_id not in reviews_by_id:
            continue
        text = " ".join(str(item.get("question") or "").split())
        questions.append(_question(review_id, text or "Hermis AI needs clarification."))

    answered_ids = {int(item["review_id"]) for item in questions if int(item["review_id"]) in reviews_by_id}
    for item in agent_result.get("resolved") or []:
        try:
            review_id = int(item["review_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if review_id not in reviews_by_id or review_id in answered_ids:
            continue
        try:
            entries = tuple(_validate_entry(entry) for entry in item.get("entries") or [])
            if not entries:
                raise ValueError("entries must be non-empty")
            resolved.append((review_id, entries))
            answered_ids.add(review_id)
        except (TypeError, ValueError) as exc:
            questions.append(_question(review_id, f"Hermis AI output could not be applied: {exc}."))
            answered_ids.add(review_id)

    for review_id in reviews_by_id:
        if review_id not in answered_ids:
            questions.append(
                _question(review_id, "Hermis AI returned no resolution or clarification for this review.")
            )
    return resolved, questions


async def apply_resolutions(store: TrackerStore, resolved, dry_run: bool):
    applied = []
    for review_id, entries in resolved:
        if dry_run:
            applied.append({"review_id": review_id, "transaction_ids": [], "entries": entries})
            continue
        records = await store.resolve_finance_review(review_id, entries)
        if records:
            applied.append(
                {
                    "review_id": review_id,
                    "transaction_ids": [record["id"] for record in records],
                    "entries": entries,
                }
            )
    return applied


def write_questions(config: Config, day: str, questions: list[dict[str, Any]]) -> Path | None:
    if not questions:
        return None
    path = config.root / "inbox" / "needs-answer" / f"{day}-finance.md"
    lines = [f"# Finance Clarifications {day}", ""]
    for item in questions:
        lines.append(f"- review:{item['review_id']}: {item['question']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def render_report(day: str, reviews, applied, questions, agent_error: str | None, question_path: Path | None) -> str:
    lines = [
        f"# Finance Review Processing - {day}",
        "",
        "## Result",
        f"- Reviews considered: {len(reviews)}",
        f"- Reviews resolved: {len(applied)}",
        f"- Clarifications needed: {len(questions)}",
    ]
    if agent_error:
        lines.append(f"- AI resolver issue: {agent_error}")
    if question_path:
        lines.append(f"- Questions written: {question_path}")

    lines.extend(["", "## Applied"])
    if applied:
        for item in applied:
            ids = ", ".join(str(tx_id) for tx_id in item["transaction_ids"]) or "dry-run"
            entry_count = len(item["entries"])
            lines.append(f"- review:{item['review_id']} -> tx {ids} ({entry_count} entr{'y' if entry_count == 1 else 'ies'})")
    else:
        lines.append("- none")

    lines.extend(["", "## Questions"])
    if questions:
        for item in questions:
            lines.append(f"- review:{item['review_id']}: {item['question']}")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Policy",
            "- Finance Discord input is review-first and AI-resolved; scripts do not infer amounts/categories from raw text.",
            "- Manual `!money edit` remains fallback only.",
            "- Weekly reports carry normal finance rollups; daily reports only mention payment promises or deadlines.",
            "",
        ]
    )
    return "\n".join(lines)


async def main_async() -> int:
    args = parse_args()
    config = load_config()
    day = date.fromisoformat(args.day).isoformat()
    output = Path(args.output).expanduser() if args.output else config.root / "reports" / "nightly" / f"{day}-finance-processing.md"

    if not config.db_path.exists():
        raise SystemExit(f"tracker DB missing: {config.db_path}")

    store = TrackerStore(config.db_path, config.root)
    await store.init()
    with sqlite3.connect(f"file:{config.db_path}?mode=ro", uri=True) as con:
        reviews = fetch_reviews(con, day, args.all_open)

    payload = agent_payload(day, reviews)
    reviews_by_id = {int(review["id"]): review for review in reviews}
    agent_error = None
    agent_result = run_agent(config, payload)
    if agent_result and "error" not in agent_result:
        resolved, questions = apply_agent_result(agent_result, reviews_by_id)
    else:
        agent_error = (agent_result or {}).get("error") if agent_result else None
        resolved = []
        questions = [
            _question(
                int(review["id"]),
                "Hermis AI finance resolver failed or was not configured; review was not guessed by script.",
            )
            for review in reviews
        ]

    applied = await apply_resolutions(store, resolved, args.dry_run)
    question_path = write_questions(config, day, questions)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_report(day, reviews, applied, questions, agent_error, question_path),
        encoding="utf-8",
    )
    print(f"wrote finance processing report: {output}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
