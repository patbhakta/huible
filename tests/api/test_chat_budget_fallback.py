"""Chat-endpoint budget-fallback tests (HU-1461, board decision 2026-08-18).

When the OpenRouter monthly spend cap is exhausted mid-conversation, the
persona-chat turn must NOT 500: the board-approved degraded posture (fake
voice as rollback) serves the turn, the trace provider label discloses the
fallback, and the turn is metered as ``persona_budget_fallback``.

The hosted client is the real :class:`OpenRouterLLMClient` wired to an
``httpx.MockTransport`` whose first response reports ``usage.cost`` equal to
the entire budget — so the second hosted call is refused by the local wall
before any network traffic.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.real_user_gate import REAL_USER_TRAFFIC_CLASS_HEADER
from huible.api.settings import Settings
from huible.llm.client import LLMConfig, LLMProvider, OpenRouterLLMClient
from tests.api.test_chat_e2e import API_KEY, PERSONA_ID, _persona, _seeded_backend

HOSTED_REPLY = "Oh, the lake. Best mornings of my life."


def _capped_openrouter_client(state_path: Path) -> OpenRouterLLMClient:
    """OpenRouter client whose first call exhausts the $1 test budget."""

    def handler(request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = {
            "id": "chatcmpl-budget-test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": HOSTED_REPLY}}],
            "usage": {"cost": 1.0},
        }
        return httpx.Response(200, json=body)

    config = LLMConfig(
        provider=LLMProvider.OPENROUTER,
        openrouter_api_key="or-test-key",
        openrouter_monthly_budget_usd=1.0,
        openrouter_spend_state_path=str(state_path),
    )
    client = OpenRouterLLMClient(config, transport=httpx.MockTransport(handler))
    client.spend._now_fn = lambda: datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    return client


def _make_client(state_path: Path) -> TestClient:
    settings = Settings(persona_chat_real_user_mode="open")
    backend, _ = _seeded_backend()
    persona = _persona()
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=_capped_openrouter_client(state_path),
        settings=settings,
        start_time=0.0,
    )
    return TestClient(application)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}", REAL_USER_TRAFFIC_CLASS_HEADER: "internal"}


def _consent(client: TestClient, conv: str) -> None:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers=_auth_headers(),
    )
    assert r.status_code == 200, r.text


def _chat(client: TestClient, conv: str, message: str = "Tell me about the lake"):
    return client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": message, "conversation_id": conv},
        headers=_auth_headers(),
    )


def test_budget_exhaustion_serves_fake_voice_not_error(tmp_path: Path) -> None:
    client = _make_client(tmp_path / "spend.json")
    conv = "conv-budget-fallback"
    _consent(client, conv)

    # Turn 1: hosted persona voice; the reported usage.cost equals the whole
    # budget, so the local cap trips for every subsequent hosted call.
    r1 = _chat(client, conv)
    assert r1.status_code == 200, r1.text
    assert r1.json()["response"] == HOSTED_REPLY
    assert r1.json()["trace"]["provider"] == "openrouter"

    # Turn 2: cap refused the hosted call — the turn still succeeds with the
    # deterministic fake voice and an honest provider label.
    r2 = _chat(client, conv)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["response"].startswith("[fake-llm:")
    assert body["trace"]["provider"] == "openrouter->fake(budget)"


def test_budget_fallback_outcome_is_metered(tmp_path: Path) -> None:
    client = _make_client(tmp_path / "spend.json")
    conv = "conv-budget-metrics"
    _consent(client, conv)

    pattern = re.compile(
        r'^huible_chat_turns_total\{outcome="persona_budget_fallback"\}\s+([0-9.eE+-]+)$'
    )

    def _fallback_total() -> float:
        metrics = client.get("/metrics").text
        matches = [pattern.match(line) for line in metrics.splitlines()]
        return sum(float(m.group(1)) for m in matches if m)

    before = _fallback_total()  # counters are process-global; delta-assert
    assert _chat(client, conv).status_code == 200  # hosted turn
    assert _chat(client, conv).status_code == 200  # capped turn
    assert _fallback_total() - before == 1.0, (
        "exactly the capped turn must be metered as persona_budget_fallback"
    )


def test_health_surfaces_llm_budget_string(tmp_path: Path) -> None:
    client = _make_client(tmp_path / "spend.json")
    conv = "conv-budget-health"
    _consent(client, conv)
    _chat(client, conv)  # exhaust the budget
    _chat(client, conv)  # now capped

    checks = client.get("/health").json()["data"]["checks"]
    assert "llm_budget" in checks
    assert checks["llm_budget"].startswith("exhausted")
    assert "1.0000/1.00 USD" in checks["llm_budget"]
