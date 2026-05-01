from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv


DEFAULT_ROOT = Path("/home/ubuntu/hermis-life-os")


@dataclass(frozen=True)
class TrackerConfig:
    discord_bot_token: str
    discord_guild_id: int | None
    discord_owner_ids: frozenset[int]
    prayer_channel_name: str
    hydration_channel_name: str
    lifeos_root: Path
    tracker_db: Path
    timezone: str
    prayer_city: str
    prayer_country: str
    prayer_method: int
    prayer_close_nudge_minutes: int
    hydration_start_hour: int
    hydration_end_hour: int
    hydration_interval_minutes: int
    hydration_target_count: int


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
        lifeos_root=lifeos_root,
        tracker_db=tracker_db,
        timezone=os.getenv("TIMEZONE", "Africa/Casablanca"),
        prayer_city=os.getenv("PRAYER_CITY", "Casablanca"),
        prayer_country=os.getenv("PRAYER_COUNTRY", "Morocco"),
        prayer_method=_int_env("PRAYER_METHOD", 21),
        prayer_close_nudge_minutes=_int_env("PRAYER_CLOSE_NUDGE_MINUTES", 10),
        hydration_start_hour=_int_env("HYDRATION_START_HOUR", 9),
        hydration_end_hour=_int_env("HYDRATION_END_HOUR", 22),
        hydration_interval_minutes=_int_env("HYDRATION_INTERVAL_MINUTES", 90),
        hydration_target_count=_int_env("HYDRATION_TARGET_COUNT", 8),
    )
