#!/usr/bin/env python3
"""NaviDC-OCR gate run vs PageIndex Flash 3.8 baseline (HU-2739).

Two-stage NaviDC pipeline against LM Studio (pat-w11pc GTX 1080) over Tailscale:
  stage 1 layout:  prompt "\nAnalyze the image layout."      (image resized 1008x1008 bicubic)
  stage 2 read:    per-block crop, type-specific prompt from DEFAULT_PROMPTS
                   (text -> LaTeX/OTSL for formula/table), official sampling
                   (temp 0, top_p 0.01, top_k 1, presence 1.0, freq 0.05),
                   min_image_edge upscaling of small crops.
Scored with the IDENTICAL scorer + GT + page mapping used for the Flash 3.8
0.696 chart_table baseline (experiments/ingestion-pdf/scripts/score.py).

Usage:
  python3 run_navidc_gate.py run    # process all 4 torture pages
  python3 run_navidc_gate.py score  # score vs GT (writes navidc_gate_scores.json)
"""
import base64, json, math, re, sys, time, urllib.request
from pathlib import Path
from PIL import Image

BASE = Path("/root/repos/huible/experiments/ingestion-pdf")
LMSTUDIO = "http://100.83.231.16:1234"
TOKEN_FILE = Path("/root/.secrets/lmstudio/api_token")
OUT = BASE / "outputs" / "navidc"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# official DEFAULT_PROMPTS (NaviOCR/vlm_utils/NaviOCR_client.py)
PROMPTS = {
    "text": "\nPlease output the text content from the image.",
    "table": "\nThis is the image of a table. Please output the table in OTSL format.",
    "formula": "\nPlease write out the expression of the formula in the image using LaTeX format.",
    "code": "\nThis image contains a code snippet, please output the parsing result."
            if False else "\nThe image contains a code snippet, please output the parsing result.",
    "char": "\nThis is a scientific figure. Please extract the table implied by this figure.",
    "default": "\nPlease output the text content from the image.",
    "title": "\nPlease output the text content from the image.",
    "header": "\nPlease output the text content from the image.",
    "footer": "\nPlease output the text content from the image.",
    "image_caption": "\nPlease output the text content from the image.",
    "list": "\nPlease output the text content from the image.",
}
# block types to skip reading entirely (non-content)
SKIP_TYPES = {"image"}
LAYOUT_PROMPT = "\nAnalyze the image layout."
VLM_PAGE = {"scanned_formula": 0, "scanned_mixed": 0, "chart_table": 0, "real_mixed": 3}


def b64_url(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode()


def chat(content, label, timeout=600):
    payload = {
        "model": "navidc-ocr",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": content},
        ],
        "temperature": 0, "top_p": 0.01, "top_k": 1,
        "presence_penalty": 1.0, "frequency_penalty": 0.05,
        "max_tokens": 3072,
    }
    req = urllib.request.Request(LMSTUDIO + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {TOKEN_FILE.read_text().strip()}",
                 "Content-Type": "application/json"})
    t0 = time.time()
    with OPENER.open(req, timeout=timeout) as r:
        body = json.loads(r.read())
    out = body["choices"][0]["message"]["content"]
    print(f"  [{label}] {time.time()-t0:.1f}s {len(out)} chars", flush=True)
    return out


def parse_boxes(layout: str):
    """-> list of (label, x1,y1,x2,y2 in 0-1000)"""
    boxes = []
    for m in re.finditer(r'<box:([\d\s]+)><label:(\w+)>(?:<(up|right|down|left)>)?', layout):
        coords = list(map(int, m.group(1).split()))
        if len(coords) >= 4:
            xs, ys = coords[0::2], coords[1::2]
            boxes.append((m.group(2), [min(xs), min(ys), max(xs), max(ys)]))
    return boxes


def to_png_bytes(im):
    import io
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def resize_by_need(im, min_edge=640, max_ratio=4.0):
    w, h = im.size
    if max(w, h) / max(1, min(w, h)) > max_ratio:
        if w > h:
            nw, nh = w, math.ceil(w / max_ratio)
        else:
            nw, nh = math.ceil(h / max_ratio), h
        canvas = Image.new(im.mode, (nw, nh), (255, 255, 255))
        canvas.paste(im, ((nw - w) // 2, (nh - h) // 2))
        im = canvas
    if min(im.size) < min_edge:
        s = min_edge / min(im.size)
        im = im.resize((math.ceil(im.width * s), math.ceil(im.height * s)), Image.Resampling.BICUBIC)
    return im


def rasterize_page(pdf: Path, page_idx: int, dpi=200) -> bytes:
    import fitz
    doc = fitz.open(pdf)
    pm = doc[page_idx].get_pixmap(dpi=dpi)
    return pm.tobytes("png")


def process_page(name: str, page_idx: int):
    OUT.mkdir(parents=True, exist_ok=True)
    raw = rasterize_page(BASE / "samples" / f"{name}.pdf", page_idx)
    import io
    page = Image.open(io.BytesIO(raw)).convert("RGB")
    W, H = page.size
    # stage 1: official layout sizing 1008x1008 (reuse cached layout if present)
    lay_cache = OUT / f"{name}.layout.txt"
    if lay_cache.exists():
        layout = lay_cache.read_text()
        print(f"  {name}: layout cached", flush=True)
    else:
        lay = page.resize((1008, 1008), Image.Resampling.BICUBIC)
        layout = chat([
            {"type": "image_url", "image_url": {"url": b64_url(to_png_bytes(lay))}},
            {"type": "text", "text": LAYOUT_PROMPT},
        ], f"{name} p{page_idx} layout")
        lay_cache.write_text(layout)

    boxes = parse_boxes(layout)
    print(f"  {name}: {len(boxes)} blocks", flush=True)
    parts, order = [], []
    for label, (x1, y1, x2, y2) in boxes:
        if label in SKIP_TYPES:
            continue
        crop = page.crop((x1*W//1000, y1*H//1000, x2*W//1000, y2*H//1000))
        if crop.width < 8 or crop.height < 8:
            continue
        crop = resize_by_need(crop)
        prompt = PROMPTS.get(label, PROMPTS["default"])
        try:
            text = chat([
                {"type": "image_url", "image_url": {"url": b64_url(to_png_bytes(crop))}},
                {"type": "text", "text": prompt},
            ], f"{name} {label}")
        except Exception as e:
            print(f"  !! {label} read failed: {e}", flush=True)
            text = ""
        parts.append({"type": label, "bbox": [x1, y1, x2, y2], "text": text})
        order.append(label)
    md = "\n\n".join(p["text"] for p in parts if p["text"].strip())
    (OUT / f"{name}.md").write_text(md)
    (OUT / f"{name}.blocks.json").write_text(json.dumps(parts, indent=2))
    print(f"== {name}: {len(md)} chars markdown ==", flush=True)


def score():
    sys.path.insert(0, str(BASE / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("score", BASE / "scripts" / "score.py")
    # reimplement scorer inline (score.py runs at import)
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

    results = {}
    for name, pi in VLM_PAGE.items():
        gt = (BASE / "ground_truth" / f"{name}.pdf.gt.txt").read_text()
        gts = [norm_tokens(p) for p in gt.split("\f")]
        vlm = (OUT / f"{name}.md").read_text()
        results[name] = round(f1(norm_tokens(vlm), gts[pi]), 3)
    print(json.dumps(results, indent=2))
    (OUT / "navidc_gate_scores.json").write_text(json.dumps(results, indent=2))
    flash = {"chart_table": 0.696, "scanned_formula": 0.829, "scanned_mixed": 0.884, "real_mixed": 0.834}
    print("\nvs Flash 3.8 (gemini_openrouter_page):")
    for k in results:
        mark = "WINS" if results[k] >= flash[k] else "loses"
        print(f"  {k}: navidc {results[k]:.3f} vs flash {flash[k]:.3f} -> {mark}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        for name, pi in VLM_PAGE.items():
            if (OUT / f"{name}.md").exists():
                print(f"skip {name} (exists)", flush=True)
                continue
            print(f"[{name}]", flush=True)
            process_page(name, pi)
    elif cmd == "score":
        score()
