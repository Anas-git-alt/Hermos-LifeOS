from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord.ext import commands, tasks

from config import TrackerConfig, is_owner_id, load_config
from finance import finance_review_request, parse_finance_message
from hydration import (
    HYDRATION_REACTIONS,
    hydration_embed_text,
    hydration_reminder_id,
    parse_hydration_footer,
)
from prayer import (
    PRAYER_NAMES,
    PRAYER_REACTIONS,
    PrayerWindow,
    build_prayer_windows,
    fetch_daily_prayer_timings,
    parse_prayer_footer,
    prayer_embed_text,
)
from store import TrackerStore


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("discord_tracker")
REACTIONS_BY_KIND = {
    "prayer": tuple(PRAYER_REACTIONS.keys()),
    "hydration": tuple(HYDRATION_REACTIONS.keys()),
}


class DiscordTracker(commands.Bot):
    def __init__(self, config: TrackerConfig, store: TrackerStore):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.reactions = True
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents)
        self.config = config
        self.store = store
        self.tz = ZoneInfo(config.timezone)
        self.http_session: aiohttp.ClientSession | None = None
        self.add_commands()

    async def setup_hook(self) -> None:
        await self.store.init()
        self.http_session = aiohttp.ClientSession()
        self.prayer_scheduler.start()
        self.hydration_scheduler.start()

    async def close(self) -> None:
        self.prayer_scheduler.cancel()
        self.hydration_scheduler.cancel()
        if self.http_session:
            await self.http_session.close()
        await super().close()

    async def on_ready(self) -> None:
        LOGGER.info("Discord tracker logged in as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        content = (message.content or "").strip()
        if content.startswith(str(self.command_prefix)):
            await self.process_commands(message)
            return
        await self._maybe_capture_finance_message(message, content)
        await self.process_commands(message)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id == (self.user.id if self.user else None):
            return
        if not is_owner_id(payload.user_id, self.config.discord_owner_ids):
            return

        emoji = str(payload.emoji)
        if emoji not in PRAYER_REACTIONS and emoji not in HYDRATION_REACTIONS:
            return

        channel = self.get_channel(payload.channel_id)
        if channel is None:
            channel = await self.fetch_channel(payload.channel_id)
        if not hasattr(channel, "fetch_message"):
            return

        message = await channel.fetch_message(payload.message_id)
        footer = _first_embed_footer(message)
        if not footer:
            return

        prayer_footer = parse_prayer_footer(footer)
        if prayer_footer and emoji in PRAYER_REACTIONS:
            await self._handle_prayer_reaction(payload, channel, prayer_footer, emoji)
            return

        hydration_footer = parse_hydration_footer(footer)
        if hydration_footer and emoji in HYDRATION_REACTIONS:
            await self._handle_hydration_reaction(payload, channel, hydration_footer, emoji)

    async def _handle_prayer_reaction(self, payload, channel, footer, emoji: str) -> None:
        status = PRAYER_REACTIONS[emoji]
        window_end_utc = await self._window_end_utc(footer.local_date, footer.prayer_name)
        created = await self.store.log_prayer(
            local_date=footer.local_date,
            prayer_name=footer.prayer_name,
            window_id=footer.window_id,
            status=status,
            message_id=payload.message_id,
            channel_id=payload.channel_id,
            logged_by=payload.user_id,
            window_end_utc=window_end_utc,
        )
        if not created:
            LOGGER.info(
                "Ignored duplicate prayer reaction for %s %s by %s",
                footer.local_date,
                footer.prayer_name,
                payload.user_id,
            )
            return
        await channel.send(
            f"Logged `{footer.prayer_name}` for {footer.local_date}: {status}."
        )

    async def _handle_hydration_reaction(self, payload, channel, footer, emoji: str) -> None:
        action, delta = HYDRATION_REACTIONS[emoji]
        new_count, created = await self.store.log_hydration_reaction(
            local_date=footer.local_date,
            reminder_id=footer.reminder_id,
            action=action,
            count_delta=delta,
            note="reaction",
            message_id=payload.message_id,
            channel_id=payload.channel_id,
            logged_by=payload.user_id,
        )
        if not created:
            LOGGER.info(
                "Ignored duplicate hydration reaction for %s %s by %s",
                footer.local_date,
                footer.reminder_id,
                payload.user_id,
            )
            return
        if action == "snooze":
            await self.store.set_hydration_snooze(
                footer.local_date,
                datetime.now(timezone.utc) + timedelta(minutes=30),
            )
            await channel.send("Snoozed hydration reminder for 30 minutes.")
        elif action == "skip":
            await channel.send(f"Skipped hydration reminder. Today: {new_count}/{self.config.hydration_target_count}.")
        else:
            await channel.send(
                f"Hydration logged: +{delta}. Today: {new_count}/{self.config.hydration_target_count}."
            )

    async def _maybe_capture_finance_message(self, message: discord.Message, content: str) -> None:
        if not content:
            return
        if not is_owner_id(message.author.id, self.config.discord_owner_ids):
            return
        channel_name = getattr(message.channel, "name", "")
        if channel_name != self.config.finance_channel_name:
            return

        local_date = datetime.now(self.tz).date().isoformat()
        parsed = finance_review_request()
        result = await self.store.log_finance_message(
            local_date=local_date,
            raw_text=content,
            parsed=parsed,
            message_id=message.id,
            channel_id=message.channel.id,
            channel_name=channel_name,
            logged_by=message.author.id,
        )
        if not result.get("created"):
            return
        if result["status"] == "parsed":
            await message.channel.send(_finance_logged_text(result["transaction_ids"], parsed.entries))
            return
        reason = result.get("review_reason") or parsed.review_reason
        if reason == "hermis_review_required":
            await message.channel.send(
                f"Captured money note `{result['review_id']}` for Hermis review. "
                f"Use `!money edit review:{result['review_id']} <corrected text>` only for immediate ledger entry."
            )
            return
        await message.channel.send(
            f"Money needs review `{result['review_id']}`: {reason}. "
            f"Use `!money edit review:{result['review_id']} <corrected text>`."
        )

    async def _window_end_utc(self, local_date: str, prayer_name: str) -> datetime | None:
        try:
            local_day = date.fromisoformat(local_date)
            windows = await self._prayer_windows_for(local_day)
        except Exception:
            LOGGER.exception("Could not resolve prayer window end for %s %s", local_date, prayer_name)
            return None
        for window in windows:
            if window.prayer_name == prayer_name:
                return window.ends_at_utc
        return None

    @tasks.loop(minutes=1)
    async def prayer_scheduler(self) -> None:
        await self.wait_until_ready()
        now_local = datetime.now(self.tz)
        now_utc = now_local.astimezone(timezone.utc)
        for local_day in (now_local.date() - timedelta(days=1), now_local.date()):
            try:
                await self._schedule_prayer_day(local_day, now_utc)
            except Exception:
                LOGGER.exception("Prayer scheduler failed for %s", local_day)

    @tasks.loop(minutes=1)
    async def hydration_scheduler(self) -> None:
        await self.wait_until_ready()
        now_local = datetime.now(self.tz)
        if not self._is_hydration_reminder_minute(now_local):
            return
        local_date = now_local.date().isoformat()
        snooze_until = await self.store.get_hydration_snooze_until(local_date)
        if snooze_until and snooze_until > datetime.now(timezone.utc):
            return
        reminder_id = hydration_reminder_id(local_date, now_local.hour, now_local.minute)
        if await self.store.get_posted_reminder("hydration", local_date, reminder_id):
            return
        channel = await self._named_channel(self.config.hydration_channel_name)
        if channel is None:
            LOGGER.warning("Hydration channel #%s not found", self.config.hydration_channel_name)
            return
        count = await self.store.get_hydration_count(local_date)
        title, description, footer = hydration_embed_text(
            local_date,
            reminder_id,
            self.config.hydration_target_count,
            count,
        )
        message = await self._send_embed_with_reactions(channel, title, description, footer, "hydration")
        await self.store.save_posted_reminder(
            "hydration",
            local_date,
            reminder_id,
            message.id,
            channel.id,
        )

    async def _schedule_prayer_day(self, local_day: date, now_utc: datetime) -> None:
        windows = await self._prayer_windows_for(local_day)
        channel = await self._named_channel(self.config.prayer_channel_name)
        if channel is None:
            LOGGER.warning("Prayer channel #%s not found", self.config.prayer_channel_name)
            return
        for window in windows:
            if window.starts_at.astimezone(timezone.utc) <= now_utc < window.ends_at_utc:
                await self._post_prayer_window(channel, window)
            await self._post_close_nudge_if_needed(channel, window, now_utc)

    async def _post_prayer_window(self, channel, window: PrayerWindow) -> None:
        if await self.store.get_posted_reminder("prayer", window.local_date, window.window_id):
            return
        title, description, footer = prayer_embed_text(window)
        message = await self._send_embed_with_reactions(channel, title, description, footer, "prayer")
        await self.store.save_posted_reminder(
            "prayer",
            window.local_date,
            window.window_id,
            message.id,
            channel.id,
        )

    async def _post_close_nudge_if_needed(self, channel, window: PrayerWindow, now_utc: datetime) -> None:
        posted = await self.store.get_posted_reminder("prayer", window.local_date, window.window_id)
        if posted is None or posted["close_nudged_at_utc"]:
            return
        if await self.store.has_prayer_log(window.local_date, window.prayer_name, window.window_id):
            return
        nudge_at = window.ends_at_utc - timedelta(minutes=self.config.prayer_close_nudge_minutes)
        if nudge_at <= now_utc < window.ends_at_utc:
            end_text = window.ends_at_utc.strftime("%Y-%m-%d %H:%M")
            await channel.send(
                f"`{window.prayer_name}` window closes at `{end_text} UTC`. React on the reminder if not logged."
            )
            await self.store.mark_close_nudged("prayer", window.local_date, window.window_id)

    async def _prayer_windows_for(self, local_day: date) -> list[PrayerWindow]:
        today = await self._prayer_timings_for(local_day)
        tomorrow = await self._prayer_timings_for(local_day + timedelta(days=1))
        return build_prayer_windows(local_day, today, tomorrow)

    async def _prayer_timings_for(self, local_day: date) -> dict[str, datetime]:
        local_date = local_day.isoformat()
        cached = await self.store.get_prayer_schedule(local_date)
        if cached is not None:
            return cached
        if self.http_session is None:
            raise RuntimeError("HTTP session is not initialized")
        timings = await fetch_daily_prayer_timings(self.http_session, self.config, local_day)
        await self.store.save_prayer_schedule(local_date, timings)
        return timings

    def _is_hydration_reminder_minute(self, now_local: datetime) -> bool:
        start = datetime.combine(now_local.date(), time(self.config.hydration_start_hour), tzinfo=self.tz)
        end = datetime.combine(now_local.date(), time(self.config.hydration_end_hour), tzinfo=self.tz)
        if not (start <= now_local <= end):
            return False
        elapsed_minutes = int((now_local - start).total_seconds() // 60)
        return elapsed_minutes % self.config.hydration_interval_minutes == 0

    async def _send_embed_with_reactions(self, channel, title: str, description: str, footer: str, kind: str):
        embed = discord.Embed(title=title, description=description)
        embed.set_footer(text=footer)
        message = await channel.send(embed=embed)
        for emoji in REACTIONS_BY_KIND[kind]:
            await message.add_reaction(emoji)
        return message

    async def _named_channel(self, name: str):
        guilds = []
        if self.config.discord_guild_id:
            guild = self.get_guild(self.config.discord_guild_id)
            if guild:
                guilds.append(guild)
        guilds.extend(guild for guild in self.guilds if guild not in guilds)
        for guild in guilds:
            channel = discord.utils.get(guild.text_channels, name=name)
            if channel:
                return channel
        return None

    def add_commands(self) -> None:
        @self.command(name="prayertoday")
        async def prayertoday(ctx: commands.Context) -> None:
            windows = await self._prayer_windows_for(datetime.now(self.tz).date())
            lines = []
            for window in windows:
                start = window.starts_at.strftime("%H:%M %Z")
                end_utc = window.ends_at_utc.strftime("%Y-%m-%d %H:%M UTC")
                lines.append(f"**{window.prayer_name}**: {start} until `{end_utc}`")
            await ctx.send("\n".join(lines))

        @self.command(name="water")
        async def water(ctx: commands.Context, first: str = "1", *, note: str = "") -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            count, final_note = _parse_water_args(first, note)
            local_date = datetime.now(self.tz).date().isoformat()
            total = await self.store.log_hydration(
                local_date=local_date,
                reminder_id="manual",
                action="manual",
                count_delta=count,
                note=final_note,
                message_id=ctx.message.id,
                channel_id=ctx.channel.id,
                logged_by=ctx.author.id,
            )
            await ctx.send(f"Hydration logged: +{count}. Today: {total}/{self.config.hydration_target_count}.")

        @self.command(name="hydration")
        async def hydration(ctx: commands.Context) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            local_date = datetime.now(self.tz).date().isoformat()
            total = await self.store.get_hydration_count(local_date)
            await ctx.send(f"Hydration today: {total}/{self.config.hydration_target_count}.")

        @self.group(name="money", invoke_without_command=True)
        async def money(ctx: commands.Context) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            local_date = datetime.now(self.tz).date().isoformat()
            summary = await self.store.get_finance_day_summary(local_date)
            await ctx.send(_finance_summary_text(f"Money today ({local_date})", summary))

        @money.command(name="today")
        async def money_today(ctx: commands.Context) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            local_date = datetime.now(self.tz).date().isoformat()
            summary = await self.store.get_finance_day_summary(local_date)
            await ctx.send(_finance_summary_text(f"Money today ({local_date})", summary))

        @money.command(name="month")
        async def money_month(ctx: commands.Context, month: str | None = None) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            month = month or datetime.now(self.tz).strftime("%Y-%m")
            try:
                datetime.strptime(month, "%Y-%m")
            except ValueError:
                await ctx.send("Use month as `YYYY-MM`.")
                return
            summary = await self.store.get_finance_month_summary(month)
            await ctx.send(_finance_summary_text(f"Money month ({month})", summary))

        @money.command(name="review")
        async def money_review(ctx: commands.Context) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            reviews = await self.store.list_finance_reviews()
            if not reviews:
                await ctx.send("No money reviews open.")
                return
            lines = ["Open money reviews:"]
            for item in reviews:
                raw_text = item["raw_text"]
                if len(raw_text) > 80:
                    raw_text = raw_text[:77] + "..."
                lines.append(f"- review:{item['id']} {item['local_date']} {item['reason']}: {raw_text}")
            await ctx.send("\n".join(lines))

        @money.command(name="edit")
        async def money_edit(ctx: commands.Context, item_id: str, *, replacement: str) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            parsed = parse_finance_message(replacement)
            if parsed.status != "parsed" or not parsed.entries:
                await ctx.send(f"Could not parse correction: {parsed.review_reason or 'needs_review'}.")
                return

            ref_kind, numeric_id = _parse_money_ref(item_id)
            if ref_kind == "review":
                records = await self.store.resolve_finance_review(numeric_id, parsed.entries)
                if records is None:
                    await ctx.send(f"No open money item `{item_id}` found.")
                    return
                await ctx.send(_finance_logged_text([record["id"] for record in records], parsed.entries))
                return
            elif ref_kind == "tx":
                if len(parsed.entries) != 1:
                    await ctx.send("Edit one transaction at a time, or resolve a `review:id` with multiple lines.")
                    return
                record = await self.store.edit_finance_transaction(numeric_id, parsed.entries[0])
            else:
                if len(parsed.entries) != 1:
                    await ctx.send("Use `review:id` when corrected text has multiple entries.")
                    return
                record = await self.store.edit_finance_transaction(numeric_id, parsed.entries[0])
                if record is None:
                    records = await self.store.resolve_finance_review(numeric_id, parsed.entries)
                    if records is not None:
                        await ctx.send(_finance_logged_text([item["id"] for item in records], parsed.entries))
                        return

            if record is None:
                await ctx.send(f"No open money item `{item_id}` found.")
                return
            await ctx.send(f"Money `{record['id']}` updated: {_finance_entry_text(parsed.entries[0])}.")

        @money.command(name="void")
        async def money_void(ctx: commands.Context, item_id: str) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            _ref_kind, numeric_id = _parse_money_ref(item_id)
            result = await self.store.void_finance_item(numeric_id)
            if result is None:
                await ctx.send(f"No open money item `{item_id}` found.")
                return
            await ctx.send(f"Voided money {result['kind']} `{result['id']}`.")

        @self.command(name="testprayer")
        async def testprayer(ctx: commands.Context, prayer_name: str = "Dhuhr") -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            prayer_name = _normalize_prayer_name(prayer_name)
            local_date = datetime.now(self.tz).date().isoformat()
            window = PrayerWindow(
                local_date=local_date,
                prayer_name=prayer_name,
                window_id=f"{local_date}-{prayer_name.lower()}-test",
                starts_at=datetime.now(self.tz),
                ends_at=datetime.now(self.tz) + timedelta(minutes=15),
            )
            title, description, footer = prayer_embed_text(window)
            await self._send_embed_with_reactions(ctx.channel, title, description, footer, "prayer")


def _first_embed_footer(message) -> str | None:
    if not message.embeds:
        return None
    footer = message.embeds[0].footer
    text = getattr(footer, "text", None)
    return text or None


def _parse_water_args(first: str, note: str) -> tuple[int, str]:
    try:
        count = int(first)
        final_note = note.strip()
    except ValueError:
        count = 1
        final_note = " ".join(part for part in (first, note.strip()) if part).strip()
    if count < 1:
        raise commands.BadArgument("Hydration count must be at least 1")
    return count, final_note


def _normalize_prayer_name(value: str) -> str:
    lookup = {name.lower(): name for name in PRAYER_NAMES}
    return lookup.get(value.lower(), value.title())


def _finance_entry_text(entry) -> str:
    amount = f"{entry.amount} {entry.currency}"
    if entry.amount_mad is None and entry.currency != "MAD":
        amount += " (not normalized to MAD)"
    return f"{entry.kind} {amount} / {entry.category} / {entry.description}"


def _finance_logged_text(transaction_ids, entries) -> str:
    if len(transaction_ids) == 1:
        return f"Logged money tx `{transaction_ids[0]}`: {_finance_entry_text(entries[0])}."
    ids = ", ".join(f"`{item}`" for item in transaction_ids)
    total_mad = sum(entry.amount_mad for entry in entries if entry.amount_mad is not None)
    return f"Logged money txs {ids}: {len(entries)} entries, {total_mad} MAD tracked."


def _finance_summary_text(title: str, summary: dict) -> str:
    lines = [
        f"**{title}**",
        f"- Transactions: {summary['transaction_count']}",
        f"- Expenses: {summary['expense_mad']} MAD",
        f"- Income: {summary['income_mad']} MAD",
        f"- Savings: {summary['savings_mad']} MAD",
        f"- Transfers: {summary['transfer_mad']} MAD",
    ]
    if summary["by_category"]:
        categories = ", ".join(
            f"{category} {amount} MAD"
            for category, amount in summary["by_category"].items()
        )
        lines.append(f"- Categories: {categories}")
    if summary["non_mad"]:
        lines.append(f"- Non-MAD entries: {len(summary['non_mad'])} not normalized")
    if summary["needs_review_count"]:
        lines.append(f"- Needs review: {summary['needs_review_count']}")
    return "\n".join(lines)


def _parse_money_ref(value: str) -> tuple[str | None, int]:
    token = value.strip().lower()
    if ":" in token:
        prefix, raw_id = token.split(":", 1)
        if prefix in {"review", "tx"}:
            return prefix, int(raw_id)
    return None, int(token)


async def main() -> None:
    config = load_config()
    if not config.discord_bot_token:
        raise RuntimeError("DISCORD_BOT_TOKEN is required in .env.discord-tracker")
    if not config.discord_owner_ids:
        raise RuntimeError("DISCORD_OWNER_IDS is required in .env.discord-tracker")
    store = TrackerStore(config.tracker_db, config.lifeos_root)
    bot = DiscordTracker(config, store)
    await bot.start(config.discord_bot_token)


if __name__ == "__main__":
    asyncio.run(main())
