"""CLI wrapper that runs TencentDB L0-L3 distillation over onboarding dialog.

This is the thin wiring layer called by the ``huible-onboard`` Kestra flow
(stage S3) right after the deterministic ``stats.py`` grounding anchor (S2b).
It consumes the cleaned dialog JSONL produced by ``modules/onboarding/clean.py``
plus the optional ``stats.json`` from ``modules/onboarding/stats.py`` and writes
the consolidated L0-L3 Markdown memory pyramid (with ``EvidenceLink`` citations
back to every raw source) via :class:`MarkdownMemoryStore`.

The distillation engine itself is deterministic and dependency-free by default
(see ``huible.distillation.distill.Distiller``). Under ``--strict`` no LLM
extrapolation is permitted: the deterministic heuristic extractor is the only
path unless an explicit ``--llm`` is requested together with an OpenRouter key,
in which case the model is constrained to the provided chunks + stats and must
answer ``"not found"`` on gaps rather than hallucinate.

Usage (mirrors the ``huible-onboard`` flow spec §2a)::

    python3 -m huible.distillation.cli \\
        --input  cleaned.jsonl \\
        --stats  stats.json \\
        --persona chandler \\
        --out-dir /tmp/onboarding/chandler/memory \\
        --strict

Run ``python3 -m huible.distillation.cli --help`` for all flags.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from huible.distillation import (
    Distiller,
    L0Record,
    MarkdownMemoryStore,
)
from huible.distillation.distill import LLMHook
from huible.distillation.records import MemoryType

# Domains the gap loop expects to see populated for a fully-onboarded persona.
# Used to flag missing coverage in the manifest; ``modules/onboarding/gaps.py``
# performs the richer facet-level gap analysis.
EXPECTED_DOMAINS: tuple[str, ...] = (
    "identity",
    "family",
    "career",
    "places",
    "hobbies",
    "food & drink",
)


def _parse_record_line(entry: dict[str, Any], persona: str, index: int) -> L0Record | None:
    """Convert one cleaned.jsonl entry into an L0 raw record.

    Returns ``None`` for entries with no usable text so the caller can count
    them as skipped (a deterministic, hallucination-free signal).
    """
    text = (entry.get("text") or "").strip()
    if not text:
        return None
    speaker = (entry.get("speaker") or persona or "").strip()
    source = (entry.get("source") or "dialog").strip()
    # Deterministic, content-addressable id so EvidenceLinks are stable across
    # re-runs of the same cleaned corpus (aid the gap loop + auditing). Uses
    # sha1 (not Python's randomized hash) for cross-process reproducibility.
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    record_id = f"{source}:{index:05d}:{digest}"
    return L0Record(
        id=record_id,
        kind="conversation",
        title=text[:48],
        content=f"{speaker}: {text}" if speaker and speaker.lower() != persona.lower() else text,
        occurred_at=None,
        metadata={
            "speaker": speaker,
            "source_file": source,
            "emotion": entry.get("emotion"),
            "persona": persona,
        },
    )


def load_cleaned(path: str | Path, persona: str) -> tuple[list[L0Record], int]:
    """Read cleaned dialog JSONL → L0 records.

    Returns ``(records, skipped)`` where ``skipped`` counts blank/unparseable
    lines so the caller can surface a deterministic quality signal.
    """
    records: list[L0Record] = []
    skipped = 0
    with open(path, encoding="utf-8") as f:
        for index, raw in enumerate(f):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                skipped += 1
                continue
            record = _parse_record_line(entry, persona, index)
            if record is None:
                skipped += 1
                continue
            records.append(record)
    return records, skipped


def load_stats(path: str | Path | None) -> dict[str, Any] | None:
    """Load the deterministic ``stats.json`` grounding anchor if present."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def build_strict_llm_hook(
    stats: dict[str, Any] | None,
    persona: str,
    model: str,
) -> Callable[..., Awaitable[dict[str, Any]]] | None:
    """Build an optional strict OpenRouter LLM hook for the distiller.

    Returns ``None`` when no ``OPENROUTER_API_KEY`` is available so the
    distiller falls back to its deterministic heuristic extractor (which is
    inherently strict: it only emits facts evidenced by a raw sentence).

    When a key is present the hook issues a single constrained extraction call
    that feeds the model *only* the provided chunks + stats and instructs it to
    return ``"not found"`` for anything not directly supported — never
    extrapolate. The deterministic extractor remains the fallback on any error.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None

    # Imported lazily so the CLI stays import-safe in environments without
    # network access (and so unit tests never trigger an import side effect).
    import urllib.error
    import urllib.request

    stats_block = json.dumps(stats or {}, ensure_ascii=False)

    async def hook(action: str, payload: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        if action != "distill_l1":
            return {"facts": []}
        system = (
            "You are a strict memory distillation engine for the Huible persona "
            f"'{persona}'. You receive raw conversation chunks and a deterministic "
            "stats anchor. Extract ONLY atomic facts directly evidenced by the "
            "provided chunks. For any gap, return memory_type='observation' with "
            "object='not found'. NEVER extrapolate, invent, or paraphrase beyond "
            "what the chunks state. Each fact must reference its source_id."
        )
        user_payload = {
            "stats_anchor": stats_block,
            "records": payload.get("records", []),
            "rules": [
                "If a facet has no supporting chunk, output object='not found'.",
                "Do not infer preferences, relationships, or locations not stated.",
                "Prefer the stats anchor's top_words/bigrams; do not invent vocabulary.",
            ],
        }
        body = json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                "max_tokens": 4000,
                "temperature": 0.0,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=body,
            method="POST",
        )
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            # Strict policy: never hallucinate. Surface the failure and let the
            # distiller fall back to the deterministic heuristic extractor.
            print(f"[distillation.cli] strict LLM call failed: {exc}", file=sys.stderr)
            return {"facts": []}
        content = result["choices"][0]["message"]["content"]
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(content)
        return {"facts": []}

    return hook


def _domain_coverage(result: Any) -> dict[str, int]:
    """Count L2 scenarios per domain for the manifest's gap signal."""
    coverage: dict[str, int] = {domain: 0 for domain in EXPECTED_DOMAINS}
    for scenario in result.scenarios:
        key = (scenario.domain or "general").lower()
        coverage[key] = coverage.get(key, 0) + 1
    return coverage


def _write_manifest(
    out_dir: Path,
    persona: str,
    result: Any,
    skipped: int,
    stats_path: str | None,
    stats: dict[str, Any] | None,
    strict: bool,
    used_llm: bool,
    model: str,
) -> dict[str, Any]:
    """Write ``distill-manifest.json`` summarizing the run for the gap loop."""
    coverage = _domain_coverage(result)
    missing_domains = [d for d, c in coverage.items() if c == 0 and d in coverage]
    manifest = {
        "persona": persona,
        "distilled_at": datetime.now(UTC).isoformat(),
        "strict": strict,
        "used_llm": used_llm,
        "llm_model": model if used_llm else None,
        "stats_source": str(stats_path) if stats_path else None,
        "stats_anchor_present": stats is not None,
        "counts": {
            "L0_raw": len(result.raw),
            "L1_facts": len(result.facts),
            "L2_scenarios": len(result.scenarios),
            "L3_profiles": len(result.profiles),
            "durable_rules": sum(
                1 for p in result.profiles if p.memory_type is MemoryType.DURABLE_RULE
            ),
            "current_states": sum(
                1 for p in result.profiles if p.memory_type is MemoryType.CURRENT_STATE
            ),
        },
        "skipped_blank_or_unparseable": skipped,
        "domain_coverage": coverage,
        "missing_domains": missing_domains,
        "all_records_have_evidence": all(
            bool(f.evidence) for f in result.facts
        )
        and all(bool(p.evidence) for p in result.profiles),
        "output_dir": str(out_dir),
    }
    manifest_path = out_dir / "distill-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


async def run(
    input_path: str,
    stats_path: str | None,
    persona: str,
    out_dir: str,
    strict: bool,
    use_llm: bool,
    model: str,
    max_records: int | None,
) -> dict[str, Any]:
    """Execute distillation and write the L0-L3 Markdown store + manifest."""
    records, skipped = load_cleaned(input_path, persona)
    if not records:
        raise SystemExit(
            f"No usable dialog records found in {input_path} for persona '{persona}'"
        )
    if max_records is not None and max_records > 0:
        records = records[:max_records]

    stats = load_stats(stats_path)

    llm_hook: LLMHook | None = None
    used_llm = False
    if use_llm:
        candidate = build_strict_llm_hook(stats, persona, model)
        if candidate is not None:
            llm_hook = candidate
            used_llm = True
        elif strict:
            # Strict + no key → deterministic only (inherently gap-safe).
            print(
                "[distillation.cli] --strict with no OPENROUTER_API_KEY: "
                "using deterministic extractor (gap-safe).",
                file=sys.stderr,
            )
        else:
            print(
                "[distillation.cli] --llm requested but no OPENROUTER_API_KEY set: "
                "falling back to deterministic extractor.",
                file=sys.stderr,
            )

    distiller = Distiller(llm=llm_hook)
    result = await distiller.distill(records)

    out = Path(out_dir)
    store = MarkdownMemoryStore(out)
    store.write_result(result)

    manifest = _write_manifest(
        out, persona, result, skipped, stats_path, stats, strict, used_llm, model
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="huible.distillation.cli",
        description=(
            "Run TencentDB L0-L3 distillation over cleaned onboarding dialog "
            "and write the consolidated Markdown memory store."
        ),
    )
    parser.add_argument("--input", required=True, help="Cleaned dialog JSONL (clean.py output).")
    parser.add_argument(
        "--stats",
        help="stats.json grounding anchor from stats.py (optional but recommended).",
    )
    parser.add_argument("--persona", required=True, help="Persona name (e.g. chandler).")
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Output directory for the L0-L3 Markdown memory pyramid.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Forbid LLM extrapolation; emit 'not found' on gaps (recommended).",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Enable the optional strict OpenRouter LLM hook (requires OPENROUTER_API_KEY).",
    )
    parser.add_argument(
        "--model",
        default="google/gemini-3-flash-preview",
        help="OpenRouter model id (only used with --llm).",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Optional cap on the number of L0 records distilled (debugging).",
    )
    args = parser.parse_args()

    manifest = asyncio.run(
        run(
            input_path=args.input,
            stats_path=args.stats,
            persona=args.persona,
            out_dir=args.out_dir,
            strict=args.strict,
            use_llm=args.llm,
            model=args.model,
            max_records=args.max_records,
        )
    )

    c = manifest["counts"]
    print(f"Distilled '{manifest['persona']}' → {args.out_dir}")
    print(
        f"  L0 raw: {c['L0_raw']}  L1 facts: {c['L1_facts']}  "
        f"L2 scenarios: {c['L2_scenarios']}  L3 profiles: {c['L3_profiles']}"
    )
    print(
        f"  durable_rules: {c['durable_rules']}  current_states: {c['current_states']}  "
        f"evidence_complete: {manifest['all_records_have_evidence']}"
    )
    print(f"  strict={manifest['strict']}  used_llm={manifest['used_llm']}")
    if manifest["missing_domains"]:
        print(f"  gap signal — missing domains: {', '.join(manifest['missing_domains'])}")
    # Kestra-readable output block.
    print(f"::{json.dumps({'outputs': manifest})}::")


if __name__ == "__main__":
    main()
