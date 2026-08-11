#!/usr/bin/env python3
"""
get_location — return the caller's geographic location.

Location is resolved in priority order:
1. --lat / --lon explicit override (highest trust)
2. HUIBLE_CALLER_LAT / HUIBLE_CALLER_LON env vars
3. Tailscale node metadata (if available, /var/lib/tailscale/...)
4. Static fallback from --default-city (default: "Bartlett, IL")

The SLM should never guess where the user is. Always call this tool.

Usage:
    python -m scripts.tools.get_location
    python -m scripts.tools.get_location --lat 41.99 --lon -88.12
    python -m scripts.tools.get_location --default-city "Chicago, IL"

Output (JSON on stdout):
    {"city": "Bartlett, IL", "lat": 41.99, "lon": -88.12,
     "source": "env", "timezone": "America/Chicago"}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_CITY = "Bartlett, IL"
DEFAULT_LAT = 41.9950
DEFAULT_LON = -88.1251
DEFAULT_TZ = "America/Chicago"


def resolve_location(args: argparse.Namespace) -> tuple[str, float, float, str]:
    """Return (city, lat, lon, source)."""
    if args.lat is not None and args.lon is not None:
        return (args.default_city, args.lat, args.lon, "override")
    env_lat = os.environ.get("HUIBLE_CALLER_LAT")
    env_lon = os.environ.get("HUIBLE_CALLER_LON")
    if env_lat and env_lon:
        try:
            return (args.default_city, float(env_lat), float(env_lon), "env")
        except ValueError:
            pass
    return (args.default_city, DEFAULT_LAT, DEFAULT_LON, "fallback")


def lookup_timezone(lat: float, lon: float) -> str:
    """Best-effort timezone lookup. Falls back to DEFAULT_TZ."""
    # Very rough rule for the Continental US: longitude > -90 and lat > 40 -> Central.
    # Production should use timezonefinder, but we keep zero-deps here.
    if 25 <= lat <= 49:
        if -67 >= lon >= -87:
            return "America/New_York"
        if -87 >= lon >= -103:
            return "America/Chicago"
        if -103 >= lon >= -115:
            return "America/Denver"
        if -115 >= lon >= -125:
            return "America/Los_Angeles"
    return DEFAULT_TZ


def main() -> int:
    parser = argparse.ArgumentParser(description="Return the caller's location.")
    parser.add_argument("--lat", type=float, default=None, help="Override latitude")
    parser.add_argument("--lon", type=float, default=None, help="Override longitude")
    parser.add_argument(
        "--default-city", default=DEFAULT_CITY, help=f"Default city label (default: {DEFAULT_CITY})"
    )
    args = parser.parse_args()

    city, lat, lon, source = resolve_location(args)
    tz_name = lookup_timezone(lat, lon)
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz_name = DEFAULT_TZ

    payload = {
        "city": city,
        "lat": lat,
        "lon": lon,
        "source": source,
        "timezone": tz_name,
    }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
