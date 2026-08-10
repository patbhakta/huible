"""Consolidated Markdown serialization with temporal frontmatter.

Each abstraction (L1 fact, L2 scenario, L3 profile) is written as a Markdown
file whose YAML frontmatter carries ``tier``, ``memory_type`` and the temporal
schema ``valid_from`` / ``valid_to``. Evidence traceability is preserved via
the ``source`` frontmatter pointing back to the L0 raw record.

This module implements a minimal frontmatter reader/writer using the standard
library only (no ``pyyaml`` dependency) so it runs anywhere the rest of the
engine runs.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from huible.distillation.records import (
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

_FRONTMATTER_DELIM = re.compile(r"^---\s*$", re.MULTILINE)


def _serialize_value(key: str, value: Any) -> str:
    if value is None:
        return f"{key}:"
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key}: {value}"
    if isinstance(value, datetime):
        return f"{key}: {to_iso(value)}"
    text = str(value).replace("\n", "\\n")
    return f"{key}: {text}"


def render_frontmatter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        lines.append(_serialize_value(key, value))
    lines.append("---")
    return "\n".join(lines)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter dict, body).  Handles missing/empty frontmatter."""
    matches = _FRONTMATTER_DELIM.findall(text)
    if len(matches) < 2:
        return {}, text
    head, rest = text.split("\n---\n", 1)
    if not head.startswith("---"):
        return {}, text
    lines = head.strip().strip("-").splitlines()
    fields: dict[str, Any] = {}
    for line in lines:
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "":
            fields[key] = None
        elif value == "true":
            fields[key] = True
        elif value == "false":
            fields[key] = False
        elif value.lstrip("-").isdigit():
            fields[key] = int(value)
        else:
            try:
                fields[key] = float(value)
            except ValueError:
                fields[key] = value.replace("\\n", "\n")
    body = rest.strip()
    return fields, body


# -- Tier record renderers -------------------------------------------------
def _acoustic_summary(metadata: dict[str, Any]) -> str:
    """Compact one-line acoustic summary for L0 frontmatter (multimodal traceability).

    Returns "" when the record carries no per-utterance acoustic features, so
    text-only onboarding output is unchanged.
    """
    ac = metadata.get("acoustic")
    if not isinstance(ac, dict) or not ac:
        return ""
    parts = []
    pitch = ac.get("pitch")
    if isinstance(pitch, dict) and "mean" in pitch:
        parts.append(f"F0={pitch['mean']}")
    intensity = ac.get("intensity")
    if isinstance(intensity, dict) and "mean" in intensity:
        parts.append(f"int={intensity['mean']}")
    if ac.get("emotion"):
        parts.append(f"emo={ac['emotion']}")
    return ",".join(parts)


def render_l0(record: L0Record) -> str:
    fm: dict[str, Any] = {
        "tier": Tier.L0.value,
        "record_id": record.id,
        "source_kind": record.kind,
        "occurred_at": record.occurred_at,
        "ingested_at": record.ingested_at,
        "title": record.title,
    }
    acoustic = _acoustic_summary(record.metadata)
    if acoustic:
        fm["acoustic"] = acoustic
    return render_frontmatter(fm) + "\n\n" + record.content


def render_l1(fact: L1Fact) -> str:
    fm = {
        "tier": Tier.L1.value,
        "memory_type": fact.memory_type.value,
        "subject": fact.subject,
        "predicate": fact.predicate,
        "object": fact.object,
        "valid_from": fact.valid_from,
        "valid_to": fact.valid_to,
        "confidence": fact.confidence,
        "source": fact.evidence[0].source_id if fact.evidence else "",
    }
    return render_frontmatter(fm) + "\n\n" + (fact.content or fact.object)


def render_l2(scenario: L2Scenario) -> str:
    fm = {
        "tier": Tier.L2.value,
        "scenario": scenario.scenario,
        "domain": scenario.domain,
        "fact_ids": ",".join(f.id for f in scenario.facts),
        "evidence_sources": ",".join(
            {e.source_id for e in scenario.evidence} or []
        ),
    }
    body = scenario.summary
    if scenario.facts:
        body += "\n\n" + "\n".join(f"- {f.content}" for f in scenario.facts)
    return render_frontmatter(fm) + "\n\n" + body


def render_l3(profile: L3Profile) -> str:
    fm = {
        "tier": Tier.L3.value,
        "memory_type": profile.memory_type.value,
        "key": profile.key,
        "valid_from": profile.valid_from,
        "valid_to": profile.valid_to,
        "confidence": profile.confidence,
        "supersedes": profile.supersedes or "",
        "source": profile.evidence[0].source_id if profile.evidence else "",
    }
    return render_frontmatter(fm) + "\n\n" + profile.rule


def parse_l0(text: str) -> tuple[dict[str, Any], str]:
    return parse_frontmatter(text)


def parse_record(text: str) -> dict[str, Any]:
    """Parse any tier record markdown into a dict with frontmatter + body."""
    fields, body = parse_frontmatter(text)
    fields["_body"] = body
    return fields


def build_l1(fields: dict[str, Any], body: str, record_id: str = "") -> L1Fact:
    evidence = (
        [EvidenceLink(source_id=fields.get("source") or record_id, source_kind="conversation")]
        if fields.get("source") or record_id
        else []
    )
    return L1Fact(
        subject=str(fields.get("subject", "")),
        predicate=str(fields.get("predicate", "")),
        object=str(fields.get("object", "")),
        memory_type=MemoryType(fields.get("memory_type", "observation")),
        valid_from=parse_iso(fields.get("valid_from")),
        valid_to=parse_iso(fields.get("valid_to")),
        evidence=evidence,
        confidence=float(fields.get("confidence", 0.5)),
        content=body,
    )
