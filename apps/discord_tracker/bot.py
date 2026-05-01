from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord.ext import commands, tasks

from config import TrackerConfig, is_owner_id, load_config
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
        await self.store.log_prayer(
            local_date=footer.local_date,
            prayer_name=footer.prayer_name,
            window_id=footer.window_id,
            status=status,
            message_id=payload.message_id,
            channel_id=payload.channel_id,
            logged_by=payload.user_id,
            window_end_utc=window_end_utc,
        )
        await channel.send(
            f"Logged `{footer.prayer_name}` for {footer.local_date}: {status}."
        )

    async def _handle_hydration_reaction(self, payload, channel, footer, emoji: str) -> None:
        action, delta = HYDRATION_REACTIONS[emoji]
        new_count = await self.store.log_hydration(
            local_date=footer.local_date,
            reminder_id=footer.reminder_id,
            action=action,
            count_delta=delta,
            note="reaction",
            message_id=payload.message_id,
            channel_id=payload.channel_id,
            logged_by=payload.user_id,
        )
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
            local_date = datetime.now(self.tz).date().isoformat()
            total = await self.store.get_hydration_count(local_date)
            await ctx.send(f"Hydration today: {total}/{self.config.hydration_target_count}.")

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
