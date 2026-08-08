#!/usr/bin/env python3
"""
Huible Onboarding — Stage 1: EXTRACT

Reads raw CSV files and extracts dialog lines for the target persona.
Handles multiple CSV formats (person/line, text/emotion, etc.)

Usage:
  python3 extract.py --input-dir <dir> --persona chandler --output <output.jsonl>
"""

import csv
import json
import argparse
import os
import sys
from pathlib import Path
from collections import defaultdict


def detect_csv_format(filepath):
    """Auto-detect CSV column format."""
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.reader(f)
        header = next(reader)
        header = [h.strip().lower() for h in header]
    
    if 'person' in header and 'line' in header:
        return 'person_line'
    elif 'text' in header and 'emotion' in header:
        return 'text_emotion'
    elif 'speaker' in header and 'utterance' in header:
        return 'speaker_utterance'
    elif 'character' in header and 'dialogue' in header:
        return 'character_dialogue'
    else:
        return 'unknown'


def extract_person_line(filepath, persona):
    """Extract from person,line format (friends-v2.csv)."""
    lines = []
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            person = (row.get('person') or '').strip().lower()
            line = (row.get('line') or '').strip()
            if person == persona.lower() and line:
                lines.append({
                    'speaker': person,
                    'text': line,
                    'source': os.path.basename(filepath),
                })
    return lines


def extract_text_emotion(filepath):
    """Extract from text,emotion format (friends_cleaned.csv)."""
    lines = []
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            text = (row.get('text') or '').strip()
            emotion = (row.get('emotion') or '').strip()
            if text:
                lines.append({
                    'speaker': None,  # No speaker attribution in this format
                    'text': text,
                    'emotion': emotion if emotion in ['neutral', 'joy', 'sadness', 'anger', 'fear', 'surprise', 'disgust', 'non-neutral'] else None,
                    'source': os.path.basename(filepath),
                })
    return lines


def extract_any_format(filepath, persona=None):
    """Auto-detect and extract."""
    fmt = detect_csv_format(filepath)
    
    if fmt == 'person_line':
        return extract_person_line(filepath, persona or ''), fmt
    elif fmt == 'text_emotion':
        return extract_text_emotion(filepath), fmt
    else:
        # Fallback: try all columns
        lines = []
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Find the text column
                for k, v in row.items():
                    if v and len(v) > 5 and k.lower() not in ['person', 'speaker', 'character']:
                        lines.append({
                            'speaker': row.get('person', row.get('speaker', '')).strip().lower() or None,
                            'text': v.strip(),
                            'source': os.path.basename(filepath),
                        })
                        break
        return lines, 'fallback'


def main():
    parser = argparse.ArgumentParser(description='Extract dialog data for persona onboarding')
    parser.add_argument('--input-dir', required=True, help='Directory containing raw CSV files')
    parser.add_argument('--persona', default='chandler', help='Target persona name')
    parser.add_argument('--output', required=True, help='Output JSONL file path')
    
    args = parser.parse_args()
    
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"ERROR: Input directory not found: {input_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Find all CSV files
    csv_files = list(input_dir.glob('*.csv'))
    if not csv_files:
        print(f"ERROR: No CSV files found in {input_dir}", file=sys.stderr)
        sys.exit(1)
    
    print(f"Found {len(csv_files)} CSV files")
    
    all_lines = []
    stats = defaultdict(int)
    
    for csv_file in csv_files:
        fmt = detect_csv_format(csv_file)
        print(f"  {csv_file.name}: format={fmt}")
        
        lines, detected_fmt = extract_any_format(csv_file, args.persona)
        
        # Filter to persona if speaker column exists
        if args.persona and any(l.get('speaker') for l in lines):
            persona_lines = [l for l in lines if l.get('speaker') == args.persona.lower()]
            all_lines.extend(persona_lines)
            stats[f'{csv_file.name}_{args.persona}'] = len(persona_lines)
            print(f"    Extracted {len(persona_lines)} {args.persona} lines")
        else:
            all_lines.extend(lines)
            stats[csv_file.name] = len(lines)
            print(f"    Extracted {len(lines)} lines (no speaker filter)")
    
    # Write output
    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    with open(args.output, 'w') as f:
        for line in all_lines:
            f.write(json.dumps(line) + '\n')
    
    print(f"\nTotal lines extracted: {len(all_lines)}")
    print(f"Output: {args.output}")
    
    # Output stats for Kestra
    result = {
        "total_lines": len(all_lines),
        "files_processed": len(csv_files),
        "stats": dict(stats),
        "persona": args.persona,
    }
    print(f'::{json.dumps({"outputs": result})}::')


if __name__ == '__main__':
    main()
