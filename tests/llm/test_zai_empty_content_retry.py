"""HU-2828: zai empty-content responses are retryable — recover, never 500.

Founder directive 2026-09-11 (live occurrence in portal conversation
portal-ab1c710de1af2291, one transient glm-5.3 empty body surfaced as a raw
``[500] engine error``):

- empty content retried x2 (short backoff), success on a later attempt
- still-empty after the retries -> :class:`LLMEmptyContentError` (an
  :class:`LLMError` subclass — every existing ``except LLMError`` posture
  unchanged)
- structurally malformed bodies (missing ``choices``) are NOT retried — a
  different failure class
- every retry logs a structured line carrying the conversation id so
  recurrence is measurable
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from huible.llm.client import (
    LLMConfig,
    LLMEmptyContentError,
    LLMError,
    LLMProvider,
    ZaiLLMClient,
)

PROMPT = "Continue this scene."
SYSTEM = "You are Chandler."


def _config(tmp_path: Any, **overrides: Any) -> LLMConfig:
    defaults: dict[str, Any] = {
        "provider": LLMProvider.ZAI,
        "zai_api_key": "zai-k",
        "zai_token_state_path": str(tmp_path / "zai-tokens.json"),
    }
    defaults.update(overrides)
    return LLMConfig(**defaults)


def _sequencing_transport(payloads: list[dict[str, Any] | Exception]):
    """Transport serving one payload per request (last one repeats)."""
    calls: list[httpx.Request] = []
    state = {"index": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        idx = min(state["index"], len(payloads) - 1)
        state["index"] += 1
        item = payloads[idx]
        if isinstance(item, Exception):
            raise item
        return httpx.Response(200, json=item)

    return httpx.MockTransport(handler), calls


def _ok_payload(content: str) -> dict[str, Any]:
    return {
        "model": "glm-5.3",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _empty_payload() -> dict[str, Any]:
    return {
        "model": "glm-5.3",
        "choices": [{"message": {"role": "assistant", "content": ""}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
    }


async def test_empty_content_retry_recovers_with_normal_reply(tmp_path: Any, caplog: Any) -> None:
    """Two transient empty bodies then a good body -> normal reply, no raise."""
    transport, calls = _sequencing_transport(
        [_empty_payload(), _empty_payload(), _ok_payload("sure")]
    )
    client = ZaiLLMClient(_config(tmp_path, zai_retry_backoff_s=0.0), transport=transport)

    with caplog.at_level(logging.ERROR, logger="huible.llm.client"):
        reply = await client.generate(PROMPT, system_prompt=SYSTEM, conversation_id="conv-retry")

    assert reply == "sure"
    # 1 original + 2 retries, then stop.
    assert len(calls) == 3
    # Every attempt's usage accrued (the provider spends tokens on empties).
    assert client.tokens.day_to_date() == 35
    # Retry events logged with the conversation id (recurrence measurable).
    retry_lines = [r for r in caplog.records if "zai.empty_content_retry" in r.getMessage()]
    assert len(retry_lines) == 2
    assert all("conversation=conv-retry" in r.getMessage() for r in retry_lines)
    assert "attempt=1/3" in retry_lines[0].getMessage()
    assert "attempt=2/3" in retry_lines[1].getMessage()


async def test_persistent_empty_content_raises_after_retries(tmp_path: Any) -> None:
    transport, calls = _sequencing_transport([_empty_payload()])
    client = ZaiLLMClient(_config(tmp_path, zai_retry_backoff_s=0.0), transport=transport)

    with pytest.raises(LLMEmptyContentError, match="returned empty content"):
        await client.generate(PROMPT, system_prompt=SYSTEM)
    assert len(calls) == 3  # 1 + 2 retries
    # The retryable class stays an LLMError subclass: existing posture intact.
    assert issubclass(LLMEmptyContentError, LLMError)


async def test_structural_missing_choices_not_retried(tmp_path: Any) -> None:
    """A malformed body is a different failure class — one attempt only."""
    transport, calls = _sequencing_transport([{"model": "glm-5.3", "choices": []}])
    client = ZaiLLMClient(_config(tmp_path, zai_retry_backoff_s=0.0), transport=transport)

    with pytest.raises(LLMError, match="missing choices"):
        await client.generate(PROMPT, system_prompt=SYSTEM)
    assert len(calls) == 1


async def test_retries_disabled_by_config(tmp_path: Any) -> None:
    transport, calls = _sequencing_transport([_empty_payload()])
    client = ZaiLLMClient(
        _config(tmp_path, zai_empty_content_retries=0), transport=transport
    )

    with pytest.raises(LLMEmptyContentError):
        await client.generate(PROMPT, system_prompt=SYSTEM)
    assert len(calls) == 1


def test_from_env_parses_retry_knobs() -> None:
    cfg = LLMConfig.from_env(
        {"ZAI_EMPTY_CONTENT_RETRIES": "3", "ZAI_RETRY_BACKOFF_S": "0.25"}
    )
    assert cfg.zai_empty_content_retries == 3
    assert cfg.zai_retry_backoff_s == 0.25
    # Invalid values fall back to the safe defaults, never crash.
    cfg = LLMConfig.from_env(
        {"ZAI_EMPTY_CONTENT_RETRIES": "many", "ZAI_RETRY_BACKOFF_S": "soon"}
    )
    assert cfg.zai_empty_content_retries == 2
    assert cfg.zai_retry_backoff_s == 0.5
