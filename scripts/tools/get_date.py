#!/usr/bin/env python3
"""
get_date — return the current date in the caller's timezone.

The SLM cannot reliably know "today's date" because model context is frozen at
training time and system prompts are easily confused. This tool always returns
the verified server date.

Usage:
    python -m scripts.tools.get_date
    python -m scripts.tools.get_date --timezone America/Chicago
    python -m scripts.tools.get_date --format "%Y-%m-%d"

Output (JSON on stdout):
    {"date": "2026-08-10", "timezone": "America/Chicago", "weekday": "Monday"}
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "UTC"


def main() -> int:
    parser = argparse.ArgumentParser(description="Return the current date in a timezone.")
    parser.add_argument(
        "--timezone", "-t", default=DEFAULT_TZ, help="IANA timezone (default UTC)"
    )
    parser.add_argument(
        "--format", "-f", default="%Y-%m-%d", help="strftime format (default %Y-%m-%d)"
    )
    args = parser.parse_args()

    try:
        tz = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        print(
            json.dumps({"error": f"unknown timezone: {args.timezone}"}),
            file=sys.stdout,
        )
        return 1

    now = datetime.now(tz)
    payload = {
        "date": now.strftime(args.format),
        "timezone": str(tz),
        "weekday": now.strftime("%A"),
        "iso": now.date().isoformat(),
    }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
