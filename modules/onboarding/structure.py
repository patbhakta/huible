#!/usr/bin/env python3
"""
Huible Onboarding — Stage 4: STRUCTURE (grounded)

Structures the persona into markdown documents using the flat, Librarian-governed
OKF frontmatter standard (two fields: ``tags`` + ``updated``). The retired OKF
v0.2 spec (``type``/``title``/``status``/``generated`` and its strict validator)
was replaced by this flat model vault-wide; provenance is carried in the document
body instead of frontmatter. Grounded by the deterministic distillation memory
produced by ``huible.distillation.cli`` (stage S3): the LLM sees the L3 profiles
(durable rules / current states), L2 scenario summaries and L1 facts as its
grounding context, NOT raw LLM over raw text. This closes the contamination
vector called out in ``onboarding-architecture-final.md``.

Inputs:
  --memory-dir   Distillation memory store (from huible.distillation.cli, S3).
                 Required for grounded structuring. The LLM prompt is built
                 from the L3/L2/L1 records.
  --input        Cleaned dialog JSONL (optional). Used only to sample notable
                 raw quotes for the sample-dialog doc; not used as the LLM's
                 primary source.
  --output-dir   Persona vault directory for persona-profile.md / sample-dialog.md
                 (personas vault: /root/repos/personas/<persona>/<tier>/).
  --persona-name Persona name.

The LLM is a tool, not the architecture. Swap models by changing --model.

Usage (matches the corrected huible-onboard flow §2a):
  python3 structure.py \\
      --memory-dir /tmp/onboarding/chandler/memory \\
      --input      /tmp/onboarding/chandler/cleaned.jsonl \\
      --output-dir /root/repos/personas/chandler-bing/02-clean \\
      --persona-name chandler
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime

# --------------------------------------------------------------------------
# Memory store loading (grounding source of truth)
# --------------------------------------------------------------------------

def _import_store():
    """Import the MarkdownMemoryStore. Returns None if the package is absent."""
    try:
        from huible.distillation import MarkdownMemoryStore, Tier  # type: ignore

        return MarkdownMemoryStore, Tier
    except Exception as exc:  # pragma: no cover - environment-dependent
        print(f"[structure] huible.distillation unavailable: {exc}", file=sys.stderr)
        return None


def load_audio_profile(audio_path):
    """Load the persona-level vocal profile produced by audio.py (multimodal).

    Returns ``None`` when no path is given or the file is absent, so callers can
    treat the vocal/prosody facet as optional (text-only onboarding skips it).
    """
    if not audio_path or not os.path.exists(audio_path):
        return None
    try:
        with open(audio_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[structure] audio profile unreadable ({audio_path}): {exc}", file=sys.stderr)
        return None
    profile = data.get("persona_profile") if isinstance(data, dict) else None
    if not isinstance(profile, dict):
        return None
    return {
        "available": bool(profile.get("available")),
        "utterance_count": int(profile.get("utterance_count") or 0),
        "pitch": profile.get("pitch"),
        "intensity": profile.get("intensity"),
        "emotion_distribution": profile.get("emotion_distribution") or {},
        "dominant_emotion": profile.get("dominant_emotion"),
        "prosody_summary": profile.get("prosody_summary") or "No acoustic data available.",
        "source": data.get("source") if isinstance(data, dict) else None,
    }


def load_memory_context(memory_dir):
    """Load the grounded memory context from the distillation store.

    Returns a dict with l3_profiles, l2_scenarios, l1_facts (each a list of
    frontmatter dicts with a ``_body`` preview). Returns an empty context if
    the store cannot be read.
    """
    store_tuple = _import_store()
    if store_tuple is None or not os.path.isdir(memory_dir):
        return {"l3_profiles": [], "l2_scenarios": [], "l1_facts": [], "source": memory_dir}

    MarkdownMemoryStore, Tier = store_tuple
    store = MarkdownMemoryStore(memory_dir)
    return {
        "l3_profiles": store.list_records(Tier.L3),
        "l2_scenarios": store.list_records(Tier.L2),
        "l1_facts": store.list_records(Tier.L1),
        "source": memory_dir,
    }


def render_memory_brief(memory, persona_name):
    """Render a compact, grounded brief of the memory for the LLM prompt.

    The LLM is instructed to use ONLY this brief; anything absent is a gap to
    mark "Not enough data to determine." rather than invent.
    """
    rules = []
    states = []
    for prof in memory.get("l3_profiles", []):
        body = prof.get("_body", "").strip()
        mtype = prof.get("memory_type", "")
        key = prof.get("key", "")
        line = f"- [{mtype}] {key}: {body}" if key else f"- [{mtype}] {body}"
        if mtype == "durable_rule":
            rules.append(line)
        elif mtype == "current_state":
            states.append(line)

    scenarios = []
    for scen in memory.get("l2_scenarios", []):
        domain = scen.get("domain", "general")
        summary = scen.get("_body", "").strip().replace("\n", " ")
        scenarios.append(f"- ({domain}) {summary}")

    facts = []
    for fact in memory.get("l1_facts", [])[:40]:  # cap to keep prompt bounded
        body = fact.get("_body", "").strip()
        facts.append(f"- {body}")

    brief = [
        f"# Grounded memory brief for {persona_name}",
        "Use ONLY the following grounded records. Do not invent. For any facet "
        "with no supporting record below, answer 'Not enough data to determine.'",
        "",
        "## Durable rules (preferences/habits)",
    ]
    brief.extend(rules or ["- (none)"])
    brief.append("")
    brief.append("## Current states")
    brief.extend(states or ["- (none)"])
    brief.append("")
    brief.append("## Scenario summaries")
    brief.extend(scenarios or ["- (none)"])
    brief.append("")
    brief.append("## Atomic facts (sample)")
    brief.extend(facts or ["- (none)"])
    return "\n".join(brief)


# --------------------------------------------------------------------------
# Dialog sampling (optional — for raw notable quotes only)
# --------------------------------------------------------------------------

def load_dialog(input_path, max_lines=500):
    """Load a sample of dialog lines for notable-quote extraction."""
    lines = []
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get('speaker'):
                    lines.append(entry)
            except Exception:
                continue

    if len(lines) > max_lines:
        step = len(lines) // max_lines
        lines = lines[::step][:max_lines]

    return lines


def format_dialog_sample(lines, max_lines=60):
    """Format a small dialog sample for the LLM prompt (quotes only)."""
    sample = lines[:max_lines]
    formatted = []
    for entry in sample:
        speaker = entry.get('speaker', '?')
        text = entry.get('text', '')
        formatted.append(f"{speaker}: {text}")
    return "\n".join(formatted)


# --------------------------------------------------------------------------
# LLM call
# --------------------------------------------------------------------------

def call_gemini(prompt, api_key, model="google/gemini-3-flash-preview"):
    """Call Gemini via OpenRouter."""
    url = "https://openrouter.ai/api/v1/chat/completions"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a persona structuring engine grounded in a memory "
                    "brief. Output valid JSON only. Only use facts present in "
                    "the memory brief. For any field without grounding, output "
                    "'Not enough data to determine.' Be conservative."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 4000,
        "temperature": 0.3,
    }

    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Authorization', f'Bearer {api_key}')
    req.add_header('Content-Type', 'application/json')

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            return result['choices'][0]['message']['content']
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        print(f"ERROR: OpenRouter returned {e.code}: {error_body[:300]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return None


def build_grounded_extraction_prompt(
    persona_name, memory_brief, dialog_sample, line_count, audio_profile=None
):
    """Build the LLM extraction prompt GROUNDED in the distillation memory."""
    dialog_section = ""
    if dialog_sample:
        dialog_section = f"""
## Raw dialog sample (for notable quotes ONLY — do not derive traits from this)
---
{dialog_sample}
---
"""
    audio_section = ""
    if audio_profile and audio_profile.get("available"):
        dist = audio_profile.get("emotion_distribution") or {}
        emo_lines = ", ".join(f"{e}={round(p * 100)}%" for e, p in list(dist.items())[:6])
        pitch = audio_profile.get("pitch") or {}
        intensity = audio_profile.get("intensity") or {}
        pitch_line = f"Pitch mean/std: {pitch.get('mean')}/{pitch.get('std')}"
        intensity_line = f"Intensity mean/std: {intensity.get('mean')}/{intensity.get('std')}"
        audio_section = f"""
## Acoustic / prosodic grounding (multimodal — vocal evidence ONLY)
These are deterministic per-utterance acoustic aggregates ({audio_profile.get('utterance_count')}
utterances). Use them ONLY to characterize the vocal/prosody facet below; do not
infer non-vocal traits from them.
- Prosody summary: {audio_profile.get('prosody_summary')}
- Emotion mix: {emo_lines or 'n/a'}
- {pitch_line}  {intensity_line}
"""
    else:
        audio_section = """
## Acoustic / prosodic grounding
No acoustic data available — populate vocal_patterns fields with "Not enough
data to determine."
"""
    return f"""You are structuring the persona "{persona_name}" into persona profile fields.

The PRIMARY source of truth is the grounded memory brief below, produced by a
deterministic distillation pipeline over the dialog corpus ({line_count} lines).
Extract the OKF structure using ONLY the memory brief. Where the brief is silent,
write "Not enough data to determine." — do not extrapolate.
{dialog_section}
{audio_section}
{memory_brief}

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
  "vocal_patterns": {{
    "prosody": "...",
    "pitch_tendency": "...",
    "dominant_vocal_emotion": "...",
    "vocal_markers": ["...", "..."]
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


# --------------------------------------------------------------------------
# Persona document writing (carries evidence back to memory source)
# --------------------------------------------------------------------------

def _evidence_block(memory):
    """Render a 'Grounding & evidence' section citing memory source ids."""
    sources = []
    seen = set()
    for tier_key in ("l3_profiles", "l2_scenarios", "l1_facts"):
        for rec in memory.get(tier_key, []):
            src = str(rec.get("source") or rec.get("evidence_sources") or "")
            if not src:
                continue
            for s in src.split(","):
                s = s.strip()
                if s and s not in seen:
                    seen.add(s)
                    sources.append(s)
    if not sources:
        return ""
    lines = ["", "## Grounding & evidence", ""]
    lines.append("Structured from distillation memory `" + str(memory.get("source", "")) + "`.")
    lines.append("Evidence links to raw L0 sources:")
    for s in sources[:20]:
        lines.append(f"- `{s}`")
    return "\n".join(lines) + "\n"


def _provenance_footer(persona_name, generated_at, audio_profile=None):
    """Render a body provenance footer.

    The retired OKF v0.2 frontmatter carried ``generated`` and ``sources`` as
    YAML. Under the flat Librarian-governed model only ``tags`` + ``updated``
    live in frontmatter, so provenance is recorded here in the document body.
    """
    slug = persona_name.lower().replace(' ', '-')
    lines = [
        "",
        "## Provenance",
        "",
        "- Generated by: `process:huible-onboarding`",
        f"- Generated at: {generated_at}",
        f"- Persona: `{slug}`",
        "- Sources:",
        f"  - `distillation-memory` — Grounded L0-L3 distillation memory "
        f"(`internal://huible/onboarding/{persona_name}/memory`)",
    ]
    if audio_profile and audio_profile.get("source"):
        lines.append(
            f"  - `acoustic-features` — Per-utterance acoustic/prosodic features "
            f"(`internal://huible/onboarding/{persona_name}/audio`)"
        )
    return "\n".join(lines) + "\n"


def write_persona_docs(
    output_dir, persona_name, extraction, line_count, memory, audio_profile=None
):
    """Write structured persona markdown documents.

    Frontmatter uses the flat Librarian-governed OKF standard (``tags`` +
    ``updated`` only). The retired OKF v0.2 provenance fields (``generated``,
    ``sources``) are carried in a body ``## Provenance`` footer instead.
    """
    os.makedirs(output_dir, exist_ok=True)

    now = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    today = now[:10]  # vault `updated` convention is date-only (YYYY-MM-DD).
    slug = persona_name.lower().replace(' ', '-')

    # 1. Persona Profile
    profile_path = os.path.join(output_dir, 'persona-profile.md')
    identity = extraction.get('identity', {})
    speech = extraction.get('speech_patterns', {})
    rels = extraction.get('relationships', {})
    vocal = extraction.get('vocal_patterns', {})

    with open(profile_path, 'w') as f:
        f.write(f"""---
tags: [huible, persona, {slug}]
updated: {today}
---

# {persona_name.title()} — Persona Profile

## Communication Style

{identity.get('communication_style', 'Not enough data to determine.')}

## Humor Type

{identity.get('humor_type', 'Not enough data to determine.')}

## Core Traits

""")
        for trait in identity.get('core_traits', []):
            f.write(f"- {trait}\n")
        if not identity.get('core_traits'):
            f.write("- Not enough data to determine.\n")

        f.write("""
## Catchphrases

""")
        for phrase in identity.get('catchphrases', []):
            f.write(f'- "{phrase}"\n')
        if not identity.get('catchphrases'):
            f.write("- Not enough data to determine.\n")

        f.write("""
## Speech Patterns

**Common words/phrases:**
""")
        for word in speech.get('common_words', []):
            f.write(f"- {word}\n")
        if not speech.get('common_words'):
            f.write("- Not enough data to determine.\n")

        f.write(f"""
**Sentence structure:** {speech.get('sentence_structure', 'N/A')}

**Emotional range:** {speech.get('emotional_range', 'N/A')}

## Vocal Patterns & Prosody

""")

        # Vocal/prosody section (multimodal, BHAA-1375). Falls back to
        # "Not enough data to determine." when no acoustic grounding exists.
        not_enough = "Not enough data to determine."
        if audio_profile and audio_profile.get("available"):
            prosody_default = audio_profile.get("prosody_summary", "N/A")
            prosody_val = vocal.get("prosody", prosody_default)
            f.write(f"**Prosody:** {prosody_val}\n\n")
        else:
            f.write(f"**Prosody:** {vocal.get('prosody', not_enough)}\n\n")
        f.write(f"**Pitch tendency:** {vocal.get('pitch_tendency', not_enough)}\n\n")
        dom_default = (
            audio_profile.get("dominant_emotion")
            if audio_profile
            else None
        ) or not_enough
        f.write(
            f"**Dominant vocal emotion:** "
            f"{vocal.get('dominant_vocal_emotion', dom_default)}\n\n"
        )
        f.write("**Vocal markers:**\n\n")
        for marker in vocal.get('vocal_markers', []):
            f.write(f"- {marker}\n")
        if not vocal.get('vocal_markers'):
            f.write("- Not enough data to determine.\n")

        f.write("""
## Relationships

""")
        for rel in rels.get('key_relationships', []):
            f.write(f"- **{rel.get('name', '?')}**: {rel.get('connection', '?')}\n")
        if not rels.get('key_relationships'):
            f.write("- Not enough data to determine.\n")

        f.write(f"""
**Dynamics:** {rels.get('relationship_dynamics', 'N/A')}
""")
        f.write(_evidence_block(memory))
        f.write(_provenance_footer(persona_name, now, audio_profile))

    # 2. Notable Quotes / Sample Dialog
    quotes_path = os.path.join(output_dir, 'sample-dialog.md')
    memories = extraction.get('memories', {})

    with open(quotes_path, 'w') as f:
        f.write(f"""---
tags: [huible, persona, {slug}, dialog]
updated: {today}
---

# {persona_name.title()} — Sample Dialog & Quotes

## Key Topics

""")
        for topic in memories.get('key_topics', []):
            f.write(f"- {topic}\n")
        if not memories.get('key_topics'):
            f.write("- Not enough data to determine.\n")

        f.write("""
## Notable Quotes

""")
        any_quote = False
        for quote in memories.get('notable_quotes', []):
            if str(quote).strip():
                any_quote = True
                f.write(f"> {quote}\n\n")
        if not any_quote:
            f.write("> Not enough data to determine.\n\n")
        f.write(_provenance_footer(persona_name, now, audio_profile=None))

    return [profile_path, quotes_path]


def main():
    parser = argparse.ArgumentParser(
        description='Structure persona into markdown docs (grounded by distillation memory).'
    )
    parser.add_argument(
        '--memory-dir', required=True,
        help='Distillation memory dir (huible.distillation.cli output, S3). '
             'Required grounding source.',
    )
    parser.add_argument(
        '--input', help='Cleaned JSONL file (optional; used for notable quotes only).'
    )
    parser.add_argument('--output-dir', required=True, help='Output directory for persona docs')
    parser.add_argument('--persona-name', required=True, help='Persona name')
    parser.add_argument(
        '--audio', help='Optional audio_features.json (from audio.py) for vocal/prosody grounding.'
    )
    parser.add_argument('--model', default='google/gemini-3-flash-preview', help='LLM model')
    parser.add_argument(
        '--no-llm', action='store_true',
        help='Deterministic smoke-test mode: build the persona doc skeleton from '
             'distillation memory only (no LLM call, zero API spend). All LLM-'
             'authored fields are honest "Not enough data to determine." gaps.',
    )

    args = parser.parse_args()

    api_key = os.environ.get('OPENROUTER_API_KEY')

    # Load grounded memory (the primary source of truth).
    memory = load_memory_context(args.memory_dir)
    memory_brief = render_memory_brief(memory, args.persona_name)
    n_profiles = len(memory.get("l3_profiles", []))
    print(f"Loaded grounded memory: {n_profiles} L3 profiles, "
          f"{len(memory.get('l2_scenarios', []))} L2 scenarios, "
          f"{len(memory.get('l1_facts', []))} L1 facts from {args.memory_dir}")

    # Optional multimodal vocal/prosody grounding (audio.py output).
    audio_profile = load_audio_profile(args.audio)
    if audio_profile and audio_profile.get("available"):
        print(f"Loaded acoustic profile: {audio_profile['utterance_count']} utterances — "
              f"{audio_profile['prosody_summary']}")
    elif args.audio:
        print(f"[structure] acoustic profile at {args.audio} unavailable; "
              f"vocal/prosody facet will be marked as a gap.", file=sys.stderr)

    # Optionally sample raw dialog for notable quotes.
    dialog_sample = ""
    line_count = 0
    if args.input and os.path.exists(args.input):
        lines = load_dialog(args.input)
        line_count = len(lines)
        dialog_sample = format_dialog_sample(lines)
        print(f"Loaded {line_count} dialog lines for notable-quote sampling.")

    if args.no_llm:
        print("[structure] --no-llm: skipping LLM call (deterministic smoke mode).")
        extraction = {}
    else:
        if not api_key:
            print("ERROR: OPENROUTER_API_KEY not set", file=sys.stderr)
            sys.exit(1)

        prompt = build_grounded_extraction_prompt(
            args.persona_name, memory_brief, dialog_sample, line_count, audio_profile
        )

        print(f"Calling {args.model} for grounded persona structuring...")

        response = call_gemini(prompt, api_key, args.model)
        if not response:
            print("ERROR: LLM call failed", file=sys.stderr)
            sys.exit(1)

        response = response.strip()
        if response.startswith('```'):
            response = response.split('\n', 1)[1].rsplit('```', 1)[0].strip()

        try:
            extraction = json.loads(response)
        except json.JSONDecodeError as e:
            print(f"ERROR: Failed to parse LLM output as JSON: {e}", file=sys.stderr)
            print(f"Response: {response[:500]}", file=sys.stderr)
            sys.exit(1)

    docs = write_persona_docs(
        args.output_dir, args.persona_name, extraction, line_count, memory, audio_profile
    )

    print("\nPersona documents written:")
    for doc in docs:
        print(f"  {doc}")

    result = {
        "persona": args.persona_name,
        "dialog_lines": line_count,
        "memory_l3_profiles": n_profiles,
        "docs_written": len(docs),
        "model_used": None if args.no_llm else args.model,
        "grounded": True,
        "multimodal": bool(audio_profile and audio_profile.get("available")),
    }
    if args.no_llm:
        result["mode"] = "deterministic-smoke"
    print(f'::{json.dumps({"outputs": result})}::')


if __name__ == '__main__':
    main()
