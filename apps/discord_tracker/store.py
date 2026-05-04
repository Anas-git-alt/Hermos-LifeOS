from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from review_automation import (
    LOW_RISK_REVERSIBLE,
    REQUIRES_APPROVAL,
    ReviewPrioritizer,
    SENSITIVE_REVIEW,
    is_sensitive_review,
)


class TrackerStore:
    def __init__(self, db_path: Path, lifeos_root: Path):
        self.db_path = Path(db_path)
        self.lifeos_root = Path(lifeos_root)
        self.prayer_dir = self.lifeos_root / "data" / "prayer"
        self.hydration_dir = self.lifeos_root / "data" / "hydration"
        self.finance_dir = self.lifeos_root / "data" / "finance"
        self.work_dir = self.lifeos_root / "data" / "work"
        self.review_dir = self.lifeos_root / "data" / "review"
        self.work_report_dir = self.lifeos_root / "reports" / "work"
        self.state_dir = self.lifeos_root / "state"
        self.raw_capture_dir = self.lifeos_root / "raw" / "captures"

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.prayer_dir.mkdir(parents=True, exist_ok=True)
        self.hydration_dir.mkdir(parents=True, exist_ok=True)
        self.finance_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.review_dir.mkdir(parents=True, exist_ok=True)
        self.work_report_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
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

                CREATE TABLE IF NOT EXISTS work_captures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_date TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_message_id INTEGER,
                    source_channel_id INTEGER,
                    source_channel_name TEXT,
                    logged_by INTEGER,
                    raw_text TEXT NOT NULL,
                    draft_parse_json TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    review_reason TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    clarification_question TEXT,
                    ignore_reason TEXT,
                    created_at_utc TEXT NOT NULL,
                    reviewed_at_utc TEXT,
                    UNIQUE(source, source_message_id)
                );

                CREATE TABLE IF NOT EXISTS work_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    capture_id INTEGER,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    project TEXT,
                    area TEXT,
                    due_date TEXT,
                    due_at TEXT,
                    scheduled_date TEXT,
                    scheduled_at TEXT,
                    energy TEXT,
                    effort_minutes INTEGER,
                    context TEXT,
                    tags_json TEXT NOT NULL,
                    note TEXT,
                    next_followup_at TEXT,
                    followup_cadence_hours INTEGER,
                    snoozed_until_utc TEXT,
                    source TEXT NOT NULL,
                    source_message_id INTEGER,
                    source_channel_id INTEGER,
                    logged_by INTEGER,
                    raw_text TEXT NOT NULL,
                    source_item_index INTEGER NOT NULL DEFAULT 0,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    completed_at_utc TEXT,
                    cancelled_at_utc TEXT,
                    UNIQUE(capture_id, source_item_index)
                );

                CREATE TABLE IF NOT EXISTS work_item_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER,
                    capture_id INTEGER,
                    event TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS work_automation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    item_id INTEGER,
                    capture_id INTEGER,
                    local_date TEXT NOT NULL,
                    reminder_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message_id INTEGER,
                    channel_id INTEGER,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    sent_at_utc TEXT,
                    UNIQUE(kind, local_date, reminder_id)
                );

                CREATE TABLE IF NOT EXISTS work_blocker_prompts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL,
                    local_date TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message_id INTEGER,
                    created_at_utc TEXT NOT NULL,
                    resolved_at_utc TEXT,
                    UNIQUE(item_id, local_date, reason)
                );

                CREATE TABLE IF NOT EXISTS work_ai_suggestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    suggestion_kind TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id INTEGER,
                    local_date TEXT NOT NULL,
                    prompt_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    review_reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reviewer_note TEXT,
                    supersedes_suggestion_id INTEGER,
                    created_at_utc TEXT NOT NULL,
                    reviewed_at_utc TEXT
                );

                CREATE TABLE IF NOT EXISTS review_items (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    source_path TEXT,
                    source_record_id TEXT,
                    source_kind TEXT,
                    ai_interpretation_json TEXT NOT NULL DEFAULT '{}',
                    ai_validation_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    confidence TEXT,
                    missing_context_json TEXT NOT NULL DEFAULT '[]',
                    priority TEXT,
                    surface_count INTEGER NOT NULL DEFAULT 0,
                    last_surface_at TEXT,
                    automation_policy TEXT,
                    auto_process_reason TEXT,
                    discord_channel_id INTEGER,
                    discord_message_id INTEGER,
                    discord_thread_id INTEGER,
                    parent_discord_message_id INTEGER,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    expires_at_utc TEXT
                );

                CREATE TABLE IF NOT EXISTS discord_message_bindings (
                    discord_message_id INTEGER NOT NULL,
                    discord_channel_id INTEGER NOT NULL,
                    discord_thread_id INTEGER,
                    review_item_id TEXT NOT NULL,
                    source_kind TEXT,
                    source_id TEXT,
                    source_path TEXT,
                    action_on_reply TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (discord_channel_id, discord_message_id)
                );

                CREATE INDEX IF NOT EXISTS idx_discord_message_bindings_message
                    ON discord_message_bindings(discord_message_id);

                CREATE TABLE IF NOT EXISTS review_item_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_item_id TEXT,
                    event TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS report_publications (
                    kind TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER,
                    created_at_utc TEXT NOT NULL,
                    PRIMARY KEY (kind, local_date, channel_id)
                );
                """
            )
            _ensure_finance_transaction_schema(db)
            _ensure_work_schema(db)
            _ensure_review_schema(db)
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    async def create_review_item(
        self,
        *,
        kind: str,
        title: str,
        body: str,
        source_path: str | None = None,
        source_record_id: str | int | None = None,
        source_kind: str | None = None,
        ai_interpretation: dict[str, Any] | None = None,
        ai_validation: dict[str, Any] | None = None,
        status: str = "pending",
        confidence: str | None = None,
        missing_context: list[str] | tuple[str, ...] | str | None = None,
        expires_at_utc: str | None = None,
        review_item_id: str | None = None,
        priority: str | None = None,
        automation_policy: str | None = None,
        auto_process_reason: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        source_record_text = str(source_record_id) if source_record_id is not None else None
        if review_item_id:
            item_id = review_item_id
        elif source_path or source_record_text:
            item_id = review_item_id_for_source(kind, source_path, source_record_text)
        else:
            inline_source = hashlib.sha1(f"{title}|{body}".encode("utf-8")).hexdigest()[:12]
            item_id = review_item_id_for_source(kind, "inline", inline_source)
        clean_status = _normalize_review_status(status)
        expires = expires_at_utc or _default_review_expiry()
        missing = _json_list(missing_context)
        interpretation_json = json.dumps(ai_interpretation or {}, sort_keys=True, ensure_ascii=False)
        validation_json = json.dumps(ai_validation or {}, sort_keys=True, ensure_ascii=False)
        policy = automation_policy or _default_review_automation_policy(
            {
                "kind": kind,
                "title": title,
                "body": body,
                "source_kind": source_kind,
                "source_path": source_path,
                "missing_context": missing,
            }
        )
        derived_priority = priority or ReviewPrioritizer().compute_priority(
            {
                "kind": kind,
                "title": title,
                "body": body,
                "source_kind": source_kind,
                "source_path": source_path,
                "status": clean_status,
                "confidence": confidence,
                "missing_context": missing,
                "expires_at_utc": expires,
                "automation_policy": policy,
                "created_at_utc": now,
            }
        )
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            existing = db.execute("SELECT * FROM review_items WHERE id = ?", (item_id,)).fetchone()
            if existing is None:
                db.execute(
                    """
                    INSERT INTO review_items
                        (id, kind, title, body, source_path, source_record_id, source_kind,
                         ai_interpretation_json, ai_validation_json, status, confidence,
                         missing_context_json, priority, automation_policy, auto_process_reason,
                         created_at_utc, updated_at_utc, expires_at_utc)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        kind,
                        title,
                        body,
                        source_path,
                        source_record_text,
                        source_kind,
                        interpretation_json,
                        validation_json,
                        clean_status,
                        confidence,
                        json.dumps(missing, sort_keys=True, ensure_ascii=False),
                        derived_priority,
                        policy,
                        auto_process_reason,
                        now,
                        now,
                        expires,
                    ),
                )
                _insert_review_event(
                    db,
                    item_id,
                    "created",
                    {
                        "id": item_id,
                        "kind": kind,
                        "title": title,
                        "source_kind": source_kind,
                        "source_record_id": source_record_text,
                        "status": clean_status,
                    },
                    now,
                )
            else:
                next_status = existing["status"]
                if next_status not in {"approved", "rejected", "auto_processed"}:
                    next_status = clean_status
                db.execute(
                    """
                    UPDATE review_items
                    SET kind = ?, title = ?, body = ?, source_path = ?,
                        source_record_id = ?, source_kind = ?,
                        ai_interpretation_json = ?,
                        ai_validation_json = ?,
                        status = ?,
                        confidence = COALESCE(?, confidence),
                        missing_context_json = ?,
                        priority = COALESCE(?, priority),
                        automation_policy = COALESCE(?, automation_policy),
                        auto_process_reason = COALESCE(?, auto_process_reason),
                        updated_at_utc = ?,
                        expires_at_utc = COALESCE(?, expires_at_utc)
                    WHERE id = ?
                    """,
                    (
                        kind,
                        title,
                        body,
                        source_path,
                        source_record_text,
                        source_kind,
                        interpretation_json,
                        validation_json,
                        next_status,
                        confidence,
                        json.dumps(missing, sort_keys=True, ensure_ascii=False),
                        derived_priority,
                        policy,
                        auto_process_reason,
                        now,
                        expires,
                        item_id,
                    ),
                )
                _insert_review_event(
                    db,
                    item_id,
                    "refreshed",
                    {"id": item_id, "status": next_status, "source_kind": source_kind},
                    now,
                )
            db.commit()
        await self._append_review_log(
            {
                "event": "review_item_upsert",
                "id": item_id,
                "kind": kind,
                "title": title,
                "status": clean_status,
                "source_kind": source_kind,
                "source_record_id": source_record_text,
                "created_at_utc": now,
            }
        )
        await self.write_review_state_snapshot()
        item = await self.get_review_item(item_id)
        if item is None:
            raise RuntimeError(f"review item not found after create: {item_id}")
        return item

    async def get_review_item(self, review_item_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM review_items WHERE id = ?", (review_item_id,)).fetchone()
        return _review_item_row(row) if row else None

    async def list_review_items(
        self,
        statuses: tuple[str, ...] | list[str] = ("pending", "needs_clarification", "expired"),
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clean = tuple(_normalize_review_status(status) for status in statuses)
        placeholders = ", ".join("?" for _ in clean)
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"""
                SELECT * FROM review_items
                WHERE status IN ({placeholders})
                ORDER BY
                    CASE priority WHEN 'urgent' THEN 0 WHEN 'normal' THEN 1 WHEN 'low' THEN 2 ELSE 1 END,
                    CASE status WHEN 'needs_clarification' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                    COALESCE(expires_at_utc, '9999-12-31T00:00:00+00:00') ASC,
                    COALESCE(surface_count, 0) ASC,
                    created_at_utc ASC
                LIMIT ?
                """,
                (*clean, limit),
            ).fetchall()
        return [_review_item_row(row) for row in rows]

    async def set_review_item_status(
        self,
        review_item_id: str,
        status: str,
        *,
        ai_interpretation: dict[str, Any] | None = None,
        ai_validation: dict[str, Any] | None = None,
        confidence: str | None = None,
        missing_context: list[str] | tuple[str, ...] | str | None = None,
        note: str | None = None,
    ) -> dict[str, Any] | None:
        now = utc_now_iso()
        clean_status = _normalize_review_status(status)
        assignments = ["status = ?", "updated_at_utc = ?"]
        values: list[Any] = [clean_status, now]
        if ai_interpretation is not None:
            assignments.append("ai_interpretation_json = ?")
            values.append(json.dumps(ai_interpretation, sort_keys=True, ensure_ascii=False))
        if ai_validation is not None:
            assignments.append("ai_validation_json = ?")
            values.append(json.dumps(ai_validation, sort_keys=True, ensure_ascii=False))
        if confidence is not None:
            assignments.append("confidence = ?")
            values.append(confidence)
        if missing_context is not None:
            assignments.append("missing_context_json = ?")
            values.append(json.dumps(_json_list(missing_context), sort_keys=True, ensure_ascii=False))
        values.append(review_item_id)
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            cursor = db.execute(
                f"UPDATE review_items SET {', '.join(assignments)} WHERE id = ?",
                tuple(values),
            )
            if cursor.rowcount == 0:
                db.commit()
                return None
            _insert_review_event(
                db,
                review_item_id,
                f"status:{clean_status}",
                {"id": review_item_id, "status": clean_status, "note": note},
                now,
            )
            db.commit()
        await self._append_review_log(
            {
                "event": "review_item_status",
                "id": review_item_id,
                "status": clean_status,
                "note": note,
                "created_at_utc": now,
            }
        )
        await self.write_review_state_snapshot()
        return await self.get_review_item(review_item_id)

    async def update_review_item_metadata(
        self,
        review_item_id: str,
        *,
        priority: str | None = None,
        automation_policy: str | None = None,
        auto_process_reason: str | None = None,
    ) -> dict[str, Any] | None:
        assignments = ["updated_at_utc = ?"]
        now = utc_now_iso()
        values: list[Any] = [now]
        if priority is not None:
            assignments.append("priority = ?")
            values.append(priority)
        if automation_policy is not None:
            assignments.append("automation_policy = ?")
            values.append(automation_policy)
        if auto_process_reason is not None:
            assignments.append("auto_process_reason = ?")
            values.append(auto_process_reason)
        values.append(review_item_id)
        with self._connect() as db:
            cursor = db.execute(
                f"UPDATE review_items SET {', '.join(assignments)} WHERE id = ?",
                tuple(values),
            )
            if cursor.rowcount == 0:
                db.commit()
                return None
            _insert_review_event(
                db,
                review_item_id,
                "metadata_updated",
                {
                    "id": review_item_id,
                    "priority": priority,
                    "automation_policy": automation_policy,
                    "auto_process_reason": auto_process_reason,
                },
                now,
            )
            db.commit()
        await self.write_review_state_snapshot()
        return await self.get_review_item(review_item_id)

    async def mark_review_item_surfaced(
        self,
        review_item_id: str,
        *,
        parent_discord_message_id: int | None = None,
        surface: str = "morning_digest",
    ) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE review_items
                SET surface_count = COALESCE(surface_count, 0) + 1,
                    last_surface_at = ?,
                    parent_discord_message_id = COALESCE(?, parent_discord_message_id),
                    updated_at_utc = ?
                WHERE id = ?
                """,
                (now, parent_discord_message_id, now, review_item_id),
            )
            if cursor.rowcount == 0:
                db.commit()
                return None
            _insert_review_event(
                db,
                review_item_id,
                "surfaced",
                {
                    "id": review_item_id,
                    "surface": surface,
                    "parent_discord_message_id": parent_discord_message_id,
                },
                now,
            )
            db.commit()
        return await self.get_review_item(review_item_id)

    async def get_review_items_by_ids(self, review_item_ids: list[str]) -> list[dict[str, Any]]:
        ids = [str(item_id) for item_id in review_item_ids if str(item_id or "").strip()]
        if not ids:
            return []
        placeholders = ", ".join("?" for _ in ids)
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"SELECT * FROM review_items WHERE id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
        loaded = [_review_item_row(row) for row in rows]
        by_id = {item["id"]: item for item in loaded}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    async def bind_discord_message(
        self,
        *,
        review_item_id: str,
        discord_message_id: int,
        discord_channel_id: int,
        discord_thread_id: int | None = None,
        source_kind: str | None = None,
        source_id: str | int | None = None,
        source_path: str | None = None,
        action_on_reply: str = "add_detail",
        parent_discord_message_id: int | None = None,
        update_review_item_message: bool = True,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        source_id_text = str(source_id) if source_id is not None else None
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO discord_message_bindings
                    (discord_message_id, discord_channel_id, discord_thread_id,
                     review_item_id, source_kind, source_id, source_path,
                     action_on_reply, created_at_utc, updated_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(discord_channel_id, discord_message_id) DO UPDATE SET
                    discord_thread_id = excluded.discord_thread_id,
                    review_item_id = excluded.review_item_id,
                    source_kind = excluded.source_kind,
                    source_id = excluded.source_id,
                    source_path = excluded.source_path,
                    action_on_reply = excluded.action_on_reply,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    discord_message_id,
                    discord_channel_id,
                    discord_thread_id,
                    review_item_id,
                    source_kind,
                    source_id_text,
                    source_path,
                    action_on_reply,
                    now,
                    now,
                ),
            )
            if update_review_item_message:
                db.execute(
                    """
                    UPDATE review_items
                    SET discord_channel_id = ?,
                        discord_message_id = ?,
                        discord_thread_id = ?,
                        parent_discord_message_id = COALESCE(?, parent_discord_message_id),
                        updated_at_utc = ?
                    WHERE id = ?
                    """,
                    (
                        discord_channel_id,
                        discord_message_id,
                        discord_thread_id,
                        parent_discord_message_id,
                        now,
                        review_item_id,
                    ),
                )
            _insert_review_event(
                db,
                review_item_id,
                "discord_bound",
                {
                    "discord_message_id": discord_message_id,
                    "discord_channel_id": discord_channel_id,
                    "discord_thread_id": discord_thread_id,
                    "action_on_reply": action_on_reply,
                },
                now,
            )
            db.commit()
        await self._append_review_log(
            {
                "event": "discord_message_bound",
                "id": review_item_id,
                "discord_message_id": discord_message_id,
                "discord_channel_id": discord_channel_id,
                "created_at_utc": now,
            }
        )
        await self.write_review_state_snapshot()
        binding = await self.get_discord_binding(discord_message_id, discord_channel_id)
        if binding is None:
            raise RuntimeError("discord binding missing after create")
        return binding

    async def get_discord_binding(
        self,
        discord_message_id: int,
        discord_channel_id: int | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            if discord_channel_id is None:
                row = db.execute(
                    """
                    SELECT * FROM discord_message_bindings
                    WHERE discord_message_id = ?
                    ORDER BY updated_at_utc DESC
                    LIMIT 1
                    """,
                    (discord_message_id,),
                ).fetchone()
            else:
                row = db.execute(
                    """
                    SELECT * FROM discord_message_bindings
                    WHERE discord_message_id = ? AND discord_channel_id = ?
                    """,
                    (discord_message_id, discord_channel_id),
                ).fetchone()
        return dict(row) if row else None

    async def record_review_reply(
        self,
        *,
        review_item_id: str,
        raw_text: str,
        actor_id: int | None,
        discord_message_id: int | None,
        discord_channel_id: int | None,
        ai_interpretation: dict[str, Any],
        ai_validation: dict[str, Any],
    ) -> dict[str, Any] | None:
        decision = str(ai_validation.get("decision") or "")
        status = "needs_clarification" if decision == "ask_clarification" else "pending"
        proposed_status = str(ai_validation.get("proposed_status") or "").strip()
        if ai_validation.get("safe_to_persist") and proposed_status in REVIEW_STATUSES:
            status = proposed_status
        item = await self.set_review_item_status(
            review_item_id,
            status,
            ai_interpretation=ai_interpretation,
            ai_validation=ai_validation,
            confidence=str(ai_validation.get("confidence") or ai_interpretation.get("confidence") or "low"),
            missing_context=ai_validation.get("missing_context") or ai_interpretation.get("missing_context"),
            note="discord_reply",
        )
        now = utc_now_iso()
        with self._connect() as db:
            _insert_review_event(
                db,
                review_item_id,
                "discord_reply",
                {
                    "raw_text": raw_text,
                    "actor_id": actor_id,
                    "discord_message_id": discord_message_id,
                    "discord_channel_id": discord_channel_id,
                    "ai_interpretation": ai_interpretation,
                    "ai_validation": ai_validation,
                },
                now,
            )
            db.commit()
        await self._append_review_log(
            {
                "event": "review_reply",
                "id": review_item_id,
                "status": status,
                "raw_text": raw_text,
                "discord_message_id": discord_message_id,
                "discord_channel_id": discord_channel_id,
                "created_at_utc": now,
            }
        )
        return item

    async def expire_review_items(
        self,
        now_utc: datetime | None = None,
        eligible_automation_policies: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        now_dt = now_utc or datetime.now(timezone.utc)
        now = now_dt.replace(microsecond=0).isoformat()
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            policy_clause = ""
            params: list[Any] = [now]
            if eligible_automation_policies:
                placeholders = ", ".join("?" for _ in eligible_automation_policies)
                policy_clause = f"AND COALESCE(automation_policy, '') IN ({placeholders})"
                params.extend(eligible_automation_policies)
            rows = db.execute(
                f"""
                SELECT * FROM review_items
                WHERE status IN ('pending', 'needs_clarification')
                  AND expires_at_utc IS NOT NULL
                  AND expires_at_utc <= ?
                  {policy_clause}
                ORDER BY expires_at_utc ASC
                """,
                tuple(params),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                placeholders = ", ".join("?" for _ in ids)
                db.execute(
                    f"""
                    UPDATE review_items
                    SET status = 'expired', updated_at_utc = ?
                    WHERE id IN ({placeholders})
                    """,
                    (now, *ids),
                )
                for item_id in ids:
                    _insert_review_event(db, item_id, "expired", {"id": item_id}, now)
            db.commit()
        expired = [_review_item_row(row) for row in rows]
        for item in expired:
            await self._append_review_log(
                {
                    "event": "review_item_expired",
                    "id": item["id"],
                    "kind": item["kind"],
                    "source_kind": item.get("source_kind"),
                    "created_at_utc": now,
                }
            )
        if expired:
            await self.write_review_state_snapshot()
        return expired

    async def record_report_publication(
        self,
        *,
        kind: str,
        local_date: str,
        channel_id: int,
        message_id: int | None,
    ) -> bool:
        now = utc_now_iso()
        with self._connect() as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO report_publications
                    (kind, local_date, channel_id, message_id, created_at_utc)
                VALUES (?, ?, ?, ?, ?)
                """,
                (kind, local_date, channel_id, message_id, now),
            )
            db.commit()
            return cursor.rowcount > 0

    async def write_review_state_snapshot(self) -> None:
        items = await self.list_review_items(
            ("pending", "needs_clarification", "expired"),
            limit=80,
        )
        lines = [
            "# Review Items",
            "",
            f"Generated: {utc_now_iso()}",
            "Source: tracker DB review_items + discord_message_bindings",
            "",
        ]
        for status in ("needs_clarification", "pending", "expired"):
            lines.extend(["", f"## {status.replace('_', ' ').title()}"])
            matching = [item for item in items if item["status"] == status]
            if not matching:
                lines.append("- none")
                continue
            for item in matching:
                source = item.get("source_kind") or "unknown"
                message = item.get("discord_message_id")
                discord = f" discord:{message}" if message else ""
                priority = item.get("priority") or "normal"
                surfaced = item.get("surface_count") or 0
                lines.append(
                    f"- {item['id']} [{priority} {item['kind']}/{source} surfaced:{surfaced}]{discord}: {item['title']}"
                )
        lines.append("")
        (self.state_dir / "review-items.md").write_text("\n".join(lines), encoding="utf-8")

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

    async def log_work_capture(
        self,
        *,
        local_date: str,
        raw_text: str,
        draft_parse: dict[str, Any] | str,
        message_id: int | None,
        channel_id: int | None,
        channel_name: str,
        logged_by: int | None,
        source: str = "discord",
        review_status: str = "unreviewed",
        review_reason: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        draft_obj, draft_json = _normalize_work_draft_parse(draft_parse, raw_text)
        confidence = str(draft_obj.get("confidence") or "low")
        reason = review_reason or str(draft_obj.get("review_reason") or "draft_only_requires_hermis_review")
        with self._connect() as db:
            duplicate = _work_capture_duplicate(db, source, message_id)
            if duplicate:
                return {"status": "duplicate", "created": False, **duplicate}
            capture_id = _insert_work_capture(
                db,
                local_date=local_date,
                source=source,
                message_id=message_id,
                channel_id=channel_id,
                channel_name=channel_name,
                logged_by=logged_by,
                raw_text=raw_text,
                draft_parse_json=draft_json,
                confidence=confidence,
                review_reason=reason,
                review_status=review_status,
                now=now,
            )
            record = _work_capture_record(
                capture_id=capture_id,
                local_date=local_date,
                raw_text=raw_text,
                draft_parse=draft_obj,
                source=source,
                message_id=message_id,
                channel_id=channel_id,
                channel_name=channel_name,
                logged_by=logged_by,
                confidence=confidence,
                review_reason=reason,
                review_status=review_status,
                now=now,
            )
            _insert_work_event(db, None, capture_id, "capture", record, local_date, now)
            db.commit()

        await self._append_daily_logs(self.work_dir, local_date, record, _work_md_row(record))
        self._append_raw_work_capture(local_date, now, source, channel_name, message_id, review_status, raw_text)
        await self.write_work_state_snapshot()
        return {
            "status": review_status,
            "created": True,
            "capture_id": capture_id,
            "confidence": confidence,
            "review_reason": reason,
        }

    async def add_manual_work_items(
        self,
        *,
        local_date: str,
        raw_text: str,
        drafts,
        draft_parse: dict[str, Any] | str,
        message_id: int | None,
        channel_id: int | None,
        channel_name: str,
        logged_by: int | None,
        source: str = "discord_command",
    ) -> dict[str, Any]:
        capture = await self.log_work_capture(
            local_date=local_date,
            raw_text=raw_text,
            draft_parse=draft_parse,
            message_id=message_id,
            channel_id=channel_id,
            channel_name=channel_name,
            logged_by=logged_by,
            source=source,
            review_status="confirmed",
            review_reason="manual_command_confirmed_by_user",
        )
        if not capture.get("created"):
            return {**capture, "item_ids": []}
        records = await self.confirm_work_capture(
            int(capture["capture_id"]),
            drafts,
            review_note="manual_command_confirmed_by_user",
        )
        return {
            "status": "confirmed",
            "created": True,
            "capture_id": capture["capture_id"],
            "item_ids": [record["id"] for record in records],
        }

    async def confirm_work_capture(
        self,
        capture_id: int,
        drafts,
        *,
        review_note: str = "hermis_review_confirmed",
    ) -> list[dict[str, Any]]:
        now = utc_now_iso()
        draft_list = _work_draft_list(drafts)
        if not draft_list:
            raise ValueError("confirmed work captures require at least one item")
        with self._connect() as db:
            row = _fetchone(
                db,
                """
                SELECT local_date, source, source_message_id, source_channel_id,
                       source_channel_name, logged_by, raw_text, review_status,
                       (SELECT COUNT(*) FROM work_items WHERE capture_id = work_captures.id)
                FROM work_captures
                WHERE id = ? AND review_status IN ('unreviewed', 'clarification', 'confirmed')
                """,
                (capture_id,),
            )
            if row is None:
                return []
            local_date, source, message_id, channel_id, channel_name, logged_by, raw_text, existing_status, item_count = row
            if existing_status == "confirmed" and int(item_count) > 0:
                return []
            source_item_start = _next_work_source_item_index(db, capture_id)
            records = []
            for offset, draft in enumerate(draft_list):
                source_item_index = source_item_start + offset
                item_id = _insert_work_item(
                    db,
                    capture_id=capture_id,
                    draft=draft,
                    raw_text=raw_text,
                    source=source,
                    message_id=message_id,
                    source_item_index=source_item_index,
                    channel_id=channel_id,
                    logged_by=logged_by,
                    now=now,
                )
                record = _work_item_record(
                    item_id=item_id,
                    capture_id=capture_id,
                    draft=draft,
                    raw_text=raw_text,
                    source=source,
                    message_id=message_id,
                    source_item_index=source_item_index,
                    channel_id=channel_id,
                    logged_by=logged_by,
                    event="work_item_confirmed",
                    now=now,
                )
                _insert_work_event(db, item_id, capture_id, "confirmed", record, local_date, now)
                records.append(record)
            db.execute(
                """
                UPDATE work_captures
                SET review_status = 'confirmed',
                    review_reason = ?,
                    clarification_question = NULL,
                    reviewed_at_utc = ?
                WHERE id = ?
                """,
                (review_note, now, capture_id),
            )
            db.commit()

        for record in records:
            await self._append_daily_logs(self.work_dir, local_date, record, _work_md_row(record))
        await self.write_work_state_snapshot()
        return records

    async def ask_work_clarification(self, capture_id: int, question: str) -> bool:
        text = " ".join(str(question or "").split())
        if not text:
            raise ValueError("clarification question is required")
        now = utc_now_iso()
        with self._connect() as db:
            row = _fetchone(
                db,
                "SELECT local_date, raw_text FROM work_captures WHERE id = ? AND review_status IN ('unreviewed', 'clarification')",
                (capture_id,),
            )
            if row is None:
                return False
            local_date, raw_text = row
            db.execute(
                """
                UPDATE work_captures
                SET review_status = 'clarification',
                    clarification_question = ?,
                    review_reason = 'needs_clarification',
                    reviewed_at_utc = ?
                WHERE id = ?
                """,
                (text, now, capture_id),
            )
            record = {
                "event": "work_capture_clarification",
                "id": capture_id,
                "local_date": local_date,
                "raw_text": raw_text,
                "question": text,
                "status": "clarification",
                "created_at_utc": now,
            }
            _insert_work_event(db, None, capture_id, "clarification", record, local_date, now)
            db.commit()
        await self._append_daily_logs(self.work_dir, local_date, record, _work_md_row(record))
        await self.write_work_state_snapshot()
        return True

    async def ignore_work_capture(self, capture_id: int, reason: str) -> bool:
        text = " ".join(str(reason or "").split())
        if not text:
            raise ValueError("ignored work captures require an explicit reason")
        now = utc_now_iso()
        with self._connect() as db:
            row = _fetchone(
                db,
                "SELECT local_date, raw_text FROM work_captures WHERE id = ? AND review_status IN ('unreviewed', 'clarification')",
                (capture_id,),
            )
            if row is None:
                return False
            local_date, raw_text = row
            db.execute(
                """
                UPDATE work_captures
                SET review_status = 'ignored',
                    ignore_reason = ?,
                    review_reason = 'ignored_after_review',
                    reviewed_at_utc = ?
                WHERE id = ?
                """,
                (text, now, capture_id),
            )
            record = {
                "event": "work_capture_ignored",
                "id": capture_id,
                "local_date": local_date,
                "raw_text": raw_text,
                "reason": text,
                "status": "ignored",
                "created_at_utc": now,
            }
            _insert_work_event(db, None, capture_id, "ignored", record, local_date, now)
            db.commit()
        await self._append_daily_logs(self.work_dir, local_date, record, _work_md_row(record))
        await self.write_work_state_snapshot()
        return True

    async def list_work_items(self, status: str = "active", limit: int = 12) -> list[dict[str, Any]]:
        if status == "active":
            where = "status IN ('open', 'waiting', 'blocked')"
            params: tuple[Any, ...] = ()
        else:
            where = "status = ?"
            params = (status,)
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"""
                SELECT * FROM work_items
                WHERE {where}
                ORDER BY
                    CASE status WHEN 'blocked' THEN 0 WHEN 'waiting' THEN 1 ELSE 2 END,
                    CASE priority WHEN 'p0' THEN 0 WHEN 'p1' THEN 1 WHEN 'p2' THEN 2 ELSE 3 END,
                    CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,
                    due_date ASC,
                    created_at_utc ASC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [_work_row_to_item(row) for row in rows]

    async def list_work_today(self, local_date: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT * FROM work_items
                WHERE status IN ('open', 'waiting', 'blocked')
                  AND (due_date <= ? OR scheduled_date = ?)
                ORDER BY
                    CASE status WHEN 'blocked' THEN 0 WHEN 'waiting' THEN 1 ELSE 2 END,
                    CASE priority WHEN 'p0' THEN 0 WHEN 'p1' THEN 1 WHEN 'p2' THEN 2 ELSE 3 END,
                    due_date ASC,
                    created_at_utc ASC
                LIMIT ?
                """,
                (local_date, local_date, limit),
            ).fetchall()
        return [_work_row_to_item(row) for row in rows]

    async def work_focus_items(self, local_date: str, limit: int = 5) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        today = datetime.fromisoformat(f"{local_date}T00:00:00").date()
        soon = (today + timedelta(days=2)).isoformat()
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            focus_rows = db.execute(
                """
                SELECT * FROM work_items
                WHERE status = 'open'
                  AND (priority IN ('p0', 'p1') OR due_date <= ? OR scheduled_date = ?)
                ORDER BY
                    CASE priority WHEN 'p0' THEN 0 WHEN 'p1' THEN 1 WHEN 'p2' THEN 2 ELSE 3 END,
                    CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,
                    due_date ASC,
                    COALESCE(effort_minutes, 999) ASC,
                    created_at_utc ASC
                LIMIT ?
                """,
                (soon, local_date, limit),
            ).fetchall()
            waiting_rows = db.execute(
                """
                SELECT * FROM work_items
                WHERE status IN ('blocked', 'waiting')
                ORDER BY updated_at_utc DESC
                LIMIT 5
                """
            ).fetchall()
        focus = [_work_row_to_item(row) for row in focus_rows]
        if len(focus) < limit:
            more = await self.list_work_items("active", limit=limit * 3)
            seen = {item["id"] for item in focus}
            for item in more:
                if item["status"] == "open" and item["id"] not in seen:
                    focus.append(item)
                    seen.add(item["id"])
                if len(focus) >= limit:
                    break
        return focus, [_work_row_to_item(row) for row in waiting_rows]

    async def list_work_reviews(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT id, local_date, review_status, review_reason, clarification_question,
                       raw_text, confidence, created_at_utc
                FROM work_captures
                WHERE review_status IN ('unreviewed', 'clarification')
                ORDER BY created_at_utc DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": row[0],
                "local_date": row[1],
                "review_status": row[2],
                "review_reason": row[3],
                "clarification_question": row[4],
                "raw_text": row[5],
                "confidence": row[6],
                "created_at_utc": row[7],
            }
            for row in rows
        ]

    async def get_work_capture(self, capture_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row = _fetchone(
                db,
                """
                SELECT id, local_date, raw_text, draft_parse_json, review_status
                FROM work_captures
                WHERE id = ?
                """,
                (capture_id,),
            )
        if row is None:
            return None
        try:
            draft_parse = json.loads(row[3] or "{}")
        except json.JSONDecodeError:
            draft_parse = {}
        return {
            "id": row[0],
            "local_date": row[1],
            "raw_text": row[2],
            "draft_parse": draft_parse,
            "review_status": row[4],
        }

    async def create_work_ai_suggestion(
        self,
        *,
        suggestion_kind: str,
        source_type: str,
        source_id: int | None,
        local_date: str,
        prompt: dict[str, Any],
        response: dict[str, Any],
        confidence: str | None = None,
        review_reason: str | None = None,
        status: str = "pending",
        reviewer_note: str | None = None,
        supersedes_suggestion_id: int | None = None,
    ) -> int:
        now = utc_now_iso()
        with self._connect() as db:
            cursor = db.execute(
                """
                INSERT INTO work_ai_suggestions
                    (suggestion_kind, source_type, source_id, local_date, prompt_json,
                     response_json, confidence, review_reason, status, reviewer_note,
                     supersedes_suggestion_id, created_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    suggestion_kind,
                    source_type,
                    source_id,
                    local_date,
                    json.dumps(prompt, sort_keys=True, ensure_ascii=False),
                    json.dumps(response, sort_keys=True, ensure_ascii=False),
                    str(confidence or response.get("confidence") or "low"),
                    str(review_reason or response.get("review_reason") or "ai_suggestion_needs_review"),
                    status,
                    reviewer_note,
                    supersedes_suggestion_id,
                    now,
                ),
            )
            db.commit()
            suggestion_id = int(cursor.lastrowid)
        if status == "pending":
            await self.create_review_item(
                kind="work_suggestion" if suggestion_kind == "capture_parse" else "work_automation_review",
                title=_work_ai_suggestion_title(suggestion_id, suggestion_kind, response),
                body=_work_ai_suggestion_body(response),
                source_path="state/work.md",
                source_record_id=suggestion_id,
                source_kind="work_ai_suggestion",
                ai_interpretation=response,
                status="pending",
                confidence=str(confidence or response.get("confidence") or "low"),
                missing_context=[str(response.get("review_reason") or "ai_suggestion_needs_review")],
            )
        return suggestion_id

    async def get_work_ai_suggestion(self, suggestion_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM work_ai_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        return _work_ai_suggestion_row(row) if row else None

    async def list_work_ai_suggestions(self, status: str = "pending", limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT * FROM work_ai_suggestions
                WHERE status = ?
                ORDER BY created_at_utc DESC, id DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        return [_work_ai_suggestion_row(row) for row in rows]

    async def work_ai_pending_exists(self, suggestion_kind: str, source_type: str, source_id: int | None) -> bool:
        with self._connect() as db:
            if source_id is None:
                row = _fetchone(
                    db,
                    """
                    SELECT 1 FROM work_ai_suggestions
                    WHERE suggestion_kind = ? AND source_type = ? AND source_id IS NULL AND status = 'pending'
                    """,
                    (suggestion_kind, source_type),
                )
            else:
                row = _fetchone(
                    db,
                    """
                    SELECT 1 FROM work_ai_suggestions
                    WHERE suggestion_kind = ? AND source_type = ? AND source_id = ? AND status = 'pending'
                    """,
                    (suggestion_kind, source_type, source_id),
                )
        return row is not None

    async def recent_work_ai_corrections(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT * FROM work_ai_suggestions
                WHERE status IN ('corrected', 'rejected')
                  AND reviewer_note IS NOT NULL
                ORDER BY reviewed_at_utc DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_work_ai_suggestion_row(row) for row in rows]

    async def reject_work_ai_suggestion(self, suggestion_id: int, reason: str) -> bool:
        note = " ".join(str(reason or "").split())
        if not note:
            raise ValueError("rejecting an AI suggestion requires a reason")
        now = utc_now_iso()
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE work_ai_suggestions
                SET status = 'rejected', reviewer_note = ?, reviewed_at_utc = ?
                WHERE id = ? AND status = 'pending'
                """,
                (note, now, suggestion_id),
            )
            db.commit()
            return cursor.rowcount > 0

    async def mark_work_ai_suggestion_corrected(
        self,
        suggestion_id: int,
        note: str,
    ) -> bool:
        clean_note = " ".join(str(note or "").split())
        if not clean_note:
            raise ValueError("correcting an AI suggestion requires a note")
        now = utc_now_iso()
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE work_ai_suggestions
                SET status = 'corrected', reviewer_note = ?, reviewed_at_utc = ?
                WHERE id = ? AND status = 'pending'
                """,
                (clean_note, now, suggestion_id),
            )
            db.commit()
            return cursor.rowcount > 0

    async def accept_work_ai_suggestion(
        self,
        suggestion_id: int,
        *,
        reviewer_note: str | None = None,
    ) -> dict[str, Any] | None:
        suggestion = await self.get_work_ai_suggestion(suggestion_id)
        if suggestion is None or suggestion["status"] != "pending":
            return None
        response = suggestion["response"]
        result: dict[str, Any] = {"suggestion_id": suggestion_id, "action": "accepted"}
        if suggestion["suggestion_kind"] == "capture_parse":
            capture_id = int(suggestion["source_id"])
            outcome = str(response.get("outcome") or "").strip().lower()
            if outcome == "confirmed":
                items = response.get("items") or []
                records = await self.confirm_work_capture(
                    capture_id,
                    items,
                    review_note=f"ai_suggestion:{suggestion_id}_accepted",
                )
                result = {"suggestion_id": suggestion_id, "action": "confirmed", "item_ids": [item["id"] for item in records]}
            elif outcome == "ignored":
                reason = str(response.get("reason") or response.get("review_reason") or "").strip()
                await self.ignore_work_capture(capture_id, reason)
                result = {"suggestion_id": suggestion_id, "action": "ignored"}
            elif outcome in {"questions", "question", "clarification"}:
                question = str(response.get("question") or "").strip()
                await self.ask_work_clarification(capture_id, question)
                result = {"suggestion_id": suggestion_id, "action": "question"}
            else:
                raise ValueError(f"Unsupported AI suggestion outcome: {outcome or 'missing'}")
        now = utc_now_iso()
        note = " ".join(str(reviewer_note or "").split()) or None
        with self._connect() as db:
            db.execute(
                """
                UPDATE work_ai_suggestions
                SET status = 'accepted', reviewer_note = ?, reviewed_at_utc = ?
                WHERE id = ?
                """,
                (note, now, suggestion_id),
            )
            db.commit()
        await self.write_work_state_snapshot()
        return result

    async def set_work_item_status(
        self,
        item_id: int,
        status: str,
        *,
        local_date: str,
        reason: str = "",
        logged_by: int | None = None,
    ) -> dict[str, Any] | None:
        if status not in {"open", "waiting", "blocked", "done", "cancelled"}:
            raise ValueError(f"Unsupported work status: {status}")
        clean_reason = " ".join(str(reason or "").split())
        if status in {"waiting", "blocked"} and not clean_reason:
            raise ValueError(f"{status} work items require a reason")
        now = utc_now_iso()
        completed_at = now if status == "done" else None
        cancelled_at = now if status == "cancelled" else None
        next_followup_at = (datetime.now(timezone.utc) + timedelta(hours=24)).replace(microsecond=0).isoformat() if status == "waiting" else None
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                return None
            db.execute(
                """
                UPDATE work_items
                SET status = ?,
                    note = COALESCE(NULLIF(?, ''), note),
                    next_followup_at = COALESCE(?, next_followup_at),
                    updated_at_utc = ?,
                    completed_at_utc = COALESCE(?, completed_at_utc),
                    cancelled_at_utc = COALESCE(?, cancelled_at_utc)
                WHERE id = ?
                """,
                (status, clean_reason, next_followup_at, now, completed_at, cancelled_at, item_id),
            )
            updated = db.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
            item = _work_row_to_item(updated)
            payload = {
                "event": f"work_item_{status}",
                "id": item_id,
                "capture_id": item.get("capture_id"),
                "status": status,
                "reason": clean_reason,
                "logged_by": logged_by,
                "created_at_utc": now,
                **item,
            }
            _insert_work_event(db, item_id, item.get("capture_id"), status, payload, local_date, now)
            db.commit()
        await self._append_daily_logs(self.work_dir, local_date, payload, _work_md_row(payload))
        await self.write_work_state_snapshot()
        return item

    async def reschedule_work_item(
        self,
        item_id: int,
        *,
        local_date: str,
        due_date: str | None = None,
        due_at: str | None = None,
        scheduled_date: str | None = None,
        scheduled_at: str | None = None,
        logged_by: int | None = None,
    ) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                return None
            db.execute(
                """
                UPDATE work_items
                SET due_date = COALESCE(?, due_date),
                    due_at = ?,
                    scheduled_date = COALESCE(?, scheduled_date),
                    scheduled_at = ?,
                    status = 'open',
                    snoozed_until_utc = NULL,
                    updated_at_utc = ?
                WHERE id = ?
                """,
                (due_date, due_at, scheduled_date, scheduled_at, now, item_id),
            )
            updated = db.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
            item = _work_row_to_item(updated)
            payload = {"event": "work_item_rescheduled", "id": item_id, "logged_by": logged_by, "created_at_utc": now, **item}
            _insert_work_event(db, item_id, item.get("capture_id"), "rescheduled", payload, local_date, now)
            db.commit()
        await self._append_daily_logs(self.work_dir, local_date, payload, _work_md_row(payload))
        await self.write_work_state_snapshot()
        return item

    async def snooze_work_item(self, item_id: int, snoozed_until_utc: datetime, *, local_date: str) -> dict[str, Any] | None:
        now = utc_now_iso()
        snooze_iso = snoozed_until_utc.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE work_items SET snoozed_until_utc = ?, updated_at_utc = ? WHERE id = ?",
                (snooze_iso, now, item_id),
            )
            updated = db.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
            item = _work_row_to_item(updated)
            payload = {"event": "work_item_snoozed", "id": item_id, "snoozed_until_utc": snooze_iso, "created_at_utc": now, **item}
            _insert_work_event(db, item_id, item.get("capture_id"), "snoozed", payload, local_date, now)
            db.commit()
        await self._append_daily_logs(self.work_dir, local_date, payload, _work_md_row(payload))
        await self.write_work_state_snapshot()
        return item

    async def answer_work_clarification(self, capture_id: int, answer: str) -> bool:
        clean_answer = " ".join(str(answer or "").split())
        if not clean_answer:
            raise ValueError("clarification answer is required")
        now = utc_now_iso()
        with self._connect() as db:
            row = _fetchone(
                db,
                """
                SELECT local_date, raw_text, clarification_question
                FROM work_captures
                WHERE id = ? AND review_status = 'clarification'
                """,
                (capture_id,),
            )
            if row is None:
                return False
            local_date, raw_text, question = row
            updated_raw = f"{raw_text}\n\nClarification question: {question}\nClarification answer: {clean_answer}"
            db.execute(
                """
                UPDATE work_captures
                SET raw_text = ?,
                    review_status = 'unreviewed',
                    review_reason = 'clarification_answered',
                    clarification_question = NULL,
                    reviewed_at_utc = NULL
                WHERE id = ?
                """,
                (updated_raw, capture_id),
            )
            record = {
                "event": "work_capture_clarified",
                "id": capture_id,
                "local_date": local_date,
                "question": question,
                "answer": clean_answer,
                "status": "unreviewed",
                "created_at_utc": now,
            }
            _insert_work_event(db, None, capture_id, "clarified", record, local_date, now)
            db.commit()
        await self._append_daily_logs(self.work_dir, local_date, record, _work_md_row(record))
        await self.write_work_state_snapshot()
        return True

    async def record_work_automation_event(
        self,
        *,
        kind: str,
        local_date: str,
        reminder_id: str,
        payload: dict[str, Any],
        item_id: int | None = None,
        capture_id: int | None = None,
        channel_id: int | None = None,
        message_id: int | None = None,
    ) -> bool:
        now = utc_now_iso()
        with self._connect() as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO work_automation_events
                    (kind, item_id, capture_id, local_date, reminder_id, status,
                     message_id, channel_id, payload_json, created_at_utc, sent_at_utc)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, NULL)
                """,
                (
                    kind,
                    item_id,
                    capture_id,
                    local_date,
                    reminder_id,
                    message_id,
                    channel_id,
                    json.dumps(payload, sort_keys=True, ensure_ascii=False),
                    now,
                ),
            )
            db.commit()
            return cursor.rowcount > 0

    async def mark_work_automation_sent(
        self,
        *,
        kind: str,
        local_date: str,
        reminder_id: str,
        message_id: int,
        channel_id: int,
        payload: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now_iso()
        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False) if payload is not None else None
        with self._connect() as db:
            if payload_json is None:
                db.execute(
                    """
                    UPDATE work_automation_events
                    SET status = 'sent', message_id = ?, channel_id = ?, sent_at_utc = ?
                    WHERE kind = ? AND local_date = ? AND reminder_id = ?
                    """,
                    (message_id, channel_id, now, kind, local_date, reminder_id),
                )
            else:
                db.execute(
                    """
                    UPDATE work_automation_events
                    SET status = 'sent', message_id = ?, channel_id = ?, sent_at_utc = ?, payload_json = ?
                    WHERE kind = ? AND local_date = ? AND reminder_id = ?
                    """,
                    (message_id, channel_id, now, payload_json, kind, local_date, reminder_id),
                )
            db.commit()

    async def automation_event_exists(self, kind: str, local_date: str, reminder_id: str) -> bool:
        with self._connect() as db:
            row = _fetchone(
                db,
                """
                SELECT 1 FROM work_automation_events
                WHERE kind = ? AND local_date = ? AND reminder_id = ?
                """,
                (kind, local_date, reminder_id),
            )
        return row is not None

    async def create_work_blocker_prompt(
        self,
        *,
        item_id: int,
        local_date: str,
        reason: str,
        message_id: int | None = None,
    ) -> bool:
        now = utc_now_iso()
        with self._connect() as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO work_blocker_prompts
                    (item_id, local_date, reason, status, message_id, created_at_utc)
                VALUES (?, ?, ?, 'open', ?, ?)
                """,
                (item_id, local_date, reason, message_id, now),
            )
            db.commit()
            return cursor.rowcount > 0

    async def work_automation_status(self, local_date: str, limit: int = 12) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT kind, reminder_id, item_id, capture_id, sent_at_utc
                FROM work_automation_events
                WHERE local_date = ?
                ORDER BY sent_at_utc DESC, id DESC
                LIMIT ?
                """,
                (local_date, limit),
            ).fetchall()
        return [
            {"kind": row[0], "reminder_id": row[1], "item_id": row[2], "capture_id": row[3], "sent_at_utc": row[4]}
            for row in rows
        ]

    async def work_clarifications(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT id, local_date, clarification_question, raw_text, created_at_utc
                FROM work_captures
                WHERE review_status = 'clarification'
                ORDER BY created_at_utc ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {"id": row[0], "local_date": row[1], "question": row[2], "raw_text": row[3], "created_at_utc": row[4]}
            for row in rows
        ]

    async def work_due_reminder_items(
        self,
        *,
        local_date: str,
        now_local: datetime,
        lookahead_minutes: int,
    ) -> list[dict[str, Any]]:
        lookahead = now_local + timedelta(minutes=lookahead_minutes)
        now_time = now_local.strftime("%H:%M")
        lookahead_time = lookahead.strftime("%H:%M")
        now_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT * FROM work_items
                WHERE status = 'open'
                  AND (snoozed_until_utc IS NULL OR snoozed_until_utc <= ?)
                  AND (
                    (due_date = ? AND due_at IS NOT NULL AND due_at BETWEEN ? AND ?)
                    OR (scheduled_date = ? AND scheduled_at IS NOT NULL AND scheduled_at BETWEEN ? AND ?)
                    OR (due_date = ? AND due_at IS NULL)
                  )
                ORDER BY
                    CASE priority WHEN 'p0' THEN 0 WHEN 'p1' THEN 1 WHEN 'p2' THEN 2 ELSE 3 END,
                    COALESCE(due_at, scheduled_at, '23:00') ASC,
                    id ASC
                LIMIT 5
                """,
                (now_utc, local_date, now_time, lookahead_time, local_date, now_time, lookahead_time, local_date),
            ).fetchall()
        return [_work_row_to_item(row) for row in rows]

    async def overdue_work_items(self, *, local_date: str, now_local: datetime, grace_minutes: int) -> list[dict[str, Any]]:
        cutoff = (now_local - timedelta(minutes=grace_minutes)).strftime("%H:%M")
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT * FROM work_items
                WHERE status = 'open'
                  AND (
                    due_date < ?
                    OR (due_date = ? AND due_at IS NOT NULL AND due_at <= ?)
                  )
                ORDER BY due_date ASC, COALESCE(due_at, '23:00') ASC, id ASC
                LIMIT 5
                """,
                (local_date, local_date, cutoff),
            ).fetchall()
        return [_work_row_to_item(row) for row in rows]

    async def waiting_followup_items(self, now_utc: datetime, limit: int = 5) -> list[dict[str, Any]]:
        now_iso = now_utc.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT * FROM work_items
                WHERE status = 'waiting'
                  AND (next_followup_at IS NULL OR next_followup_at <= ?)
                  AND (snoozed_until_utc IS NULL OR snoozed_until_utc <= ?)
                ORDER BY COALESCE(next_followup_at, updated_at_utc) ASC, id ASC
                LIMIT ?
                """,
                (now_iso, now_iso, limit),
            ).fetchall()
        return [_work_row_to_item(row) for row in rows]

    async def write_work_shutdown_report(
        self,
        local_date: str,
        *,
        focus: list[dict[str, Any]],
        overdue: list[dict[str, Any]],
        waiting: list[dict[str, Any]],
        clarifications: list[dict[str, Any]],
        first_action: dict[str, Any] | None,
    ) -> Path:
        lines = [
            f"# Work Shutdown - {local_date}",
            "",
            "## Done",
            "- answer in Discord",
            "",
            "## Still Open",
        ]
        lines.extend(_work_state_lines(focus[:10]))
        lines.extend(["", "## Overdue"])
        lines.extend(_work_state_lines(overdue[:10]))
        lines.extend(["", "## Blocked / Waiting"])
        lines.extend(_work_state_lines(waiting[:10]))
        lines.extend(["", "## Clarifications"])
        if clarifications:
            for item in clarifications:
                lines.append(f"- capture:{item['id']}: {item['question']}")
        else:
            lines.append("- none")
        lines.extend(["", "## First Tomorrow"])
        if first_action:
            lines.append(f"- #{first_action['id']} {first_action['title']}")
        else:
            lines.append("- none")
        lines.append("")
        path = self.work_report_dir / f"{local_date}-shutdown.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    async def write_work_state_snapshot(self) -> None:
        if not self.db_path.exists():
            return
        today = datetime.now().date().isoformat()
        active = await self.list_work_items("active", limit=80)
        today_items = await self.list_work_today(today, limit=10)
        reviews = await self.list_work_reviews(limit=20)
        suggestions = await self.list_work_ai_suggestions("pending", limit=10)
        now_items = [item for item in active if item["status"] == "open" and item["priority"] in {"p0", "p1"}]
        next_items = [item for item in active if item["status"] == "open" and item["priority"] not in {"p0", "p1"}]
        waiting = [item for item in active if item["status"] in {"blocked", "waiting"}]
        overdue = [item for item in active if item.get("due_date") and item["due_date"] < today]
        first_action = (today_items or now_items or next_items or [None])[0]
        lines = [
            "# Work State",
            "",
            f"Generated: {utc_now_iso()}",
            "Source: tracker DB work_items + work_captures",
            "",
            "## Today Focus",
        ]
        lines.extend(_work_state_lines(today_items[:8]))
        lines.extend([
            "",
            "## Recommended Next Action",
        ])
        if first_action:
            lines.append(f"- #{first_action['id']} {first_action['title']}")
        else:
            lines.append("- none")
        lines.extend([
            "",
            "## Overdue",
        ])
        lines.extend(_work_state_lines(overdue[:10]))
        lines.extend([
            "",
            "## Now",
        ])
        lines.extend(_work_state_lines(now_items[:10]))
        lines.extend(["", "## Next"])
        lines.extend(_work_state_lines(next_items[:20]))
        lines.extend(["", "## Blocked / Waiting"])
        lines.extend(_work_state_lines(waiting[:20]))
        lines.extend(["", "## Unreviewed / Unclear Captures"])
        if reviews:
            for review in reviews:
                snippet = _snippet(review["raw_text"], 120)
                detail = review["clarification_question"] or review["review_reason"]
                lines.append(f"- capture:{review['id']} {review['review_status']} ({detail}): {snippet}")
        else:
            lines.append("- none")
        lines.extend(["", "## Pending AI Suggestions"])
        if suggestions:
            for suggestion in suggestions:
                response = suggestion["response"]
                detail = response.get("outcome") or response.get("message") or suggestion["review_reason"]
                lines.append(f"- suggestion:{suggestion['id']} {suggestion['suggestion_kind']} ({suggestion['confidence']}): {_snippet(detail, 120)}")
        else:
            lines.append("- none")
        lines.extend(["", "## Next Automation Events"])
        lines.append("- prep: 13:00 Africa/Casablanca")
        lines.append("- start plan: 14:00 Africa/Casablanca")
        lines.append("- shutdown: 23:00 Africa/Casablanca")
        lines.extend(
            [
                "",
                "## Operating Rules",
                "- Normal #work-tracker messages are AI-first and review-gated: raw capture, AI suggestion, then human accept/correct/reject.",
                "- Draft parse JSON and AI suggestions are hints only; they are not final task truth.",
                "- Confirmed work follows the Casablanca work window: 14:00-23:00.",
                "",
            ]
        )
        (self.state_dir / "work.md").write_text("\n".join(lines), encoding="utf-8")

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
                await self.create_review_item(
                    kind="finance_review",
                    title=f"Money needs review {review_id}",
                    body=f"Reason: {parsed.review_reason or 'needs_review'}\n\n{raw_text}",
                    source_path=f"raw/captures/{local_date}.md",
                    source_record_id=review_id,
                    source_kind="finance_parse_review",
                    confidence="low",
                    missing_context=[parsed.review_reason or "needs_review"],
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

    def _append_raw_work_capture(
        self,
        local_date: str,
        timestamp_utc: str,
        source: str,
        channel_name: str,
        message_id: int | None,
        review_status: str,
        raw_text: str,
    ) -> None:
        capture_id = f"work-{source}-{message_id or timestamp_utc.replace(':', '').replace('+', '')}"
        processed = "true" if review_status == "confirmed" else "false"
        block = (
            "\n---\n\n"
            f"capture_id: {capture_id}\n"
            f"timestamp: {timestamp_utc}\n"
            f"source: {source}:#{channel_name}\n"
            "classification: work\n"
            f"status: work_{review_status}\n"
            f"processed: {processed}\n\n"
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

    async def _append_review_log(self, record: dict[str, Any]) -> None:
        timestamp = str(record.get("created_at_utc") or utc_now_iso())
        local_date = timestamp[:10]
        jsonl_path = self.review_dir / f"{local_date}.jsonl"
        md_path = self.review_dir / f"{local_date}.md"
        _append_text(jsonl_path, json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        if not md_path.exists():
            _append_text(
                md_path,
                f"# Review Log {local_date}\n\n"
                "| Time (UTC) | Event | ID | Status | Title / Detail |\n"
                "|---|---|---|---|---|\n",
            )
        _append_text(md_path, _review_md_row(record))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


REVIEW_STATUSES = {
    "pending",
    "approved",
    "rejected",
    "needs_clarification",
    "expired",
    "auto_processed",
}


def review_item_id_for_source(kind: str, source_path: str | None, source_record_id: str | int | None) -> str:
    source = f"{kind}|{source_path or ''}|{source_record_id or ''}"
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
    clean_kind = "".join(ch if ch.isalnum() else "-" for ch in kind.lower()).strip("-") or "item"
    return f"review-{clean_kind}-{digest}"


def _default_review_expiry(hours: int = 18) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def _normalize_review_status(status: str) -> str:
    clean = str(status or "pending").strip().lower()
    if clean == "clarification":
        clean = "needs_clarification"
    if clean not in REVIEW_STATUSES:
        raise ValueError(f"Unsupported review item status: {status}")
    return clean


def _json_list(value: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    return [" ".join(str(item).split()) for item in value if " ".join(str(item).split())]


def _json_obj(text: str | None) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _json_array(text: str | None) -> list[Any]:
    try:
        value = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _review_item_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["ai_interpretation"] = _json_obj(item.pop("ai_interpretation_json", "{}"))
    item["ai_validation"] = _json_obj(item.pop("ai_validation_json", "{}"))
    item["missing_context"] = _json_array(item.pop("missing_context_json", "[]"))
    return item


def _insert_review_event(
    db: sqlite3.Connection,
    review_item_id: str | None,
    event: str,
    payload: dict[str, Any],
    now: str,
) -> None:
    db.execute(
        """
        INSERT INTO review_item_events (review_item_id, event, payload_json, created_at_utc)
        VALUES (?, ?, ?, ?)
        """,
        (review_item_id, event, json.dumps(payload, sort_keys=True, ensure_ascii=False), now),
    )


def _review_md_row(record: dict[str, Any]) -> str:
    logged_at = str(record.get("created_at_utc") or utc_now_iso()).replace("+00:00", "Z")
    detail = str(record.get("title") or record.get("raw_text") or record.get("note") or "").replace("|", "\\|")
    return (
        f"| {logged_at} | {record.get('event')} | {record.get('id') or ''} | "
        f"{record.get('status') or ''} | {detail} |\n"
    )


def _fetchone(db: sqlite3.Connection, query: str, params: tuple[Any, ...]):
    return db.execute(query, params).fetchone()


def _normalize_work_draft_parse(draft_parse: dict[str, Any] | str, raw_text: str) -> tuple[dict[str, Any], str]:
    if isinstance(draft_parse, str):
        try:
            parsed = json.loads(draft_parse)
        except json.JSONDecodeError:
            parsed = {
                "status": "draft_parse",
                "confidence": "low",
                "review_reason": "invalid_draft_parse_json",
                "candidates": [],
                "raw_text": raw_text,
            }
    elif isinstance(draft_parse, dict):
        parsed = dict(draft_parse)
    else:
        parsed = {
            "status": "draft_parse",
            "confidence": "low",
            "review_reason": "missing_draft_parse",
            "candidates": [],
            "raw_text": raw_text,
        }
    parsed.setdefault("status", "draft_parse")
    parsed.setdefault("confidence", "low")
    parsed.setdefault("review_reason", "draft_only_requires_hermis_review")
    parsed.setdefault("candidates", [])
    parsed.setdefault("raw_text", raw_text)
    return parsed, json.dumps(parsed, sort_keys=True, ensure_ascii=False)


def _work_capture_duplicate(db: sqlite3.Connection, source: str, message_id: int | None) -> dict[str, Any] | None:
    if message_id is None:
        return None
    row = _fetchone(
        db,
        """
        SELECT id, review_status
        FROM work_captures
        WHERE source = ? AND source_message_id = ?
        """,
        (source, message_id),
    )
    if row is not None:
        item_rows = db.execute(
            """
            SELECT id FROM work_items
            WHERE capture_id = ?
            ORDER BY source_item_index, id
            """,
            (row[0],),
        ).fetchall()
        return {
            "capture_id": row[0],
            "review_status": row[1],
            "item_ids": [item_row[0] for item_row in item_rows],
        }
    return None


def _insert_work_capture(
    db: sqlite3.Connection,
    *,
    local_date: str,
    source: str,
    message_id: int | None,
    channel_id: int | None,
    channel_name: str,
    logged_by: int | None,
    raw_text: str,
    draft_parse_json: str,
    confidence: str,
    review_reason: str,
    review_status: str,
    now: str,
) -> int:
    cursor = db.execute(
        """
        INSERT INTO work_captures
            (local_date, source, source_message_id, source_channel_id,
             source_channel_name, logged_by, raw_text, draft_parse_json,
             confidence, review_reason, review_status, created_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            local_date,
            source,
            message_id,
            channel_id,
            channel_name,
            logged_by,
            raw_text,
            draft_parse_json,
            confidence,
            review_reason,
            review_status,
            now,
        ),
    )
    return int(cursor.lastrowid)


def _work_capture_record(
    *,
    capture_id: int,
    local_date: str,
    raw_text: str,
    draft_parse: dict[str, Any],
    source: str,
    message_id: int | None,
    channel_id: int | None,
    channel_name: str,
    logged_by: int | None,
    confidence: str,
    review_reason: str,
    review_status: str,
    now: str,
) -> dict[str, Any]:
    return {
        "event": "work_capture",
        "id": capture_id,
        "local_date": local_date,
        "raw_text": raw_text,
        "draft_parse": draft_parse,
        "confidence": confidence,
        "review_reason": review_reason,
        "status": review_status,
        "source": source,
        "source_message_id": message_id,
        "source_channel_id": channel_id,
        "source_channel_name": channel_name,
        "logged_by": logged_by,
        "created_at_utc": now,
    }


def _work_draft_list(drafts) -> list[Any]:
    if drafts is None:
        return []
    if isinstance(drafts, (list, tuple)):
        return list(drafts)
    return [drafts]


def _draft_value(draft, key: str, default: Any = None) -> Any:
    if isinstance(draft, dict):
        return draft.get(key, default)
    return getattr(draft, key, default)


def _insert_work_item(
    db: sqlite3.Connection,
    *,
    capture_id: int,
    draft,
    raw_text: str,
    source: str,
    message_id: int | None,
    source_item_index: int,
    channel_id: int | None,
    logged_by: int | None,
    now: str,
) -> int:
    tags = _draft_value(draft, "tags", ())
    if isinstance(tags, str):
        tags = [tags]
    cursor = db.execute(
        """
        INSERT INTO work_items
            (capture_id, title, status, priority, project, area, due_date,
             due_at, scheduled_date, scheduled_at, energy, effort_minutes, context, tags_json, note,
             source, source_message_id, source_channel_id, logged_by, raw_text,
             source_item_index, created_at_utc, updated_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            capture_id,
            str(_draft_value(draft, "title") or "").strip(),
            str(_draft_value(draft, "status", "open") or "open"),
            str(_draft_value(draft, "priority", "p2") or "p2"),
            _draft_value(draft, "project"),
            _draft_value(draft, "area"),
            _draft_value(draft, "due_date"),
            _draft_value(draft, "due_at"),
            _draft_value(draft, "scheduled_date"),
            _draft_value(draft, "scheduled_at"),
            _draft_value(draft, "energy"),
            _draft_value(draft, "effort_minutes"),
            _draft_value(draft, "context"),
            json.dumps(list(tags), sort_keys=True, ensure_ascii=False),
            _draft_value(draft, "note"),
            source,
            message_id,
            channel_id,
            logged_by,
            raw_text,
            source_item_index,
            now,
            now,
        ),
    )
    return int(cursor.lastrowid)


def _next_work_source_item_index(db: sqlite3.Connection, capture_id: int) -> int:
    row = _fetchone(
        db,
        """
        SELECT COALESCE(MAX(source_item_index), -1)
        FROM work_items
        WHERE capture_id = ?
        """,
        (capture_id,),
    )
    return int(row[0]) + 1 if row else 0


def _insert_work_event(
    db: sqlite3.Connection,
    item_id: int | None,
    capture_id: int | None,
    event: str,
    payload: dict[str, Any],
    local_date: str,
    now: str,
) -> None:
    db.execute(
        """
        INSERT INTO work_item_events
            (item_id, capture_id, event, payload_json, local_date, created_at_utc)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (item_id, capture_id, event, json.dumps(payload, sort_keys=True, ensure_ascii=False), local_date, now),
    )


def _work_item_record(
    *,
    item_id: int,
    capture_id: int,
    draft,
    raw_text: str,
    source: str,
    message_id: int | None,
    source_item_index: int,
    channel_id: int | None,
    logged_by: int | None,
    event: str,
    now: str,
) -> dict[str, Any]:
    tags = _draft_value(draft, "tags", ())
    if isinstance(tags, str):
        tags = [tags]
    return {
        "event": event,
        "id": item_id,
        "capture_id": capture_id,
        "title": str(_draft_value(draft, "title") or "").strip(),
        "status": str(_draft_value(draft, "status", "open") or "open"),
        "priority": str(_draft_value(draft, "priority", "p2") or "p2"),
        "project": _draft_value(draft, "project"),
        "area": _draft_value(draft, "area"),
        "due_date": _draft_value(draft, "due_date"),
        "due_at": _draft_value(draft, "due_at"),
        "scheduled_date": _draft_value(draft, "scheduled_date"),
        "scheduled_at": _draft_value(draft, "scheduled_at"),
        "energy": _draft_value(draft, "energy"),
        "effort_minutes": _draft_value(draft, "effort_minutes"),
        "context": _draft_value(draft, "context"),
        "tags": list(tags),
        "note": _draft_value(draft, "note"),
        "raw_text": raw_text,
        "source": source,
        "source_message_id": message_id,
        "source_item_index": source_item_index,
        "source_channel_id": channel_id,
        "logged_by": logged_by,
        "created_at_utc": now,
        "updated_at_utc": now,
    }


def _work_row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["tags"] = json.loads(item.pop("tags_json") or "[]")
    except json.JSONDecodeError:
        item["tags"] = []
    return item


def _work_ai_suggestion_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("prompt_json", "response_json"):
        output_key = key.removesuffix("_json")
        try:
            item[output_key] = json.loads(item.pop(key) or "{}")
        except json.JSONDecodeError:
            item[output_key] = {}
    return item


def _work_ai_suggestion_title(suggestion_id: int, suggestion_kind: str, response: dict[str, Any]) -> str:
    if suggestion_kind == "capture_parse":
        outcome = str(response.get("outcome") or "review").replace("_", " ")
        return f"Work suggestion {suggestion_id}: {outcome}"
    return f"Work automation message {suggestion_id}"


def _work_ai_suggestion_body(response: dict[str, Any]) -> str:
    if response.get("message"):
        return str(response["message"])
    if response.get("question"):
        return f"Question: {response['question']}"
    if response.get("reason"):
        return f"Reason: {response['reason']}"
    items = response.get("items") or []
    if items:
        lines = ["Proposed work items:"]
        for item in items:
            lines.append(f"- {item.get('title', 'untitled')} ({item.get('priority', 'p2')})")
        return "\n".join(lines)
    return json.dumps(response, sort_keys=True, ensure_ascii=False)


def _work_state_lines(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- none"]
    lines = []
    for item in items:
        due = f" due {item['due_date']}" if item.get("due_date") else ""
        scheduled = f" scheduled {item['scheduled_date']}" if item.get("scheduled_date") else ""
        project = f" project:{item['project']}" if item.get("project") else ""
        status = f" status:{item['status']}" if item.get("status") != "open" else ""
        lines.append(
            f"- #{item['id']} [{str(item.get('priority') or 'p2').upper()}]{due}{scheduled}{project}{status}: {item['title']}"
        )
    return lines


def _work_md_row(record: dict[str, Any]) -> str:
    logged_at = str(record.get("updated_at_utc") or record.get("created_at_utc") or utc_now_iso()).replace("+00:00", "Z")
    title = str(
        record.get("title")
        or record.get("question")
        or record.get("reason")
        or record.get("raw_text")
        or ""
    ).replace("|", "\\|")
    return (
        f"| {logged_at} | {record.get('event')} | {record.get('id')} | "
        f"{record.get('priority') or ''} | {record.get('status') or ''} | "
        f"{title} | {record.get('due_date') or ''} |\n"
    )


def _snippet(text: str, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


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


def _ensure_work_schema(db: sqlite3.Connection) -> None:
    row = _fetchone(
        db,
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'work_items'",
        (),
    )
    if row is None:
        return
    table_sql = row[0] or ""
    columns = {
        "due_at": "TEXT",
        "scheduled_at": "TEXT",
        "next_followup_at": "TEXT",
        "followup_cadence_hours": "INTEGER",
        "snoozed_until_utc": "TEXT",
    }
    for name, column_type in columns.items():
        if name not in table_sql:
            db.execute(f"ALTER TABLE work_items ADD COLUMN {name} {column_type}")


def _ensure_review_schema(db: sqlite3.Connection) -> None:
    row = _fetchone(
        db,
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'review_items'",
        (),
    )
    if row is None:
        return
    table_sql = row[0] or ""
    columns = {
        "priority": "TEXT",
        "surface_count": "INTEGER NOT NULL DEFAULT 0",
        "last_surface_at": "TEXT",
        "automation_policy": "TEXT",
        "auto_process_reason": "TEXT",
    }
    for name, column_type in columns.items():
        if name not in table_sql:
            db.execute(f"ALTER TABLE review_items ADD COLUMN {name} {column_type}")
    db.execute("UPDATE review_items SET surface_count = 0 WHERE surface_count IS NULL")
    db.execute("UPDATE review_items SET priority = 'normal' WHERE priority IS NULL")
    db.execute(
        """
        UPDATE review_items
        SET automation_policy = CASE
            WHEN source_kind IN ('finance_parse_review', 'finance_review', 'health_review', 'family_review', 'legal_review', 'memory_review', 'durable_memory', 'identity_memory')
                THEN ?
            WHEN source_kind IN ('needs_answer', 'review_fallback', 'tracker_summary')
                THEN ?
            WHEN kind IN ('open_question', 'morning_question')
                THEN ?
            ELSE ?
        END
        WHERE automation_policy IS NULL OR automation_policy = ''
        """,
        (SENSITIVE_REVIEW, LOW_RISK_REVERSIBLE, LOW_RISK_REVERSIBLE, REQUIRES_APPROVAL),
    )


def _default_review_automation_policy(item: dict[str, Any]) -> str:
    if is_sensitive_review(item):
        return SENSITIVE_REVIEW
    source_kind = str(item.get("source_kind") or "")
    kind = str(item.get("kind") or "")
    if source_kind in {"needs_answer", "review_fallback", "tracker_summary"}:
        return LOW_RISK_REVERSIBLE
    if kind in {"open_question", "morning_question"}:
        return LOW_RISK_REVERSIBLE
    return REQUIRES_APPROVAL


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
    elif kind == "work":
        title = "Work Log"
        columns = "| Time (UTC) | Event | ID | Priority | Status | Title / Review | Due |\n|---|---|---:|---|---|---|---|\n"
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
