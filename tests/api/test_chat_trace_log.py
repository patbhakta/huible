"""Stdout ``chat.trace`` log hook tests (HU-1442).

The risk-enforcement, claim-alignment, and dosage signals are response-only
``.trace`` fields with no server-side aggregation. HU-1442 emits one
``chat.trace`` stdout line per chat turn (via ``_JsonLineFormatter``) so the
daily review can ``grep chat.trace`` across all five telemetry surfaces. These
tests assert the line is emitted on the persona, crisis, and kill-switch
branches and carries the fields the rollout-plan runbook reads.
"""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.real_user_gate import REAL_USER_TRAFFIC_CLASS_HEADER
from huible.api.settings import Settings
from huible.llm.client import FakeLLMClient
from tests.api.test_chat_e2e import API_KEY, PERSONA_ID, _persona, _seeded_backend


def _make_client(*, mode: str = "open") -> TestClient:
    settings = Settings(persona_chat_real_user_mode=mode)
    backend, _ = _seeded_backend()
    persona = _persona()
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=FakeLLMClient(persona_name="Chandler"),
        settings=settings,
        start_time=0.0,
    )
    return TestClient(application)


def _auth_headers(*, traffic: str = "internal") -> dict:
    return {"Authorization": f"Bearer {API_KEY}", REAL_USER_TRAFFIC_CLASS_HEADER: traffic}


def _consent(client: TestClient, conv: str) -> None:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers=_auth_headers(),
    )
    assert r.status_code == 200, r.text


def _trace_lines(records: list[logging.LogRecord]) -> list[str]:
    return [r.getMessage() for r in records if r.getMessage().startswith("chat.trace ")]


class TestChatTraceHook:
    def test_persona_turn_emits_trace_with_alignment_fields(self, caplog):
        client = _make_client()
        conv = "sess-trace-persona"
        _consent(client, conv)
        with caplog.at_level(logging.INFO, logger="huible.api.app"):
            r = client.post(
                f"/api/v1/chat/{PERSONA_ID}",
                json={
                    "message": "tell me about fishing on the lake",
                    "conversation_id": conv,
                },
                headers=_auth_headers(),
            )
        assert r.status_code == 200, r.text
        lines = _trace_lines(caplog.records)
        assert lines, "expected a chat.trace line on the persona turn"
        line = lines[-1]
        assert "action=" in line
        # alignment surface is present on the default path
        assert "ungrounded=" in line
        assert "disposition=" in line
        assert "turn_count=1" in line
        assert f"session={conv}" in line

    def test_crisis_turn_emits_handoff_trace(self, caplog):
        client = _make_client()
        conv = "sess-trace-crisis"
        _consent(client, conv)
        with caplog.at_level(logging.INFO, logger="huible.api.app"):
            client.post(
                f"/api/v1/chat/{PERSONA_ID}",
                json={"message": "I want to kill myself", "conversation_id": conv},
                headers=_auth_headers(),
            )
        lines = _trace_lines(caplog.records)
        assert lines, "expected a chat.trace line on the crisis turn"
        assert "action=handoff" in lines[-1]

    def test_kill_switch_refusal_emits_refuse_trace(self, caplog):
        client = _make_client(mode="off")
        with caplog.at_level(logging.INFO, logger="huible.api.app"):
            client.post(
                f"/api/v1/chat/{PERSONA_ID}",
                json={"message": "hi there", "conversation_id": "sess-trace-refuse"},
                # no internal header → treated as a real (grieving) user
                headers={"Authorization": f"Bearer {API_KEY}"},
            )
        lines = _trace_lines(caplog.records)
        assert lines, "expected a chat.trace line on the kill-switch refusal"
        assert "action=refuse" in lines[-1]

    def test_one_trace_line_per_turn(self, caplog):
        client = _make_client()
        conv = "sess-trace-count"
        _consent(client, conv)
        with caplog.at_level(logging.INFO, logger="huible.api.app"):
            for n in range(3):
                client.post(
                    f"/api/v1/chat/{PERSONA_ID}",
                    json={"message": f"turn {n}", "conversation_id": conv},
                    headers=_auth_headers(),
                )
        lines = _trace_lines(caplog.records)
        assert len(lines) == 3, f"expected 3 trace lines, got {len(lines)}"
        # turn_count should advance across the session
        assert "turn_count=1" in lines[0]
        assert "turn_count=3" in lines[-1]
