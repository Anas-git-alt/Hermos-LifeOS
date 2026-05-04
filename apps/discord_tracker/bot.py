from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord.ext import commands, tasks

from ai_review import AIInputInterpreter, AIValidationPass
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
from review_reports import (
    build_morning_discord_summary,
    morning_review_candidates,
    read_morning_report,
)
from review_automation import ReviewDigestBuilder, ReviewPrioritizer, SafeAutoProcessor
from store import TrackerStore, review_item_id_for_source
from work import (
    draft_parse_work_message,
    render_work_focus,
    render_work_items,
    should_capture_work_message,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("discord_tracker")
REACTIONS_BY_KIND = {
    "prayer": tuple(PRAYER_REACTIONS.keys()),
    "hydration": tuple(HYDRATION_REACTIONS.keys()),
}
REVIEW_REACTIONS = {
    "✅": "approve",
    "❌": "reject",
    "❓": "needs_clarification",
    "📝": "add_detail",
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
        self.input_interpreter = AIInputInterpreter(self._run_review_ai_json)
        self.validation_pass = AIValidationPass(self._run_review_ai_json)
        self.review_prioritizer = ReviewPrioritizer()
        self.review_digest_builder = ReviewDigestBuilder(self.review_prioritizer)
        self.safe_auto_processor = SafeAutoProcessor(self.store)
        self.add_commands()

    async def setup_hook(self) -> None:
        await self.store.init()
        self.http_session = aiohttp.ClientSession()
        self.prayer_scheduler.start()
        self.hydration_scheduler.start()
        self.work_scheduler.start()
        self.morning_review_scheduler.start()

    async def close(self) -> None:
        self.prayer_scheduler.cancel()
        self.hydration_scheduler.cancel()
        self.work_scheduler.cancel()
        self.morning_review_scheduler.cancel()
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
        if await self._maybe_handle_review_reply(message, content):
            return
        await self._maybe_capture_finance_message(message, content)
        await self._maybe_capture_work_message(message, content)
        await self.process_commands(message)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id == (self.user.id if self.user else None):
            return
        if not is_owner_id(payload.user_id, self.config.discord_owner_ids):
            return

        channel = self.get_channel(payload.channel_id)
        if channel is None:
            channel = await self.fetch_channel(payload.channel_id)
        if not hasattr(channel, "fetch_message"):
            return

        emoji = str(payload.emoji)
        if emoji in REVIEW_REACTIONS:
            binding = await self.store.get_discord_binding(payload.message_id, payload.channel_id)
            if binding:
                await self._handle_review_reaction(payload, channel, binding, emoji)
                return

        if emoji not in PRAYER_REACTIONS and emoji not in HYDRATION_REACTIONS:
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

    async def _handle_review_reaction(self, payload, channel, binding: dict[str, Any], emoji: str) -> None:
        action = REVIEW_REACTIONS[emoji]
        item = await self.store.get_review_item(binding["review_item_id"])
        if item is None:
            LOGGER.warning("Review binding points at missing item %s", binding["review_item_id"])
            return

        if action == "approve":
            item = await self._approve_review_item(item, note="approved via Discord reaction")
            if item:
                await channel.send(f"Approved review `{item['id']}`.")
            return
        if action == "reject":
            item = await self._reject_review_item(item, "rejected via Discord reaction")
            if item:
                await channel.send(f"Rejected review `{item['id']}`.")
            return
        if action == "needs_clarification":
            question = _review_followup_question(item)
            await self.store.set_review_item_status(
                item["id"],
                "needs_clarification",
                note="needs clarification via Discord reaction",
                missing_context=item.get("missing_context") or ["user clarification"],
            )
            await channel.send(f"Review `{item['id']}` needs clarification. {question}")
            return
        if action == "add_detail":
            await channel.send(f"Reply to review `{item['id']}` with the details you want attached.")

    async def _maybe_handle_review_reply(self, message: discord.Message, content: str) -> bool:
        if not content:
            return False
        if not is_owner_id(message.author.id, self.config.discord_owner_ids):
            return False
        reference = getattr(message, "reference", None)
        referenced_message_id = getattr(reference, "message_id", None)
        if referenced_message_id is None:
            return False
        binding = await self.store.get_discord_binding(referenced_message_id)
        if binding is None:
            return False
        item = await self.store.get_review_item(binding["review_item_id"])
        if item is None:
            LOGGER.warning("Reply binding points at missing review item %s", binding["review_item_id"])
            return False
        related_items = await self.store.get_review_items_by_ids(_binding_related_review_ids(binding))

        context = {
            "review_item": item,
            "related_review_items": related_items,
            "binding": binding,
            "reply": {
                "message_id": message.id,
                "channel_id": message.channel.id,
                "author_id": message.author.id,
                "content": content,
            },
        }
        interpretation = await self.input_interpreter.interpret(content, context)
        validation = await self.validation_pass.validate(interpretation, context)
        updated = await self.store.record_review_reply(
            review_item_id=item["id"],
            raw_text=content,
            actor_id=message.author.id,
            discord_message_id=message.id,
            discord_channel_id=message.channel.id,
            ai_interpretation=interpretation,
            ai_validation=validation,
        )
        if updated is None:
            return True
        linked_updates = await self._apply_related_review_reply_updates(
            item,
            related_items,
            content,
            message,
            interpretation,
            validation,
        )

        if validation.get("safe_to_persist") and validation.get("proposed_status") == "approved":
            await self._approve_review_item(updated, note="approved via Discord reply")
            suffix = _linked_reply_suffix(linked_updates)
            await message.channel.send(f"Approved review `{item['id']}` with your added context.{suffix}")
            return True
        if validation.get("safe_to_persist") and validation.get("proposed_status") == "rejected":
            await self._reject_review_item(updated, "rejected via Discord reply")
            suffix = _linked_reply_suffix(linked_updates)
            await message.channel.send(f"Rejected review `{item['id']}` with your note attached.{suffix}")
            return True
        if validation.get("decision") == "ask_clarification":
            question = validation.get("clarification_question") or _review_followup_question(updated)
            await message.channel.send(f"Added your reply to `{item['id']}`, but I need one clarification: {question}")
            return True
        suffix = _linked_reply_suffix(linked_updates)
        await message.channel.send(f"Added your reply to review `{item['id']}`. It is still pending review.{suffix}")
        return True

    async def _apply_related_review_reply_updates(
        self,
        item: dict[str, Any],
        related_items: list[dict[str, Any]],
        content: str,
        message: discord.Message,
        interpretation: dict[str, Any],
        validation: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not validation.get("safe_to_persist"):
            return []
        if validation.get("confidence") != "high":
            return []
        related_ids = set(str(value) for value in validation.get("related_review_item_ids") or [])
        if not related_ids:
            return []
        by_id = {related["id"]: related for related in related_items if related["id"] != item["id"]}
        updated_items = []
        for related_id in sorted(related_ids):
            related = by_id.get(related_id)
            if related is None:
                continue
            updated = await self.store.record_review_reply(
                review_item_id=related["id"],
                raw_text=content,
                actor_id=message.author.id,
                discord_message_id=message.id,
                discord_channel_id=message.channel.id,
                ai_interpretation=interpretation,
                ai_validation=validation,
            )
            if updated is None:
                continue
            if validation.get("proposed_status") == "approved":
                updated = await self._approve_review_item(updated, note=f"approved via linked Discord reply to {item['id']}")
            elif validation.get("proposed_status") == "rejected":
                updated = await self._reject_review_item(updated, f"rejected via linked Discord reply to {item['id']}")
            if updated:
                updated_items.append(updated)
        return updated_items

    async def _approve_review_item(self, item: dict[str, Any], *, note: str) -> dict[str, Any] | None:
        source_kind = item.get("source_kind")
        source_id = item.get("source_record_id")
        if source_kind == "work_ai_suggestion" and source_id:
            try:
                await self.store.accept_work_ai_suggestion(int(source_id), reviewer_note=note)
            except (TypeError, ValueError) as exc:
                LOGGER.warning("Could not accept work suggestion %s from review %s: %s", source_id, item["id"], exc)
        return await self.store.set_review_item_status(item["id"], "approved", note=note)

    async def _reject_review_item(self, item: dict[str, Any], reason: str) -> dict[str, Any] | None:
        source_kind = item.get("source_kind")
        source_id = item.get("source_record_id")
        if source_kind == "work_ai_suggestion" and source_id:
            try:
                await self.store.reject_work_ai_suggestion(int(source_id), reason)
            except (TypeError, ValueError) as exc:
                LOGGER.info("Work suggestion %s was not rejected from review %s: %s", source_id, item["id"], exc)
        return await self.store.set_review_item_status(item["id"], "rejected", note=reason)

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
            await self._dispatch_review_item_by_source(
                "finance_review",
                "raw/captures/" + local_date + ".md",
                result["review_id"],
                message.channel,
            )
            return
        await message.channel.send(
            f"Money needs review `{result['review_id']}`: {reason}. "
            f"Use `!money edit review:{result['review_id']} <corrected text>`."
        )
        await self._dispatch_review_item_by_source(
            "finance_review",
            "raw/captures/" + local_date + ".md",
            result["review_id"],
            message.channel,
        )

    async def _maybe_capture_work_message(self, message: discord.Message, content: str) -> None:
        if not should_capture_work_message(content):
            return
        if not is_owner_id(message.author.id, self.config.discord_owner_ids):
            return
        channel_name = getattr(message.channel, "name", "")
        if channel_name != self.config.work_channel_name:
            return

        local_date = datetime.now(self.tz).date().isoformat()
        draft_parse = draft_parse_work_message(content, datetime.now(self.tz).date())
        result = await self.store.log_work_capture(
            local_date=local_date,
            raw_text=content,
            draft_parse=draft_parse,
            message_id=message.id,
            channel_id=message.channel.id,
            channel_name=channel_name,
            logged_by=message.author.id,
        )
        if not result.get("created"):
            return
        suggestion_id = await self._create_work_capture_ai_suggestion(
            capture_id=int(result["capture_id"]),
            local_date=local_date,
            raw_text=content,
            draft_parse=draft_parse,
        )
        ai_text = f" AI suggestion:`{suggestion_id}` pending review." if suggestion_id else " AI draft failed; raw capture is safe."
        await message.channel.send(
            f"Captured work note `{result['capture_id']}` for Hermis review. "
            f"Draft parse is unconfirmed.{ai_text}"
        )
        if suggestion_id:
            await self._dispatch_review_item_by_source(
                "work_suggestion",
                "state/work.md",
                suggestion_id,
                message.channel,
            )

    async def _create_work_capture_ai_suggestion(
        self,
        *,
        capture_id: int,
        local_date: str,
        raw_text: str,
        draft_parse: dict,
        correction_note: str | None = None,
        supersedes_suggestion_id: int | None = None,
    ) -> int | None:
        prompt_payload = {
            "task": "suggest_work_capture",
            "timezone": self.config.timezone,
            "work_window": f"{self.config.work_start_hour:02d}:00-{self.config.work_end_hour:02d}:00",
            "capture": {
                "id": capture_id,
                "local_date": local_date,
                "raw_text": raw_text,
                "draft_parse": draft_parse,
            },
            "correction_note": correction_note,
            "recent_corrections": await self.store.recent_work_ai_corrections(limit=5),
            "rules": [
                "AI drafts only; human review confirms final DB truth.",
                "Use confirmed only for actionable work; split into multiple items when useful.",
                "Use questions when unclear.",
                "Use ignored only with explicit reason.",
            ],
        }
        prompt = _work_capture_ai_prompt(prompt_payload)
        try:
            response = await self._run_work_ai_json(prompt, automation=False)
            response = _normalize_capture_ai_response(response, capture_id)
        except Exception:
            LOGGER.exception("Work capture AI suggestion failed for capture %s", capture_id)
            return None
        return await self.store.create_work_ai_suggestion(
            suggestion_kind="capture_parse",
            source_type="capture",
            source_id=capture_id,
            local_date=local_date,
            prompt=prompt_payload,
            response=response,
            confidence=str(response.get("confidence") or "low"),
            review_reason=str(response.get("review_reason") or "ai_suggestion_needs_review"),
            supersedes_suggestion_id=supersedes_suggestion_id,
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

    @tasks.loop(minutes=1)
    async def work_scheduler(self) -> None:
        await self.wait_until_ready()
        now_local = datetime.now(self.tz).replace(second=0, microsecond=0)
        try:
            await self._schedule_work_automation(now_local)
        except Exception:
            LOGGER.exception("Work scheduler failed")

    async def _schedule_work_automation(self, now_local: datetime) -> None:
        local_date = now_local.date().isoformat()
        start = datetime.combine(now_local.date(), time(self.config.work_start_hour), tzinfo=self.tz)
        end = datetime.combine(now_local.date(), time(self.config.work_end_hour), tzinfo=self.tz)
        prep = start - timedelta(minutes=self.config.work_prep_lead_minutes)
        if prep <= now_local < start:
            await self._send_work_plan("prep", now_local)
        if start <= now_local < start + timedelta(minutes=15):
            await self._send_work_plan("start", now_local)
        if self.config.work_mid_shift_checkin_enabled:
            mid = start + ((end - start) / 2)
            if mid <= now_local < mid + timedelta(minutes=15):
                await self._send_work_plan("midshift", now_local)
        if self.config.work_shutdown_review_enabled and end <= now_local < end + timedelta(minutes=60):
            await self._send_work_shutdown(now_local)
        if start <= now_local <= end:
            await self._send_due_work_reminders(now_local)
            await self._send_waiting_followups(now_local)
        if start <= now_local <= end + timedelta(minutes=60):
            await self._send_overdue_blocker_prompts(now_local)

    async def _send_work_plan(self, mode: str, now_local: datetime):
        local_date = now_local.date().isoformat()
        reminder_id = f"{mode}-{local_date}"
        if await self.store.automation_event_exists(f"work_{mode}", local_date, reminder_id):
            return None
        plan = await self._work_plan_payload(now_local)
        text = _work_plan_text(mode, local_date, self.config.timezone, plan)
        return await self._send_work_automation_message(
            kind=f"work_{mode}",
            local_date=local_date,
            reminder_id=reminder_id,
            text=text,
            payload=plan,
        )

    async def _send_work_shutdown(self, now_local: datetime):
        local_date = now_local.date().isoformat()
        reminder_id = f"shutdown-{local_date}"
        if await self.store.automation_event_exists("work_shutdown", local_date, reminder_id):
            return None
        plan = await self._work_plan_payload(now_local)
        report_path = await self.store.write_work_shutdown_report(
            local_date,
            focus=plan["focus"],
            overdue=plan["overdue"],
            waiting=plan["waiting"],
            clarifications=plan["clarifications"],
            first_action=plan["first_action"],
        )
        plan["report_path"] = str(report_path.relative_to(self.config.lifeos_root))
        text = _work_shutdown_text(local_date, plan)
        return await self._send_work_automation_message(
            kind="work_shutdown",
            local_date=local_date,
            reminder_id=reminder_id,
            text=text,
            payload=plan,
        )

    async def _send_due_work_reminders(self, now_local: datetime) -> None:
        local_date = now_local.date().isoformat()
        items = await self.store.work_due_reminder_items(
            local_date=local_date,
            now_local=now_local,
            lookahead_minutes=self.config.work_reminder_lookahead_minutes,
        )
        for item in items:
            reminder_id = f"due-{item['id']}-{local_date}-{item.get('due_at') or item.get('scheduled_at') or 'eod'}"
            if await self.store.automation_event_exists("work_due", local_date, reminder_id):
                continue
            text = _work_due_text(item)
            await self._send_work_automation_message(
                kind="work_due",
                local_date=local_date,
                reminder_id=reminder_id,
                text=text,
                payload={"item": item},
                item_id=int(item["id"]),
            )

    async def _send_overdue_blocker_prompts(self, now_local: datetime) -> None:
        local_date = now_local.date().isoformat()
        items = await self.store.overdue_work_items(
            local_date=local_date,
            now_local=now_local,
            grace_minutes=self.config.work_overdue_grace_minutes,
        )
        for item in items:
            reminder_id = f"overdue-blocker-{item['id']}-{local_date}"
            if await self.store.automation_event_exists("work_overdue_blocker", local_date, reminder_id):
                continue
            text = _work_overdue_text(item)
            message = await self._send_work_automation_message(
                kind="work_overdue_blocker",
                local_date=local_date,
                reminder_id=reminder_id,
                text=text,
                payload={"item": item},
                item_id=int(item["id"]),
            )
            await self.store.create_work_blocker_prompt(
                item_id=int(item["id"]),
                local_date=local_date,
                reason="overdue",
                message_id=getattr(message, "id", None),
            )

    async def _send_waiting_followups(self, now_local: datetime) -> None:
        local_date = now_local.date().isoformat()
        items = await self.store.waiting_followup_items(now_local.astimezone(timezone.utc), limit=3)
        for item in items:
            reminder_id = f"waiting-followup-{item['id']}-{local_date}"
            if await self.store.automation_event_exists("work_waiting_followup", local_date, reminder_id):
                continue
            text = _work_waiting_text(item)
            await self._send_work_automation_message(
                kind="work_waiting_followup",
                local_date=local_date,
                reminder_id=reminder_id,
                text=text,
                payload={"item": item},
                item_id=int(item["id"]),
            )

    async def _work_plan_payload(self, now_local: datetime) -> dict:
        local_date = now_local.date().isoformat()
        focus, waiting = await self.store.work_focus_items(local_date, limit=5)
        overdue = await self.store.overdue_work_items(
            local_date=local_date,
            now_local=now_local,
            grace_minutes=self.config.work_overdue_grace_minutes,
        )
        clarifications = await self.store.work_clarifications(limit=5)
        p01 = [item for item in focus if item.get("priority") in {"p0", "p1"}]
        prep_items = [
            item for item in focus
            if item.get("scheduled_date") == local_date or item.get("effort_minutes")
        ][:3]
        first_action = next((item for item in focus if item.get("status") == "open"), None)
        return {
            "p01": p01[:3],
            "focus": focus,
            "overdue": overdue,
            "waiting": waiting,
            "clarifications": clarifications,
            "prep_items": prep_items,
            "first_action": first_action,
        }

    async def _send_work_automation_message(
        self,
        *,
        kind: str,
        local_date: str,
        reminder_id: str,
        text: str,
        payload: dict,
        item_id: int | None = None,
        capture_id: int | None = None,
    ):
        channel = await self._named_channel(self.config.work_channel_name)
        if channel is None:
            LOGGER.warning("Work channel #%s not found", self.config.work_channel_name)
            return None
        claimed = await self.store.record_work_automation_event(
            kind=kind,
            local_date=local_date,
            reminder_id=reminder_id,
            payload=payload,
            item_id=item_id,
            capture_id=capture_id,
            channel_id=channel.id,
        )
        if not claimed:
            return None
        final_text, ai_meta = await self._work_automation_ai_text(
            kind=kind,
            local_date=local_date,
            reminder_id=reminder_id,
            payload=payload,
            fallback_text=text,
        )
        final_payload = {**payload, "message_source": ai_meta["source"]}
        if ai_meta.get("suggestion_id"):
            final_payload["ai_suggestion_id"] = ai_meta["suggestion_id"]
        message = await channel.send(_discord_clip(final_text))
        await self.store.mark_work_automation_sent(
            kind=kind,
            local_date=local_date,
            reminder_id=reminder_id,
            channel_id=channel.id,
            message_id=message.id,
            payload=final_payload,
        )
        return message

    async def _work_automation_ai_text(
        self,
        *,
        kind: str,
        local_date: str,
        reminder_id: str,
        payload: dict,
        fallback_text: str,
    ) -> tuple[str, dict[str, Any]]:
        prompt_payload = {
            "task": "draft_work_automation_message",
            "kind": kind,
            "local_date": local_date,
            "reminder_id": reminder_id,
            "timezone": self.config.timezone,
            "work_window": f"{self.config.work_start_hour:02d}:00-{self.config.work_end_hour:02d}:00",
            "payload": payload,
            "fallback_text": fallback_text,
            "recent_corrections": await self.store.recent_work_ai_corrections(limit=5),
            "rules": [
                "Be short and ADHD-friendly.",
                "Prefer one clear next action.",
                "Do not invent work items or facts not in payload.",
                "Return one Discord-ready message.",
            ],
        }
        try:
            response = await self._run_work_ai_json(_work_automation_ai_prompt(prompt_payload), automation=True)
            message = str(response.get("message") or "").strip()
            if not message:
                raise ValueError("AI automation response missing message")
            suggestion_id = await self.store.create_work_ai_suggestion(
                suggestion_kind="automation_message",
                source_type="automation",
                source_id=None,
                local_date=local_date,
                prompt=prompt_payload,
                response=response,
                confidence=str(response.get("confidence") or "medium"),
                review_reason=str(response.get("review_reason") or "ai_automation_message_sent"),
                status="pending",
            )
            return message, {"source": "ai", "suggestion_id": suggestion_id}
        except Exception as exc:
            LOGGER.warning("Work automation AI failed for %s %s: %s", kind, reminder_id, exc)
            response = {
                "message": fallback_text,
                "confidence": "fallback",
                "review_reason": "ai_failed_used_deterministic_fallback",
            }
            suggestion_id = await self.store.create_work_ai_suggestion(
                suggestion_kind="automation_message",
                source_type="automation",
                source_id=None,
                local_date=local_date,
                prompt=prompt_payload,
                response=response,
                confidence="fallback",
                review_reason="ai_failed_used_deterministic_fallback",
                status="pending",
            )
            return fallback_text, {"source": "fallback", "suggestion_id": suggestion_id}

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

    @tasks.loop(minutes=1)
    async def morning_review_scheduler(self) -> None:
        await self.wait_until_ready()
        if not self.config.morning_review_enabled:
            return
        now_local = datetime.now(self.tz).replace(second=0, microsecond=0)
        if now_local.hour != self.config.morning_review_hour or now_local.minute != self.config.morning_review_minute:
            return
        try:
            await self.publish_morning_report(now_local.date().isoformat())
        except Exception:
            LOGGER.exception("Morning review Discord publish failed")

    async def publish_morning_report(
        self,
        local_date: str,
        *,
        channel=None,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        report_text = read_morning_report(self.config.lifeos_root, local_date)
        if not report_text:
            LOGGER.warning("Morning report missing for %s", local_date)
            return []
        channel = channel or await self._named_channel(self.config.review_channel_name)
        if channel is None:
            LOGGER.warning("Morning review channel #%s not found", self.config.review_channel_name)
            return []

        review_items: list[dict[str, Any]] = []
        for candidate in morning_review_candidates(self.config.lifeos_root, local_date, report_text):
            item = await self.store.create_review_item(**candidate)
            review_items.append(item)

        prioritizer = ReviewPrioritizer()
        digest_builder = ReviewDigestBuilder(prioritizer)
        safe_auto_processor = SafeAutoProcessor(self.store)

        open_items = await self.store.list_review_items(("pending", "needs_clarification", "expired"), limit=50)
        prioritized = prioritizer.prioritize(open_items)
        for item in prioritized:
            await self.store.update_review_item_metadata(
                item["id"],
                priority=item["priority"],
                automation_policy=item["automation_policy"],
            )

        auto_processed = await safe_auto_processor.process_pending(prioritized)
        open_items = await self.store.list_review_items(("pending", "needs_clarification", "expired"), limit=50)
        digest = digest_builder.build(open_items, local_date, auto_processed=auto_processed)

        should_publish = force
        if not force:
            should_publish = await self.store.record_report_publication(
                kind="morning_review_inbox",
                local_date=local_date,
                channel_id=channel.id,
                message_id=None,
            )
        digest_message = None
        if should_publish:
            digest_message = await channel.send(_discord_clip(digest.text, 1800))
            if open_items:
                await self._bind_review_digest_message(channel, digest_message, open_items)
            for item in open_items:
                await self.store.mark_review_item_surfaced(
                    item["id"],
                    parent_discord_message_id=getattr(digest_message, "id", None),
                    surface="morning_digest",
                )
            if not force:
                await self.store.record_report_publication(
                    kind="morning_review_inbox_message",
                    local_date=local_date,
                    channel_id=channel.id,
                    message_id=getattr(digest_message, "id", None),
                )

        for item in digest.cards:
            if item.get("discord_message_id"):
                continue
            await self._post_review_card(channel, item)
        return review_items

    async def _bind_review_digest_message(self, channel, message, items: list[dict[str, Any]]) -> None:
        first = items[0]
        related_ids = ",".join(item["id"] for item in items[:12])
        await self.store.bind_discord_message(
            review_item_id=first["id"],
            discord_message_id=message.id,
            discord_channel_id=channel.id,
            discord_thread_id=getattr(channel, "id", None) if getattr(channel, "type", None) == discord.ChannelType.public_thread else None,
            source_kind="morning_digest",
            source_id=related_ids,
            source_path=None,
            action_on_reply="morning_digest",
            update_review_item_message=False,
        )

    async def _dispatch_review_item_by_source(
        self,
        kind: str,
        source_path: str | None,
        source_record_id: int | str,
        channel,
    ) -> None:
        item_id = review_item_id_for_source(kind, source_path, source_record_id)
        item = await self.store.get_review_item(item_id)
        if item is None:
            return
        if item.get("discord_message_id"):
            return
        await self._post_review_card(channel, item)

    async def _post_review_card(self, channel, item: dict[str, Any]):
        embed = discord.Embed(title=item["title"], description=_review_card_body(item))
        embed.set_footer(text=f"review:{item['id']}")
        message = await channel.send(embed=embed)
        for emoji in REVIEW_REACTIONS:
            await message.add_reaction(emoji)
        await self.store.bind_discord_message(
            review_item_id=item["id"],
            discord_message_id=message.id,
            discord_channel_id=channel.id,
            discord_thread_id=getattr(channel, "id", None) if getattr(channel, "type", None) == discord.ChannelType.public_thread else None,
            source_kind=item.get("source_kind"),
            source_id=item.get("source_record_id"),
            source_path=item.get("source_path"),
            action_on_reply="add_detail",
        )
        await self.store.mark_review_item_surfaced(
            item["id"],
            parent_discord_message_id=item.get("parent_discord_message_id"),
            surface="review_card",
        )
        return message

    async def _run_review_ai_json(self, prompt: str) -> dict:
        configured = self.config.review_ai_cmd or self.config.work_ai_cmd
        if not configured:
            lifeos_alias = Path.home() / ".local" / "bin" / "lifeos"
            configured = str(lifeos_alias) if lifeos_alias.exists() else "hermes"
        args = shlex.split(configured)
        if not args:
            raise RuntimeError("Missing Hermis review AI command")
        env = os.environ.copy()
        env["HERMES_HOME"] = str(self.config.hermes_home)
        env.setdefault("LIFEOS_ROOT", str(self.config.lifeos_root))
        proc = await asyncio.create_subprocess_exec(
            *args,
            "-z",
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.config.lifeos_root),
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or f"Hermis review AI exited {proc.returncode}")
        return _extract_json(stdout.decode("utf-8", errors="replace"))

    async def _run_work_ai_json(self, prompt: str, *, automation: bool) -> dict:
        configured = self.config.work_automation_ai_cmd if automation else self.config.work_ai_cmd
        if not configured:
            lifeos_alias = Path.home() / ".local" / "bin" / "lifeos"
            configured = str(lifeos_alias) if lifeos_alias.exists() else "hermes"
        args = shlex.split(configured)
        if not args:
            raise RuntimeError("Missing Hermis work AI command")
        env = os.environ.copy()
        env["HERMES_HOME"] = str(self.config.hermes_home)
        env.setdefault("LIFEOS_ROOT", str(self.config.lifeos_root))
        proc = await asyncio.create_subprocess_exec(
            *args,
            "-z",
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.config.lifeos_root),
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or f"Hermis work AI exited {proc.returncode}")
        return _extract_json(stdout.decode("utf-8", errors="replace"))

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

        @self.group(name="work", invoke_without_command=True)
        async def work_group(ctx: commands.Context) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            local_date = datetime.now(self.tz).date().isoformat()
            today = await self.store.list_work_today(local_date, limit=8)
            reviews = await self.store.list_work_reviews(limit=5)
            lines = [render_work_items(f"Work today ({local_date})", today)]
            if reviews:
                lines.append("")
                lines.append(f"Unreviewed / unclear captures: {len(reviews)}. Use `!work review`.")
            await ctx.send(_discord_clip("\n".join(lines)))

        @work_group.command(name="add")
        async def work_add(ctx: commands.Context, *, text: str) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            local_day = datetime.now(self.tz).date()
            local_date = local_day.isoformat()
            draft_parse = draft_parse_work_message(text, local_day)
            result = await self.store.log_work_capture(
                local_date=local_date,
                raw_text=text,
                draft_parse=draft_parse,
                message_id=ctx.message.id,
                channel_id=ctx.channel.id,
                channel_name=getattr(ctx.channel, "name", "unknown"),
                logged_by=ctx.author.id,
                source="discord_command",
            )
            if not result.get("created"):
                await ctx.send(f"Work note `{result['capture_id']}` was already captured.")
                return
            suggestion_id = await self._create_work_capture_ai_suggestion(
                capture_id=int(result["capture_id"]),
                local_date=local_date,
                raw_text=text,
                draft_parse=draft_parse,
            )
            if suggestion_id:
                await ctx.send(f"Captured work note `{result['capture_id']}`. AI suggestion:`{suggestion_id}` pending. Use `!work accept suggestion:{suggestion_id}` if right.")
                await self._dispatch_review_item_by_source(
                    "work_suggestion",
                    "state/work.md",
                    suggestion_id,
                    ctx.channel,
                )
            else:
                await ctx.send(f"Captured work note `{result['capture_id']}`. AI draft failed; raw capture is safe.")

        @work_group.command(name="list")
        async def work_list(ctx: commands.Context) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            items = await self.store.list_work_items("active", limit=15)
            await ctx.send(_discord_clip(render_work_items("Active work", items)))

        @work_group.command(name="today")
        async def work_today(ctx: commands.Context) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            local_date = datetime.now(self.tz).date().isoformat()
            items = await self.store.list_work_today(local_date, limit=15)
            await ctx.send(_discord_clip(render_work_items(f"Work today ({local_date})", items)))

        @work_group.command(name="focus")
        async def work_focus(ctx: commands.Context) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            local_date = datetime.now(self.tz).date().isoformat()
            focus, waiting = await self.store.work_focus_items(local_date, limit=5)
            window = f"{self.config.work_start_hour:02d}:00-{self.config.work_end_hour:02d}:00 {self.config.timezone}"
            await ctx.send(_discord_clip(render_work_focus(local_date, window, focus, waiting)))

        @work_group.command(name="automation")
        async def work_automation(ctx: commands.Context) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            local_date = datetime.now(self.tz).date().isoformat()
            events = await self.store.work_automation_status(local_date)
            lines = [
                f"**Work automation {local_date}**",
                f"- prep: {self.config.work_start_hour:02d}:00 - {self.config.work_prep_lead_minutes}m",
                f"- start: {self.config.work_start_hour:02d}:00",
                f"- shutdown: {self.config.work_end_hour:02d}:00 ({'on' if self.config.work_shutdown_review_enabled else 'off'})",
                f"- reminder lookahead: {self.config.work_reminder_lookahead_minutes}m",
            ]
            if events:
                lines.append("")
                lines.append("Sent today:")
                for item in events[:6]:
                    lines.append(f"- {item['kind']} {item['reminder_id']}")
            await ctx.send(_discord_clip("\n".join(lines)))

        @work_group.command(name="plan")
        async def work_plan(ctx: commands.Context) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            plan = await self._work_plan_payload(datetime.now(self.tz))
            local_date = datetime.now(self.tz).date().isoformat()
            await ctx.send(_discord_clip(_work_plan_text("manual", local_date, self.config.timezone, plan)))

        @work_group.command(name="shutdown")
        async def work_shutdown(ctx: commands.Context) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            plan = await self._work_plan_payload(datetime.now(self.tz))
            local_date = datetime.now(self.tz).date().isoformat()
            report_path = await self.store.write_work_shutdown_report(
                local_date,
                focus=plan["focus"],
                overdue=plan["overdue"],
                waiting=plan["waiting"],
                clarifications=plan["clarifications"],
                first_action=plan["first_action"],
            )
            plan["report_path"] = str(report_path.relative_to(self.config.lifeos_root))
            await ctx.send(_discord_clip(_work_shutdown_text(local_date, plan)))

        @work_group.command(name="done")
        async def work_done(ctx: commands.Context, item_id: int) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            local_date = datetime.now(self.tz).date().isoformat()
            item = await self.store.set_work_item_status(
                item_id,
                "done",
                local_date=local_date,
                logged_by=ctx.author.id,
            )
            if item is None:
                await ctx.send(f"No work item `{item_id}` found.")
                return
            await ctx.send(f"Done: `{item_id}` {item['title']}.")

        @work_group.command(name="block")
        async def work_block(ctx: commands.Context, item_id: int, *, reason: str) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            local_date = datetime.now(self.tz).date().isoformat()
            item = await self.store.set_work_item_status(
                item_id,
                "blocked",
                local_date=local_date,
                reason=reason,
                logged_by=ctx.author.id,
            )
            if item is None:
                await ctx.send(f"No work item `{item_id}` found.")
                return
            await ctx.send(f"Blocked: `{item_id}` {item['title']} - {reason}.")

        @work_group.command(name="wait")
        async def work_wait(ctx: commands.Context, item_id: int, *, reason: str) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            local_date = datetime.now(self.tz).date().isoformat()
            item = await self.store.set_work_item_status(
                item_id,
                "waiting",
                local_date=local_date,
                reason=reason,
                logged_by=ctx.author.id,
            )
            if item is None:
                await ctx.send(f"No work item `{item_id}` found.")
                return
            await ctx.send(f"Waiting: `{item_id}` {item['title']} - {reason}.")

        @work_group.command(name="reschedule")
        async def work_reschedule(ctx: commands.Context, item_id: int, *, when: str) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            try:
                due_date, due_at = _parse_work_when(when, datetime.now(self.tz))
            except ValueError as exc:
                await ctx.send(str(exc))
                return
            local_date = datetime.now(self.tz).date().isoformat()
            item = await self.store.reschedule_work_item(
                item_id,
                local_date=local_date,
                due_date=due_date,
                due_at=due_at,
                logged_by=ctx.author.id,
            )
            if item is None:
                await ctx.send(f"No work item `{item_id}` found.")
                return
            due_text = f"{due_date} {due_at or 'EOD'}"
            await ctx.send(f"Rescheduled `{item_id}` to {due_text}.")

        @work_group.command(name="blocker")
        async def work_blocker(ctx: commands.Context, item_id: int, *, reason: str) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            local_date = datetime.now(self.tz).date().isoformat()
            item = await self.store.set_work_item_status(
                item_id,
                "blocked",
                local_date=local_date,
                reason=reason,
                logged_by=ctx.author.id,
            )
            if item is None:
                await ctx.send(f"No work item `{item_id}` found.")
                return
            await self.store.create_work_blocker_prompt(item_id=item_id, local_date=local_date, reason=reason)
            await ctx.send(f"Blocker logged for `{item_id}`: {reason}.")

        @work_group.command(name="snooze")
        async def work_snooze(ctx: commands.Context, item_id: int, duration: str) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            try:
                delta = _parse_duration(duration)
            except ValueError as exc:
                await ctx.send(str(exc))
                return
            local_date = datetime.now(self.tz).date().isoformat()
            until = datetime.now(timezone.utc) + delta
            item = await self.store.snooze_work_item(item_id, until, local_date=local_date)
            if item is None:
                await ctx.send(f"No work item `{item_id}` found.")
                return
            await ctx.send(f"Snoozed `{item_id}` until {until.strftime('%H:%M UTC')}.")

        @work_group.command(name="clarify")
        async def work_clarify(ctx: commands.Context, capture_ref: str, *, answer: str) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            try:
                capture_id = _parse_capture_ref(capture_ref)
            except ValueError as exc:
                await ctx.send(str(exc))
                return
            ok = await self.store.answer_work_clarification(capture_id, answer)
            if not ok:
                await ctx.send(f"No clarification capture `{capture_ref}` found.")
                return
            await ctx.send(f"Clarification saved for capture `{capture_id}`. Hermis will re-review it.")

        @work_group.command(name="review")
        async def work_review(ctx: commands.Context) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            confirmed = await self.store.list_work_items("active", limit=10)
            reviews = await self.store.list_work_reviews(limit=10)
            suggestions = await self.store.list_work_ai_suggestions("pending", limit=8)
            lines = [render_work_items("Confirmed work", confirmed), "", "**Pending AI suggestions**"]
            if suggestions:
                for suggestion in suggestions:
                    response = suggestion["response"]
                    if suggestion["suggestion_kind"] == "capture_parse":
                        if response.get("outcome") == "confirmed":
                            detail = ", ".join(item.get("title", "untitled") for item in response.get("items", [])[:3])
                        elif response.get("outcome") in {"questions", "question", "clarification"}:
                            detail = response.get("question", "clarification")
                        else:
                            detail = response.get("reason") or response.get("review_reason") or response.get("outcome")
                    else:
                        detail = _discord_clip(response.get("message", ""), 110)
                    lines.append(f"- suggestion:`{suggestion['id']}` {suggestion['suggestion_kind']} {suggestion['confidence']}: {detail}")
            else:
                lines.append("- none")
            lines.extend(["", "**Unreviewed / unclear captures**"])
            if reviews:
                for item in reviews:
                    snippet = _discord_clip(" ".join(item["raw_text"].split()), 140)
                    detail = item["clarification_question"] or item["review_reason"]
                    lines.append(f"- capture:`{item['id']}` {item['review_status']} ({detail}): {snippet}")
            else:
                lines.append("- none")
            await ctx.send(_discord_clip("\n".join(lines)))

        @work_group.command(name="accept")
        async def work_accept(ctx: commands.Context, suggestion_ref: str, *, note: str = "") -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            try:
                suggestion_id = _parse_suggestion_ref(suggestion_ref)
                result = await self.store.accept_work_ai_suggestion(suggestion_id, reviewer_note=note)
            except (ValueError, TypeError) as exc:
                await ctx.send(str(exc))
                return
            if result is None:
                await ctx.send(f"No pending AI suggestion `{suggestion_ref}` found.")
                return
            action = result["action"]
            if action == "confirmed":
                ids = ", ".join(f"`{item_id}`" for item_id in result.get("item_ids", [])) or "none"
                await ctx.send(f"Accepted suggestion `{suggestion_id}`. Confirmed work items: {ids}.")
            elif action == "ignored":
                await ctx.send(f"Accepted suggestion `{suggestion_id}`. Capture ignored.")
            elif action == "question":
                await ctx.send(f"Accepted suggestion `{suggestion_id}`. Clarification question opened.")
            else:
                await ctx.send(f"Accepted suggestion `{suggestion_id}`.")

        @work_group.command(name="reject")
        async def work_reject(ctx: commands.Context, suggestion_ref: str, *, reason: str) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            try:
                suggestion_id = _parse_suggestion_ref(suggestion_ref)
                ok = await self.store.reject_work_ai_suggestion(suggestion_id, reason)
            except (ValueError, TypeError) as exc:
                await ctx.send(str(exc))
                return
            if not ok:
                await ctx.send(f"No pending AI suggestion `{suggestion_ref}` found.")
                return
            await ctx.send(f"Rejected suggestion `{suggestion_id}`: {reason}.")

        @work_group.command(name="correct")
        async def work_correct(ctx: commands.Context, suggestion_ref: str, *, correction: str) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            try:
                suggestion_id = _parse_suggestion_ref(suggestion_ref)
            except ValueError as exc:
                await ctx.send(str(exc))
                return
            suggestion = await self.store.get_work_ai_suggestion(suggestion_id)
            if suggestion is None or suggestion["status"] != "pending":
                await ctx.send(f"No pending AI suggestion `{suggestion_ref}` found.")
                return
            if suggestion["suggestion_kind"] != "capture_parse":
                prompt_payload = dict(suggestion["prompt"])
                prompt_payload["correction_note"] = correction
                prompt_payload["recent_corrections"] = await self.store.recent_work_ai_corrections(limit=5)
                try:
                    response = await self._run_work_ai_json(_work_automation_ai_prompt(prompt_payload), automation=True)
                    if not str(response.get("message") or "").strip():
                        raise ValueError("AI automation response missing message")
                except Exception:
                    LOGGER.exception("Work automation AI correction failed for suggestion %s", suggestion_id)
                    await ctx.send("AI correction failed. Old suggestion remains pending.")
                    return
                new_id = await self.store.create_work_ai_suggestion(
                    suggestion_kind=suggestion["suggestion_kind"],
                    source_type=suggestion["source_type"],
                    source_id=suggestion["source_id"],
                    local_date=suggestion["local_date"],
                    prompt=prompt_payload,
                    response=response,
                    confidence=str(response.get("confidence") or "medium"),
                    review_reason=str(response.get("review_reason") or "ai_correction_draft"),
                    supersedes_suggestion_id=suggestion_id,
                )
                await self.store.mark_work_ai_suggestion_corrected(suggestion_id, correction)
                await ctx.send(f"Corrected suggestion `{suggestion_id}`. New AI suggestion:`{new_id}` pending review.")
                return
            capture_id = int(suggestion["source_id"])
            capture = await self.store.get_work_capture(capture_id)
            if capture is None:
                await ctx.send(f"Capture `{capture_id}` not found.")
                return
            new_id = await self._create_work_capture_ai_suggestion(
                capture_id=capture_id,
                local_date=capture["local_date"],
                raw_text=capture["raw_text"],
                draft_parse=capture["draft_parse"],
                correction_note=correction,
                supersedes_suggestion_id=suggestion_id,
            )
            if new_id is None:
                await ctx.send("AI correction failed. Old suggestion remains pending.")
                return
            await self.store.mark_work_ai_suggestion_corrected(suggestion_id, correction)
            await ctx.send(f"Corrected suggestion `{suggestion_id}`. New AI suggestion:`{new_id}` pending review.")

        @self.group(name="review", invoke_without_command=True)
        async def review_group(ctx: commands.Context) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            items = await self.store.list_review_items(limit=10)
            if not items:
                await ctx.send("No review items open.")
                return
            lines = ["Open review items:"]
            for item in items:
                priority = item.get("priority") or "normal"
                lines.append(f"- `{item['id']}` {priority} {item['kind']} {item['status']}: {_discord_clip(item['title'], 90)}")
            await ctx.send(_discord_clip("\n".join(lines)))

        @review_group.command(name="publish")
        async def review_publish(ctx: commands.Context) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            items = await self.store.list_review_items(limit=20)
            posted = 0
            for item in items:
                if item.get("discord_message_id"):
                    continue
                await self._post_review_card(ctx.channel, item)
                posted += 1
            await ctx.send(f"Posted {posted} review card(s).")

        @self.group(name="morning", invoke_without_command=True)
        async def morning_group(ctx: commands.Context, day: str | None = None) -> None:
            if not is_owner_id(ctx.author.id, self.config.discord_owner_ids):
                return
            local_date = day or datetime.now(self.tz).date().isoformat()
            items = await self.publish_morning_report(local_date, channel=ctx.channel, force=True)
            if not items:
                await ctx.send(f"Morning review refreshed for {local_date}. No new review candidates.")

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


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = stripped.removeprefix("json").strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


def _work_capture_ai_prompt(payload: dict) -> str:
    return f"""You are Hermis, drafting a review suggestion for one Life OS work capture.

Return only valid JSON. No markdown. No prose.

AI drafts first, but human review is the only final gate. Do not claim anything was confirmed.
Use recent correction notes if present.

Required JSON shape:
{{
  "outcome": "confirmed|ignored|questions",
  "confidence": "low|medium|high",
  "review_reason": "short reason",
  "items": [
    {{
      "title": "actionable title",
      "priority": "p0|p1|p2|p3",
      "status": "open|waiting|blocked",
      "project": null,
      "area": null,
      "due_date": null,
      "due_at": null,
      "scheduled_date": null,
      "scheduled_at": null,
      "energy": null,
      "effort_minutes": null,
      "context": null,
      "tags": [],
      "note": null
    }}
  ],
  "reason": "required when outcome=ignored",
  "question": "required when outcome=questions"
}}

Payload:
{json.dumps(payload, ensure_ascii=False, sort_keys=True)}
"""


def _work_automation_ai_prompt(payload: dict) -> str:
    return f"""You are Hermis, drafting one proactive Discord work assistant message.

Return only valid JSON. No markdown. No prose.

Use payload as source truth. Do not invent tasks, deadlines, blockers, or facts.
Be short, ADHD-friendly, and action-first.

Required JSON shape:
{{
  "message": "Discord-ready message",
  "confidence": "low|medium|high",
  "review_reason": "short reason"
}}

Payload:
{json.dumps(payload, ensure_ascii=False, sort_keys=True)}
"""


def _normalize_capture_ai_response(response: dict, capture_id: int) -> dict:
    if "confirmed" in response or "ignored" in response or "questions" in response:
        for item in response.get("confirmed") or []:
            if int(item.get("capture_id", -1)) == capture_id:
                response = {
                    "outcome": "confirmed",
                    "items": item.get("items") or [],
                    "confidence": response.get("confidence", "medium"),
                    "review_reason": response.get("review_reason", "ai_confirmed_draft"),
                }
                break
        else:
            for item in response.get("ignored") or []:
                if int(item.get("capture_id", -1)) == capture_id:
                    response = {
                        "outcome": "ignored",
                        "reason": item.get("reason"),
                        "confidence": response.get("confidence", "medium"),
                        "review_reason": response.get("review_reason", "ai_ignored_draft"),
                    }
                    break
            else:
                for item in response.get("questions") or []:
                    if int(item.get("capture_id", -1)) == capture_id:
                        response = {
                            "outcome": "questions",
                            "question": item.get("question"),
                            "confidence": response.get("confidence", "medium"),
                            "review_reason": response.get("review_reason", "ai_question_draft"),
                        }
                        break
    outcome = str(response.get("outcome") or "").strip().lower()
    response["outcome"] = outcome
    response.setdefault("confidence", "low")
    response.setdefault("review_reason", "ai_suggestion_needs_review")
    if outcome == "confirmed":
        items = response.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("confirmed AI suggestion requires items")
        for item in items:
            if not str(item.get("title") or "").strip():
                raise ValueError("confirmed AI suggestion item requires title")
            item.setdefault("priority", "p2")
            item.setdefault("status", "open")
            item.setdefault("tags", [])
    elif outcome == "ignored":
        if not str(response.get("reason") or "").strip():
            raise ValueError("ignored AI suggestion requires reason")
    elif outcome in {"questions", "question", "clarification"}:
        response["outcome"] = "questions"
        if not str(response.get("question") or "").strip():
            raise ValueError("question AI suggestion requires question")
    else:
        raise ValueError(f"unsupported AI suggestion outcome: {outcome or 'missing'}")
    return response


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


def _work_plan_text(mode: str, local_date: str, timezone_name: str, plan: dict) -> str:
    title = {
        "prep": "Work prep",
        "start": "Start work",
        "midshift": "Work check-in",
        "manual": "Work plan",
    }.get(mode, "Work plan")
    lines = [f"**{title} - {local_date} ({timezone_name})**"]
    first = plan.get("first_action")
    if first:
        lines.append(f"Next: #{first['id']} {first['title']}")
        if first.get("effort_minutes"):
            lines.append(f"Start with {min(int(first['effort_minutes']), 10)} min draft.")
    else:
        lines.append("Next: clear captures or pick one small task.")
    if plan.get("overdue"):
        item = plan["overdue"][0]
        lines.append(f"Overdue: #{item['id']} {item['title']} - answer blocker.")
    if plan.get("p01"):
        text = ", ".join(f"#{item['id']} {item['title']}" for item in plan["p01"][:3])
        lines.append(f"P0/P1: {text}")
    if plan.get("waiting"):
        text = ", ".join(f"#{item['id']} {item['title']}" for item in plan["waiting"][:2])
        lines.append(f"Blocked/waiting: {text}")
    if plan.get("clarifications"):
        item = plan["clarifications"][0]
        lines.append(f"Clarify capture:{item['id']}: {item['question']}")
    if mode == "prep" and plan.get("prep_items"):
        item = plan["prep_items"][0]
        lines.append(f"Prep: gather context for #{item['id']} {item['title']}.")
    lines.append("Reply with `!work done`, `!work blocker`, `!work wait`, or `!work reschedule`.")
    return "\n".join(lines)


def _work_shutdown_text(local_date: str, plan: dict) -> str:
    lines = [
        f"**Work shutdown - {local_date}**",
        "Reply short:",
        "1. done?",
        "2. still open?",
        "3. blocked?",
        "4. first tomorrow?",
    ]
    if plan.get("clarifications"):
        item = plan["clarifications"][0]
        lines.append(f"Clarify: capture:{item['id']} - {item['question']}")
    if plan.get("first_action"):
        item = plan["first_action"]
        lines.append(f"Suggested first tomorrow: #{item['id']} {item['title']}")
    if plan.get("report_path"):
        lines.append(f"Report: `{plan['report_path']}`")
    return "\n".join(lines)


def _work_due_text(item: dict) -> str:
    title = item.get("title") or "work item"
    when = item.get("due_at") or item.get("scheduled_at") or "end of shift"
    effort = item.get("effort_minutes")
    start = "Start with a 10-minute draft." if effort else "Pick first concrete step."
    return f"Reminder: #{item['id']} {title} is due by {when}. {start}"


def _work_overdue_text(item: dict) -> str:
    return (
        f"#{item['id']} is overdue. What blocked it: unclear next step, waiting on someone, "
        "too big, forgot, low energy, or no longer needed?\n"
        f"Use `!work blocker {item['id']} <reason>`, `!work wait {item['id']} <reason>`, "
        f"`!work reschedule {item['id']} <date/time>`, or `!work done {item['id']}`."
    )


def _work_waiting_text(item: dict) -> str:
    title = item.get("title") or "work item"
    note = item.get("note")
    suffix = f" ({note})" if note else ""
    return f"#{item['id']} waiting: {title}{suffix}. Follow up today or keep waiting?"


def _parse_work_when(value: str, now_local: datetime) -> tuple[str, str | None]:
    text = value.strip()
    if not text:
        raise ValueError("Use date/time like `2026-05-04 16:30`, `2026-05-04`, or `16:30`.")
    parts = text.split()
    if len(parts) == 1 and ":" in parts[0]:
        _validate_hhmm(parts[0])
        return now_local.date().isoformat(), parts[0]
    try:
        day = date.fromisoformat(parts[0]).isoformat()
    except ValueError as exc:
        raise ValueError("Use date/time like `2026-05-04 16:30`, `2026-05-04`, or `16:30`.") from exc
    if len(parts) == 1:
        return day, None
    _validate_hhmm(parts[1])
    return day, parts[1]


def _validate_hhmm(value: str) -> None:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
    except ValueError as exc:
        raise ValueError("Time must be `HH:MM`.") from exc
    if hour > 23 or minute > 59:
        raise ValueError("Time must be `HH:MM`.")


def _parse_duration(value: str) -> timedelta:
    token = value.strip().lower()
    if token.endswith("m"):
        amount = int(token[:-1])
        return timedelta(minutes=amount)
    if token.endswith("h"):
        amount = int(token[:-1])
        return timedelta(hours=amount)
    raise ValueError("Use duration like `30m` or `2h`.")


def _parse_capture_ref(value: str) -> int:
    token = value.strip().lower()
    if token.startswith("capture:"):
        token = token.split(":", 1)[1]
    try:
        return int(token)
    except ValueError as exc:
        raise ValueError("Use `capture:<id>`.") from exc


def _parse_suggestion_ref(value: str) -> int:
    token = value.strip().lower()
    if token.startswith("suggestion:"):
        token = token.split(":", 1)[1]
    try:
        return int(token)
    except ValueError as exc:
        raise ValueError("Use `suggestion:<id>`.") from exc


def _discord_clip(text: str, limit: int = 1900) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _review_card_body(item: dict[str, Any]) -> str:
    body = str(item.get("body") or "").strip()
    source = item.get("source_path") or item.get("source_kind") or "unknown source"
    missing = item.get("missing_context") or []
    lines = [
        _discord_clip(body, 950),
        "",
        f"ID: `{item['id']}`",
        f"Status: `{item.get('status')}`",
        f"Source: `{source}`",
        "",
        "React: ✅ approve | ❌ reject | ❓ clarify | 📝 add details",
        "Or reply to this message.",
    ]
    if missing:
        lines.insert(1, f"Needs: {', '.join(str(value) for value in missing[:3])}")
    return _discord_clip("\n".join(lines), 1900)


def _review_followup_question(item: dict[str, Any]) -> str:
    validation = item.get("ai_validation") or {}
    question = str(validation.get("clarification_question") or "").strip()
    if question:
        return question
    missing = item.get("missing_context") or []
    if missing:
        return f"Can you clarify: {missing[0]}"
    return "What should I change or add before this becomes durable truth?"


def _binding_related_review_ids(binding: dict[str, Any]) -> list[str]:
    ids = [str(binding.get("review_item_id") or "")]
    if binding.get("action_on_reply") == "morning_digest":
        raw = str(binding.get("source_id") or "")
        ids.extend(part.strip() for part in raw.split(",") if part.strip())
    seen: set[str] = set()
    result = []
    for item_id in ids:
        if item_id and item_id not in seen:
            seen.add(item_id)
            result.append(item_id)
    return result


def _linked_reply_suffix(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    ids = ", ".join(f"`{item['id']}`" for item in items[:3])
    more = f" and {len(items) - 3} more" if len(items) > 3 else ""
    return f" Also updated {ids}{more}."


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
