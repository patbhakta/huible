#!/usr/bin/env python3
"""Gate-audit re-score: does HTML-tag token pollution explain the chart_table collapse?

Hypothesis: our prompt says 'Format tables as HTML' -> models emit <table><tr><td>...
The F1 tokenizer keeps [a-z0-9$%.,/+\\-\\s]; '<'/'>' become spaces, so EVERY tag leaves
junk tokens (table, tr, td, img, src...) that crush precision. Flash 3.8's 0.696 baseline
was produced under a markdown-format prompt (no tag pollution).

Test: identical scorer but strip HTML tags from predictions first. Re-score frontier,
ovisocr2, and (for fairness) Flash's own saved outputs with the same stripping.
"""
import json, re
from pathlib import Path

BASE = Path("/root/repos/huible/experiments/ingestion-pdf")
GT = BASE / "ground_truth"
VLM_PAGE = {"scanned_formula": 0, "scanned_mixed": 0, "chart_table": 0, "real_mixed": 3}
PROMPT_HTML = {  # models run under OUR prompt (asks for HTML tables)
    "ovisocr2": BASE / "outputs/ovisocr2",
    "opus5": BASE / "outputs/frontier/claude-opus-5",
    "sonnet5": BASE / "outputs/frontier/claude-sonnet-5",
    "astra": BASE / "outputs/frontier/gpt-6-astra",
    "image2": BASE / "outputs/frontier/gpt-5.4-image-2",
}
FLASH_DIRS = [BASE / "outputs/vlm_gemini", BASE / "outputs/vlm_gemini_or"]


def norm_tokens(text, strip_html):
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


def find_flash_dir():
    for d in FLASH_DIRS:
        if (d / "chart_table.md").exists():
            return d
    return None


def main():
    flash_dir = find_flash_dir()
    print(f"flash outputs dir: {flash_dir}")
    rows = {}
    for label, d in sorted(PROMPT_HTML.items()):
        raw, stripped = {}, {}
        for name, pi in VLM_PAGE.items():
            f = d / f"{name}.md"
            if not f.exists():
                continue
            gts = [norm_tokens(p, False) for p in (GT / f"{name}.pdf.gt.txt").read_text().split("\f")]
            raw[name] = round(f1(norm_tokens(f.read_text(), False), gts[pi]), 3)
            stripped[name] = round(f1(norm_tokens(f.read_text(), True), gts[pi]), 3)
        rows[label] = (raw, stripped)
    if flash_dir:
        raw, stripped = {}, {}
        for name, pi in VLM_PAGE.items():
            f = flash_dir / f"{name}.md"
            if not f.exists():
                continue
            gts = [norm_tokens(p, False) for p in (GT / f"{name}.pdf.gt.txt").read_text().split("\f")]
            raw[name] = round(f1(norm_tokens(f.read_text(), False), gts[pi]), 3)
            stripped[name] = round(f1(norm_tokens(f.read_text(), True), gts[pi]), 3)
        rows["flash3.8(saved)"] = (raw, stripped)
    print(f"\n{'model':16s} {'lane':15s} {'raw':>6s} {'no-html':>7s}  delta")
    for label, (raw, stripped) in rows.items():
        for k in ("chart_table", "scanned_formula", "scanned_mixed", "real_mixed"):
            if k in raw:
                print(f"{label:16s} {k:15s} {raw[k]:6.3f} {stripped[k]:7.3f}  {'+' if stripped[k]>=raw[k] else ''}{stripped[k]-raw[k]:.3f}")
    out = {label: {"raw": r, "tags_stripped": s} for label, (r, s) in rows.items()}
    (BASE / "outputs" / "gate_audit_rescore.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
