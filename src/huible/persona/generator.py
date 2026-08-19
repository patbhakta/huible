"""Persona generator client — the speaking voice (two-tier).

Implements the two-tier separation mandated by
``docs/PERSONA_MODEL_STRATEGY.md``:

* The **generator** (this module) is the model that *is* the persona in
  conversation. Per the strategy doc it must be a self-hosted openweight
  uncensored 7B-24B model served behind an OpenAI-compatible endpoint
  (vLLM / llama.cpp). Closed APIs (Claude / Gemini / GPT) are advisory-only
  and must NEVER be the production voice.
* The advisory / extraction layer lives elsewhere (``huible.advisory``) and
  never speaks as the persona.

This module provides:

* :class:`PersonaGeneratorClient` — the swappable async protocol
  (``async def generate(prompt, **kwargs) -> str``).
* :class:`MockPersonaGeneratorClient` — a deterministic, key-free mock that
  mirrors the ``EMBEDDING_PROVIDER=fake`` pattern. This is the default and the
  only provider that may run without an explicit board hosting decision.
* :class:`OpenAICompatibleGeneratorClient` — a concrete client targeting any
  OpenAI-compatible endpoint (the vLLM / llama.cpp self-hosted openweight
  server). It is the strategy-preferred Option A code path. Activating it as
  the production voice requires the board generator-hosting decision (approval
  linked on HU-1398).
* :func:`make_generator_client` — config-driven factory.

Two-tier firewall: no closed / advisory model is wired as the production voice
by default. The mock is the default provider; a real endpoint is only reached
when explicitly configured.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_GENERATOR_PROVIDER",
    "GeneratorConfig",
    "GeneratorError",
    "GeneratorProvider",
    "HttpTransport",
    "MockPersonaGeneratorClient",
    "OpenAICompatibleGeneratorClient",
    "PersonaGeneratorClient",
    "make_generator_client",
]


class GeneratorProvider(StrEnum):
    """Selectable generator provider.

    ``mock`` is the default and requires no key or host — it mirrors the
    ``EMBEDDING_PROVIDER=fake`` convention so foundation work and the e2e
    pipeline run key-free. ``openai_compatible`` targets any OpenAI-compatible
    endpoint (the strategy-preferred self-hosted openweight server via
    vLLM/llama.cpp). Activating ``openai_compatible`` as the production voice
    requires the board generator-hosting decision.
    """

    MOCK = "mock"
    OPENAI_COMPATIBLE = "openai_compatible"


#: Default provider when none is configured. Deliberately the mock so no real
#: model is ever wired as the voice without explicit configuration plus the
#: board hosting decision.
DEFAULT_GENERATOR_PROVIDER = GeneratorProvider.MOCK


class GeneratorError(RuntimeError):
    """Raised when a concrete generator client fails to produce a response."""


@runtime_checkable
class PersonaGeneratorClient(Protocol):
    """The speaking-voice protocol.

    Implementations take the fully-assembled prompt (as produced by
    :class:`huible.persona.context.ContextBuilder`) and return the persona's
    response text. The protocol is deliberately minimal so the generator can be
    swapped per the two-tier strategy without touching callers.
    """

    async def generate(self, prompt: str, **kwargs: Any) -> str: ...


#: Sync HTTP transport signature used by :class:`OpenAICompatibleGeneratorClient`.
#:
#: ``(url, headers, body, timeout_s) -> response_body_text``. Sync because it
#: wraps ``urllib`` and is awaited via :func:`asyncio.to_thread`; injectable so
#: tests exercise the full request path without a live endpoint.
HttpTransport = Callable[[str, dict[str, str], bytes, float], str]


@dataclass(slots=True)
class GeneratorConfig:
    """Configuration for the generator client.

    Mirrors the ``GENERATOR_*`` env vars (see ``.env.example``). Only
    ``provider`` selects an implementation; the remaining fields are used by the
    real client and ignored by the mock.
    """

    provider: GeneratorProvider = DEFAULT_GENERATOR_PROVIDER
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    max_tokens: int = 512
    temperature: float = 0.7
    request_timeout_s: float = 60.0
    extra: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GeneratorConfig:
        """Build config from ``GENERATOR_*`` environment variables.

        Reads ``GENERATOR_PROVIDER``, ``GENERATOR_BASE_URL``,
        ``GENERATOR_MODEL``, ``GENERATOR_API_KEY``,
        ``GENERATOR_MAX_TOKENS``, ``GENERATOR_TEMPERATURE``, and
        ``GENERATOR_REQUEST_TIMEOUT_S``. Unknown / unparsable provider values
        fall back to the mock default so a misconfiguration can never silently
        wire a real model as the voice.
        """
        env = env if env is not None else os.environ

        raw_provider = (env.get("GENERATOR_PROVIDER") or "").strip().lower()
        if not raw_provider:
            provider = DEFAULT_GENERATOR_PROVIDER
        else:
            try:
                provider = GeneratorProvider(raw_provider)
            except ValueError:
                logger.warning(
                    "Unknown GENERATOR_PROVIDER %r; falling back to %s",
                    raw_provider,
                    DEFAULT_GENERATOR_PROVIDER.value,
                )
                provider = DEFAULT_GENERATOR_PROVIDER

        def _float(name: str, default: float) -> float:
            raw = (env.get(name) or "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                logger.warning("Ignoring non-numeric %s=%r", name, raw)
                return default

        def _int(name: str, default: int) -> int:
            raw = (env.get(name) or "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                logger.warning("Ignoring non-integer %s=%r", name, raw)
                return default

        return cls(
            provider=provider,
            base_url=(env.get("GENERATOR_BASE_URL") or "").strip(),
            model=(env.get("GENERATOR_MODEL") or "").strip(),
            api_key=(env.get("GENERATOR_API_KEY") or "").strip(),
            max_tokens=_int("GENERATOR_MAX_TOKENS", 512),
            temperature=_float("GENERATOR_TEMPERATURE", 0.7),
            request_timeout_s=_float("GENERATOR_REQUEST_TIMEOUT_S", 60.0),
            extra=_extra_from_env(env),
        )


def _extra_from_env(env: Mapping[str, str]) -> dict[str, Any]:
    """Parse ``GENERATOR_EXTRA_JSON`` into top-level payload extras.

    Lets operators forward provider-specific top-level fields (for example
    ``{"reasoning": {"effort": "low"}}`` for reasoning-tuned models served via
    OpenAI-compatible endpoints) without a code change. Invalid JSON or a
    non-object value is a configuration error, raised loudly at startup
    rather than silently ignored mid-conversation.
    """
    raw = (env.get("GENERATOR_EXTRA_JSON") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GENERATOR_EXTRA_JSON is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            "GENERATOR_EXTRA_JSON must be a JSON object of top-level request-body fields"
        )
    return parsed


class MockPersonaGeneratorClient:
    """Deterministic, key-free generator client.

    Mirrors the ``EMBEDDING_PROVIDER=fake`` pattern: no network, no key, fully
    deterministic output derived from the prompt. This is the default provider
    and the only one that may run without the board hosting decision.

    The response is a stable, persona-flavoured digest of the prompt so the e2e
    pipeline and tests can assert on it without a model. Callers needing a fixed
    canned reply can pass ``response`` at construction. Every prompt received is
    recorded on :attr:`calls` for inspection.
    """

    def __init__(self, response: str | None = None, *, persona_name: str = "Persona") -> None:
        self._fixed_response = response
        self._persona_name = persona_name
        self.calls: list[str] = []

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        self.calls.append(prompt)
        if self._fixed_response is not None:
            return self._fixed_response
        return self._deterministic_response(prompt)

    def _deterministic_response(self, prompt: str) -> str:
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
        return f"[mock:{digest}] {self._persona_name} reflects on what you shared."


def _urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> str:
    """Default :data:`HttpTransport`: a stdlib ``urllib`` POST.

    Kept synchronous on purpose — it is awaited via :func:`asyncio.to_thread`
    so the generator layer introduces no third-party HTTP dependency.
    """
    request = Request(url, data=body, headers=headers, method="POST")
    with urlopen(request, timeout=timeout_s) as response:
        return response.read().decode("utf-8")


class OpenAICompatibleGeneratorClient:
    """Concrete generator targeting any OpenAI-compatible endpoint.

    Strategy-preferred Option A: points at a self-hosted openweight generator
    served via vLLM / llama.cpp (both expose the OpenAI-compatible
    ``/chat/completions`` API). The same client can target a board-authorized
    closed-API stand-in for a smoke test ONLY — in every case the production
    swap is a config change, never a code change.

    Uses the standard library (``urllib``) via :func:`asyncio.to_thread` so the
    generator layer adds no third-party HTTP dependency. The HTTP transport is
    injectable (``transport``) so tests exercise the full request-building path
    without a live endpoint.

    Two-tier firewall: this client MUST NOT be the default provider. It is only
    constructed when ``provider=openai_compatible`` is explicitly configured,
    and activating it as the production voice requires the board
    generator-hosting decision (approval linked on HU-1398).
    """

    def __init__(
        self,
        config: GeneratorConfig,
        *,
        transport: HttpTransport | None = None,
    ) -> None:
        if not config.base_url:
            raise ValueError("OpenAI-compatible generator requires GENERATOR_BASE_URL")
        if not config.model:
            raise ValueError("OpenAI-compatible generator requires GENERATOR_MODEL")
        self._config = config
        self._transport: HttpTransport = transport or _urllib_transport

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        payload = self._build_payload(prompt, kwargs)
        url = self._chat_completions_url()
        headers = self._headers()
        body = json.dumps(payload).encode("utf-8")
        timeout = float(kwargs.pop("request_timeout_s", self._config.request_timeout_s))
        try:
            raw = await asyncio.to_thread(self._transport, url, headers, body, timeout)
        except Exception as exc:
            raise GeneratorError(f"Generator request to {url} failed: {exc}") from exc
        return self._extract_content(raw, url)

    def _chat_completions_url(self) -> str:
        return self._config.base_url.rstrip("/") + "/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    def _build_payload(self, prompt: str, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": kwargs.pop("model", self._config.model),
            "messages": self._split_system_block(prompt),
            "temperature": float(kwargs.pop("temperature", self._config.temperature)),
            "max_tokens": int(kwargs.pop("max_tokens", self._config.max_tokens)),
        }
        if self._config.extra:
            payload.update(self._config.extra)
        # Remaining caller overrides are forwarded as top-level API fields.
        payload.update(kwargs)
        return payload

    def _split_system_block(self, prompt: str) -> list[dict[str, str]]:
        """Channel a leading ``SYSTEM:`` block into a real system message.

        The ContextBuilder renders ``SYSTEM: {system_prompt}`` as the first
        paragraph of its flat prompt. Hosted-model firewalls (notably
        OpenRouter's prompt-injection filter, pattern
        ``system_prefix_spoofing``) reject system-style directives embedded in
        user-role content with ``403 Forbidden``. This lifts the leading
        block verbatim into a ``role: system`` message; everything after the
        first blank line stays in the user message byte-for-byte. Prompts
        without the leading marker (or without a following block) are sent
        unchanged as a single user message.
        """
        if not prompt.startswith("SYSTEM: "):
            return [{"role": "user", "content": prompt}]
        system_block, separator, rest = prompt.partition("\n\n")
        if not separator or not rest.strip():
            return [{"role": "user", "content": prompt}]
        return [
            {"role": "system", "content": system_block[len("SYSTEM: ") :]},
            {"role": "user", "content": rest},
        ]

    def _extract_content(self, raw: str, url: str) -> str:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GeneratorError(f"Generator at {url} returned non-JSON body: {exc}") from exc
        try:
            choices = data["choices"]
            content = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GeneratorError(
                f"Generator at {url} response missing choices[0].message.content: {exc}"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            hint = ""
            message = (choices[0] or {}).get("message") or {}
            # Dialect note: zai/glm surfaces hidden reasoning as
            # ``reasoning_content``; other OpenAI-compatible servers use
            # ``reasoning``. Check both so the operator hint is honest.
            if message.get("reasoning") or message.get("reasoning_content"):
                hint = (
                    " (model spent the token budget on hidden reasoning — "
                    "reasoning text present; raise GENERATOR_MAX_TOKENS or "
                    "cap reasoning effort via GENERATOR_EXTRA_JSON)"
                )
            raise GeneratorError(f"Generator at {url} returned empty content{hint}")
        return content


def make_generator_client(
    config: GeneratorConfig | None = None,
    *,
    transport: HttpTransport | None = None,
    **overrides: Any,
) -> PersonaGeneratorClient:
    """Build a generator client from config.

    The mock is returned by default (and whenever provider is unset), so a real
    model is never wired as the voice without explicit configuration. Unknown
    providers raise :class:`ValueError`.

    ``transport`` is only meaningful for the OpenAI-compatible provider and lets
    tests inject a fake transport. ``overrides`` are applied to a copy of the
    config before provider selection.
    """
    effective = config or GeneratorConfig()
    if overrides:
        effective = _with_overrides(effective, overrides)

    if effective.provider is GeneratorProvider.MOCK:
        return MockPersonaGeneratorClient()
    if effective.provider is GeneratorProvider.OPENAI_COMPATIBLE:
        return OpenAICompatibleGeneratorClient(effective, transport=transport)
    raise ValueError(f"Unknown generator provider: {effective.provider!r}")


def _with_overrides(config: GeneratorConfig, overrides: Mapping[str, Any]) -> GeneratorConfig:
    """Return a new :class:`GeneratorConfig` with non-None overrides applied."""
    fields = {
        "provider",
        "base_url",
        "model",
        "api_key",
        "max_tokens",
        "temperature",
        "request_timeout_s",
        "extra",
    }
    updates = {k: v for k, v in overrides.items() if v is not None and k in fields}
    if not updates:
        return config
    current = {f: getattr(config, f) for f in fields}
    current.update(updates)
    return GeneratorConfig(**current)
