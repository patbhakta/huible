"""Scoring helpers shared with the regression smoke tests.

``norm_tokens``/``token_f1`` mirror ``experiments/ingestion-pdf/scripts/score.py``
(the measured HU-2692 baseline) and ``wer`` mirrors
``experiments/ingestion-media/scripts/score_wer.py`` (the measured HU-2697
baseline): bag-of-tokens F1, case/punct-normalized; word-level WER with
Levenshtein distance.
"""

from __future__ import annotations

import re
from collections import Counter


def norm_tokens(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9$%.,/+\-\s]", " ", text)
    return [t for t in text.split() if t]


def token_f1(pred: str, gt: str) -> float:
    pc, gc = Counter(norm_tokens(pred)), Counter(norm_tokens(gt))
    overlap = sum((pc & gc).values())
    if not overlap:
        return 0.0
    p = overlap / max(1, len(norm_tokens(pred)))
    r = overlap / max(1, len(norm_tokens(gt)))
    return 2 * p * r / (p + r)


def word_norm(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9\s]", "", text.lower()).split()


def wer(hypothesis: str, reference: str) -> float:
    """Word error rate (lowercase, punctuation-stripped, Levenshtein)."""
    h, r = word_norm(hypothesis), word_norm(reference)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(r) + 1))
    for i, hw in enumerate(h, 1):
        cur = [i]
        for j, rw in enumerate(r, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (hw != rw)))
        prev = cur
    return prev[-1] / len(r)
