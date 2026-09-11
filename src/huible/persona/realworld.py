"""HU-2828 real-world search tool — lane-routed external lookups (SearXNG).

The persona engine's tool layer gains an *external* lane: real-world lookup
through self-hosted SearXNG (founder decision 2026-09-11 — no new vendor;
SearXNG's JSON API is replaceable behind this module's single function).
Unlike every lane in :mod:`huible.persona.tools` (deterministic, local,
era-gated vault reads), this lane reaches the live web, so it is gated twice:

1. **Lane routing (per-persona permission).** A persona-scoped
   :class:`PersonaKnowledgeProfile` — provisioned in ``PersonaConfig.metadata``
   under ``search_lanes``, derived from *dialog evidence* by
   :func:`derive_search_lanes` (never hard-coded per character) — decides
   which question classes may fire the tool:

   - ``current_reality``: questions about the persona's own present world
     (rent where he lives, local prices/places, current scores, weather).
     Chandler: ON — his corpus is dense with rent/apartment/neighborhood/
     sports evidence. Fires the search tool.
   - ``academic``: scholarly/encyclopedic topics (the Mayans, Markov models).
     Chandler: OFF — honest ignorance is the *correct* answer there (the M-0
     competency wall + vault-only posture survive); Ross (paleontologist)
     would provision ON — the same question class resolves differently per
     persona.

   Fail-closed: a lane absent from metadata is OFF. No provisioning, no
   lookup — an unconfigured persona keeps byte-identical behavior.

2. **Shape classification (same measured-discriminator doctrine).**
   Conservative message-*shape* regexes (:func:`is_real_world_question`,
   :func:`is_academic_question`), misses accepted, never model judgment.

Degradation: a search failure is logged and swallowed by the caller — the
turn proceeds without the block (the lane never breaks a chat turn), and the
persona answers from its own world or deflects, exactly as before HU-2828.

Grounding safety: rendered hits ride into the §7.4.2 alignment corpus and the
W3 capability guard as ``external_context`` (HU-2070 widening precedent), so
a search-backed factual claim is *grounded*, not suppressed — and the wall
stays silent on a lane-fired turn (the turn is in-domain by construction).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "PERSONA_LOCATION_LABEL_KEY",
    "PERSONA_SEARCH_LANES_KEY",
    "PersonaKnowledgeProfile",
    "SearchHit",
    "SearchToolError",
    "build_search_query",
    "derive_search_lanes",
    "is_academic_question",
    "is_real_world_question",
    "realworld_grounding_text",
    "render_realworld_block",
    "searxng_search",
]


# --- Lane classification (message shape) ---------------------------------------

#: CURRENT-REALITY lane shapes: the persona's own present world — rent where
#: he lives, local costs/places, current scores, weather now. Deliberately NOT
#: matched: encyclopedic/scholarly shapes (the academic lane's job), abstract
#: trivia, and autobiographical turns ("do you remember when we...").
_CURRENT_REALITY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Rent / cost of living where the persona lives.
        r"\bhow\s+much\s+is\s+(the\s+)?rent\b",
        r"\bwhat'?s\s+(the\s+)?rent\b",
        r"\brent\b.{0,24}\b(where\s+you\s+live|your\s+(place|apartment|area|neighborhood))\b",
        r"\b(where\s+you\s+live|your\s+(place|apartment|area|neighborhood))\b.{0,24}\brent\b",
        r"\b(your|the)\s+rent\b",
        r"\bhow\s+much\s+do\s+you\s+pay\b",
        # Where do you live / neighborhood probes.
        r"\bwhere\s+do\s+you\s+live\b",
        r"\bwhere\s+is\s+(your\s+place|your\s+apartment)\b",
        r"\byour\s+neighborhood\b",
        r"\bwhat'?s\s+(it\s+like\s+)?(around|in)\s+(your\s+area|your\s+neighborhood|where\s+you\s+live)\b",
        # Local prices (coffee, apartments, everyday stuff in his city).
        r"\bhow\s+much\s+(is|are|does)\s+(a\s+|an\s+|the\s+)?(coffee|bagel|pizza|beer|apartment|loft|subway|gas)\b",
        # Current sports (his teams, last night's game).
        r"\bwho\s+won\s+the\s+(game|match)\b",
        r"\b(did\s+you\s+(see|watch|catch))\s+(the\s+)?(game|match)\b",
        r"\bwhat'?s\s+the\s+score\b",
        r"\bthe\s+game\s+(last\s+night|yesterday|tonight)\b",
        # Weather right now.
        r"\bwhat'?s\s+the\s+weather\b",
        r"\bhow'?s\s+the\s+weather\b",
        r"\bis\s+it\s+(raining|snowing)\s+(there|outside|yet)\b",
    )
)

#: ACADEMIC lane shapes: scholarly/encyclopedic territory. For a persona
#: without the academic lane this classification is *inert* — the lane simply
#: does not fire and honest ignorance stands (correct behavior for Chandler;
#: Ross would provision the lane ON and engage).
_ACADEMIC_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(what|who|tell\s+me\s+about|do\s+you\s+know\s+(about|anything\s+about))\s+(the\s+)?"
        r"(mayans?|aztecs?|incas?|pharaohs?|egyptians?|romans?|gladiators?|babylonians?)\b",
        r"\b(ancient|medieval|renaissance)\s+(civilization|history|egypt|rome|greece|times)\b",
        r"\bhistory\s+of\s+(the\s+)?[a-z]+\b",
        r"\bhow\s+does\s+[a-z]+\s+work\b",
        r"\bexplain\s+(the\s+)?[a-z]+\b",
        r"\b(theory\s+of|quantum|photosynthesis|markov|black\s+hole|dinosaurs?)\b",
        r"\bwhat\s+was\s+the\s+(renaissance|industrial\s+revolution|enlightenment)\b",
    )
)


def is_real_world_question(message: str) -> bool:
    """True when the message is a current-reality probe (search lane class)."""
    if not message:
        return False
    return any(p.search(message) for p in _CURRENT_REALITY_PATTERNS)


def is_academic_question(message: str) -> bool:
    """True when the message is a scholarly/encyclopedic probe (academic class)."""
    if not message:
        return False
    return any(p.search(message) for p in _ACADEMIC_PATTERNS)


# --- Persona-scoped knowledge profile ------------------------------------------

#: Per-persona lane permissions inside ``PersonaConfig.metadata``. Provisioned
#: from dialog evidence (:func:`derive_search_lanes`) at onboarding — never
#: hard-coded per character. Absent key / lane → OFF (fail-closed).
PERSONA_SEARCH_LANES_KEY = "search_lanes"

#: Optional human-readable persona location (``PersonaConfig.metadata``) used
#: to localize search queries and the grounding block ("Greenwich Village,
#: New York" for Chandler).
PERSONA_LOCATION_LABEL_KEY = "location_label"


@dataclass(frozen=True)
class PersonaKnowledgeProfile:
    """Which external lookup lanes this persona is permitted to fire.

    ``False`` is the default for every lane: the tool layer cannot reach the
    web on a persona's behalf without explicit, evidence-derived provisioning.
    """

    current_reality: bool = False
    academic: bool = False

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any] | None) -> PersonaKnowledgeProfile:
        """Build the profile from ``metadata[PERSONA_SEARCH_LANES_KEY]``.

        Only explicit boolean ``True`` values enable a lane; anything absent,
        mistyped, or falsy is OFF (misconfiguration can only *disable*, never
        enable).
        """
        lanes = (metadata or {}).get(PERSONA_SEARCH_LANES_KEY)
        if not isinstance(lanes, dict):
            return cls()
        return cls(
            current_reality=lanes.get("current_reality") is True,
            academic=lanes.get("academic") is True,
        )


#: Dialog-evidence keyword markers per lane (lowercased substrings). Derived
#: from the personas' own corpora: Chandler's lines are dense with
#: rent/apartment/neighborhood/Knicks evidence and void of scholarly markers;
#: Ross's are the reverse (museum/paleontology/lecture/PhD).
_ACADEMIC_EVIDENCE: tuple[str, ...] = (
    "paleontolog",
    "museum",
    "university",
    "professor",
    "phd",
    "doctorate",
    "thesis",
    "dissertation",
    "lecture",
    "excavation",
    "dig site",
    "archeolog",
    "archaeolog",
    "anthropolog",
    "scholar",
    "research grant",
    "peer review",
)
_CURRENT_REALITY_EVIDENCE: tuple[str, ...] = (
    "rent",
    "rent-controlled",
    "apartment",
    "landlord",
    "neighborhood",
    "greenwich village",
    "the village",
    "bills",
    "subway",
    "knicks",
    "yankees",
    "mets",
    "rangers",
    "giants",
    "jets",
    "season tickets",
    "madison square garden",
)


def derive_search_lanes(profile_text: str) -> dict[str, bool]:
    """Derive lane permissions from a persona's own dialog/profile evidence.

    The provisioning-time counterpart to :class:`PersonaKnowledgeProfile`: a
    lane is enabled only when the persona's corpus demonstrably carries that
    life-domain (so the tool reflects who the persona is, not a global
    default). Deterministic keyword evidence — no model judgment.
    """
    text = (profile_text or "").lower()
    return {
        "current_reality": any(mark in text for mark in _CURRENT_REALITY_EVIDENCE),
        "academic": any(mark in text for mark in _ACADEMIC_EVIDENCE),
    }


# --- Query building -------------------------------------------------------------


#: Leading question scaffolding stripped before searching (deterministic;
#: longest-first so "how much do you pay for" wins over "how much").
_QUESTION_SCAFFOLDING: tuple[str, ...] = (
    "how much do you pay for",
    "how much do you pay",
    "how much is",
    "how much are",
    "how much does",
    "do you know about",
    "do you know anything about",
    "can you tell me about",
    "tell me about",
    "what's the deal with",
    "what is the",
    "what's the",
    "what is",
    "what's",
    "where is",
    "how is",
)

#: Local-context phrases replaced by the persona's location label.
_LOCAL_CONTEXT_PHRASES: tuple[str, ...] = (
    "where you live",
    "where do you live",
    "in your neighborhood",
    "around your area",
    "in your area",
    "around here",
    "your neighborhood",
    "your area",
    "your city",
    "your part of town",
)


def build_search_query(message: str, location_label: str = "") -> str:
    """Build a deterministic SearXNG query from a real-world question.

    Strips question scaffolding, swaps local-context phrases for the persona's
    location label, and returns a compact keyword query. No model judgment —
    the same message always yields the same query.
    """
    text = (message or "").strip().rstrip("?").strip()
    low = text.lower()
    location = (location_label or "").strip()
    for phrase in _LOCAL_CONTEXT_PHRASES:
        if phrase in low:
            replacement = location if location else "the local area"
            start = low.index(phrase)
            text = (text[:start] + replacement + text[start + len(phrase):]).strip()
            low = text.lower()
            break
    for prefix in sorted(_QUESTION_SCAFFOLDING, key=len, reverse=True):
        if low.startswith(prefix + " "):
            text = text[len(prefix) + 1:].strip()
            break
    text = " ".join(text.split())
    if not text:
        text = (message or "").strip().rstrip("?").strip()
    return text


# --- SearXNG client --------------------------------------------------------------


class SearchToolError(RuntimeError):
    """Raised when the SearXNG lookup fails (network, HTTP, bad payload).

    Callers treat it as a degraded lane: log + proceed without the grounding
    block — the search lane never breaks a chat turn.
    """


@dataclass(frozen=True)
class SearchHit:
    """One normalized SearXNG result (title/snippet/url)."""

    title: str
    url: str
    content: str


async def searxng_search(
    query: str,
    *,
    base_url: str,
    timeout_s: float = 6.0,
    transport: httpx.AsyncBaseTransport | None = None,
    limit: int = 5,
) -> list[SearchHit]:
    """Query self-hosted SearXNG's JSON API and return normalized hits.

    ``transport`` is injectable so tests exercise the full request path
    without a live endpoint (same convention as the LLM clients). Raises
    :class:`SearchToolError` on network/HTTP/payload failure — never a bare
    httpx exception, so the caller's degradation path handles one error type.
    """
    if not query or not query.strip():
        raise SearchToolError("refusing empty search query")
    url = base_url.rstrip("/") + "/search"
    try:
        async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as client:
            response = await client.get(
                url,
                params={"q": query, "format": "json", "safesearch": "1", "language": "en"},
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise SearchToolError(f"search request to {url} failed: {exc}") from exc
    if response.status_code >= 400:
        raise SearchToolError(
            f"search at {url} returned HTTP {response.status_code}: {response.text[:200]}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise SearchToolError(f"search at {url} returned non-JSON body: {exc}") from exc
    if not isinstance(data, dict):
        raise SearchToolError(f"search at {url} returned non-object JSON body")
    hits: list[SearchHit] = []
    for row in data.get("results") or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        content = str(row.get("content") or "").strip()
        hit_url = str(row.get("url") or "").strip()
        if not (title or content):
            continue
        hits.append(SearchHit(title=title, url=hit_url, content=content))
        if len(hits) >= max(1, limit):
            break
    return hits


# --- Prompt rendering + guard grounding -------------------------------------------


def render_realworld_block(hits: list[SearchHit], location_label: str = "") -> str:
    """Render the CURRENT-REALITY grounding block for the system prompt.

    Structural machinery (same category as the era-boundary line), not a voice
    sheet. The block explicitly frames itself as the era-boundary exception —
    live probing (2026-09-11) showed the era line otherwise outweighs the
    notes and the model slides back to canon or invents facts.
    """
    if not hits:
        return ""
    place = (location_label or "").strip() or "your world"
    lines = [
        "CURRENT-WORLD NOTES (research service, verified current real-world "
        f"facts about {place}, as of today):",
        "These notes are your sanctioned exception to the era boundary: they "
        "describe your world as it is right now. When someone asks about "
        "this side of life — rent, what things cost, your neighborhood, "
        "last night's game, the weather — answer from these notes in your "
        "own voice (quote the figures naturally, the way anybody cites "
        "their own rent). If a note doesn't cover what they asked, you "
        "don't know it: deflect like you always do.",
    ]
    for idx, hit in enumerate(hits, start=1):
        snippet = hit.content or hit.title
        label = f"{hit.title} — " if hit.title and hit.title != snippet else ""
        lines.append(f"{idx}. {label}{snippet}")
    return "\n".join(lines)


def realworld_grounding_text(hits: list[SearchHit]) -> str:
    """Concatenated hit text for the safety guards' grounding corpus.

    A search-backed factual claim must align against the *researched* text,
    not be suppressed as confabulation (§7.4.2 ``external_context`` widening).
    """
    return "\n".join(
        part
        for hit in hits
        for part in (hit.title, hit.content)
        if part
    )
