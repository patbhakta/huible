"""VLM-assisted extraction via GLM-4.5V on rasterized pages."""
import base64
import json
import os
import pathlib
import time
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / "outputs" / "vlm"
OUT.mkdir(parents=True, exist_ok=True)

PROMPT = (
    "Extract all content from this document page verbatim: body text, headings, "
    "tables as markdown, formulas as LaTeX, and describe charts briefly in brackets. "
    "Output only the extracted content, no commentary."
)

def extract(png_path: pathlib.Path):
    png = base64.b64encode(png_path.read_bytes()).decode()
    body = json.dumps(
        {
            "model": "glm-4.5v",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}},
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
            "max_tokens": 6000,
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.z.ai/api/coding/paas/v4/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {os.environ['GLM_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    t0 = time.perf_counter()
    r = json.load(urllib.request.urlopen(req, timeout=300))
    dt = time.perf_counter() - t0
    return r["choices"][0]["message"]["content"], r["usage"], round(dt, 1)


results = {}
for png in sorted((BASE / "outputs" / "page_png").glob("*.png")):
    name = png.stem.rsplit("_p", 1)[0]
    text, usage, dt = extract(png)
    (OUT / f"{name}.md").write_text(text)
    results[name] = {
        "seconds": dt,
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
    }
    print(name, results[name], flush=True)
print(json.dumps(results, indent=2))
