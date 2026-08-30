#!/usr/bin/env python3
"""Deterministic statistics extraction — no LLM, no hallucination."""
import json, argparse, sys, os
from collections import Counter

STOPWORDS = set('the a an and or but in on at to for of is are was were be been being have has had do does did will would could should may might must can this that these those i you he she it we they me him her us them my your his its our their what which who whom whose where when why how all each every both few more most other some such no not only own same so than too very just but is it i\'m i\'ve i\'ll i\'d don\'t didn\'t can\'t won\'t that\'s there\'s he\'s she\'s it\'s they\'re we\'re you\'re'.split())

def main():
    parser = argparse.ArgumentParser(description='Deterministic stats from dialog')
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    
    lines = []
    with open(args.input) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: lines.append(json.loads(line))
            except: continue
    
    texts = [e['text'] for e in lines if e.get('text')]
    total = len(texts)
    
    # Word frequency (filtered)
    word_freq = Counter()
    for text in texts:
        for word in text.lower().split():
            w = word.strip('.,!?";\'()[]{}')
            if w and w not in STOPWORDS and len(w) > 2:
                word_freq[w] += 1
    
    # Bigrams
    bigram_freq = Counter()
    for text in texts:
        words = [w.strip('.,!?";\'()[]{}').lower() for w in text.split()]
        for i in range(len(words)-1):
            if words[i] not in STOPWORDS and words[i+1] not in STOPWORDS:
                if len(words[i]) > 2 and len(words[i+1]) > 2:
                    bigram_freq[f"{words[i]} {words[i+1]}"] += 1
    
    # Metrics
    exclamations = sum(1 for t in texts if '!' in t)
    questions = sum(1 for t in texts if '?' in t)
    avg_len = sum(len(t.split()) for t in texts) // max(total, 1)
    
    # Catchphrase candidates (phrases appearing 5+ times)
    catchphrases = [(w, c) for w, c in word_freq.most_common(50) if c >= 10]
    
    # Character-length register (HU-2231 reply budgets): char percentiles of
    # these lines. provision_persona.py copies this block onto the persona
    # record and the engine derives the persona's reply budget (token cap +
    # directive anchors) from it. Linear interpolation, matching
    # statistics.quantiles(method='inclusive') in huible.persona.length.
    char_lens = sorted(len(t) for t in texts)
    
    def _pct(sorted_vals, p):
        if not sorted_vals:
            return None
        k = (len(sorted_vals) - 1) * (p / 100.0)
        f = int(k)
        c = min(f + 1, len(sorted_vals) - 1)
        if f == c:
            return sorted_vals[f]
        return round(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))
    
    stats = {
        "total_lines": total,
        "avg_words_per_line": avg_len,
        "exclamation_ratio": round(exclamations * 100 / max(total, 1), 1),
        "question_ratio": round(questions * 100 / max(total, 1), 1),
        "top_words": word_freq.most_common(20),
        "top_bigrams": bigram_freq.most_common(15),
        "frequent_words_10plus": catchphrases[:15],
        "char_length": {
            "median_chars": _pct(char_lens, 50),
            "p75_chars": _pct(char_lens, 75),
            "p90_chars": _pct(char_lens, 90),
            "sample_lines": len(char_lens),
        },
    }
    
    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    with open(args.output, 'w') as f:
        json.dump(stats, f, indent=2)
    
    print(f"Lines: {total}")
    print(f"Avg words/line: {avg_len}")
    print(f"Length register: {stats['char_length']}")
    print(f"Exclamations: {stats['exclamation_ratio']}%")
    print(f"Questions: {stats['question_ratio']}%")
    print(f"Top words: {[w[0] for w in stats['top_words'][:10]]}")
    print(f'::{json.dumps({"outputs": stats})}::')

if __name__ == '__main__':
    main()
