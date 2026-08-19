#!/usr/bin/env python3
"""Print durable telemetry-log lines from a trailing time window (HU-1945).

The daily-review runbook reads the stdout telemetry surfaces
(``chat.trace`` / ``consent.record`` / ``handoff.page``) over a trailing 24h
window. Docker's json-file history dies with the container on every recreate,
so the app mirrors those lines to a rotating file under the bind-mounted
app-state volume (default ``docker/runtime/app-state/logs/telemetry.log``).
This helper prints the lines whose ``ts`` timestamp falls inside the window so
the runbook greps keep working across container recreations:

    python3 scripts/telemetry_window.py | grep 'consent.record'
    python3 scripts/telemetry_window.py | grep 'chat.trace'
    python3 scripts/telemetry_window.py | grep 'handoff.page'

Non-JSON lines and malformed timestamps are skipped. A missing or empty log
file is not an error (a quiet day is a valid daily-review result).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

#: Repo-rooted default matching the bind mount in docker-compose.yml.
DEFAULT_LOG = Path(__file__).resolve().parent.parent / (
    "docker/runtime/app-state/logs/telemetry.log"
)


def _parse_since(raw: str) -> timedelta:
    """Parse a ``24h`` / ``90m`` / ``7d`` style window into a timedelta."""
    value = raw.strip().lower()
    units = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}
    if len(value) >= 2 and value[-1] in units:
        try:
            return timedelta(**{units[value[-1]]: float(value[:-1])})
        except ValueError:
            pass
    raise SystemExit(f"[telemetry_window] invalid --since value: {raw!r} (e.g. 24h)")


def _parse_ts(raw: object) -> datetime | None:
    """Parse the ``ts`` field of a telemetry JSON line (naive-UTC tolerant).

    The app's ``_JsonLineFormatter`` emits ``time.strftime``-style timestamps
    where ``%f`` is NOT expanded (no microseconds in ``logging.formatTime``),
    so ``2026-08-19T16:25:30.%fZ`` must parse the same as a whole-second
    ``...:30Z`` stamp.
    """
    if not isinstance(raw, str):
        return None
    normalized = raw.replace(".%fZ", "Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(normalized, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        stamp = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp


def window_lines(path: Path, since: timedelta, now: datetime | None = None) -> list[str]:
    """Return the log lines whose ``ts`` falls in the trailing window."""
    now = now or datetime.now(UTC)
    cutoff = now - since
    kept: list[str] = []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return kept
    for line in raw_lines:
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        stamp = _parse_ts(payload.get("ts"))
        if stamp is not None and stamp >= cutoff:
            kept.append(line)
    return kept


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_LOG,
        help="telemetry log path (default: %(default)s)",
    )
    parser.add_argument(
        "--since",
        default="24h",
        help="trailing window, e.g. 24h / 90m / 7d (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    for line in window_lines(args.file, _parse_since(args.since)):
        sys.stdout.write(line + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
