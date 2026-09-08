#!/usr/bin/env python3
"""Frontier OCR ceiling test (HU-2739 lane, boss directive 2026-09-08):
'let's try claude and chatgpt to compare with' — measure what the expensive
frontier vision models score on the same 4 torture pages, identical prompt,
identical scorer + GT, so the numbers are comparable with Flash 3.8 / OvisOCR2 / NaviDC.

Models: anthropic/claude-opus-5, anthropic/claude-sonnet-5,
        openai/gpt-6-astra, openai/gpt-5.4-image-2   (via OpenRouter)

Usage: python3 run_frontier_ocr_gate.py run|score
"""
import base64, json, re, sys, time, urllib.request
from pathlib import Path

BASE = Path("/root/repos/huible/experiments/ingestion-pdf")
PNG = BASE / "outputs" / "page_png"
OUTROOT = BASE / "outputs" / "frontier"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
OR_KEY = json.load(open("/root/.local/share/opencode/auth.json"))["openrouter"]["key"]

MODELS = [
    "anthropic/claude-opus-5",
    "anthropic/claude-sonnet-5",
    "openai/gpt-6-astra",
    "openai/gpt-5.4-image-2",
]

PROMPT = ("Extract all readable content from the image in natural human reading order and "
          "output the result as a single Markdown document. For charts or images, represent "
          "them using an HTML image tag: <img src=\"images/bbox_{left}_{top}_{right}_{bottom}.jpg\" />, "
          "where left, top, right, bottom are bounding box coordinates scaled to [0, 1000). "
          "Format formulas as LaTeX. Format tables as HTML: <table>...</table>. "
          "Transcribe all other text as standard Markdown. "
          "Preserve the original text without translation or paraphrasing.")

VLM_PAGE = {"scanned_formula": 0, "scanned_mixed": 0, "chart_table": 0, "real_mixed": 3}


def slug(m):
    return m.split("/")[-1].replace(":", "_")


def chat(model, page_png, timeout=600):
    b64 = base64.b64encode(page_png.read_bytes()).decode()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}],
        "max_tokens": 8192,
    }
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + OR_KEY, "Content-Type": "application/json"})
    t0 = time.time()
    with OPENER.open(req, timeout=timeout) as r:
        d = json.loads(r.read())
    if "choices" not in d:
        raise RuntimeError(str(d)[:300])
    out = d["choices"][0]["message"].get("content") or ""
    cost = d.get("usage", {}).get("cost", 0)
    return out, time.time() - t0, cost


def run():
    for model in MODELS:
        out_dir = OUTROOT / slug(model)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, pi in VLM_PAGE.items():
            f = out_dir / f"{name}.md"
            if f.exists():
                print(f"skip {model} {name}", flush=True)
                continue
            try:
                out, dt, cost = chat(model, PNG / f"{name}_p{pi}.png")
                f.write_text(out)
                print(f"[{slug(model)}] {name}: {dt:.1f}s {len(out)} chars ${cost:.4f} "
                      f"(tables={out.count('<table')} latex$={out.count('$')})", flush=True)
            except Exception as e:
                print(f"[{slug(model)}] {name} FAILED: {str(e)[:200]}", flush=True)
                time.sleep(3)


def norm_tokens(text):
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


def score():
    flash = {"chart_table": 0.696, "scanned_formula": 0.829, "scanned_mixed": 0.884, "real_mixed": 0.834}
    ovis = {"chart_table": 0.297, "scanned_formula": 0.879, "scanned_mixed": 0.829, "real_mixed": 0.879}
    all_results = {}
    for model in MODELS:
        out_dir = OUTROOT / slug(model)
        res = {}
        for name, pi in VLM_PAGE.items():
            f = out_dir / f"{name}.md"
            if not f.exists():
                continue
            gt = (BASE / "ground_truth" / f"{name}.pdf.gt.txt").read_text()
            gts = [norm_tokens(p) for p in gt.split("\f")]
            res[name] = round(f1(norm_tokens(f.read_text()), gts[pi]), 3)
        if res:
            all_results[model] = res
            (out_dir / "scores.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(all_results, indent=1))
    (OUTROOT / "frontier_scores.json").write_text(json.dumps(all_results, indent=1))
    print("\nLANE-BY-LANE (flash / ovisocr2 for reference):")
    for model, res in all_results.items():
        for k in ("chart_table", "scanned_formula", "scanned_mixed", "real_mixed"):
            if k in res:
                print(f"  {slug(model):22s} {k:16s} {res[k]:.3f}  (flash {flash[k]:.3f} / ovis {ovis[k]:.3f})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    OUTROOT.mkdir(parents=True, exist_ok=True)
    run() if cmd == "run" else score()
