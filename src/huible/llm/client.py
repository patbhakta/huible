"""LLM client abstraction — fake + real (OpenRouter / Gemini) providers.

Provides the runtime generation client used by the persona chat surface and
other features that need a hosted LLM without taking a hard dependency on a
live key. Mirrors the key-free-default convention used across the engine
(``EMBEDDING_PROVIDER=fake``, ``GENERATOR_PROVIDER=mock``): the fake provider
is the default and runs with no network and no key, while the real providers
are only constructed when explicitly selected *and* their key is present.

This module is intentionally distinct from :mod:`huible.persona.generator`.
The generator is the two-tier "speaking voice" (strategy-preferred
self-hosted openweight model behind an OpenAI-compatible endpoint).
This client is the general-purpose hosted-LLM abstraction (OpenRouter /
native Gemini) used for runtime generation and the chat endpoint's hosted
fallback path.

Public surface:

* :class:`LLMClient` — the swappable async protocol
  (``async def generate(prompt, *, system_prompt=None, **kwargs) -> str``).
* :class:`FakeLLMClient` — deterministic, key-free stub (default provider).
* :class:`OpenRouterLLMClient` — calls the OpenRouter chat-completions API
  (default model ``google/gemini-3-flash-preview``, matching the distillation
  CLI). Requires ``OPENROUTER_API_KEY``.
* :class:`GeminiLLMClient` — calls the native Gemini ``generateContent`` REST
  API via ``httpx`` (no extra SDK dependency). Requires ``GEMINI_API_KEY``.
* :class:`ZaiLLMClient` — calls the zai (GLM) OpenAI-compatible
  chat-completions endpoint on the existing coding subscription (default
  model ``glm-5.3``, the verified Chandler voice line). Requires
  ``ZAI_API_KEY`` (falls back to ``GLM_API_KEY``). Carries the day-1
  persona-voice guardrails (HU-1910): a hard per-UTC-day token ceiling plus
  a structured cost log line per conversation turn.
* :class:`LLMConfig` + :func:`build_llm_client` — config-driven factory.

Real providers raise :class:`LLMConfigError` at construction when their key is
absent — a real endpoint is never silently wired, and activation is a pure
env-var change once a key lands.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import httpx

from huible.llm.budget import DailyTokenTracker, MonthlySpendTracker

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_GEMINI_BASE_URL",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_LLM_PROVIDER",
    "DEFAULT_OPENROUTER_BASE_URL",
    "DEFAULT_OPENROUTER_MODEL",
    "DEFAULT_OPENROUTER_MONTHLY_BUDGET_USD",
    "DEFAULT_OPENROUTER_SPEND_STATE_PATH",
    "DEFAULT_ZAI_BASE_URL",
    "DEFAULT_ZAI_DAILY_TOKEN_LIMIT",
    "DEFAULT_ZAI_MODEL",
    "DEFAULT_ZAI_THINKING",
    "DEFAULT_ZAI_TOKEN_STATE_PATH",
    "FakeLLMClient",
    "GeminiLLMClient",
    "LLMBudgetExceededError",
    "LLMClient",
    "LLMConfig",
    "LLMConfigError",
    "LLMDailyTokenLimitExceededError",
    "LLMError",
    "LLMProvider",
    "OpenRouterLLMClient",
    "ZaiLLMClient",
    "build_llm_client",
]


# --- Errors -----------------------------------------------------------------


class LLMError(RuntimeError):
    """Raised when a concrete LLM client fails to produce a response."""


class LLMConfigError(RuntimeError):
    """Raised when a real provider is selected without its required key.

    A missing key is a startup / configuration failure, never a silent fall-back
    to a real endpoint — callers must either set the key or switch providers.
    """


class LLMBudgetExceededError(LLMError):
    """Raised when the OpenRouter monthly spend cap is already exhausted.

    Board decision 2026-08-18 (HU-1774 sweep, item 3): OpenRouter is approved
    with a $50/month hard cap. Once month-to-date accrued spend reaches the
    budget, :class:`OpenRouterLLMClient` refuses to place further calls — the
    cap is enforced *before* the request, so no additional spend is possible.
    Callers should serve the approved degraded posture (fake voice) on this
    error; see the chat handler's budget fallback.
    """


class LLMDailyTokenLimitExceededError(LLMBudgetExceededError):
    """Raised when the zai per-day token ceiling is already exhausted.

    Day-1 persona-voice guardrail (HU-1910 scope item 4): the zai (GLM)
    endpoint runs on an existing subscription, so the wall is a hard
    per-UTC-day token ceiling rather than a USD cap. Once today's accrued
    tokens reach ``ZAI_DAILY_TOKEN_LIMIT``, :class:`ZaiLLMClient` refuses to
    place further calls *before* the network request. Subclasses
    :class:`LLMBudgetExceededError` so the chat handler's existing
    budget-fallback posture (fake voice, never a dropped turn) applies
    unchanged.
    """


# --- Provider enum ----------------------------------------------------------


class LLMProvider(StrEnum):
    """Selectable LLM provider.

    ``fake`` is the default and requires no key or network — it mirrors the
    ``EMBEDDING_PROVIDER=fake`` / ``GENERATOR_PROVIDER=mock`` convention so
    foundation work, the chat endpoint, and the test suite all run key-free.
    ``openrouter``, ``gemini`` and ``zai`` target hosted APIs and require
    their respective keys.
    """

    FAKE = "fake"
    OPENROUTER = "openrouter"
    GEMINI = "gemini"
    ZAI = "zai"


#: Default provider when none is configured. Deliberately ``fake`` so a hosted
#: endpoint is never wired without explicit configuration plus a key.
DEFAULT_LLM_PROVIDER = LLMProvider.FAKE

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
#: Matches the distillation CLI's model choice (``huible.distillation.cli``).
DEFAULT_OPENROUTER_MODEL = "google/gemini-3-flash-preview"
#: Board-approved hard monthly cap (HU-1774 decision sweep 2026-08-18, item 3).
DEFAULT_OPENROUTER_MONTHLY_BUDGET_USD = 50.0
#: Durable spend ledger; deployment bind-mounts a writable volume here.
DEFAULT_OPENROUTER_SPEND_STATE_PATH = "/var/lib/huible/openrouter-spend.json"

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
#: A known-good current native Gemini model id; override via ``LLM_MODEL``.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

#: zai (GLM) OpenAI-compatible coding-endpoint base (HU-1910 day-1 voice).
DEFAULT_ZAI_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
#: The verified Chandler voice line — glm-5.2 test passed 2026-08-10; glm-5.3
#: is the current generation on the same subscription (live-probed 2026-08-19).
DEFAULT_ZAI_MODEL = "glm-5.3"
#: Day-1 guardrail (HU-1910 scope item 4): hard per-UTC-day token ceiling.
#: Sized for Stage-A dogfood volumes (~dozens of conversation turns/day at
#: ~2-4k tokens/turn incl. glm-5.3 reasoning tokens) with wide headroom;
#: ``<= 0`` disables. The subscription itself remains the outer wall.
DEFAULT_ZAI_DAILY_TOKEN_LIMIT = 200_000
#: Durable daily-token ledger; deployment bind-mounts a writable volume here.
DEFAULT_ZAI_TOKEN_STATE_PATH = "/var/lib/huible/zai-tokens.json"
#: glm-5.3 defaults to thinking-on, and its reasoning tokens share the
#: ``max_tokens`` budget — a persona-voice turn can burn the whole budget on
#: reasoning and return empty content. The persona voice needs no chain of
#: thought, so the zai client disables thinking by default; set
#: ``ZAI_THINKING=enabled`` to opt back in.
DEFAULT_ZAI_THINKING = "disabled"


# --- Protocol ---------------------------------------------------------------


@runtime_checkable
class LLMClient(Protocol):
    """The runtime generation protocol.

    Implementations take a user ``prompt`` plus an optional persona
    ``system_prompt`` and return the generated text. The protocol is minimal
    so providers can be swapped (fake → openrouter → gemini) without touching
    callers. Keyword arguments are forwarded to the underlying provider where
    meaningful (``max_tokens``, ``temperature``, ``model`` overrides).
    """

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> str: ...


# --- Config -----------------------------------------------------------------


@dataclass(slots=True)
class LLMConfig:
    """Configuration for the LLM client.

    Mirrors the ``LLM_*`` / ``OPENROUTER_*`` / ``GEMINI_*`` env vars (see
    ``.env.example``). Only ``provider`` selects an implementation; the
    remaining fields are used by the real clients and ignored by the fake.
    """

    provider: LLMProvider = DEFAULT_LLM_PROVIDER
    openrouter_api_key: str = ""
    openrouter_base_url: str = DEFAULT_OPENROUTER_BASE_URL
    openrouter_model: str = DEFAULT_OPENROUTER_MODEL
    openrouter_monthly_budget_usd: float = DEFAULT_OPENROUTER_MONTHLY_BUDGET_USD
    openrouter_spend_state_path: str = DEFAULT_OPENROUTER_SPEND_STATE_PATH
    gemini_api_key: str = ""
    gemini_base_url: str = DEFAULT_GEMINI_BASE_URL
    gemini_model: str = DEFAULT_GEMINI_MODEL
    zai_api_key: str = ""
    zai_base_url: str = DEFAULT_ZAI_BASE_URL
    zai_model: str = DEFAULT_ZAI_MODEL
    zai_daily_token_limit: int = DEFAULT_ZAI_DAILY_TOKEN_LIMIT
    zai_token_state_path: str = DEFAULT_ZAI_TOKEN_STATE_PATH
    zai_thinking: str = DEFAULT_ZAI_THINKING
    max_tokens: int = 512
    temperature: float = 0.7
    request_timeout_s: float = 60.0
    extra: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LLMConfig:
        """Build config from environment variables.

        Reads ``LLM_PROVIDER`` (``fake`` | ``openrouter`` | ``gemini`` |
        ``zai``), ``OPENROUTER_API_KEY`` / ``OPENROUTER_BASE_URL`` /
        ``OPENROUTER_MODEL``, ``OPENROUTER_MONTHLY_BUDGET_USD`` (hard monthly
        cap; ``<= 0`` disables) and ``OPENROUTER_SPEND_STATE_PATH`` (durable
        spend ledger), ``GEMINI_API_KEY`` / ``GEMINI_BASE_URL`` /
        ``GEMINI_MODEL``, ``ZAI_API_KEY`` (with ``GLM_API_KEY`` fallback —
        the existing-subscription credential name) / ``ZAI_BASE_URL`` /
        ``ZAI_MODEL`` / ``ZAI_DAILY_TOKEN_LIMIT`` (hard per-UTC-day token
        ceiling; ``<= 0`` disables) / ``ZAI_TOKEN_STATE_PATH`` (durable
        daily-token ledger), ``LLM_MODEL``, ``LLM_MAX_TOKENS``,
        ``LLM_TEMPERATURE``, and ``LLM_REQUEST_TIMEOUT_S``.

        ``LLM_MODEL`` (when set) overrides *all* real providers' models —
        handy for a single env knob. Provider-specific ``OPENROUTER_MODEL`` /
        ``GEMINI_MODEL`` / ``ZAI_MODEL`` apply only to their provider and are
        overridden by ``LLM_MODEL`` when both are set. Unknown / unparsable
        provider values fall back to the fake default so a misconfiguration
        can never silently wire a hosted endpoint.
        """
        env = env if env is not None else os.environ

        raw_provider = (env.get("LLM_PROVIDER") or "").strip().lower()
        if not raw_provider:
            provider = DEFAULT_LLM_PROVIDER
        else:
            try:
                provider = LLMProvider(raw_provider)
            except ValueError:
                logger.warning(
                    "Unknown LLM_PROVIDER %r; falling back to %s",
                    raw_provider,
                    DEFAULT_LLM_PROVIDER.value,
                )
                provider = DEFAULT_LLM_PROVIDER

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

        openrouter_base = (env.get("OPENROUTER_BASE_URL") or "").strip()
        gemini_base = (env.get("GEMINI_BASE_URL") or "").strip()
        zai_base = (env.get("ZAI_BASE_URL") or "").strip()
        global_model = (env.get("LLM_MODEL") or "").strip()
        openrouter_model = (
            global_model or (env.get("OPENROUTER_MODEL") or "").strip() or DEFAULT_OPENROUTER_MODEL
        )
        gemini_model = (
            global_model or (env.get("GEMINI_MODEL") or "").strip() or DEFAULT_GEMINI_MODEL
        )
        zai_model = global_model or (env.get("ZAI_MODEL") or "").strip() or DEFAULT_ZAI_MODEL
        # ZAI_API_KEY is canonical; GLM_API_KEY is the existing-subscription
        # credential name (the pi-ai adapter's zai route reads it), so it
        # stands in when ZAI_API_KEY is unset.
        zai_api_key = (env.get("ZAI_API_KEY") or "").strip() or (
            env.get("GLM_API_KEY") or ""
        ).strip()

        return cls(
            provider=provider,
            openrouter_api_key=(env.get("OPENROUTER_API_KEY") or "").strip(),
            openrouter_base_url=openrouter_base or DEFAULT_OPENROUTER_BASE_URL,
            openrouter_model=openrouter_model,
            openrouter_monthly_budget_usd=_float(
                "OPENROUTER_MONTHLY_BUDGET_USD", DEFAULT_OPENROUTER_MONTHLY_BUDGET_USD
            ),
            openrouter_spend_state_path=(env.get("OPENROUTER_SPEND_STATE_PATH") or "").strip()
            or DEFAULT_OPENROUTER_SPEND_STATE_PATH,
            gemini_api_key=(env.get("GEMINI_API_KEY") or "").strip(),
            gemini_base_url=gemini_base or DEFAULT_GEMINI_BASE_URL,
            gemini_model=gemini_model,
            zai_api_key=zai_api_key,
            zai_base_url=zai_base or DEFAULT_ZAI_BASE_URL,
            zai_model=zai_model,
            zai_daily_token_limit=_int("ZAI_DAILY_TOKEN_LIMIT", DEFAULT_ZAI_DAILY_TOKEN_LIMIT),
            zai_token_state_path=(env.get("ZAI_TOKEN_STATE_PATH") or "").strip()
            or DEFAULT_ZAI_TOKEN_STATE_PATH,
            zai_thinking=_zai_thinking(env),
            max_tokens=_int("LLM_MAX_TOKENS", 512),
            temperature=_float("LLM_TEMPERATURE", 0.7),
            request_timeout_s=_float("LLM_REQUEST_TIMEOUT_S", 60.0),
        )


# --- Fake provider ----------------------------------------------------------


class FakeLLMClient:
    """Deterministic, key-free LLM client.

    Mirrors the ``EMBEDDING_PROVIDER=fake`` pattern: no network, no key, fully
    deterministic output derived from a stable hash of the system + user prompt.
    This is the default provider and the only one that runs without a hosted
    key.

    The response is a stable, persona-flavoured digest of the prompt so the
    chat pipeline and tests can assert on it without a model. Callers needing a
    fixed canned reply can pass ``response`` at construction. Every prompt
    received (with its system prompt) is recorded on :attr:`calls` for
    inspection.
    """

    def __init__(self, response: str | None = None, *, persona_name: str = "Persona") -> None:
        self._fixed_response = response
        self._persona_name = persona_name
        self.calls: list[tuple[str, str | None]] = []
        # Per-call generation kwargs (e.g. ``max_tokens``), recorded so tests
        # can assert on the reply budget the chat path handed the generator
        # (HU-2231 per-persona caps) without changing :attr:`calls` shape.
        self.kwargs_calls: list[dict[str, Any]] = []
        # Self-describing provider label, surfaced in the chat response trace.
        self.provider: str = LLMProvider.FAKE.value
        # HU-2243 metering: usage of the most recent generate() call, so the
        # chat path can meter every turn without changing the str protocol.
        self.last_usage: dict[str, Any] | None = None

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> str:
        self.calls.append((prompt, system_prompt))
        self.kwargs_calls.append(dict(kwargs))
        if self._fixed_response is not None:
            text = self._fixed_response
        else:
            text = self._deterministic_response(prompt, system_prompt)
        # Estimated usage (~4 chars/token) — the fake voice consumes no
        # hosted resource, but the metering write path still records the
        # turn's shape (HU-2243).
        self.last_usage = {
            "model": "fake",
            "prompt_tokens": _estimate_tokens((system_prompt or "") + "\n" + prompt),
            "completion_tokens": _estimate_tokens(text),
            "total_tokens": _estimate_tokens((system_prompt or "") + "\n" + prompt)
            + _estimate_tokens(text),
        }
        return text

    @staticmethod
    def _deterministic_response(prompt: str, system_prompt: str | None) -> str:
        key = (system_prompt or "") + "\n" + prompt
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
        return f"[fake-llm:{digest}] Deterministic response."


def _estimate_tokens(text: str) -> int:
    """Cheap ~4-chars/token estimate for providers with no usage block."""
    if not text:
        return 0
    return max(1, len(text) // 4)


# --- Shared HTTP helpers ----------------------------------------------------


def _resolve(kwargs: Mapping[str, Any], name: str, default: float | int) -> float | int:
    """Pop a numeric override from kwargs or fall back to the config default."""
    if name in kwargs:
        try:
            value = kwargs[name]
            return int(value) if isinstance(default, int) else float(value)
        except (TypeError, ValueError):
            logger.warning("Ignoring non-numeric override %s=%r", name, value)
    return default


async def _post_json(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: float,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    """Issue an async POST and return the parsed JSON body.

    ``transport`` is normally ``None`` (production, real network). Tests inject
    an :class:`httpx.MockTransport` (or any ``AsyncBaseTransport``) so the full
    request-building path is exercised without a live endpoint.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise LLMError(f"LLM request to {url} failed: {exc}") from exc
    if response.status_code >= 400:
        raise LLMError(
            f"LLM request to {url} returned HTTP {response.status_code}: {response.text[:200]}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise LLMError(f"LLM at {url} returned non-JSON body: {exc}") from exc


# --- OpenRouter provider ----------------------------------------------------


class OpenRouterLLMClient:
    """Concrete LLM client targeting the OpenRouter chat-completions API.

    Uses ``httpx`` and the standard OpenAI-compatible ``/chat/completions``
    request shape. The default model is ``google/gemini-3-flash-preview``
    (matching the distillation CLI) so the hosted path is consistent across
    the engine. The HTTP transport is injectable so tests exercise the full
    request path without a live endpoint.

    Carries the board-approved $50/month hard cap (HU-1774 decision sweep
    2026-08-18, item 3): each successful call accrues its reported
    ``usage.cost`` (USD) into a durable per-month ledger
    (:class:`huible.llm.budget.MonthlySpendTracker`), and once month-to-date
    spend reaches ``openrouter_monthly_budget_usd`` every further
    :meth:`generate` raises :class:`LLMBudgetExceededError` *before* any
    network call — the cap is a wall, not a meter. ``budget <= 0`` disables
    the cap. The :attr:`spend` tracker is exposed for /health surfacing.
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not config.openrouter_api_key:
            raise LLMConfigError(
                "LLM_PROVIDER=openrouter requires OPENROUTER_API_KEY "
                "(set the key or switch to LLM_PROVIDER=fake)."
            )
        self._config = config
        self._transport = transport
        # Self-describing provider label, surfaced in the chat response trace.
        self.provider: str = LLMProvider.OPENROUTER.value
        # HU-2243 metering: usage of the most recent generate() call.
        self.last_usage: dict[str, Any] | None = None
        self.spend = MonthlySpendTracker(
            budget_usd=config.openrouter_monthly_budget_usd,
            state_path=config.openrouter_spend_state_path,
        )

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> str:
        # Optional correlation handle forwarded by the chat handler; never
        # sent to the API (consumed here so it cannot leak into the payload).
        kwargs.pop("conversation_id", None)
        if self.spend.is_exhausted():
            snapshot = self.spend.snapshot()
            logger.error(
                "OpenRouter monthly budget exhausted; refusing hosted call "
                "(cap=%.2f USD, month-to-date=%.6f USD, month=%s). "
                "Serve the approved fake-voice fallback or raise the budget.",
                snapshot["budget_usd"],
                snapshot["month_to_date_usd"],
                snapshot["month"],
            )
            raise LLMBudgetExceededError(
                f"OpenRouter monthly budget of {snapshot['budget_usd']:.2f} USD "
                f"exhausted (month-to-date {snapshot['month_to_date_usd']:.6f} USD)"
            )
        payload = self._build_payload(prompt, system_prompt, kwargs)
        url = self._chat_completions_url()
        headers = self._headers()
        timeout = float(kwargs.pop("request_timeout_s", self._config.request_timeout_s))
        data = await _post_json(
            url=url,
            headers=headers,
            payload=payload,
            timeout_s=timeout,
            transport=self._transport,
        )
        reported_cost = _extract_cost(data)
        self.spend.record_cost(reported_cost)
        # HU-2243 metering: exact token counts + the provider-reported USD
        # cost of this call (``cost_basis='reported'`` in the usage row).
        usage = _extract_usage(data)
        self.last_usage = {
            **usage,
            "model": str(payload.get("model", self._config.openrouter_model)),
            "cost": reported_cost,
        }
        return self._extract_content(data, url)

    def _chat_completions_url(self) -> str:
        return self._config.openrouter_base_url.rstrip("/") + "/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._config.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        if self._config.extra.get("http_referer"):
            headers["HTTP-Referer"] = str(self._config.extra["http_referer"])
        if self._config.extra.get("x_title"):
            headers["X-Title"] = str(self._config.extra["x_title"])
        return headers

    def _build_payload(
        self,
        prompt: str,
        system_prompt: str | None,
        kwargs: Mapping[str, Any],
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": kwargs.pop("model", self._config.openrouter_model),
            "messages": messages,
            "temperature": _resolve(kwargs, "temperature", self._config.temperature),
            "max_tokens": _resolve(kwargs, "max_tokens", self._config.max_tokens),
        }
        if self._config.extra:
            for key, value in self._config.extra.items():
                if key in {"http_referer", "x_title"}:
                    continue
                payload.setdefault(key, value)
        # Remaining caller overrides are forwarded as top-level API fields.
        payload.update(kwargs)
        return payload

    @staticmethod
    def _extract_content(data: Mapping[str, Any], url: str) -> str:
        try:
            choices = data["choices"]
            content = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"LLM at {url} response missing choices[0].message.content: {exc}"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError(f"LLM at {url} returned empty content")
        return content


def _extract_cost(data: Mapping[str, Any]) -> float:
    """Pull the USD cost OpenRouter reports on a paid-key completion.

    OpenRouter includes ``usage.cost`` (USD) on chat completions billed to
    the key. Missing/unusable values return ``0.0`` — the local ledger then
    under-counts, and the console-side key spend limit remains the outer
    wall, so a missing field can never *disable* the cap, only soften local
    metering.
    """
    try:
        cost = data["usage"]["cost"]  # type: ignore[index]
        value = float(cost)
        return value if value > 0 else 0.0
    except (KeyError, TypeError, ValueError):
        return 0.0


# --- Gemini provider (native REST, no SDK) ----------------------------------


class GeminiLLMClient:
    """Concrete LLM client targeting the native Gemini ``generateContent`` API.

    Calls ``generativelanguage.googleapis.com`` directly via ``httpx`` — no
    ``google-generativeai`` SDK dependency, keeping the HTTP surface uniform
    with :class:`OpenRouterLLMClient`. Requires ``GEMINI_API_KEY``. The HTTP
    transport is injectable for tests.
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not config.gemini_api_key:
            raise LLMConfigError(
                "LLM_PROVIDER=gemini requires GEMINI_API_KEY "
                "(set the key or switch to LLM_PROVIDER=fake)."
            )
        self._config = config
        self._transport = transport
        # Self-describing provider label, surfaced in the chat response trace.
        self.provider: str = LLMProvider.GEMINI.value
        # HU-2243 metering: usage of the most recent generate() call.
        self.last_usage: dict[str, Any] | None = None

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> str:
        # Optional correlation handle forwarded by the chat handler; never
        # sent to the API.
        kwargs.pop("conversation_id", None)
        payload = self._build_payload(prompt, system_prompt, kwargs)
        url = self._generate_content_url()
        headers = self._headers()
        timeout = float(kwargs.pop("request_timeout_s", self._config.request_timeout_s))
        data = await _post_json(
            url=url,
            headers=headers,
            payload=payload,
            timeout_s=timeout,
            transport=self._transport,
        )
        # HU-2243 metering: Gemini reports usageMetadata token counts.
        self.last_usage = {
            **_extract_gemini_usage(data),
            "model": self._config.gemini_model,
        }
        return self._extract_text(data, url)

    def _generate_content_url(self) -> str:
        model = self._config.gemini_model
        return self._config.gemini_base_url.rstrip("/") + f"/models/{model}:generateContent"

    def _headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self._config.gemini_api_key,
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        prompt: str,
        system_prompt: str | None,
        kwargs: Mapping[str, Any],
    ) -> dict[str, Any]:
        contents = [{"role": "user", "parts": [{"text": prompt}]}]
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": _resolve(kwargs, "max_tokens", self._config.max_tokens),
                "temperature": _resolve(kwargs, "temperature", self._config.temperature),
            },
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        if self._config.extra:
            for key, value in self._config.extra.items():
                payload.setdefault(key, value)
        return payload

    @staticmethod
    def _extract_text(data: Mapping[str, Any], url: str) -> str:
        try:
            candidates = data["candidates"]
            parts = candidates[0]["content"]["parts"]
            text = parts[0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"LLM at {url} response missing candidates[0].content.parts[0].text: {exc}"
            ) from exc
        if not isinstance(text, str) or not text.strip():
            raise LLMError(f"LLM at {url} returned empty content")
        return text


# --- zai provider (existing GLM subscription — day-1 persona voice) ---------


class ZaiLLMClient:
    """Concrete LLM client targeting the zai (GLM) coding endpoint.

    Day-1 board-approved persona voice (HU-1910 executing HU-1461; approval
    granted by Pat 2026-08-19). Calls the OpenAI-compatible
    ``/chat/completions`` route on the existing coding subscription via
    ``httpx`` — no new spend, no SDK. The default model is ``glm-5.3`` (the
    verified Chandler voice line: glm-5.2 test passed 2026-08-10, glm-5.3
    live-probed 2026-08-19). Key resolution: ``ZAI_API_KEY``, falling back
    to ``GLM_API_KEY`` (the subscription's credential name). The HTTP
    transport is injectable so tests exercise the full request path without
    a live endpoint.

    Guardrails (HU-1910 scope item 4):

    * **Hard per-UTC-day token ceiling** — every successful call accrues its
      reported ``usage.total_tokens`` (incl. glm reasoning tokens) into a
      durable per-day ledger (:class:`huible.llm.budget.DailyTokenTracker`),
      and once today's accrual reaches ``zai_daily_token_limit`` every
      further :meth:`generate` raises
      :class:`LLMDailyTokenLimitExceededError` *before* any network call.
      ``limit <= 0`` disables the ceiling. The :attr:`tokens` tracker is
      exposed for /health surfacing.
    * **Cost log line per conversation** — each successful call emits one
      structured ``zai.usage`` INFO line carrying the conversation id,
      tokens in/out, day-to-date accrual, ceiling and cost basis
      (subscription → $0 incremental metered spend).
    * **One-knob abort** — ``LLM_PROVIDER=fake`` returns the deterministic
      key-free client with no code change.
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not config.zai_api_key:
            raise LLMConfigError(
                "LLM_PROVIDER=zai requires ZAI_API_KEY (or GLM_API_KEY) "
                "(set the key or switch to LLM_PROVIDER=fake)."
            )
        self._config = config
        self._transport = transport
        # Self-describing provider label, surfaced in the chat response trace.
        self.provider: str = LLMProvider.ZAI.value
        # HU-2243 metering: usage of the most recent generate() call.
        self.last_usage: dict[str, Any] | None = None
        self.tokens = DailyTokenTracker(
            limit_tokens=config.zai_daily_token_limit,
            state_path=config.zai_token_state_path,
        )

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> str:
        # Optional correlation handle forwarded by the chat handler; used for
        # the per-conversation cost log line, never sent to the API.
        conversation_id = str(kwargs.pop("conversation_id", "") or "")
        if self.tokens.is_exhausted():
            snapshot = self.tokens.snapshot()
            logger.error(
                "zai daily token ceiling reached; refusing hosted call "
                "(limit=%d tokens, day-to-date=%d tokens, day=%s). "
                "Serve the approved fake-voice fallback or raise the ceiling.",
                snapshot["limit_tokens"],
                snapshot["day_to_date_tokens"],
                snapshot["day"],
            )
            raise LLMDailyTokenLimitExceededError(
                f"zai daily token ceiling of {snapshot['limit_tokens']} reached "
                f"(day-to-date {snapshot['day_to_date_tokens']} tokens)"
            )
        payload = self._build_payload(prompt, system_prompt, kwargs)
        url = self._chat_completions_url()
        headers = self._headers()
        timeout = float(kwargs.pop("request_timeout_s", self._config.request_timeout_s))
        data = await _post_json(
            url=url,
            headers=headers,
            payload=payload,
            timeout_s=timeout,
            transport=self._transport,
        )
        usage = _extract_usage(data)
        self.tokens.record_tokens(usage["total_tokens"])
        # HU-2243 metering: exact in/out counts from the usage block; the
        # subscription bills quota not tokens, so the usage row carries a
        # *modeled* cost at reference rates (cost_basis='modeled').
        self.last_usage = {
            **usage,
            "model": str(payload.get("model", self._config.zai_model)),
        }
        snapshot = self.tokens.snapshot()
        logger.info(
            "zai.usage conversation=%s model=%s tokens_in=%d tokens_out=%d "
            "day_to_date_tokens=%d daily_limit=%d cost_basis=subscription "
            "incremental_cost_usd=0.00",
            conversation_id or "-",
            payload.get("model", self._config.zai_model),
            usage["prompt_tokens"],
            usage["completion_tokens"],
            snapshot["day_to_date_tokens"],
            snapshot["limit_tokens"],
        )
        return self._extract_content(data, url)

    def _chat_completions_url(self) -> str:
        return self._config.zai_base_url.rstrip("/") + "/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.zai_api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        prompt: str,
        system_prompt: str | None,
        kwargs: Mapping[str, Any],
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": kwargs.pop("model", self._config.zai_model),
            "messages": messages,
            "temperature": _resolve(kwargs, "temperature", self._config.temperature),
            "max_tokens": _resolve(kwargs, "max_tokens", self._config.max_tokens),
        }
        # Provider dialect (see DEFAULT_ZAI_THINKING): glm thinking shares the
        # max_tokens budget; the persona voice opts out by default. setdefault
        # keeps an explicit config.extra / caller override authoritative.
        if self._config.zai_thinking in {"enabled", "disabled"}:
            payload.setdefault("thinking", {"type": self._config.zai_thinking})
        if self._config.extra:
            for key, value in self._config.extra.items():
                payload.setdefault(key, value)
        # Remaining caller overrides are forwarded as top-level API fields.
        payload.update(kwargs)
        return payload

    @staticmethod
    def _extract_content(data: Mapping[str, Any], url: str) -> str:
        try:
            choices = data["choices"]
            content = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"LLM at {url} response missing choices[0].message.content: {exc}"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError(f"LLM at {url} returned empty content")
        return content


def _zai_thinking(env: Mapping[str, str]) -> str:
    """Parse ``ZAI_THINKING`` (``enabled`` | ``disabled``).

    Invalid values warn and fall back to the persona-voice-safe default
    (``disabled``) so a typo can never silently resurrect empty-content
    turns from reasoning-token burn.
    """
    raw = (env.get("ZAI_THINKING") or "").strip().lower()
    if not raw:
        return DEFAULT_ZAI_THINKING
    if raw in {"enabled", "disabled"}:
        return raw
    logger.warning("Unknown ZAI_THINKING %r; using %s", raw, DEFAULT_ZAI_THINKING)
    return DEFAULT_ZAI_THINKING


def _extract_usage(data: Mapping[str, Any]) -> dict[str, int]:
    """Pull token counts from an OpenAI-compatible ``usage`` block.

    glm-5.3 reports ``prompt_tokens`` / ``completion_tokens`` /
    ``total_tokens`` (reasoning tokens included in completion). Missing or
    unusable values count as ``0`` — the daily ledger then under-counts,
    and the subscription-side limit remains the outer wall.
    """
    usage = data.get("usage")
    if not isinstance(usage, Mapping):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _count(name: str) -> int:
        try:
            value = int(usage[name])  # type: ignore[index]
            return value if value > 0 else 0
        except (KeyError, TypeError, ValueError):
            return 0

    return {
        "prompt_tokens": _count("prompt_tokens"),
        "completion_tokens": _count("completion_tokens"),
        "total_tokens": _count("total_tokens"),
    }


def _extract_gemini_usage(data: Mapping[str, Any]) -> dict[str, int]:
    """Pull token counts from a Gemini ``usageMetadata`` block.

    ``promptTokenCount`` / ``candidatesTokenCount`` /
    ``totalTokenCount``; missing or unusable values count as ``0``
    (same under-count posture as the OpenAI-compatible extractor).
    """
    usage = data.get("usageMetadata")
    if not isinstance(usage, Mapping):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _count(name: str) -> int:
        try:
            value = int(usage[name])  # type: ignore[index]
            return value if value > 0 else 0
        except (KeyError, TypeError, ValueError):
            return 0

    return {
        "prompt_tokens": _count("promptTokenCount"),
        "completion_tokens": _count("candidatesTokenCount"),
        "total_tokens": _count("totalTokenCount"),
    }


# --- Factory ----------------------------------------------------------------


def build_llm_client(
    config: LLMConfig | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    **overrides: Any,
) -> LLMClient:
    """Build an LLM client from config.

    The fake provider is returned by default (and whenever ``LLM_PROVIDER`` is
    unset or unknown), so a hosted endpoint is never wired without explicit
    configuration. Real providers raise :class:`LLMConfigError` at construction
    when their key is absent — there is no silent fall-back to a live call.

    ``transport`` is only meaningful for the real providers and lets tests
    inject an :class:`httpx.MockTransport`. ``overrides`` are applied to a copy
    of the config before provider selection.
    """
    effective = config or LLMConfig.from_env()
    if overrides:
        effective = _with_overrides(effective, overrides)

    if effective.provider is LLMProvider.FAKE:
        return FakeLLMClient()
    if effective.provider is LLMProvider.OPENROUTER:
        return OpenRouterLLMClient(effective, transport=transport)
    if effective.provider is LLMProvider.GEMINI:
        return GeminiLLMClient(effective, transport=transport)
    if effective.provider is LLMProvider.ZAI:
        return ZaiLLMClient(effective, transport=transport)
    raise ValueError(f"Unknown LLM provider: {effective.provider!r}")


_LLM_CONFIG_FIELDS: frozenset[str] = frozenset(
    {
        "provider",
        "openrouter_api_key",
        "openrouter_base_url",
        "openrouter_model",
        "openrouter_monthly_budget_usd",
        "openrouter_spend_state_path",
        "gemini_api_key",
        "gemini_base_url",
        "gemini_model",
        "zai_api_key",
        "zai_base_url",
        "zai_model",
        "zai_daily_token_limit",
        "zai_token_state_path",
        "zai_thinking",
        "max_tokens",
        "temperature",
        "request_timeout_s",
        "extra",
    }
)


def _with_overrides(config: LLMConfig, overrides: Mapping[str, Any]) -> LLMConfig:
    """Return a new :class:`LLMConfig` with non-None overrides applied."""
    updates = {k: v for k, v in overrides.items() if v is not None and k in _LLM_CONFIG_FIELDS}
    if not updates:
        return config
    current = {f: getattr(config, f) for f in _LLM_CONFIG_FIELDS}
    current.update(updates)
    return LLMConfig(**current)
