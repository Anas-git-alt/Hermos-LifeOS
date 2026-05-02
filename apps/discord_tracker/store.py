from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


class TrackerStore:
    def __init__(self, db_path: Path, lifeos_root: Path):
        self.db_path = Path(db_path)
        self.lifeos_root = Path(lifeos_root)
        self.prayer_dir = self.lifeos_root / "data" / "prayer"
        self.hydration_dir = self.lifeos_root / "data" / "hydration"
        self.finance_dir = self.lifeos_root / "data" / "finance"
        self.raw_capture_dir = self.lifeos_root / "raw" / "captures"

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.prayer_dir.mkdir(parents=True, exist_ok=True)
        self.hydration_dir.mkdir(parents=True, exist_ok=True)
        self.finance_dir.mkdir(parents=True, exist_ok=True)
        self.raw_capture_dir.mkdir(parents=True, exist_ok=True)
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

                CREATE TABLE IF NOT EXISTS hydration_reaction_events (
                    local_date TEXT NOT NULL,
                    reminder_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    logged_by INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    claimed_at_utc TEXT NOT NULL,
                    PRIMARY KEY (local_date, reminder_id, message_id, logged_by)
                );

                CREATE TABLE IF NOT EXISTS hydration_snoozes (
                    local_date TEXT PRIMARY KEY,
                    snooze_until_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS finance_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_date TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    amount_mad TEXT,
                    category TEXT NOT NULL,
                    merchant TEXT,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    review_reason TEXT,
                    source TEXT NOT NULL,
                    source_message_id INTEGER,
                    source_channel_id INTEGER,
                    logged_by INTEGER,
                    raw_text TEXT NOT NULL,
                    source_item_index INTEGER NOT NULL DEFAULT 0,
                    occurred_at_utc TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    voided_at_utc TEXT,
                    UNIQUE(source, source_message_id, source_item_index)
                );

                CREATE TABLE IF NOT EXISTS finance_recurring_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    category TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    cadence TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_transaction_id INTEGER,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    UNIQUE(name, currency, kind)
                );

                CREATE TABLE IF NOT EXISTS finance_savings_goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    target_amount TEXT,
                    target_currency TEXT,
                    current_amount TEXT NOT NULL,
                    current_currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_transaction_id INTEGER,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS finance_parse_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_date TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_message_id INTEGER,
                    source_channel_id INTEGER,
                    logged_by INTEGER,
                    raw_text TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    transaction_id INTEGER,
                    created_at_utc TEXT NOT NULL,
                    resolved_at_utc TEXT,
                    UNIQUE(source, source_message_id)
                );
                """
            )
            _ensure_finance_transaction_schema(db)
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
    ) -> bool:
        logged_at_utc = utc_now_iso()
        window_end = window_end_utc.isoformat() if window_end_utc else None
        with self._connect() as db:
            existing = _fetchone(
                db,
                """
                SELECT status, message_id, logged_by FROM prayer_events
                WHERE local_date = ? AND prayer_name = ? AND window_id = ?
                """,
                (local_date, prayer_name, window_id),
            )
            if existing == (status, message_id, logged_by):
                return False
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
        return True

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

    async def log_hydration_reaction(
        self,
        *,
        local_date: str,
        reminder_id: str,
        action: str,
        count_delta: int,
        note: str,
        message_id: int,
        channel_id: int | None,
        logged_by: int,
    ) -> tuple[int, bool]:
        logged_at_utc = utc_now_iso()
        with self._connect() as db:
            claimed = db.execute(
                """
                INSERT OR IGNORE INTO hydration_reaction_events
                    (local_date, reminder_id, message_id, logged_by, action, claimed_at_utc)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    local_date,
                    reminder_id,
                    message_id,
                    logged_by,
                    action,
                    logged_at_utc,
                ),
            )
            if claimed.rowcount == 0:
                row = _fetchone(
                    db,
                    "SELECT count FROM hydration_daily WHERE local_date = ?",
                    (local_date,),
                )
                return (int(row[0]) if row else 0), False

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
        return new_count, True

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

    async def log_finance_message(
        self,
        *,
        local_date: str,
        raw_text: str,
        parsed,
        message_id: int | None,
        channel_id: int | None,
        channel_name: str,
        logged_by: int | None,
        source: str = "discord",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as db:
            duplicate = _finance_duplicate(db, source, message_id)
            if duplicate:
                return {"status": "duplicate", "created": False, **duplicate}

            if parsed.status != "parsed" or not parsed.entries:
                review_id = _insert_finance_review(
                    db,
                    local_date=local_date,
                    source=source,
                    message_id=message_id,
                    channel_id=channel_id,
                    logged_by=logged_by,
                    raw_text=raw_text,
                    reason=parsed.review_reason or "needs_review",
                    now=now,
                )
                db.commit()
                record = {
                    "event": "finance_review",
                    "id": review_id,
                    "local_date": local_date,
                    "raw_text": raw_text,
                    "reason": parsed.review_reason or "needs_review",
                    "status": "open",
                    "source": source,
                    "source_message_id": message_id,
                    "source_channel_id": channel_id,
                    "logged_by": logged_by,
                    "created_at_utc": now,
                }
                await self._append_daily_logs(self.finance_dir, local_date, record, _finance_md_row(record))
                self._append_raw_finance_capture(
                    local_date,
                    now,
                    source,
                    channel_name,
                    message_id,
                    "finance_needs_review",
                    raw_text,
                )
                return {"status": "needs_review", "created": True, "review_id": review_id}

            transaction_ids = []
            records = []
            for source_item_index, entry in enumerate(parsed.entries):
                transaction_id = _insert_finance_transaction(
                    db,
                    local_date=local_date,
                    entry=entry,
                    raw_text=raw_text,
                    source=source,
                    message_id=message_id,
                    source_item_index=source_item_index,
                    channel_id=channel_id,
                    logged_by=logged_by,
                    now=now,
                )
                _upsert_finance_derivatives(db, transaction_id, entry, now)
                transaction_ids.append(transaction_id)
                records.append(
                    _finance_transaction_record(
                        transaction_id,
                        local_date,
                        entry,
                        raw_text,
                        source_item_index,
                        "finance_transaction",
                        "parsed",
                        source,
                        message_id,
                        channel_id,
                        logged_by,
                        now,
                    )
                )
            db.commit()

        for record in records:
            await self._append_daily_logs(self.finance_dir, local_date, record, _finance_md_row(record))
        self._append_raw_finance_capture(
            local_date,
            now,
            source,
            channel_name,
            message_id,
            "finance_structured",
            raw_text,
        )
        return {"status": "parsed", "created": True, "transaction_ids": transaction_ids}

    async def get_finance_day_summary(self, local_date: str) -> dict[str, Any]:
        with self._connect() as db:
            return _finance_summary(db, "local_date = ?", (local_date,), local_date)

    async def get_finance_month_summary(self, month: str) -> dict[str, Any]:
        with self._connect() as db:
            return _finance_summary(db, "local_date LIKE ?", (f"{month}-%",), month)

    async def list_finance_reviews(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT id, local_date, reason, raw_text, created_at_utc
                FROM finance_parse_reviews
                WHERE status = 'open'
                ORDER BY created_at_utc DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": row[0],
                "local_date": row[1],
                "reason": row[2],
                "raw_text": row[3],
                "created_at_utc": row[4],
            }
            for row in rows
        ]

    async def edit_finance_transaction(self, transaction_id: int, entry) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self._connect() as db:
            row = _fetchone(
                db,
                """
                SELECT local_date, raw_text, source, source_message_id, source_item_index,
                       source_channel_id, logged_by
                FROM finance_transactions
                WHERE id = ? AND status != 'void'
                """,
                (transaction_id,),
            )
            if row is None:
                return None
            db.execute(
                """
                UPDATE finance_transactions
                SET kind = ?, amount = ?, currency = ?, amount_mad = ?, category = ?,
                    merchant = ?, description = ?, status = 'parsed', confidence = ?,
                    review_reason = ?, updated_at_utc = ?
                WHERE id = ?
                """,
                (
                    entry.kind,
                    _decimal_text(entry.amount),
                    entry.currency,
                    _decimal_text(entry.amount_mad) if entry.amount_mad is not None else None,
                    entry.category,
                    entry.merchant,
                    entry.description,
                    entry.confidence,
                    entry.review_reason,
                    now,
                    transaction_id,
                ),
            )
            _upsert_finance_derivatives(db, transaction_id, entry, now)
            db.commit()

        local_date, raw_text, source, message_id, source_item_index, channel_id, logged_by = row
        record = _finance_transaction_record(
            transaction_id,
            local_date,
            entry,
            raw_text,
            source_item_index,
            "finance_edit",
            "parsed",
            source,
            message_id,
            channel_id,
            logged_by,
            now,
        )
        await self._append_daily_logs(self.finance_dir, local_date, record, _finance_md_row(record))
        return record

    async def resolve_finance_review(self, review_id: int, entries) -> list[dict[str, Any]] | None:
        now = utc_now_iso()
        entry_list = _entry_list(entries)
        with self._connect() as db:
            row = _fetchone(
                db,
                """
                SELECT local_date, source, source_message_id, source_channel_id, logged_by, raw_text
                FROM finance_parse_reviews
                WHERE id = ? AND status = 'open'
                """,
                (review_id,),
            )
            if row is None:
                return None
            local_date, source, message_id, channel_id, logged_by, raw_text = row
            source_item_start = _next_finance_source_item_index(db, source, message_id)
            records = []
            transaction_ids = []
            for offset, entry in enumerate(entry_list):
                source_item_index = source_item_start + offset
                transaction_id = _insert_finance_transaction(
                    db,
                    local_date=local_date,
                    entry=entry,
                    raw_text=raw_text,
                    source=source,
                    message_id=message_id,
                    source_item_index=source_item_index,
                    channel_id=channel_id,
                    logged_by=logged_by,
                    now=now,
                )
                _upsert_finance_derivatives(db, transaction_id, entry, now)
                transaction_ids.append(transaction_id)
                records.append(
                    _finance_transaction_record(
                        transaction_id,
                        local_date,
                        entry,
                        raw_text,
                        source_item_index,
                        "finance_review_resolved",
                        "parsed",
                        source,
                        message_id,
                        channel_id,
                        logged_by,
                        now,
                    )
                )
            db.execute(
                """
                UPDATE finance_parse_reviews
                SET status = 'resolved', transaction_id = ?, resolved_at_utc = ?
                WHERE id = ?
                """,
                (transaction_ids[0] if transaction_ids else None, now, review_id),
            )
            db.commit()

        for record in records:
            await self._append_daily_logs(self.finance_dir, local_date, record, _finance_md_row(record))
        return records

    async def void_finance_item(self, item_id: int) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self._connect() as db:
            tx = _fetchone(
                db,
                """
                SELECT local_date, kind, amount, currency, amount_mad, category, merchant,
                       description, confidence, review_reason, raw_text, source,
                       source_message_id, source_item_index, source_channel_id, logged_by
                FROM finance_transactions
                WHERE id = ? AND status != 'void'
                """,
                (item_id,),
            )
            if tx is not None:
                db.execute(
                    """
                    UPDATE finance_transactions
                    SET status = 'void', voided_at_utc = ?, updated_at_utc = ?
                    WHERE id = ?
                    """,
                    (now, now, item_id),
                )
                db.commit()
                record = _finance_void_record(item_id, tx, now)
                await self._append_daily_logs(self.finance_dir, record["local_date"], record, _finance_md_row(record))
                return {"kind": "transaction", "id": item_id}

            review = _fetchone(
                db,
                """
                SELECT local_date, raw_text, reason, source, source_message_id,
                       source_channel_id, logged_by
                FROM finance_parse_reviews
                WHERE id = ? AND status = 'open'
                """,
                (item_id,),
            )
            if review is None:
                return None
            db.execute(
                """
                UPDATE finance_parse_reviews
                SET status = 'void', resolved_at_utc = ?
                WHERE id = ?
                """,
                (now, item_id),
            )
            db.commit()
            local_date, raw_text, reason, source, message_id, channel_id, logged_by = review
            record = {
                "event": "finance_review_void",
                "id": item_id,
                "local_date": local_date,
                "raw_text": raw_text,
                "reason": reason,
                "status": "void",
                "source": source,
                "source_message_id": message_id,
                "source_channel_id": channel_id,
                "logged_by": logged_by,
                "created_at_utc": now,
            }
            await self._append_daily_logs(self.finance_dir, local_date, record, _finance_md_row(record))
            return {"kind": "review", "id": item_id}

    def _append_raw_finance_capture(
        self,
        local_date: str,
        timestamp_utc: str,
        source: str,
        channel_name: str,
        message_id: int | None,
        status: str,
        raw_text: str,
    ) -> None:
        capture_id = f"finance-{source}-{message_id or timestamp_utc.replace(':', '').replace('+', '')}"
        block = (
            "\n---\n\n"
            f"capture_id: {capture_id}\n"
            f"timestamp: {timestamp_utc}\n"
            f"source: {source}:#{channel_name}\n"
            "classification: finance\n"
            f"status: {status}\n"
            f"processed: {'true' if status == 'finance_structured' else 'false'}\n\n"
            f"{raw_text}\n"
        )
        _append_text(self.raw_capture_dir / f"{local_date}.md", block)

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


def _ensure_finance_transaction_schema(db: sqlite3.Connection) -> None:
    row = _fetchone(
        db,
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'finance_transactions'",
        (),
    )
    if row is None:
        return
    table_sql = row[0] or ""
    if (
        "source_item_index" in table_sql
        and "UNIQUE(source, source_message_id, source_item_index)" in table_sql
    ):
        return

    db.executescript(
        """
        PRAGMA foreign_keys = OFF;
        ALTER TABLE finance_transactions RENAME TO finance_transactions_old;
        CREATE TABLE finance_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_date TEXT NOT NULL,
            kind TEXT NOT NULL,
            amount TEXT NOT NULL,
            currency TEXT NOT NULL,
            amount_mad TEXT,
            category TEXT NOT NULL,
            merchant TEXT,
            description TEXT NOT NULL,
            status TEXT NOT NULL,
            confidence TEXT NOT NULL,
            review_reason TEXT,
            source TEXT NOT NULL,
            source_message_id INTEGER,
            source_channel_id INTEGER,
            logged_by INTEGER,
            raw_text TEXT NOT NULL,
            source_item_index INTEGER NOT NULL DEFAULT 0,
            occurred_at_utc TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            voided_at_utc TEXT,
            UNIQUE(source, source_message_id, source_item_index)
        );
        INSERT INTO finance_transactions
            (id, local_date, kind, amount, currency, amount_mad, category, merchant,
             description, status, confidence, review_reason, source, source_message_id,
             source_channel_id, logged_by, raw_text, source_item_index, occurred_at_utc,
             created_at_utc, updated_at_utc, voided_at_utc)
        SELECT
            id, local_date, kind, amount, currency, amount_mad, category, merchant,
            description, status, confidence, review_reason, source, source_message_id,
            source_channel_id, logged_by, raw_text, 0, occurred_at_utc,
            created_at_utc, updated_at_utc, voided_at_utc
        FROM finance_transactions_old;
        DROP TABLE finance_transactions_old;
        PRAGMA foreign_keys = ON;
        """
    )


def _finance_duplicate(db: sqlite3.Connection, source: str, message_id: int | None) -> dict[str, Any] | None:
    if message_id is None:
        return None
    rows = db.execute(
        """
        SELECT id, status FROM finance_transactions
        WHERE source = ? AND source_message_id = ?
          AND status != 'void'
        ORDER BY source_item_index, id
        """,
        (source, message_id),
    ).fetchall()
    if rows:
        return {
            "kind": "transaction",
            "transaction_ids": [row[0] for row in rows],
            "item_status": rows[0][1],
        }
    row = _fetchone(
        db,
        """
        SELECT id, status FROM finance_parse_reviews
        WHERE source = ? AND source_message_id = ?
        """,
        (source, message_id),
    )
    if row is not None:
        return {"kind": "review", "review_id": row[0], "item_status": row[1]}
    return None


def _insert_finance_review(
    db: sqlite3.Connection,
    *,
    local_date: str,
    source: str,
    message_id: int | None,
    channel_id: int | None,
    logged_by: int | None,
    raw_text: str,
    reason: str,
    now: str,
) -> int:
    cursor = db.execute(
        """
        INSERT INTO finance_parse_reviews
            (local_date, source, source_message_id, source_channel_id, logged_by,
             raw_text, reason, status, created_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (local_date, source, message_id, channel_id, logged_by, raw_text, reason, now),
    )
    return int(cursor.lastrowid)


def _insert_finance_transaction(
    db: sqlite3.Connection,
    *,
    local_date: str,
    entry,
    raw_text: str,
    source: str,
    message_id: int | None,
    source_item_index: int,
    channel_id: int | None,
    logged_by: int | None,
    now: str,
) -> int:
    cursor = db.execute(
        """
        INSERT INTO finance_transactions
            (local_date, kind, amount, currency, amount_mad, category, merchant,
             description, status, confidence, review_reason, source, source_message_id,
             source_channel_id, logged_by, raw_text, source_item_index, occurred_at_utc,
             created_at_utc, updated_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'parsed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            local_date,
            entry.kind,
            _decimal_text(entry.amount),
            entry.currency,
            _decimal_text(entry.amount_mad) if entry.amount_mad is not None else None,
            entry.category,
            entry.merchant,
            entry.description,
            entry.confidence,
            entry.review_reason,
            source,
            message_id,
            channel_id,
            logged_by,
            raw_text,
            source_item_index,
            now,
            now,
            now,
        ),
    )
    return int(cursor.lastrowid)


def _next_finance_source_item_index(
    db: sqlite3.Connection,
    source: str,
    message_id: int | None,
) -> int:
    if message_id is None:
        return 0
    row = _fetchone(
        db,
        """
        SELECT COALESCE(MAX(source_item_index), -1)
        FROM finance_transactions
        WHERE source = ? AND source_message_id = ?
        """,
        (source, message_id),
    )
    return int(row[0]) + 1 if row else 0


def _upsert_finance_derivatives(db: sqlite3.Connection, transaction_id: int, entry, now: str) -> None:
    if entry.kind in {"bill", "subscription"}:
        name = entry.merchant or entry.description
        db.execute(
            """
            INSERT INTO finance_recurring_items
                (name, kind, category, amount, currency, cadence, status,
                 source_transaction_id, created_at_utc, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, 'monthly', 'active', ?, ?, ?)
            ON CONFLICT(name, currency, kind) DO UPDATE SET
                category = excluded.category,
                amount = excluded.amount,
                status = 'active',
                source_transaction_id = excluded.source_transaction_id,
                updated_at_utc = excluded.updated_at_utc
            """,
            (
                name,
                entry.kind,
                entry.category,
                _decimal_text(entry.amount),
                entry.currency,
                transaction_id,
                now,
                now,
            ),
        )

    if entry.kind in {"savings_contribution", "savings_goal"}:
        name = entry.merchant or entry.description or "savings"
        existing = _fetchone(
            db,
            """
            SELECT current_amount, current_currency, target_amount, target_currency
            FROM finance_savings_goals
            WHERE name = ?
            """,
            (name,),
        )
        if existing is None:
            current_amount = entry.amount if entry.kind == "savings_contribution" else Decimal("0")
            target_amount = entry.amount if entry.kind == "savings_goal" else None
            db.execute(
                """
                INSERT INTO finance_savings_goals
                    (name, target_amount, target_currency, current_amount, current_currency,
                     status, source_transaction_id, created_at_utc, updated_at_utc)
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    name,
                    _decimal_text(target_amount) if target_amount is not None else None,
                    entry.currency if target_amount is not None else None,
                    _decimal_text(current_amount),
                    entry.currency,
                    transaction_id,
                    now,
                    now,
                ),
            )
            return

        current_amount, current_currency, target_amount, target_currency = existing
        if current_currency == entry.currency and entry.kind == "savings_contribution":
            next_current = Decimal(current_amount) + entry.amount
        else:
            next_current = Decimal(current_amount)
        next_target = _decimal_text(entry.amount) if entry.kind == "savings_goal" else target_amount
        next_target_currency = entry.currency if entry.kind == "savings_goal" else target_currency
        db.execute(
            """
            UPDATE finance_savings_goals
            SET target_amount = ?, target_currency = ?, current_amount = ?,
                source_transaction_id = ?, updated_at_utc = ?
            WHERE name = ?
            """,
            (next_target, next_target_currency, _decimal_text(next_current), transaction_id, now, name),
        )


def _finance_summary(
    db: sqlite3.Connection,
    where_clause: str,
    params: tuple[Any, ...],
    label: str,
) -> dict[str, Any]:
    rows = db.execute(
        f"""
        SELECT id, kind, amount, currency, amount_mad, category, description, local_date
        FROM finance_transactions
        WHERE {where_clause} AND status = 'parsed'
        ORDER BY local_date, id
        """,
        params,
    ).fetchall()
    review_count = db.execute(
        f"""
        SELECT COUNT(*)
        FROM finance_parse_reviews
        WHERE {where_clause} AND status = 'open'
        """,
        params,
    ).fetchone()[0]

    expense_mad = Decimal("0")
    income_mad = Decimal("0")
    savings_mad = Decimal("0")
    transfer_mad = Decimal("0")
    by_category: dict[str, Decimal] = {}
    non_mad: list[dict[str, Any]] = []

    for tx_id, kind, amount, currency, amount_mad, category, description, local_date in rows:
        if amount_mad is None:
            non_mad.append(
                {
                    "id": tx_id,
                    "local_date": local_date,
                    "kind": kind,
                    "amount": amount,
                    "currency": currency,
                    "category": category,
                    "description": description,
                }
            )
            continue
        value = Decimal(amount_mad)
        if kind in {"expense", "bill", "subscription"}:
            expense_mad += value
            by_category[category] = by_category.get(category, Decimal("0")) + value
        elif kind == "income":
            income_mad += value
        elif kind == "savings_contribution":
            savings_mad += value
        elif kind == "transfer":
            transfer_mad += value

    return {
        "label": label,
        "transaction_count": len(rows),
        "expense_mad": _decimal_text(expense_mad),
        "income_mad": _decimal_text(income_mad),
        "savings_mad": _decimal_text(savings_mad),
        "transfer_mad": _decimal_text(transfer_mad),
        "by_category": {key: _decimal_text(value) for key, value in sorted(by_category.items())},
        "non_mad": non_mad,
        "needs_review_count": int(review_count),
    }


def _finance_transaction_record(
    transaction_id: int,
    local_date: str,
    entry,
    raw_text: str,
    source_item_index: int,
    event: str,
    status: str,
    source: str,
    message_id: int | None,
    channel_id: int | None,
    logged_by: int | None,
    now: str,
) -> dict[str, Any]:
    return {
        "event": event,
        "id": transaction_id,
        "local_date": local_date,
        "kind": entry.kind,
        "amount": _decimal_text(entry.amount),
        "currency": entry.currency,
        "amount_mad": _decimal_text(entry.amount_mad) if entry.amount_mad is not None else None,
        "category": entry.category,
        "merchant": entry.merchant,
        "description": entry.description,
        "status": status,
        "confidence": entry.confidence,
        "review_reason": entry.review_reason,
        "raw_text": raw_text,
        "source": source,
        "source_message_id": message_id,
        "source_item_index": source_item_index,
        "source_channel_id": channel_id,
        "logged_by": logged_by,
        "created_at_utc": now,
    }


def _finance_void_record(transaction_id: int, row: tuple[Any, ...], now: str) -> dict[str, Any]:
    (
        local_date,
        kind,
        amount,
        currency,
        amount_mad,
        category,
        merchant,
        description,
        confidence,
        review_reason,
        raw_text,
        source,
        message_id,
        source_item_index,
        channel_id,
        logged_by,
    ) = row
    return {
        "event": "finance_void",
        "id": transaction_id,
        "local_date": local_date,
        "kind": kind,
        "amount": amount,
        "currency": currency,
        "amount_mad": amount_mad,
        "category": category,
        "merchant": merchant,
        "description": description,
        "status": "void",
        "confidence": confidence,
        "review_reason": review_reason,
        "raw_text": raw_text,
        "source": source,
        "source_message_id": message_id,
        "source_item_index": source_item_index,
        "source_channel_id": channel_id,
        "logged_by": logged_by,
        "created_at_utc": now,
    }


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text == "-0" else text


def _entry_list(entries) -> list:
    if isinstance(entries, (tuple, list)):
        return list(entries)
    return [entries]


def _append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def _md_header(kind: str, local_date: str) -> str:
    if kind == "prayer":
        title = "Prayer Log"
        columns = "| Time (UTC) | Prayer | Status | Window | User |\n|---|---|---|---|---|\n"
    elif kind == "finance":
        title = "Finance Log"
        columns = "| Time (UTC) | Event | ID | Kind | Amount | Category | Description | Status |\n|---|---|---:|---|---:|---|---|---|\n"
    else:
        title = "Hydration Log"
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


def _finance_md_row(record: dict[str, Any]) -> str:
    logged_at = str(record.get("created_at_utc") or utc_now_iso()).replace("+00:00", "Z")
    amount = record.get("amount")
    currency = record.get("currency") or ""
    if amount:
        amount_text = f"{amount} {currency}".strip()
    else:
        amount_text = ""
    description = str(record.get("description") or record.get("raw_text") or "").replace("|", "\\|")
    return (
        f"| {logged_at} | {record.get('event')} | {record.get('id')} | "
        f"{record.get('kind') or ''} | {amount_text} | {record.get('category') or ''} | "
        f"{description} | {record.get('status')} |\n"
    )
