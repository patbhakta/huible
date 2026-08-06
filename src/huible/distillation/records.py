"""TencentDB-style L0-L3 memory pyramid records.

The Huible persona engine reads from consolidated Markdown produced by the
L0 -> L1 -> L2 -> L3 distillation pipeline.  Every abstraction level keeps
deterministic evidence links back to the raw L0 source so traceability is
preserved without a graph database.

Memory types
------------
Memory is typed so the persona engine can answer *dynamically* from a single
Markdown store:

- ``observation``: a transient, single-event fact (e.g. "Pat mentioned the
  garden on 2024-05-01").  Has a bounded validity window.
- ``current_state``: the present truth of a mutable fact (e.g. "Pat's current
  address").  A newer observation supersedes the old value; only the latest
  active state is used.
- ``durable_rule``: a long-lived rule or preference (e.g. "Pat always drinks
  Earl Grey with oat milk").  Valid indefinitely unless explicitly revoked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class MemoryType(StrEnum):
    OBSERVATION = "observation"
    CURRENT_STATE = "current_state"
    DURABLE_RULE = "durable_rule"


class Tier(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


@dataclass(slots=True, frozen=True)
class EvidenceLink:
    """Deterministic pointer back to a raw L0 source record."""

    source_id: str
    source_kind: str
    span: str = ""
    note: str = ""


@dataclass(slots=True)
class L0Record:
    """Raw source: verbatim conversation turns or photo/upload metadata."""

    id: str = field(default_factory=lambda: str(uuid4()))
    kind: str = "conversation"
    title: str = ""
    content: str = ""
    occurred_at: datetime | None = None
    ingested_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class L1Fact:
    """Atomic fact distilled from an L0 record."""

    id: str = field(default_factory=lambda: str(uuid4()))
    subject: str = ""
    predicate: str = ""
    object: str = ""
    memory_type: MemoryType = MemoryType.OBSERVATION
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    evidence: list[EvidenceLink] = field(default_factory=list)
    confidence: float = 0.5
    content: str = ""


@dataclass(slots=True)
class L2Scenario:
    """Scenario/domain block grouping related L1 facts."""

    id: str = field(default_factory=lambda: str(uuid4()))
    scenario: str = ""
    domain: str = ""
    summary: str = ""
    facts: list[L1Fact] = field(default_factory=list)
    evidence: list[EvidenceLink] = field(default_factory=list)


@dataclass(slots=True)
class L3Profile:
    """Distilled persona rule/preference (durable rule or current state)."""

    id: str = field(default_factory=lambda: str(uuid4()))
    key: str = ""
    rule: str = ""
    memory_type: MemoryType = MemoryType.DURABLE_RULE
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    evidence: list[EvidenceLink] = field(default_factory=list)
    confidence: float = 0.5
    supersedes: str | None = None


@dataclass(slots=True)
class DistillationResult:
    """Full output of a L0 -> L3 distillation run for one upload batch."""

    raw: list[L0Record]
    facts: list[L1Fact]
    scenarios: list[L2Scenario]
    profiles: list[L3Profile]
    distilled_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
