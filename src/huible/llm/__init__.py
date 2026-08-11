"""LLM client abstraction — fake + real (OpenRouter / Gemini) providers.

See :mod:`huible.llm.client` for the protocol, providers, and factory.
"""

from huible.llm.client import (
    DEFAULT_GEMINI_BASE_URL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OPENROUTER_BASE_URL,
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

__all__ = [
    "DEFAULT_GEMINI_BASE_URL",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_LLM_PROVIDER",
    "DEFAULT_OPENROUTER_BASE_URL",
    "DEFAULT_OPENROUTER_MODEL",
    "FakeLLMClient",
    "GeminiLLMClient",
    "LLMClient",
    "LLMConfig",
    "LLMConfigError",
    "LLMError",
    "LLMProvider",
    "OpenRouterLLMClient",
    "build_llm_client",
]
