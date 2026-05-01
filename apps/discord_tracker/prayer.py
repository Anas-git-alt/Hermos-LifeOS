from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


PRAYER_NAMES = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")
PRAYER_REACTIONS = {
    "✅": "on_time",
    "🕒": "late",
    "❌": "missed",
}


@dataclass(frozen=True)
class PrayerFooter:
    local_date: str
    prayer_name: str
    window_id: str


@dataclass(frozen=True)
class PrayerWindow:
    local_date: str
    prayer_name: str
    window_id: str
    starts_at: datetime
    ends_at: datetime

    @property
    def ends_at_utc(self) -> datetime:
        return self.ends_at.astimezone(timezone.utc)


def parse_prayer_footer(text: str) -> PrayerFooter | None:
    parts = text.strip().split(":", 3)
    if len(parts) != 4 or parts[0] != "prayer":
        return None
    local_date, prayer_name, window_id = parts[1], parts[2], parts[3]
    if not local_date or not prayer_name or not window_id:
        return None
    return PrayerFooter(local_date=local_date, prayer_name=prayer_name, window_id=window_id)


def prayer_window_id(local_date: str, prayer_name: str) -> str:
    return f"{local_date}-{prayer_name.lower()}"


def _parse_time_value(value: str) -> time:
    match = re.search(r"(\d{1,2}):(\d{2})", value)
    if not match:
        raise ValueError(f"Could not parse prayer time: {value!r}")
    hour = int(match.group(1))
    minute = int(match.group(2))
    return time(hour=hour, minute=minute)


def parse_aladhan_timings(payload: dict[str, Any], local_day: date, tz_name: str) -> dict[str, datetime]:
    timings = payload.get("data", {}).get("timings", {})
    if not timings:
        raise ValueError("AlAdhan response did not include data.timings")

    tz = ZoneInfo(tz_name)
    parsed: dict[str, datetime] = {}
    for name in PRAYER_NAMES:
        if name not in timings:
            raise ValueError(f"AlAdhan response missing {name}")
        parsed[name] = datetime.combine(local_day, _parse_time_value(timings[name]), tzinfo=tz)
    return parsed


def build_prayer_windows(
    local_day: date,
    timings: dict[str, datetime],
    next_day_timings: dict[str, datetime],
) -> list[PrayerWindow]:
    local_date = local_day.isoformat()
    windows: list[PrayerWindow] = []
    for index, prayer_name in enumerate(PRAYER_NAMES):
        starts_at = timings[prayer_name]
        if prayer_name == "Isha":
            ends_at = next_day_timings["Fajr"]
        else:
            ends_at = timings[PRAYER_NAMES[index + 1]]
        windows.append(
            PrayerWindow(
                local_date=local_date,
                prayer_name=prayer_name,
                window_id=prayer_window_id(local_date, prayer_name),
                starts_at=starts_at,
                ends_at=ends_at,
            )
        )
    return windows


def prayer_embed_text(window: PrayerWindow) -> tuple[str, str, str]:
    end_text = window.ends_at_utc.strftime("%Y-%m-%d %H:%M")
    title = f"🕌 {window.prayer_name} Reminder"
    description = (
        f"Prayer window: until `{end_text} UTC`\n"
        "React now:\n"
        "✅ on-time | 🕒 late | ❌ missed"
    )
    footer = f"prayer:{window.local_date}:{window.prayer_name}:{window.window_id}"
    return title, description, footer


def today_and_tomorrow(day: date) -> tuple[date, date]:
    return day, day + timedelta(days=1)


async def fetch_daily_prayer_timings(session: Any, config: Any, local_day: date) -> dict[str, datetime]:
    day_part = local_day.strftime("%d-%m-%Y")
    url = f"https://api.aladhan.com/v1/timingsByCity/{day_part}"
    params = {
        "city": config.prayer_city,
        "country": config.prayer_country,
        "method": str(config.prayer_method),
    }
    async with session.get(url, params=params, timeout=30) as response:
        response.raise_for_status()
        payload = await response.json()
    return parse_aladhan_timings(payload, local_day, config.timezone)
