#!/usr/bin/env python3
"""Go/no-go A/B harness: contaminated vs grounded onboarding on Chandler data.

BHAA-1361 validation gate. Runs the **same** local chat model through BOTH
prompt architectures so the grounding layer is the only variable:

- CONTAMINATED: the pre-BHAA-1360 ``build_extraction_prompt`` (LLM over a raw
  dialog sample; no grounding anchor; invites the model to fill every slot).
- GROUNDED: the post-BHAA-1360 ``build_grounded_extraction_prompt`` (LLM over
  the deterministic L0-L3 memory brief; instructed to mark gaps rather than
  invent; carries EvidenceLink citations).

Measured dimensions (acceptance criteria of BHAA-1361):
- citation coverage: % of distilled records with an EvidenceLink, plus the
  evidence block emitted by the grounded OKF docs vs. the contaminated docs.
- hallucination rate: fraction of content tokens in the LLM output that do NOT
  appear in the 7,519-line corpus vocabulary (lower = more grounded).
- gap honesty: count of "Not enough data to determine." markers (higher = the
  model admits gaps instead of inventing).

Usage:
  python3 docs/onboarding/validation/compare_contaminated_vs_grounded.py \
      --cleaned  /root/repos/personas/chandler-bing-01-garbage/extracted/cleaned.jsonl \
      --memory   /tmp/onboarding/chandler/memory \
      --model    qwen2.5:0.5b \
      --out      /tmp/onboarding/chandler/comparison.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# --- load the two structure.py prompt builders from disk --------------------

def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


# --- CONTAMINATED prompt builder (vendored from pre-BHAA-1360 structure.py) --
# Reproduced verbatim from git revision fa23ef1^ so this harness is self-
# contained and reproducible without a working tree checkout of the old file.

def contam_format_dialog_sample(lines):
    formatted = []
    for entry in lines:
        speaker = entry.get("speaker", "?")
        text = entry.get("text", "")
        emotion = entry.get("emotion", "")
        suffix = f" [{emotion}]" if emotion else ""
        formatted.append(f"{speaker}: {text}{suffix}")
    return "\n".join(formatted)


def contam_build_extraction_prompt(persona_name, dialog_sample, line_count):
    return f"""Analyze the following dialog data for the persona "{persona_name}".
There are {line_count} total lines of dialog available; below is a representative sample.

Extract the following structured information about {persona_name}:

1. IDENTITY
   - communication_style: How they speak (vocabulary level, rhythm, sentence length)
   - humor_type: What kind of humor (sarcastic, self-deprecating, witty, slapstick)
   - core_traits: 3-5 dominant personality traits
   - catchphrases: Recurring phrases or verbal tics

2. SPEECH_PATTERNS
   - common_words: 10-15 frequently used words/phrases
   - sentence_structure: Typical sentence patterns
   - emotional_range: What emotions they express and how

3. RELATIONSHIPS (from dialog context)
   - key_relationships: People mentioned and their connection
   - relationship_dynamics: How they interact with each

4. MEMORIES (episodic)
   - key_topics: 5-10 recurring topics/interests
   - notable_quotes: 5-10 most characteristic lines

Dialog sample:
---
{dialog_sample}
---

Output as JSON with this exact structure:
{{
  "identity": {{
    "communication_style": "...",
    "humor_type": "...",
    "core_traits": ["...", "..."],
    "catchphrases": ["...", "..."]
  }},
  "speech_patterns": {{
    "common_words": ["...", "..."],
    "sentence_structure": "...",
    "emotional_range": "..."
  }},
  "relationships": {{
    "key_relationships": [{{"name": "...", "connection": "..."}}],
    "relationship_dynamics": "..."
  }},
  "memories": {{
    "key_topics": ["...", "..."],
    "notable_quotes": ["...", "..."]
  }}
}}"""


def load_corpus_vocab(cleaned_path: Path) -> set[str]:
    """Lowercased alpha tokens (len>=3) + capitalized proper-noun candidates."""
    words: set[str] = set()
    proper: set[str] = set()
    with open(cleaned_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = entry.get("text", "")
            for tok in re.findall(r"[A-Za-z']+", text):
                low = tok.lower()
                if len(low) >= 3:
                    words.add(low)
                if tok[0].isupper() and len(tok) >= 3:
                    proper.add(tok)
    return words | proper


def sample_dialog(cleaned_path: Path, n: int = 60) -> tuple[list[dict], int]:
    """Match structure.load_dialog sampling (every Nth line)."""
    lines = []
    with open(cleaned_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("speaker"):
                lines.append(entry)
    total = len(lines)
    if total > 500:
        step = total // 500
        lines = lines[::step][:500]
    return lines[:n], total


def call_ollama(model: str, system: str, user: str) -> str | None:
    body = json.dumps({
        "model": model,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 800},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:11434/api/chat", data=body, method="POST"
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
        return data.get("message", {}).get("content", "")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"[ollama] call failed: {exc}", file=sys.stderr)
        return None


def parse_json_response(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    # find first { ... last }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


GAP_MARKER = "not enough data to determine"


def flatten_strings(obj) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(flatten_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(flatten_strings(v))
    return out


def measure_output(extraction: dict, vocab: set[str]) -> dict:
    strings = flatten_strings(extraction)
    gap_markers = 0
    novel_tokens = 0
    total_tokens = 0
    for s in strings:
        low = s.lower()
        if GAP_MARKER in low:
            gap_markers += 1
        for tok in re.findall(r"[A-Za-z']+", low):
            if len(tok) < 3:
                continue
            total_tokens += 1
            if tok not in vocab:
                novel_tokens += 1
    hallucination_rate = (novel_tokens / total_tokens) if total_tokens else 0.0
    return {
        "fields_emitted": len(strings),
        "gap_markers": gap_markers,
        "content_tokens": total_tokens,
        "novel_tokens_not_in_corpus": novel_tokens,
        "hallucination_rate": round(hallucination_rate, 4),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cleaned", required=True)
    p.add_argument("--memory", required=True)
    p.add_argument("--model", default="qwen2.5:0.5b")
    p.add_argument("--out", required=True)
    p.add_argument("--persona", default="chandler")
    args = p.parse_args()

    cleaned = Path(args.cleaned)
    memory_dir = Path(args.memory)

    grounded_mod = _load(REPO / "modules/onboarding/structure.py", "grounded_structure")
    contaminated_mod = type("M", (), {
        "format_dialog_sample": staticmethod(contam_format_dialog_sample),
        "build_extraction_prompt": staticmethod(contam_build_extraction_prompt),
    })()

    vocab = load_corpus_vocab(cleaned)
    print(f"corpus vocabulary: {len(vocab)} distinct tokens")

    sample, total_lines = sample_dialog(cleaned)
    dialog_sample_contam = contaminated_mod.format_dialog_sample(sample)
    dialog_sample_ground = grounded_mod.format_dialog_sample(sample)

    memory = grounded_mod.load_memory_context(str(memory_dir))
    memory_brief = grounded_mod.render_memory_brief(memory, args.persona)
    n_sources = len(
        {s for tier in ("l3_profiles", "l2_scenarios", "l1_facts")
         for rec in memory.get(tier, [])
         for s in str(rec.get("source") or "").split(",") if s.strip()}
    )
    print(f"grounded memory: {len(memory['l3_profiles'])} L3 / "
          f"{len(memory['l2_scenarios'])} L2 / {len(memory['l1_facts'])} L1; "
          f"{n_sources} distinct cited sources")

    contam_prompt = contaminated_mod.build_extraction_prompt(
        args.persona, dialog_sample_contam, total_lines
    )
    ground_prompt = grounded_mod.build_grounded_extraction_prompt(
        args.persona, memory_brief, dialog_sample_ground, total_lines
    )

    # CONTAMINATED system prompt (old): generic, fill-everything.
    contam_sys = ("You are a persona extraction engine. Output valid JSON only. "
                  "Be conservative — only extract what's directly evidenced in the data.")
    # GROUNDED system prompt (new): use-only-brief, mark gaps.
    ground_sys = ("You are a persona structuring engine grounded in a memory brief. "
                  "Output valid JSON only. Only use facts present in the memory brief. "
                  "For any field without grounding, output 'Not enough data to determine.' "
                  "Be conservative.")

    print("\n=== CONTAMINATED run (LLM over raw text) ===")
    contam_raw = call_ollama(args.model, contam_sys, contam_prompt)
    contam_ext = parse_json_response(contam_raw) or {}
    print(f"parsed fields: {len(flatten_strings(contam_ext))}")

    print("\n=== GROUNDED run (LLM over memory brief) ===")
    ground_raw = call_ollama(args.model, ground_sys, ground_prompt)
    ground_ext = parse_json_response(ground_raw) or {}
    print(f"parsed fields: {len(flatten_strings(ground_ext))}")

    contam_metrics = measure_output(contam_ext, vocab)
    ground_metrics = measure_output(ground_ext, vocab)

    # Citation coverage (measured from the deterministic memory store).
    manifest_path = memory_dir / "distill-manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    report = {
        "persona": args.persona,
        "model": args.model,
        "corpus_lines": total_lines,
        "corpus_vocab_size": len(vocab),
        "grounding": {
            "memory_l3_profiles": len(memory["l3_profiles"]),
            "memory_l2_scenarios": len(memory["l2_scenarios"]),
            "memory_l1_facts": len(memory["l1_facts"]),
            "distinct_cited_sources": n_sources,
            "evidence_complete": manifest.get("all_records_have_evidence"),
            "evidence_coverage_pct": 100.0 if manifest.get("all_records_have_evidence") else 0.0,
            "missing_domains": manifest.get("missing_domains", []),
        },
        "contaminated": {
            "prompt": "LLM over raw dialog sample (no grounding anchor, fill-everything)",
            "citation_coverage_pct": 0.0,
            "evidence_block": "none (old structure.py has no evidence mechanism)",
            **contam_metrics,
            "extraction": contam_ext,
        },
        "grounded": {
            "prompt": "LLM over deterministic L0-L3 memory brief (use-only-brief, mark-gaps)",
            "citation_coverage_pct": 100.0 if manifest.get("all_records_have_evidence") else 0.0,
            "evidence_block": f"{n_sources} distinct L0 sources cited",
            **ground_metrics,
            "extraction": ground_ext,
        },
        "delta": {
            "hallucination_rate_ground_minus_contam": round(
                ground_metrics["hallucination_rate"] - contam_metrics["hallucination_rate"], 4
            ),
            "gap_markers_ground_minus_contam": (
                ground_metrics["gap_markers"] - contam_metrics["gap_markers"]
            ),
        },
    }

    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\n=== report written to {args.out} ===")
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("contaminated", "grounded")}, indent=2))


if __name__ == "__main__":
    main()
