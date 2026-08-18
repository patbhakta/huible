"""Monthly spend tracking for the OpenRouter persona-voice client.

The board-approved activation of OpenRouter (decision sweep 2026-08-18,
HU-1774 card ``9461babc``, item 3) carries a **$50/month hard cap**. This
module provides the local enforcement wall behind the console-side key
limit: every successful OpenRouter call reports its ``usage.cost`` (USD)
and accrues into a per-calendar-month bucket in a small JSON state file,
and the client refuses to place further calls once the budget is
exhausted (:class:`huible.llm.client.LLMBudgetExceededError`).

Design constraints:

* **Durable across restarts** — state lives in a JSON file (atomic
  ``tmp + os.replace`` write) on a bind-mounted volume, so container
  recreation cannot reset the meter.
* **Fail-open on state corruption, never on the cap** — an unreadable or
  corrupt state file logs a warning and restarts accrual at zero (the
  console-side key limit remains the outer wall), but a healthy state
  file at/over budget always blocks.
* **Month bucketing by UTC** calendar month; months older than the
  current one are pruned on write so the file stays tiny.
* ``budget_usd <= 0`` disables the cap (explicit opt-out documented in
  ``.env.example``); the default matches the approved $50 cap.

Key-free and deterministic: no network, no secrets. Tests inject a frozen
``now_fn`` for month-boundary coverage.
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
    "MonthlySpendTracker",
    "utc_month_key",
]

#: Default filename used when the tracker is constructed with a directory.
DEFAULT_SPEND_STATE_FILENAME = "openrouter-spend.json"

#: Keep the current + previous month (prune anything older on write).
_RETAINED_MONTHS = 2


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
