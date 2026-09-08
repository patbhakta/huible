#!/usr/bin/env python3
"""Rerun all models on the FIXED chart_table page + report the corrected board.
Local models via LM Studio; frontier via OpenRouter. Same prompt, same scorer
(now tag-stripping aware). Only chart_table is re-run; other lanes' scores stand."""
import base64, json, re, time, urllib.request
from pathlib import Path

BASE = Path("/root/repos/huible/experiments/ingestion-pdf")
PNG = BASE / "outputs/page_png/chart_table_p0.png"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
OR_KEY = json.load(open("/root/.local/share/opencode/auth.json"))["openrouter"]["key"]
TOK = open("/root/.secrets/lmstudio/api_token").read().strip()

PROMPT = ("Extract all readable content from the image in natural human reading order and "
          "output the result as a single Markdown document. For charts or images, represent "
          "them using an HTML image tag: <img src=\"images/bbox_{left}_{top}_{right}_{bottom}.jpg\" />, "
          "where left, top, right, bottom are bounding box coordinates scaled to [0, 1000). "
          "Format formulas as LaTeX. Format tables as HTML: <table>...</table>. "
          "Transcribe all other text as standard Markdown. "
          "Preserve the original text without translation or paraphrasing.")

LOCAL = {"ovisocr2": "ovisocr2-sota-20260823"}
FRONTIER = {
    "claude-opus-5": "anthropic/claude-opus-5",
    "claude-sonnet-5": "anthropic/claude-sonnet-5",
    "gpt-6-astra": "openai/gpt-6-astra",
    "gpt-5.4-image-2": "openai/gpt-5.4-image-2",
}
DEST = {"ovisocr2": BASE / "outputs/ovisocr2",
        "claude-opus-5": BASE / "outputs/frontier/claude-opus-5",
        "claude-sonnet-5": BASE / "outputs/frontier/claude-sonnet-5",
        "gpt-6-astra": BASE / "outputs/frontier/gpt-6-astra",
        "gpt-5.4-image-2": BASE / "outputs/frontier/gpt-5.4-image-2"}
# flash baseline must ALSO be re-run on the fixed page; save separately
FLASH_GT_PREV = {"scanned_formula": 0.829, "scanned_mixed": 0.884, "real_mixed": 0.834}


def b64():
    return base64.b64encode(PNG.read_bytes()).decode()


def call_local(model):
    payload = {"model": model, "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64()}},
            {"type": "text", "text": PROMPT}]}],
        "temperature": 0, "top_p": 0.01, "top_k": 1, "presence_penalty": 1.0,
        "frequency_penalty": 0.05, "max_tokens": 6144}
    req = urllib.request.Request("http://100.83.231.16:1234/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + TOK, "Content-Type": "application/json"})
    t0 = time.time()
    with OPENER.open(req, timeout=900) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]["content"] or "", time.time() - t0, 0.0


def call_or(model):
    payload = {"model": model, "messages": [{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64()}}]}],
        "max_tokens": 8192}
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + OR_KEY, "Content-Type": "application/json"})
    t0 = time.time()
    with OPENER.open(req, timeout=600) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]["content"] or "", time.time() - t0, d.get("usage", {}).get("cost", 0)


def norm_tokens(text):
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


def main():
    gt = norm_tokens((BASE / "ground_truth/chart_table.pdf.gt.txt").read_text())
    results = {}
    for label, model in list(LOCAL.items()) + list(FRONTIER.items()):
        f = DEST[label] / "chart_table_fixed.md"
        if not f.exists():
            try:
                out, dt, cost = (call_local(model) if label in LOCAL else call_or(model))
                f.write_text(out)
                print(f"[{label}] {dt:.1f}s ${cost:.4f} {len(out)} chars", flush=True)
            except Exception as e:
                print(f"[{label}] FAILED {str(e)[:150]}", flush=True)
                continue
            time.sleep(2)
        results[label] = round(f1(norm_tokens(f.read_text()), gt), 3)
    print("\nFIXED chart_table F1:")
    for k, v in results.items():
        print(f"  {k:18s} {v:.3f}")
    (BASE / "outputs" / "chart_table_fixed_scores.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
