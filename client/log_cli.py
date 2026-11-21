"""Query persisted gateway events without running the server."""

import argparse
import json
from pathlib import Path

from gateway.storage import EventLogStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Query smart dorm gateway event logs")
    parser.add_argument("--db", default="data/events.sqlite", help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    recent = sub.add_parser("recent", help="List recent events")
    recent.add_argument("--limit", type=int, default=20)
    recent.add_argument("--device-id")
    recent.add_argument("--event")

    trail = sub.add_parser("trail", help="Show all events for one request_id")
    trail.add_argument("request_id")

    args = parser.parse_args()
    if not Path(args.db).is_file():
        parser.exit(1, f"Database not found: {args.db}\n")

    store = EventLogStore(args.db)
    if args.command == "recent":
        rows = store.query_recent(
            limit=args.limit,
            event_name=args.event,
            device_id=args.device_id,
        )
    else:
        rows = store.query_command_trail(args.request_id)

    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()