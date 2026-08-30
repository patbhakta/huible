"""Startup hydration of the persona registry from the DB (HU-1435).

The real-user flip verification on prod caught the wiring gap: the bare app
boots with an empty ``InMemoryPersonaRegistry`` and nothing reads the
``personas`` table, so a DB-seeded deploy served ``404 PERSONA_NOT_FOUND``
for every chat turn. These tests pin the boot-time hydration contract.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace
from uuid import UUID

import pytest

from huible.api.app import _hydrate_persona_registry, create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.settings import Settings
from huible.memory.protocol import MemoryBackend

PERSONA_ID = UUID("a0000000-0000-0000-0000-000000000001")


class _NullBackend(MemoryBackend):
    async def store_memory(self, node):  # pragma: no cover - unused here
        return node.id

    async def get_memory(self, memory_id):  # pragma: no cover - unused here
        return None


def _row(**overrides):
    row = {
        "id": PERSONA_ID,
        "name": "Robert James Mitchell",
        "voice_instructions": "Warm Texan.",
        "era_knowledge_boundary": dt.date(2021, 11, 3),
        "age_at_death": 69,
        "death_date": dt.date(2021, 11, 3),
        # personas.metadata is a json column; asyncpg hands it back as a
        # str (or None). HU-2231: carries the measured corpus length
        # register when the persona was provisioned from transcripts.
        "metadata": None,
    }
    row.update(overrides)
    return row


def _app(rows=None, *, connect_error=None, preseeded=False, url="postgresql+asyncpg://x/y"):
    application = create_app(
        settings=Settings(database_url=url),
        api_key_store=InMemoryApiKeyStore(),
        persona_registry=InMemoryPersonaRegistry(
            {PERSONA_ID: (SimpleNamespace(id=PERSONA_ID), _NullBackend())}
        )
        if preseeded
        else None,
    )
    application.state.memory_backend = _NullBackend()

    class _Conn:
        async def fetch(self, sql):
            assert "FROM personas" in sql
            return rows or []

        async def close(self):
            pass

    class _Module:
        @staticmethod
        async def connect(u):
            if connect_error:
                raise connect_error
            # hydration normalizes the SQLAlchemy scheme to plain postgresql
            assert u == "postgresql://x/y"
            return _Conn()

    import huible.api.app as app_module

    (monkey := pytest.MonkeyPatch()).setattr(app_module, "asyncpg", _Module, raising=False)
    # asyncpg.connect is imported inside the function body, so patch the real
    # module attribute it resolves against at call time.
    import asyncpg as real_asyncpg

    monkey.setattr(real_asyncpg, "connect", _Module.connect)
    return application, monkey


def test_hydrate_registers_db_persona_into_empty_registry():
    application, monkey = _app(rows=[_row()])
    try:
        registry = application.state.persona_registry
        assert len(registry) == 0
        count = asyncio.run(_hydrate_persona_registry(application))
        assert count == 1
        assert len(registry) == 1
        binding = registry.get(PERSONA_ID, requester_tier=None)
        assert binding.persona.name == "Robert James Mitchell"
        assert binding.persona.voice_instructions == "Warm Texan."
        assert binding.persona.era_knowledge_boundary == "2021-11-03"
        assert binding.persona.death_date == "2021-11-03"
        assert binding.persona.age_at_death == 69
        # No corpus_length block -> safe default budget (fail closed).
        assert binding.persona.length_stats is None
        assert binding.backend is application.state.memory_backend
    finally:
        monkey.undo()


def test_hydrate_defaults_when_optional_fields_missing():
    application, monkey = _app(
        rows=[_row(era_knowledge_boundary=None, death_date=None, age_at_death=None)]
    )
    try:
        asyncio.run(_hydrate_persona_registry(application))
        persona = application.state.persona_registry.get(
            PERSONA_ID, requester_tier=None
        ).persona
        assert persona.era_knowledge_boundary == "2020-01-01"
        assert persona.death_date is None
        assert persona.age_at_death is None
    finally:
        monkey.undo()


def test_hydrate_loads_corpus_length_register_from_metadata():
    """HU-2231: a provisioned persona's measured length register (json str
    from asyncpg) hydrates onto PersonaConfig.length_stats; garbage in the
    block fails closed to the default budget instead of breaking boot."""
    import json

    from huible.persona.length import CHANDLER_GROUND_TRUTH

    good = json.dumps(
        {"corpus": "friends-v2.csv", "corpus_length": {
            "median_chars": 44, "p75_chars": 79,
            "p90_chars": 129, "sample_lines": 8376,
        }}
    )
    application, monkey = _app(rows=[_row(metadata=good)])
    try:
        asyncio.run(_hydrate_persona_registry(application))
        persona = application.state.persona_registry.get(
            PERSONA_ID, requester_tier=None
        ).persona
        assert persona.length_stats == CHANDLER_GROUND_TRUTH
    finally:
        monkey.undo()

    bad = "{not json at all"
    application2, monkey2 = _app(rows=[_row(metadata=bad)])
    try:
        assert asyncio.run(_hydrate_persona_registry(application2)) == 1
        persona2 = application2.state.persona_registry.get(
            PERSONA_ID, requester_tier=None
        ).persona
        assert persona2.length_stats is None
    finally:
        monkey2.undo()


def test_hydrate_skipped_when_registry_preseeded():
    application, monkey = _app(rows=[_row()], preseeded=True)
    try:
        assert asyncio.run(_hydrate_persona_registry(application)) == 0
        assert len(application.state.persona_registry) == 1  # only the seed
    finally:
        monkey.undo()


def test_hydrate_survives_db_connect_failure():
    application, monkey = _app(connect_error=RuntimeError("db down"))
    try:
        assert asyncio.run(_hydrate_persona_registry(application)) == 0
        assert len(application.state.persona_registry) == 0
    finally:
        monkey.undo()


def test_hydrate_noop_without_database_url():
    application, monkey = _app(rows=[_row()], url="")
    try:
        assert asyncio.run(_hydrate_persona_registry(application)) == 0
    finally:
        monkey.undo()
