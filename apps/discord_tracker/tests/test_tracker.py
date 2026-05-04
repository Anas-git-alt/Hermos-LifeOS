from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import sys

APP_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = APP_DIR.parents[1]
SCRIPTS_DIR = ROOT_DIR / "scripts"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from bot import DiscordTracker
from ai_review import AIInputInterpreter, AIValidationPass
from config import TrackerConfig, is_owner_id, parse_owner_ids
from finance import finance_review_request, parse_finance_message
from hydration import HYDRATION_REACTIONS, parse_hydration_footer
from prayer import PRAYER_REACTIONS, parse_aladhan_timings, parse_prayer_footer
from process_finance_reviews import apply_agent_result, apply_resolutions, fetch_reviews
from process_review_fallback import Config as ReviewFallbackConfig, run_fallback
from process_work_reviews import (
    apply_agent_result as apply_work_agent_result,
    apply_resolutions as apply_work_resolutions,
    create_ai_suggestions as create_work_ai_suggestions,
    fetch_captures,
)
from review_automation import (
    AUTO_PROCESS_SAFE,
    LOW_RISK_REVERSIBLE,
    ReviewDigestBuilder,
    ReviewPrioritizer,
    SafeAutoProcessor,
)
from review_reports import build_morning_discord_summary, morning_review_candidates
from store import TrackerStore
from build_automation_health_report import build_report as build_automation_health_report
from summarize_finance_week import fetch_week
from summarize_tracker_day import fetch_finance, render
from work import WorkItemDraft, draft_parse_work_message, item_from_manual_text


ALADHAN_FIXTURE = {
    "code": 200,
    "status": "OK",
    "data": {
        "timings": {
            "Fajr": "04:44",
            "Sunrise": "06:14",
            "Dhuhr": "13:30",
            "Asr": "17:07",
            "Sunset": "20:35",
            "Maghrib": "20:35",
            "Isha": "21:55",
        }
    },
}


class _FakeDiscordMessage:
    def __init__(self, message_id: int):
        self.id = message_id
        self.reactions: list[str] = []

    async def add_reaction(self, emoji: str):
        self.reactions.append(emoji)


class _FakeDiscordChannel:
    id = 999

    def __init__(self):
        self.sent: list[str] = []
        self.embeds: list[object] = []
        self.messages: dict[int, _FakeDiscordMessage] = {}

    async def send(self, content=None, **kwargs):
        self.sent.append(content or "")
        embed = kwargs.get("embed")
        if embed is not None:
            self.embeds.append(embed)
        message = _FakeDiscordMessage(7000 + len(self.sent) + len(self.embeds))
        self.messages[message.id] = message
        return message

    async def fetch_message(self, message_id: int):
        return self.messages[message_id]


class _FakeAuthor:
    bot = False

    def __init__(self, author_id: int):
        self.id = author_id


class _FakeReference:
    def __init__(self, message_id: int):
        self.message_id = message_id


class _FakeReplyMessage:
    def __init__(self, message_id: int, channel: _FakeDiscordChannel, content: str, reference_id: int):
        self.id = message_id
        self.channel = channel
        self.content = content
        self.author = _FakeAuthor(123)
        self.reference = _FakeReference(reference_id)


class _FakeReactionPayload:
    def __init__(self, message_id: int, channel_id: int, emoji: str, user_id: int = 123):
        self.message_id = message_id
        self.channel_id = channel_id
        self.user_id = user_id
        self.emoji = emoji


def _tracker_config(root: Path) -> TrackerConfig:
    return TrackerConfig(
        discord_bot_token="token",
        discord_guild_id=None,
        discord_owner_ids=frozenset({123}),
        prayer_channel_name="prayer-tracker",
        hydration_channel_name="habits",
        finance_channel_name="finance-tracker",
        work_channel_name="work-tracker",
        daily_plan_channel_name="daily-plan",
        review_channel_name="daily-plan",
        lifeos_root=root,
        tracker_db=root / "tracker.db",
        hermes_home=root / ".hermes" / "profiles" / "lifeos",
        timezone="Africa/Casablanca",
        prayer_city="Casablanca",
        prayer_country="Morocco",
        prayer_method=21,
        prayer_close_nudge_minutes=10,
        hydration_start_hour=9,
        hydration_end_hour=22,
        hydration_interval_minutes=90,
        hydration_target_count=8,
        work_start_hour=14,
        work_end_hour=23,
        work_prep_lead_minutes=60,
        work_mid_shift_checkin_enabled=False,
        work_shutdown_review_enabled=True,
        work_reminder_lookahead_minutes=30,
        work_overdue_grace_minutes=15,
        work_ai_cmd="",
        work_automation_ai_cmd="",
        review_ai_cmd="",
        morning_review_enabled=True,
        morning_review_hour=7,
        morning_review_minute=40,
        review_item_expiry_hours=18,
    )


def _minimal_lint_jobs(*, include_fallback: bool = True) -> list[dict]:
    jobs = [
        {
            "id": "a1abddcdcf79",
            "prompt": (
                "Run scripts/build_discord_morning_summary.py. Hermes lifeos agent retries/refines. "
                "Overnight system status finance review result memory review result needs-answer "
                "Do not mention resolved finance reviews as blockers Prayer / Hydration. "
                "Do not call send_message."
            ),
            "deliver": "discord:#daily-plan",
            "schedule": {"expr": "35 7 * * *"},
            "enabled": True,
        },
        {
            "id": "4d661d5b4b5d",
            "prompt": "Run scripts/build_morning_report.py. Hermes lifeos agent retries/refines.",
            "deliver": "local",
            "schedule": {"expr": "30 7 * * *"},
            "enabled": True,
        },
        {
            "id": "finance-review-autoprocess",
            "prompt": "Run scripts/process_finance_reviews.py --all-open, then scripts/summarize_finance_day.py. AI-led review.",
            "deliver": "local",
            "schedule": {"expr": "0 1 * * *"},
            "enabled": True,
        },
        {
            "id": "work-review-autoprocess",
            "prompt": "Run scripts/process_work_reviews.py --all-open.",
            "deliver": "local",
            "schedule": {"expr": "15 1 * * *"},
            "enabled": True,
        },
        {
            "id": "a48c14ea917b",
            "prompt": "Use life-memory-review.",
            "skill": "life-memory-review",
            "skills": ["life-memory-review"],
            "deliver": "local",
            "schedule": {"expr": "10 2 * * *"},
            "enabled": True,
        },
        {"id": "180421089e9e", "prompt": "", "deliver": "local", "schedule": {"expr": "30 1 * * *"}, "enabled": True},
        {"id": "c70e18134a87", "prompt": "", "deliver": "local", "schedule": {"expr": "30 2 * * *"}, "enabled": True},
        {"id": "12df41197bb9", "prompt": "", "deliver": "local", "schedule": {"expr": "0 3 * * *"}, "enabled": True},
        {"id": "87eefd62d1c2", "prompt": "", "deliver": "local", "schedule": {"expr": "25 7 * * *"}, "enabled": True},
        {
            "id": "automation-health-weekly",
            "prompt": "Run scripts/build_automation_health_report.py.",
            "deliver": "local",
            "schedule": {"expr": "15 10 * * 5"},
            "enabled": True,
        },
    ]
    if include_fallback:
        jobs.append(
            {
                "id": "review-fallback-nightly",
                "prompt": "Run scripts/process_review_fallback.py YYYY-MM-DD.",
                "deliver": "local",
                "schedule": {"expr": "45 1 * * *"},
                "enabled": True,
            }
        )
    return jobs


class TrackerUnitTests(unittest.TestCase):
    def test_prayer_footer_parsing(self) -> None:
        footer = parse_prayer_footer("prayer:2026-04-30:Fajr:2026-04-30-fajr")
        self.assertIsNotNone(footer)
        self.assertEqual(footer.local_date, "2026-04-30")
        self.assertEqual(footer.prayer_name, "Fajr")
        self.assertEqual(footer.window_id, "2026-04-30-fajr")

    def test_hydration_footer_parsing(self) -> None:
        footer = parse_hydration_footer("hydration:2026-04-30:2026-04-30-0900")
        self.assertIsNotNone(footer)
        self.assertEqual(footer.local_date, "2026-04-30")
        self.assertEqual(footer.reminder_id, "2026-04-30-0900")

    def test_owner_validation(self) -> None:
        owners = parse_owner_ids("123, 456 789")
        self.assertTrue(is_owner_id(456, owners))
        self.assertFalse(is_owner_id(111, owners))

    def test_reaction_mapping(self) -> None:
        self.assertEqual(PRAYER_REACTIONS["✅"], "on_time")
        self.assertEqual(PRAYER_REACTIONS["🕒"], "late")
        self.assertEqual(PRAYER_REACTIONS["❌"], "missed")
        self.assertEqual(HYDRATION_REACTIONS["💧"], ("drink", 1))
        self.assertEqual(HYDRATION_REACTIONS["🥤"], ("large_drink", 2))
        self.assertEqual(HYDRATION_REACTIONS["💤"], ("snooze", 0))

    def test_aladhan_response_parsing(self) -> None:
        timings = parse_aladhan_timings(
            ALADHAN_FIXTURE,
            date(2026, 4, 30),
            "Africa/Casablanca",
        )
        self.assertEqual(timings["Fajr"].hour, 4)
        self.assertEqual(timings["Isha"].minute, 55)
        self.assertEqual(timings["Dhuhr"].tzinfo, ZoneInfo("Africa/Casablanca"))

    def test_finance_parser_expense_default_mad(self) -> None:
        result = parse_finance_message("spent 45 lunch")
        self.assertEqual(result.status, "parsed")
        entry = result.entries[0]
        self.assertEqual(entry.kind, "expense")
        self.assertEqual(entry.amount, Decimal("45"))
        self.assertEqual(entry.currency, "MAD")
        self.assertEqual(entry.amount_mad, Decimal("45"))
        self.assertEqual(entry.category, "eating_out")

    def test_finance_parser_subscription_non_mad(self) -> None:
        result = parse_finance_message("paid Netflix 12 USD")
        self.assertEqual(result.status, "parsed")
        entry = result.entries[0]
        self.assertEqual(entry.kind, "subscription")
        self.assertEqual(entry.amount, Decimal("12"))
        self.assertEqual(entry.currency, "USD")
        self.assertIsNone(entry.amount_mad)
        self.assertEqual(entry.category, "subscriptions")

    def test_finance_parser_savings_contribution(self) -> None:
        result = parse_finance_message("saved 300 emergency fund")
        self.assertEqual(result.status, "parsed")
        entry = result.entries[0]
        self.assertEqual(entry.kind, "savings_contribution")
        self.assertEqual(entry.category, "savings")
        self.assertEqual(entry.merchant, "emergency fund")

    def test_finance_parser_ambiguous_multi_amount(self) -> None:
        result = parse_finance_message("spent 20 groceries and 30 transport")
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.review_reason, "multiple_amounts")

    def test_finance_parser_multiline_entries(self) -> None:
        result = parse_finance_message("-300 dh wifi bill\n- 100 wife phone bill")
        self.assertEqual(result.status, "parsed")
        self.assertEqual(len(result.entries), 2)
        self.assertEqual([entry.amount for entry in result.entries], [Decimal("300"), Decimal("100")])
        self.assertEqual([entry.category for entry in result.entries], ["utilities", "utilities"])

    def test_daily_summary_omits_normal_finance(self) -> None:
        prayer = {name: None for name in ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")}
        hydration = {
            "total": 0,
            "drink_events": 0,
            "large_drink_events": 0,
            "manual_events": 0,
            "snoozes": 0,
            "skips": 0,
        }
        text = render("2026-04-30", prayer, hydration, 8)
        self.assertNotIn("## Finance", text)
        self.assertIn("promises to pay", text)


class TrackerStoreTests(unittest.TestCase):
    def test_hydration_count_update_and_logs(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                total = await store.log_hydration(
                    local_date="2026-04-30",
                    reminder_id="manual",
                    action="manual",
                    count_delta=2,
                    note="after walk",
                    message_id=None,
                    channel_id=None,
                    logged_by=123,
                )
                self.assertEqual(total, 2)
                self.assertEqual(await store.get_hydration_count("2026-04-30"), 2)
                self.assertTrue((root / "data" / "hydration" / "2026-04-30.jsonl").exists())
                self.assertTrue((root / "data" / "hydration" / "2026-04-30.md").exists())

        asyncio.run(run_case())

    def test_hydration_reaction_is_idempotent_per_reminder(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()

                first_total, first_created = await store.log_hydration_reaction(
                    local_date="2026-04-30",
                    reminder_id="2026-04-30-0900",
                    action="drink",
                    count_delta=1,
                    note="reaction",
                    message_id=222,
                    channel_id=333,
                    logged_by=123,
                )
                second_total, second_created = await store.log_hydration_reaction(
                    local_date="2026-04-30",
                    reminder_id="2026-04-30-0900",
                    action="large_drink",
                    count_delta=2,
                    note="reaction",
                    message_id=222,
                    channel_id=333,
                    logged_by=123,
                )

                self.assertTrue(first_created)
                self.assertFalse(second_created)
                self.assertEqual(first_total, 1)
                self.assertEqual(second_total, 1)
                self.assertEqual(await store.get_hydration_count("2026-04-30"), 1)
                jsonl_path = root / "data" / "hydration" / "2026-04-30.jsonl"
                self.assertEqual(len(jsonl_path.read_text().splitlines()), 1)

        asyncio.run(run_case())

    def test_prayer_duplicate_status_is_idempotent(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()

                first_created = await store.log_prayer(
                    local_date="2026-04-30",
                    prayer_name="Fajr",
                    window_id="2026-04-30-fajr",
                    status="on_time",
                    message_id=222,
                    channel_id=333,
                    logged_by=123,
                    window_end_utc=None,
                )
                second_created = await store.log_prayer(
                    local_date="2026-04-30",
                    prayer_name="Fajr",
                    window_id="2026-04-30-fajr",
                    status="on_time",
                    message_id=222,
                    channel_id=333,
                    logged_by=123,
                    window_end_utc=None,
                )

                self.assertTrue(first_created)
                self.assertFalse(second_created)
                jsonl_path = root / "data" / "prayer" / "2026-04-30.jsonl"
                self.assertEqual(len(jsonl_path.read_text().splitlines()), 1)

        asyncio.run(run_case())

    def test_finance_message_idempotent_and_summary(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                parsed = parse_finance_message("spent 45 lunch")
                first = await store.log_finance_message(
                    local_date="2026-04-30",
                    raw_text="spent 45 lunch",
                    parsed=parsed,
                    message_id=777,
                    channel_id=888,
                    channel_name="finance-tracker",
                    logged_by=123,
                )
                second = await store.log_finance_message(
                    local_date="2026-04-30",
                    raw_text="spent 45 lunch",
                    parsed=parsed,
                    message_id=777,
                    channel_id=888,
                    channel_name="finance-tracker",
                    logged_by=123,
                )

                self.assertTrue(first["created"])
                self.assertFalse(second["created"])
                self.assertEqual(first["status"], "parsed")
                self.assertEqual(second["status"], "duplicate")
                summary = await store.get_finance_day_summary("2026-04-30")
                self.assertEqual(summary["expense_mad"], "45")
                self.assertEqual(summary["by_category"]["eating_out"], "45")
                self.assertTrue((root / "data" / "finance" / "2026-04-30.jsonl").exists())
                self.assertTrue((root / "data" / "finance" / "2026-04-30.md").exists())
                self.assertTrue((root / "raw" / "captures" / "2026-04-30.md").exists())

        asyncio.run(run_case())

    def test_finance_review_edit_void_and_script_summary(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                review = await store.log_finance_message(
                    local_date="2026-04-30",
                    raw_text="spent 20 groceries and 30 transport",
                    parsed=parse_finance_message("spent 20 groceries and 30 transport"),
                    message_id=111,
                    channel_id=222,
                    channel_name="finance-tracker",
                    logged_by=123,
                )
                self.assertEqual(review["status"], "needs_review")
                reviews = await store.list_finance_reviews()
                self.assertEqual(len(reviews), 1)

                entry = parse_finance_message("spent 30 groceries").entries[0]
                resolved = await store.resolve_finance_review(review["review_id"], entry)
                self.assertIsNotNone(resolved)
                tx_id = resolved[0]["id"]
                summary = await store.get_finance_day_summary("2026-04-30")
                self.assertEqual(summary["expense_mad"], "30")
                self.assertEqual(summary["needs_review_count"], 0)

                edit_entry = parse_finance_message("spent 35 groceries").entries[0]
                edited = await store.edit_finance_transaction(tx_id, edit_entry)
                self.assertIsNotNone(edited)
                summary = await store.get_finance_day_summary("2026-04-30")
                self.assertEqual(summary["expense_mad"], "35")

                with store._connect() as con:
                    finance = fetch_finance(con, "2026-04-30")
                self.assertEqual(finance["expense_mad"], 35.0)
                self.assertEqual(finance["by_category"]["groceries"], 35.0)

                voided = await store.void_finance_item(tx_id)
                self.assertEqual(voided, {"kind": "transaction", "id": tx_id})
                summary = await store.get_finance_day_summary("2026-04-30")
                self.assertEqual(summary["expense_mad"], "0")

        asyncio.run(run_case())

    def test_finance_recurring_and_savings_derivatives(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                await store.log_finance_message(
                    local_date="2026-04-30",
                    raw_text="paid Netflix 12 USD",
                    parsed=parse_finance_message("paid Netflix 12 USD"),
                    message_id=501,
                    channel_id=888,
                    channel_name="finance-tracker",
                    logged_by=123,
                )
                await store.log_finance_message(
                    local_date="2026-04-30",
                    raw_text="saved 300 emergency fund",
                    parsed=parse_finance_message("saved 300 emergency fund"),
                    message_id=502,
                    channel_id=888,
                    channel_name="finance-tracker",
                    logged_by=123,
                )

                with store._connect() as con:
                    recurring = con.execute(
                        "SELECT name, kind, amount, currency FROM finance_recurring_items"
                    ).fetchone()
                    goal = con.execute(
                        "SELECT name, current_amount, current_currency FROM finance_savings_goals"
                    ).fetchone()
                self.assertEqual(recurring, ("Netflix", "subscription", "12", "USD"))
                self.assertEqual(goal, ("emergency fund", "300", "MAD"))

        asyncio.run(run_case())

    def test_finance_hermis_review_capture_and_multientry_resolution(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                review = await store.log_finance_message(
                    local_date="2026-04-30",
                    raw_text="-300 dh wifi bill\n- 100 wife phone bill",
                    parsed=finance_review_request(),
                    message_id=901,
                    channel_id=888,
                    channel_name="finance-tracker",
                    logged_by=123,
                )
                self.assertEqual(review["status"], "needs_review")
                self.assertEqual(await store.get_finance_day_summary("2026-04-30"), {
                    "label": "2026-04-30",
                    "transaction_count": 0,
                    "expense_mad": "0",
                    "income_mad": "0",
                    "savings_mad": "0",
                    "transfer_mad": "0",
                    "by_category": {},
                    "non_mad": [],
                    "needs_review_count": 1,
                })

                parsed = parse_finance_message("-300 dh wifi bill\n- 100 wife phone bill")
                records = await store.resolve_finance_review(review["review_id"], parsed.entries)
                self.assertIsNotNone(records)
                self.assertEqual(len(records), 2)
                summary = await store.get_finance_day_summary("2026-04-30")
                self.assertEqual(summary["transaction_count"], 2)
                self.assertEqual(summary["expense_mad"], "400")
                self.assertEqual(summary["by_category"]["utilities"], "400")

        asyncio.run(run_case())

    def test_finance_processor_resolves_clear_reviews_and_weekly_report_sees_them(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                await store.log_finance_message(
                    local_date="2026-04-30",
                    raw_text="-300 dh wifi bill\n-100 dh wife phone bill",
                    parsed=finance_review_request(),
                    message_id=777,
                    channel_id=888,
                    channel_name="finance-tracker",
                    logged_by=123,
                )
                with store._connect() as con:
                    reviews = fetch_reviews(con, "2026-04-30", False)
                resolved, questions = apply_agent_result(
                    {
                        "resolved": [
                            {
                                "review_id": reviews[0]["id"],
                                "entries": [
                                    {
                                        "kind": "bill",
                                        "amount": "300",
                                        "currency": "MAD",
                                        "category": "utilities",
                                        "merchant": "wifi",
                                        "description": "wifi bill",
                                    },
                                    {
                                        "kind": "bill",
                                        "amount": "100",
                                        "currency": "MAD",
                                        "category": "utilities",
                                        "merchant": "wife phone",
                                        "description": "wife phone bill",
                                    },
                                ],
                            }
                        ],
                        "questions": [],
                    },
                    {int(review["id"]): review for review in reviews},
                )
                self.assertEqual(len(questions), 0)
                applied = await apply_resolutions(store, resolved, False)
                self.assertEqual(len(applied), 1)
                self.assertEqual(len(applied[0]["transaction_ids"]), 2)
                with store._connect() as con:
                    weekly = fetch_week(con, "2026-04-24", "2026-04-30")
                self.assertEqual(weekly["expense_mad"], 400.0)
                self.assertEqual(weekly["open_reviews"], [])

        asyncio.run(run_case())

    def test_finance_processor_uses_ai_json_for_multiline_review(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                await store.log_finance_message(
                    local_date="2026-05-02",
                    raw_text="-20 glovo prime subscription (2nd account)\n"
                    "-10 cash moul msemen (3 msemna, 3 batbouta normal, 4 batbouta 3amra)\n"
                    "-8 cash (2 khobza smida, 6 2x foure chocolat)\n"
                    "-1 cash n3na3",
                    parsed=finance_review_request(),
                    message_id=778,
                    channel_id=888,
                    channel_name="finance-tracker",
                    logged_by=123,
                )
                with store._connect() as con:
                    reviews = fetch_reviews(con, "2026-05-02", False)
                resolved, questions = apply_agent_result(
                    {
                        "resolved": [
                            {
                                "review_id": reviews[0]["id"],
                                "entries": [
                                    {
                                        "kind": "subscription",
                                        "amount": "20",
                                        "currency": "MAD",
                                        "category": "subscriptions",
                                        "merchant": "Glovo Prime",
                                        "description": "Glovo Prime subscription for second account",
                                    },
                                    {
                                        "kind": "expense",
                                        "amount": "10",
                                        "currency": "MAD",
                                        "category": "groceries",
                                        "merchant": "moul msemen",
                                        "description": "cash breakfast breads from moul msemen",
                                    },
                                    {
                                        "kind": "expense",
                                        "amount": "8",
                                        "currency": "MAD",
                                        "category": "groceries",
                                        "merchant": "bakery",
                                        "description": "cash bread and chocolate pastries",
                                    },
                                    {
                                        "kind": "expense",
                                        "amount": "1",
                                        "currency": "MAD",
                                        "category": "groceries",
                                        "merchant": "n3na3",
                                        "description": "cash mint",
                                    },
                                ],
                            }
                        ],
                        "questions": [],
                    },
                    {int(review["id"]): review for review in reviews},
                )
                self.assertEqual(questions, [])
                applied = await apply_resolutions(store, resolved, False)
                self.assertEqual(len(applied[0]["transaction_ids"]), 4)
                summary = await store.get_finance_day_summary("2026-05-02")
                self.assertEqual(summary["expense_mad"], "39")
                self.assertEqual(summary["needs_review_count"], 0)

        asyncio.run(run_case())

    def test_finance_processor_rejects_malformed_ai_json_without_guessing(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                await store.log_finance_message(
                    local_date="2026-04-30",
                    raw_text="spent something unclear",
                    parsed=finance_review_request(),
                    message_id=779,
                    channel_id=888,
                    channel_name="finance-tracker",
                    logged_by=123,
                )
                with store._connect() as con:
                    reviews = fetch_reviews(con, "2026-04-30", False)
                reviews_by_id = {int(review["id"]): review for review in reviews}
                resolved, questions = apply_agent_result(
                    {"resolved": [{"review_id": reviews[0]["id"], "entries": [{"amount": "-5"}]}]},
                    reviews_by_id,
                )
                self.assertEqual(resolved, [])
                self.assertEqual(len(questions), 1)
                applied = await apply_resolutions(store, resolved, False)
                self.assertEqual(applied, [])
                summary = await store.get_finance_day_summary("2026-04-30")
                self.assertEqual(summary["transaction_count"], 0)
                self.assertEqual(summary["needs_review_count"], 1)

                resolved, questions = apply_agent_result({"resolved": [], "questions": []}, reviews_by_id)
                self.assertEqual(resolved, [])
                self.assertEqual(len(questions), 1)

        asyncio.run(run_case())

    def test_finance_review_can_resolve_after_void_same_message(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                first = await store.log_finance_message(
                    local_date="2026-04-30",
                    raw_text="65 dh phone bill",
                    parsed=parse_finance_message("65 dh phone bill"),
                    message_id=333,
                    channel_id=888,
                    channel_name="finance-tracker",
                    logged_by=123,
                )
                await store.void_finance_item(first["transaction_ids"][0])
                review = await store.log_finance_message(
                    local_date="2026-04-30",
                    raw_text="65 dh phone bill",
                    parsed=finance_review_request(),
                    message_id=333,
                    channel_id=888,
                    channel_name="finance-tracker",
                    logged_by=123,
                )
                records = await store.resolve_finance_review(
                    review["review_id"],
                    parse_finance_message("65 dh phone bill").entries,
                )
                self.assertIsNotNone(records)
                self.assertEqual(records[0]["source_item_index"], 1)
                summary = await store.get_finance_day_summary("2026-04-30")
                self.assertEqual(summary["expense_mad"], "65")

        asyncio.run(run_case())

    def test_work_capture_saves_raw_without_creating_confirmed_items(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                raw_text = "todo: send client update due:2026-05-04 p1 project:Hermis"
                result = await store.log_work_capture(
                    local_date="2026-05-03",
                    raw_text=raw_text,
                    draft_parse=draft_parse_work_message(raw_text, today=date(2026, 5, 3)),
                    message_id=1001,
                    channel_id=2002,
                    channel_name="work-tracker",
                    logged_by=123,
                )
                self.assertTrue(result["created"])
                with store._connect() as con:
                    capture = con.execute(
                        """
                        SELECT raw_text, source, source_message_id, source_channel_id,
                               draft_parse_json, review_status
                        FROM work_captures
                        """
                    ).fetchone()
                    item_count = con.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]
                self.assertEqual(capture[0], raw_text)
                self.assertEqual(capture[1], "discord")
                self.assertEqual(capture[2], 1001)
                self.assertEqual(capture[3], 2002)
                self.assertEqual(json.loads(capture[4])["status"], "draft_parse")
                self.assertEqual(capture[5], "unreviewed")
                self.assertEqual(item_count, 0)
                raw_capture_file = root / "raw" / "captures" / "2026-05-03.md"
                self.assertIn(raw_text, raw_capture_file.read_text(encoding="utf-8"))

        asyncio.run(run_case())

    def test_work_messy_capture_does_not_disappear(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                raw_text = "ugh maybe that client thing??"
                await store.log_work_capture(
                    local_date="2026-05-03",
                    raw_text=raw_text,
                    draft_parse=draft_parse_work_message(raw_text, today=date(2026, 5, 3)),
                    message_id=1002,
                    channel_id=2002,
                    channel_name="work-tracker",
                    logged_by=123,
                )
                reviews = await store.list_work_reviews()
                self.assertEqual(len(reviews), 1)
                self.assertEqual(reviews[0]["raw_text"], raw_text)
                self.assertEqual(reviews[0]["review_status"], "unreviewed")

        asyncio.run(run_case())

    def test_work_one_capture_can_become_multiple_confirmed_items(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                raw_text = "- send proposal\n- book kickoff call"
                await store.log_work_capture(
                    local_date="2026-05-03",
                    raw_text=raw_text,
                    draft_parse=draft_parse_work_message(raw_text, today=date(2026, 5, 3)),
                    message_id=1003,
                    channel_id=2002,
                    channel_name="work-tracker",
                    logged_by=123,
                )
                with store._connect() as con:
                    captures = fetch_captures(con, "2026-05-03", False)
                capture_id = captures[0]["id"]
                confirmed, ignored, questions = apply_work_agent_result(
                    {
                        "confirmed": [
                            {
                                "capture_id": capture_id,
                                "items": [
                                    {"title": "Send proposal", "priority": "p1", "status": "open"},
                                    {"title": "Book kickoff call", "priority": "p2", "status": "open"},
                                ],
                            }
                        ],
                        "ignored": [],
                        "questions": [],
                    },
                    {capture_id: captures[0]},
                )
                self.assertEqual(ignored, [])
                self.assertEqual(questions, [])
                applied, applied_ignored, applied_questions = await apply_work_resolutions(
                    store, confirmed, ignored, questions, False
                )
                self.assertEqual(applied_ignored, [])
                self.assertEqual(applied_questions, [])
                self.assertEqual(len(applied[0]["item_ids"]), 2)
                active = await store.list_work_items("active")
                self.assertEqual([item["title"] for item in active], ["Send proposal", "Book kickoff call"])

        asyncio.run(run_case())

    def test_work_unclear_capture_becomes_clarification_question(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                raw_text = "deal with the thing"
                await store.log_work_capture(
                    local_date="2026-05-03",
                    raw_text=raw_text,
                    draft_parse=draft_parse_work_message(raw_text, today=date(2026, 5, 3)),
                    message_id=1004,
                    channel_id=2002,
                    channel_name="work-tracker",
                    logged_by=123,
                )
                with store._connect() as con:
                    captures = fetch_captures(con, "2026-05-03", False)
                capture_id = captures[0]["id"]
                confirmed, ignored, questions = apply_work_agent_result(
                    {"confirmed": [], "ignored": [], "questions": [{"capture_id": capture_id, "question": "Which thing should this refer to?"}]},
                    {capture_id: captures[0]},
                )
                await apply_work_resolutions(store, confirmed, ignored, questions, False)
                reviews = await store.list_work_reviews()
                self.assertEqual(reviews[0]["review_status"], "clarification")
                self.assertEqual(reviews[0]["clarification_question"], "Which thing should this refer to?")
                self.assertEqual(await store.list_work_items("active"), [])

        asyncio.run(run_case())

    def test_work_ignored_capture_requires_explicit_reason(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                raw_text = "not actually work, just venting"
                await store.log_work_capture(
                    local_date="2026-05-03",
                    raw_text=raw_text,
                    draft_parse=draft_parse_work_message(raw_text, today=date(2026, 5, 3)),
                    message_id=1005,
                    channel_id=2002,
                    channel_name="work-tracker",
                    logged_by=123,
                )
                with store._connect() as con:
                    captures = fetch_captures(con, "2026-05-03", False)
                capture_id = captures[0]["id"]
                confirmed, ignored, questions = apply_work_agent_result(
                    {"confirmed": [], "ignored": [{"capture_id": capture_id, "reason": ""}], "questions": []},
                    {capture_id: captures[0]},
                )
                self.assertEqual(confirmed, [])
                self.assertEqual(ignored, [])
                self.assertEqual(len(questions), 1)

                confirmed, ignored, questions = apply_work_agent_result(
                    {"confirmed": [], "ignored": [{"capture_id": capture_id, "reason": "not an actionable work item"}], "questions": []},
                    {capture_id: captures[0]},
                )
                await apply_work_resolutions(store, confirmed, ignored, questions, False)
                with store._connect() as con:
                    row = con.execute(
                        "SELECT review_status, ignore_reason FROM work_captures WHERE id = ?",
                        (capture_id,),
                    ).fetchone()
                    item_count = con.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]
                self.assertEqual(row, ("ignored", "not an actionable work item"))
                self.assertEqual(item_count, 0)

        asyncio.run(run_case())

    def test_work_manual_add_is_explicit_confirmation(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                raw_text = "todo: send update due:2026-05-03 p1"
                result = await store.add_manual_work_items(
                    local_date="2026-05-03",
                    raw_text=raw_text,
                    drafts=item_from_manual_text(raw_text, today=date(2026, 5, 3)),
                    draft_parse=draft_parse_work_message(raw_text, today=date(2026, 5, 3)),
                    message_id=1006,
                    channel_id=2002,
                    channel_name="daily-plan",
                    logged_by=123,
                )
                self.assertEqual(result["status"], "confirmed")
                self.assertEqual(len(result["item_ids"]), 1)
                active = await store.list_work_items("active")
                self.assertEqual(active[0]["title"], "send update")

        asyncio.run(run_case())

    def test_work_ai_draft_created_but_not_confirmed_until_accept(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                raw_text = "send client update"
                capture = await store.log_work_capture(
                    local_date="2026-05-03",
                    raw_text=raw_text,
                    draft_parse=draft_parse_work_message(raw_text, today=date(2026, 5, 3)),
                    message_id=3001,
                    channel_id=2002,
                    channel_name="work-tracker",
                    logged_by=123,
                )
                suggestion_id = await store.create_work_ai_suggestion(
                    suggestion_kind="capture_parse",
                    source_type="capture",
                    source_id=capture["capture_id"],
                    local_date="2026-05-03",
                    prompt={"raw_text": raw_text},
                    response={
                        "outcome": "confirmed",
                        "confidence": "high",
                        "review_reason": "clear_action",
                        "items": [{"title": "Send client update", "priority": "p1", "status": "open"}],
                    },
                )
                with store._connect() as con:
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM work_items").fetchone()[0], 0)
                    self.assertEqual(con.execute("SELECT status FROM work_ai_suggestions").fetchone()[0], "pending")
                result = await store.accept_work_ai_suggestion(suggestion_id)
                self.assertEqual(result["action"], "confirmed")
                active = await store.list_work_items("active")
                self.assertEqual([item["title"] for item in active], ["Send client update"])

        asyncio.run(run_case())

    def test_work_ai_correction_keeps_old_and_creates_new_pending(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                old_id = await store.create_work_ai_suggestion(
                    suggestion_kind="capture_parse",
                    source_type="capture",
                    source_id=10,
                    local_date="2026-05-03",
                    prompt={"raw_text": "fix thing"},
                    response={"outcome": "questions", "question": "Which thing?", "confidence": "low", "review_reason": "unclear"},
                )
                self.assertTrue(await store.mark_work_ai_suggestion_corrected(old_id, "It means staging login."))
                new_id = await store.create_work_ai_suggestion(
                    suggestion_kind="capture_parse",
                    source_type="capture",
                    source_id=10,
                    local_date="2026-05-03",
                    prompt={"raw_text": "fix thing", "correction_note": "It means staging login."},
                    response={
                        "outcome": "confirmed",
                        "items": [{"title": "Fix staging login", "priority": "p1", "status": "open"}],
                    },
                    supersedes_suggestion_id=old_id,
                )
                old = await store.get_work_ai_suggestion(old_id)
                new = await store.get_work_ai_suggestion(new_id)
                self.assertEqual(old["status"], "corrected")
                self.assertEqual(old["reviewer_note"], "It means staging login.")
                self.assertEqual(new["status"], "pending")
                self.assertEqual(new["supersedes_suggestion_id"], old_id)

        asyncio.run(run_case())

    def test_work_ai_reject_requires_reason(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                suggestion_id = await store.create_work_ai_suggestion(
                    suggestion_kind="capture_parse",
                    source_type="capture",
                    source_id=1,
                    local_date="2026-05-03",
                    prompt={},
                    response={"outcome": "ignored", "reason": "noise"},
                )
                with self.assertRaises(ValueError):
                    await store.reject_work_ai_suggestion(suggestion_id, "")
                self.assertTrue(await store.reject_work_ai_suggestion(suggestion_id, "wrong capture"))
                suggestion = await store.get_work_ai_suggestion(suggestion_id)
                self.assertEqual(suggestion["status"], "rejected")

        asyncio.run(run_case())

    def test_work_ai_suggestion_can_split_one_capture_on_accept(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                raw_text = "send proposal and book kickoff"
                capture = await store.log_work_capture(
                    local_date="2026-05-03",
                    raw_text=raw_text,
                    draft_parse=draft_parse_work_message(raw_text, today=date(2026, 5, 3)),
                    message_id=3002,
                    channel_id=2002,
                    channel_name="work-tracker",
                    logged_by=123,
                )
                suggestion_id = await store.create_work_ai_suggestion(
                    suggestion_kind="capture_parse",
                    source_type="capture",
                    source_id=capture["capture_id"],
                    local_date="2026-05-03",
                    prompt={"raw_text": raw_text},
                    response={
                        "outcome": "confirmed",
                        "items": [
                            {"title": "Send proposal", "priority": "p1", "status": "open"},
                            {"title": "Book kickoff", "priority": "p2", "status": "open"},
                        ],
                    },
                )
                result = await store.accept_work_ai_suggestion(suggestion_id)
                self.assertEqual(len(result["item_ids"]), 2)
                active = await store.list_work_items("active")
                self.assertEqual([item["title"] for item in active], ["Send proposal", "Book kickoff"])

        asyncio.run(run_case())

    def test_nightly_ai_suggestions_are_not_duplicated(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                capture = await store.log_work_capture(
                    local_date="2026-05-03",
                    raw_text="send update",
                    draft_parse=draft_parse_work_message("send update", today=date(2026, 5, 3)),
                    message_id=3010,
                    channel_id=2002,
                    channel_name="work-tracker",
                    logged_by=123,
                )
                captures_by_id = {capture["capture_id"]: {"id": capture["capture_id"], "local_date": "2026-05-03", "raw_text": "send update"}}
                confirmed = [(capture["capture_id"], (WorkItemDraft(title="Send update", priority="p1"),))]
                first = await create_work_ai_suggestions(store, captures_by_id, confirmed, [], [], False)
                second = await create_work_ai_suggestions(store, captures_by_id, confirmed, [], [], False)
                self.assertIsInstance(first[0]["suggestion_id"], int)
                self.assertEqual(second[0]["suggestion_id"], "existing")
                suggestions = await store.list_work_ai_suggestions("pending")
                self.assertEqual(len(suggestions), 1)

        asyncio.run(run_case())

    def test_work_ai_question_accept_opens_clarification(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                capture = await store.log_work_capture(
                    local_date="2026-05-03",
                    raw_text="fix that thing",
                    draft_parse=draft_parse_work_message("fix that thing", today=date(2026, 5, 3)),
                    message_id=3003,
                    channel_id=2002,
                    channel_name="work-tracker",
                    logged_by=123,
                )
                suggestion_id = await store.create_work_ai_suggestion(
                    suggestion_kind="capture_parse",
                    source_type="capture",
                    source_id=capture["capture_id"],
                    local_date="2026-05-03",
                    prompt={},
                    response={"outcome": "questions", "question": "Which thing did you mean?"},
                )
                await store.accept_work_ai_suggestion(suggestion_id)
                questions = await store.work_clarifications()
                self.assertEqual(questions[0]["question"], "Which thing did you mean?")

        asyncio.run(run_case())

    def test_work_prep_start_shutdown_automation_send_once_per_day(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                for kind in ("work_prep", "work_start", "work_shutdown"):
                    first = await store.record_work_automation_event(
                        kind=kind,
                        local_date="2026-05-03",
                        reminder_id=f"{kind}-2026-05-03",
                        payload={"kind": kind},
                    )
                    second = await store.record_work_automation_event(
                        kind=kind,
                        local_date="2026-05-03",
                        reminder_id=f"{kind}-2026-05-03",
                        payload={"kind": kind},
                    )
                    self.assertTrue(first)
                    self.assertFalse(second)
                events = await store.work_automation_status("2026-05-03")
                self.assertEqual(len(events), 3)

        asyncio.run(run_case())

    def test_work_automation_uses_ai_text_when_valid(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                bot = DiscordTracker(_tracker_config(root), store)
                channel = _FakeDiscordChannel()

                async def fake_named_channel(_name):
                    return channel

                async def fake_ai(_prompt, *, automation):
                    return {"message": "AI says: start with #42 for 10 minutes.", "confidence": "high", "review_reason": "clear_next_action"}

                bot._named_channel = fake_named_channel
                bot._run_work_ai_json = fake_ai
                await bot._send_work_automation_message(
                    kind="work_start",
                    local_date="2026-05-03",
                    reminder_id="start-2026-05-03",
                    text="Fallback start plan",
                    payload={"first_action": {"id": 42, "title": "Send update"}},
                )
                self.assertEqual(channel.sent[0], "AI says: start with #42 for 10 minutes.")
                suggestions = await store.list_work_ai_suggestions("pending")
                self.assertEqual(suggestions[0]["suggestion_kind"], "automation_message")
                events = await store.work_automation_status("2026-05-03")
                self.assertEqual(len(events), 1)

        asyncio.run(run_case())

    def test_work_automation_falls_back_when_ai_fails(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                bot = DiscordTracker(_tracker_config(root), store)
                channel = _FakeDiscordChannel()

                async def fake_named_channel(_name):
                    return channel

                async def fake_ai(_prompt, *, automation):
                    raise RuntimeError("AI unavailable")

                bot._named_channel = fake_named_channel
                bot._run_work_ai_json = fake_ai
                await bot._send_work_automation_message(
                    kind="work_start",
                    local_date="2026-05-03",
                    reminder_id="start-2026-05-03",
                    text="Fallback start plan",
                    payload={"first_action": None},
                )
                self.assertEqual(channel.sent[0], "Fallback start plan")
                suggestions = await store.list_work_ai_suggestions("pending")
                self.assertEqual(suggestions[0]["confidence"], "fallback")
                with store._connect() as con:
                    payload = json.loads(con.execute("SELECT payload_json FROM work_automation_events").fetchone()[0])
                self.assertEqual(payload["message_source"], "fallback")

        asyncio.run(run_case())

    def test_work_due_reminder_idempotency_and_restart_safe(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                result = await store.add_manual_work_items(
                    local_date="2026-05-03",
                    raw_text="status update",
                    drafts=[WorkItemDraft(title="Send status update", priority="p1", due_date="2026-05-03", due_at="15:00", effort_minutes=20)],
                    draft_parse=draft_parse_work_message("status update", today=date(2026, 5, 3)),
                    message_id=2001,
                    channel_id=2002,
                    channel_name="work-tracker",
                    logged_by=123,
                )
                now_local = datetime(2026, 5, 3, 14, 45, tzinfo=ZoneInfo("Africa/Casablanca"))
                items = await store.work_due_reminder_items(local_date="2026-05-03", now_local=now_local, lookahead_minutes=30)
                self.assertEqual([item["id"] for item in items], result["item_ids"])
                reminder_id = f"due-{result['item_ids'][0]}-2026-05-03-15:00"
                self.assertTrue(await store.record_work_automation_event(kind="work_due", local_date="2026-05-03", reminder_id=reminder_id, payload={}))
                self.assertFalse(await store.record_work_automation_event(kind="work_due", local_date="2026-05-03", reminder_id=reminder_id, payload={}))

        asyncio.run(run_case())

    def test_work_overdue_creates_blocker_prompt_without_spam(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                result = await store.add_manual_work_items(
                    local_date="2026-05-03",
                    raw_text="send status update",
                    drafts=[WorkItemDraft(title="Send status update", priority="p1", due_date="2026-05-03", due_at="14:00")],
                    draft_parse=draft_parse_work_message("send status update", today=date(2026, 5, 3)),
                    message_id=2011,
                    channel_id=2002,
                    channel_name="work-tracker",
                    logged_by=123,
                )
                now_local = datetime(2026, 5, 3, 14, 20, tzinfo=ZoneInfo("Africa/Casablanca"))
                overdue = await store.overdue_work_items(local_date="2026-05-03", now_local=now_local, grace_minutes=15)
                self.assertEqual([item["id"] for item in overdue], result["item_ids"])
                item_id = result["item_ids"][0]
                self.assertTrue(await store.create_work_blocker_prompt(item_id=item_id, local_date="2026-05-03", reason="overdue"))
                self.assertFalse(await store.create_work_blocker_prompt(item_id=item_id, local_date="2026-05-03", reason="overdue"))

        asyncio.run(run_case())

    def test_work_waiting_followup_and_snooze(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                result = await store.add_manual_work_items(
                    local_date="2026-05-03",
                    raw_text="wait for reply",
                    drafts=[WorkItemDraft(title="Wait for Youssef reply", priority="p2")],
                    draft_parse=draft_parse_work_message("wait for reply", today=date(2026, 5, 3)),
                    message_id=2021,
                    channel_id=2002,
                    channel_name="work-tracker",
                    logged_by=123,
                )
                item_id = result["item_ids"][0]
                await store.set_work_item_status(item_id, "waiting", local_date="2026-05-03", reason="waiting on Youssef")
                with store._connect() as con:
                    con.execute("UPDATE work_items SET next_followup_at = ? WHERE id = ?", ("2026-05-03T10:00:00+00:00", item_id))
                    con.commit()
                due = await store.waiting_followup_items(datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc))
                self.assertEqual([item["id"] for item in due], [item_id])
                await store.snooze_work_item(item_id, datetime(2026, 5, 3, 13, 0, tzinfo=timezone.utc), local_date="2026-05-03")
                snoozed = await store.waiting_followup_items(datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc))
                self.assertEqual(snoozed, [])

        asyncio.run(run_case())

    def test_work_clarification_question_text_is_available_for_automation(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                await store.log_work_capture(
                    local_date="2026-05-03",
                    raw_text="fix that thing from yesterday",
                    draft_parse=draft_parse_work_message("fix that thing from yesterday", today=date(2026, 5, 3)),
                    message_id=2031,
                    channel_id=2002,
                    channel_name="work-tracker",
                    logged_by=123,
                )
                with store._connect() as con:
                    capture_id = con.execute("SELECT id FROM work_captures").fetchone()[0]
                await store.ask_work_clarification(capture_id, "Which staging issue did you mean?")
                questions = await store.work_clarifications()
                self.assertEqual(questions[0]["question"], "Which staging issue did you mean?")
                self.assertIn("fix that thing", questions[0]["raw_text"])
                self.assertTrue(await store.answer_work_clarification(capture_id, "The staging login timeout."))
                reviews = await store.list_work_reviews()
                self.assertEqual(reviews[0]["review_status"], "unreviewed")

        asyncio.run(run_case())

    def test_generic_review_item_and_discord_binding_are_durable(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                item = await store.create_review_item(
                    kind="morning_question",
                    title="Clarify morning item",
                    body="Which commitment should move first?",
                    source_path="reports/morning/2026-05-04.md",
                    source_record_id="q1",
                    source_kind="morning_report",
                    missing_context=["priority"],
                )
                binding = await store.bind_discord_message(
                    review_item_id=item["id"],
                    discord_message_id=444,
                    discord_channel_id=555,
                    source_kind=item["source_kind"],
                    source_id=item["source_record_id"],
                    source_path=item["source_path"],
                    action_on_reply="answer_question",
                )
                loaded = await store.get_review_item(item["id"])
                self.assertEqual(loaded["discord_message_id"], 444)
                self.assertEqual(binding["review_item_id"], item["id"])
                self.assertTrue((root / "data" / "review").exists())
                self.assertIn(item["id"], (root / "state" / "review-items.md").read_text(encoding="utf-8"))

        asyncio.run(run_case())

    def test_review_cards_are_posted_and_bound_to_discord_messages(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                bot = DiscordTracker(_tracker_config(root), store)
                channel = _FakeDiscordChannel()
                item = await store.create_review_item(
                    kind="open_question",
                    title="Open question",
                    body="What should Hermis do with this?",
                    source_path="inbox/needs-answer/2026-05-04.md",
                    source_record_id="q1",
                    source_kind="needs_answer",
                )
                message = await bot._post_review_card(channel, item)
                binding = await store.get_discord_binding(message.id, channel.id)
                loaded = await store.get_review_item(item["id"])
                self.assertEqual(binding["review_item_id"], item["id"])
                self.assertEqual(loaded["discord_message_id"], message.id)
                self.assertIn("✅", message.reactions)
                self.assertIn("❌", message.reactions)
                self.assertIn("❓", message.reactions)

        asyncio.run(run_case())

    def test_review_reactions_approve_reject_and_request_clarification(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                bot = DiscordTracker(_tracker_config(root), store)
                channel = _FakeDiscordChannel()

                approve_item = await store.create_review_item(kind="open_question", title="Approve me", body="ok?")
                approve_message = await bot._post_review_card(channel, approve_item)
                approve_binding = await store.get_discord_binding(approve_message.id, channel.id)
                await bot._handle_review_reaction(_FakeReactionPayload(approve_message.id, channel.id, "✅"), channel, approve_binding, "✅")
                self.assertEqual((await store.get_review_item(approve_item["id"]))["status"], "approved")

                reject_item = await store.create_review_item(kind="open_question", title="Reject me", body="ok?", source_record_id="reject")
                reject_message = await bot._post_review_card(channel, reject_item)
                reject_binding = await store.get_discord_binding(reject_message.id, channel.id)
                await bot._handle_review_reaction(_FakeReactionPayload(reject_message.id, channel.id, "❌"), channel, reject_binding, "❌")
                self.assertEqual((await store.get_review_item(reject_item["id"]))["status"], "rejected")

                clarify_item = await store.create_review_item(kind="open_question", title="Clarify me", body="ok?", source_record_id="clarify")
                clarify_message = await bot._post_review_card(channel, clarify_item)
                clarify_binding = await store.get_discord_binding(clarify_message.id, channel.id)
                await bot._handle_review_reaction(_FakeReactionPayload(clarify_message.id, channel.id, "❓"), channel, clarify_binding, "❓")
                self.assertEqual((await store.get_review_item(clarify_item["id"]))["status"], "needs_clarification")

        asyncio.run(run_case())

    def test_reply_to_review_card_runs_ai_validation_and_attaches_detail(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                bot = DiscordTracker(_tracker_config(root), store)
                channel = _FakeDiscordChannel()
                item = await store.create_review_item(kind="open_question", title="Needs answer", body="Which project?")
                card = await bot._post_review_card(channel, item)

                async def fake_interpreter(_prompt: str):
                    return {
                        "intent": "answer_question",
                        "answers": ["the staging login project"],
                        "proposed_update": {"answer": "the staging login project"},
                        "confidence": "low",
                        "missing_context": ["which deadline this affects"],
                    }

                bot.input_interpreter = AIInputInterpreter(fake_interpreter)
                bot.validation_pass = AIValidationPass()
                reply = _FakeReplyMessage(8800, channel, "It was the staging login project.", card.id)
                handled = await bot._maybe_handle_review_reply(reply, reply.content)
                loaded = await store.get_review_item(item["id"])
                self.assertTrue(handled)
                self.assertEqual(loaded["status"], "needs_clarification")
                self.assertEqual(loaded["ai_interpretation"]["intent"], "answer_question")
                self.assertEqual(loaded["ai_validation"]["decision"], "ask_clarification")

        asyncio.run(run_case())

    def test_review_prioritizer_orders_blocking_expired_and_low_confidence_items(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        prioritizer = ReviewPrioritizer(now)
        items = [
            {
                "id": "normal",
                "kind": "open_question",
                "title": "Can answer later",
                "body": "Which label should this use?",
                "source_kind": "needs_answer",
                "status": "pending",
                "confidence": "medium",
                "missing_context": [],
                "created_at_utc": "2026-05-04T09:00:00+00:00",
            },
            {
                "id": "low-confidence",
                "kind": "work_suggestion",
                "title": "Work blocker",
                "body": "Low confidence work suggestion needs context",
                "source_kind": "work_ai_suggestion",
                "status": "pending",
                "confidence": "low",
                "missing_context": ["which task"],
                "created_at_utc": "2026-05-04T08:00:00+00:00",
            },
            {
                "id": "expired",
                "kind": "open_question",
                "title": "Expired question",
                "body": "Old unresolved item",
                "source_kind": "needs_answer",
                "status": "expired",
                "confidence": "medium",
                "missing_context": [],
                "created_at_utc": "2026-05-03T08:00:00+00:00",
            },
        ]
        ordered = prioritizer.prioritize(items)
        self.assertEqual([item["id"] for item in ordered[:2]], ["expired", "low-confidence"])
        self.assertEqual(ordered[0]["priority"], "urgent")
        self.assertEqual(ordered[-1]["priority"], "normal")

    def test_safe_auto_processor_refuses_sensitive_and_processes_only_safe_high_confidence(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                finance = await store.create_review_item(
                    kind="finance_review",
                    title="Finance claim",
                    body="Approve this transaction",
                    source_kind="finance_parse_review",
                    ai_validation={"confidence": "high", "safe_to_persist": True, "missing_context": []},
                    confidence="high",
                    automation_policy=AUTO_PROCESS_SAFE,
                )
                memory = await store.create_review_item(
                    kind="morning_question",
                    title="Durable memory claim",
                    body="Make this durable memory claim about identity.",
                    source_kind="needs_answer",
                    source_record_id="memory",
                    ai_validation={"confidence": "high", "safe_to_persist": True, "missing_context": []},
                    confidence="high",
                    automation_policy=AUTO_PROCESS_SAFE,
                )
                safe = await store.create_review_item(
                    kind="open_question",
                    title="Low-risk note",
                    body="Attach harmless detail to temporary review note.",
                    source_kind="low_risk_note",
                    source_record_id="safe",
                    ai_validation={"confidence": "high", "safe_to_persist": True, "missing_context": []},
                    confidence="high",
                    automation_policy=AUTO_PROCESS_SAFE,
                )
                processor = SafeAutoProcessor(store)
                self.assertFalse(processor.can_auto_process(finance))
                self.assertFalse(processor.can_auto_process(memory))
                processed = await processor.process_pending(limit=10)
                self.assertEqual([item["id"] for item in processed], [safe["id"]])
                self.assertEqual((await store.get_review_item(safe["id"]))["status"], "auto_processed")
                self.assertEqual((await store.get_review_item(finance["id"]))["status"], "pending")

        asyncio.run(run_case())

    def test_one_discord_reply_can_update_linked_review_items(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                bot = DiscordTracker(_tracker_config(root), store)
                channel = _FakeDiscordChannel()
                first = await store.create_review_item(kind="open_question", title="Question one", body="First?")
                second = await store.create_review_item(
                    kind="open_question",
                    title="Question two",
                    body="Second?",
                    source_record_id="second",
                )
                card = await bot._post_review_card(channel, first)
                await store.bind_discord_message(
                    review_item_id=first["id"],
                    discord_message_id=card.id,
                    discord_channel_id=channel.id,
                    source_kind="morning_digest",
                    source_id=f"{first['id']},{second['id']}",
                    action_on_reply="morning_digest",
                )

                async def fake_interpreter(_prompt: str):
                    return {
                        "intent": "answer_question",
                        "answers": ["both are done"],
                        "proposed_update": {"answer": "both are done"},
                        "confidence": "high",
                        "missing_context": [],
                    }

                async def fake_validator(_prompt: str):
                    return {
                        "valid": True,
                        "decision": "propose_update",
                        "confidence": "high",
                        "missing_context": [],
                        "contradictions": [],
                        "unsafe_assumptions": [],
                        "safe_to_persist": True,
                        "proposed_status": "approved",
                        "related_review_item_ids": [second["id"]],
                        "improved_update": {"answer": "both are done"},
                    }

                bot.input_interpreter = AIInputInterpreter(fake_interpreter)
                bot.validation_pass = AIValidationPass(fake_validator)
                reply = _FakeReplyMessage(8801, channel, "Both are done.", card.id)
                handled = await bot._maybe_handle_review_reply(reply, reply.content)
                self.assertTrue(handled)
                self.assertEqual((await store.get_review_item(first["id"]))["status"], "approved")
                self.assertEqual((await store.get_review_item(second["id"]))["status"], "approved")
                self.assertIn("Also updated", channel.sent[-1])

        asyncio.run(run_case())

    def test_morning_report_summary_and_questions_publish_to_discord_review_inbox(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                report_dir = root / "reports" / "morning"
                report_dir.mkdir(parents=True)
                report = report_dir / "2026-05-04.md"
                report.write_text(
                    "# Morning Report - 2026-05-04\n\n"
                    "## Top 3 Priorities\n- Ship the review workflow\n\n"
                    "## Due or Overdue Commitments\n- none\n\n"
                    "## Deen Anchor\n- Dhuhr anchor\n\n"
                    "## Health Anchor\n- hydrate\n\n"
                    "## Prayer / Hydration\n- Total: 4/5 logged\n- Total: 6/8\n\n"
                    "## Memory Review Needed\n- review: Should this become durable memory?\n\n"
                    "## One Next Action\n- Open Discord review inbox\n",
                    encoding="utf-8",
                )
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                bot = DiscordTracker(_tracker_config(root), store)
                channel = _FakeDiscordChannel()
                items = await bot.publish_morning_report("2026-05-04", channel=channel, force=True)
                self.assertTrue(report.exists())
                self.assertIn("Today's Review Inbox - 2026-05-04", channel.sent[0])
                self.assertIn("needs decision now", channel.sent[0])
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["kind"], "morning_question")
                self.assertGreaterEqual(len(channel.embeds), 1)

        asyncio.run(run_case())

    def test_morning_digest_groups_items_without_losing_individual_bindings(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                bot = DiscordTracker(_tracker_config(root), store)
                channel = _FakeDiscordChannel()
                urgent = await store.create_review_item(
                    kind="open_question",
                    title="Blocking review",
                    body="This blocks morning. What now?",
                    source_kind="needs_answer",
                    source_record_id="urgent",
                    confidence="low",
                    missing_context=["decision"],
                )
                normal = await store.create_review_item(
                    kind="open_question",
                    title="Normal review",
                    body="Can answer when easy.",
                    source_kind="needs_answer",
                    source_record_id="normal",
                    confidence="medium",
                )
                digest = ReviewDigestBuilder(ReviewPrioritizer()).build([urgent, normal], "2026-05-04")
                self.assertIn("needs decision now", digest.text)
                self.assertIn("answer when easy", digest.text)
                self.assertEqual([item["id"] for item in digest.cards], [urgent["id"], normal["id"]])
                for item in digest.cards:
                    message = await bot._post_review_card(channel, item)
                    binding = await store.get_discord_binding(message.id, channel.id)
                    self.assertEqual(binding["review_item_id"], item["id"])

        asyncio.run(run_case())

    def test_nightly_fallback_expires_and_resurfaces_unclear_review_items(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                store = TrackerStore(root / "tracker.db", root)
                await store.init()
                await store.create_review_item(
                    kind="open_question",
                    title="Expired question",
                    body="What is the missing detail?",
                    source_path="reports/morning/2026-05-04.md",
                    source_record_id="expired",
                    source_kind="morning_report",
                    expires_at_utc="2026-05-04T00:00:00+00:00",
                )
                output = root / "reports" / "nightly" / "2026-05-04-review-fallback.md"
                await run_fallback(ReviewFallbackConfig(root=root, db_path=root / "tracker.db"), "2026-05-04", output)
                self.assertTrue(output.exists())
                needs_answer = root / "inbox" / "needs-answer" / "2026-05-04-review.md"
                self.assertTrue(needs_answer.exists())
                candidates = morning_review_candidates(root, "2026-05-05", "")
                self.assertTrue(any("missing detail" in item["body"] for item in candidates))

        asyncio.run(run_case())

    def test_weekly_automation_health_report_includes_required_counts(self) -> None:
        events = [
            {"review_item_id": "a", "event": "status:auto_processed", "payload": {"status": "auto_processed"}, "created_at_utc": "2026-05-04T01:00:00+00:00"},
            {"review_item_id": "b", "event": "status:approved", "payload": {"status": "approved"}, "created_at_utc": "2026-05-04T01:00:00+00:00"},
            {"review_item_id": "c", "event": "status:rejected", "payload": {"status": "rejected"}, "created_at_utc": "2026-05-04T01:00:00+00:00"},
            {"review_item_id": "d", "event": "expired", "payload": {}, "created_at_utc": "2026-05-04T01:00:00+00:00"},
            {"review_item_id": "e", "event": "surfaced", "payload": {}, "created_at_utc": "2026-05-04T01:00:00+00:00"},
        ]
        items = [{"status": "pending", "kind": "open_question", "body": "Repeated?", "created_at_utc": "2026-05-01T01:00:00+00:00"}]
        text = build_automation_health_report("2026-05-04", events, items)
        for label in ("Auto-processed: 1", "Approved: 1", "Rejected: 1", "Expired: 1", "Resurfaced: 1", "Still pending: 1"):
            self.assertIn(label, text)

    def test_report_lint_detects_missing_review_fallback_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            report_dir = root / "reports" / "morning"
            report_dir.mkdir(parents=True)
            (root / "data" / "daily-summary").mkdir(parents=True)
            (root / "inbox" / "needs-answer").mkdir(parents=True)
            (report_dir / "2026-05-04.md").write_text(
                "# Morning Report - 2026-05-04\n\n"
                "## Top 3 Priorities\n- one\n\n"
                "## Due or Overdue Commitments\n- none\n\n"
                "## Deen Anchor\n- prayer\n\n"
                "## Health Anchor\n- water\n\n"
                "## Prayer / Hydration\n- Total: 4/5 logged\n\n"
                "## Work / Money Anchor\n- none\n\n"
                "## Overnight Research\n- none\n\n"
                "## Memory Review Needed\n- none\n\n"
                "## One Next Action\n- start\n",
                encoding="utf-8",
            )
            jobs_path = root / "jobs.json"
            jobs_path.write_text(json.dumps({"jobs": _minimal_lint_jobs(include_fallback=False)}), encoding="utf-8")
            env = {
                **os.environ,
                "LIFEOS_ROOT": str(root),
                "HERMIS_LIFEOS_JOBS": str(jobs_path),
            }
            completed = subprocess.run(
                ["bash", str(ROOT_DIR / "scripts" / "report_lint.sh"), "2026-05-04"],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("review fallback cron job missing", completed.stdout)

    def test_ai_validation_marks_low_confidence_interpretation_unclear(self) -> None:
        async def run_case() -> None:
            validation = await AIValidationPass().validate(
                {
                    "intent": "add_detail",
                    "confidence": "low",
                    "missing_context": ["which item this refers to"],
                    "proposed_update": {},
                },
                {"review_item": {"id": "review-test"}},
            )
            self.assertEqual(validation["decision"], "ask_clarification")
            self.assertFalse(validation["safe_to_persist"])

        asyncio.run(run_case())

    def test_work_automation_respects_casablanca_window_times(self) -> None:
        tz = ZoneInfo("Africa/Casablanca")
        start = datetime.combine(date(2026, 5, 3), datetime.min.time().replace(hour=14), tzinfo=tz)
        prep = start - timedelta(minutes=60)
        shutdown = datetime.combine(date(2026, 5, 3), datetime.min.time().replace(hour=23), tzinfo=tz)
        self.assertEqual(prep.strftime("%H:%M"), "13:00")
        self.assertEqual(start.strftime("%H:%M"), "14:00")
        self.assertEqual(shutdown.strftime("%H:%M"), "23:00")


if __name__ == "__main__":
    unittest.main()
