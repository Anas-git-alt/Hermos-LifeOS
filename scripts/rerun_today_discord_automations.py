#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "apps" / "discord_tracker"
sys.path.insert(0, str(APP_DIR))

from review_automation import ReviewDigestBuilder, ReviewPrioritizer, SafeAutoProcessor  # noqa: E402
from review_reports import build_morning_discord_summary, morning_review_candidates, read_morning_report  # noqa: E402
from store import TrackerStore  # noqa: E402


API_BASE = "https://discord.com/api/v10"
REVIEW_REACTIONS = ("✅", "❌", "❓", "📝")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rerun today's Discord-facing Life OS automations.")
    parser.add_argument("day", nargs="?", default=date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true")
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


async def main_async() -> int:
    args = parse_args()
    env = load_env()
    token = env.get("DISCORD_BOT_TOKEN")
    guild_id = env.get("DISCORD_GUILD_ID")
    if not token or not guild_id:
        print("FAIL: DISCORD_BOT_TOKEN and DISCORD_GUILD_ID are required", file=sys.stderr)
        return 1

    day = date.fromisoformat(args.day)
    client = DiscordClient(token)
    channels = client.text_channels(guild_id)
    daily_name = env.get("DAILY_PLAN_CHANNEL_NAME", "daily-plan")
    review_name = env.get("REVIEW_CHANNEL_NAME", "approval-queue")
    system_name = "system-notifications"
    for name in (daily_name, review_name, system_name):
        if name not in channels:
            print(f"FAIL: missing Discord channel #{name}", file=sys.stderr)
            return 1

    summary = build_or_fallback_morning_summary(day)
    if args.dry_run:
        print(f"WOULD SEND daily plan summary to #{daily_name}")
    else:
        client.send_message(channels[daily_name]["id"], summary)
        print(f"sent daily plan summary to #{daily_name}")

    review_result = await publish_review_inbox(
        env=env,
        client=client,
        channel=channels[review_name],
        day=day,
        dry_run=args.dry_run,
    )

    status = (
        f"Manual automation rerun complete for {day.isoformat()}.\n"
        f"- Daily plan summary: #{daily_name}\n"
        f"- Review inbox: #{review_name} ({review_result['open_count']} open, {review_result['card_count']} card(s), "
        f"{review_result['auto_processed_count']} auto-processed)\n"
        f"- Work prep/start/shutdown continue through #work-tracker scheduler; existing sent rows were left intact."
    )
    if args.dry_run:
        print(f"WOULD SEND status to #{system_name}")
    else:
        client.send_message(channels[system_name]["id"], status)
        print(f"sent status receipt to #{system_name}")
    return 0


def build_or_fallback_morning_summary(day: date) -> str:
    summary_script = ROOT / "scripts" / "build_discord_morning_summary.py"
    completed = subprocess.run(
        [sys.executable, str(summary_script), day.isoformat()],
        cwd=str(ROOT),
        env={**os.environ, "LIFEOS_ROOT": str(ROOT)},
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return completed.stdout.strip()
    report_text = read_morning_report(ROOT, day.isoformat())
    fallback = build_morning_discord_summary(report_text, day.isoformat())
    if completed.stderr.strip():
        fallback += f"\n\nSummary runner fallback used: {completed.stderr.strip()[:180]}"
    return fallback


async def publish_review_inbox(
    *,
    env: dict[str, str],
    client: "DiscordClient",
    channel: dict[str, Any],
    day: date,
    dry_run: bool,
) -> dict[str, int]:
    db_path = Path(env.get("TRACKER_DB", str(ROOT / "data" / "lifeos_tracker.db"))).expanduser()
    store = TrackerStore(db_path, ROOT)
    await store.init()
    report_text = read_morning_report(ROOT, day.isoformat())
    for candidate in morning_review_candidates(ROOT, day.isoformat(), report_text):
        await store.create_review_item(**candidate)

    prioritizer = ReviewPrioritizer()
    open_items = await store.list_review_items(("pending", "needs_clarification", "expired"), limit=50)
    prioritized = prioritizer.prioritize(open_items)
    for item in prioritized:
        await store.update_review_item_metadata(
            item["id"],
            priority=item["priority"],
            automation_policy=item["automation_policy"],
        )

    auto_processed = await SafeAutoProcessor(store).process_pending(prioritized)
    open_items = await store.list_review_items(("pending", "needs_clarification", "expired"), limit=50)
    digest = ReviewDigestBuilder(ReviewPrioritizer()).build(open_items, day.isoformat(), auto_processed=auto_processed)

    if dry_run:
        print(f"WOULD SEND review digest to #{channel['name']}")
        return {"open_count": len(open_items), "card_count": len(digest.cards), "auto_processed_count": len(auto_processed)}

    digest_message = client.send_message(channel["id"], digest.text)
    if open_items:
        await bind_digest(store, channel, digest_message, open_items)
    for item in open_items:
        await store.mark_review_item_surfaced(
            item["id"],
            parent_discord_message_id=int(digest_message["id"]),
            surface="manual_rerun_digest",
        )

    card_count = 0
    for item in digest.cards:
        message = client.send_embed(channel["id"], item["title"], review_card_body(item), f"review:{item['id']}")
        for emoji in REVIEW_REACTIONS:
            client.add_reaction(channel["id"], message["id"], emoji)
            time.sleep(0.2)
        await store.bind_discord_message(
            review_item_id=item["id"],
            discord_message_id=int(message["id"]),
            discord_channel_id=int(channel["id"]),
            source_kind=item.get("source_kind"),
            source_id=item.get("source_record_id"),
            source_path=item.get("source_path"),
            action_on_reply="add_detail",
            parent_discord_message_id=int(digest_message["id"]),
        )
        await store.mark_review_item_surfaced(
            item["id"],
            parent_discord_message_id=int(digest_message["id"]),
            surface="manual_rerun_card",
        )
        card_count += 1
    return {"open_count": len(open_items), "card_count": card_count, "auto_processed_count": len(auto_processed)}


async def bind_digest(store: TrackerStore, channel: dict[str, Any], message: dict[str, Any], items: list[dict[str, Any]]) -> None:
    first = items[0]
    related_ids = ",".join(item["id"] for item in items[:12])
    await store.bind_discord_message(
        review_item_id=first["id"],
        discord_message_id=int(message["id"]),
        discord_channel_id=int(channel["id"]),
        source_kind="morning_digest",
        source_id=related_ids,
        action_on_reply="morning_digest",
        update_review_item_message=False,
    )


def review_card_body(item: dict[str, Any]) -> str:
    body = str(item.get("body") or "").strip()
    source = item.get("source_path") or item.get("source_kind") or "unknown source"
    missing = item.get("missing_context") or []
    lines = [
        clip(body, 950),
        "",
        f"ID: `{item['id']}`",
        f"Priority: `{item.get('priority') or 'normal'}`",
        f"Status: `{item.get('status')}`",
        f"Source: `{source}`",
        "",
        "React: ✅ approve | ❌ reject | ❓ clarify | 📝 add details",
        "Or reply to this message.",
    ]
    if missing:
        lines.insert(1, f"Needs: {', '.join(str(value) for value in missing[:3])}")
    return clip("\n".join(lines), 1900)


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


class DiscordClient:
    def __init__(self, token: str):
        self.token = token

    def text_channels(self, guild_id: str) -> dict[str, dict[str, Any]]:
        channels = self.request("GET", f"/guilds/{quote(guild_id)}/channels")
        return {item["name"]: item for item in channels if item.get("type") == 0}

    def send_message(self, channel_id: str, content: str) -> dict[str, Any]:
        return self.request("POST", f"/channels/{quote(str(channel_id))}/messages", {"content": clip(content, 1900)})

    def send_embed(self, channel_id: str, title: str, description: str, footer: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/channels/{quote(str(channel_id))}/messages",
            {"embeds": [{"title": clip(title, 250), "description": clip(description, 3900), "footer": {"text": footer}}]},
        )

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        self.request(
            "PUT",
            f"/channels/{quote(str(channel_id))}/messages/{quote(str(message_id))}/reactions/{quote(emoji, safe='')}/@me",
        )

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
            with urlopen(request, timeout=30) as response:
                data = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Discord API {method} {path} failed: HTTP {exc.code} {detail}") from exc
        return json.loads(data) if data else None


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
