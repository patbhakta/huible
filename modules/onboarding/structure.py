#!/usr/bin/env python3
"""
Huible Onboarding — Stage 4: STRUCTURE (grounded)

Structures the persona into OKF v0.2 markdown documents. Grounded by the
deterministic distillation memory produced by ``huible.distillation.cli``
(stage S3): the LLM sees the L3 profiles (durable rules / current states),
L2 scenario summaries and L1 facts as its grounding context, NOT raw LLM over
raw text. This closes the contamination vector called out in
``onboarding-architecture-final.md``.

Inputs:
  --memory-dir   Distillation memory store (from huible.distillation.cli, S3).
                 Required for grounded structuring. The LLM prompt is built
                 from the L3/L2/L1 records.
  --input        Cleaned dialog JSONL (optional). Used only to sample notable
                 raw quotes for the sample-dialog doc; not used as the LLM's
                 primary source.
  --output-dir   Vault directory for the OKF persona-profile.md / sample-dialog.md.
  --persona-name Persona name.

The LLM is a tool, not the architecture. Swap models by changing --model.

Usage (matches the corrected huible-onboard flow §2a):
  python3 structure.py \\
      --memory-dir /tmp/onboarding/chandler/memory \\
      --input      /tmp/onboarding/chandler/cleaned.jsonl \\
      --output-dir /root/repos/brain/Huible/write/personas/chandler \\
      --persona-name chandler
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
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


# --------------------------------------------------------------------------
# Identity anchor (BHAA-1364: counter cross-character drift)
# --------------------------------------------------------------------------

# Sentence-initial capitalized words that are NOT proper names of other
# characters (common sentence starters / generic tokens). These are excluded
# when mining the corpus for "other named entities".
_NON_NAME_CAPITALIZED = {
    "The", "A", "An", "And", "But", "Or", "So", "If", "When", "While",
    "Because", "Although", "Though", "Unless", "Since", "Before", "After",
    "What", "Where", "Why", "Who", "How", "Which", "That", "This", "These",
    "Those", "There", "Here", "It", "Its", "He", "She", "They", "We", "Us",
    "My", "Your", "His", "Her", "Their", "Our", "You", "Me", "Him", "Them",
    "I", "Im", "Id", "Ill", "Ive", "Could", "Would", "Should", "Will",
    "Can", "May", "Might", "Must", "Do", "Does", "Did", "Is", "Are", "Was",
    "Were", "Has", "Have", "Had", "Nobody", "Nothing", "Never", "Always",
    "Actually", "Really", "Maybe", "Okay", "Ok", "Oh", "Ah", "Um", "Uh",
    "Yeah", "Yes", "No", "Well", "Look", "Listen", "Wait", "Come", "Go",
    "Let", "Lets", "Dont", "Cant", "Wont", "Hes", "Shes", "Theyre",
    "Youre", "Thats", "Hows", "Whats", "Yknow", "Geez", "Wow",
    "God", "Jesus", "Whoa", "Hey", "Hi", "Hello", "Bye", "Please", "Thanks",
}

_CAPITALIZED_TOKEN_RE = re.compile(r"\b([A-Z][a-z]{2,})\b")
_SPEAKER_PREFIX_RE = re.compile(r"^[A-Z][\w'-]{1,30}:\s+")

# Stopwords + common verbs/adjectives filtered out of the frequency fallback so
# that salient nouns / character names surface to the top on lowercased
# corpora (the real Chandler data is fully lowercased, so capitalization
# cannot be the only signal).
_NAME_MINING_STOPWORDS = {
    # Determiners / prepositions / conjunctions.
    "the", "a", "an", "and", "but", "or", "so", "if", "as", "at", "by", "for",
    "in", "of", "on", "to", "with", "from", "into", "onto", "out", "up", "down",
    "off", "over", "about", "above", "below", "between", "through", "during",
    "after", "before", "again", "still", "even", "also", "just", "really",
    "actually", "very", "too", "then", "than", "there", "here", "all", "some",
    "any", "every", "both", "each", "few", "more", "most", "other", "such",
    "no", "not", "nor", "only", "own", "same", "that", "this", "these",
    "those", "what", "which", "who", "whom", "whose", "when", "where", "why",
    "how", "because", "while", "though", "although", "unless", "since",
    # Common verbs / fillers.
    "well", "okay", "ok", "yeah", "yes", "oh", "ah", "um", "uh", "huh", "wow",
    "hey", "look", "know", "get", "got", "go", "going", "gone", "come", "let",
    "see", "say", "said", "tell", "told", "want", "wanted", "think", "thought",
    "make", "makes", "made", "like", "liked", "likes", "love", "loved",
    "feel", "felt", "good", "new", "old", "big", "right", "now", "back",
    "way", "thing", "things", "time", "lot", "kind", "sort", "stuff", "guy",
    "guys", "man", "woman", "girl", "boy", "people", "one", "two", "three",
    "first", "last", "gonna", "wanna", "gotta",
    # Verb / function-word forms (contractions already excluded by the no
    # apostrophe tokenizer; these cover the bare forms).
    "was", "were", "been", "being", "are", "werent", "had", "has", "have",
    "having", "does", "did", "done", "doing", "can", "could", "should",
    "would", "may", "might", "must", "shall", "put", "take", "took", "taken",
    "give", "gave", "given", "went", "goes", "try", "tried", "mean", "meant",
    "call", "called", "play", "played", "ask", "asked", "talk", "talking",
    "looking", "getting", "telling", "wants", "needs", "saw", "seen", "found",
    "guess", "maybe", "thanks", "thank", "hello", "bye", "nope", "yep",
    "alright", "wait", "listen", "stop", "move", "stay", "leave", "left",
    "came",
    # Pronouns / determiners (frequent but never names).
    "you", "your", "yours", "her", "him", "his", "their", "them", "us", "our",
    "ours", "my", "mine", "me", "she", "he", "it", "its", "they", "we", "i",
    "yall", "dont",
    # Fragments left by apostrophe-splitting of contractions.
    "don", "didn", "won", "isn", "aren", "wasn", "weren", "couldn", "wouldn",
    "shouldn", "hasn", "haven", "hadn", "aint", "im", "ive", "id", "ill",
    "youre", "theyre", "yo", "em",
}


def _mine_other_named_entities(memory, persona_name, max_facts=2500):
    """Deterministically surface OTHER named characters/entities in the corpus.

    Two complementary signals (corpora may be properly cased OR fully
    lowercased — the real Chandler data is lowercased, so capitalization cannot
    be the only signal):

    1. Capitalized proper-noun candidates in L1 fact bodies (excluding the
       persona and generic sentence starters).
    2. A frequency fallback over non-stopword tokens so recurring salient
       nouns/character names surface on lowercased corpora.

    Returns a list of names (most informative first). This is what the
    structuring model needs to avoid mis-attributing the persona's quotes to a
    mentioned character (the 3b proxy attributed Chandler quotes to "Joey").
    """
    persona_tokens = {t.lower() for t in re.split(r"\W+", persona_name) if t}
    persona_tokens |= {"pat"}
    cap_counts: Counter[str] = Counter()
    freq_counts: Counter[str] = Counter()
    for fact in memory.get("l1_facts", [])[:max_facts]:
        body = fact.get("_body", "")
        # Strip a leading ``Speaker:`` prefix so it is not counted as a name.
        stripped = _SPEAKER_PREFIX_RE.sub("", body)
        for tok in _CAPITALIZED_TOKEN_RE.findall(stripped):
            if tok in _NON_NAME_CAPITALIZED:
                continue
            if tok.lower() in persona_tokens:
                continue
            cap_counts[tok] += 1
        # Frequency fallback over lowercase tokens (handles lowercased corpora).
        # No-apostrophe tokenizer: contractions (it's, don't, i'm) are dropped
        # automatically since names rarely contain apostrophes.
        for raw in re.findall(r"[a-z]{3,}", stripped.lower()):
            if raw in persona_tokens:
                continue
            if raw in _NAME_MINING_STOPWORDS:
                continue
            freq_counts[raw] += 1

    ranked: list[str] = []
    seen: set[str] = set()
    # Prefer capitalized proper nouns when the corpus is cased.
    for name, _ in cap_counts.most_common(8):
        key = name.lower()
        if key not in seen:
            ranked.append(name)
            seen.add(key)
    # Supplement with the most frequent non-stopword tokens (lowercased corpora).
    for name, count in freq_counts.most_common(20):
        if len(ranked) >= 10:
            break
        if count < 3:
            break
        if name in seen:
            continue
        ranked.append(name)
        seen.add(name)
    return ranked


def build_identity_anchor(memory, persona_name):
    """Render a crisp identity anchor that leads the grounded memory brief.

    Tells the structuring model exactly who the persona is and that any other
    named entities are OTHER people/things — not the persona — so the model
    does not drift across mentioned characters.
    """
    others = _mine_other_named_entities(memory, persona_name)
    lines = [
        f"- This brief describes **{persona_name}**. Every record below is "
        f"{persona_name}'s own statement or a fact about {persona_name}.",
        f"- Structuring rule: attribute all traits, quotes, preferences, and "
        f"states to **{persona_name}** — never to any other name.",
    ]
    if others:
        lines.append(
            "- Other named entities appear in the corpus only as people/things "
            f"{persona_name} mentions. They are NOT {persona_name}; treat them as "
            f"relationships or topics: " + ", ".join(others) + "."
        )
    else:
        lines.append(
            f"- No other recurring named entities were detected; the corpus is "
            f"focused on {persona_name}."
        )
    return lines


def render_memory_brief(memory, persona_name):
    """Render a compact, grounded brief of the memory for the LLM prompt.

    The LLM is instructed to use ONLY this brief; anything absent is a gap to
    mark "Not enough data to determine." rather than invent. The brief LEADS
    with a crisp identity anchor so structuring models do not drift across
    mentioned characters (BHAA-1364).
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
        "## Identity anchor",
        *build_identity_anchor(memory, persona_name),
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


def build_grounded_extraction_prompt(persona_name, memory_brief, dialog_sample, line_count):
    """Build the LLM extraction prompt GROUNDED in the distillation memory."""
    dialog_section = ""
    if dialog_sample:
        dialog_section = f"""
## Raw dialog sample (for notable quotes ONLY — do not derive traits from this)
---
{dialog_sample}
---
"""
    return f"""You are structuring the persona "{persona_name}" into OKF v0.2 fields.

The PRIMARY source of truth is the grounded memory brief below, produced by a
deterministic distillation pipeline over the dialog corpus ({line_count} lines).
Extract the OKF structure using ONLY the memory brief. Where the brief is silent,
write "Not enough data to determine." — do not extrapolate.
{dialog_section}
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
# OKF document writing (carries evidence back to memory source)
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


def write_okf_docs(output_dir, persona_name, extraction, line_count, memory):
    """Write structured OKF v0.2 markdown documents."""
    os.makedirs(output_dir, exist_ok=True)

    now = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')

    # 1. Persona Profile
    profile_path = os.path.join(output_dir, 'persona-profile.md')
    identity = extraction.get('identity', {})
    speech = extraction.get('speech_patterns', {})
    rels = extraction.get('relationships', {})

    with open(profile_path, 'w') as f:
        f.write(f"""---
type: Person Profile
title: "{persona_name.title()} — Persona Profile"
description: "Extracted persona profile for {persona_name.title()}"
status: draft
generated:
  by: process:huible-onboarding
  at: {now}
tags: [huible, persona, {persona_name.lower().replace(' ', '-')}]
sources:
  - id: distillation-memory
    resource: "internal://huible/onboarding/{persona_name}/memory"
    title: "Grounded L0-L3 distillation memory"
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

    # 2. Notable Quotes / Sample Dialog
    quotes_path = os.path.join(output_dir, 'sample-dialog.md')
    memories = extraction.get('memories', {})

    with open(quotes_path, 'w') as f:
        f.write(f"""---
type: Reference
title: "{persona_name.title()} — Sample Dialog & Quotes"
description: "Notable quotes and key topics from dialog corpus"
status: draft
generated:
  by: process:huible-onboarding
  at: {now}
tags: [huible, persona, {persona_name.lower().replace(' ', '-')}, dialog]
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

    return [profile_path, quotes_path]


def main():
    parser = argparse.ArgumentParser(
        description='Structure persona into OKF docs (grounded by distillation memory).'
    )
    parser.add_argument(
        '--memory-dir', required=True,
        help='Distillation memory dir (huible.distillation.cli output, S3). '
             'Required grounding source.',
    )
    parser.add_argument(
        '--input', help='Cleaned JSONL file (optional; used for notable quotes only).'
    )
    parser.add_argument('--output-dir', required=True, help='Output directory for OKF docs')
    parser.add_argument('--persona-name', required=True, help='Persona name')
    parser.add_argument('--model', default='google/gemini-3-flash-preview', help='LLM model')

    args = parser.parse_args()

    api_key = os.environ.get('OPENROUTER_API_KEY')

    # Load grounded memory (the primary source of truth).
    memory = load_memory_context(args.memory_dir)
    memory_brief = render_memory_brief(memory, args.persona_name)
    n_profiles = len(memory.get("l3_profiles", []))
    print(f"Loaded grounded memory: {n_profiles} L3 profiles, "
          f"{len(memory.get('l2_scenarios', []))} L2 scenarios, "
          f"{len(memory.get('l1_facts', []))} L1 facts from {args.memory_dir}")

    # Optionally sample raw dialog for notable quotes.
    dialog_sample = ""
    line_count = 0
    if args.input and os.path.exists(args.input):
        lines = load_dialog(args.input)
        line_count = len(lines)
        dialog_sample = format_dialog_sample(lines)
        print(f"Loaded {line_count} dialog lines for notable-quote sampling.")

    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    prompt = build_grounded_extraction_prompt(
        args.persona_name, memory_brief, dialog_sample, line_count
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

    docs = write_okf_docs(args.output_dir, args.persona_name, extraction, line_count, memory)

    print("\nOKF documents written:")
    for doc in docs:
        print(f"  {doc}")

    result = {
        "persona": args.persona_name,
        "dialog_lines": line_count,
        "memory_l3_profiles": n_profiles,
        "docs_written": len(docs),
        "model_used": args.model,
        "grounded": True,
    }
    print(f'::{json.dumps({"outputs": result})}::')


if __name__ == '__main__':
    main()
