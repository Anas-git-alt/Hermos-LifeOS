#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "apps" / "discord_tracker"
sys.path.insert(0, str(APP_DIR))

from review_automation import SafeAutoProcessor  # noqa: E402
from store import TrackerStore  # noqa: E402


@dataclass(frozen=True)
class Config:
    root: Path
    db_path: Path


def load_config() -> Config:
    root = Path(os.environ.get("LIFEOS_ROOT", str(ROOT))).expanduser()
    db_path = Path(os.environ.get("TRACKER_DB", str(root / "data" / "lifeos_tracker.db"))).expanduser()
    env_file = root / ".env.discord-tracker"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == "TRACKER_DB":
                db_path = Path(value.strip().strip('"')).expanduser()
    return Config(root=root, db_path=db_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expire unanswered Discord review items for nightly fallback.")
    parser.add_argument("day", nargs="?", default=date.today().isoformat())
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def write_needs_answer(root: Path, day: str, items: list[dict]) -> Path | None:
    if not items:
        return None
    path = root / "inbox" / "needs-answer" / f"{day}-review.md"
    lines = [f"# Discord Review Fallback {day}", ""]
    for item in items:
        lines.append(f"- {item['id']}: {item['title']}")
        lines.append(f"  Source: {item.get('source_path') or item.get('source_kind') or 'unknown'}")
        body = " ".join(str(item.get("body") or "").split())
        if body:
            lines.append(f"  Question: {body}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def render_report(
    day: str,
    expired: list[dict],
    open_items: list[dict],
    question_path: Path | None,
    auto_processed: list[dict] | None = None,
) -> str:
    auto_processed = auto_processed or []
    lines = [
        f"# Review Fallback - {day}",
        "",
        "## Result",
        f"- Auto-processed safely: {len(auto_processed)}",
        f"- Newly expired: {len(expired)}",
        f"- Still unresolved: {len(open_items)}",
        f"- Questions written: {question_path if question_path else 'none'}",
        "",
        "## Auto-Processed",
    ]
    if auto_processed:
        for item in auto_processed:
            lines.append(f"- {item['id']} {item['kind']}: {item['title']}")
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## Expired",
    ])
    if expired:
        for item in expired:
            lines.append(f"- {item['id']} {item['kind']}: {item['title']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Still Unresolved"])
    if open_items:
        for item in open_items:
            lines.append(f"- {item['id']} {item['status']}: {item['title']}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Policy",
            "- Discord review items are not discarded when unanswered.",
            "- Expired or unclear items are written to inbox/needs-answer and resurfaced in the next morning report/review queue.",
            "- Existing work and finance processors remain the safe automation path for their source queues.",
            "- Sensitive finance, health, family, legal, and durable identity/memory claims require explicit approval.",
            "",
        ]
    )
    return "\n".join(lines)


async def run_fallback(config: Config, day: str, output: Path) -> tuple[list[dict], list[dict], Path | None]:
    store = TrackerStore(config.db_path, config.root)
    await store.init()
    processor = SafeAutoProcessor(store)
    auto_processed = await processor.process_pending(limit=80)
    expired = await processor.expire_low_risk()
    open_items = await store.list_review_items(("needs_clarification", "expired"), limit=80)
    question_path = write_needs_answer(config.root, day, open_items)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_report(day, expired, open_items, question_path, auto_processed), encoding="utf-8")
    return expired, open_items, question_path


async def main_async() -> int:
    args = parse_args()
    config = load_config()
    day = date.fromisoformat(args.day).isoformat()
    output = Path(args.output).expanduser() if args.output else config.root / "reports" / "nightly" / f"{day}-review-fallback.md"
    await run_fallback(config, day, output)
    print(f"wrote review fallback report: {output}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
