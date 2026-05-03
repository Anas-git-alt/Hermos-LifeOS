from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import sys

APP_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = APP_DIR.parents[1]
SCRIPTS_DIR = ROOT_DIR / "scripts"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from config import is_owner_id, parse_owner_ids
from finance import finance_review_request, parse_finance_message
from hydration import HYDRATION_REACTIONS, parse_hydration_footer
from prayer import PRAYER_REACTIONS, parse_aladhan_timings, parse_prayer_footer
from process_finance_reviews import apply_agent_result, apply_resolutions, fetch_reviews
from store import TrackerStore
from summarize_finance_week import fetch_week
from summarize_tracker_day import fetch_finance, render


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


if __name__ == "__main__":
    unittest.main()
