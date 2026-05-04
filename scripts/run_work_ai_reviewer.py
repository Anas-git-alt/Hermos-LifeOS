#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
    return f"""You are Hermis, reviewing Life OS work captures.

Return only valid JSON. No markdown. No prose.

The user's work timezone is Africa/Casablanca.
The normal work window is 14:00 to 23:00 Casablanca time.

Review each raw Discord work capture. The included draft_parse is only a hint.
Do not treat draft_parse as final truth.

For each capture, choose exactly one outcome:
- confirmed: create one or more actionable work items.
- ignored: ignore non-task/noise, with an explicit reason.
- questions: ask a short clarification if the capture is unclear.

Allowed priorities: p0, p1, p2, p3.
Allowed new item statuses: open, waiting, blocked.
Do not create memory.

Required JSON shape:
{{
  "confirmed": [
    {{
      "capture_id": 123,
      "items": [
        {{
          "title": "Send client invoice",
          "priority": "p1",
          "status": "open",
          "project": "optional",
          "area": "optional",
          "due_date": "2026-05-04",
          "due_at": "16:30",
          "scheduled_date": null,
          "scheduled_at": null,
          "energy": "medium",
          "effort_minutes": 30,
          "context": "email",
          "tags": ["billing"],
          "note": "optional review note"
        }}
      ]
    }}
  ],
  "ignored": [
    {{"capture_id": 456, "reason": "not a work task"}}
  ],
  "questions": [
    {{"capture_id": 789, "question": "Which project is this for?"}}
  ]
}}

Work review payload:
{payload}
"""


def main() -> int:
    payload = sys.stdin.read()
    if not payload.strip():
        print("missing work payload on stdin", file=sys.stderr)
        return 2

    lifeos_alias = Path.home() / ".local" / "bin" / "lifeos"
    default_hermes = str(lifeos_alias) if lifeos_alias.exists() else "hermes"
    hermes = os.environ.get("HERMIS_WORK_AI_CMD", default_hermes)
    env = os.environ.copy()
    env["HERMES_HOME"] = os.environ.get("HERMES_HOME", str(Path.home() / ".hermes" / "profiles" / "lifeos"))
    completed = subprocess.run(
        [hermes, "-z", _prompt(payload)],
        text=True,
        capture_output=True,
        check=False,
        cwd=str(ROOT),
        env=env,
    )
    if completed.returncode != 0:
        print(completed.stderr.strip() or f"Hermis work reviewer exited {completed.returncode}", file=sys.stderr)
        return completed.returncode or 1

    try:
        result = _extract_json(completed.stdout)
    except json.JSONDecodeError as exc:
        print(f"Hermis work reviewer returned invalid JSON: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
