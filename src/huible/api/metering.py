"""Usage + billing metering for the persona product surface (HU-2243 Sprint 1).

Founder directive (Pat, 2026-08-30): HUible product traffic must move to a
dedicated provider API key so usage/billing are trackable separately from
internal workloads, with per-persona / per-conversation token + cost
accounting surfaced as dashboard/report data. Sprint 1 (CEO scope,
2026-08-31) lands the **metering skeleton** inside the SaaS codebase:

* per-org / per-conversation usage rows — requests, tokens in/out, latency,
  modeled cost — on the existing Postgres, written by the chat path on every
  LLM turn;
* a read endpoint (``GET /api/v1/usage/daily``) returning per-key /
  per-persona daily aggregates.

Out of scope this sprint: Polar wiring, the BYOK vault, dashboard UI.

Design (mirrors :mod:`huible.safety.store`, the §7.4 durability pattern):

* **Sync SQLAlchemy** (``postgresql+psycopg``) so the chat-path write stays
  on the same synchronous call shape the safety backends use, and the test
  suite runs against in-memory SQLite (backend-portable types).
* **Deterministic in-memory default** (:class:`InMemoryUsageRecorder`) so
  the key-free / pre-DB posture never breaks a chat turn — the durable
  :class:`PostgresUsageRecorder` drops in when a safety DB URL is
  configured (wired in :func:`huible.api.app.create_app`).
* **Key attribution without raw keys** — rows carry ``api_key_id``, a
  SHA-256 digest of the caller's bearer key (16 hex chars). Raw API keys
  are never persisted in usage rows.
* **Modeled cost at reference rates** — the current Chandler voice runs on
  the z.ai coding subscription, which is quota-not-metered (founder
  directive note), so incremental spend is $0 but valuation / plan-pricing
  data needs a *modeled* cost. :func:`modeled_cost_usd` prices tokens at
  public per-1M-token reference rates (:data:`REFERENCE_RATES_USD_PER_MTOK`)
  with a generic default for unknown models. ``cost_basis`` records how the
  number was derived (``modeled`` reference rates, ``reported`` provider
  cost, or ``free`` for the deterministic fake voice).

Rows are written only for turns that reach the LLM (persona-voiced
generation, including the budget-exhausted fake-voice fallback). Turns that
short-circuit before generation (crisis handoff, consent gate, risk
enforcement refusals) consume no metered resource; their outcome telemetry
stays in the Prometheus counters (``huible.api.metrics``).
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

logger = logging.getLogger(__name__)

__all__ = [
    "REFERENCE_RATES_USD_PER_MTOK",
    "InMemoryUsageRecorder",
    "PostgresUsageRecorder",
    "UsageDailyAggregate",
    "UsageRecord",
    "UsageRecorder",
    "UsageRow",
    "api_key_attribution_id",
    "estimate_tokens",
    "modeled_cost_usd",
]


# --- attribution + cost model ------------------------------------------------


def api_key_attribution_id(api_key: str) -> str:
    """Return the stable attribution id for a raw bearer API key.

    First 16 hex chars of the SHA-256 digest — enough to distinguish keys
    and join aggregates, never enough to reuse the credential. The same
    caller key always lands on the same ``api_key_id`` across days, so
    per-key rollups (plan pricing, B2B API billing) key correctly without
    the raw key ever being persisted.
    """
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


#: Public per-1M-token reference rates (USD) used to *model* cost. The z.ai
#: coding subscription bills quota, not tokens — incremental spend is $0 —
#: so valuation / plan-pricing data needs a modeled number at rates a paid
#: API key would cost. Rates are deliberately coarse reference points, not
#: live pricebook data; refine when the dedicated product key lands (the
#: provider then reports real usage/cost, and ``cost_basis`` flips to
#: ``reported``).
REFERENCE_RATES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # model -> (input USD / 1M tokens, output USD / 1M tokens)
    "glm-5.3": (0.60, 2.20),
    "glm-5.2": (0.60, 2.20),
    "gemini-2.5-flash": (0.30, 2.50),
    "google/gemini-3-flash-preview": (0.30, 2.50),
}

#: Generic fallback rate for models not in the table (in, out) per 1M tokens.
DEFAULT_RATE_USD_PER_MTOK: tuple[float, float] = (0.60, 2.20)


def modeled_cost_usd(
    *,
    model: str | None,
    tokens_in: int,
    tokens_out: int,
    provider: str,
    reported_cost_usd: float | None = None,
) -> tuple[float, str]:
    """Return ``(modeled_cost_usd, cost_basis)`` for one metered turn.

    * ``reported`` — the provider reported a real USD cost for the call
      (OpenRouter ``usage.cost``); that number wins.
    * ``free`` — the deterministic fake voice (no hosted resource consumed).
    * ``modeled`` — reference-rate pricing of the token counts (the z.ai
      subscription case: quota-not-metered, so the number exists for
      valuation / plan pricing, not invoice reconciliation).
    """
    if reported_cost_usd is not None and reported_cost_usd > 0:
        return float(reported_cost_usd), "reported"
    # Substring match so the budget-fallback composite labels
    # (``zai->fake(budget)``) also count as fake-voiced turns — no hosted
    # resource was consumed on those either.
    if "fake" in provider:
        return 0.0, "free"
    rate_in, rate_out = REFERENCE_RATES_USD_PER_MTOK.get(
        (model or "").lower(), DEFAULT_RATE_USD_PER_MTOK
    )
    cost = (tokens_in * rate_in + tokens_out * rate_out) / 1_000_000
    # Round to 8 decimals — NUMERIC(14,8) precision, dust-free aggregates.
    return round(cost, 8), "modeled"


def estimate_tokens(text: str) -> int:
    """Cheap deterministic token estimate (~4 chars/token) for providers
    that do not report a usage block (the fake voice, degraded responses).

    Only a fallback for metering breadth — every real provider in
    :mod:`huible.llm.client` reports exact counts on ``last_usage``.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def utc_day(now: datetime | None = None) -> date:
    """The UTC calendar date for a timestamp (daily-aggregate grain).

    Consistent with the zai daily-token ledger (HU-1910), which also rolls
    on the UTC day — one day-boundary convention across metering surfaces.
    """
    return (now or datetime.now(UTC)).astimezone(UTC).date()


# --- value objects ------------------------------------------------------------


@dataclass(slots=True)
class UsageRecord:
    """One metered chat-turn LLM call (the write-path unit).

    Attribution: ``org_id`` (tenant, ``None`` until orgs exist — the column
    is ready so per-org rollups work the moment keys gain org bindings),
    ``api_key_id`` (bearer-key digest, see :func:`api_key_attribution_id`),
    ``persona_id``, ``conversation_id``. Resource: ``provider`` / ``model``,
    ``requests`` / ``tokens_in`` / ``tokens_out`` / ``latency_ms``, and the
    modeled cost + basis from :func:`modeled_cost_usd`.
    """

    api_key_id: str
    persona_id: str
    provider: str
    model: str | None
    tokens_in: int
    tokens_out: int
    latency_ms: int
    conversation_id: str | None = None
    org_id: str | None = None
    requests: int = 1
    reported_cost_usd: float | None = None
    #: Which provider key served the turn (HU-2243 key separation / BYOK):
    #: ``byok`` client-supplied key, ``product`` dedicated product key,
    #: ``shared`` the shared internals key (pre-separation default).
    key_source: str = "shared"
    day: date = field(default_factory=utc_day)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def resolved_cost(self) -> tuple[float, str]:
        return modeled_cost_usd(
            model=self.model,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            provider=self.provider,
            reported_cost_usd=self.reported_cost_usd,
        )


@dataclass(slots=True)
class UsageDailyAggregate:
    """One (day, api_key_id, persona_id) aggregate row (the read-path unit)."""

    day: date
    api_key_id: str
    persona_id: str
    requests: int
    tokens_in: int
    tokens_out: int
    modeled_cost_usd: float
    avg_latency_ms: float
    conversations: int
    org_id: str | None = None
    key_source: str = "shared"


@runtime_checkable
class UsageRecorder(Protocol):
    """Pluggable metering backend (write one row, read daily aggregates)."""

    def record_turn(self, record: UsageRecord) -> None: ...

    def daily_aggregates(
        self,
        *,
        from_day: date,
        to_day: date,
        persona_id: str | None = None,
        api_key_id: str | None = None,
    ) -> list[UsageDailyAggregate]: ...


# --- in-memory default ---------------------------------------------------------


class InMemoryUsageRecorder:
    """Deterministic in-memory :class:`UsageRecorder` (key-free default).

    Same shape as the pre-real-user §7.4 defaults: the chat path meters
    even with no DB wired, so the skeleton is exercisable key-free and the
    durable backend drops in without touching callers. Aggregates are
    computed in Python over the retained rows.
    """

    def __init__(self) -> None:
        self.rows: list[UsageRecord] = []

    def record_turn(self, record: UsageRecord) -> None:
        self.rows.append(record)

    def daily_aggregates(
        self,
        *,
        from_day: date,
        to_day: date,
        persona_id: str | None = None,
        api_key_id: str | None = None,
    ) -> list[UsageDailyAggregate]:
        grouped: dict[tuple[date, str, str, str], list[UsageRecord]] = {}
        for row in self.rows:
            if not (from_day <= row.day <= to_day):
                continue
            if persona_id is not None and row.persona_id != persona_id:
                continue
            if api_key_id is not None and row.api_key_id != api_key_id:
                continue
            grouped.setdefault(
                (row.day, row.api_key_id, row.persona_id, row.key_source), []
            ).append(row)
        aggregates: list[UsageDailyAggregate] = []
        for (day, key_id, pid, source), rows in grouped.items():
            costs = [row.resolved_cost()[0] for row in rows]
            org_ids = {row.org_id for row in rows if row.org_id}
            aggregates.append(
                UsageDailyAggregate(
                    day=day,
                    api_key_id=key_id,
                    persona_id=pid,
                    requests=sum(r.requests for r in rows),
                    tokens_in=sum(r.tokens_in for r in rows),
                    tokens_out=sum(r.tokens_out for r in rows),
                    modeled_cost_usd=round(sum(costs), 8),
                    avg_latency_ms=round(sum(r.latency_ms for r in rows) / len(rows), 2),
                    conversations=len({r.conversation_id for r in rows if r.conversation_id}),
                    org_id=org_ids.pop() if len(org_ids) == 1 else None,
                    key_source=source,
                )
            )
        aggregates.sort(key=lambda a: (a.day, a.api_key_id, a.persona_id, a.key_source))
        return aggregates


# --- durable Postgres backend --------------------------------------------------


class MeteringBase(DeclarativeBase):
    """ORM base for the metering table (``llm_usage``, migration 004)."""


class UsageRow(MeteringBase):
    """Durable row for one metered chat-turn LLM call.

    Portable types (String/Integer/Float) so the test suite can run against
    SQLite; the Alembic migration declares the production Postgres types
    (NUMERIC, TIMESTAMPTZ, DATE, UUID). Same convention as
    :mod:`huible.safety.store`.
    """

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    org_id: Mapped[str | None] = mapped_column(
        # Native UUID on Postgres (matches migration 004), portable storage
        # elsewhere — ``as_uuid=False`` keeps the Python-side str contract.
        Uuid(as_uuid=False),
        nullable=True,
    )
    api_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    persona_id: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    modeled_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_basis: Mapped[str] = mapped_column(String(16), nullable=False, default="modeled")
    # HU-2243 key separation / BYOK: which provider key served the turn —
    # ``byok`` | ``product`` | ``shared`` (migration 005; historical rows
    # backfilled ``shared`` — they ran on the shared internals key).
    key_source: Mapped[str] = mapped_column(String(16), nullable=False, default="shared")
    day: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        Index("idx_llm_usage_day", "day"),
        Index("idx_llm_usage_key_day", "api_key_id", "day"),
        Index("idx_llm_usage_persona_day", "persona_id", "day"),
        Index("idx_llm_usage_conversation", "conversation_id"),
        Index("idx_llm_usage_org_day", "org_id", "day"),
    )


class PostgresUsageRecorder:
    """Durable :class:`UsageRecorder` backend (sync SQLAlchemy + Postgres).

    One indexed INSERT per metered turn; daily aggregates are a single
    GROUP BY over ``(day, api_key_id, persona_id)``. Construction is lazy
    (``create_engine`` does not connect) so app startup stays fast — the
    same posture as the §7.4 safety backends (HU-1440).
    """

    def __init__(
        self,
        database_url: str,
        *,
        engine: Any | None = None,
        pool_size: int = 5,
        max_overflow: int = 10,
    ) -> None:
        # ``engine`` is injectable so tests can point the recorder at a
        # pre-created (sqlite) engine — production passes only the URL.
        self._engine = engine or create_engine(
            database_url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            future=True,
        )
        self._session_factory = sessionmaker(
            self._engine,
            class_=Session,
            expire_on_commit=False,
        )

    def close(self) -> None:
        self._engine.dispose()

    def record_turn(self, record: UsageRecord) -> None:
        cost, basis = record.resolved_cost()
        with self._session_factory() as session:
            session.add(
                UsageRow(
                    org_id=record.org_id,
                    api_key_id=record.api_key_id,
                    persona_id=record.persona_id,
                    conversation_id=record.conversation_id,
                    provider=record.provider,
                    model=record.model,
                    requests=record.requests,
                    tokens_in=record.tokens_in,
                    tokens_out=record.tokens_out,
                    latency_ms=record.latency_ms,
                    modeled_cost_usd=cost,
                    cost_basis=basis,
                    key_source=record.key_source,
                    day=record.day,
                    created_at=record.created_at,
                )
            )
            session.commit()

    def daily_aggregates(
        self,
        *,
        from_day: date,
        to_day: date,
        persona_id: str | None = None,
        api_key_id: str | None = None,
    ) -> list[UsageDailyAggregate]:
        day = UsageRow.day
        stmt = (
            select(
                day.label("day"),
                UsageRow.api_key_id.label("api_key_id"),
                UsageRow.persona_id.label("persona_id"),
                UsageRow.key_source.label("key_source"),
                func.sum(UsageRow.requests).label("requests"),
                func.sum(UsageRow.tokens_in).label("tokens_in"),
                func.sum(UsageRow.tokens_out).label("tokens_out"),
                func.sum(UsageRow.modeled_cost_usd).label("modeled_cost_usd"),
                func.avg(UsageRow.latency_ms).label("avg_latency_ms"),
                func.count(func.distinct(UsageRow.conversation_id)).label("conversations"),
                # ``min(uuid)`` has no Postgres impl — cast to text. Rows in a
                # group share one org (or are NULL), so min is the group's org.
                func.min(UsageRow.org_id.cast(String(64))).label("org_id"),
            )
            .where(day >= from_day)
            .where(day <= to_day)
            .group_by(day, UsageRow.api_key_id, UsageRow.persona_id, UsageRow.key_source)
            .order_by(day, UsageRow.api_key_id, UsageRow.persona_id, UsageRow.key_source)
        )
        if persona_id is not None:
            stmt = stmt.where(UsageRow.persona_id == persona_id)
        if api_key_id is not None:
            stmt = stmt.where(UsageRow.api_key_id == api_key_id)
        aggregates: list[UsageDailyAggregate] = []
        with self._session_factory() as session:
            for row in session.execute(stmt):
                aggregates.append(
                    UsageDailyAggregate(
                        day=row.day,
                        api_key_id=row.api_key_id,
                        persona_id=row.persona_id,
                        requests=int(row.requests or 0),
                        tokens_in=int(row.tokens_in or 0),
                        tokens_out=int(row.tokens_out or 0),
                        modeled_cost_usd=round(float(row.modeled_cost_usd or 0.0), 8),
                        avg_latency_ms=_round_half_up(row.avg_latency_ms),
                        conversations=int(row.conversations or 0),
                        org_id=row.org_id,
                        key_source=row.key_source,
                    )
                )
        return aggregates


def _round_half_up(value: Any) -> float:
    """Round a SQL AVG result to 2 decimals, NaN-safe (empty set guards)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number):
        return 0.0
    return round(number, 2)


def default_window(days: int, *, now: datetime | None = None) -> tuple[date, date]:
    """Return the ``(from_day, to_day)`` window for a trailing-days query."""
    today = utc_day(now)
    span = max(1, min(days, 366))
    return today - timedelta(days=span - 1), today
