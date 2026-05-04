from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv


DEFAULT_ROOT = Path.home() / "hermis-life-os"
DEFAULT_HERMES_HOME = Path.home() / ".hermes" / "profiles" / "lifeos"


@dataclass(frozen=True)
class TrackerConfig:
    discord_bot_token: str
    discord_guild_id: int | None
    discord_owner_ids: frozenset[int]
    prayer_channel_name: str
    hydration_channel_name: str
    finance_channel_name: str
    work_channel_name: str
    daily_plan_channel_name: str
    review_channel_name: str
    lifeos_root: Path
    tracker_db: Path
    hermes_home: Path
    timezone: str
    prayer_city: str
    prayer_country: str
    prayer_method: int
    prayer_close_nudge_minutes: int
    hydration_start_hour: int
    hydration_end_hour: int
    hydration_interval_minutes: int
    hydration_target_count: int
    work_start_hour: int
    work_end_hour: int
    work_prep_lead_minutes: int
    work_mid_shift_checkin_enabled: bool
    work_shutdown_review_enabled: bool
    work_reminder_lookahead_minutes: int
    work_overdue_grace_minutes: int
    work_ai_cmd: str
    work_automation_ai_cmd: str
    review_ai_cmd: str
    morning_review_enabled: bool
    morning_review_hour: int
    morning_review_minute: int
    review_item_expiry_hours: int


def parse_owner_ids(raw: str | None) -> frozenset[int]:
    if not raw:
        return frozenset()
    values: set[int] = set()
    for chunk in raw.replace(";", ",").replace(" ", ",").split(","):
        token = chunk.strip()
        if token:
            values.add(int(token))
    return frozenset(values)


def is_owner_id(user_id: int, owner_ids: Iterable[int]) -> bool:
    return int(user_id) in set(owner_ids)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_env_file() -> None:
    configured = os.getenv("DISCORD_TRACKER_ENV", ".env.discord-tracker")
    path = Path(configured)
    candidates = [path] if path.is_absolute() else [Path.cwd() / path, _repo_root() / path]
    for candidate in candidates:
        if candidate.exists():
            load_dotenv(candidate)
            return


def _int_env(name: str, default: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        if default is None:
            raise ValueError(f"Missing required integer env var: {name}")
        return default
    return int(raw)


def _optional_int_env(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return None
    return int(raw)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> TrackerConfig:
    _load_env_file()

    lifeos_root = Path(os.getenv("LIFEOS_ROOT", str(DEFAULT_ROOT))).expanduser()
    tracker_db = Path(
        os.getenv("TRACKER_DB", str(lifeos_root / "data" / "lifeos_tracker.db"))
    ).expanduser()

    return TrackerConfig(
        discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", ""),
        discord_guild_id=_optional_int_env("DISCORD_GUILD_ID"),
        discord_owner_ids=parse_owner_ids(os.getenv("DISCORD_OWNER_IDS")),
        prayer_channel_name=os.getenv("PRAYER_CHANNEL_NAME", "prayer-tracker"),
        hydration_channel_name=os.getenv("HYDRATION_CHANNEL_NAME", "habits"),
        finance_channel_name=os.getenv("FINANCE_CHANNEL_NAME", "finance-tracker"),
        work_channel_name=os.getenv("WORK_CHANNEL_NAME", "work-tracker"),
        daily_plan_channel_name=os.getenv("DAILY_PLAN_CHANNEL_NAME", "daily-plan"),
        review_channel_name=os.getenv(
            "REVIEW_CHANNEL_NAME",
            os.getenv("DAILY_PLAN_CHANNEL_NAME", "daily-plan"),
        ),
        lifeos_root=lifeos_root,
        tracker_db=tracker_db,
        hermes_home=Path(os.getenv("HERMES_HOME", str(DEFAULT_HERMES_HOME))).expanduser(),
        timezone=os.getenv("TIMEZONE", "Africa/Casablanca"),
        prayer_city=os.getenv("PRAYER_CITY", "Casablanca"),
        prayer_country=os.getenv("PRAYER_COUNTRY", "Morocco"),
        prayer_method=_int_env("PRAYER_METHOD", 21),
        prayer_close_nudge_minutes=_int_env("PRAYER_CLOSE_NUDGE_MINUTES", 10),
        hydration_start_hour=_int_env("HYDRATION_START_HOUR", 9),
        hydration_end_hour=_int_env("HYDRATION_END_HOUR", 22),
        hydration_interval_minutes=_int_env("HYDRATION_INTERVAL_MINUTES", 90),
        hydration_target_count=_int_env("HYDRATION_TARGET_COUNT", 8),
        work_start_hour=_int_env("WORK_START_HOUR", 14),
        work_end_hour=_int_env("WORK_END_HOUR", 23),
        work_prep_lead_minutes=_int_env("WORK_PREP_LEAD_MINUTES", 60),
        work_mid_shift_checkin_enabled=_bool_env("WORK_MID_SHIFT_CHECKIN_ENABLED", False),
        work_shutdown_review_enabled=_bool_env("WORK_SHUTDOWN_REVIEW_ENABLED", True),
        work_reminder_lookahead_minutes=_int_env("WORK_REMINDER_LOOKAHEAD_MINUTES", 30),
        work_overdue_grace_minutes=_int_env("WORK_OVERDUE_GRACE_MINUTES", 15),
        work_ai_cmd=os.getenv("HERMIS_WORK_AI_CMD", ""),
        work_automation_ai_cmd=os.getenv("HERMIS_WORK_AUTOMATION_AI_CMD", ""),
        review_ai_cmd=os.getenv("HERMIS_REVIEW_AI_CMD", ""),
        morning_review_enabled=_bool_env("MORNING_REVIEW_ENABLED", True),
        morning_review_hour=_int_env("MORNING_REVIEW_HOUR", 7),
        morning_review_minute=_int_env("MORNING_REVIEW_MINUTE", 40),
        review_item_expiry_hours=_int_env("REVIEW_ITEM_EXPIRY_HOURS", 18),
    )
