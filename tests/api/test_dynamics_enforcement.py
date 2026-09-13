"""HU-2774 dynamics enforcer — chat-path wiring tests (2026-09-13).

Proves the enforcement pass is wired into ``POST /api/v1/chat/{persona_id}``:
flag off (default) leaves every reply verbatim with a null ``trace.dynamics``;
flag on runs the pass, mutates violating replies, and reports every action on
the trace (decision condition (2): auditable text mutation) and the
``chat.trace`` telemetry line.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.settings import Settings
from huible.llm.client import FakeLLMClient
from tests.api.test_chat import (
    API_KEY,
    OTHER_PERSONA_ID,
    PERSONA_ID,
    _FakeBackend,
    _persona,
)

BAD_REPLY = "ha, no questions from me. do i look like a sitcom writer's room to you?"


def _make_client(*, dynamics: bool) -> tuple[TestClient, FakeLLMClient]:
    backend = _FakeBackend()
    llm = FakeLLMClient(response=BAD_REPLY, persona_name="Chandler")
    persona = _persona(PERSONA_ID)
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore(
        {API_KEY: PERSONA_ID, "key-other": OTHER_PERSONA_ID}, read_env=False
    )
    settings = Settings(dynamics_enforcer_enabled=dynamics)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=llm,
        settings=settings,
        start_time=0.0,
    )
    return TestClient(application), llm


def _consent(client: TestClient, conv: str) -> str:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text
    return conv


def _chat(client: TestClient, message: str, conv: str):
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": message, "conversation_id": conv},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_flag_off_reply_verbatim_and_trace_null():
    client, _llm = _make_client(dynamics=False)
    conv = _consent(client, f"conv-{uuid4()}")
    out = _chat(client, "sobbing over the staplers again lol", conv)
    assert out["response"] == BAD_REPLY
    assert out["trace"]["dynamics"] is None


def test_flag_on_strips_tell_and_reports_trace():
    client, _llm = _make_client(dynamics=True)
    conv = _consent(client, f"conv-{uuid4()}")
    out = _chat(client, "how was your week?", conv)
    dynamics = out["trace"]["dynamics"]
    assert dynamics is not None
    assert "sitcom-meta" in dynamics["fired"]
    assert "mutate:strip_tells" in dynamics["actions"]
    assert "sitcom" not in out["response"].casefold()
    # the persona reply that the conversation store / working memory captured
    # is the ENFORCED text — what the user saw is what memory keeps.
    assert out["response"] != BAD_REPLY


def test_flag_on_history_rules_fire_across_turns():
    client, _llm = _make_client(dynamics=True)
    conv = _consent(client, f"conv-{uuid4()}")
    # turn 1: tell stripped; no echo/deficit rules yet (empty history warmup)
    t1 = _chat(client, "sobbing over the staplers again lol", conv)
    assert "sitcom" not in t1["response"].casefold()
    # turn 2: previous persona reply echoed nothing from the inbound ->
    # this one must hook it.
    t2 = _chat(client, "and the staplers were everywhere, honestly", conv)
    assert t2["trace"]["dynamics"]["fired"], t2["trace"]["dynamics"]
    # turn 3: two question-less persona replies in the window -> a question
    # is mechanically required by now.
    t3 = _chat(client, "okay okay, and then what happened", conv)
    assert t3["response"].rstrip().endswith("?")
    assert (
        "question_deficit" in t3["trace"]["dynamics"]["fired"]
        or "mutate:append_question" in t3["trace"]["dynamics"]["actions"]
    )
