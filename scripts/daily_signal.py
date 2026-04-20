"""
Daily signal scanner — prints the last 24h of unknown_messages grouped by category.

Run from repo root:
    python -m scripts.daily_signal
    python -m scripts.daily_signal --hours 72

Output is markdown — copy-paste into an LLM for synthesis.
"""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict

from app.repositories.unknown_message_repository import UnknownMessageRepository
from app.repositories.user_repository import UserRepository


def _format_ts(ts) -> str:
    if ts is None:
        return "?"
    if hasattr(ts, "astimezone"):
        ts = ts.astimezone(timezone.utc)
    return ts.strftime("%Y-%m-%d %H:%M UTC")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan recent unknown_messages for daily signal review."
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Look-back window in hours (default: 24).",
    )
    args = parser.parse_args()

    messages = UnknownMessageRepository.list_recent(hours=args.hours)

    name_cache: Dict[str, str] = {}

    def resolve_name(phone: str) -> str:
        if not phone:
            return "(no phone)"
        if phone not in name_cache:
            user = UserRepository.get_user(phone) or {}
            name_cache[phone] = user.get("name") or "(unknown)"
        return name_cache[phone]

    grouped = defaultdict(list)
    for m in messages:
        grouped[m.get("category") or "(uncategorized)"].append(m)

    print(f"# Daily signal — last {args.hours}h")
    print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Total messages: {len(messages)}")
    print()

    if not messages:
        print("_No unknown messages in this window._")
        return

    for category in sorted(grouped.keys()):
        items = grouped[category]
        print(f"## {category} ({len(items)})")
        print()
        for m in items:
            ts = _format_ts(m.get("created_at"))
            name = resolve_name(m.get("user_phone_number") or "")
            language = m.get("language") or "?"
            routed_to = m.get("routed_to") or "?"
            raw = (m.get("raw_message") or "").replace("\n", " ").strip()
            print(f"- [{ts}] {name} ({language}) → {routed_to}")
            print(f"  > {raw}")
        print()


if __name__ == "__main__":
    main()
