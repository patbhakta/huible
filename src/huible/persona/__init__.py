"""Persona layer: provenance-safe memory -> prompt bridge and voice assembly.

Phase 2 (M2 "Make It Speak"). The :mod:`huible.persona.context` module is the
only sanctioned bridge between the spreading-activation retrieval output and the
LLM prompt. It enforces the memory-integrity hard rules before any memory
reaches the generator. :mod:`huible.persona.generator` provides the swappable
speaking-voice client (two-tier: the generator is the voice; closed APIs are
advisory-only and never the production voice).
"""

from huible.persona.context import (
    ConfidenceLevel,
    ContextBuilder,
    ConversationTurn,
    PersonaConfig,
    PromptContext,
    RelationshipTier,
    get_confidence_level,
)
from huible.persona.generator import (
    DEFAULT_GENERATOR_PROVIDER,
    GeneratorConfig,
    GeneratorProvider,
    MockPersonaGeneratorClient,
    OpenAICompatibleGeneratorClient,
    PersonaGeneratorClient,
    make_generator_client,
)

__all__ = [
    "DEFAULT_GENERATOR_PROVIDER",
    "ConfidenceLevel",
    "ContextBuilder",
    "ConversationTurn",
    "GeneratorConfig",
    "GeneratorProvider",
    "MockPersonaGeneratorClient",
    "OpenAICompatibleGeneratorClient",
    "PersonaConfig",
    "PersonaGeneratorClient",
    "PromptContext",
    "RelationshipTier",
    "get_confidence_level",
    "make_generator_client",
]
