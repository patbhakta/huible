"""TencentDB-style L0-L3 memory pyramid for the Huible persona engine.

Phase 3 wiring: on client upload, run TencentDB distillation
L0 -> L1 -> L2 -> L3 to consolidated Markdown; the persona responds from that
Markdown (source of truth) instead of raw vectors.  Temporal schema
(``valid_from`` / ``valid_to``) and memory TYPES (durable rule / current
state / observation) are carried in Markdown frontmatter, and every L1-L3
abstraction keeps evidence links back to the raw L0 source.
"""

from huible.distillation.distill import Distiller
from huible.distillation.markdown import (
    parse_frontmatter,
    parse_l0,
    parse_record,
    render_frontmatter,
    render_l0,
    render_l1,
    render_l2,
    render_l3,
)
from huible.distillation.records import (
    DistillationResult,
    EvidenceLink,
    L0Record,
    L1Fact,
    L2Scenario,
    L3Profile,
    MemoryType,
    Tier,
    parse_iso,
    to_iso,
)
from huible.distillation.responder import MarkdownHit, MarkdownPersonaResponder, MarkdownResponse
from huible.distillation.store import MarkdownMemoryStore

__all__ = [
    "DistillationResult",
    "Distiller",
    "EvidenceLink",
    "L0Record",
    "L1Fact",
    "L2Scenario",
    "L3Profile",
    "MarkdownHit",
    "MarkdownMemoryStore",
    "MarkdownPersonaResponder",
    "MarkdownResponse",
    "MemoryType",
    "Tier",
    "parse_frontmatter",
    "parse_iso",
    "parse_l0",
    "parse_record",
    "render_frontmatter",
    "render_l0",
    "render_l1",
    "render_l2",
    "render_l3",
    "to_iso",
]
