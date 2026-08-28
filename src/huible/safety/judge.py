"""LLM-as-judge adjudication for §7.4.2 alignment suppressions (HU-2161).

The Phase-1 §7.4.2 alignment guard (:mod:`huible.safety.alignment`) is a
deterministic content-overlap check: a biographical / relationship claim is
grounded iff one of its salient tokens appears in the turn's grounding corpus
(activated refs + persona-scope G4-admissible memories + conversation history
+ persona vault). That check is deliberately conservative — it is precise
about *fabricated named entities* but has a documented false-positive class:
a truthful reply that names canon the corpus simply does not contain (the
HU-2070 recurrence: e.g. a job title that appears nowhere in the raw-dialogue
corpus) is suppressed and the user sees the canned reflection fallback.

This module is the §7.4.2-roadmap hardening pulled forward (HU-2161): an
optional **LLM-judge backstop on the suppression decision**. When the overlap
filter flags a biographical / relationship claim, the judge — the same real
generator provider that produced the reply, never the key-free fake —
adjudicates each flagged claim as ``supported`` or ``fabricated`` against a
compact persona record digest (canonical-tier memories + vault). Claims the
judge supports are removed from the un-grounded set; a turn whose flagged
claims are all supported passes with its original text. A suppression that
survives the judge is a **high-confidence confabulation** — that, and only
that, is what §3 Sev-1 (A) pages on (see :mod:`huible.api.paging`).

Design constraints:

* **Deterministic suites unchanged.** When no real provider is configured
  (fake / mock generators — the key-free default and the whole deterministic
  guardrail suite) the judge reports ``unavailable`` and callers keep the
  strict Phase-1 suppression. Every existing suppression test stays green.
* **Fail toward clinical safety, not toward paging.** Judge timeout / error /
  unparseable output also reports ``unavailable``: the suppression stands
  (the user never sees an un-adjudicated flagged claim) but the turn is
  recorded as *unconfirmed* — it must not page a human, because a Phase-1
  content-overlap verdict alone is not evidence the generator confabulated
  (the exact HU-2070/HU-2161 false-positive class).
* **Policy claims never reach the judge.** Identity (G2/G5) and advice (G9)
  claims are pattern-level policy violations — the vault can never
  legitimately contain them — and are always suppressed deterministically.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from huible.safety.alignment import Claim, ClaimCategory

__all__ = [
    "JUDGE_UNAVAILABLE_TIMEOUT_S",
    "JudgeVerdict",
    "LLMClientLike",
    "adjudicate_alignment_claims",
    "build_canon_digest",
    "judge_eligible",
]

#: Providers that can never serve as the judge (key-free deterministic stubs).
_NON_JUDGE_PROVIDERS: frozenset[str] = frozenset({"fake", "mock", "unknown", ""})

#: Upper bound on the persona-record digest handed to the judge (chars). The
#: digest is canonical-tier facts + vault fields; the cap keeps the judge call
#: cheap and bounded even on very large personas.
_DIGEST_CHAR_CAP: int = 6_000

#: Max canonical-tier facts included in the digest.
_DIGEST_FACT_CAP: int = 80

#: Overall wall-clock budget for one adjudication call, seconds. On expiry the
#: judge reports ``unavailable`` — the suppression stands, unconfirmed.
JUDGE_UNAVAILABLE_TIMEOUT_S: float = 12.0

#: Canonical-tier memory tier value used to pick record facts for the digest.
_CANONICAL_TIER_VALUES: frozenset[str] = frozenset({"canonical"})


@runtime_checkable
class LLMClientLike(Protocol):
    """Structural view of the app LLM client (avoids an import cycle)."""

    provider: str

    async def generate(
        self, prompt: str, *, system_prompt: str | None = None, **kwargs: Any
    ) -> str: ...


@dataclass(slots=True, frozen=True)
class JudgeVerdict:
    """Outcome of adjudicating the overlap-flagged claims of one turn.

    ``outcome`` is one of:

    * ``"supported"`` — every flagged biographical / relationship claim is
      consistent with the persona record; the turn must be restored.
    * ``"fabricated"`` — at least one flagged claim is a judge-confirmed
      confabulation; the suppression stands and is page-worthy (§3 Sev-1 (A)).
    * ``"unavailable"`` — no real judge ran (fake provider, timeout, error,
      unparseable output). The suppression stands but is *unconfirmed*: not
      page-worthy, surfaced via the unconfirmed-suppression counter instead.
    """

    outcome: str
    reason: str = ""
    supported: tuple[str, ...] = field(default_factory=tuple)
    fabricated: tuple[str, ...] = field(default_factory=tuple)


def judge_eligible(llm: LLMClientLike | None) -> bool:
    """True when ``llm`` is a real provider that may serve as the judge.

    The key-free fake / deterministic mock generators never judge: under them
    a flagged biographical claim is a fixture (the fake only echoes refs), and
    the strict Phase-1 behavior is what the deterministic suite asserts.
    """
    if llm is None:
        return False
    return str(getattr(llm, "provider", "unknown")).lower() not in _NON_JUDGE_PROVIDERS


def build_canon_digest(
    *,
    persona_name: str,
    voice_instructions: str = "",
    era_knowledge_boundary: str = "",
    persona_scope_refs: Any = None,
) -> str:
    """Build the compact persona-record digest the judge adjudicates against.

    Vault fields (name, voice instructions, era boundary) plus a capped sample
    of canonical-tier persona-scope memories — the curated, G4-admissible
    profile layer — capped at :data:`_DIGEST_FACT_CAP` facts /
    :data:`_DIGEST_CHAR_CAP` chars so the judge call stays small and bounded.
    """
    lines: list[str] = [f"Persona: {persona_name or 'unknown'}"]
    if era_knowledge_boundary:
        lines.append(f"Record ends (era boundary): {era_knowledge_boundary}")
    if voice_instructions:
        lines.append(f"Voice / character record: {voice_instructions.strip()}")
    facts: list[str] = []
    if persona_scope_refs:
        for node in persona_scope_refs:
            tier = str(getattr(getattr(node, "tier", ""), "value", getattr(node, "tier", "")))
            if tier not in _CANONICAL_TIER_VALUES:
                continue
            content = (getattr(node, "content", "") or "").strip()
            if content:
                facts.append(content)
            if len(facts) >= _DIGEST_FACT_CAP:
                break
    if facts:
        lines.append("Verified record (canonical memories):")
        lines.extend(f"- {f}" for f in facts)
    digest = "\n".join(lines)
    if len(digest) > _DIGEST_CHAR_CAP:
        digest = digest[:_DIGEST_CHAR_CAP]
    return digest


_JUDGE_SYSTEM_PROMPT = """You are a meticulous fact-checking judge for a memorial-persona service. \
The persona is a deceased person represented by a verified record of memories. \
A content-overlap filter flagged first-person claims in a draft reply as possibly un-grounded \
(their salient words do not literally appear in the record). Adjudicate each flagged claim.

Mark a claim FABRICATED only when, given the record:
- the record directly contradicts it, OR
- it asserts a specific invented fact — a named person, place, employer, unique job title, or life \
event — that has no basis in the record and is not established canon about this persona.

Otherwise mark it SUPPORTED, including when:
- the claim paraphrases or is consistent with the record, OR
- it is widely-known canon about this persona, OR
- it is a mild, non-specific life assertion the record simply does not mention.

Err toward SUPPORTED when uncertain: the filter already catches fabricated named entities; your \
job is to spare truthful canon the record fails to literally contain.

Respond with ONLY a JSON object, no prose:
{"verdicts": [{"claim": "<claim text>", "verdict": "supported"|"fabricated"}], \
"reason": "<one sentence>"}"""

_JSON_BLOB = re.compile(r"\{.*\}", re.DOTALL)


def _normalize_claim_text(text: str) -> str:
    """Normalize a claim text for judge-echo matching.

    Judges routinely echo the claim without surrounding quotation marks,
    curly/straight-quote differences, or em-dash spacing. Collapse all of
    that to bare lowercase alphanumerics so a faithful echo still matches.
    """
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _parse_verdicts(raw: str, claims: list[Claim]) -> tuple[JudgeVerdict, bool]:
    """Parse the judge JSON against the flagged ``claims``.

    Returns ``(verdict, ok)``; ``ok=False`` means unparseable/incomplete →
    callers treat as ``unavailable`` (never fail open on a malformed judge).
    """
    if not raw:
        return JudgeVerdict(outcome="unavailable", reason="empty judge output"), False
    match = _JSON_BLOB.search(raw)
    if match is None:
        return JudgeVerdict(outcome="unavailable", reason="no JSON in judge output"), False
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return JudgeVerdict(outcome="unavailable", reason="unparseable judge output"), False
    entries = data.get("verdicts")
    if not isinstance(entries, list) or not entries:
        return JudgeVerdict(outcome="unavailable", reason="judge returned no verdicts"), False

    by_text: dict[str, str] = {}
    by_norm: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("claim", "")).strip()
        verdict = str(entry.get("verdict", "")).strip().lower()
        if text and verdict in ("supported", "fabricated"):
            by_text[text] = verdict
            by_norm[_normalize_claim_text(text)] = verdict

    supported: list[str] = []
    fabricated: list[str] = []
    missing = 0
    for claim in claims:
        text = claim.text.strip()
        got = by_text.get(text)
        if got is None:
            # Lenient claim-text matching: the judge may trim/normalize
            # punctuation or quotes around the echoed claim text. Fall back
            # to a punctuation-insensitive comparison before declaring the
            # claim unmatched.
            got = by_norm.get(_normalize_claim_text(text))
        if got is None:
            missing += 1
            continue
        (fabricated if got == "fabricated" else supported).append(claim.text)

    if missing == len(claims):
        return JudgeVerdict(outcome="unavailable", reason="verdicts matched no claims"), False
    # Any unmatched claim is treated as fabricated (fail toward suppression,
    # never toward releasing a claim the judge did not clear).
    reason = str(data.get("reason", ""))[:300]
    outcome = "fabricated" if fabricated or missing else "supported"
    return (
        JudgeVerdict(
            outcome=outcome,
            reason=reason,
            supported=tuple(supported),
            fabricated=tuple(fabricated),
        ),
        True,
    )


async def adjudicate_alignment_claims(
    *,
    llm: LLMClientLike | None,
    persona_name: str,
    canon_digest: str,
    claims: list[Claim],
    timeout_s: float = JUDGE_UNAVAILABLE_TIMEOUT_S,
) -> JudgeVerdict:
    """Adjudicate the overlap-flagged biographical / relationship ``claims``.

    Returns :class:`JudgeVerdict`. Never raises: every failure mode (no real
    provider, timeout, transport error, malformed output) degrades to
    ``unavailable`` so the caller can keep the suppression *without* paging.
    """
    judged = [c for c in claims if c.category not in (ClaimCategory.IDENTITY, ClaimCategory.ADVICE)]
    if not judged:
        return JudgeVerdict(outcome="supported", reason="no judgeable claims")
    if not judge_eligible(llm):
        return JudgeVerdict(outcome="unavailable", reason="no real judge provider")

    numbered = "\n".join(f"{i}. {c.text}" for i, c in enumerate(judged, 1))
    prompt = (
        f"Persona record:\n{canon_digest}\n\n"
        f"Flagged claims from the draft reply:\n{numbered}\n\n"
        "Adjudicate each flagged claim per the instructions."
    )
    try:
        raw = await asyncio.wait_for(
            llm.generate(prompt, system_prompt=_JUDGE_SYSTEM_PROMPT),
            timeout=timeout_s,
        )
    except TimeoutError:
        return JudgeVerdict(outcome="unavailable", reason=f"judge timeout >{timeout_s}s")
    except Exception as exc:  # transport / provider error — never break the turn
        return JudgeVerdict(outcome="unavailable", reason=f"judge error: {type(exc).__name__}")

    verdict, ok = _parse_verdicts(raw, judged)
    if not ok:
        return verdict
    return verdict
