from __future__ import annotations

from dataclasses import dataclass


HYDRATION_REACTIONS = {
    "💧": ("drink", 1),
    "🥤": ("large_drink", 2),
    "💤": ("snooze", 0),
    "❌": ("skip", 0),
}


@dataclass(frozen=True)
class HydrationFooter:
    local_date: str
    reminder_id: str


def parse_hydration_footer(text: str) -> HydrationFooter | None:
    parts = text.strip().split(":", 2)
    if len(parts) != 3 or parts[0] != "hydration":
        return None
    local_date, reminder_id = parts[1], parts[2]
    if not local_date or not reminder_id:
        return None
    return HydrationFooter(local_date=local_date, reminder_id=reminder_id)


def hydration_reminder_id(local_date: str, hour: int, minute: int) -> str:
    return f"{local_date}-{hour:02d}{minute:02d}"


def hydration_embed_text(
    local_date: str,
    reminder_id: str,
    target_count: int,
    logged_count: int,
) -> tuple[str, str, str]:
    title = "💧 Hydration Reminder"
    description = (
        f"Target: {target_count} today\n"
        f"Logged so far: {logged_count}/{target_count}\n"
        "React now:\n"
        "💧 drank water | 🥤 large drink | 💤 snooze | ❌ skip"
    )
    footer = f"hydration:{local_date}:{reminder_id}"
    return title, description, footer
