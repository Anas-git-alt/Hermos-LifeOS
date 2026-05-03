#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "apps" / "discord_tracker"
sys.path.insert(0, str(APP_DIR))

from finance import FINANCE_CATEGORIES  # noqa: E402


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


def _prompt(payload: str) -> str:
    categories = ", ".join(FINANCE_CATEGORIES)
    return f"""You are Hermis, resolving Life OS finance review items.

Return only valid JSON. No markdown. No prose.

Interpret the raw user finance notes using judgment, not regex rules.
If an item is clear, place it in resolved. If unclear, place it in questions.
Do not invent missing amounts. Do not create memory. Default currency is MAD.

Allowed entry kinds:
expense, bill, subscription, income, transfer, savings_contribution, savings_goal

Allowed categories:
{categories}

Required JSON shape:
{{
  "resolved": [
    {{
      "review_id": 123,
      "entries": [
        {{
          "kind": "expense",
          "amount": "20",
          "currency": "MAD",
          "category": "subscriptions",
          "merchant": "Glovo Prime",
          "description": "Glovo Prime subscription for second account"
        }}
      ]
    }}
  ],
  "questions": [
    {{
      "review_id": 456,
      "question": "What amount/category should this review use?"
    }}
  ]
}}

Finance review payload:
{payload}
"""


def main() -> int:
    payload = sys.stdin.read()
    if not payload.strip():
        print("missing finance payload on stdin", file=sys.stderr)
        return 2

    lifeos_alias = Path("/home/ubuntu/.local/bin/lifeos")
    default_hermes = str(lifeos_alias) if lifeos_alias.exists() else "hermes"
    hermes = os.environ.get("HERMIS_FINANCE_AI_CMD", default_hermes)
    env = os.environ.copy()
    env["HERMES_HOME"] = "/home/ubuntu/.hermes/profiles/lifeos"
    completed = subprocess.run(
        [hermes, "-z", _prompt(payload)],
        text=True,
        capture_output=True,
        check=False,
        cwd=str(ROOT),
        env=env,
    )
    if completed.returncode != 0:
        print(completed.stderr.strip() or f"Hermis AI resolver exited {completed.returncode}", file=sys.stderr)
        return completed.returncode or 1

    try:
        result = _extract_json(completed.stdout)
    except json.JSONDecodeError as exc:
        print(f"Hermis AI resolver returned invalid JSON: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
