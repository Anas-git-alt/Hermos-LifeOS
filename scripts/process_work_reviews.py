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
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "apps" / "discord_tracker"
sys.path.insert(0, str(APP_DIR))

from store import TrackerStore  # noqa: E402
from work import WORK_PRIORITIES, WorkItemDraft  # noqa: E402


ALLOWED_CREATE_STATUSES = {"open", "waiting", "blocked"}


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
        agent_cmd=os.environ.get("HERMIS_WORK_AGENT_CMD")
        or f"{sys.executable} {root / 'scripts' / 'run_work_ai_reviewer.py'}",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process unconfirmed work captures into reviewed work items.")
    parser.add_argument(
        "day",
        nargs="?",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Local date to process, default: yesterday",
    )
    parser.add_argument("--all-open", action="store_true", help="Process all unreviewed/unclear work captures.")
    parser.add_argument("--dry-run", action="store_true", help="Write report without applying changes.")
    parser.add_argument(
        "--output",
        default=None,
        help="Default: reports/work/YYYY-MM-DD-parsing-review.md",
    )
    return parser.parse_args()


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
    return row is not None


def fetch_captures(con: sqlite3.Connection, day: str, all_open: bool) -> list[dict[str, Any]]:
    if not table_exists(con, "work_captures"):
        return []
    where = "review_status IN ('unreviewed', 'clarification')" if all_open else (
        "review_status IN ('unreviewed', 'clarification') AND local_date = ?"
    )
    params: tuple[Any, ...] = () if all_open else (day,)
    rows = con.execute(
        f"""
        SELECT id, local_date, source, source_message_id, source_channel_id,
               source_channel_name, logged_by, raw_text, draft_parse_json,
               confidence, review_reason, review_status, clarification_question,
               created_at_utc
        FROM work_captures
        WHERE {where}
        ORDER BY local_date, created_at_utc, id
        """,
        params,
    ).fetchall()
    captures = []
    for row in rows:
        try:
            draft_parse = json.loads(row[8] or "{}")
        except json.JSONDecodeError:
            draft_parse = {"status": "draft_parse", "confidence": "low", "review_reason": "invalid_json"}
        captures.append(
            {
                "id": row[0],
                "local_date": row[1],
                "source": row[2],
                "source_message_id": row[3],
                "source_channel_id": row[4],
                "source_channel_name": row[5],
                "logged_by": row[6],
                "raw_text": row[7],
                "draft_parse": draft_parse,
                "confidence": row[9],
                "review_reason": row[10],
                "review_status": row[11],
                "clarification_question": row[12],
                "created_at_utc": row[13],
            }
        )
    return captures


def agent_payload(day: str, captures: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "task": "review_work_captures",
        "day": day,
        "rules": {
            "timezone": "Africa/Casablanca",
            "work_window": "14:00-23:00",
            "draft_parse_is_hint_only": True,
            "allow_multiple_items_per_capture": True,
            "ask_only_if_unclear": True,
            "ignored_captures_require_reason": True,
            "do_not_create_memory": True,
        },
        "schema": {
            "confirmed": [
                {
                    "capture_id": "integer",
                    "items": [
                        {
                            "title": "short actionable work item",
                            "priority": "p0|p1|p2|p3",
                            "status": "open|waiting|blocked",
                            "project": "optional",
                            "area": "optional",
                            "due_date": "optional YYYY-MM-DD",
                            "scheduled_date": "optional YYYY-MM-DD",
                            "energy": "optional low|medium|high",
                            "effort_minutes": "optional integer",
                            "context": "optional",
                            "tags": ["optional strings"],
                            "note": "optional correction or split rationale",
                        }
                    ],
                }
            ],
            "ignored": [{"capture_id": "integer", "reason": "explicit reason"}],
            "questions": [{"capture_id": "integer", "question": "short clarification needed"}],
        },
        "captures": captures,
    }


def run_agent(config: Config, payload: dict[str, Any]) -> dict[str, Any] | None:
    if not config.agent_cmd or not payload.get("captures"):
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


def _question(capture_id: int, text: str) -> dict[str, Any]:
    return {"capture_id": capture_id, "question": text}


def _optional_date(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD") from exc


def _optional_int(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if parsed < 1:
        raise ValueError(f"{field} must be positive")
    return parsed


def _validate_item(raw: Any) -> WorkItemDraft:
    if not isinstance(raw, dict):
        raise ValueError("item must be an object")
    title = " ".join(str(raw.get("title") or "").split())
    if not title:
        raise ValueError("title is required")
    priority = str(raw.get("priority") or "p2").strip().lower()
    if priority not in WORK_PRIORITIES:
        raise ValueError(f"invalid priority: {priority!r}")
    status = str(raw.get("status") or "open").strip().lower()
    if status not in ALLOWED_CREATE_STATUSES:
        raise ValueError(f"invalid status for new work item: {status!r}")
    tags = raw.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, list):
        raise ValueError("tags must be a list")
    return WorkItemDraft(
        title=title,
        priority=priority,
        status=status,
        project=_optional_text(raw.get("project")),
        area=_optional_text(raw.get("area")),
        due_date=_optional_date(raw.get("due_date"), "due_date"),
        scheduled_date=_optional_date(raw.get("scheduled_date"), "scheduled_date"),
        energy=_optional_text(raw.get("energy")),
        effort_minutes=_optional_int(raw.get("effort_minutes"), "effort_minutes"),
        context=_optional_text(raw.get("context")),
        tags=tuple(" ".join(str(tag).split()) for tag in tags if " ".join(str(tag).split())),
        note=_optional_text(raw.get("note")),
    )


def _optional_text(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def apply_agent_result(
    agent_result: dict[str, Any],
    captures_by_id: dict[int, dict[str, Any]],
) -> tuple[list[tuple[int, tuple[WorkItemDraft, ...]]], list[tuple[int, str]], list[dict[str, Any]]]:
    if not isinstance(agent_result, dict):
        return [], [], [_question(capture_id, "Hermis work reviewer returned invalid output.") for capture_id in captures_by_id]

    confirmed: list[tuple[int, tuple[WorkItemDraft, ...]]] = []
    ignored: list[tuple[int, str]] = []
    questions: list[dict[str, Any]] = []

    for item in agent_result.get("questions") or []:
        try:
            capture_id = int(item["capture_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if capture_id not in captures_by_id:
            continue
        text = _optional_text(item.get("question")) or "Hermis needs clarification."
        questions.append(_question(capture_id, text))

    answered_ids = {int(item["capture_id"]) for item in questions if int(item["capture_id"]) in captures_by_id}

    for item in agent_result.get("ignored") or []:
        try:
            capture_id = int(item["capture_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if capture_id not in captures_by_id or capture_id in answered_ids:
            continue
        reason = _optional_text(item.get("reason"))
        if not reason:
            questions.append(_question(capture_id, "Hermis marked this ignored without an explicit reason."))
        else:
            ignored.append((capture_id, reason))
        answered_ids.add(capture_id)

    for item in agent_result.get("confirmed") or []:
        try:
            capture_id = int(item["capture_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if capture_id not in captures_by_id or capture_id in answered_ids:
            continue
        try:
            drafts = tuple(_validate_item(raw_item) for raw_item in item.get("items") or [])
            if not drafts:
                raise ValueError("confirmed captures require at least one item")
            confirmed.append((capture_id, drafts))
        except (TypeError, ValueError) as exc:
            questions.append(_question(capture_id, f"Hermis work output could not be applied: {exc}."))
        answered_ids.add(capture_id)

    for capture_id in captures_by_id:
        if capture_id not in answered_ids:
            questions.append(_question(capture_id, "Hermis returned no confirmation, ignore reason, or clarification."))

    return confirmed, ignored, questions


async def apply_resolutions(store: TrackerStore, confirmed, ignored, questions, dry_run: bool):
    applied_confirmed = []
    applied_ignored = []
    applied_questions = []
    for capture_id, drafts in confirmed:
        if dry_run:
            applied_confirmed.append({"capture_id": capture_id, "item_ids": [], "items": drafts})
            continue
        records = await store.confirm_work_capture(capture_id, drafts)
        if records:
            applied_confirmed.append(
                {"capture_id": capture_id, "item_ids": [record["id"] for record in records], "items": drafts}
            )
    for capture_id, reason in ignored:
        if dry_run:
            applied_ignored.append({"capture_id": capture_id, "reason": reason})
            continue
        if await store.ignore_work_capture(capture_id, reason):
            applied_ignored.append({"capture_id": capture_id, "reason": reason})
    for item in questions:
        if dry_run:
            applied_questions.append(item)
            continue
        if await store.ask_work_clarification(int(item["capture_id"]), item["question"]):
            applied_questions.append(item)
    return applied_confirmed, applied_ignored, applied_questions


def write_questions(config: Config, day: str, questions: list[dict[str, Any]]) -> Path | None:
    if not questions:
        return None
    path = config.root / "inbox" / "needs-answer" / f"{day}-work.md"
    lines = [f"# Work Clarifications {day}", ""]
    for item in questions:
        lines.append(f"- capture:{item['capture_id']}: {item['question']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def render_report(
    day: str,
    captures,
    confirmed,
    ignored,
    questions,
    agent_error: str | None,
    question_path: Path | None,
) -> str:
    lines = [
        f"# Work Parsing Review - {day}",
        "",
        "## Result",
        f"- Captures considered: {len(captures)}",
        f"- Captures confirmed: {len(confirmed)}",
        f"- Captures ignored: {len(ignored)}",
        f"- Clarifications needed: {len(questions)}",
    ]
    if agent_error:
        lines.append(f"- AI reviewer issue: {agent_error}")
    if question_path:
        lines.append(f"- Questions written: {question_path}")

    lines.extend(["", "## Confirmed"])
    if confirmed:
        for item in confirmed:
            ids = ", ".join(str(item_id) for item_id in item["item_ids"]) or "dry-run"
            lines.append(f"- capture:{item['capture_id']} -> work {ids} ({len(item['items'])} item(s))")
    else:
        lines.append("- none")

    lines.extend(["", "## Ignored"])
    if ignored:
        for item in ignored:
            lines.append(f"- capture:{item['capture_id']}: {item['reason']}")
    else:
        lines.append("- none")

    lines.extend(["", "## Questions"])
    if questions:
        for item in questions:
            lines.append(f"- capture:{item['capture_id']}: {item['question']}")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Policy",
            "- Work Discord input is review-first: raw capture and draft parse first, confirmed work item only after Hermis review.",
            "- Draft parse JSON is a hint only and must not be treated as final truth.",
            "- Ignored captures require explicit reasons.",
            "- Work window: 14:00-23:00 Africa/Casablanca.",
            "",
        ]
    )
    return "\n".join(lines)


async def main_async() -> int:
    args = parse_args()
    config = load_config()
    day = date.fromisoformat(args.day).isoformat()
    output = Path(args.output).expanduser() if args.output else config.root / "reports" / "work" / f"{day}-parsing-review.md"

    if not config.db_path.exists():
        raise SystemExit(f"tracker DB missing: {config.db_path}")

    store = TrackerStore(config.db_path, config.root)
    await store.init()
    with sqlite3.connect(f"file:{config.db_path}?mode=ro", uri=True) as con:
        captures = fetch_captures(con, day, args.all_open)

    payload = agent_payload(day, captures)
    captures_by_id = {int(capture["id"]): capture for capture in captures}
    agent_error = None
    agent_result = run_agent(config, payload)
    if agent_result and "error" not in agent_result:
        confirmed, ignored, questions = apply_agent_result(agent_result, captures_by_id)
    else:
        agent_error = (agent_result or {}).get("error") if agent_result else None
        confirmed = []
        ignored = []
        questions = [
            _question(
                int(capture["id"]),
                "Hermis work reviewer failed or was not configured; capture was left unconfirmed.",
            )
            for capture in captures
        ]

    applied_confirmed, applied_ignored, applied_questions = await apply_resolutions(
        store,
        confirmed,
        ignored,
        questions,
        args.dry_run,
    )
    question_path = write_questions(config, day, applied_questions)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_report(
            day,
            captures,
            applied_confirmed,
            applied_ignored,
            applied_questions,
            agent_error,
            question_path,
        ),
        encoding="utf-8",
    )
    print(f"wrote work parsing review: {output}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
