from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TrackerStore:
    def __init__(self, db_path: Path, lifeos_root: Path):
        self.db_path = Path(db_path)
        self.lifeos_root = Path(lifeos_root)
        self.prayer_dir = self.lifeos_root / "data" / "prayer"
        self.hydration_dir = self.lifeos_root / "data" / "hydration"

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.prayer_dir.mkdir(parents=True, exist_ok=True)
        self.hydration_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS prayer_schedule (
                    local_date TEXT PRIMARY KEY,
                    timings_json TEXT NOT NULL,
                    fetched_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS posted_reminders (
                    kind TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    reminder_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    posted_at_utc TEXT NOT NULL,
                    close_nudged_at_utc TEXT,
                    PRIMARY KEY (kind, local_date, reminder_id)
                );

                CREATE TABLE IF NOT EXISTS prayer_events (
                    local_date TEXT NOT NULL,
                    prayer_name TEXT NOT NULL,
                    window_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message_id INTEGER,
                    channel_id INTEGER,
                    logged_by INTEGER,
                    logged_at_utc TEXT NOT NULL,
                    window_end_utc TEXT,
                    PRIMARY KEY (local_date, prayer_name, window_id)
                );

                CREATE TABLE IF NOT EXISTS hydration_daily (
                    local_date TEXT PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hydration_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_date TEXT NOT NULL,
                    reminder_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    count_delta INTEGER NOT NULL,
                    note TEXT,
                    message_id INTEGER,
                    channel_id INTEGER,
                    logged_by INTEGER,
                    logged_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hydration_snoozes (
                    local_date TEXT PRIMARY KEY,
                    snooze_until_utc TEXT NOT NULL
                );
                """
            )
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    async def save_prayer_schedule(self, local_date: str, timings: dict[str, datetime]) -> None:
        payload = {name: value.isoformat() for name, value in timings.items()}
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO prayer_schedule (local_date, timings_json, fetched_at_utc)
                VALUES (?, ?, ?)
                ON CONFLICT(local_date) DO UPDATE SET
                    timings_json = excluded.timings_json,
                    fetched_at_utc = excluded.fetched_at_utc
                """,
                (local_date, json.dumps(payload, sort_keys=True), utc_now_iso()),
            )
            db.commit()

    async def get_prayer_schedule(self, local_date: str) -> dict[str, datetime] | None:
        with self._connect() as db:
            row = _fetchone(
                db,
                "SELECT timings_json FROM prayer_schedule WHERE local_date = ?",
                (local_date,),
            )
        if row is None:
            return None
        payload = json.loads(row[0])
        return {name: datetime.fromisoformat(value) for name, value in payload.items()}

    async def get_posted_reminder(
        self,
        kind: str,
        local_date: str,
        reminder_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = _fetchone(
                db,
                """
                SELECT message_id, channel_id, posted_at_utc, close_nudged_at_utc
                FROM posted_reminders
                WHERE kind = ? AND local_date = ? AND reminder_id = ?
                """,
                (kind, local_date, reminder_id),
            )
        if row is None:
            return None
        return {
            "message_id": row[0],
            "channel_id": row[1],
            "posted_at_utc": row[2],
            "close_nudged_at_utc": row[3],
        }

    async def save_posted_reminder(
        self,
        kind: str,
        local_date: str,
        reminder_id: str,
        message_id: int,
        channel_id: int,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO posted_reminders
                    (kind, local_date, reminder_id, message_id, channel_id, posted_at_utc)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (kind, local_date, reminder_id, message_id, channel_id, utc_now_iso()),
            )
            db.commit()

    async def mark_close_nudged(self, kind: str, local_date: str, reminder_id: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                UPDATE posted_reminders
                SET close_nudged_at_utc = ?
                WHERE kind = ? AND local_date = ? AND reminder_id = ?
                """,
                (utc_now_iso(), kind, local_date, reminder_id),
            )
            db.commit()

    async def has_prayer_log(self, local_date: str, prayer_name: str, window_id: str) -> bool:
        with self._connect() as db:
            row = _fetchone(
                db,
                """
                SELECT 1 FROM prayer_events
                WHERE local_date = ? AND prayer_name = ? AND window_id = ?
                """,
                (local_date, prayer_name, window_id),
            )
        return row is not None

    async def log_prayer(
        self,
        *,
        local_date: str,
        prayer_name: str,
        window_id: str,
        status: str,
        message_id: int | None,
        channel_id: int | None,
        logged_by: int | None,
        window_end_utc: datetime | None,
    ) -> None:
        logged_at_utc = utc_now_iso()
        window_end = window_end_utc.isoformat() if window_end_utc else None
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO prayer_events
                    (local_date, prayer_name, window_id, status, message_id, channel_id,
                     logged_by, logged_at_utc, window_end_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(local_date, prayer_name, window_id) DO UPDATE SET
                    status = excluded.status,
                    message_id = excluded.message_id,
                    channel_id = excluded.channel_id,
                    logged_by = excluded.logged_by,
                    logged_at_utc = excluded.logged_at_utc,
                    window_end_utc = excluded.window_end_utc
                """,
                (
                    local_date,
                    prayer_name,
                    window_id,
                    status,
                    message_id,
                    channel_id,
                    logged_by,
                    logged_at_utc,
                    window_end,
                ),
            )
            db.commit()
        record = {
            "event": "prayer_status",
            "local_date": local_date,
            "prayer_name": prayer_name,
            "window_id": window_id,
            "status": status,
            "message_id": message_id,
            "channel_id": channel_id,
            "logged_by": logged_by,
            "logged_at_utc": logged_at_utc,
            "window_end_utc": window_end,
        }
        await self._append_daily_logs(self.prayer_dir, local_date, record, _prayer_md_row(record))

    async def get_hydration_count(self, local_date: str) -> int:
        with self._connect() as db:
            row = _fetchone(
                db,
                "SELECT count FROM hydration_daily WHERE local_date = ?",
                (local_date,),
            )
        return int(row[0]) if row else 0

    async def log_hydration(
        self,
        *,
        local_date: str,
        reminder_id: str,
        action: str,
        count_delta: int,
        note: str,
        message_id: int | None,
        channel_id: int | None,
        logged_by: int | None,
    ) -> int:
        logged_at_utc = utc_now_iso()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO hydration_events
                    (local_date, reminder_id, action, count_delta, note, message_id,
                     channel_id, logged_by, logged_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    local_date,
                    reminder_id,
                    action,
                    count_delta,
                    note,
                    message_id,
                    channel_id,
                    logged_by,
                    logged_at_utc,
                ),
            )
            db.execute(
                """
                INSERT INTO hydration_daily (local_date, count, updated_at_utc)
                VALUES (?, ?, ?)
                ON CONFLICT(local_date) DO UPDATE SET
                    count = hydration_daily.count + excluded.count,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (local_date, count_delta, logged_at_utc),
            )
            db.commit()
            row = _fetchone(
                db,
                "SELECT count FROM hydration_daily WHERE local_date = ?",
                (local_date,),
            )
        new_count = int(row[0]) if row else 0
        record = {
            "event": "hydration",
            "local_date": local_date,
            "reminder_id": reminder_id,
            "action": action,
            "count_delta": count_delta,
            "note": note,
            "message_id": message_id,
            "channel_id": channel_id,
            "logged_by": logged_by,
            "logged_at_utc": logged_at_utc,
            "daily_count": new_count,
        }
        await self._append_daily_logs(
            self.hydration_dir,
            local_date,
            record,
            _hydration_md_row(record),
        )
        return new_count

    async def set_hydration_snooze(self, local_date: str, snooze_until_utc: datetime) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO hydration_snoozes (local_date, snooze_until_utc)
                VALUES (?, ?)
                ON CONFLICT(local_date) DO UPDATE SET
                    snooze_until_utc = excluded.snooze_until_utc
                """,
                (local_date, snooze_until_utc.astimezone(timezone.utc).isoformat()),
            )
            db.commit()

    async def get_hydration_snooze_until(self, local_date: str) -> datetime | None:
        with self._connect() as db:
            row = _fetchone(
                db,
                "SELECT snooze_until_utc FROM hydration_snoozes WHERE local_date = ?",
                (local_date,),
            )
        if row is None:
            return None
        return datetime.fromisoformat(row[0])

    async def _append_daily_logs(
        self,
        directory: Path,
        local_date: str,
        record: dict[str, Any],
        markdown_row: str,
    ) -> None:
        jsonl_path = directory / f"{local_date}.jsonl"
        md_path = directory / f"{local_date}.md"
        json_line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
        _append_text(jsonl_path, json_line)
        if not md_path.exists():
            _append_text(md_path, _md_header(directory.name, local_date))
        _append_text(md_path, markdown_row)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fetchone(db: sqlite3.Connection, query: str, params: tuple[Any, ...]):
    return db.execute(query, params).fetchone()


def _append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def _md_header(kind: str, local_date: str) -> str:
    title = "Prayer Log" if kind == "prayer" else "Hydration Log"
    if kind == "prayer":
        columns = "| Time (UTC) | Prayer | Status | Window | User |\n|---|---|---|---|---|\n"
    else:
        columns = "| Time (UTC) | Action | Delta | Total | Note | User |\n|---|---|---:|---:|---|---|\n"
    return f"# {title} {local_date}\n\n{columns}"


def _prayer_md_row(record: dict[str, Any]) -> str:
    logged_at = record["logged_at_utc"].replace("+00:00", "Z")
    return (
        f"| {logged_at} | {record['prayer_name']} | {record['status']} | "
        f"{record['window_id']} | {record['logged_by']} |\n"
    )


def _hydration_md_row(record: dict[str, Any]) -> str:
    logged_at = record["logged_at_utc"].replace("+00:00", "Z")
    note = str(record.get("note") or "").replace("|", "\\|")
    return (
        f"| {logged_at} | {record['action']} | {record['count_delta']} | "
        f"{record['daily_count']} | {note} | {record['logged_by']} |\n"
    )
