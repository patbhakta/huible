#!/usr/bin/env python3
"""V2 Curate — B3 abuse-log exclusion (persona-vaults.md v1.8 §1.B3, §3.V2).

Official semantics (Librarian doc, HU-2446): the V2 Curate stage executes the
abuse-log exclusion (B3), purging abusive, contemptuous, or belittling
patterns across all persona classes, so "argues, doesn't budge, deflects"
never reproduces abuse. Style stats (stats.py) must be computed on the
SURVIVING corpus, so this filter runs BEFORE stats in the vault pipeline.

Doctrine (matches the safety-guard house pattern):
- Deterministic, no LLM, idempotent (same input -> same output).
- Replace-only-on-concrete-fire: a record is excluded only on an explicit
  lexicon hit (word-boundary regex). Banter, sarcasm, and hedge vocabulary
  are NOT excluded — sitcom-class (A) voices keep their register; only
  dehumanization, demeaning imperatives, and slur-level abuse are purged.
- Provenance is never destroyed: raw intake (01-raw) stays immutable; this
  filter operates downstream (02-clean -> curated) and writes every excluded
  record + reason to an audit JSONL sidecar.
- Lexicon expansion is vault governance (Librarian), not a runtime knob:
  extend via --patterns-file (JSON), never by editing thresholds ad hoc.

Usage:
  python3 scripts/vault_abuse_exclusion.py \
      --input  /tmp/pv/<persona>/cleaned.jsonl \
      --output /tmp/pv/<persona>/curated.jsonl \
      --audit  /tmp/pv/<persona>/abuse-exclusions.jsonl \
      --report /tmp/pv/<persona>/abuse-exclusion-report.json
"""

import argparse
import json
import os
import re

# Core lexicon — conservative by design (only concrete abuse survives the
# word-boundary match). Categories mirror B3: abuse, contempt, belittling.
CORE_PATTERNS = {
    "dehumanization": [
        r"\byou(?:'re|\s+are)\s+(?:such\s+|so\s+|a\s+|an\s+)*(?:worthless|useless|pathetic|disgusting|garbage|trash|subhuman|a\s+waste\s+of\s+(?:space|air|skin))\b",
        r"\b(?:garbage|trash|worthless|useless)\s+(?:human|person|being)\b",
    ],
    "demeaning_imperative": [
        r"\bshut\s+the\s+hell\s+up\b",
        r"\bdo\s+(?:the\s+world|everyone)\s+a\s+favor\s+and\b",
        r"\bnobody\s+(?:loves|cares\s+about|would\s+miss)\s+you\b",
        r"\bgo\s+(?:die|jump\s+off\s+a)\b",
    ],
    "slur_level": [
        r"\bretard(?:ed|s)?\b",
        r"\bf[a4]gg?o?t?s?\b",
        r"\bn[i1]gg?(?:er|a)s?\b",
        r"\bk[i1]k[e3]s?\b",
        r"\btr[a4]nn?y\b",
    ],
    "contempt_identity": [
        r"\b(?:women|men|people\s+like\s+(?:you|that))\s+(?:are|'re)\s+(?:all\s+)?(?:worthless|garbage|subhuman|a\s+disease)\b",
    ],
}


def compile_patterns(extra_file=None):
    """Compile the core lexicon plus any governance-provided extensions."""
    by_category = {k: list(v) for k, v in CORE_PATTERNS.items()}
    if extra_file:
        with open(extra_file) as f:
            extra = json.load(f)
        for category, patterns in extra.items():
            by_category.setdefault(category, []).extend(patterns)
    compiled = []
    for category, patterns in by_category.items():
        for pat in patterns:
            compiled.append((category, re.compile(pat, re.IGNORECASE)))
    return compiled


def match_record(text, compiled):
    """Return (category, matched_span) for the first concrete hit, else None."""
    for category, rx in compiled:
        m = rx.search(text)
        if m:
            return category, m.group(0)
    return None


def main():
    parser = argparse.ArgumentParser(
        description="B3 abuse-log exclusion for the V2 Curate vault stage"
    )
    parser.add_argument("--input", required=True, help="Input JSONL (clean.py output)")
    parser.add_argument("--output", required=True, help="Curated JSONL output")
    parser.add_argument("--audit", required=True, help="Audit JSONL of excluded records")
    parser.add_argument("--report", required=True, help="JSON exclusion report")
    parser.add_argument(
        "--patterns-file",
        default=None,
        help="Optional JSON {category: [regex,...]} governance extension of the lexicon",
    )
    args = parser.parse_args()

    compiled = compile_patterns(args.patterns_file)

    kept, excluded = [], []
    total = 0
    with open(args.input) as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            text = entry.get("text", "")
            hit = match_record(text, compiled)
            if hit:
                category, span = hit
                excluded.append(
                    {
                        "line_no": line_no,
                        "category": category,
                        "matched": span,
                        "record": entry,
                    }
                )
            else:
                kept.append(entry)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        for entry in kept:
            f.write(json.dumps(entry) + "\n")

    os.makedirs(os.path.dirname(args.audit) or ".", exist_ok=True)
    with open(args.audit, "w") as f:
        for exc in excluded:
            f.write(json.dumps(exc) + "\n")

    by_category = {}
    for exc in excluded:
        by_category[exc["category"]] = by_category.get(exc["category"], 0) + 1

    report = {
        "stage": "v2-curate-abuse-exclusion",
        "boundary": "B3",
        "input_records": total,
        "kept_records": len(kept),
        "excluded_records": len(excluded),
        "excluded_by_category": by_category,
        "lexicon_source": "core" if not args.patterns_file else "core+governance-file",
    }
    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w") as f:
        json.dump(report, f, indent=2)

    print(
        f"Input: {total} records | kept: {len(kept)} | excluded: {len(excluded)} "
        f"({by_category or 'no hits'})"
    )
    print(f'::{json.dumps({"outputs": report})}::')


if __name__ == "__main__":
    main()
