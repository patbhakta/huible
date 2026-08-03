from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from huible.ingestion.quarantine import InMemoryQuarantineStore, QuarantineQueue

PERSONA_ID = uuid4()


@pytest.fixture
def store() -> InMemoryQuarantineStore:
    return InMemoryQuarantineStore()


@pytest.fixture
def queue(store: InMemoryQuarantineStore) -> QuarantineQueue:
    return QuarantineQueue(store=store)


@pytest.fixture
def persona_id() -> UUID:
    return PERSONA_ID
