"""Persona layer: provenance-safe memory -> prompt bridge and voice assembly.

Phase 2 (M2 "Make It Speak"). The :mod:`huible.persona.context` module is the
only sanctioned bridge between the spreading-activation retrieval output and the
LLM prompt. It enforces the memory-integrity hard rules before any memory
reaches the generator.
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

__all__ = [
    "ConfidenceLevel",
    "ContextBuilder",
    "ConversationTurn",
    "PersonaConfig",
    "PromptContext",
    "RelationshipTier",
    "get_confidence_level",
]
