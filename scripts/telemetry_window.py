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

Liveness gate (HU-2674, ``--assert-live``): a zero-line window is only a
valid GREEN when the platform was actually quiet — on 2026-09-01/02 the sink
wrote nothing and the digest could not tell "quiet" from "sink dead" apart.
The gate fails loudly when the sink has no lines in the window while the
DB-backed traffic tables (conversation_turns / consent_records /
handoff_tickets) show activity, or when the current container's startup log
does not confirm ``telemetry file sink active``. Exit 0 = live (or confirmed
quiet), exit 1 = assertion failed / unverifiable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

#: Repo-rooted default matching the bind mount in docker-compose.yml.
DEFAULT_LOG = Path(__file__).resolve().parent.parent / (
    "docker/runtime/app-state/logs/telemetry.log"
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Startup confirmation line written by
#: ``huible.api.app._attach_telemetry_file_sink`` on every boot (HU-1945).
SINK_ACTIVE_MARKER = "telemetry file sink active"

#: DB-backed mirrors of the telemetry surfaces used for the quiet-vs-dead
#: cross-check (HU-2674). Each is optional; missing tables count as zero.
_TRAFFIC_TABLES = (
    ("conversation_turns", "created_at"),
    ("consent_records", "acknowledged_at"),
    ("handoff_tickets", "created_at"),
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


def _resolve_db_dsn(explicit: str | None) -> str:
    """Resolve an asyncpg DSN for the traffic cross-check, or ``""``.

    Order: ``--db`` flag, ``DATABASE_URL`` env (``+asyncpg`` stripped, matching
    the W1 migration script), the repo ``.env`` file. A docker service-name
    host (``postgres``) is translated to the compose loopback publish
    (``127.0.0.1:5433``, docs/09 §8) so the digest works on-host.
    """
    if explicit:
        raw = explicit
    else:
        raw = os.environ.get("DATABASE_URL", "")
        env: dict[str, str] = {}
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                match = re.match(r"^([A-Z_]+)=(.+?)\s*(?:#.*)?$", line.strip())
                if match:
                    env[match.group(1)] = match.group(2).strip().strip("'\"")
        if not raw:
            raw = env.get("DATABASE_URL", "")
        if not raw:
            return ""
        # .env templates interpolate ${VAR} from sibling POSTGRES_* keys
        # (os.environ wins when the var is actually exported).

        def _substitute(m: re.Match[str]) -> str:
            return os.environ.get(m.group(1)) or env.get(m.group(1), m.group(0))

        raw = re.sub(r"\$\{([A-Z_]+)\}", _substitute, raw)
    dsn = raw.replace("+asyncpg", "")
    dsn = re.sub(r"@postgres:(?:\d+)?", "@127.0.0.1:5433", dsn)
    return dsn


def _db_traffic_rows(dsn: str, seconds: float) -> int | None:
    """Sum DB-backed telemetry traffic rows inside the window.

    Returns ``None`` when the DB is unreachable (caller decides whether that
    is fatal). Tables that do not exist count as zero traffic.
    """
    try:
        import asyncpg
    except ImportError:  # pragma: no cover - asyncpg is a package dependency
        print(
            "[telemetry_window] ASSERT FAIL: asyncpg not importable for cross-check",
            file=sys.stderr,
        )
        return None

    import asyncio

    async def _query() -> int:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            total = 0
            for table, column in _TRAFFIC_TABLES:
                try:
                    count = await conn.fetchval(
                        f"SELECT count(*) FROM {table} WHERE {column} >= "
                        "now() - make_interval(secs => $1)",
                        seconds,
                    )
                    total += int(count or 0)
                except asyncpg.UndefinedTableError:
                    continue
            return total
        finally:
            await conn.close()

    try:
        return asyncio.run(_query())
    except Exception as exc:
        print(f"[telemetry_window] traffic cross-check unreachable: {exc}", file=sys.stderr)
        return None


def _startup_line_confirmed() -> bool:
    """Grep the running container's logs for the sink-active startup line.

    The line is emitted once per boot by
    ``huible.api.app._attach_telemetry_file_sink``; after a recreate it must
    be present or the surfaces degrade to stdout-only (dies with the next
    recreate). Tries ``docker compose logs app`` from the repo root first,
    then the pinned ``docker logs huible-app``.
    """
    candidates = (
        ["docker", "compose", "logs", "--no-color", "app"],
        ["docker", "logs", "huible-app"],
    )
    saw_docker = False
    for cmd in candidates:
        try:
            proc = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        saw_docker = True
        if proc.returncode == 0 and SINK_ACTIVE_MARKER in proc.stdout:
            return True
    if not saw_docker:
        print(
            "[telemetry_window] ASSERT FAIL: docker CLI unavailable; cannot verify "
            f"the {SINK_ACTIVE_MARKER!r} startup line (pass --skip-startup-line to waive)",
            file=sys.stderr,
        )
    return False


def assert_live(
    path: Path,
    since: timedelta,
    *,
    dsn: str = "",
    skip_startup_line: bool = False,
    now: datetime | None = None,
) -> int:
    """HU-2674 liveness gate for the daily digest. Returns the exit code.

    GREEN requires, for the trailing ``since`` window:
      1. the running container's startup log confirms the sink attached; and
      2. sink lines exist, OR the DB-backed traffic tables confirm the window
         was genuinely quiet (zero rows in every mirror table).

    A zero-line sink with DB traffic is the false-GREEN trap this gate exists
    for (Sep 1-2 2026): surfaces (2)-(5) looked clean because the sink was
    blind, not because the platform was quiet.
    """
    seconds = since.total_seconds()

    if not skip_startup_line and not _startup_line_confirmed():
        print(
            "[telemetry_window] ASSERT FAIL: no "
            f"{SINK_ACTIVE_MARKER!r} line in the running container's log — "
            "the sink may not have attached after the last recreate",
            file=sys.stderr,
        )
        return 1

    lines = window_lines(path, since, now=now)
    if lines:
        print(
            f"[telemetry_window] ASSERT OK: sink live — {len(lines)} telemetry "
            f"line(s) in the trailing {since}"
        )
        return 0

    dsn = _resolve_db_dsn(dsn)
    if not dsn:
        print(
            "[telemetry_window] ASSERT FAIL: sink has 0 lines in the window and "
            "no DB DSN available for the quiet-vs-dead cross-check "
            "(pass --db or populate DATABASE_URL/.env)",
            file=sys.stderr,
        )
        return 1

    traffic = _db_traffic_rows(dsn, seconds)
    if traffic is None:
        print(
            "[telemetry_window] ASSERT FAIL: sink has 0 lines in the window and "
            "the traffic cross-check could not reach the DB — treat the digest "
            "window as unverified, not GREEN",
            file=sys.stderr,
        )
        return 1
    if traffic > 0:
        print(
            f"[telemetry_window] ASSERT FAIL: sink has 0 lines but the DB shows "
            f"{traffic} traffic row(s) in the window — false-GREEN trap "
            "(HU-2674): stdout surfaces for this window are unrecoverable",
            file=sys.stderr,
        )
        return 1
    print(
        f"[telemetry_window] ASSERT OK: confirmed quiet window — 0 sink lines "
        f"and 0 DB traffic rows over the trailing {since}"
    )
    return 0


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
    parser.add_argument(
        "--assert-live",
        action="store_true",
        help=(
            "HU-2674 liveness gate: fail when the sink has no lines in the "
            "window while DB traffic tables show activity, or when the "
            "startup log does not confirm the sink attached (exit 1)"
        ),
    )
    parser.add_argument(
        "--db",
        default="",
        help="asyncpg DSN for the traffic cross-check (default: DATABASE_URL or repo .env)",
    )
    parser.add_argument(
        "--skip-startup-line",
        action="store_true",
        help="waive the 'telemetry file sink active' startup-log check (e.g. no docker access)",
    )
    args = parser.parse_args(argv)
    if args.assert_live:
        return assert_live(
            args.file,
            _parse_since(args.since),
            dsn=args.db,
            skip_startup_line=args.skip_startup_line,
        )
    for line in window_lines(args.file, _parse_since(args.since)):
        sys.stdout.write(line + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
