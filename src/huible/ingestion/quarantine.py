from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import UUID

from huible.memory.protocol import (
    QuarantineEntry,
    QuarantinePriority,
    QuarantineStatus,
)

logger = logging.getLogger(__name__)

_PRIORITY_ORDER: dict[QuarantinePriority, int] = {
    QuarantinePriority.CRITICAL: 0,
    QuarantinePriority.HIGH: 1,
    QuarantinePriority.MEDIUM: 2,
    QuarantinePriority.LOW: 3,
}

_PRIORITY_NAMES: dict[int, str] = {v: k.value for k, v in _PRIORITY_ORDER.items()}


@runtime_checkable
class QuarantineStore(Protocol):
    async def save(self, entry: QuarantineEntry) -> UUID: ...
    async def get(self, entry_id: UUID) -> QuarantineEntry | None: ...
    async def list_pending(
        self,
        priority: QuarantinePriority | None = None,
        limit: int = 50,
    ) -> list[QuarantineEntry]: ...
    async def update(self, entry: QuarantineEntry) -> None: ...
    async def get_by_persona(
        self,
        persona_id: UUID,
        status: QuarantineStatus | None = None,
    ) -> list[QuarantineEntry]: ...


class InMemoryQuarantineStore(QuarantineStore):
    def __init__(self) -> None:
        self._entries: dict[UUID, QuarantineEntry] = {}

    async def save(self, entry: QuarantineEntry) -> UUID:
        self._entries[entry.id] = entry
        return entry.id

    async def get(self, entry_id: UUID) -> QuarantineEntry | None:
        return self._entries.get(entry_id)

    async def list_pending(
        self,
        priority: QuarantinePriority | None = None,
        limit: int = 50,
    ) -> list[QuarantineEntry]:
        pending = [
            e for e in self._entries.values()
            if e.status == QuarantineStatus.PENDING
            and (priority is None or e.priority == priority)
        ]
        pending.sort(key=lambda e: _PRIORITY_ORDER.get(e.priority, 3))
        return pending[:limit]

    async def update(self, entry: QuarantineEntry) -> None:
        if entry.id in self._entries:
            self._entries[entry.id] = entry

    async def get_by_persona(
        self,
        persona_id: UUID,
        status: QuarantineStatus | None = None,
    ) -> list[QuarantineEntry]:
        results = [
            e for e in self._entries.values()
            if e.persona_id == persona_id
            and (status is None or e.status == status)
        ]
        results.sort(key=lambda e: e.created_at, reverse=True)
        return results


class QuarantineQueue:
    def __init__(self, store: QuarantineStore | None = None) -> None:
        self._store = store or InMemoryQuarantineStore()

    async def enqueue(self, entry: QuarantineEntry) -> UUID:
        logger.info(
            "Quarantine enqueue: id=%s priority=%s gates=%s",
            entry.id,
            entry.priority.value,
            entry.failed_gates,
        )
        return await self._store.save(entry)

    async def dequeue(
        self,
        priority: QuarantinePriority | None = None,
        limit: int = 1,
    ) -> list[QuarantineEntry]:
        entries = await self._store.list_pending(priority=priority, limit=limit)
        return entries

    async def adjudicate(
        self,
        entry_id: UUID,
        decision: str,
        adjudicated_by: UUID | None = None,
        note: str = "",
    ) -> QuarantineEntry | None:
        entry = await self._store.get(entry_id)
        if entry is None or entry.status != QuarantineStatus.PENDING:
            return None

        if decision == "promote":
            new_status = QuarantineStatus.PROMOTED
        elif decision == "reject":
            new_status = QuarantineStatus.REJECTED
        elif decision == "adjudicated":
            new_status = QuarantineStatus.ADJUDICATED
        else:
            raise ValueError(f"Unknown decision: {decision}")

        updated_data = dict(entry.candidate_data)
        updated_data.setdefault("_adjudication", {})
        updated_data["_adjudication"]["decision"] = decision
        updated_data["_adjudication"]["note"] = note

        entry = replace(
            entry,
            status=new_status,
            adjudicated_by=adjudicated_by,
            adjudicated_at=datetime.now(timezone.utc),
            candidate_data=updated_data,
        )

        await self._store.update(entry)
        logger.info(
            "Quarantine adjudication: id=%s decision=%s by=%s",
            entry_id,
            decision,
            adjudicated_by,
        )
        return entry

    async def get(self, entry_id: UUID) -> QuarantineEntry | None:
        return await self._store.get(entry_id)

    async def list_by_persona(
        self,
        persona_id: UUID,
        status: QuarantineStatus | None = None,
    ) -> list[QuarantineEntry]:
        return await self._store.get_by_persona(persona_id, status)

    @property
    def store(self) -> QuarantineStore:
        return self._store
