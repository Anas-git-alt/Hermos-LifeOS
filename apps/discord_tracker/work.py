from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any


WORK_PRIORITIES = ("p0", "p1", "p2", "p3")
WORK_ITEM_STATUSES = ("open", "waiting", "blocked", "done", "cancelled")
WORK_CAPTURE_REVIEW_STATUSES = ("unreviewed", "confirmed", "clarification", "ignored")
WORK_TIMEZONE = "Africa/Casablanca"
WORK_WINDOW_START_HOUR = 14
WORK_WINDOW_END_HOUR = 23

WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


@dataclass(frozen=True)
class WorkItemDraft:
    title: str
    priority: str = "p2"
    status: str = "open"
    project: str | None = None
    area: str | None = None
    due_date: str | None = None
    due_at: str | None = None
    scheduled_date: str | None = None
    scheduled_at: str | None = None
    energy: str | None = None
    effort_minutes: int | None = None
    context: str | None = None
    tags: tuple[str, ...] = ()
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tags"] = list(self.tags)
        return payload


def should_capture_work_message(raw_text: str) -> bool:
    text = raw_text.strip()
    if not text or text.startswith("!"):
        return False
    return True


def split_work_message(raw_text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        line = re.sub(r"^\[(?: |x|X)\]\s+", "", line)
        line = re.sub(r"^(todo|task|work|next|mit)[:\s-]+", "", line, flags=re.IGNORECASE)
        if line:
            lines.append(line)
    return lines


def draft_parse_work_message(raw_text: str, today: date | None = None) -> dict[str, Any]:
    """Return parser hints only. These hints must never create final work items."""

    today = today or date.today()
    raw = raw_text.strip()
    if not raw:
        return {
            "status": "draft_parse",
            "confidence": "low",
            "review_reason": "empty_capture",
            "candidates": [],
            "raw_text": raw_text,
        }

    lines = split_work_message(raw_text)
    candidates: list[dict[str, Any]] = []
    low_confidence = False
    for line in lines:
        draft = _parse_work_line(line, today)
        if draft is None:
            low_confidence = True
            continue
        if _looks_too_vague(draft.title):
            low_confidence = True
        candidates.append(draft.to_dict())

    if not candidates:
        return {
            "status": "draft_parse",
            "confidence": "low",
            "review_reason": "no_actionable_candidate_detected",
            "candidates": [],
            "raw_text": raw_text,
        }

    confidence = "low" if low_confidence else ("medium" if len(candidates) == 1 else "medium")
    return {
        "status": "draft_parse",
        "confidence": confidence,
        "review_reason": "draft_only_requires_hermis_review",
        "candidates": candidates,
        "raw_text": raw_text,
    }


def draft_parse_json(raw_text: str, today: date | None = None) -> str:
    return json.dumps(draft_parse_work_message(raw_text, today), ensure_ascii=False, sort_keys=True)


def item_from_manual_text(raw_text: str, today: date | None = None) -> list[WorkItemDraft]:
    today = today or date.today()
    drafts = [_parse_work_line(line, today) for line in split_work_message(raw_text)]
    return [draft for draft in drafts if draft is not None]


def normalize_work_priority(value: Any) -> str:
    token = str(value or "p2").strip().lower()
    token = token.replace("priority", "").replace(":", "").strip()
    if token in WORK_PRIORITIES:
        return token
    if token in {"0", "urgent", "critical", "now", "must"}:
        return "p0"
    if token in {"1", "high", "important", "next"}:
        return "p1"
    if token in {"2", "normal", "medium"}:
        return "p2"
    if token in {"3", "low", "backlog", "someday"}:
        return "p3"
    return "p2"


def render_work_item_line(item: dict[str, Any]) -> str:
    parts = [f"`{item['id']}`", str(item.get("priority") or "p2").upper(), str(item.get("title") or "")]
    due = item.get("due_date")
    if due:
        parts.append(f"due {due}")
    project = item.get("project")
    if project:
        parts.append(f"project:{project}")
    status = item.get("status")
    if status and status != "open":
        parts.append(f"status:{status}")
    return " - ".join(part for part in parts if part)


def render_work_items(title: str, items: list[dict[str, Any]]) -> str:
    if not items:
        return f"**{title}**\nNo matching work items."
    lines = [f"**{title}**"]
    lines.extend(f"- {render_work_item_line(item)}" for item in items)
    return "\n".join(lines)


def render_work_focus(
    local_date: str,
    work_window: str,
    focus: list[dict[str, Any]],
    waiting: list[dict[str, Any]],
) -> str:
    lines = [f"**Work focus for {local_date} ({work_window})**"]
    if focus:
        for index, item in enumerate(focus, start=1):
            lines.append(f"{index}. {render_work_item_line(item)}")
    else:
        lines.append("No confirmed focus items.")
    if waiting:
        lines.append("")
        lines.append("Blocked / waiting:")
        lines.extend(f"- {render_work_item_line(item)}" for item in waiting[:5])
    return "\n".join(lines)


def _parse_work_line(text: str, today: date) -> WorkItemDraft | None:
    original = " ".join(text.strip().split())
    if not original:
        return None
    lowered = original.lower()
    title = _clean_title(original)
    if not title:
        return None
    due_date = _parse_due_date(lowered, today)
    due_at = _parse_time_token(lowered, "due_at")
    scheduled_at = _parse_time_token(lowered, "scheduled_at")
    return WorkItemDraft(
        title=title,
        priority=_parse_priority(lowered, due_date, today),
        status=_parse_status(lowered),
        project=_parse_named_token(original, "project"),
        area=_parse_named_token(original, "area"),
        due_date=due_date,
        due_at=due_at,
        scheduled_date=_parse_scheduled_date(lowered, today),
        scheduled_at=scheduled_at,
        energy=_parse_energy(lowered),
        effort_minutes=_parse_effort_minutes(lowered),
        context=_parse_context(original),
        tags=tuple(dict.fromkeys(tag.lower() for tag in re.findall(r"(?<!\w)#([A-Za-z][\w-]*)", original))),
    )


def _parse_status(lowered: str) -> str:
    if lowered.startswith(("blocked", "blocker")) or " blocked by " in lowered:
        return "blocked"
    if lowered.startswith(("waiting", "wait for", "follow up")) or " waiting for " in lowered:
        return "waiting"
    return "open"


def _parse_priority(lowered: str, due_date: str | None, today: date) -> str:
    explicit = re.search(r"\bpriority[:=]?\s*p?([0-3])\b|\bp([0-3])\b", lowered)
    if explicit:
        return f"p{explicit.group(1) or explicit.group(2)}"
    if any(token in lowered for token in ("urgent", "asap", "right now", "critical", "blocker")):
        return "p0"
    if any(token in lowered for token in ("important", "next action", "mit", "high priority")):
        return "p1"
    if any(token in lowered for token in ("someday", "backlog", "low priority", "nice to have")):
        return "p3"
    if due_date:
        due = date.fromisoformat(due_date)
        if due <= today:
            return "p0"
        if due <= today + timedelta(days=1):
            return "p1"
    return "p2"


def _parse_due_date(lowered: str, today: date) -> str | None:
    iso = re.search(r"\b(?:due|by|deadline)[:\s]+(20\d{2}-\d{2}-\d{2})\b", lowered)
    if iso:
        return iso.group(1)
    if re.search(r"\b(?:today|eod|end of day)\b", lowered):
        return today.isoformat()
    if re.search(r"\btomorrow\b", lowered):
        return (today + timedelta(days=1)).isoformat()
    in_days = re.search(r"\bin\s+(\d{1,2})\s+days?\b", lowered)
    if in_days:
        return (today + timedelta(days=int(in_days.group(1)))).isoformat()
    weekday = re.search(
        r"\b(?:by|due|deadline|next)?\s*(mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:r|rs|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b",
        lowered,
    )
    if not weekday:
        return None
    target = WEEKDAYS.get(weekday.group(1))
    if target is None:
        return None
    delta = (target - today.weekday()) % 7
    if delta == 0 and "next" in lowered:
        delta = 7
    return (today + timedelta(days=delta)).isoformat()


def _parse_scheduled_date(lowered: str, today: date) -> str | None:
    match = re.search(r"\b(?:schedule|scheduled|start)[:\s]+(20\d{2}-\d{2}-\d{2})\b", lowered)
    if match:
        return match.group(1)
    if "start tomorrow" in lowered:
        return (today + timedelta(days=1)).isoformat()
    return None


def _parse_time_token(lowered: str, key: str) -> str | None:
    match = re.search(rf"\b{re.escape(key)}[:=]\s*(\d{{1,2}}):(\d{{2}})\b", lowered)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _parse_named_token(text: str, key: str) -> str | None:
    match = re.search(rf"\b{re.escape(key)}[:=]([^#@|;]+)", text, flags=re.IGNORECASE)
    if not match:
        return None
    value = match.group(1).strip()
    value = re.split(
        r"\s{2,}|\sdue:|\sdeadline:|\spriority:|\sarea:|\sproject:|\sschedule:",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return value.strip() or None


def _parse_energy(lowered: str) -> str | None:
    match = re.search(r"\benergy[:=]\s*(low|medium|med|high)\b", lowered)
    if match:
        return "medium" if match.group(1) == "med" else match.group(1)
    if "low energy" in lowered:
        return "low"
    if "high energy" in lowered or "deep work" in lowered:
        return "high"
    return None


def _parse_effort_minutes(lowered: str) -> int | None:
    match = re.search(r"\beffort[:=]\s*(\d{1,3})\s*(m|min|mins|minutes|h|hr|hrs|hours)?\b", lowered)
    if match:
        amount = int(match.group(1))
        unit = match.group(2) or "m"
        return amount * 60 if unit.startswith("h") else amount
    match = re.search(r"\b(\d{1,3})\s*(m|min|mins|minutes|h|hr|hrs|hours)\b", lowered)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    return amount * 60 if unit.startswith("h") else amount


def _parse_context(text: str) -> str | None:
    match = re.search(r"(?<!\w)@([A-Za-z][\w-]*)", text)
    return match.group(1).lower() if match else None


def _clean_title(text: str) -> str:
    title = text.strip()
    title = re.sub(r"^\[([^\]]{1,60})\]\s*", "", title)
    title = re.sub(r"^(todo|task|work|next|mit|blocked|blocker|waiting|follow up)[:\s-]+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\bpriority[:=]?\s*p?[0-3]\b|\bp[0-3]\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:due|by|deadline)[:\s]+20\d{2}-\d{2}-\d{2}\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:due_at|scheduled_at)[:=]\s*\d{1,2}:\d{2}\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:due|by|deadline)[:\s]+(?:today|tomorrow|mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:r|rs|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:today|tomorrow|eod|end of day)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\bschedule(?:d)?[:\s]+20\d{2}-\d{2}-\d{2}\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\bstart tomorrow\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\beffort[:=]\s*\d{1,3}\s*(?:m|min|mins|minutes|h|hr|hrs|hours)?\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b\d{1,3}\s*(?:m|min|mins|minutes|h|hr|hrs|hours)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\benergy[:=]\s*(?:low|medium|med|high)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:project|area)[:=][^#@|;]+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"(?<!\w)[#@][A-Za-z][\w-]*", "", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip(" -:;|")


def _looks_too_vague(title: str) -> bool:
    lowered = title.lower().strip()
    vague = {"maybe", "stuff", "things", "work", "later", "todo", "follow up", "fix"}
    if lowered in vague:
        return True
    return len(lowered.split()) <= 1
