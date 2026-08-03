from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from huible.memory.protocol import (
    ContentType,
    MemoryNode,
    MemoryTier,
    SourceType,
)
from tests.f1.conftest import CosineFakeBackend

PERSONA_ID = uuid4()


@pytest.fixture
def backend() -> CosineFakeBackend:
    return CosineFakeBackend()


@pytest.fixture
def persona_id() -> uuid4:
    return PERSONA_ID


def _make_node(
    persona_id: uuid4,
    content: str = "Dad loved fishing",
    tier: MemoryTier = MemoryTier.ACCRUED,
    version: int = 1,
    is_active: bool = True,
    supersedes: uuid4 | None = None,
) -> MemoryNode:
    return MemoryNode(
        id=uuid4(),
        persona_id=persona_id,
        tier=tier,
        content=content,
        content_type=ContentType.NARRATIVE,
        memory_date=date(2015, 7, 15),
        source_type=SourceType.EXTRACTION,
        version=version,
        is_active=is_active,
        supersedes=supersedes,
    )
