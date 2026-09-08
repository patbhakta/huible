#!/usr/bin/env python3
"""OvisOCR2 gate run vs PageIndex Flash 3.8 baseline (HU-2745 OCR lane; sibling of HU-2739).

Single-pass end-to-end page parsing (no layout/read stages) against LM Studio
(pat-w11pc GTX 1080) over Tailscale. Model: ovisocr2-sota-20260823.
Official OvisOCR2 prompt (ATH-MaaS/OvisOCR2 README). Scored with the IDENTICAL
scorer + GT + page mapping used for the Flash 3.8 baselines and the NaviDC run.

Usage:
  python3 run_ovisocr2_gate.py run
  python3 run_ovisocr2_gate.py score
"""
import base64, json, re, sys, time, urllib.request
from pathlib import Path

BASE = Path("/root/repos/huible/experiments/ingestion-pdf")
LMSTUDIO = "http://100.83.231.16:1234"
MODEL = "ovisocr2-sota-20260823"
TOKEN_FILE = Path("/root/.secrets/lmstudio/api_token")
OUT = BASE / "outputs" / "ovisocr2"
PNG = BASE / "outputs" / "page_png"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PROMPT = ("Extract all readable content from the image in natural human reading order and "
          "output the result as a single Markdown document. For charts or images, represent "
          "them using an HTML image tag: <img src=\"images/bbox_{left}_{top}_{right}_{bottom}.jpg\" />, "
          "where left, top, right, bottom are bounding box coordinates scaled to [0, 1000). "
          "Format formulas as LaTeX. Format tables as HTML: <table>...</table>. "
          "Transcribe all other text as standard Markdown. "
          "Preserve the original text without translation or paraphrasing.")

VLM_PAGE = {"scanned_formula": 0, "scanned_mixed": 0, "chart_table": 0, "real_mixed": 3}


def chat(page_png: Path, label: str, timeout=900):
    b64 = base64.b64encode(page_png.read_bytes()).decode()
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
                {"type": "text", "text": PROMPT},
            ]},
        ],
        "temperature": 0, "top_p": 0.01, "top_k": 1,
        "presence_penalty": 1.0, "frequency_penalty": 0.05,
        "max_tokens": 6144,
    }
    req = urllib.request.Request(LMSTUDIO + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {TOKEN_FILE.read_text().strip()}",
                 "Content-Type": "application/json"})
    t0 = time.time()
    with OPENER.open(req, timeout=timeout) as r:
        body = json.loads(r.read())
    out = body["choices"][0]["message"]["content"] or ""
    return out, time.time() - t0


def process_page(name, pi):
    png = PNG / f"{name}_p{pi}.png"
    out, dt = chat(png, name)
    (OUT / f"{name}.md").write_text(out)
    print(f"== {name}: {dt:.1f}s, {len(out)} chars (tables={out.count('<table')} "
          f"latex$={out.count('$')} bbox={out.count('bbox_')})", flush=True)


def score():
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

    results, lat = {}, {}
    for name, pi in VLM_PAGE.items():
        gt = (BASE / "ground_truth" / f"{name}.pdf.gt.txt").read_text()
        gts = [norm_tokens(p) for p in gt.split("\f")]
        pred = (OUT / f"{name}.md").read_text()
        results[name] = round(f1(norm_tokens(pred), gts[pi]), 3)
    print(json.dumps(results, indent=2))
    (OUT / "ovisocr2_gate_scores.json").write_text(json.dumps(results, indent=2))
    flash = {"chart_table": 0.696, "scanned_formula": 0.829, "scanned_mixed": 0.884, "real_mixed": 0.834}
    navidc = {"chart_table": 0.234, "scanned_formula": 0.503, "scanned_mixed": 0.774, "real_mixed": 0.712}
    print("\nvs Flash 3.8 (pipeline OCR incumbent) and NaviDC (rejected):")
    for k in results:
        v = results[k]
        mark = "WINS" if v >= flash[k] else ("beats-navidc" if v > navidc[k] else "loses")
        print(f"  {k}: ovisocr2 {v:.3f} vs flash {flash[k]:.3f} / navidc {navidc[k]:.3f} -> {mark}")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        for name, pi in VLM_PAGE.items():
            if (OUT / f"{name}.md").exists():
                print(f"skip {name} (exists)", flush=True)
                continue
            try:
                process_page(name, pi)
            except Exception as e:
                print(f"  !! {name} FAILED: {e}", flush=True)
    elif cmd == "score":
        score()
