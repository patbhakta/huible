#!/usr/bin/env python3
"""
Huible Onboarding — Stage 3: STRUCTURE

Uses Gemini Flash 3.6 (via OpenRouter) to extract persona traits from
cleaned dialog data and structure them into OKF v0.2 markdown documents.

The LLM is a tool, not the architecture. Swap models by changing one line.

Usage:
  python3 structure.py --input <cleaned.jsonl> --output-dir <dir> --persona-name chandler
"""

import json
import argparse
import os
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone


def load_dialog(input_path, max_lines=500):
    """Load a sample of dialog lines for persona extraction."""
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
            except:
                continue
    
    # Sample diverse lines (every Nth line for variety)
    if len(lines) > max_lines:
        step = len(lines) // max_lines
        lines = lines[::step][:max_lines]
    
    return lines


def format_dialog_sample(lines):
    """Format dialog lines for the LLM prompt."""
    formatted = []
    for entry in lines:
        speaker = entry.get('speaker', '?')
        text = entry.get('text', '')
        emotion = entry.get('emotion', '')
        suffix = f" [{emotion}]" if emotion else ""
        formatted.append(f"{speaker}: {text}{suffix}")
    return "\n".join(formatted)


def call_gemini(prompt, api_key, model="google/gemini-3-flash-preview"):
    """Call Gemini via OpenRouter."""
    url = "https://openrouter.ai/api/v1/chat/completions"
    
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a persona extraction engine. Output valid JSON only. Be conservative — only extract what's directly evidenced in the data."},
            {"role": "user", "content": prompt}
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


def build_extraction_prompt(persona_name, dialog_sample, line_count):
    """Build the LLM extraction prompt."""
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


def write_okf_docs(output_dir, persona_name, extraction, line_count):
    """Write structured OKF v0.2 markdown documents."""
    os.makedirs(output_dir, exist_ok=True)
    
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    
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
  - id: dialog-corpus
    resource: "internal://huible/onboarding/{persona_name}"
    title: "{line_count} dialog lines extracted and cleaned"
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
        
        f.write(f"""
## Catchphrases

""")
        for phrase in identity.get('catchphrases', []):
            f.write(f'- "{phrase}"\n')
        
        f.write(f"""
## Speech Patterns

**Common words/phrases:**
""")
        for word in speech.get('common_words', []):
            f.write(f"- {word}\n")
        
        f.write(f"""
**Sentence structure:** {speech.get('sentence_structure', 'N/A')}

**Emotional range:** {speech.get('emotional_range', 'N/A')}

## Relationships

""")
        for rel in rels.get('key_relationships', []):
            f.write(f"- **{rel.get('name', '?')}**: {rel.get('connection', '?')}\n")
        
        f.write(f"""
**Dynamics:** {rels.get('relationship_dynamics', 'N/A')}
""")
    
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
        
        f.write(f"""
## Notable Quotes

""")
        for quote in memories.get('notable_quotes', []):
            f.write(f"> {quote}\n\n")
    
    return [profile_path, quotes_path]


def main():
    parser = argparse.ArgumentParser(description='Structure dialog data into OKF docs')
    parser.add_argument('--input', required=True, help='Cleaned JSONL file')
    parser.add_argument('--output-dir', required=True, help='Output directory for OKF docs')
    parser.add_argument('--persona-name', required=True, help='Persona name')
    parser.add_argument('--model', default='google/gemini-3-flash-preview', help='LLM model')
    
    args = parser.parse_args()
    
    api_key = os.environ.get('OPENROUTER_API_KEY')
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    
    # Load dialog
    lines = load_dialog(args.input)
    print(f"Loaded {len(lines)} dialog lines for {args.persona_name}")
    
    # Format sample for LLM
    dialog_sample = format_dialog_sample(lines)
    prompt = build_extraction_prompt(args.persona_name, dialog_sample, len(lines))
    
    print(f"Calling {args.model} for persona extraction...")
    
    # Call LLM
    response = call_gemini(prompt, api_key, args.model)
    if not response:
        print("ERROR: LLM call failed", file=sys.stderr)
        sys.exit(1)
    
    # Parse JSON from response (handle markdown code blocks)
    response = response.strip()
    if response.startswith('```'):
        response = response.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    
    try:
        extraction = json.loads(response)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse LLM output as JSON: {e}", file=sys.stderr)
        print(f"Response: {response[:500]}", file=sys.stderr)
        sys.exit(1)
    
    # Write OKF docs
    docs = write_okf_docs(args.output_dir, args.persona_name, extraction, len(lines))
    
    print(f"\nOKF documents written:")
    for doc in docs:
        print(f"  {doc}")
    
    result = {
        "persona": args.persona_name,
        "dialog_lines": len(lines),
        "docs_written": len(docs),
        "model_used": args.model,
    }
    print(f'::{json.dumps({"outputs": result})}::')


if __name__ == '__main__':
    main()
