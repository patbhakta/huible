from __future__ import annotations

from uuid import uuid4

from huible.memory.protocol import (
    QuarantineEntry,
    QuarantinePriority,
    QuarantineStatus,
)


class TestF3_1_EnqueueDequeue:
    """F3.1: Quarantine entries can be enqueued and dequeued in priority order."""

    async def test_enqueue_and_retrieve(self, queue, persona_id):
        entry = QuarantineEntry(
            id=uuid4(),
            candidate_data={"content": "test"},
            persona_id=persona_id,
            failed_gates=["safety"],
            priority=QuarantinePriority.CRITICAL,
        )
        saved_id = await queue.enqueue(entry)
        pending = await queue.dequeue()
        assert len(pending) == 1
        assert pending[0].id == saved_id
        assert pending[0].priority == QuarantinePriority.CRITICAL

    async def test_priority_ordering_critical_first(self, queue, persona_id):
        for priority in [
            QuarantinePriority.LOW,
            QuarantinePriority.CRITICAL,
            QuarantinePriority.MEDIUM,
        ]:
            await queue.enqueue(QuarantineEntry(
                id=uuid4(), candidate_data={"content": "test"}, persona_id=persona_id,
                failed_gates=["test"], priority=priority,
            ))

        pending = await queue.dequeue(limit=10)
        assert pending[0].priority == QuarantinePriority.CRITICAL
        assert pending[1].priority == QuarantinePriority.MEDIUM
        assert pending[2].priority == QuarantinePriority.LOW


class TestF3_2_Adjudication:
    """F3.2: Quarantine entries can be adjudicated (promote/reject)."""

    async def test_promote_updates_status(self, queue, persona_id):
        entry = QuarantineEntry(
            id=uuid4(), candidate_data={"content": "test"}, persona_id=persona_id,
            failed_gates=["pertinence"], priority=QuarantinePriority.LOW,
        )
        await queue.enqueue(entry)
        result = await queue.adjudicate(
            entry.id, "promote", adjudicated_by=uuid4(), note="Approved",
        )
        assert result.status == QuarantineStatus.PROMOTED
        assert result.adjudicated_by is not None

    async def test_reject_updates_status(self, queue, persona_id):
        entry = QuarantineEntry(
            id=uuid4(), candidate_data={"content": "test"}, persona_id=persona_id,
            failed_gates=["safety"], priority=QuarantinePriority.CRITICAL,
        )
        await queue.enqueue(entry)
        result = await queue.adjudicate(entry.id, "reject", note="Dangerous")
        assert result.status == QuarantineStatus.REJECTED

    async def test_adjudicate_nonexistent_returns_none(self, queue):
        result = await queue.adjudicate(uuid4(), "promote")
        assert result is None

    async def test_double_adjudicate_returns_none(self, queue, persona_id):
        entry = QuarantineEntry(
            id=uuid4(), candidate_data={"content": "test"}, persona_id=persona_id,
            failed_gates=["test"], priority=QuarantinePriority.LOW,
        )
        await queue.enqueue(entry)
        await queue.adjudicate(entry.id, "reject")
        second = await queue.adjudicate(entry.id, "promote")
        assert second is None

    async def test_adjudication_records_note(self, queue, persona_id):
        entry = QuarantineEntry(
            id=uuid4(), candidate_data={"content": "test"}, persona_id=persona_id,
            failed_gates=["test"], priority=QuarantinePriority.LOW,
        )
        await queue.enqueue(entry)
        result = await queue.adjudicate(entry.id, "promote", note="Family approved")
        assert result.candidate_data["_adjudication"]["note"] == "Family approved"


class TestF3_3_PersonaScoping:
    """F3.3: Quarantine entries are scoped to persona."""

    async def test_list_by_persona(self, queue, persona_id):
        other_persona = uuid4()
        await queue.enqueue(QuarantineEntry(
            id=uuid4(), candidate_data={"content": "a"}, persona_id=persona_id,
            failed_gates=["test"], priority=QuarantinePriority.LOW,
        ))
        await queue.enqueue(QuarantineEntry(
            id=uuid4(), candidate_data={"content": "b"}, persona_id=other_persona,
            failed_gates=["test"], priority=QuarantinePriority.LOW,
        ))
        persona_entries = await queue.list_by_persona(persona_id)
        assert len(persona_entries) == 1

    async def test_filter_by_priority(self, queue, persona_id):
        await queue.enqueue(QuarantineEntry(
            id=uuid4(), candidate_data={"content": "c"}, persona_id=persona_id,
            failed_gates=["safety"], priority=QuarantinePriority.CRITICAL,
        ))
        await queue.enqueue(QuarantineEntry(
            id=uuid4(), candidate_data={"content": "d"}, persona_id=persona_id,
            failed_gates=["pertinence"], priority=QuarantinePriority.LOW,
        ))
        critical = await queue.dequeue(priority=QuarantinePriority.CRITICAL)
        assert len(critical) == 1
        assert critical[0].priority == QuarantinePriority.CRITICAL

    async def test_dequeue_skips_adjudicated(self, queue, persona_id):
        entry = QuarantineEntry(
            id=uuid4(), candidate_data={"content": "e"}, persona_id=persona_id,
            failed_gates=["test"], priority=QuarantinePriority.LOW,
        )
        await queue.enqueue(entry)
        await queue.adjudicate(entry.id, "reject")
        pending = await queue.dequeue()
        assert len(pending) == 0
