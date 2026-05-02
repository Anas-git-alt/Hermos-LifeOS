from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


DEFAULT_CURRENCY = "MAD"
HIGH_CONFIDENCE = "high"
LOW_CONFIDENCE = "low"

FINANCE_CATEGORIES = (
    "groceries",
    "eating_out",
    "transport",
    "rent",
    "utilities",
    "subscriptions",
    "shopping",
    "health",
    "family",
    "deen_charity",
    "work_tools",
    "education",
    "travel",
    "fees_taxes",
    "entertainment",
    "savings",
    "income",
    "transfer",
    "unknown",
)

EXPENSE_KINDS = {"expense", "bill", "subscription"}
MONEY_KINDS = EXPENSE_KINDS | {
    "income",
    "transfer",
    "savings_contribution",
    "savings_goal",
}

_CURRENCY_WORDS = (
    "mad",
    "dh",
    "dhs",
    "dirham",
    "dirhams",
    "usd",
    "dollar",
    "dollars",
    "eur",
    "euro",
    "euros",
    "gbp",
    "pound",
    "pounds",
)
_CURRENCY_RE = "|".join(re.escape(word) for word in _CURRENCY_WORDS)
_AMOUNT_RE = re.compile(
    rf"(?:(?P<prefix>\b(?:{_CURRENCY_RE})\b|\$)\s*)?"
    r"(?P<amount>\d+(?:[.,]\d{1,2})?)"
    rf"(?:\s*(?P<suffix>\b(?:{_CURRENCY_RE})\b|\$))?",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+[.)]|\u2022)\s*")

_CATEGORY_KEYWORDS = {
    "groceries": ("grocery", "groceries", "supermarket", "marjane", "carrefour", "bim"),
    "eating_out": ("lunch", "dinner", "breakfast", "coffee", "restaurant", "takeout", "meal"),
    "transport": ("taxi", "uber", "careem", "tram", "train", "fuel", "gas", "parking"),
    "rent": ("rent",),
    "utilities": ("water bill", "electricity", "utility", "utilities", "internet", "phone bill", "wifi"),
    "subscriptions": ("subscription", "netflix", "spotify", "icloud", "youtube", "notion", "openai"),
    "shopping": ("clothes", "shoes", "amazon", "jumia", "shopping"),
    "health": ("doctor", "pharmacy", "medicine", "gym", "dentist"),
    "family": ("family", "parents", "mom", "dad", "brother", "sister"),
    "deen_charity": ("sadaqa", "zakat", "charity", "masjid", "donation"),
    "work_tools": ("work tool", "saas", "domain", "hosting", "software", "server"),
    "education": ("course", "book", "class", "training", "study"),
    "travel": ("flight", "hotel", "airbnb", "travel", "booking"),
    "fees_taxes": ("fee", "fees", "tax", "taxes", "dgi", "bank fee"),
    "entertainment": ("movie", "cinema", "game", "gaming", "concert"),
    "savings": ("saved", "saving", "savings", "emergency fund", "invest"),
    "income": ("income", "salary", "earned", "got paid", "received"),
    "transfer": ("transfer", "moved money", "sent money"),
}

_LEADING_WORDS_RE = re.compile(
    r"\b(spent|paid|pay|bought|buy|got|received|earned|salary|saved|save|"
    r"transfer|transferred|moved|bill|subscription|for|on|to|from|into)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FinanceEntry:
    kind: str
    amount: Decimal
    currency: str
    amount_mad: Decimal | None
    category: str
    merchant: str
    description: str
    confidence: str
    review_reason: str | None = None


@dataclass(frozen=True)
class FinanceParseResult:
    entries: tuple[FinanceEntry, ...]
    status: str
    review_reason: str | None = None


def finance_review_request(reason: str = "hermis_review_required") -> FinanceParseResult:
    return FinanceParseResult(entries=(), status="needs_review", review_reason=reason)


def parse_finance_message(text: str, default_currency: str = DEFAULT_CURRENCY) -> FinanceParseResult:
    lines = _finance_lines(text)
    if len(lines) > 1:
        entries: list[FinanceEntry] = []
        for index, line in enumerate(lines, start=1):
            parsed = _parse_single_finance_line(line, default_currency)
            if parsed.status != "parsed" or not parsed.entries:
                reason = parsed.review_reason or "needs_review"
                return FinanceParseResult(entries=(), status="needs_review", review_reason=f"line_{index}_{reason}")
            entries.extend(parsed.entries)
        return FinanceParseResult(entries=tuple(entries), status="parsed")
    return _parse_single_finance_line(lines[0] if lines else text, default_currency)


def _finance_lines(text: str) -> list[str]:
    lines = []
    for raw_line in text.splitlines():
        line = _BULLET_RE.sub("", raw_line).strip()
        if line:
            lines.append(line)
    return lines


def _parse_single_finance_line(text: str, default_currency: str = DEFAULT_CURRENCY) -> FinanceParseResult:
    clean = " ".join(text.strip().split())
    if not clean:
        return FinanceParseResult(entries=(), status="needs_review", review_reason="empty_message")

    matches = [match for match in _AMOUNT_RE.finditer(clean)]
    if not matches:
        return FinanceParseResult(entries=(), status="needs_review", review_reason="missing_amount")
    if len(matches) > 1:
        return FinanceParseResult(entries=(), status="needs_review", review_reason="multiple_amounts")

    match = matches[0]
    amount = _parse_decimal(match.group("amount"))
    if amount is None or amount <= 0:
        return FinanceParseResult(entries=(), status="needs_review", review_reason="invalid_amount")

    currency = _normalize_currency(match.group("prefix") or match.group("suffix") or default_currency)
    kind = _classify_kind(clean)
    category = _classify_category(clean, kind)
    description = _description_from_text(clean, match)
    if not description:
        return FinanceParseResult(entries=(), status="needs_review", review_reason="missing_description")

    amount_mad = amount if currency == "MAD" else None
    entry = FinanceEntry(
        kind=kind,
        amount=amount,
        currency=currency,
        amount_mad=amount_mad,
        category=category,
        merchant=_merchant_from_description(description, kind),
        description=description,
        confidence=HIGH_CONFIDENCE,
    )
    return FinanceParseResult(entries=(entry,), status="parsed")


def _parse_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation:
        return None


def _normalize_currency(value: str) -> str:
    token = value.strip().lower()
    if token in {"mad", "dh", "dhs", "dirham", "dirhams"}:
        return "MAD"
    if token in {"usd", "dollar", "dollars", "$"}:
        return "USD"
    if token in {"eur", "euro", "euros"}:
        return "EUR"
    if token in {"gbp", "pound", "pounds"}:
        return "GBP"
    return token.upper()


def _classify_kind(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("salary", "income", "got paid", "paid me", "earned", "received")):
        return "income"
    if any(word in lowered for word in ("transfer", "transferred", "moved money")):
        return "transfer"
    if any(word in lowered for word in ("savings goal", "saving goal", "goal")):
        return "savings_goal"
    if any(word in lowered for word in ("saved", "save ", "put aside", "emergency fund")):
        return "savings_contribution"
    if any(word in lowered for word in ("subscription", "netflix", "spotify", "icloud")):
        return "subscription"
    if any(word in lowered for word in ("bill", "rent", "electricity", "internet", "water bill")):
        return "bill"
    return "expense"


def _classify_category(text: str, kind: str) -> str:
    if kind == "income":
        return "income"
    if kind == "transfer":
        return "transfer"
    if kind in {"savings_contribution", "savings_goal"}:
        return "savings"

    lowered = text.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return category
    return "unknown"


def _description_from_text(text: str, match: re.Match[str]) -> str:
    without_amount = (text[: match.start()] + " " + text[match.end() :]).strip()
    without_amount = _LEADING_WORDS_RE.sub(" ", without_amount)
    without_amount = re.sub(r"\s+", " ", without_amount).strip(" -:,.")
    return without_amount or text.strip()


def _merchant_from_description(description: str, kind: str) -> str:
    words = description.split()
    if not words:
        return ""
    if kind in {"savings_contribution", "savings_goal", "transfer"}:
        return description
    if len(words) <= 3:
        return description
    return " ".join(words[:3])
