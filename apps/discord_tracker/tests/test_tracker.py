from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import sys

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from config import is_owner_id, parse_owner_ids
from hydration import HYDRATION_REACTIONS, parse_hydration_footer
from prayer import PRAYER_REACTIONS, parse_aladhan_timings, parse_prayer_footer
from store import TrackerStore


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


if __name__ == "__main__":
    unittest.main()
