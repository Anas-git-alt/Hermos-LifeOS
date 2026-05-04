#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
API_BASE = "https://discord.com/api/v10"


@dataclass(frozen=True)
class DesiredChannel:
    category: str
    name: str
    topic: str


DESIRED_CATEGORIES = (
    "HERMIS HOME",
    "HERMIS TRACKERS",
    "LIFE AREAS",
    "SYSTEM",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create/update the Hermis Discord category/channel layout.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without calling write endpoints.")
    parser.add_argument("--post-update", action="store_true", help="Post a concise update message to the daily-plan channel.")
    return parser.parse_args()


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    env_file = ROOT / ".env.discord-tracker"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env


def desired_channels(env: dict[str, str]) -> list[DesiredChannel]:
    daily_plan = env.get("DAILY_PLAN_CHANNEL_NAME", "daily-plan")
    review = env.get("REVIEW_CHANNEL_NAME", "approval-queue")
    channels = [
        DesiredChannel(
            "HERMIS HOME",
            "dashboard",
            "High-level Hermis status, system overview, and future dashboard summaries.",
        ),
        DesiredChannel(
            "HERMIS HOME",
            daily_plan,
            "Daily plan and morning summary. Commands: !morning, !review.",
        ),
        DesiredChannel(
            "HERMIS HOME",
            review,
            "Approval queue: Today's Review Inbox, review cards, reactions, and natural-language replies.",
        ),
        DesiredChannel(
            "HERMIS TRACKERS",
            env.get("PRAYER_CHANNEL_NAME", "prayer-tracker"),
            "Prayer reminders and reaction logging. Commands: !prayertoday, !testprayer.",
        ),
        DesiredChannel(
            "HERMIS TRACKERS",
            env.get("HYDRATION_CHANNEL_NAME", "habits"),
            "Hydration reminders and manual water logging. Commands: !water, !hydration.",
        ),
        DesiredChannel(
            "HERMIS TRACKERS",
            env.get("WORK_CHANNEL_NAME", "work-tracker"),
            "Work captures and assistant nudges. AI suggestions are review-gated; use !work review and !work accept.",
        ),
        DesiredChannel(
            "HERMIS TRACKERS",
            env.get("FINANCE_CHANNEL_NAME", "finance-tracker"),
            "Finance captures. Review-first AI-led processing; sensitive changes require explicit approval.",
        ),
        DesiredChannel("LIFE AREAS", "daily-adhkar", "Daily adhkar and deen support. Dormant until automation is enabled."),
        DesiredChannel("LIFE AREAS", "fitness-log", "Fitness, body, and training logs. Dormant until automation is enabled."),
        DesiredChannel("LIFE AREAS", "family-calendar", "Family schedule and calendar coordination. Dormant until automation is enabled."),
        DesiredChannel("LIFE AREAS", "wife-commitments", "Commitments and follow-through related to wife/family life. Sensitive; approval-first."),
        DesiredChannel("LIFE AREAS", "ai-content", "AI content ideas, drafts, and publishing queue. Dormant until automation is enabled."),
        DesiredChannel("LIFE AREAS", "analytics", "Personal analytics, trends, and future dashboards. Dormant until automation is enabled."),
        DesiredChannel("LIFE AREAS", "weekly-review", "Weekly review prompts, summaries, and planning outputs."),
        DesiredChannel("SYSTEM", "system-notifications", "Automation status, failures, deployments, and operational notices."),
        DesiredChannel("SYSTEM", "audit-log", "Audit trail for sensitive approvals and durable state changes."),
    ]
    return _dedupe_channels(channels)


def main() -> int:
    args = parse_args()
    env = load_env()
    token = env.get("DISCORD_BOT_TOKEN")
    guild_id = env.get("DISCORD_GUILD_ID")
    if not token:
        print("FAIL: DISCORD_BOT_TOKEN is missing", file=sys.stderr)
        return 1
    if not guild_id:
        print("FAIL: DISCORD_GUILD_ID is missing", file=sys.stderr)
        return 1

    client = DiscordClient(token)
    existing = client.request("GET", f"/guilds/{quote(guild_id)}/channels")
    categories = {item["name"]: item for item in existing if item.get("type") == 4}
    text_channels = {item["name"]: item for item in existing if item.get("type") == 0}

    print("Discord layout map:")
    for category in DESIRED_CATEGORIES:
        print(f"- {category}")
        for channel in [item for item in desired_channels(env) if item.category == category]:
            print(f"  - #{channel.name}: {channel.topic}")

    for category in DESIRED_CATEGORIES:
        if category in categories:
            continue
        print(f"CREATE category: {category}")
        if args.dry_run:
            continue
        created = client.request("POST", f"/guilds/{quote(guild_id)}/channels", {"name": category, "type": 4})
        categories[category] = created
        time.sleep(0.5)

    for index, channel in enumerate(desired_channels(env), start=1):
        category_id = categories.get(channel.category, {}).get("id")
        existing_channel = text_channels.get(channel.name)
        payload = {"topic": channel.topic, "position": index}
        if category_id:
            payload["parent_id"] = category_id
        if existing_channel is None:
            print(f"CREATE channel: #{channel.name} under {channel.category}")
            if args.dry_run:
                continue
            created = client.request(
                "POST",
                f"/guilds/{quote(guild_id)}/channels",
                {"name": channel.name, "type": 0, **payload},
            )
            text_channels[channel.name] = created
            time.sleep(0.5)
            continue
        needs_patch = existing_channel.get("topic") != channel.topic
        if category_id and existing_channel.get("parent_id") != category_id:
            needs_patch = True
        if needs_patch:
            print(f"UPDATE channel: #{channel.name}")
            if not args.dry_run:
                client.request("PATCH", f"/channels/{quote(existing_channel['id'])}", payload)
                time.sleep(0.5)
        else:
            print(f"OK channel: #{channel.name}")

    if args.post_update:
        update_name = _update_channel_name(text_channels)
        update_channel = text_channels.get(update_name)
        if update_channel is None:
            print(f"WARN: cannot post update; #{update_name} was not found", file=sys.stderr)
        else:
            message = (
                "Hermis Discord layout updated.\n"
                "- Home: #dashboard, #daily-plan, #approval-queue.\n"
                "- Trackers: prayer, habits, work, and finance.\n"
                "- Life Areas and System channels are mapped for future automation.\n"
                "- Safe automation only handles high-confidence, low-risk reversible items; sensitive domains still need approval."
            )
            print(f"POST update: #{update_name}")
            if not args.dry_run:
                client.request("POST", f"/channels/{quote(update_channel['id'])}/messages", {"content": message})

    return 0


class DiscordClient:
    def __init__(self, token: str):
        self.token = token

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = None
        headers = {
            "Authorization": f"Bot {self.token}",
            "User-Agent": "HermisLifeOS (https://github.com/hermis-life-os, 1.0)",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{API_BASE}{path}", data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=20) as response:
                data = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Discord API {method} {path} failed: HTTP {exc.code} {detail}") from exc
        return json.loads(data) if data else None


def _dedupe_channels(channels: list[DesiredChannel]) -> list[DesiredChannel]:
    seen = set()
    output = []
    for channel in channels:
        if channel.name in seen:
            continue
        seen.add(channel.name)
        output.append(channel)
    return output


def _update_channel_name(text_channels: dict[str, Any]) -> str:
    for name in ("system-notifications", "dashboard", "daily-plan"):
        if name in text_channels:
            return name
    return "daily-plan"


if __name__ == "__main__":
    raise SystemExit(main())
