#!/usr/bin/env python3
"""
get_time — return the current time in the caller's timezone.

Sibling to get_date. Use these together when the SLM needs a single timestamp.

Usage:
    python -m scripts.tools.get_time
    python -m scripts.tools.get_time --timezone America/Chicago
    python -m scripts.tools.get_time --format "%H:%M:%S"

Output (JSON on stdout):
    {"time": "22:14:05", "timezone": "America/Chicago",
     "utc_offset": "-05:00", "iso": "2026-08-10T22:14:05-05:00"}
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "UTC"


def main() -> int:
    parser = argparse.ArgumentParser(description="Return the current time in a timezone.")
    parser.add_argument(
        "--timezone", "-t", default=DEFAULT_TZ, help="IANA timezone (default UTC)"
    )
    parser.add_argument(
        "--format", "-f", default="%H:%M:%S", help="strftime format (default %H:%M:%S)"
    )
    args = parser.parse_args()

    try:
        tz = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        print(json.dumps({"error": f"unknown timezone: {args.timezone}"}))
        return 1

    now = datetime.now(tz)
    payload = {
        "time": now.strftime(args.format),
        "timezone": str(tz),
        "utc_offset": now.strftime("%z")[:3] + ":" + now.strftime("%z")[3:],
        "iso": now.isoformat(),
    }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
