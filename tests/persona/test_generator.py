"""Tests for ``huible.persona.generator`` — the speaking-voice client (two-tier).

Covers the HU-1400 acceptance criteria:

* The :class:`PersonaGeneratorClient` protocol is swappable and satisfied by
  both the mock and the OpenAI-compatible client.
* The mock is deterministic and key-free (mirrors ``EMBEDDING_PROVIDER=fake``);
  ``generate()`` returns model text given the assembled prompt.
* No closed / advisory model is wired as the production voice by default — the
  mock is the default provider and a real endpoint is only reached when
  explicitly configured.
* The OpenAI-compatible client builds the correct request, targets the
  self-hosted openweight ``/chat/completions`` endpoint, and extracts the
  response text — exercised via an injected transport with no network.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from huible.persona.generator import (
    DEFAULT_GENERATOR_PROVIDER,
    GeneratorConfig,
    GeneratorError,
    GeneratorProvider,
    MockPersonaGeneratorClient,
    OpenAICompatibleGeneratorClient,
    PersonaGeneratorClient,
    make_generator_client,
)

PROMPT = (
    "SYSTEM: You are embodying Chandler.\n\n"
    "ACTIVATED MEMORIES:\n[NARRATIVE] Chandler loved fishing on Lake Travis.\n\n"
    "CURRENT MESSAGE:\nRemember the lake?\nChandler:"
)


# --- Protocol ---------------------------------------------------------------


def test_mock_satisfies_protocol() -> None:
    assert isinstance(MockPersonaGeneratorClient(), PersonaGeneratorClient)


def test_openai_compatible_satisfies_protocol() -> None:
    client = OpenAICompatibleGeneratorClient(
        GeneratorConfig(
            provider=GeneratorProvider.OPENAI_COMPATIBLE,
            base_url="http://vllm.local:8000/v1",
            model="test-model",
        ),
        transport=_ok_transport("ok"),
    )
    assert isinstance(client, PersonaGeneratorClient)


# --- Mock client ------------------------------------------------------------


async def test_mock_generate_returns_deterministic_text() -> None:
    client = MockPersonaGeneratorClient(persona_name="Chandler")
    first = await client.generate(PROMPT)
    second = await client.generate(PROMPT)

    assert isinstance(first, str)
    assert first == second
    assert "Chandler" in first
    assert first.startswith("[mock:")


async def test_mock_generate_differs_per_prompt() -> None:
    client = MockPersonaGeneratorClient()
    a = await client.generate("first prompt")
    b = await client.generate("second prompt")
    assert a != b


async def test_mock_fixed_response_honored_and_calls_recorded() -> None:
    client = MockPersonaGeneratorClient(response="canned reply")
    assert await client.generate(PROMPT) == "canned reply"
    assert await client.generate("another") == "canned reply"
    assert client.calls == [PROMPT, "another"]


# --- OpenAI-compatible client (no network via injected transport) -----------


def _ok_transport(content: str) -> Any:
    """Build a fake transport returning a chat-completion with ``content``."""

    def transport(url, headers, body, timeout_s):
        payload = json.loads(body.decode("utf-8"))
        transport.last_url = url
        transport.last_headers = headers
        transport.last_payload = payload
        transport.last_timeout = timeout_s
        return json.dumps(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            }
        )

    return transport


def _config() -> GeneratorConfig:
    return GeneratorConfig(
        provider=GeneratorProvider.OPENAI_COMPATIBLE,
        base_url="http://vllm.local:8000/v1/",
        model="openweight-7b",
        api_key="secret-key",
        temperature=0.4,
        max_tokens=128,
    )


async def test_openai_compatible_posts_to_chat_completions_and_parses() -> None:
    transport = _ok_transport("Oh, the lake. Best mornings of my life.")
    client = OpenAICompatibleGeneratorClient(_config(), transport=transport)

    out = await client.generate(PROMPT)

    assert out == "Oh, the lake. Best mornings of my life."
    assert transport.last_url == "http://vllm.local:8000/v1/chat/completions"
    assert transport.last_headers["Authorization"] == "Bearer secret-key"
    assert transport.last_headers["Content-Type"] == "application/json"
    assert transport.last_payload["model"] == "openweight-7b"
    assert transport.last_payload["messages"] == [
        {"role": "system", "content": "You are embodying Chandler."},
        {
            "role": "user",
            "content": (
                "ACTIVATED MEMORIES:\n[NARRATIVE] Chandler loved fishing on Lake Travis.\n\n"
                "CURRENT MESSAGE:\nRemember the lake?\nChandler:"
            ),
        },
    ]
    assert transport.last_payload["temperature"] == 0.4
    assert transport.last_payload["max_tokens"] == 128
    assert transport.last_timeout == 60.0


async def test_openai_compatible_leading_system_block_becomes_system_role() -> None:
    """A ``SYSTEM:``-prefixed prompt is split so hosted firewalls (OpenRouter
    ``system_prefix_spoofing``) do not 403 the turn. Content is preserved
    verbatim; only the role channeling changes."""
    transport = _ok_transport("reply")
    client = OpenAICompatibleGeneratorClient(_config(), transport=transport)

    prompt = (
        "SYSTEM: [REALITY FRAMING — immutable]\n\n"
        "ACTIVATED MEMORIES:\n(none)\n\n"
        "CURRENT MESSAGE:\nHi\nChandler:"
    )
    await client.generate(prompt)

    messages = transport.last_payload["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == "[REALITY FRAMING — immutable]"
    assert "SYSTEM:" not in messages[1]["content"]
    assert messages[1]["content"].startswith("ACTIVATED MEMORIES:")


async def test_openai_compatible_plain_prompt_stays_single_user_message() -> None:
    transport = _ok_transport("reply")
    client = OpenAICompatibleGeneratorClient(_config(), transport=transport)

    await client.generate("No marker here.")

    assert transport.last_payload["messages"] == [
        {"role": "user", "content": "No marker here."}
    ]


async def test_openai_compatible_kwargs_override_config_fields() -> None:
    transport = _ok_transport("reply")
    client = OpenAICompatibleGeneratorClient(_config(), transport=transport)

    await client.generate(PROMPT, temperature=0.1, max_tokens=16, model="other-model")

    assert transport.last_payload["temperature"] == 0.1
    assert transport.last_payload["max_tokens"] == 16
    assert transport.last_payload["model"] == "other-model"


async def test_openai_compatible_no_api_key_omits_auth_header() -> None:
    cfg = GeneratorConfig(
        provider=GeneratorProvider.OPENAI_COMPATIBLE,
        base_url="http://vllm.local:8000/v1",
        model="openweight-7b",
    )
    transport = _ok_transport("hi")
    client = OpenAICompatibleGeneratorClient(cfg, transport=transport)

    await client.generate(PROMPT)

    assert "Authorization" not in transport.last_headers


async def test_openai_compatible_request_failure_raises_generator_error() -> None:
    def boom(url, headers, body, timeout_s):
        raise ConnectionError("refused")

    client = OpenAICompatibleGeneratorClient(_config(), transport=boom)

    with pytest.raises(GeneratorError, match="refused"):
        await client.generate(PROMPT)


async def test_openai_compatible_bad_json_raises_generator_error() -> None:
    client = OpenAICompatibleGeneratorClient(
        _config(), transport=lambda *a: "<html>not json</html>"
    )
    with pytest.raises(GeneratorError, match="non-JSON"):
        await client.generate(PROMPT)


async def test_openai_compatible_missing_choices_raises_generator_error() -> None:
    client = OpenAICompatibleGeneratorClient(
        _config(), transport=lambda *a: json.dumps({"object": "chat.completion"})
    )
    with pytest.raises(GeneratorError, match="missing choices"):
        await client.generate(PROMPT)


async def test_openai_compatible_empty_content_raises_generator_error() -> None:
    payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": "   "}}]})
    client = OpenAICompatibleGeneratorClient(_config(), transport=lambda *a: payload)
    with pytest.raises(GeneratorError, match="empty content"):
        await client.generate(PROMPT)


def test_openai_compatible_requires_base_url_and_model() -> None:
    with pytest.raises(ValueError, match="GENERATOR_BASE_URL"):
        OpenAICompatibleGeneratorClient(
            GeneratorConfig(provider=GeneratorProvider.OPENAI_COMPATIBLE, model="x"),
            transport=lambda *a: "",
        )
    with pytest.raises(ValueError, match="GENERATOR_MODEL"):
        OpenAICompatibleGeneratorClient(
            GeneratorConfig(provider=GeneratorProvider.OPENAI_COMPATIBLE, base_url="http://x/v1"),
            transport=lambda *a: "",
        )


# --- Factory ----------------------------------------------------------------


def test_factory_defaults_to_mock() -> None:
    client = make_generator_client()
    assert isinstance(client, MockPersonaGeneratorClient)


def test_factory_unspecified_provider_is_mock() -> None:
    cfg = GeneratorConfig(base_url="http://x/v1", model="m")
    assert isinstance(make_generator_client(cfg), MockPersonaGeneratorClient)


def test_factory_openai_compatible() -> None:
    cfg = GeneratorConfig(
        provider=GeneratorProvider.OPENAI_COMPATIBLE,
        base_url="http://x/v1",
        model="m",
    )
    client = make_generator_client(cfg, transport=_ok_transport("ok"))
    assert isinstance(client, OpenAICompatibleGeneratorClient)


def test_factory_overrides_select_provider() -> None:
    client = make_generator_client(
        provider=GeneratorProvider.OPENAI_COMPATIBLE,
        base_url="http://x/v1",
        model="m",
        transport=_ok_transport("ok"),
    )
    assert isinstance(client, OpenAICompatibleGeneratorClient)


def test_default_provider_constant_is_mock() -> None:
    assert DEFAULT_GENERATOR_PROVIDER is GeneratorProvider.MOCK


# --- from_env ---------------------------------------------------------------


def test_from_env_defaults_to_mock_when_unset() -> None:
    cfg = GeneratorConfig.from_env({})
    assert cfg.provider is GeneratorProvider.MOCK
    assert cfg.base_url == ""
    assert cfg.model == ""


def test_from_env_reads_all_vars() -> None:
    cfg = GeneratorConfig.from_env(
        {
            "GENERATOR_PROVIDER": "openai_compatible",
            "GENERATOR_BASE_URL": "http://vllm.local:8000/v1",
            "GENERATOR_MODEL": "openweight-7b",
            "GENERATOR_API_KEY": "k",
            "GENERATOR_MAX_TOKENS": "256",
            "GENERATOR_TEMPERATURE": "0.2",
            "GENERATOR_REQUEST_TIMEOUT_S": "15",
        }
    )
    assert cfg.provider is GeneratorProvider.OPENAI_COMPATIBLE
    assert cfg.base_url == "http://vllm.local:8000/v1"
    assert cfg.model == "openweight-7b"
    assert cfg.api_key == "k"
    assert cfg.max_tokens == 256
    assert cfg.temperature == 0.2
    assert cfg.request_timeout_s == 15.0


def test_from_env_unknown_provider_falls_back_to_mock() -> None:
    cfg = GeneratorConfig.from_env({"GENERATOR_PROVIDER": "closed-api-production"})
    assert cfg.provider is GeneratorProvider.MOCK


def test_from_env_garbage_numeric_fields_use_defaults() -> None:
    cfg = GeneratorConfig.from_env(
        {"GENERATOR_MAX_TOKENS": "lots", "GENERATOR_TEMPERATURE": "warm"}
    )
    assert cfg.max_tokens == 512
    assert cfg.temperature == 0.7
