from __future__ import annotations

import logging
from uuid import UUID

from huible.ingestion.quarantine import QuarantineQueue
from huible.memory.protocol import QuarantinePriority

logger = logging.getLogger(__name__)


class AdjudicationAPI:
    def __init__(self, queue: QuarantineQueue) -> None:
        self._queue = queue

    async def list_pending(
        self,
        priority: QuarantinePriority | None = None,
        limit: int = 50,
    ) -> list[dict]:
        entries = await self._queue.dequeue(priority=priority, limit=limit)
        return [_entry_to_dict(e) for e in entries]

    async def get_entry(self, entry_id: UUID) -> dict | None:
        entry = await self._queue.get(entry_id)
        if entry is None:
            return None
        return _entry_to_dict(entry)

    async def approve(
        self,
        entry_id: UUID,
        adjudicated_by: UUID | None = None,
        note: str = "",
    ) -> dict | None:
        entry = await self._queue.adjudicate(
            entry_id=entry_id,
            decision="promote",
            adjudicated_by=adjudicated_by,
            note=note,
        )
        if entry is None:
            return None
        return _entry_to_dict(entry)

    async def reject(
        self,
        entry_id: UUID,
        adjudicated_by: UUID | None = None,
        note: str = "",
    ) -> dict | None:
        entry = await self._queue.adjudicate(
            entry_id=entry_id,
            decision="reject",
            adjudicated_by=adjudicated_by,
            note=note,
        )
        if entry is None:
            return None
        return _entry_to_dict(entry)

    async def mark_adjudicated(
        self,
        entry_id: UUID,
        decision: str = "adjudicated",
        adjudicated_by: UUID | None = None,
        note: str = "",
    ) -> dict | None:
        entry = await self._queue.adjudicate(
            entry_id=entry_id,
            decision=decision,
            adjudicated_by=adjudicated_by,
            note=note,
        )
        if entry is None:
            return None
        return _entry_to_dict(entry)


def _entry_to_dict(entry) -> dict:
    return {
        "id": str(entry.id),
        "persona_id": str(entry.persona_id),
        "failed_gates": entry.failed_gates,
        "priority": entry.priority.value,
        "status": entry.status.value,
        "adjudicated_by": str(entry.adjudicated_by) if entry.adjudicated_by else None,
        "adjudicated_at": entry.adjudicated_at.isoformat() if entry.adjudicated_at else None,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "candidate_data": entry.candidate_data,
    }
