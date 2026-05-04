from __future__ import annotations

import json
from typing import Any, Awaitable, Callable


AIRunner = Callable[[str], Awaitable[dict[str, Any]]]


class AIInputInterpreter:
    """Interpret one natural-language review reply into a structured draft."""

    def __init__(self, runner: AIRunner | None = None):
        self.runner = runner

    async def interpret(self, raw_text: str, context: dict[str, Any]) -> dict[str, Any]:
        if self.runner is None:
            return _safe_interpretation(
                raw_text,
                context,
                "AI interpreter is not configured.",
            )
        try:
            result = await self.runner(_interpret_prompt(raw_text, context))
        except Exception as exc:
            return _safe_interpretation(raw_text, context, f"AI interpreter failed: {exc}")
        return normalize_interpretation(result, raw_text, context)


class AIValidationPass:
    """Validate and improve an interpretation before any durable mutation."""

    def __init__(self, runner: AIRunner | None = None):
        self.runner = runner

    async def validate(self, interpretation: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if self.runner is not None:
            try:
                result = await self.runner(_validation_prompt(interpretation, context))
                return normalize_validation(result, interpretation)
            except Exception:
                pass
        return normalize_validation({}, interpretation)


def normalize_interpretation(
    result: dict[str, Any],
    raw_text: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return _safe_interpretation(raw_text, context, "AI interpreter returned non-object JSON.")
    item = context.get("review_item") or {}
    intent = _text(result.get("intent")) or "unknown"
    missing_context = _string_list(result.get("missing_context"))
    confidence = _confidence(result.get("confidence"))
    proposed_update = result.get("proposed_update")
    if not isinstance(proposed_update, dict):
        proposed_update = {}
    return {
        "intent": intent,
        "source_review_item_id": _text(result.get("source_review_item_id")) or item.get("id"),
        "entities": result.get("entities") if isinstance(result.get("entities"), dict) else {},
        "dates": _string_list(result.get("dates")),
        "commitments": result.get("commitments") if isinstance(result.get("commitments"), list) else [],
        "tasks": result.get("tasks") if isinstance(result.get("tasks"), list) else [],
        "notes": result.get("notes") if isinstance(result.get("notes"), list) else [],
        "corrections": result.get("corrections") if isinstance(result.get("corrections"), list) else [],
        "answers": result.get("answers") if isinstance(result.get("answers"), list) else [],
        "proposed_update": proposed_update,
        "confidence": confidence,
        "missing_context": missing_context,
        "raw_text": raw_text,
    }


def normalize_validation(result: dict[str, Any], interpretation: dict[str, Any]) -> dict[str, Any]:
    result = result if isinstance(result, dict) else {}
    missing_context = _string_list(result.get("missing_context")) or _string_list(
        interpretation.get("missing_context")
    )
    confidence = _confidence(result.get("confidence") or interpretation.get("confidence"))
    decision = _text(result.get("decision"))
    clarification_question = _text(result.get("clarification_question"))
    safe_to_persist = bool(result.get("safe_to_persist"))
    improved_update = result.get("improved_update")
    if not isinstance(improved_update, dict):
        improved_update = dict(interpretation.get("proposed_update") or {})

    if not decision:
        if confidence == "low" or missing_context:
            decision = "ask_clarification"
        elif improved_update:
            decision = "propose_update"
        else:
            decision = "acknowledge_detail"

    if decision == "ask_clarification" and not clarification_question:
        clarification_question = _fallback_question(missing_context)

    valid = bool(result.get("valid", decision != "ask_clarification"))
    return {
        "valid": valid,
        "decision": decision,
        "confidence": confidence,
        "missing_context": missing_context,
        "contradictions": _string_list(result.get("contradictions")),
        "unsafe_assumptions": _string_list(result.get("unsafe_assumptions")),
        "clarification_question": clarification_question,
        "safe_to_persist": safe_to_persist and decision != "ask_clarification",
        "proposed_status": _text(result.get("proposed_status")),
        "related_review_item_ids": _string_list(result.get("related_review_item_ids")),
        "improved_update": improved_update,
    }


def _safe_interpretation(raw_text: str, context: dict[str, Any], reason: str) -> dict[str, Any]:
    item = context.get("review_item") or {}
    return {
        "intent": "unknown",
        "source_review_item_id": item.get("id"),
        "entities": {},
        "dates": [],
        "commitments": [],
        "tasks": [],
        "notes": [raw_text] if raw_text else [],
        "corrections": [],
        "answers": [],
        "proposed_update": {},
        "confidence": "low",
        "missing_context": [reason],
        "raw_text": raw_text,
    }


def _interpret_prompt(raw_text: str, context: dict[str, Any]) -> str:
    return f"""You are Hermis, interpreting one Discord reply for a Life OS review item.

Return only valid JSON. No markdown. No prose.

The filesystem/wiki remains durable truth. Do not finalize important updates here.
Interpret the user's natural-language reply into a structured draft.

Required JSON shape:
{{
  "intent": "answer_question|add_detail|correction|clarification|approve_with_context|reject|unknown",
  "source_review_item_id": "review item id if known",
  "entities": {{}},
  "dates": [],
  "commitments": [],
  "tasks": [],
  "notes": [],
  "corrections": [],
  "answers": [],
  "proposed_update": {{}},
  "confidence": "low|medium|high",
  "missing_context": []
}}

Context:
{json.dumps(context, ensure_ascii=False, sort_keys=True)}

Raw reply:
{raw_text}
"""


def _validation_prompt(interpretation: dict[str, Any], context: dict[str, Any]) -> str:
    return f"""You are Hermis, validating a structured interpretation before Life OS persists anything important.

Return only valid JSON. No markdown. No prose.

Check ambiguity, missing context, contradictions, unsafe assumptions, and low confidence.
Improve the proposed update if possible. If the update is unclear, ask one short clarification question.
Do not mark safe_to_persist true unless the user clearly approved or only harmless detail is being attached.

Required JSON shape:
{{
  "valid": true,
  "decision": "acknowledge_detail|propose_update|ask_clarification",
  "confidence": "low|medium|high",
  "missing_context": [],
  "contradictions": [],
  "unsafe_assumptions": [],
  "clarification_question": "",
  "safe_to_persist": false,
  "proposed_status": "pending|approved|rejected|needs_clarification|",
  "related_review_item_ids": [],
  "improved_update": {{}}
}}

Context:
{json.dumps(context, ensure_ascii=False, sort_keys=True)}

Interpretation:
{json.dumps(interpretation, ensure_ascii=False, sort_keys=True)}
"""


def _confidence(value: Any) -> str:
    token = str(value or "low").strip().lower()
    return token if token in {"low", "medium", "high"} else "low"


def _string_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    return [_text(value)]


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _fallback_question(missing_context: list[str]) -> str:
    if missing_context:
        return f"Can you clarify: {missing_context[0]}"
    return "Can you clarify what update you want me to attach to this item?"
