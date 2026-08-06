"""File-backed Markdown memory store.

The persona engine reads from the consolidated Markdown produced by the
L0-L3 pipeline (source of truth), replacing raw-vector reliance for persona
answers.  This store writes/loads tier records as Markdown files and answers
temporal queries using the ``valid_from`` / ``valid_to`` frontmatter.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from huible.distillation.markdown import (
    parse_frontmatter,
    render_l0,
    render_l1,
    render_l2,
    render_l3,
)
from huible.distillation.records import (
    DistillationResult,
    L0Record,
    MemoryType,
    Tier,
    parse_iso,
)

logger = logging.getLogger(__name__)

_TIER_DIRS = {
    Tier.L0: "raw",
    Tier.L1: "facts",
    Tier.L2: "scenarios",
    Tier.L3: "profiles",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MarkdownMemoryStore:
    """Persists and queries consolidated Markdown memory records."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        for sub in _TIER_DIRS.values():
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # -- write -------------------------------------------------------------
    def write_result(self, result: DistillationResult) -> None:
        for record in result.raw:
            self._write(Tier.L0, record.id, render_l0(record))
        for fact in result.facts:
            self._write(Tier.L1, fact.id, render_l1(fact))
        for scenario in result.scenarios:
            self._write(Tier.L2, scenario.id, render_l2(scenario))
        for profile in result.profiles:
            self._write(Tier.L3, profile.id, render_l3(profile))

    def _write(self, tier: Tier, record_id: str, text: str) -> None:
        path = self.root / _TIER_DIRS[tier] / f"{record_id}.md"
        path.write_text(text, encoding="utf-8")

    # -- read --------------------------------------------------------------
    def list_records(self, tier: Tier) -> list[dict[str, Any]]:
        directory = self.root / _TIER_DIRS[tier]
        records: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.md")):
            fields, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            fields["_body"] = body
            fields["_path"] = str(path)
            records.append(fields)
        return records

    def load_raw(self) -> list[L0Record]:
        records: list[L0Record] = []
        for fields, body in self._read_all(Tier.L0):
            records.append(
                L0Record(
                    id=str(fields.get("record_id", "")),
                    kind=str(fields.get("source_kind", "conversation")),
                    title=str(fields.get("title", "")),
                    content=body,
                    occurred_at=parse_iso(fields.get("occurred_at")),
                    ingested_at=parse_iso(fields.get("ingested_at")) or _utcnow(),
                )
            )
        return records

    def _read_all(self, tier: Tier) -> list[tuple[dict[str, Any], str]]:
        directory = self.root / _TIER_DIRS[tier]
        out: list[tuple[dict[str, Any], str]] = []
        for path in sorted(directory.glob("*.md")):
            out.append(parse_frontmatter(path.read_text(encoding="utf-8")))
        return out

    # -- query -------------------------------------------------------------
    def query(
        self,
        memory_type: MemoryType | None = None,
        now: datetime | None = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Return consolidated Markdown records valid at ``now``.

        A record is valid when ``valid_from <= now`` and (``valid_to`` is
        null or ``valid_to >= now``).  Durable rules and current states are
        always considered active unless explicitly revoked via ``valid_to``.
        """
        now = now or _utcnow()
        results: list[dict[str, Any]] = []
        for tier in (Tier.L1, Tier.L2, Tier.L3):
            for fields, body in self._read_all(tier):
                if memory_type is not None and fields.get("memory_type") != memory_type.value:
                    continue
                if active_only and not self._is_valid(fields, now):
                    continue
                fields["_body"] = body
                results.append(fields)
        return results

    def _is_valid(self, fields: dict[str, Any], now: datetime) -> bool:
        valid_from = parse_iso(fields.get("valid_from"))
        valid_to = parse_iso(fields.get("valid_to"))
        if valid_from is not None and valid_from > now:
            return False
        return not (valid_to is not None and valid_to < now)

    def active_rules(self, now: datetime | None = None) -> list[dict[str, Any]]:
        return self.query(MemoryType.DURABLE_RULE, now=now)

    def current_states(self, now: datetime | None = None) -> list[dict[str, Any]]:
        return self.query(MemoryType.CURRENT_STATE, now=now)

    def observations(self, now: datetime | None = None) -> list[dict[str, Any]]:
        return self.query(MemoryType.OBSERVATION, now=now)
