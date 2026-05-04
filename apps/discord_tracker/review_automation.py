from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


PRIORITIES = ("urgent", "normal", "low")
PRIORITY_RANK = {value: index for index, value in enumerate(PRIORITIES)}

REQUIRES_APPROVAL = "requires_explicit_approval"
LOW_RISK_REVERSIBLE = "low_risk_reversible"
AUTO_PROCESS_SAFE = "auto_process_safe"
SENSITIVE_REVIEW = "sensitive_requires_approval"

SENSITIVE_SOURCE_KINDS = {
    "finance_parse_review",
    "finance_review",
    "health_review",
    "family_review",
    "legal_review",
    "memory_review",
    "durable_memory",
    "identity_memory",
}
SENSITIVE_TERMS = {
    "finance",
    "money",
    "transaction",
    "payment",
    "health",
    "medical",
    "doctor",
    "family",
    "wife",
    "legal",
    "lawyer",
    "contract",
    "identity",
    "durable memory",
    "memory claim",
}
SAFE_AUTO_SOURCE_KINDS = {
    "needs_answer",
    "review_fallback",
    "tracker_summary",
    "low_risk_note",
}
BLOCKING_SOURCE_KINDS = {
    "finance_parse_review",
    "work_ai_suggestion",
    "morning_report",
    "memory_review",
}


@dataclass(frozen=True)
class ReviewDigest:
    text: str
    cards: list[dict[str, Any]]
    grouped: dict[str, list[dict[str, Any]]]


class ReviewPrioritizer:
    """Compute review priority and resurfacing order from durable item fields."""

    def __init__(self, now_utc: datetime | None = None):
        self.now_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)

    def derive_automation_policy(self, item: dict[str, Any]) -> str:
        existing = str(item.get("automation_policy") or "").strip()
        if existing:
            return existing
        source_kind = str(item.get("source_kind") or "").strip()
        kind = str(item.get("kind") or "").strip()
        if is_sensitive_review(item):
            return SENSITIVE_REVIEW
        if source_kind in SAFE_AUTO_SOURCE_KINDS:
            return LOW_RISK_REVERSIBLE
        if kind in {"open_question", "morning_question"}:
            return LOW_RISK_REVERSIBLE
        return REQUIRES_APPROVAL

    def compute_priority(self, item: dict[str, Any]) -> str:
        status = str(item.get("status") or "")
        source_kind = str(item.get("source_kind") or "")
        confidence = _confidence(item)
        missing = bool(item.get("missing_context"))
        age_hours = _hours_since(item.get("created_at_utc"), self.now_utc)
        expires_in = _hours_until(item.get("expires_at_utc"), self.now_utc)
        text = _review_text(item)

        if status == "expired":
            return "urgent"
        if "block" in text or "overdue" in text or "due today" in text:
            return "urgent"
        if source_kind in {"finance_parse_review", "memory_review"}:
            return "urgent"
        if source_kind == "work_ai_suggestion" and (confidence == "low" or missing):
            return "urgent"
        if expires_in is not None and expires_in <= 3:
            return "urgent"
        if age_hours is not None and age_hours >= 48 and missing:
            return "urgent"
        if confidence == "low" and missing:
            return "urgent"
        if self.derive_automation_policy(item) == AUTO_PROCESS_SAFE and confidence == "high" and not missing:
            return "low"
        if source_kind in BLOCKING_SOURCE_KINDS or missing:
            return "normal"
        if confidence == "high":
            return "low"
        return "normal"

    def prioritize(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared = []
        for item in items:
            enriched = dict(item)
            enriched["priority"] = self.compute_priority(enriched)
            enriched["automation_policy"] = self.derive_automation_policy(enriched)
            prepared.append(enriched)
        return sorted(prepared, key=self.sort_key)

    def sort_key(self, item: dict[str, Any]) -> tuple[Any, ...]:
        expires_at = _parse_dt(item.get("expires_at_utc"))
        last_surface_at = _parse_dt(item.get("last_surface_at"))
        created_at = _parse_dt(item.get("created_at_utc"))
        return (
            PRIORITY_RANK.get(str(item.get("priority") or "normal"), 1),
            expires_at or datetime.max.replace(tzinfo=timezone.utc),
            int(item.get("surface_count") or 0),
            last_surface_at or datetime.min.replace(tzinfo=timezone.utc),
            created_at or datetime.max.replace(tzinfo=timezone.utc),
        )


class ReviewDigestBuilder:
    """Build one compact morning review inbox plus the few cards worth posting."""

    def __init__(self, prioritizer: ReviewPrioritizer | None = None, top_card_limit: int = 3):
        self.prioritizer = prioritizer or ReviewPrioritizer()
        self.top_card_limit = top_card_limit

    def build(
        self,
        items: list[dict[str, Any]],
        local_date: str,
        *,
        auto_processed: list[dict[str, Any]] | None = None,
        limit: int = 1800,
    ) -> ReviewDigest:
        prioritized = self.prioritizer.prioritize(items)
        grouped = {
            "needs decision now": [],
            "answer when easy": [],
            "FYI / auto-processing": [],
        }
        for item in prioritized:
            if item["priority"] == "urgent":
                grouped["needs decision now"].append(item)
            elif item["priority"] == "low":
                grouped["FYI / auto-processing"].append(item)
            else:
                grouped["answer when easy"].append(item)

        cards = [
            item
            for item in prioritized
            if item.get("priority") in {"urgent", "normal"}
            and item.get("status") in {"pending", "needs_clarification", "expired"}
        ][: self.top_card_limit]

        lines = [f"Today's Review Inbox - {local_date}"]
        total = sum(len(value) for value in grouped.values())
        if auto_processed:
            lines.append(f"{total} open, {len(auto_processed)} auto-processed safely.")
        else:
            lines.append(f"{total} open review item(s).")

        if total == 0 and not auto_processed:
            lines.append("No open review items.")
        for label, values in grouped.items():
            lines.extend(["", f"{label}:"])
            if not values:
                lines.append("- none")
                continue
            for item in values[:5]:
                lines.append(_digest_line(item))
            if len(values) > 5:
                lines.append(f"- +{len(values) - 5} more")

        if auto_processed:
            lines.extend(["", "auto-processed safely:"])
            for item in auto_processed[:5]:
                lines.append(f"- `{item['id']}` {_clip(str(item.get('title') or 'review item'), 90)}")

        text = "\n".join(lines).strip()
        return ReviewDigest(text=_clip(text, limit), cards=cards, grouped=grouped)


class SafeAutoProcessor:
    """Apply only high-confidence, low-risk, reversible review automations."""

    def __init__(self, store, now_utc: datetime | None = None):
        self.store = store
        self.prioritizer = ReviewPrioritizer(now_utc)

    def refusal_reason(self, item: dict[str, Any]) -> str | None:
        source_kind = str(item.get("source_kind") or "")
        policy = str(item.get("automation_policy") or "") or self.prioritizer.derive_automation_policy(item)
        validation = item.get("ai_validation") or {}
        confidence = str(validation.get("confidence") or item.get("confidence") or "").lower()
        missing_context = list(item.get("missing_context") or []) + list(validation.get("missing_context") or [])
        contradictions = validation.get("contradictions") or []
        unsafe = validation.get("unsafe_assumptions") or []

        if is_sensitive_review(item):
            return "sensitive review requires explicit approval"
        if source_kind == "work_ai_suggestion" and policy != LOW_RISK_REVERSIBLE:
            return "work suggestions require explicit approval unless marked low-risk reversible"
        if source_kind not in SAFE_AUTO_SOURCE_KINDS and not (
            source_kind == "work_ai_suggestion" and policy == LOW_RISK_REVERSIBLE
        ):
            return "source kind is not explicitly safe for auto-processing"
        if policy not in {AUTO_PROCESS_SAFE, LOW_RISK_REVERSIBLE}:
            return "automation policy is not safe-auto"
        if confidence != "high":
            return "validation confidence is not high"
        if missing_context:
            return "validation has missing context"
        if contradictions:
            return "validation has contradictions"
        if unsafe:
            return "validation has unsafe assumptions"
        if not validation.get("safe_to_persist"):
            return "validation did not mark safe_to_persist"
        return None

    def can_auto_process(self, item: dict[str, Any]) -> bool:
        return self.refusal_reason(item) is None

    async def process_pending(
        self,
        items: list[dict[str, Any]] | None = None,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        candidates = items
        if candidates is None:
            candidates = await self.store.list_review_items(("pending", "needs_clarification"), limit=limit)
        processed = []
        for item in candidates[:limit]:
            if self.can_auto_process(item):
                updated = await self.process_item(item)
                if updated is not None:
                    processed.append(updated)
        return processed

    async def process_item(self, item: dict[str, Any]) -> dict[str, Any] | None:
        reason = "high-confidence safe automation"
        if item.get("source_kind") == "work_ai_suggestion" and item.get("source_record_id"):
            await self.store.accept_work_ai_suggestion(
                int(item["source_record_id"]),
                reviewer_note=f"auto-processed: {reason}",
            )
        await self.store.update_review_item_metadata(
            item["id"],
            priority=self.prioritizer.compute_priority(item),
            automation_policy=item.get("automation_policy") or self.prioritizer.derive_automation_policy(item),
            auto_process_reason=reason,
        )
        return await self.store.set_review_item_status(
            item["id"],
            "auto_processed",
            note=reason,
        )

    async def expire_low_risk(self, now_utc: datetime | None = None) -> list[dict[str, Any]]:
        return await self.store.expire_review_items(
            now_utc=now_utc,
            eligible_automation_policies=(LOW_RISK_REVERSIBLE, AUTO_PROCESS_SAFE),
        )


def is_sensitive_review(item: dict[str, Any]) -> bool:
    source_kind = str(item.get("source_kind") or "").strip()
    if source_kind in SENSITIVE_SOURCE_KINDS:
        return True
    text = _review_text(item)
    if "memory" in text and ("durable" in text or "identity" in text or "claim" in text):
        return True
    return any(term in text for term in SENSITIVE_TERMS)


def _digest_line(item: dict[str, Any]) -> str:
    source = item.get("source_kind") or item.get("source_path") or "review"
    status = item.get("status") or "pending"
    title = _clip(str(item.get("title") or "review item"), 96)
    return f"- `{item['id']}` {title} ({status}, {source})"


def _confidence(item: dict[str, Any]) -> str:
    validation = item.get("ai_validation") or {}
    return str(validation.get("confidence") or item.get("confidence") or "low").lower()


def _review_text(item: dict[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in (
            item.get("kind"),
            item.get("title"),
            item.get("body"),
            item.get("source_kind"),
            item.get("source_path"),
        )
    ).lower()


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hours_since(value: Any, now_utc: datetime) -> float | None:
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    return (now_utc - parsed).total_seconds() / 3600


def _hours_until(value: Any, now_utc: datetime) -> float | None:
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    return (parsed - now_utc).total_seconds() / 3600


def _clip(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."
