from __future__ import annotations

from uuid import uuid4

import pytest

from tests.f1.conftest import CosineFakeBackend
from tests.f1.corpus import SyntheticCorpus

PERSONA_ID = uuid4()


@pytest.fixture(scope="module")
def corpus() -> SyntheticCorpus:
    c = SyntheticCorpus(n_memories=500, n_edges=1000, seed=42)
    c.generate()
    return c


@pytest.fixture(scope="module")
def backend(corpus: SyntheticCorpus) -> CosineFakeBackend:
    b = CosineFakeBackend()
    b.bulk_load(corpus.memories, corpus.edges)
    return b
