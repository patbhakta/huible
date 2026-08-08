#!/usr/bin/env python3
"""
Huible Onboarding — Stage 2: CLEAN

Cleans and normalizes extracted dialog data.
- Deduplicates
- Removes platform artifacts
- Normalizes whitespace
- Filters noise (very short lines, stage directions)

Usage:
  python3 clean.py --input <input.jsonl> --output <output.jsonl>
"""

import json
import argparse
import sys
import re
from collections import OrderedDict


# Patterns to filter out
STAGE_DIRECTION = re.compile(r'^\[.*\]$|^\(.*\)$|^\<.*\>$')
NOISE_PATTERNS = [
    re.compile(r'^[A-Z\s]+$'),  # ALL CAPS (scene labels)
    re.compile(r'^(Scene|Scene:|Cut to|Fade)', re.IGNORECASE),
    re.compile(r'^\s*\(?[A-Z][a-z]+:\s*\)?$'),  # Speaker label without text
]

# Minimum meaningful line length
MIN_LENGTH = 3


def clean_text(text):
    """Clean a single line of dialog."""
    if not text:
        return None
    
    # Strip whitespace
    text = text.strip()
    
    # Remove leading speaker label if present
    text = re.sub(r'^[A-Z][a-z]+:\s*', '', text)
    
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    
    # Remove leading comma (from CSV parsing artifacts)
    text = text.lstrip(',').strip()
    
    return text if text else None


def is_noise(text):
    """Check if a line is noise (stage directions, scene labels, etc.)."""
    if not text or len(text) < MIN_LENGTH:
        return True
    
    if STAGE_DIRECTION.match(text):
        return True
    
    for pattern in NOISE_PATTERNS:
        if pattern.match(text):
            return True
    
    return False


def main():
    parser = argparse.ArgumentParser(description='Clean dialog data')
    parser.add_argument('--input', required=True, help='Input JSONL file')
    parser.add_argument('--output', required=True, help='Output JSONL file')
    
    args = parser.parse_args()
    
    seen = set()
    cleaned = []
    removed = 0
    
    with open(args.input) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                removed += 1
                continue
            
            text = clean_text(entry.get('text', ''))
            
            if not text or is_noise(text):
                removed += 1
                continue
            
            # Deduplicate
            key = text.lower()
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            
            entry['text'] = text
            cleaned.append(entry)
    
    # Write output
    import os
    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    with open(args.output, 'w') as f:
        for entry in cleaned:
            f.write(json.dumps(entry) + '\n')
    
    print(f"Input: {len(cleaned) + removed} lines")
    print(f"Output: {len(cleaned)} lines (removed {removed})")
    
    result = {
        "input_lines": len(cleaned) + removed,
        "output_lines": len(cleaned),
        "removed": removed,
    }
    print(f'::{json.dumps({"outputs": result})}::')


if __name__ == '__main__':
    main()
