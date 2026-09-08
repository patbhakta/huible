#!/usr/bin/env python3
"""Find the TRUE page mapping for chart_table: score each model's output against
EVERY page of chart_table.pdf.gt.txt. The pairing that maximizes Flash's F1 should
reproduce the official 0.696 baseline and reveal the correct page index."""
import re
from pathlib import Path

BASE = Path("/root/repos/huible/experiments/ingestion-pdf")
GT = BASE / "ground_truth" / "chart_table.pdf.gt.txt"
CANDS = {
    "flash3.8": BASE / "outputs/vlm_gemini/chart_table.md",
    "ovisocr2": BASE / "outputs/ovisocr2/chart_table.md",
    "opus5": BASE / "outputs/frontier/claude-opus-5/chart_table.md",
    "astra": BASE / "outputs/frontier/gpt-6-astra/chart_table.md",
}
VLM_PAGE = {"scanned_formula": 0, "scanned_mixed": 0, "chart_table": 0, "real_mixed": 3}


def norm_tokens(text, strip_html=True):
    if strip_html:
        text = re.sub(r"<[^>]+>", " ", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9$%.,/+\-\s]", " ", text)
    return [t for t in text.split() if t]


def f1(pred, gt):
    from collections import Counter
    pc, gc = Counter(pred), Counter(gt)
    ov = sum((pc & gc).values())
    if not ov:
        return 0.0
    p = ov / max(1, len(pred)); r = ov / max(1, len(gt))
    return 2 * p * r / (p + r)


pages = [norm_tokens(p) for p in GT.read_text().split("\f")]
print(f"chart_table GT has {len(pages)} pages; sizes: {[len(p) for p in pages]}")
for label, f in CANDS.items():
    if not f.exists():
        continue
    pred = norm_tokens(f.read_text())
    scores = [round(f1(pred, g), 3) for g in pages]
    best = max(range(len(pages)), key=lambda i: scores[i])
    print(f"{label:10s} per-GT-page F1: {scores}  -> best page {best} ({scores[best]:.3f})")
