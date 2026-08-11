"""Tests for ``huible.llm.client`` — the runtime LLM abstraction (HU-1405).

Acceptance coverage:

* :class:`LLMClient` protocol is satisfied by the fake and both real clients.
* :class:`FakeLLMClient` is deterministic and key-free (mirrors
  ``EMBEDDING_PROVIDER=fake``); ``generate()`` returns stable text.
* :func:`build_llm_client` factory selection — defaults to fake, real providers
  only when explicitly selected, unknown provider falls back to fake.
* Real providers raise :class:`LLMConfigError` when their key is absent — a
  hosted endpoint is never silently wired.
* :class:`OpenRouterLLMClient` issues the expected request shape (model,
  messages incl. persona system prompt, auth header) and parses the response —
  exercised via :class:`httpx.MockTransport` with no network.
* :class:`GeminiLLMClient` issues the native ``generateContent`` request and
  parses the response — also via :class:`httpx.MockTransport`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from huible.llm.client import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OPENROUTER_MODEL,
    FakeLLMClient,
    GeminiLLMClient,
    LLMClient,
    LLMConfig,
    LLMConfigError,
    LLMError,
    LLMProvider,
    OpenRouterLLMClient,
    build_llm_client,
)

PROMPT = "Remember the lake?"
SYSTEM = "You are embodying Chandler, a warm Texas storyteller."


# --- Protocol ---------------------------------------------------------------


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeLLMClient(), LLMClient)


def test_openrouter_satisfies_protocol() -> None:
    client = OpenRouterLLMClient(
        LLMConfig(provider=LLMProvider.OPENROUTER, openrouter_api_key="k"),
        transport=_noop_transport(),
    )
    assert isinstance(client, LLMClient)


def test_gemini_satisfies_protocol() -> None:
    client = GeminiLLMClient(
        LLMConfig(provider=LLMProvider.GEMINI, gemini_api_key="k"),
        transport=_noop_transport(),
    )
    assert isinstance(client, LLMClient)


# --- FakeLLMClient ----------------------------------------------------------


async def test_fake_generate_returns_deterministic_text() -> None:
    client = FakeLLMClient()
    first = await client.generate(PROMPT, system_prompt=SYSTEM)
    second = await client.generate(PROMPT, system_prompt=SYSTEM)

    assert isinstance(first, str)
    assert first == second
    assert first.startswith("[fake-llm:")


async def test_fake_generate_differs_per_prompt() -> None:
    client = FakeLLMClient()
    a = await client.generate("first prompt")
    b = await client.generate("second prompt")
    assert a != b


async def test_fake_generate_differs_per_system_prompt() -> None:
    """Determinism is seeded by the system + user prompt together."""
    client = FakeLLMClient()
    a = await client.generate(PROMPT, system_prompt="You are Chandler.")
    b = await client.generate(PROMPT, system_prompt="You are Ross.")
    assert a != b


async def test_fake_fixed_response_honored_and_calls_recorded() -> None:
    client = FakeLLMClient(response="canned reply")
    assert await client.generate(PROMPT, system_prompt=SYSTEM) == "canned reply"
    assert await client.generate("another") == "canned reply"
    assert client.calls == [(PROMPT, SYSTEM), ("another", None)]


def test_default_provider_constant_is_fake() -> None:
    assert DEFAULT_LLM_PROVIDER is LLMProvider.FAKE


# --- OpenRouter client (no network via MockTransport) -----------------------


class _Capture:
    """Holds the last ``httpx.Request`` + decoded payload seen by a MockTransport.

    ``httpx.MockTransport`` wraps the handler, so closure-local attributes are
    not reachable from the outside — this holder is captured by the handler
    closure and inspected by the test.
    """

    request: httpx.Request | None = None
    payload: Any = None


def _openrouter_transport(content: str) -> tuple[_Capture, httpx.MockTransport]:
    """MockTransport returning a chat completion with ``content``; captures the request."""

    cap = _Capture()

    def handler(request: httpx.Request) -> httpx.Response:
        cap.request = request
        cap.payload = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            },
        )

    return cap, httpx.MockTransport(handler)


def _noop_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": []}))


def _openrouter_config(**overrides: Any) -> LLMConfig:
    base: dict[str, Any] = {
        "provider": LLMProvider.OPENROUTER,
        "openrouter_api_key": "or-secret",
        "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_model": "google/gemini-3-flash-preview",
        "temperature": 0.4,
        "max_tokens": 128,
    }
    base.update(overrides)
    return LLMConfig(**base)


async def test_openrouter_posts_to_chat_completions_and_parses() -> None:
    cap, transport = _openrouter_transport("Oh, the lake. Best mornings of my life.")
    client = OpenRouterLLMClient(_openrouter_config(), transport=transport)

    out = await client.generate(PROMPT, system_prompt=SYSTEM)

    assert out == "Oh, the lake. Best mornings of my life."
    assert cap.request is not None
    assert str(cap.request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert cap.request.headers["Authorization"] == "Bearer or-secret"
    assert cap.request.headers["Content-Type"] == "application/json"
    payload = cap.payload
    assert payload["model"] == "google/gemini-3-flash-preview"
    assert payload["messages"] == [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": PROMPT},
    ]
    assert payload["temperature"] == 0.4
    assert payload["max_tokens"] == 128


async def test_openrouter_omits_system_message_when_no_system_prompt() -> None:
    cap, transport = _openrouter_transport("reply")
    client = OpenRouterLLMClient(_openrouter_config(), transport=transport)

    await client.generate(PROMPT)

    assert cap.payload["messages"] == [{"role": "user", "content": PROMPT}]


async def test_openrouter_kwargs_override_config_fields() -> None:
    cap, transport = _openrouter_transport("reply")
    client = OpenRouterLLMClient(_openrouter_config(), transport=transport)

    await client.generate(
        PROMPT, system_prompt=SYSTEM, temperature=0.1, max_tokens=16, model="other"
    )

    payload = cap.payload
    assert payload["temperature"] == 0.1
    assert payload["max_tokens"] == 16
    assert payload["model"] == "other"


async def test_openrouter_http_error_raises_llm_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text="unauthorized"))
    client = OpenRouterLLMClient(_openrouter_config(), transport=transport)

    with pytest.raises(LLMError, match="HTTP 401"):
        await client.generate(PROMPT, system_prompt=SYSTEM)


async def test_openrouter_network_failure_raises_llm_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = OpenRouterLLMClient(_openrouter_config(), transport=httpx.MockTransport(boom))

    with pytest.raises(LLMError, match="refused"):
        await client.generate(PROMPT, system_prompt=SYSTEM)


async def test_openrouter_missing_choices_raises_llm_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"object": "chat.completion"})
    )
    client = OpenRouterLLMClient(_openrouter_config(), transport=transport)

    with pytest.raises(LLMError, match="missing choices"):
        await client.generate(PROMPT, system_prompt=SYSTEM)


async def test_openrouter_empty_content_raises_llm_error() -> None:
    payload = {"choices": [{"message": {"role": "assistant", "content": "   "}}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    client = OpenRouterLLMClient(_openrouter_config(), transport=transport)

    with pytest.raises(LLMError, match="empty content"):
        await client.generate(PROMPT, system_prompt=SYSTEM)


def test_openrouter_requires_api_key() -> None:
    with pytest.raises(LLMConfigError, match="OPENROUTER_API_KEY"):
        OpenRouterLLMClient(
            LLMConfig(provider=LLMProvider.OPENROUTER, openrouter_api_key=""),
            transport=_noop_transport(),
        )


# --- Gemini client (no network via MockTransport) ---------------------------


def _gemini_transport(text: str) -> tuple[_Capture, httpx.MockTransport]:
    cap = _Capture()

    def handler(request: httpx.Request) -> httpx.Response:
        cap.request = request
        cap.payload = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": text}],
                        }
                    }
                ]
            },
        )

    return cap, httpx.MockTransport(handler)


def _gemini_config(**overrides: Any) -> LLMConfig:
    base: dict[str, Any] = {
        "provider": LLMProvider.GEMINI,
        "gemini_api_key": "gem-secret",
        "gemini_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "gemini_model": DEFAULT_GEMINI_MODEL,
        "temperature": 0.4,
        "max_tokens": 128,
    }
    base.update(overrides)
    return LLMConfig(**base)


async def test_gemini_posts_to_generate_content_and_parses() -> None:
    cap, transport = _gemini_transport("Oh, the lake. Best mornings of my life.")
    client = GeminiLLMClient(_gemini_config(), transport=transport)

    out = await client.generate(PROMPT, system_prompt=SYSTEM)

    assert out == "Oh, the lake. Best mornings of my life."
    assert cap.request is not None
    assert (
        str(cap.request.url)
        == f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_GEMINI_MODEL}:generateContent"
    )
    assert cap.request.headers["x-goog-api-key"] == "gem-secret"
    assert cap.request.headers["Content-Type"] == "application/json"
    payload = cap.payload
    assert payload["contents"] == [{"role": "user", "parts": [{"text": PROMPT}]}]
    assert payload["systemInstruction"] == {"parts": [{"text": SYSTEM}]}
    assert payload["generationConfig"]["maxOutputTokens"] == 128
    assert payload["generationConfig"]["temperature"] == 0.4


async def test_gemini_omits_system_instruction_when_no_system_prompt() -> None:
    cap, transport = _gemini_transport("reply")
    client = GeminiLLMClient(_gemini_config(), transport=transport)

    await client.generate(PROMPT)

    assert "systemInstruction" not in cap.payload


async def test_gemini_kwargs_override_config_fields() -> None:
    cap, transport = _gemini_transport("reply")
    client = GeminiLLMClient(_gemini_config(), transport=transport)

    await client.generate(PROMPT, temperature=0.1, max_tokens=16)

    assert cap.payload["generationConfig"]["temperature"] == 0.1
    assert cap.payload["generationConfig"]["maxOutputTokens"] == 16


async def test_gemini_http_error_raises_llm_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(403, text="forbidden"))
    client = GeminiLLMClient(_gemini_config(), transport=transport)

    with pytest.raises(LLMError, match="HTTP 403"):
        await client.generate(PROMPT, system_prompt=SYSTEM)


async def test_gemini_missing_candidates_raises_llm_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"error": "x"}))
    client = GeminiLLMClient(_gemini_config(), transport=transport)

    with pytest.raises(LLMError, match="missing candidates"):
        await client.generate(PROMPT, system_prompt=SYSTEM)


def test_gemini_requires_api_key() -> None:
    with pytest.raises(LLMConfigError, match="GEMINI_API_KEY"):
        GeminiLLMClient(
            LLMConfig(provider=LLMProvider.GEMINI, gemini_api_key=""),
            transport=_noop_transport(),
        )


# --- Factory ----------------------------------------------------------------


def test_factory_defaults_to_fake() -> None:
    assert isinstance(build_llm_client(), FakeLLMClient)


def test_factory_unspecified_provider_is_fake() -> None:
    cfg = LLMConfig(openrouter_api_key="k")
    assert isinstance(build_llm_client(cfg), FakeLLMClient)


def test_factory_openrouter() -> None:
    cfg = LLMConfig(provider=LLMProvider.OPENROUTER, openrouter_api_key="k")
    client = build_llm_client(cfg, transport=_noop_transport())
    assert isinstance(client, OpenRouterLLMClient)


def test_factory_gemini() -> None:
    cfg = LLMConfig(provider=LLMProvider.GEMINI, gemini_api_key="k")
    client = build_llm_client(cfg, transport=_noop_transport())
    assert isinstance(client, GeminiLLMClient)


def test_factory_overrides_select_provider() -> None:
    client = build_llm_client(
        provider=LLMProvider.OPENROUTER,
        openrouter_api_key="k",
        transport=_noop_transport(),
    )
    assert isinstance(client, OpenRouterLLMClient)


def test_factory_openrouter_missing_key_raises_config_error() -> None:
    cfg = LLMConfig(provider=LLMProvider.OPENROUTER, openrouter_api_key="")
    with pytest.raises(LLMConfigError, match="OPENROUTER_API_KEY"):
        build_llm_client(cfg)


def test_factory_gemini_missing_key_raises_config_error() -> None:
    cfg = LLMConfig(provider=LLMProvider.GEMINI, gemini_api_key="")
    with pytest.raises(LLMConfigError, match="GEMINI_API_KEY"):
        build_llm_client(cfg)


# --- from_env ---------------------------------------------------------------


def test_from_env_defaults_to_fake_when_unset() -> None:
    cfg = LLMConfig.from_env({})
    assert cfg.provider is LLMProvider.FAKE
    assert cfg.openrouter_model == DEFAULT_OPENROUTER_MODEL
    assert cfg.gemini_model == DEFAULT_GEMINI_MODEL


def test_from_env_reads_all_vars() -> None:
    cfg = LLMConfig.from_env(
        {
            "LLM_PROVIDER": "openrouter",
            "OPENROUTER_API_KEY": "or-k",
            "OPENROUTER_BASE_URL": "https://or.example/api/v1",
            "OPENROUTER_MODEL": "openrouter/custom-model",
            "GEMINI_API_KEY": "gem-k",
            "GEMINI_BASE_URL": "https://gem.example/v1beta",
            "GEMINI_MODEL": "gemini-custom",
            "LLM_MAX_TOKENS": "256",
            "LLM_TEMPERATURE": "0.2",
            "LLM_REQUEST_TIMEOUT_S": "15",
        }
    )
    assert cfg.provider is LLMProvider.OPENROUTER
    assert cfg.openrouter_api_key == "or-k"
    assert cfg.openrouter_base_url == "https://or.example/api/v1"
    assert cfg.openrouter_model == "openrouter/custom-model"
    assert cfg.gemini_api_key == "gem-k"
    assert cfg.gemini_base_url == "https://gem.example/v1beta"
    assert cfg.gemini_model == "gemini-custom"
    assert cfg.max_tokens == 256
    assert cfg.temperature == 0.2
    assert cfg.request_timeout_s == 15.0


def test_from_env_llm_model_overrides_both_providers() -> None:
    cfg = LLMConfig.from_env(
        {
            "OPENROUTER_MODEL": "openrouter/specific",
            "GEMINI_MODEL": "gemini-specific",
            "LLM_MODEL": "global/override",
        }
    )
    assert cfg.openrouter_model == "global/override"
    assert cfg.gemini_model == "global/override"


def test_from_env_unknown_provider_falls_back_to_fake() -> None:
    cfg = LLMConfig.from_env({"LLM_PROVIDER": "claude-magic"})
    assert cfg.provider is LLMProvider.FAKE


def test_from_env_garbage_numeric_fields_use_defaults() -> None:
    cfg = LLMConfig.from_env({"LLM_MAX_TOKENS": "lots", "LLM_TEMPERATURE": "warm"})
    assert cfg.max_tokens == 512
    assert cfg.temperature == 0.7


# --- Settings bridge --------------------------------------------------------


def test_settings_to_llm_config_defaults_to_fake() -> None:
    from huible.api.settings import Settings

    cfg = Settings(llm_provider="fake").to_llm_config()
    assert cfg.provider is LLMProvider.FAKE


def test_settings_to_llm_config_passes_through_real_provider() -> None:
    from huible.api.settings import Settings

    settings = Settings(
        llm_provider="openrouter",
        openrouter_api_key="or-k",
        openrouter_model="openrouter/custom",
        gemini_api_key="gem-k",
        gemini_model="gemini-custom",
    )
    cfg = settings.to_llm_config()
    assert cfg.provider is LLMProvider.OPENROUTER
    assert cfg.openrouter_api_key == "or-k"
    assert cfg.openrouter_model == "openrouter/custom"
    # gemini fields flow through independently of the selected provider.
    assert cfg.gemini_api_key == "gem-k"
    assert cfg.gemini_model == "gemini-custom"


def test_settings_llm_model_override_wins() -> None:
    from huible.api.settings import Settings

    settings = Settings(
        openrouter_model="openrouter/custom",
        gemini_model="gemini-custom",
        llm_model="global/override",
    )
    cfg = settings.to_llm_config()
    assert cfg.openrouter_model == "global/override"
    assert cfg.gemini_model == "global/override"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
