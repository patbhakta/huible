"""Durable usage ledgers for the hosted persona-voice LLM clients.

Two trackers live here, sharing one design:

* :class:`MonthlySpendTracker` — USD spend per UTC calendar month for the
  OpenRouter client. The board-approved activation (decision sweep
  2026-08-18, HU-1774 card ``9461babc``, item 3) carries a **$50/month hard
  cap**: every successful OpenRouter call reports its ``usage.cost`` (USD)
  into a per-month bucket, and the client refuses further calls once the
  budget is exhausted (:class:`huible.llm.client.LLMBudgetExceededError`).
* :class:`DailyTokenTracker` — tokens per UTC calendar day for the zai
  (GLM) day-1 persona voice (HU-1910 executing HU-1461). The zai endpoint
  is an existing subscription (no metered USD), so the guardrail is a hard
  per-day token ceiling on the same durable-ledger pattern.

Design constraints (both trackers):

* **Durable across restarts** — state lives in a JSON file (atomic
  ``tmp + os.replace`` write) on a bind-mounted volume, so container
  recreation cannot reset the meter.
* **Fail-open on state corruption, never on the cap** — an unreadable or
  corrupt state file logs a warning and restarts accrual at zero (the
  provider-side limit remains the outer wall), but a healthy state file
  at/over the limit always blocks.
* **Bucketing by UTC** calendar bucket; buckets older than the current +
  previous one are pruned on write so the file stays tiny.
* ``limit <= 0`` disables the cap (explicit opt-out documented in
  ``.env.example``).

Key-free and deterministic: no network, no secrets. Tests inject a frozen
``now_fn`` for bucket-boundary coverage.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_SPEND_STATE_FILENAME",
    "DEFAULT_TOKEN_STATE_FILENAME",
    "DailyTokenTracker",
    "MonthlySpendTracker",
    "utc_day_key",
    "utc_month_key",
]

#: Default filename used when the tracker is constructed with a directory.
DEFAULT_SPEND_STATE_FILENAME = "openrouter-spend.json"

#: Default filename for the daily token ledger (zai persona voice, HU-1910).
DEFAULT_TOKEN_STATE_FILENAME = "zai-tokens.json"

#: Keep the current + previous month (prune anything older on write).
_RETAINED_MONTHS = 2

#: Keep the current + previous day (prune anything older on write).
_RETAINED_DAYS = 2


def utc_day_key(now: datetime | None = None) -> str:
    """Return the UTC calendar-day bucket key, ``YYYY-MM-DD``."""
    moment = now or datetime.now(UTC)
    return f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}"


def utc_month_key(now: datetime | None = None) -> str:
    """Return the UTC calendar-month bucket key, ``YYYY-MM``."""
    moment = now or datetime.now(UTC)
    return f"{moment.year:04d}-{moment.month:02d}"


class MonthlySpendTracker:
    """Accrue per-month USD spend against a hard monthly budget.

    Parameters
    ----------
    budget_usd:
        Hard monthly cap in USD. ``<= 0`` disables the cap (tracker still
        records accrual, :meth:`is_exhausted` is always ``False``).
    state_path:
        Path of the JSON state file. Parent directories are created on
        first write. Unwritable paths degrade to in-memory accrual with a
        warning (single-process lifetime only — pair with a writable bind
        mount in deployment).
    now_fn:
        Injectable clock (UTC) for deterministic month-boundary tests.
    """

    def __init__(
        self,
        *,
        budget_usd: float,
        state_path: str | Path,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.budget_usd = float(budget_usd)
        self._state_path = Path(state_path)
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        # Loaded lazily on first access and cached; record_cost() rewrites
        # the file atomically and updates the cache.
        self._months: dict[str, float] | None = None

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def capped(self) -> bool:
        """Whether the hard cap is active (budget_usd > 0)."""
        return self.budget_usd > 0

    def month_to_date(self) -> float:
        """Accrued USD for the current UTC month."""
        return self._loaded_months().get(utc_month_key(self._now_fn()), 0.0)

    def is_exhausted(self) -> bool:
        """True when the cap is active and month-to-date spend reached it."""
        return self.capped and self.month_to_date() >= self.budget_usd

    def record_cost(self, usd: float) -> None:
        """Accrue ``usd`` into the current month bucket and persist.

        Non-finite or negative amounts are ignored (defensive: a malformed
        ``usage.cost`` must never poison the ledger).
        """
        try:
            amount = float(usd)
        except (TypeError, ValueError):
            logger.warning("Ignoring non-numeric usage.cost=%r", usd)
            return
        if not amount > 0 or amount != amount or amount in (float("inf"), float("-inf")):
            if amount < 0:
                logger.warning("Ignoring negative usage.cost=%r", usd)
            return
        months = self._loaded_months()
        key = utc_month_key(self._now_fn())
        months[key] = round(months.get(key, 0.0) + amount, 6)
        self._prune(months)
        self._persist(months)

    def snapshot(self) -> dict[str, Any]:
        """Operator-facing view for /health + logs (no secrets)."""
        return {
            "budget_usd": round(self.budget_usd, 2),
            "month": utc_month_key(self._now_fn()),
            "month_to_date_usd": round(self.month_to_date(), 6),
            "capped": self.capped,
            "exhausted": self.is_exhausted(),
        }

    # ── Internals ─────────────────────────────────────────────────────────

    def _loaded_months(self) -> dict[str, float]:
        if self._months is not None:
            return self._months
        months = self._read_state_file()
        self._months = months
        return months

    def _read_state_file(self) -> dict[str, float]:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            # Corrupt/unreadable state: restart accrual at zero with a loud
            # warning. The console-side key limit stays the outer wall.
            logger.warning(
                "OpenRouter spend state %s unreadable (%s); restarting accrual at 0",
                self._state_path,
                exc,
            )
            return {}
        months: dict[str, float] = {}
        if isinstance(raw, dict) and isinstance(raw.get("months"), dict):
            for key, value in raw["months"].items():
                if isinstance(key, str) and isinstance(value, (int, float)):
                    months[key] = float(value)
        return months

    def _prune(self, months: dict[str, float]) -> None:
        if len(months) <= _RETAINED_MONTHS:
            return
        for key in sorted(months)[:-_RETAINED_MONTHS]:
            months.pop(key, None)

    def _persist(self, months: dict[str, float]) -> None:
        payload = {"version": 1, "months": months}
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._state_path.parent),
                prefix=self._state_path.name + ".",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                os.replace(tmp_name, self._state_path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
                raise
        except OSError as exc:
            # Degrade to in-memory accrual — the cap still holds for this
            # process lifetime; deployment pairs the app with a writable
            # bind mount so this path should stay theoretical.
            logger.warning(
                "OpenRouter spend state %s unwritable (%s); "
                "accrual is in-memory for this process only",
                self._state_path,
                exc,
            )


class DailyTokenTracker:
    """Accrue per-UTC-day token usage against a hard daily ceiling.

    The zai (GLM) day-1 persona voice runs on an existing subscription
    (HU-1910 executing HU-1461), so there is no metered ``usage.cost`` to
    ledger — the board-approved guardrail is a per-day token wall. Every
    successful call accrues its reported ``usage.total_tokens`` into a
    per-UTC-day bucket, and :meth:`is_exhausted` flips true once the day's
    accrual reaches ``limit_tokens``; the client then refuses further calls
    *before* the network request.

    Parameters
    ----------
    limit_tokens:
        Hard daily token ceiling. ``<= 0`` disables the cap (tracker still
        records accrual, :meth:`is_exhausted` is always ``False``).
    state_path:
        Path of the JSON state file. Same durability semantics as
        :class:`MonthlySpendTracker` (atomic write, corrupt state restarts
        accrual at zero with a warning, unwritable path degrades to
        in-memory accrual for the process lifetime).
    now_fn:
        Injectable clock (UTC) for deterministic day-boundary tests.
    """

    def __init__(
        self,
        *,
        limit_tokens: int,
        state_path: str | Path,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.limit_tokens = int(limit_tokens)
        self._state_path = Path(state_path)
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        # Loaded lazily on first access and cached; record_tokens() rewrites
        # the file atomically and updates the cache.
        self._days: dict[str, int] | None = None

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def capped(self) -> bool:
        """Whether the hard ceiling is active (limit_tokens > 0)."""
        return self.limit_tokens > 0

    def day_to_date(self) -> int:
        """Accrued tokens for the current UTC day."""
        return self._loaded_days().get(utc_day_key(self._now_fn()), 0)

    def is_exhausted(self) -> bool:
        """True when the ceiling is active and today's tokens reached it."""
        return self.capped and self.day_to_date() >= self.limit_tokens

    def record_tokens(self, tokens: int) -> None:
        """Accrue ``tokens`` into the current day bucket and persist.

        Non-finite or negative amounts are ignored (defensive: a malformed
        ``usage`` block must never poison the ledger).
        """
        try:
            amount = int(tokens)
        except (TypeError, ValueError):
            logger.warning("Ignoring non-integer token count=%r", tokens)
            return
        if amount < 0:
            logger.warning("Ignoring negative token count=%r", tokens)
            return
        days = self._loaded_days()
        key = utc_day_key(self._now_fn())
        days[key] = days.get(key, 0) + amount
        self._prune(days)
        self._persist(days)

    def snapshot(self) -> dict[str, Any]:
        """Operator-facing view for /health + logs (no secrets)."""
        return {
            "limit_tokens": self.limit_tokens,
            "day": utc_day_key(self._now_fn()),
            "day_to_date_tokens": self.day_to_date(),
            "capped": self.capped,
            "exhausted": self.is_exhausted(),
        }

    # ── Internals ─────────────────────────────────────────────────────────

    def _loaded_days(self) -> dict[str, int]:
        if self._days is not None:
            return self._days
        days = self._read_state_file()
        self._days = days
        return days

    def _read_state_file(self) -> dict[str, int]:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            # Corrupt/unreadable state: restart accrual at zero with a loud
            # warning. The provider-side subscription limit stays the outer
            # wall.
            logger.warning(
                "Daily token state %s unreadable (%s); restarting accrual at 0",
                self._state_path,
                exc,
            )
            return {}
        days: dict[str, int] = {}
        if isinstance(raw, dict) and isinstance(raw.get("days"), dict):
            for key, value in raw["days"].items():
                if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool):
                    days[key] = int(value)
        return days

    def _prune(self, days: dict[str, int]) -> None:
        if len(days) <= _RETAINED_DAYS:
            return
        for key in sorted(days)[:-_RETAINED_DAYS]:
            days.pop(key, None)

    def _persist(self, days: dict[str, int]) -> None:
        payload = {"version": 1, "days": days}
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._state_path.parent),
                prefix=self._state_path.name + ".",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                os.replace(tmp_name, self._state_path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
                raise
        except OSError as exc:
            # Degrade to in-memory accrual — the ceiling still holds for
            # this process lifetime; deployment pairs the app with a
            # writable bind mount so this path should stay theoretical.
            logger.warning(
                "Daily token state %s unwritable (%s); accrual is in-memory for this process only",
                self._state_path,
                exc,
            )
