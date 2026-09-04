"""pymupdf baseline: plain text extraction per sample."""
import json
import pathlib
import time

import pymupdf

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / "outputs" / "pymupdf"
OUT.mkdir(parents=True, exist_ok=True)

samples = ["real_mixed", "scanned_formula", "scanned_mixed", "chart_table"]
results = {}
for name in samples:
    pdf = BASE / "samples" / f"{name}.pdf"
    doc = pymupdf.open(pdf)
    t0 = time.perf_counter()
    pages = [p.get_text("text") for p in doc]
    dt = time.perf_counter() - t0
    text = "\n\f\n".join(pages)
    (OUT / f"{name}.txt").write_text(text)
    results[name] = {"chars": len(text), "seconds": round(dt, 3)}
    doc.close()
print(json.dumps(results, indent=2))
