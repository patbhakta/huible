#!/usr/bin/env python3
"""
Huible Onboarding — Gap detection from distillation memory.

Reads the L0-L3 Markdown memory store written by ``huible.distillation.cli``
(stage S3) and emits a structured gap list for the onboarding gap loop. A gap
is a persona facet that is **missing** (no supporting memory records) or
**weak** (only low-confidence / observation-level evidence, no durable rule or
current state). Each gap carries a suggested probing question the Onboarding /
Q&A agent can route to the client.

This stage is deterministic and grounded: it never invents gaps, it only
reports coverage holes against an explicit facet model derived from the OKF
v0.2 persona structure (identity, speech, relationships, preferences, current
situation, memories).

Usage:
  python3 modules/onboarding/gaps.py \\
      --memory-dir /tmp/onboarding/chandler/memory \\
      --persona chandler \\
      --output   /tmp/onboarding/chandler/gaps.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

# When provided, also read the distill-manifest.json for domain-level coverage
# (the CLI's deterministic gap signal) so the gap loop sees both the facet view
# and the distillation domain view.
MANIFEST_FILENAME = "distill-manifest.json"

# Facet model: the persona dimensions the OKF structuring stage (structure.py)
# needs. Each facet has keyword signals used to classify L1-L3 records and a
# probing question used when the facet is missing/weak.
FACETS: list[dict[str, Any]] = [
    {
        "id": "identity",
        "label": "Identity & core traits",
        "keywords": [
            "identity", "trait", "personality", "character", "name", "who",
        ],
        "question": "How would you describe {persona}'s personality and core traits?",
    },
    {
        "id": "speech",
        "label": "Speech patterns & catchphrases",
        "keywords": [
            "speech", "catchphrase", "vocabulary", "humor", "sarcastic", "wit",
            "sentence", "language", "say", "says", "talk",
        ],
        "question": "What are {persona}'s catchphrases or distinctive speech habits?",
    },
    {
        "id": "relationships",
        "label": "Key relationships",
        "keywords": [
            "family", "mother", "father", "son", "daughter", "sister", "brother",
            "friend", "wife", "husband", "partner", "relationship", "marriage",
            "grandfather", "grandmother",
        ],
        "question": "Who are the most important people in {persona}'s life?",
    },
    {
        "id": "preferences",
        "label": "Preferences & durable habits",
        "keywords": [
            "prefers", "likes", "loves", "enjoys", "always", "never", "habit",
            "favorite", "favourite", "ritual", "tradition", "tea", "coffee",
            "music", "food", "drink",
        ],
        "question": "What does {persona} always love or always refuse to do?",
    },
    {
        "id": "current_state",
        "label": "Current life situation",
        "keywords": [
            "currently", "now", "lives", "living", "works", "moved", "resides",
            "job", "career", "home", "city", "seattle", "india",
        ],
        "question": "What is {persona}'s current life situation (home, work, routine)?",
    },
    {
        "id": "memories",
        "label": "Episodic memories & topics",
        "keywords": [
            "remember", "remembered", "memory", "story", "stories", "trip",
            "garden", "travel", "event", "experience", "childhood",
        ],
        "question": "What stories or memories does {persona} often bring up?",
    },
]

# Confidence below which a record is treated as weak evidence.
WEAK_CONFIDENCE_THRESHOLD = 0.6


def _load_memory_records(memory_dir: str) -> list[dict[str, Any]]:
    """Load every L1/L2/L3 markdown record in the memory store as a dict."""
    from huible.distillation import MarkdownMemoryStore, Tier

    store = MarkdownMemoryStore(memory_dir)
    records: list[dict[str, Any]] = []
    for tier in (Tier.L1, Tier.L2, Tier.L3):
        for rec in store.list_records(tier):
            rec = dict(rec)
            rec["tier"] = tier.value
            records.append(rec)
    return records


def _classify(record: dict[str, Any]) -> str:
    """Return the facet id a record best supports, or 'general' if none match."""
    text = " ".join(
        str(record.get(k, ""))
        for k in ("key", "scenario", "domain", "_body", "subject", "predicate", "object")
    ).lower()
    best, best_hits = "general", 0
    for facet in FACETS:
        hits = sum(1 for kw in facet["keywords"] if kw in text)
        if hits > best_hits:
            best, best_hits = facet["id"], hits
    return best


def _confidence(record: dict[str, Any]) -> float:
    raw = record.get("confidence")
    try:
        return float(raw) if raw is not None else 0.5
    except (TypeError, ValueError):
        return 0.5


def _memory_type(record: dict[str, Any]) -> str:
    mt = record.get("memory_type")
    return str(mt) if mt is not None else ""


def _is_not_found(record: dict[str, Any]) -> bool:
    """A strict-mode distillation gap marker."""
    body = str(record.get("_body", "")).strip().lower()
    obj = str(record.get("object", "")).strip().lower()
    return body == "not found" or obj == "not found"


def build_gap_report(
    memory_dir: str,
    persona: str,
    weak_threshold: float = WEAK_CONFIDENCE_THRESHOLD,
) -> dict[str, Any]:
    """Build the structured gap report from the distillation memory store."""
    records = _load_memory_records(memory_dir)

    # Bucket records by facet and strength.
    coverage: dict[str, dict[str, Any]] = {
        facet["id"]: {
            "label": facet["label"],
            "strong": 0,  # durable_rule / current_state records
            "weak": 0,    # observation / low-confidence records
            "not_found": 0,
            "evidence_sources": set(),
        }
        for facet in FACETS
    }

    weak_records: list[dict[str, Any]] = []
    not_found_records: list[dict[str, Any]] = []

    for record in records:
        facet_id = _classify(record)
        if facet_id not in coverage:
            continue
        bucket = coverage[facet_id]
        source = str(record.get("source") or record.get("evidence_sources") or "")
        if source:
            for s in re.split(r"[,\s]+", source):
                if s:
                    bucket["evidence_sources"].add(s)
        if _is_not_found(record):
            bucket["not_found"] += 1
            not_found_records.append(_summarize_record(record, facet_id))
            continue
        if _memory_type(record) in ("durable_rule", "current_state") and _confidence(
            record
        ) >= weak_threshold:
            bucket["strong"] += 1
        else:
            bucket["weak"] += 1
            if _confidence(record) < weak_threshold:
                weak_records.append(_summarize_record(record, facet_id))

    gaps: list[dict[str, Any]] = []
    for facet in FACETS:
        bucket = coverage[facet["id"]]
        strong = bucket["strong"]
        total = strong + bucket["weak"] + bucket["not_found"]
        if total == 0:
            status = "missing"
        elif strong == 0:
            status = "weak"
        else:
            continue  # facet has strong, grounded evidence → not a gap
        gaps.append(
            {
                "facet": facet["id"],
                "label": facet["label"],
                "status": status,
                "strong_records": strong,
                "weak_records": bucket["weak"],
                "not_found_markers": bucket["not_found"],
                "total_records": total,
                "suggested_question": facet["question"].format(persona=persona),
            }
        )

    # Merge the deterministic domain-coverage signal from the distill manifest.
    manifest_path = os.path.join(memory_dir, MANIFEST_FILENAME)
    manifest_missing_domains: list[str] = []
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        manifest_missing_domains = list(manifest.get("missing_domains", []) or [])

    report = {
        "persona": persona,
        "memory_dir": os.path.abspath(memory_dir),
        "total_records": len(records),
        "gap_count": len(gaps),
        "gaps": gaps,
        "weak_records": weak_records,
        "not_found_records": not_found_records,
        "distill_missing_domains": manifest_missing_domains,
        "coverage": {
            fid: {
                "label": cov["label"],
                "strong": cov["strong"],
                "weak": cov["weak"],
                "not_found": cov["not_found"],
                "evidence_sources": sorted(cov["evidence_sources"]),
            }
            for fid, cov in coverage.items()
        },
    }
    return report


def _summarize_record(record: dict[str, Any], facet_id: str) -> dict[str, Any]:
    return {
        "facet": facet_id,
        "tier": record.get("tier"),
        "memory_type": _memory_type(record),
        "confidence": _confidence(record),
        "source": str(record.get("source") or record.get("evidence_sources") or ""),
        "preview": str(record.get("_body", ""))[:120],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Emit a structured gap list from the distillation memory store."
    )
    parser.add_argument(
        "--memory-dir",
        required=True,
        help="Directory written by huible.distillation.cli (L0-L3 Markdown + manifest).",
    )
    parser.add_argument("--persona", required=True, help="Persona name.")
    parser.add_argument(
        "--output",
        help="Optional JSON output path for the gap report.",
    )
    parser.add_argument(
        "--weak-threshold",
        type=float,
        default=WEAK_CONFIDENCE_THRESHOLD,
        help=(
            "Confidence below which a record counts as weak "
            f"(default {WEAK_CONFIDENCE_THRESHOLD})."
        ),
    )
    args = parser.parse_args()

    if not os.path.isdir(args.memory_dir):
        print(f"ERROR: memory dir not found: {args.memory_dir}", file=sys.stderr)
        sys.exit(1)

    report = build_gap_report(args.memory_dir, args.persona, args.weak_threshold)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    print(f"Persona: {report['persona']}")
    print(f"Memory records scanned: {report['total_records']}")
    print(f"Gaps: {report['gap_count']}")
    for gap in report["gaps"]:
        print(f"  [{gap['status'].upper()}] {gap['facet']} — {gap['label']}")
        print(f"      → {gap['suggested_question']}")
    if report["distill_missing_domains"]:
        print(
            "  distill domain gaps: "
            + ", ".join(report["distill_missing_domains"])
        )

    # Kestra-readable output block.
    print(f"::{json.dumps({'outputs': report})}::")


if __name__ == "__main__":
    main()
