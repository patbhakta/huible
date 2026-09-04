"""Score extraction outputs vs ground truth: token-level F1 + notes."""
import json
import pathlib
import re

BASE = pathlib.Path(__file__).resolve().parent.parent


def norm_tokens(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9$%.,/+\-\s]", " ", text)
    return [t for t in text.split() if t]


def f1(pred: list[str], gt: list[str]) -> float:
    from collections import Counter

    pc, gc = Counter(pred), Counter(gt)
    overlap = sum((pc & gc).values())
    if not overlap:
        return 0.0
    p = overlap / max(1, len(pred))
    r = overlap / max(1, len(gt))
    return 2 * p * r / (p + r)


def gt_pages(name: str) -> list[list[str]]:
    gt = (BASE / "ground_truth" / f"{name}.pdf.gt.txt").read_text()
    return [norm_tokens(p) for p in gt.split("\f")]


# VLM only processed one page per sample; map sample -> GT page index used
VLM_PAGE = {"scanned_formula": 0, "scanned_mixed": 0, "chart_table": 0, "real_mixed": 3}

results = {}
for name in ["real_mixed", "scanned_formula", "scanned_mixed", "chart_table"]:
    gts = gt_pages(name)
    row = {}
    # pymupdf baseline: full doc
    pm = (BASE / "outputs" / "pymupdf" / f"{name}.txt").read_text()
    row["pymupdf_full"] = round(f1(norm_tokens(pm), gts[0] if len(gts) == 1 else sum(gts, [])), 3)
    # docling: full doc
    dl = (BASE / "outputs" / "docling" / f"{name}.md").read_text()
    gt_all = gts[0] if len(gts) == 1 else sum(gts, [])
    row["docling_full"] = round(f1(norm_tokens(dl), gt_all), 3)
    row["docling_formula_not_decoded"] = dl.count("formula-not-decoded")
    # vlm: single page
    vlm_path = BASE / "outputs" / "vlm" / f"{name}.md"
    if vlm_path.exists():
        vlm = vlm_path.read_text()
        row["vlm_page"] = round(f1(norm_tokens(vlm), gts[VLM_PAGE[name]]), 3)
    results[name] = row
print(json.dumps(results, indent=2))
(BASE / "outputs" / "scores.json").write_text(json.dumps(results, indent=2))
