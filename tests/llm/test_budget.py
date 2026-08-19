"""Tests for the OpenRouter $50/month hard spend cap (HU-1461).

Board decision 2026-08-18 (HU-1774 sweep, item 3): OpenRouter is approved
with a $50/month hard cap and the fake voice is the approved rollback.

Acceptance coverage:

* :class:`huible.llm.budget.MonthlySpendTracker` accrues per UTC month,
  persists durably (atomic rewrite readable by a fresh instance), prunes old
  months, tolerates corrupt state (restart at 0, never a crash), and treats
  ``budget <= 0`` as cap-disabled.
* :class:`huible.llm.client.OpenRouterLLMClient` refuses to place a hosted
  call once the cap is exhausted — raised *before* the transport fires, so
  the wall is spend-proof — and accrues ``usage.cost`` after each successful
  call.
* ``LLMConfig.from_env`` parses the two new knobs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from huible.llm.budget import DailyTokenTracker, MonthlySpendTracker, utc_day_key, utc_month_key
from huible.llm.client import (
    LLMBudgetExceededError,
    LLMConfig,
    LLMProvider,
    OpenRouterLLMClient,
)

KEY = "or-test-key"
PROMPT = "Remember the lake?"


def _frozen(moment: datetime):
    return lambda: moment


# --- MonthlySpendTracker ------------------------------------------------------


def test_tracker_accrues_and_persists(tmp_path: Path) -> None:
    state = tmp_path / "spend.json"
    t1 = MonthlySpendTracker(
        budget_usd=50.0,
        state_path=state,
        now_fn=_frozen(datetime(2026, 8, 18, 12, 0, tzinfo=UTC)),
    )
    t1.record_cost(0.01234)
    t1.record_cost(0.5)
    assert t1.month_to_date() == pytest.approx(0.51234)

    # A fresh instance (container restart) reads the same durable ledger.
    t2 = MonthlySpendTracker(
        budget_usd=50.0,
        state_path=state,
        now_fn=_frozen(datetime(2026, 8, 31, 23, 59, tzinfo=UTC)),
    )
    assert t2.month_to_date() == pytest.approx(0.51234)
    assert not t2.is_exhausted()


def test_tracker_month_rollover_resets_accrual(tmp_path: Path) -> None:
    state = tmp_path / "spend.json"
    august = MonthlySpendTracker(
        budget_usd=50.0,
        state_path=state,
        now_fn=_frozen(datetime(2026, 8, 31, 23, 0, tzinfo=UTC)),
    )
    august.record_cost(49.0)
    assert august.is_exhausted() is False

    september = MonthlySpendTracker(
        budget_usd=50.0,
        state_path=state,
        now_fn=_frozen(datetime(2026, 9, 1, 0, 1, tzinfo=UTC)),
    )
    assert september.month_to_date() == 0.0
    assert september.is_exhausted() is False
    # Old month is retained in the ledger but pruned once two months pass.
    september.record_cost(1.0)
    november = MonthlySpendTracker(
        budget_usd=50.0,
        state_path=state,
        now_fn=_frozen(datetime(2026, 11, 1, 0, 0, tzinfo=UTC)),
    )
    november.record_cost(1.0)
    stored = json.loads(state.read_text())["months"]
    assert "2026-08" not in stored


def test_tracker_exhaustion_boundary(tmp_path: Path) -> None:
    t = MonthlySpendTracker(
        budget_usd=1.0,
        state_path=tmp_path / "s.json",
        now_fn=_frozen(datetime(2026, 8, 18, tzinfo=UTC)),
    )
    t.record_cost(0.999)
    assert not t.is_exhausted()
    t.record_cost(0.001)
    assert t.is_exhausted()
    snap = t.snapshot()
    assert snap["exhausted"] is True
    assert snap["budget_usd"] == 1.0
    assert snap["month"] == "2026-08"


def test_tracker_zero_budget_disables_cap(tmp_path: Path) -> None:
    t = MonthlySpendTracker(
        budget_usd=0.0,
        state_path=tmp_path / "s.json",
        now_fn=_frozen(datetime(2026, 8, 18, tzinfo=UTC)),
    )
    t.record_cost(1_000.0)
    assert t.capped is False
    assert t.is_exhausted() is False


def test_tracker_corrupt_state_restarts_at_zero(tmp_path: Path) -> None:
    state = tmp_path / "spend.json"
    state.write_text("{not json", encoding="utf-8")
    t = MonthlySpendTracker(
        budget_usd=50.0,
        state_path=state,
        now_fn=_frozen(datetime(2026, 8, 18, tzinfo=UTC)),
    )
    assert t.month_to_date() == 0.0
    t.record_cost(1.0)  # overwrites the corrupt file with a fresh ledger
    assert json.loads(state.read_text())["months"]["2026-08"] == 1.0


def test_tracker_ignores_bad_costs(tmp_path: Path) -> None:
    t = MonthlySpendTracker(
        budget_usd=50.0,
        state_path=tmp_path / "s.json",
        now_fn=_frozen(datetime(2026, 8, 18, tzinfo=UTC)),
    )
    t.record_cost(-5.0)
    t.record_cost("NaN")
    t.record_cost(None)  # type: ignore[arg-type]
    assert t.month_to_date() == 0.0


def test_utc_month_key() -> None:
    assert utc_month_key(datetime(2026, 8, 18, 23, 59, tzinfo=UTC)) == "2026-08"


# --- OpenRouterLLMClient cap integration -------------------------------------


def _transport_with_cost(
    reply: str = "The lake at dawn.",
    usage: dict[str, Any] | None = None,
    hits: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    body: dict[str, Any] = {
        "id": "chatcmpl-test",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}}],
    }
    if usage is not None:
        body["usage"] = usage

    def handler(request: httpx.Request) -> httpx.Response:
        if hits is not None:
            hits.append(request)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def _client(
    tmp_path: Path,
    *,
    budget: float,
    transport: httpx.MockTransport,
) -> OpenRouterLLMClient:
    config = LLMConfig(
        provider=LLMProvider.OPENROUTER,
        openrouter_api_key=KEY,
        openrouter_monthly_budget_usd=budget,
        openrouter_spend_state_path=str(tmp_path / "spend.json"),
    )
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    client = OpenRouterLLMClient(config, transport=transport)
    client.spend._now_fn = _frozen(now)
    return client


async def test_successful_call_accrues_reported_cost(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        budget=50.0,
        transport=_transport_with_cost(usage={"cost": 0.0125}),
    )
    reply = await client.generate(PROMPT)
    assert reply == "The lake at dawn."
    assert client.spend.month_to_date() == pytest.approx(0.0125)


async def test_missing_usage_cost_accrues_zero(tmp_path: Path) -> None:
    client = _client(tmp_path, budget=50.0, transport=_transport_with_cost())
    await client.generate(PROMPT)
    assert client.spend.month_to_date() == 0.0


async def test_exhausted_budget_blocks_call_before_network(tmp_path: Path) -> None:
    hits: list[httpx.Request] = []
    client = _client(
        tmp_path,
        budget=1.0,
        transport=_transport_with_cost(usage={"cost": 1.0}, hits=hits),
    )
    # First call spends exactly the budget.
    await client.generate(PROMPT)
    assert hits, "transport should have fired once"
    # Second call must be refused without touching the network.
    with pytest.raises(LLMBudgetExceededError):
        await client.generate(PROMPT)
    assert len(hits) == 1, "cap must block the hosted call, not just meter it"


async def test_zero_budget_disables_client_cap(tmp_path: Path) -> None:
    hits: list[httpx.Request] = []
    client = _client(
        tmp_path,
        budget=0.0,
        transport=_transport_with_cost(usage={"cost": 99.0}, hits=hits),
    )
    await client.generate(PROMPT)
    await client.generate(PROMPT)
    assert len(hits) == 2
    assert client.spend.is_exhausted() is False


# --- Config parsing -----------------------------------------------------------


def test_from_env_parses_budget_knobs(tmp_path: Path) -> None:
    cfg = LLMConfig.from_env(
        {
            "LLM_PROVIDER": "openrouter",
            "OPENROUTER_API_KEY": "k",
            "OPENROUTER_MONTHLY_BUDGET_USD": "25.5",
            "OPENROUTER_SPEND_STATE_PATH": str(tmp_path / "ledger.json"),
        }
    )
    assert cfg.provider is LLMProvider.OPENROUTER
    assert cfg.openrouter_monthly_budget_usd == 25.5
    assert cfg.openrouter_spend_state_path == str(tmp_path / "ledger.json")


def test_from_env_budget_defaults_match_board_approval() -> None:
    cfg = LLMConfig.from_env({"LLM_PROVIDER": "fake"})
    assert cfg.openrouter_monthly_budget_usd == 50.0
    assert cfg.openrouter_spend_state_path == "/var/lib/huible/openrouter-spend.json"


# --- DailyTokenTracker (zai day-1 persona voice, HU-1910) ---------------------


def test_daily_tracker_accrues_and_persists(tmp_path: Path) -> None:
    state = tmp_path / "zai-tokens.json"
    t1 = DailyTokenTracker(
        limit_tokens=200_000,
        state_path=state,
        now_fn=_frozen(datetime(2026, 8, 19, 12, 0, tzinfo=UTC)),
    )
    t1.record_tokens(1_400)
    t1.record_tokens(27)
    assert t1.day_to_date() == 1_427

    # A fresh instance (container restart) reads the same durable ledger.
    t2 = DailyTokenTracker(
        limit_tokens=200_000,
        state_path=state,
        now_fn=_frozen(datetime(2026, 8, 19, 18, 0, tzinfo=UTC)),
    )
    assert t2.day_to_date() == 1_427
    assert t2.is_exhausted() is False


def test_daily_tracker_exhaustion_and_recovery_next_day(tmp_path: Path) -> None:
    state = tmp_path / "zai-tokens.json"
    day1 = datetime(2026, 8, 19, 23, 59, tzinfo=UTC)
    tracker = DailyTokenTracker(limit_tokens=100, state_path=state, now_fn=_frozen(day1))
    tracker.record_tokens(100)
    assert tracker.is_exhausted() is True

    # Next UTC day: a fresh bucket — the ceiling resets without operator action.
    day2 = datetime(2026, 8, 20, 0, 1, tzinfo=UTC)
    next_day = DailyTokenTracker(limit_tokens=100, state_path=state, now_fn=_frozen(day2))
    assert next_day.is_exhausted() is False
    assert next_day.day_to_date() == 0


def test_daily_tracker_zero_limit_disables_cap(tmp_path: Path) -> None:
    tracker = DailyTokenTracker(limit_tokens=0, state_path=tmp_path / "t.json")
    tracker.record_tokens(10_000_000)
    assert tracker.capped is False
    assert tracker.is_exhausted() is False


def test_daily_tracker_ignores_garbage_and_negative_counts(tmp_path: Path) -> None:
    tracker = DailyTokenTracker(limit_tokens=10, state_path=tmp_path / "t.json")
    tracker.record_tokens(-5)
    tracker.record_tokens("lots")  # type: ignore[arg-type]
    assert tracker.day_to_date() == 0


def test_daily_tracker_corrupt_state_restarts_at_zero(tmp_path: Path) -> None:
    state = tmp_path / "zai-tokens.json"
    state.write_text("{not json", encoding="utf-8")
    tracker = DailyTokenTracker(limit_tokens=10, state_path=state)
    assert tracker.day_to_date() == 0
    tracker.record_tokens(4)
    assert json.loads(state.read_text())["days"] == {utc_day_key(): 4}
